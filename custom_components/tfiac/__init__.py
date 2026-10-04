"""Set up TFIAC with one client and coordinator per config entry."""

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api import TfiacClient
from .const import CONF_COMMAND_PROFILE
from .coordinator import TfiacConfigEntry, TfiacCoordinator

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: TfiacConfigEntry) -> bool:
    """Retry setup automatically when the first device read fails."""
    client = TfiacClient(entry.options.get(CONF_HOST, entry.data[CONF_HOST]))
    coordinator = TfiacCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Retire the opt-in without touching legacy host overrides or user options.
    if CONF_COMMAND_PROFILE in entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={
                key: value
                for key, value in entry.options.items()
                if key != CONF_COMMAND_PROFILE
            },
        )
    # Remove only our superseded beta switches. Keep climate/Display/Beep IDs.
    registry = er.async_get(hass)
    removed_ids = {f"{entry.entry_id}_eco", f"{entry.entry_id}_turbo"}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.domain == "switch"
            and entity.platform == "tfiac"
            and entity.unique_id in removed_ids
        ):
            registry.async_remove(entity.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TfiacConfigEntry) -> bool:
    """Unload entities; HA handles coordinator shutdown via the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
