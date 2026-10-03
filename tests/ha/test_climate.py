"""HA climate services, capability flags, and availability recovery."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.tfiac.api import TfiacTimeoutError
from custom_components.tfiac.climate import TfiacClimate
from custom_components.tfiac.models import Fan, Operation, Power, TfiacChanges

pytestmark = pytest.mark.asyncio


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[
        0
    ].entity_id


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
            {"swing_mode": "both"},
            TfiacChanges(swing_horizontal=True, swing_vertical=True),
        ),
        ("set_preset_mode", {"preset_mode": "sleep"}, TfiacChanges(sleep=True)),
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
        swing_horizontal=None,
        swing_vertical=None,
        current_temperature=None,
    )
    entity_id = await setup(hass, entry)
    attrs = hass.states.get(entity_id).attributes
    assert not attrs["supported_features"] & ClimateEntityFeature.PRESET_MODE
    assert not attrs["supported_features"] & ClimateEntityFeature.SWING_MODE
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
