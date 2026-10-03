"""Parsing, normalization and payload regressions independent of HA."""

from dataclasses import FrozenInstanceError, replace
from xml.etree import ElementTree as ET

import pytest


def fields(message: bytes) -> dict[str, str]:
    root = ET.fromstring(message)
    return {node.tag: node.text for node in root.find("SetMessage")}


def test_legacy_snapshot(protocol, state):
    assert state.name == "Katie AC"
    assert state.current_temperature == 70.0
    assert state.target_temperature == 77.0
    assert state.operation == protocol.models.Operation.AUTO
    assert state.fan == protocol.models.Fan.LOW
    assert state.power == protocol.models.Power.ON
    assert state.sleep == "off"
    assert (state.swing_horizontal, state.swing_vertical) == (False, False)
    with pytest.raises(FrozenInstanceError):
        state.fan = protocol.models.Fan.HIGH


@pytest.mark.parametrize(
    "horizontal,vertical", [(False, False), (False, True), (True, False), (True, True)]
)
def test_swing_payload(protocol, state, horizontal, vertical):
    desired = replace(state, swing_horizontal=horizontal, swing_vertical=vertical)
    assert fields(protocol.api.build_swing_message(desired, "123")) == {
        "WindDirection_H": "on" if horizontal else "off",
        "WindDirection_V": "on" if vertical else "off",
    }


def test_full_state_payload(protocol, state):
    assert fields(protocol.api.build_set_message(state, "123")) == {
        "TurnOn": "on",
        "BaseMode": "selfFeel",
        "SetTemp": "77.0",
        "WindSpeed": "Low",
        "Opt_sleepMode": "off",
    }


def test_optional_capabilities(protocol, status_response):
    root = ET.fromstring(status_response)
    status = root.find("statusUpdateMsg")
    for field in (
        "Opt_sleepMode",
        "WindDirection_H",
        "WindDirection_V",
        "IndoorTemp",
        "DeviceName",
    ):
        status.remove(status.find(field))
    result = protocol.api.parse_status(ET.tostring(root))
    assert result.sleep is None
    assert result.current_temperature is None
    assert result.name is None
    assert result.swing_vertical is None
    assert "Opt_sleepMode" not in fields(protocol.api.build_set_message(result, "123"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("SetTemp", "NaN"),
        ("IndoorTemp", "inf"),
        ("TurnOn", "unknown"),
        ("BaseMode", "mystery"),
        ("WindSpeed", "Medium"),
        ("WindDirection_H", "yes"),
    ],
)
def test_invalid_status(protocol, status_response, field, value):
    root = ET.fromstring(status_response)
    root.find(f"statusUpdateMsg/{field}").text = value
    with pytest.raises(protocol.api.TfiacParseError):
        protocol.api.parse_status(ET.tostring(root))


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"<msg>",
        b"\xff",
        b"<other />",
        b'<msg msgid="UnknownCmd"><UnknownCmd /></msg>',
        b"<msg><statusUpdateMsg /></msg>",
        b"<!DOCTYPE msg [<!ENTITY x 'payload'>]><msg>&x;</msg>",
        b"x" * 16385,
    ],
)
def test_invalid_xml(protocol, response):
    with pytest.raises(protocol.api.TfiacProtocolError):
        protocol.api.parse_status(response)


def test_duplicate_field_rejected(protocol, status_response):
    root = ET.fromstring(status_response)
    ET.SubElement(root.find("statusUpdateMsg"), "TurnOn").text = "off"
    with pytest.raises(protocol.api.TfiacParseError):
        protocol.api.parse_status(ET.tostring(root))


def test_duplicate_status_rejected(protocol, status_response):
    root = ET.fromstring(status_response)
    root.append(ET.fromstring(status_response).find("statusUpdateMsg"))
    with pytest.raises(protocol.api.TfiacProtocolError):
        protocol.api.parse_status(ET.tostring(root))


def test_operation_turns_on(protocol, state):
    desired = protocol.models.apply_changes(
        replace(state, power=protocol.models.Power.OFF),
        protocol.models.TfiacChanges(operation=protocol.models.Operation.HEAT),
    )
    assert desired.power == protocol.models.Power.ON
    assert desired.operation == protocol.models.Operation.HEAT
    assert state.operation == protocol.models.Operation.AUTO


@pytest.mark.parametrize("temperature", [60, 89, float("nan"), float("inf")])
def test_invalid_temperature(protocol, state, temperature):
    with pytest.raises(ValueError):
        protocol.models.apply_changes(
            state, protocol.models.TfiacChanges(target_temperature=temperature)
        )


def test_conflicting_power_and_operation(protocol, state):
    with pytest.raises(ValueError):
        protocol.models.apply_changes(
            state,
            protocol.models.TfiacChanges(
                power=protocol.models.Power.OFF,
                operation=protocol.models.Operation.HEAT,
            ),
        )


def test_sleep_uses_existing_profile(protocol, state):
    desired = protocol.models.apply_changes(
        state, protocol.models.TfiacChanges(sleep=True)
    )
    assert desired.sleep == protocol.models.SLEEP_MODE_ON
    assert desired.fan == state.fan


@pytest.mark.parametrize("change", ["sleep", "swing_horizontal", "swing_vertical"])
def test_unsupported_changes(protocol, state, change):
    unsupported = replace(state, sleep=None, swing_horizontal=None, swing_vertical=None)
    with pytest.raises(ValueError):
        protocol.models.apply_changes(
            unsupported, protocol.models.TfiacChanges(**{change: True})
        )
