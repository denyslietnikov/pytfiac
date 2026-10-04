"""Native temperature validation and numeric precision, not hardware-step claims."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest


@pytest.mark.parametrize(
    "requested,expected",
    [
        (60.8, 60.8),
        (61, 61.0),
        (88, 88.0),
        (78.8, 78.8),
        (78.80000000000001, 78.8),
        (79.022222204, 79.02),
        (73.418, 73.42),
        (77.004, 77.0),
    ],
)
def test_request_has_same_precision_as_decoded_status(
    protocol, state, requested, expected
):
    desired = protocol.models.apply_changes(
        state, protocol.models.TfiacChanges(target_temperature=requested)
    )
    assert desired.target_temperature == expected
    assert state.target_temperature == 77
    payload = ET.fromstring(protocol.api.build_set_message(desired, "123"))
    assert payload.find("SetMessage/SetTemp").text == str(expected)


@pytest.mark.parametrize(
    "requested",
    [
        60.7999,
        88.0001,
        60,
        89,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
        "78.8",
        None,
        complex(78, 1),
        10**1000,
    ],
)
def test_invalid_request_is_not_rounded_or_clamped_into_range(protocol, requested):
    with pytest.raises(ValueError, match="finite number"):
        protocol.models.normalize_target_temperature(requested)


@pytest.mark.parametrize("degree_half", [None, False, True])
def test_degree_half_does_not_quantize_temperature(protocol, state, degree_half):
    desired = protocol.models.apply_changes(
        replace(state, degree_half=degree_half),
        protocol.models.TfiacChanges(target_temperature=78.8),
    )
    assert desired.target_temperature == 78.8
    assert desired.degree_half == degree_half
    payload = ET.fromstring(protocol.api.build_set_message(desired, "123"))
    assert payload.find("SetMessage/SetTemp").text == "78.8"
    assert payload.find("SetMessage/Degree_Half") is None


@pytest.mark.parametrize("power", ["on", "off"])
def test_fan_only_has_no_temperature_intent_but_can_switch_to_cool(
    protocol, state, power
):
    current = replace(
        state,
        operation=protocol.models.Operation.FAN,
        power=protocol.models.Power(power),
    )
    with pytest.raises(ValueError, match="not available in Fan Only"):
        protocol.models.apply_changes(
            current, protocol.models.TfiacChanges(target_temperature=73)
        )
    with pytest.raises(ValueError, match="not available in Fan Only"):
        protocol.models.apply_changes(
            state,
            protocol.models.TfiacChanges(
                operation=protocol.models.Operation.FAN, target_temperature=73
            ),
        )
    desired = protocol.models.apply_changes(
        current,
        protocol.models.TfiacChanges(
            operation=protocol.models.Operation.COOL, target_temperature=73
        ),
    )
    assert desired.target_temperature == 73
    assert desired.operation == protocol.models.Operation.COOL
    assert desired.power == protocol.models.Power.ON


def test_long_fraction_is_confirmed_without_write_retry(
    protocol, status_response, monkeypatch
):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_TIMEOUT", 0.05)

    async def scenario():
        root = ET.fromstring(status_response)
        root.find("statusUpdateMsg/SetTemp").text = "79.02"
        # Sensor data is independent of the requested setpoint.
        root.find("statusUpdateMsg/IndoorTemp").text = "-40"
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(
            side_effect=[
                status_response,
                b"<msg><SetMessage /></msg>",
                ET.tostring(root),
            ]
        )
        result = await client.async_apply_changes(
            protocol.models.TfiacChanges(target_temperature=79.022222204)
        )
        assert result.target_temperature == 79.02
        assert result.current_temperature == -40
        requests = [
            ET.fromstring(call.args[0]) for call in client._send.await_args_list
        ]
        assert [r.get("msgid") for r in requests] == [
            "SyncStatusReq",
            "SetMessage",
            "SyncStatusReq",
        ]
        assert requests[1].find("SetMessage/SetTemp").text == "79.02"

    asyncio.run(scenario())


def test_sub_precision_request_is_noop(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(return_value=status_response)
        result = await client.async_apply_changes(
            protocol.models.TfiacChanges(target_temperature=77.004)
        )
        assert result.target_temperature == 77
        client._send.assert_awaited_once()

    asyncio.run(scenario())
