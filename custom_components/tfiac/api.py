"""Bounded, serialized local UDP client for TFIAC."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from math import isfinite
from time import time_ns
from xml.etree import ElementTree as ET

from .models import Fan, Operation, Power, TfiacChanges, TfiacState, apply_changes

UDP_PORT = 7777
MAX_DATAGRAM_SIZE = 16384
REQUEST_TIMEOUT = 5
KNOWN_STATUS_FIELDS = frozenset(
    {
        "TurnOn",
        "BaseMode",
        "SetTemp",
        "WindSpeed",
        "IndoorTemp",
        "DeviceName",
        "WindDirection_H",
        "WindDirection_V",
        "Opt_sleepMode",
        "Opt_ECO",
        "Opt_eco",
        "Opt_super",
        "Opt_display",
        "BeepEnable",
        "Opt_beep",
        "OutdoorTemp",
        "Degree_Half",
    }
)


class TfiacError(Exception):
    """Base device error."""


class TfiacTimeoutError(TfiacError):
    """The device did not respond."""


class TfiacConnectionError(TfiacError):
    """The UDP connection failed."""


class TfiacProtocolError(TfiacError):
    """The device rejected a message or returned an unexpected envelope."""


class TfiacParseError(TfiacProtocolError):
    """The response cannot be represented as a valid device state."""


def _parse_xml(response: bytes) -> ET.Element:
    """Accept bounded UTF-8 XML without DTDs or entity declarations."""
    if len(response) > MAX_DATAGRAM_SIZE:
        raise TfiacParseError("Response is too large")
    try:
        text = response.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise TfiacParseError("XML declarations are not supported")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError) as err:
        raise TfiacParseError("Malformed XML response") from err
    if root.tag != "msg":
        raise TfiacProtocolError("Unexpected response envelope")
    if root.get("msgid", "").lower() == "unknowncmd" or any(
        node.tag.lower() == "unknowncmd" for node in root
    ):
        raise TfiacProtocolError("Device returned UnknownCmd")
    return root


def parse_status(response: bytes) -> TfiacState:
    """Parse known status fields; tolerate absent optional capabilities."""
    root = _parse_xml(response)
    statuses = root.findall("statusUpdateMsg")
    if len(statuses) != 1:
        raise TfiacProtocolError("Response must contain exactly one statusUpdateMsg")
    status = statuses[0]
    fields: dict[str, str] = {}
    for child in status:
        if child.tag in fields or (len(child) and child.tag in KNOWN_STATUS_FIELDS):
            raise TfiacParseError("Repeated or nested status field")
        # Unknown nested extensions are not control fields. Do not retain their
        # arbitrary contents, but do record the tag for later protocol inspection.
        fields[child.tag] = "[nested]" if len(child) else (child.text or "").strip()

    optional_issues: list[str] = []

    def optional_power(*aliases: str) -> bool | None:
        present = [alias for alias in aliases if alias in fields]
        if not present:
            return None
        try:
            values = {Power(fields[alias]) == Power.ON for alias in present}
        except ValueError:
            optional_issues.extend(f"{alias}:invalid_value" for alias in present)
            return None
        if len(values) != 1:
            optional_issues.extend(f"{alias}:conflicting_aliases" for alias in present)
            return None
        return values.pop()

    def outdoor_temperature() -> float | None:
        if "OutdoorTemp" not in fields:
            return None
        try:
            value = float(fields["OutdoorTemp"])
            if not isfinite(value):
                raise ValueError("Not finite")
        except ValueError:
            optional_issues.append("OutdoorTemp:invalid_value")
            return None
        # The supplied sample has 0, which may be a sentinel. Do not fabricate a
        # measurement (or treat it as confirmed 0 °F) before hardware validation.
        value = round(value, 2)
        if value == 0:
            optional_issues.append("OutdoorTemp:ambiguous_zero")
            return None
        return value

    def temperature(field: str) -> float:
        value = float(fields[field])
        if not isfinite(value):
            raise ValueError("Temperature is not finite")
        return round(value, 2)

    def direction(field: str) -> bool | None:
        if field not in fields:
            return None
        return Power(fields[field]) == Power.ON

    try:
        sleep = fields.get("Opt_sleepMode")
        if sleep is not None and (not sleep or sleep.lower().startswith("off")):
            sleep = "off"
        return TfiacState(
            power=Power(fields["TurnOn"]),
            operation=Operation(fields["BaseMode"]),
            target_temperature=temperature("SetTemp"),
            fan=Fan(fields["WindSpeed"]),
            current_temperature=(
                temperature("IndoorTemp") if fields.get("IndoorTemp") else None
            ),
            name=fields.get("DeviceName") or None,
            swing_horizontal=direction("WindDirection_H"),
            swing_vertical=direction("WindDirection_V"),
            sleep=sleep,
            eco=optional_power("Opt_ECO", "Opt_eco"),
            turbo=optional_power("Opt_super"),
            display=optional_power("Opt_display"),
            beep=optional_power("BeepEnable", "Opt_beep"),
            outdoor_temperature=outdoor_temperature(),
            degree_half=optional_power("Degree_Half"),
            raw_fields=tuple(sorted(fields.items())),
            optional_issues=tuple(optional_issues),
        )
    except (KeyError, ValueError) as err:
        raise TfiacParseError("Invalid or missing status field") from err


def _envelope(message_id: str, fields: list[tuple[str, str]], seq: str) -> bytes:
    root = ET.Element("msg", msgid=message_id, type="Control", seq=seq)
    payload = ET.SubElement(root, message_id)
    for name, value in fields:
        ET.SubElement(payload, name).text = value
    return ET.tostring(root, encoding="utf-8", short_empty_elements=False)


def build_status_message(seq: str) -> bytes:
    """Build the existing status request."""
    return _envelope("SyncStatusReq", [], seq)


def build_set_message(state: TfiacState, seq: str) -> bytes:
    """Build the legacy full-state command, omitting absent optional fields."""
    fields = [
        ("TurnOn", str(state.power)),
        ("BaseMode", str(state.operation)),
        ("SetTemp", str(state.target_temperature)),
        ("WindSpeed", str(state.fan)),
    ]
    if state.sleep is not None:
        fields.append(("Opt_sleepMode", state.sleep))
    return _envelope("SetMessage", fields, seq)


def build_swing_message(state: TfiacState, seq: str) -> bytes:
    """Preserve the existing two-field swing payload."""
    fields = [
        (name, "on" if value else "off")
        for name, value in (
            ("WindDirection_H", state.swing_horizontal),
            ("WindDirection_V", state.swing_vertical),
        )
        if value is not None
    ]
    if not fields:
        raise ValueError("Device does not report swing")
    return _envelope("SetMessage", fields, seq)


class _ResponseProtocol(asyncio.DatagramProtocol):
    """Collect a single reply on a connected UDP socket."""

    def __init__(self, future: asyncio.Future[bytes]) -> None:
        self.future = future

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if self.future.done():
            return
        if len(data) > MAX_DATAGRAM_SIZE:
            self.future.set_exception(TfiacParseError("Response is too large"))
        else:
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(TfiacConnectionError("UDP request failed"))

    def connection_lost(self, exc: Exception | None) -> None:
        if not self.future.done():
            self.future.set_exception(TfiacConnectionError("UDP connection closed"))


class TfiacClient:
    """Serialize status reads and complete read/write/read transactions."""

    def __init__(self, host: str) -> None:
        self.host = host
        self._lock = asyncio.Lock()
        self._last_sequence = 0

    def _sequence(self) -> str:
        value = max(time_ns() // 1_000_000, self._last_sequence + 1)
        self._last_sequence = value
        return str(value)[-7:]

    async def _send(self, message: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        transport = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                # remote_addr creates a connected socket: replies from other peers
                # are filtered by the OS. Each exchange gets a fresh socket.
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _ResponseProtocol(future), remote_addr=(self.host, UDP_PORT)
                )
                transport.sendto(message)
                return await future
        except TimeoutError as err:
            raise TfiacTimeoutError("Device did not respond") from err
        except OSError as err:
            raise TfiacConnectionError("Could not communicate with device") from err
        finally:
            if not future.done():
                future.cancel()
            if transport is not None:
                transport.close()

    async def _read_status(self) -> TfiacState:
        return parse_status(await self._send(build_status_message(self._sequence())))

    async def async_update(self) -> TfiacState:
        """Read current state with no client-side throttling."""
        async with self._lock:
            return await self._read_status()

    async def async_apply_changes(self, changes: TfiacChanges) -> TfiacState:
        """Read, send once, then read confirmed state before releasing the lock."""
        swing = (
            changes.swing_horizontal is not None or changes.swing_vertical is not None
        )
        full_state = any(
            getattr(changes, field) is not None
            for field in ("power", "operation", "target_temperature", "fan", "sleep")
        )
        if swing and full_state:
            raise ValueError("Swing and full-state changes require separate commands")
        async with self._lock:
            current = await self._read_status()
            desired = apply_changes(current, changes)
            if desired == current:
                return current
            builder: Callable[[TfiacState, str], bytes] = (
                build_swing_message if swing else build_set_message
            )
            response = await self._send(builder(desired, self._sequence()))
            # An acknowledgement is not evidence that the desired state was applied.
            _parse_xml(response)
            return await self._read_status()
