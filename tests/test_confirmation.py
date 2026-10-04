"""Delayed firmware confirmation, without real sockets or physical controls."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest


@pytest.mark.parametrize(
    "field,requested,reported",
    [
        ("power", "on", "off"),
        ("operation", "cool", "fan"),
        ("target_temperature", 78, 77),
        ("fan", "High", "Middle"),
        ("swing_horizontal", True, False),
        ("swing_vertical", False, True),
        ("display", False, None),
        ("beep", False, True),
    ],
)
def test_only_requested_controls_must_match(
    protocol, state, field, requested, reported
):
    changes = protocol.models.TfiacChanges(**{field: requested})
    current = replace(state, eco=False, turbo=False, display=True, beep=True)
    desired = protocol.models.apply_changes(current, changes)
    observed = replace(desired, **{field: reported})
    assert not protocol.api._changes_confirmed(observed, desired, changes)
    assert protocol.api._changes_confirmed(
        replace(desired, current_temperature=999, outdoor_temperature=-99),
        desired,
        changes,
    )


def test_mode_alone_does_not_confirm_implicit_power_on(protocol, state):
    changes = protocol.models.TfiacChanges(operation=protocol.models.Operation.COOL)
    desired = protocol.models.apply_changes(state, changes)
    observed = replace(desired, power=protocol.models.Power.OFF)
    assert not protocol.api._changes_confirmed(observed, desired, changes)


def test_delayed_commands_and_poll_remain_serialized(
    protocol, status_response, monkeypatch
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_INTERVAL", 0)

    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        root = ET.fromstring(status_response)
        status = root.find("statusUpdateMsg")
        pending = None
        remaining_reads = 0
        requests = []

        async def send(message):
            nonlocal pending, remaining_reads
            request = ET.fromstring(message)
            requests.append(request)
            await asyncio.sleep(0)
            if request.get("msgid") == "SetMessage":
                assert pending is None, "A write overtook unconfirmed hardware state"
                pending = request.find("SetMessage")
                remaining_reads = 3
                return b"<msg><SetMessage /></msg>"
            if pending is not None:
                remaining_reads -= 1
                if not remaining_reads:
                    for node in pending:
                        status.find(node.tag).text = node.text
                    pending = None
            return ET.tostring(root)

        client._send = send
        temperature, fan, polled = await asyncio.wait_for(
            asyncio.gather(
                client.async_apply_changes(
                    protocol.models.TfiacChanges(target_temperature=78)
                ),
                client.async_apply_changes(
                    protocol.models.TfiacChanges(fan=protocol.models.Fan.HIGH)
                ),
                client.async_update(),
            ),
            timeout=1,
        )
        assert temperature.target_temperature == 78
        assert fan.target_temperature == polled.target_temperature == 78
        assert fan.fan == polled.fan == protocol.models.Fan.HIGH
        assert [r.get("msgid") for r in requests] == (
            ["SyncStatusReq", "SetMessage"] + ["SyncStatusReq"] * 3
        ) * 2 + ["SyncStatusReq"]
        writes = [
            r.find("SetMessage") for r in requests if r.get("msgid") == "SetMessage"
        ]
        assert writes[1].find("SetTemp").text == "78.0"

    asyncio.run(scenario())


def test_sleep_confirms_normalized_profile_and_actual_fan(protocol, status_response):
    async def scenario():
        root = ET.fromstring(status_response)
        before = ET.fromstring(status_response)
        before.find("statusUpdateMsg/WindSpeed").text = "Middle"
        status = root.find("statusUpdateMsg")
        status.find("Opt_sleepMode").text = "sleepMode1:0:0:0:0:0:0:0:0:0:0"
        status.find("WindSpeed").text = "Auto"
        status.find("IndoorTemp").text = "123"
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(
            side_effect=[
                ET.tostring(before),
                b"<msg><SetMessage /></msg>",
                ET.tostring(root),
            ]
        )
        result = await client.async_apply_changes(
            protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
        )
        assert result.sleep == "sleepMode1:0:0:0:0:0:0:0:0:0:0"
        assert result.fan == protocol.models.Fan.AUTO
        assert result.current_temperature == 123
        assert client._send.await_count == 3
        client._send.reset_mock(side_effect=True)
        client._send.return_value = ET.tostring(root)
        # Already active with a shortened firmware profile: do not resend Sleep.
        assert (
            await client.async_apply_changes(
                protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
            )
            == result
        )
        client._send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,tag",
    [
        ("display", "Opt_display"),
        ("beep", "BeepEnable"),
    ],
)
def test_optional_confirmation_waits_through_missing_and_old_flags(
    protocol, status_response, monkeypatch, field, tag
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_INTERVAL", 0)

    async def scenario():
        root = ET.fromstring(status_response)
        status = root.find("statusUpdateMsg")
        for flag in ("Opt_ECO", "Opt_super", "Opt_display", "BeepEnable"):
            node = status.find(flag)
            if node is None:
                node = ET.SubElement(status, flag)
            node.text = "off"
        before = ET.tostring(root)
        node = status.find(tag)
        status.remove(node)
        missing = ET.tostring(root)
        status.append(node)
        node.text = "on"
        after = ET.tostring(root)
        client = protocol.api.TfiacClient(
            "192.0.2.1",
            command_profile=protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
        )
        client._send = AsyncMock(
            side_effect=[before, b"<msg><SetMessage /></msg>", missing, before, after]
        )
        result = await client.async_apply_changes(
            protocol.models.TfiacChanges(**{field: True})
        )
        assert getattr(result, field) is True
        assert client._send.await_count == 5
        ids = [
            ET.fromstring(call.args[0]).get("msgid")
            for call in client._send.await_args_list
        ]
        assert ids.count("SetMessage") == 1

    asyncio.run(scenario())


def test_confirmation_has_absolute_read_deadline_and_releases_lock(
    protocol, status_response, monkeypatch
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_TIMEOUT", 0.02)

    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        calls = []
        cancelled_read = asyncio.Event()

        async def send(message):
            calls.append(ET.fromstring(message).get("msgid"))
            if len(calls) == 1:
                return status_response
            if len(calls) == 2:
                return b"<msg><SetMessage /></msg>"
            try:
                await asyncio.Event().wait()
            finally:
                cancelled_read.set()

        client._send = send
        with pytest.raises(protocol.api.TfiacTimeoutError, match="confirmation"):
            await asyncio.wait_for(
                client.async_apply_changes(
                    protocol.models.TfiacChanges(target_temperature=78)
                ),
                timeout=1,
            )
        assert cancelled_read.is_set()
        assert calls == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
        client._send = AsyncMock(return_value=status_response)
        assert await asyncio.wait_for(client.async_update(), timeout=1)

    asyncio.run(scenario())


def test_deadline_after_valid_mismatches_keeps_latest_status(
    protocol, status_response, monkeypatch
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_TIMEOUT", 0.02)
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_INTERVAL", 0.001)

    async def scenario():
        root = ET.fromstring(status_response)
        root.find("statusUpdateMsg/WindSpeed").text = "High"
        response = ET.tostring(root)
        requests = []

        async def send(message):
            msgid = ET.fromstring(message).get("msgid")
            requests.append(msgid)
            return b"<msg><SetMessage /></msg>" if msgid == "SetMessage" else response

        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = send
        with pytest.raises(protocol.api.TfiacCommandNotConfirmedError) as error:
            await client.async_apply_changes(
                protocol.models.TfiacChanges(target_temperature=78)
            )
        assert error.value.last_state.target_temperature == 77
        assert error.value.last_state.fan == protocol.models.Fan.HIGH
        assert requests.count("SetMessage") == 1
        assert requests.count("SyncStatusReq") > 2
        assert await asyncio.wait_for(client.async_update(), timeout=1)

    asyncio.run(scenario())
