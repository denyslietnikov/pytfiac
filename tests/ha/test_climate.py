"""HA climate services, capability flags, and availability recovery."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.icon import async_get_icons
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.tfiac.api import TfiacTimeoutError
from custom_components.tfiac.climate import TfiacClimate
from custom_components.tfiac.models import Fan, Operation, Power, Preset, TfiacChanges

pytestmark = pytest.mark.asyncio


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("climate", "tfiac", entry.entry_id)


async def test_boost_preset_icon(hass, entry, client, ha_state):
    """Expose only the Boost override through HA's native icon resources."""
    entity_id = await setup(hass, entry)
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.translation_key == "climate"
    assert registry_entry.unique_id == entry.entry_id
    assert hass.states.get(entity_id).attributes["friendly_name"] == ha_state.name
    assert "icon" not in hass.states.get(entity_id).attributes

    icons = await async_get_icons(hass, "entity", {"tfiac"})
    assert icons["tfiac"]["climate"][registry_entry.translation_key] == {
        "state_attributes": {"preset_mode": {"state": {"boost": "mdi:weather-windy"}}}
    }


@pytest.mark.parametrize(
    "service,data,expected",
    [
        (
            "set_temperature",
            {"temperature": 78, "hvac_mode": "heat"},
            TfiacChanges(target_temperature=78, operation=Operation.HEAT),
        ),
        ("set_hvac_mode", {"hvac_mode": "off"}, TfiacChanges(power=Power.OFF)),
        (
            "set_hvac_mode",
            {"hvac_mode": "cool"},
            TfiacChanges(operation=Operation.COOL),
        ),
        ("set_fan_mode", {"fan_mode": "middle"}, TfiacChanges(fan=Fan.MIDDLE)),
        (
            "set_swing_mode",
            {"swing_mode": "on"},
            TfiacChanges(swing_vertical=True),
        ),
        (
            "set_swing_horizontal_mode",
            {"swing_horizontal_mode": "on"},
            TfiacChanges(swing_horizontal=True),
        ),
        (
            "set_preset_mode",
            {"preset_mode": "sleep"},
            TfiacChanges(preset=Preset.SLEEP),
        ),
        (
            "set_preset_mode",
            {"preset_mode": "boost"},
            TfiacChanges(preset=Preset.BOOST),
        ),
        ("set_preset_mode", {"preset_mode": "none"}, TfiacChanges(preset=Preset.NONE)),
        ("turn_on", {}, TfiacChanges(power=Power.ON)),
        ("turn_off", {}, TfiacChanges(power=Power.OFF)),
    ],
)
async def test_services(hass, entry, client, service, data, expected):
    entity_id = await setup(hass, entry)
    await hass.services.async_call(
        "climate", service, {"entity_id": entity_id, **data}, blocking=True
    )
    client.async_apply_changes.assert_awaited_once_with(expected)


async def test_coordinator_offline_recovery(hass, entry, client, ha_state):
    entity_id = await setup(hass, entry)
    assert hass.states.get(entity_id).state == "auto"
    client.async_update.side_effect = TfiacTimeoutError()
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "unavailable"
    client.async_update.side_effect = None
    client.async_update.return_value = replace(ha_state, fan=Fan.MIDDLE)
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "auto"
    assert hass.states.get(entity_id).attributes["fan_mode"] == "middle"


async def test_unchanged_poll_does_not_dispatch(hass, entry, client):
    await setup(hass, entry)
    listener = Mock()
    remove = entry.runtime_data.async_add_listener(listener)
    await entry.runtime_data.async_refresh()
    listener.assert_not_called()
    remove()


async def test_command_error_and_recovery(hass, entry, client):
    entity_id = await setup(hass, entry)
    client.async_apply_changes.side_effect = TfiacTimeoutError()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    assert hass.states.get(entity_id).state == "unavailable"
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "auto"


async def test_command_validation_does_not_mark_offline(hass, entry, client):
    entity_id = await setup(hass, entry)
    client.async_apply_changes.side_effect = ValueError("Invalid target")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    assert hass.states.get(entity_id).state == "auto"


async def test_optional_features(hass, entry, client, ha_state):
    client.async_update.return_value = replace(
        ha_state,
        sleep=None,
        turbo=None,
        swing_horizontal=None,
        swing_vertical=None,
        current_temperature=None,
    )
    entity_id = await setup(hass, entry)
    attrs = hass.states.get(entity_id).attributes
    assert not attrs["supported_features"] & ClimateEntityFeature.PRESET_MODE
    assert not attrs["supported_features"] & ClimateEntityFeature.SWING_MODE
    assert not attrs["supported_features"] & ClimateEntityFeature.SWING_HORIZONTAL_MODE
    assert attrs["current_temperature"] is None


async def test_turn_on_preserves_last_operation(hass, entry, client, ha_state):
    client.async_update.return_value = replace(
        ha_state, power=Power.OFF, operation=Operation.HEAT
    )
    await setup(hass, entry)
    climate = TfiacClimate(entry.runtime_data)
    assert climate.hvac_mode == HVACMode.OFF
    await climate.async_turn_on()
    client.async_apply_changes.assert_awaited_once_with(TfiacChanges(power=Power.ON))


@pytest.mark.parametrize("sleep", [None, "off", "sleepMode1:0:0"])
@pytest.mark.parametrize("turbo", [None, False, True])
async def test_preset_capabilities_and_actual_status(
    hass, entry, client, ha_state, sleep, turbo
):
    state = replace(ha_state, sleep=sleep, turbo=turbo)
    client.async_update.return_value = state
    entity_id = await setup(hass, entry)
    attrs = hass.states.get(entity_id).attributes
    modes = (["sleep"] if sleep is not None else []) + (
        ["boost"] if turbo is not None else []
    )
    assert bool(attrs["supported_features"] & ClimateEntityFeature.PRESET_MODE) == bool(
        modes
    )
    if modes:
        assert attrs["preset_modes"] == ["none", *modes]
        assert attrs["preset_mode"] == state.preset


async def test_eco_is_not_a_preset(hass, entry, client):
    entity_id = await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": "eco"},
            blocking=True,
        )
    client.async_apply_changes.assert_not_awaited()


async def test_metric_service_temperature_is_converted_by_ha(hass, entry, client):
    hass.config.units = METRIC_SYSTEM
    entity_id = await setup(hass, entry)
    assert hass.states.get(entity_id).attributes["temperature"] == 25
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 26},
        blocking=True,
    )
    requested = client.async_apply_changes.await_args.args[0]
    assert requested.target_temperature == pytest.approx(78.8)


async def test_invalid_fan_is_rejected_before_device_write(hass, entry, client):
    entity_id = await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": "medium"},
            blocking=True,
        )
    client.async_apply_changes.assert_not_awaited()


@pytest.mark.parametrize("horizontal", [None, False, True])
@pytest.mark.parametrize("vertical", [None, False, True])
async def test_swing_capabilities_and_state_per_axis(
    hass, entry, client, ha_state, horizontal, vertical
):
    client.async_update.return_value = replace(
        ha_state, swing_horizontal=horizontal, swing_vertical=vertical
    )
    entity_id = await setup(hass, entry)
    attrs = hass.states.get(entity_id).attributes
    features = attrs["supported_features"]
    for value, feature, mode_attr, modes_attr in (
        (vertical, ClimateEntityFeature.SWING_MODE, "swing_mode", "swing_modes"),
        (
            horizontal,
            ClimateEntityFeature.SWING_HORIZONTAL_MODE,
            "swing_horizontal_mode",
            "swing_horizontal_modes",
        ),
    ):
        assert bool(features & feature) is (value is not None)
        if value is None:
            assert mode_attr not in attrs
            assert modes_attr not in attrs
        else:
            assert attrs[mode_attr] == ("on" if value else "off")
            assert attrs[modes_attr] == ["off", "on"]


@pytest.mark.parametrize("legacy_mode", ["horizontal", "vertical", "both"])
async def test_legacy_swing_mode_rejected_before_write(
    hass, entry, client, legacy_mode
):
    entity_id = await setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_swing_mode",
            {"entity_id": entity_id, "swing_mode": legacy_mode},
            blocking=True,
        )
    client.async_apply_changes.assert_not_awaited()


@pytest.mark.parametrize(
    "service,attribute,missing_field",
    [
        ("set_swing_mode", "swing_mode", "swing_vertical"),
        ("set_swing_horizontal_mode", "swing_horizontal_mode", "swing_horizontal"),
    ],
)
async def test_missing_swing_axis_rejected_before_write(
    hass, entry, client, ha_state, service, attribute, missing_field
):
    client.async_update.return_value = replace(ha_state, **{missing_field: None})
    entity_id = await setup(hass, entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id, attribute: "on"}, blocking=True
        )
    client.async_apply_changes.assert_not_awaited()
