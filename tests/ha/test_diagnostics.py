"""Diagnostics are serializable, redacted, read-only, and require no device I/O."""

import json
from dataclasses import replace

import pytest
from homeassistant.components.diagnostics import REDACTED

from custom_components.tfiac.diagnostics import async_get_config_entry_diagnostics

pytestmark = pytest.mark.asyncio


async def test_diagnostics_redact_config_and_raw_firmware_data(
    hass, entry, client, ha_state
):
    hass.config_entries.async_update_entry(
        entry,
        data={"host": "private-host.local", "future_token": "config-secret"},
        options={"host": "192.0.2.99", "friendly_name": "Private bedroom"},
    )
    client.async_update.return_value = replace(
        ha_state,
        name="Private AC name",
        display=True,
        raw_fields=ha_state.raw_fields + (("FirmwareToken", "firmware-secret"),),
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    before_calls = client.async_update.await_count
    data = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(data)
    for private in (
        "private-host.local",
        "192.0.2.99",
        "config-secret",
        "Private bedroom",
        "Private AC name",
        "firmware-secret",
        "Katie AC",
    ):
        assert private not in serialized
    assert data["entry_data"] == {"host": REDACTED, "future_token": REDACTED}
    assert data["status"]["name"] == REDACTED
    assert data["capabilities"]["display"] is True
    assert "FirmwareToken" in data["reported_fields"]
    assert "OutdoorTemp:ambiguous_zero" in data["optional_issues"]
    assert data["notes"]["decoded_status_does_not_prove_writability"]
    assert data["notes"]["target_temperature_contract"] == {
        "native_min": 61,
        "native_max": 88,
        "numeric_decimal_places": 2,
        "hardware_step": None,
        "degree_half_determines_step": False,
        "out_of_range_requests_are_clamped": False,
    }
    assert client.async_update.await_count == before_calls
    client.async_apply_changes.assert_not_awaited()
    assert entry.data["future_token"] == "config-secret"


async def test_offline_diagnostics_do_not_export_exception_message(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.async_set_update_error(RuntimeError("secret-host.local"))
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["available"] is False
    assert data["last_error_type"] == "RuntimeError"
    assert "secret-host.local" not in json.dumps(data)
    client.async_update.assert_awaited_once()


async def test_diagnostics_keep_unknown_sleep_profile_private(
    hass, entry, client, ha_state
):
    client.async_update.return_value = replace(ha_state, sleep="private-profile-secret")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["status"]["sleep_active"] is True
    assert "private-profile-secret" not in json.dumps(data)


async def test_diagnostics_before_setup_or_after_unload(hass, entry, client):
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["runtime_loaded"] is False
    assert data["status"] is None
    assert data["available"] is False
    client.async_update.assert_not_awaited()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["runtime_loaded"] is False
    assert data["status"] is None
