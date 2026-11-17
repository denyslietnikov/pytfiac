"""Native climate API preserves wire units without HA deprecation warnings."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.tfiac.models import Operation

pytestmark = pytest.mark.asyncio


async def test_importing_climate_does_not_use_deprecated_temperature_api(caplog):
    """Exercise class creation even when another test imported climate earlier."""
    spec = importlib.util.spec_from_file_location(
        "custom_components.tfiac.temperature_api_probe",
        Path(__file__).parents[2] / "custom_components/tfiac/climate.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not [
        record.getMessage()
        for record in caplog.records
        if record.name == module.__name__ and "deprecated" in record.getMessage()
    ]


@pytest.mark.parametrize(
    "units,current,target",
    [(METRIC_SYSTEM, 21.1, 25), (US_CUSTOMARY_SYSTEM, 70, 77)],
)
async def test_native_temperatures_keep_wire_units_and_display_units(
    hass, entry, client, units, current, target, caplog
):
    """Legacy getters or double conversion must fail this regression test."""
    hass.config.units = units
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "climate", "tfiac", entry.entry_id
    )
    entity = hass.data["climate"].get_entity(entity_id)
    assert getattr(entity, "native_current_temperature", None) == 70
    assert getattr(entity, "native_target_temperature", None) == 77
    state = hass.states.get(entity_id)
    assert state.attributes["current_temperature"] == current
    assert state.attributes["temperature"] == target
    assert not [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.tfiac")
        and "deprecated" in record.getMessage()
    ]


async def test_native_temperatures_follow_updates_and_fan_only(
    hass, entry, client, ha_state
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "climate", "tfiac", entry.entry_id
    )
    entity = hass.data["climate"].get_entity(entity_id)
    entry.runtime_data.async_set_updated_data(
        replace(ha_state, operation=Operation.FAN, current_temperature=None)
    )
    await hass.async_block_till_done()
    assert getattr(entity, "native_current_temperature", "missing") is None
    assert getattr(entity, "native_target_temperature", "missing") is None
    assert hass.states.get(entity_id).attributes["temperature"] is None
    entry.runtime_data.async_set_updated_data(
        replace(ha_state, operation=Operation.COOL, target_temperature=78.8)
    )
    await hass.async_block_till_done()
    assert entity.native_current_temperature == 70
    assert entity.native_target_temperature == 78.8
