"""Lifecycle and entity-registry compatibility with actual HA setup."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.tfiac.api import TfiacTimeoutError

pytestmark = pytest.mark.asyncio


async def test_setup_reload_unload_keep_registry(hass, entry, client):
    registry = er.async_get(hass)
    previous = registry.async_get_or_create(
        "climate",
        "tfiac",
        entry.entry_id,
        suggested_object_id="existing_ac",
        config_entry=entry,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.client is client
    assert entry.unique_id == "legacy-id"
    assert entry.version == 1
    assert hass.states.get(previous.entity_id) is not None
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].identifiers == {("tfiac", entry.entry_id)}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert hass.states.get(previous.entity_id).state == "auto"
    assert coordinator._shutdown_requested
    reloaded_coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state == ConfigEntryState.NOT_LOADED
    assert reloaded_coordinator._shutdown_requested
    assert not hasattr(entry, "runtime_data")


async def test_offline_setup_retries(hass, entry, client):
    client.async_update.side_effect = TfiacTimeoutError()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.SETUP_RETRY
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_legacy_host_and_name(hass, entry, client):
    hass.config_entries.async_update_entry(
        entry, options={"host": "192.0.2.2", "friendly_name": "Bedroom"}
    )
    with patch(
        "custom_components.tfiac.TfiacClient", return_value=client
    ) as constructor:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    constructor.assert_called_once_with("192.0.2.2")
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert devices[0].name == "Bedroom"
