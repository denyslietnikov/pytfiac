"""Transactions and UDP lifecycle with no real network or air conditioner."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import pytest


def test_atomic_concurrent_commands(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        root = ET.fromstring(status_response)
        wire = root.find("statusUpdateMsg")
        events = []

        async def send(message):
            payload = ET.fromstring(message)
            await asyncio.sleep(0)
            events.append(payload.attrib["msgid"])
            if payload.attrib["msgid"] == "SetMessage":
                for node in payload.find("SetMessage"):
                    wire.find(node.tag).text = node.text
                return b'<msg msgid="SetMessage"><SetMessage /></msg>'
            return ET.tostring(root)

        client._send = send
        await asyncio.wait_for(
            asyncio.gather(
                client.async_apply_changes(
                    protocol.models.TfiacChanges(target_temperature=78)
                ),
                client.async_apply_changes(
                    protocol.models.TfiacChanges(fan=protocol.models.Fan.HIGH)
                ),
            ),
            timeout=1,
        )
        result = await client.async_update()
        assert result.target_temperature == 78
        assert result.fan == protocol.models.Fan.HIGH
        assert events == ["SyncStatusReq", "SetMessage", "SyncStatusReq"] * 2 + [
            "SyncStatusReq"
        ]

    asyncio.run(scenario())


def test_unapplied_command_reports_last_device_state(
    protocol, status_response, monkeypatch
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_TIMEOUT", 0.05)

    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(
            side_effect=[status_response, b"<msg><SetMessage /></msg>", status_response]
        )
        with pytest.raises(protocol.api.TfiacCommandNotConfirmedError) as error:
            await client.async_apply_changes(
                protocol.models.TfiacChanges(target_temperature=78)
            )
        assert error.value.last_state.target_temperature == 77
        assert client._send.await_count == 3

    asyncio.run(scenario())


def test_failed_write_is_not_retried(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(
            side_effect=[status_response, protocol.api.TfiacTimeoutError()]
        )
        with pytest.raises(protocol.api.TfiacTimeoutError):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
            )
        assert client._send.await_count == 2
        client._send = AsyncMock(return_value=status_response)
        assert (await client.async_update()).sleep == "off"

    asyncio.run(scenario())


@pytest.mark.parametrize("has_snapshot", [False, True])
def test_confirmation_deadline_during_read_uses_last_snapshot(
    protocol, state, monkeypatch, has_snapshot
):
    """Expire exactly during a read; do not depend on a 30 ms scheduling race."""

    async def scenario():
        budget = asyncio.timeout(None)
        monkeypatch.setattr(protocol.api.asyncio, "timeout", lambda seconds: budget)
        monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_INTERVAL", 0)
        client = protocol.api.TfiacClient("192.0.2.1")
        reads = 0

        async def read():
            nonlocal reads
            reads += 1
            if has_snapshot and reads == 1:
                return state
            budget.reschedule(asyncio.get_running_loop().time())
            await asyncio.Future()

        client._read_status = read
        expected = (
            protocol.api.TfiacCommandNotConfirmedError
            if has_snapshot
            else protocol.api.TfiacTimeoutError
        )
        with pytest.raises(expected) as err:
            await client._confirm_changes(
                replace(state, target_temperature=78),
                protocol.models.TfiacChanges(target_temperature=78),
            )
        if has_snapshot:
            assert err.value.last_state == state
        assert reads == (2 if has_snapshot else 1)

    asyncio.run(scenario())


def test_real_read_timeout_is_not_reclassified_as_unconfirmed(
    protocol, state, monkeypatch
):
    async def scenario():
        monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_INTERVAL", 0)
        client = protocol.api.TfiacClient("192.0.2.1")
        client._read_status = AsyncMock(
            side_effect=[state, protocol.api.TfiacTimeoutError("UDP timeout")]
        )
        with pytest.raises(protocol.api.TfiacTimeoutError, match="UDP timeout"):
            await client._confirm_changes(
                replace(state, target_temperature=78),
                protocol.models.TfiacChanges(target_temperature=78),
            )

    asyncio.run(scenario())


def test_noop_does_not_write(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(return_value=status_response)
        await client.async_apply_changes(
            protocol.models.TfiacChanges(target_temperature=77)
        )
        client._send.assert_awaited_once()

    asyncio.run(scenario())


def test_cancelled_transaction_releases_lock(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        reading_after_write = asyncio.Event()
        read_count = 0

        async def send(message):
            nonlocal read_count
            if ET.fromstring(message).get("msgid") == "SetMessage":
                return b"<msg><SetMessage /></msg>"
            read_count += 1
            if read_count == 2:
                reading_after_write.set()
                await asyncio.Event().wait()
            return status_response

        client._send = send
        transaction = asyncio.create_task(
            client.async_apply_changes(
                protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
            )
        )
        await asyncio.wait_for(reading_after_write.wait(), timeout=1)
        transaction.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transaction
        result = await asyncio.wait_for(client.async_update(), timeout=1)
        assert result.sleep == "off"

    asyncio.run(scenario())


def test_mixed_payload_rejected(protocol):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock()
        with pytest.raises(ValueError):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(
                    target_temperature=78, swing_horizontal=True
                )
            )
        client._send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "timeout",
        "cancel",
        "socket_error",
        "send_error",
        "oversize",
        "error_received",
        "connection_lost",
    ],
)
def test_udp_transport_cleanup(protocol, monkeypatch, outcome):
    async def scenario():
        loop = asyncio.get_running_loop()
        transport = Mock()
        received = None

        async def endpoint(factory, **kwargs):
            nonlocal received
            assert kwargs["remote_addr"] == ("192.0.2.1", 7777)
            if outcome == "socket_error":
                raise OSError("unreachable")
            received = factory()
            return transport, received

        monkeypatch.setattr(loop, "create_datagram_endpoint", endpoint)
        if outcome == "send_error":
            transport.sendto.side_effect = OSError("send failed")
        elif outcome == "error_received":
            transport.sendto.side_effect = lambda message: received.error_received(
                OSError("UDP error callback")
            )
        elif outcome == "connection_lost":
            transport.sendto.side_effect = lambda message: received.connection_lost(
                None
            )
        elif outcome in ("success", "oversize"):
            transport.sendto.side_effect = lambda message: received.datagram_received(
                b"x" * (16385 if outcome == "oversize" else 1), ("192.0.2.1", 7777)
            )
        monkeypatch.setattr(protocol.api, "REQUEST_TIMEOUT", 0.01)
        client = protocol.api.TfiacClient("192.0.2.1")
        if outcome == "cancel":
            task = asyncio.create_task(client._send(b"request"))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "success":
            assert await client._send(b"request") == b"x"
        else:
            with pytest.raises(protocol.api.TfiacError):
                await client._send(b"request")
        if outcome != "socket_error":
            transport.close.assert_called_once()

    asyncio.run(scenario())


def test_late_datagrams_are_ignored(protocol):
    async def scenario():
        future = asyncio.get_running_loop().create_future()
        response = protocol.api._ResponseProtocol(future)
        response.datagram_received(b"first", ("192.0.2.1", 7777))
        response.datagram_received(b"second", ("192.0.2.1", 7777))
        response.connection_lost(None)
        response.error_received(OSError())
        assert await future == b"first"

    asyncio.run(scenario())
