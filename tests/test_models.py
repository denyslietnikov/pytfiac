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


def test_status_capabilities_are_not_writable_claims(protocol, state):
    assert state.eco is False
    assert state.turbo is False
    assert state.beep is True
    assert state.degree_half is False
    assert state.display is None
    assert state.outdoor_temperature is None
    assert "OutdoorTemp:ambiguous_zero" in state.optional_issues
    assert state.capabilities.eco
    assert state.capabilities.turbo
    assert state.capabilities.beep
    assert not state.capabilities.display
    assert not state.capabilities.outdoor_temperature
    assert dict(state.raw_fields)["OutdoorTemp"] == "0"
    with pytest.raises(FrozenInstanceError):
        state.capabilities.eco = False


@pytest.mark.parametrize(
    "tag,attribute",
    [
        ("Opt_ECO", "eco"),
        ("Opt_eco", "eco"),
        ("Opt_super", "turbo"),
        ("Opt_display", "display"),
        ("BeepEnable", "beep"),
        ("Opt_beep", "beep"),
        ("Degree_Half", "degree_half"),
    ],
)
@pytest.mark.parametrize("value", ["on", "off", "invalid", ""])
def test_optional_boolean_values(protocol, status_response, tag, attribute, value):
    root = ET.fromstring(status_response)
    status = root.find("statusUpdateMsg")
    for alias in ("Opt_ECO", "Opt_eco", "BeepEnable", "Opt_beep", tag):
        if (node := status.find(alias)) is not None:
            status.remove(node)
    ET.SubElement(status, tag).text = value
    result = protocol.api.parse_status(ET.tostring(root))
    if value in {"on", "off"}:
        assert getattr(result, attribute) is (value == "on")
        assert getattr(result.capabilities, attribute)
    else:
        assert getattr(result, attribute) is None
        assert f"{tag}:invalid_value" in result.optional_issues
    assert result.target_temperature == 77


@pytest.mark.parametrize(
    "tags,attribute",
    [(("Opt_ECO", "Opt_eco"), "eco"), (("BeepEnable", "Opt_beep"), "beep")],
)
@pytest.mark.parametrize("matching", [True, False])
def test_optional_aliases_do_not_guess_precedence(
    protocol, status_response, tags, attribute, matching
):
    root = ET.fromstring(status_response)
    status = root.find("statusUpdateMsg")
    for index, tag in enumerate(tags):
        node = status.find(tag)
        if node is None:
            node = ET.SubElement(status, tag)
        node.text = "on" if matching or index == 0 else "off"
    result = protocol.api.parse_status(ET.tostring(root))
    assert getattr(result, attribute) is (True if matching else None)
    if not matching:
        assert all(
            f"{tag}:conflicting_aliases" in result.optional_issues for tag in tags
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("72.123", 72.12),
        ("-5", -5),
        ("0", None),
        ("0.001", None),
        ("", None),
        ("NaN", None),
        ("inf", None),
        ("unknown", None),
    ],
)
def test_outdoor_temperature_is_conservative(
    protocol, status_response, value, expected
):
    root = ET.fromstring(status_response)
    root.find("statusUpdateMsg/OutdoorTemp").text = value
    result = protocol.api.parse_status(ET.tostring(root))
    assert result.outdoor_temperature == expected
    assert result.power == protocol.models.Power.ON


def test_missing_optional_status(protocol, status_response):
    root = ET.fromstring(status_response)
    status = root.find("statusUpdateMsg")
    for tag in ("Opt_ECO", "Opt_super", "BeepEnable", "Degree_Half", "OutdoorTemp"):
        status.remove(status.find(tag))
    result = protocol.api.parse_status(ET.tostring(root))
    assert all(
        getattr(result, field) is None
        for field in ("eco", "turbo", "beep", "degree_half", "outdoor_temperature")
    )
    assert result.optional_issues == ()


def test_raw_metadata_does_not_trigger_state_change(protocol, state):
    assert (
        replace(state, raw_fields=(("Unknown", "different"),), optional_issues=("new",))
        == state
    )
    assert "FirmwareSecret" not in repr(
        replace(state, raw_fields=(("Unknown", "FirmwareSecret"),))
    )


def test_unknown_extension_is_not_sent_back(protocol, status_response):
    root = ET.fromstring(status_response)
    extra = ET.SubElement(root.find("statusUpdateMsg"), "FirmwareExtension")
    ET.SubElement(extra, "PrivateData").text = "secret"
    result = protocol.api.parse_status(ET.tostring(root))
    assert dict(result.raw_fields)["FirmwareExtension"] == "[nested]"
    assert "secret" not in repr(result)
    assert "FirmwareExtension" not in fields(
        protocol.api.build_set_message(result, "123")
    )
    assert "Opt_ECO" not in fields(protocol.api.build_set_message(result, "123"))
    assert "BeepEnable" not in fields(protocol.api.build_set_message(result, "123"))


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
