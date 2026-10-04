"""Sleep/Turbo encoding, exclusion and confirmation independent of HA."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest


@pytest.mark.parametrize("preset", ["none", "sleep", "boost"])
@pytest.mark.parametrize("initial", ["none", "sleep", "boost"])
def test_presets_encode_distinct_flags_and_only_boost_changes_target(
    protocol, state, preset, initial
):
    current = replace(
        state,
        operation=protocol.models.Operation.COOL,
        power=protocol.models.Power.OFF,
        sleep="firmwareSleepProfile:0:0" if initial == "sleep" else "off",
        turbo=initial == "boost",
        eco=True,
        display=True,
        beep=False,
    )
    changes = protocol.models.TfiacChanges(preset=protocol.models.Preset(preset))
    desired = protocol.models.apply_changes(current, changes)
    payload = ET.fromstring(protocol.api.build_preset_message(desired, "123")).find(
        "SetMessage"
    )
    fields = {node.tag: node.text for node in payload}
    assert fields == {
        "TurnOn": "off",
        "BaseMode": str(current.operation),
        "SetTemp": str(60.8 if preset == "boost" else current.target_temperature),
        "WindSpeed": str(current.fan),
        "Opt_sleepMode": protocol.models.SLEEP_MODE_ON if preset == "sleep" else "off",
        "Opt_super": "on" if preset == "boost" else "off",
    }
    assert desired.preset == preset
    for field in ("power", "fan", "eco", "display", "beep"):
        assert getattr(desired, field) == getattr(current, field)
    tags = list(fields)
    if preset == "sleep":
        assert tags.index("Opt_super") < tags.index("Opt_sleepMode")
    elif preset == "boost":
        assert tags.index("Opt_sleepMode") < tags.index("Opt_super")


@pytest.mark.parametrize(
    "preset,field,reported",
    [
        ("sleep", "turbo", True),
        ("sleep", "turbo", None),
        ("sleep", "sleep", "off"),
        ("sleep", "sleep", None),
        ("boost", "sleep", "sleepMode1:0:0"),
        ("boost", "sleep", None),
        ("boost", "turbo", False),
        ("boost", "turbo", None),
        ("none", "sleep", "sleepMode1:0:0"),
        ("none", "turbo", True),
    ],
)
def test_both_preset_flags_must_confirm(protocol, state, preset, field, reported):
    state = replace(state, operation=protocol.models.Operation.COOL)
    changes = protocol.models.TfiacChanges(preset=protocol.models.Preset(preset))
    desired = protocol.models.apply_changes(state, changes)
    assert not protocol.api._changes_confirmed(
        replace(desired, **{field: reported}), desired, changes
    )
    assert protocol.api._changes_confirmed(desired, desired, changes)


def test_conflicting_flags_have_no_priority_and_can_be_cleared(protocol, state):
    current = replace(
        state,
        operation=protocol.models.Operation.COOL,
        sleep="sleepMode1:0:0",
        turbo=True,
    )
    assert current.preset is None
    with pytest.raises(ValueError, match="cannot be enabled together"):
        protocol.api.build_preset_message(current, "123")
    for preset in protocol.models.Preset:
        assert (
            protocol.models.apply_changes(
                current, protocol.models.TfiacChanges(preset=preset)
            ).preset
            == preset
        )


@pytest.mark.parametrize("preset,field", [("sleep", "sleep"), ("boost", "turbo")])
def test_missing_requested_capability_blocks_write(protocol, state, preset, field):
    with pytest.raises(ValueError, match="does not report"):
        protocol.models.apply_changes(
            replace(state, **{field: None}),
            protocol.models.TfiacChanges(preset=protocol.models.Preset(preset)),
        )


def test_legacy_sleep_only_does_not_invent_turbo_field(protocol, state):
    current = replace(state, turbo=None)
    desired = protocol.models.apply_changes(
        current, protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
    )
    assert b"Opt_super" not in protocol.api.build_preset_message(desired, "123")


def test_removed_flags_are_not_command_intents(protocol):
    assert protocol.models.OPTIONAL_CONTROL_FIELDS == ("display", "beep")
    for field in ("eco", "turbo", "sleep"):
        with pytest.raises(TypeError):
            protocol.models.TfiacChanges(**{field: True})


@pytest.mark.parametrize("power", ["on", "off"])
@pytest.mark.parametrize("operation", ["heat", "selfFeel", "dehumi", "fan", "cool"])
def test_sleep_requires_compatible_operation(protocol, state, power, operation):
    current = replace(
        state,
        power=protocol.models.Power(power),
        operation=protocol.models.Operation(operation),
    )
    changes = protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
    if operation == "fan":
        with pytest.raises(ValueError, match="Sleep is not available in Fan Only"):
            protocol.models.apply_changes(current, changes)
    else:
        desired = protocol.models.apply_changes(current, changes)
        assert desired.preset == protocol.models.Preset.SLEEP
        assert desired.power == current.power


def test_sleep_validation_uses_fresh_status_before_write(protocol, status_response):
    async def scenario():
        root = ET.fromstring(status_response)
        root.find("statusUpdateMsg/BaseMode").text = "fan"
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock(return_value=ET.tostring(root))
        with pytest.raises(ValueError, match="Sleep is not available in Fan Only"):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(preset=protocol.models.Preset.SLEEP)
            )
        client._send.assert_awaited_once()
        assert b"SyncStatusReq" in client._send.await_args.args[0]

    asyncio.run(scenario())


def test_fan_only_still_allows_clearing_sleep(protocol, state):
    current = replace(
        state, operation=protocol.models.Operation.FAN, sleep="sleepMode1:0"
    )
    desired = protocol.models.apply_changes(
        current, protocol.models.TfiacChanges(preset=protocol.models.Preset.NONE)
    )
    assert desired.sleep == "off"


@pytest.mark.parametrize("power", ["on", "off"])
@pytest.mark.parametrize("operation,target", [("cool", 60.8), ("heat", 87.8)])
def test_boost_sets_documented_target_without_forcing_power_or_fan(
    protocol, state, power, operation, target
):
    current = replace(
        state,
        operation=protocol.models.Operation(operation),
        power=protocol.models.Power(power),
    )
    desired = protocol.models.apply_changes(
        current, protocol.models.TfiacChanges(preset=protocol.models.Preset.BOOST)
    )
    assert desired.target_temperature == target
    assert desired.operation == current.operation
    assert desired.power == current.power
    assert desired.fan == current.fan
    assert desired.preset == protocol.models.Preset.BOOST
    assert not protocol.api._changes_confirmed(
        replace(desired, target_temperature=current.target_temperature),
        desired,
        protocol.models.TfiacChanges(preset=protocol.models.Preset.BOOST),
    )


@pytest.mark.parametrize("operation", ["selfFeel", "dehumi", "fan"])
def test_boost_refuses_undocumented_operations_before_write(protocol, state, operation):
    current = replace(state, operation=protocol.models.Operation(operation))
    with pytest.raises(ValueError, match="only in Cool or Heat"):
        protocol.models.apply_changes(
            current, protocol.models.TfiacChanges(preset=protocol.models.Preset.BOOST)
        )


def test_no_preset_capability_is_unknown_and_cannot_be_written(protocol, state):
    current = replace(state, sleep=None, turbo=None)
    assert current.preset is None
    with pytest.raises(ValueError, match="does not report presets"):
        protocol.models.apply_changes(
            current, protocol.models.TfiacChanges(preset=protocol.models.Preset.NONE)
        )


@pytest.mark.parametrize(
    "extra", [{"display": True}, {"target_temperature": 78}, {"swing_vertical": True}]
)
def test_presets_reject_mixed_intent_before_io(protocol, extra):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock()
        with pytest.raises(ValueError, match="separate command"):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(
                    preset=protocol.models.Preset.BOOST, **extra
                )
            )
        client._send.assert_not_awaited()

    asyncio.run(scenario())
