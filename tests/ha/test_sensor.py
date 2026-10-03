"""Optional sensor identity, defaults, shared polling, and availability."""

from dataclasses import replace

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.tfiac.api import TfiacTimeoutError

pytestmark = pytest.mark.asyncio


def sensor_entries(hass, entry):
    return [
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain == "sensor"
    ]


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_no_outdoor_sensor_for_legacy_zero(hass, entry, client):
    await setup(hass, entry)
    assert sensor_entries(hass, entry) == []
    assert entry.runtime_data.data.outdoor_temperature is None


async def test_valid_optional_sensor_is_disabled_by_default(
    hass, entry, client, ha_state
):
    client.async_update.return_value = replace(ha_state, outdoor_temperature=72)
    await setup(hass, entry)
    sensor = sensor_entries(hass, entry)[0]
    assert sensor.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert sensor.unique_id == f"{entry.entry_id}_outdoor_temperature"
    assert sensor.original_name == "Outdoor temperature"
    assert hass.states.get(sensor.entity_id) is None
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    client.async_update.assert_awaited_once()
    client.async_apply_changes.assert_not_awaited()


@pytest.mark.parametrize("metric", [False, True])
async def test_enabled_sensor_uses_coordinator_and_keeps_identity(
    hass, entry, client, ha_state, metric
):
    if metric:
        hass.config.units = METRIC_SYSTEM
    client.async_update.return_value = replace(ha_state, outdoor_temperature=72)
    registry = er.async_get(hass)
    previous = registry.async_get_or_create(
        "sensor",
        "tfiac",
        f"{entry.entry_id}_outdoor_temperature",
        config_entry=entry,
        suggested_object_id="ac_outdoor",
        disabled_by=None,
    )
    await setup(hass, entry)
    state = hass.states.get(previous.entity_id)
    assert float(state.state) == pytest.approx(22.2 if metric else 72, abs=0.05)
    assert state.attributes["device_class"] == "temperature"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["unit_of_measurement"] == ("°C" if metric else "°F")
    assert len(entry.runtime_data._listeners) == 2
    client.async_update.return_value = replace(ha_state, outdoor_temperature=None)
    await entry.runtime_data.async_refresh()
    assert hass.states.get(previous.entity_id).state == "unknown"
    assert entry.runtime_data.last_update_success
    client.async_update.side_effect = TfiacTimeoutError()
    await entry.runtime_data.async_refresh()
    assert hass.states.get(previous.entity_id).state == "unavailable"
    client.async_update.side_effect = None
    client.async_update.return_value = replace(ha_state, outdoor_temperature=72)
    await entry.runtime_data.async_refresh()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    sensors = sensor_entries(hass, entry)
    assert len(sensors) == 1
    assert sensors[0].entity_id == previous.entity_id
    assert sensors[0].disabled_by is None
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    client.async_apply_changes.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
