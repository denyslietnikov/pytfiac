"""Characterization tests for the bundled TFIAC protocol client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


def run(coroutine):
    """Run a protocol coroutine without requiring a pytest asyncio plugin."""
    return asyncio.run(coroutine)


def test_update_maps_status(protocol, status_response: bytes) -> None:
    """A status response is mapped to the public state used by climate.py."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=status_response)

    run(client.update())

    assert client.name == "Katie AC"
    assert client.available is True
    assert client.status == {
        protocol.SLEEP_MODE: protocol.SLEEP_MODE_OFF,
        protocol.CURR_TEMP: 70.0,
        protocol.TARGET_TEMP: 77.0,
        protocol.OPERATION_MODE: "selfFeel",
        protocol.FAN_MODE: "Low",
        protocol.ON_MODE: "on",
        protocol.SWING_MODE: "Off",
    }


@pytest.mark.parametrize(
    ("horizontal", "vertical", "expected"),
    [
        ("off", "off", "Off"),
        ("on", "off", "Horizontal"),
        ("off", "on", "Vertical"),
        ("on", "on", "Both"),
    ],
)
def test_wind_direction_mapping(
    protocol, horizontal: str, vertical: str, expected: str
) -> None:
    """The two protocol flags map to the four current swing modes."""
    client = protocol.Tfiac("192.0.2.1")

    assert (
        client._map_winddirection(
            {"WindDirection_H": horizontal, "WindDirection_V": vertical}
        )
        == expected
    )


def test_update_is_throttled(protocol, status_response: bytes) -> None:
    """Repeated status reads within SHORT_WAIT do not send another datagram."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=status_response)

    run(client.update())
    run(client.update())

    client._send.assert_awaited_once()


def test_set_operation_sends_complete_state(protocol, status_response: bytes) -> None:
    """Changing the operation preserves the legacy full-state write behavior."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=status_response)
    run(client.update())
    client._send.reset_mock()
    client._send.return_value = b"<ack />"

    run(client.set_state(protocol.OPERATION_MODE, "cool"))

    client._send.assert_awaited_once()
    message = client._send.await_args.args[0]
    assert '<msg msgid="SetMessage"' in message
    assert "<TurnOn>on</TurnOn>" in message
    assert "<BaseMode>cool</BaseMode>" in message
    assert "<SetTemp>77.0</SetTemp>" in message
    assert "<WindSpeed>Low</WindSpeed>" in message
    assert "<Opt_sleepMode>off</Opt_sleepMode>" in message


@pytest.mark.parametrize("swing_mode", ["Off", "Vertical", "Horizontal", "Both"])
def test_set_swing_uses_existing_payload(protocol, swing_mode: str) -> None:
    """Every public swing mode sends the corresponding legacy payload."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=b"<ack />")

    run(client.set_swing(swing_mode))

    message = client._send.await_args.args[0]
    assert protocol.SET_SWING[swing_mode] in message


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(False, "off"), (True, None)],
)
def test_set_sleep_updates_state_after_send(
    protocol, status_response: bytes, enabled: bool, expected: str | None
) -> None:
    """Sleep uses a full-state write and updates local state after it succeeds."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=status_response)
    run(client.update())
    client._send.reset_mock()
    client._send.return_value = b"<ack />"

    run(client.set_sleep(enabled))

    sleep_value = protocol.SLEEP_MODE_ON if expected is None else expected
    message = client._send.await_args.args[0]
    assert f"<Opt_sleepMode>{sleep_value}</Opt_sleepMode>" in message
    assert client.status[protocol.SLEEP_MODE] == sleep_value


def test_failed_sleep_write_keeps_previous_state(
    protocol, status_response: bytes
) -> None:
    """The legacy client only changes local sleep state after a successful write."""
    client = protocol.Tfiac("192.0.2.1")
    client._send = AsyncMock(return_value=status_response)
    run(client.update())
    client._send.reset_mock()
    client._send.side_effect = protocol.Unavailable()

    with pytest.raises(protocol.Unavailable):
        run(client.set_sleep(True))

    assert client.status[protocol.SLEEP_MODE] == protocol.SLEEP_MODE_OFF
