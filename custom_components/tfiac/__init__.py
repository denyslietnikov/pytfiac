"""Set up TFIAC with one client and coordinator per config entry."""

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .api import TfiacClient
from .coordinator import TfiacConfigEntry, TfiacCoordinator

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TfiacConfigEntry) -> bool:
    """Retry setup automatically when the first device read fails."""
    client = TfiacClient(entry.options.get(CONF_HOST, entry.data[CONF_HOST]))
    coordinator = TfiacCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TfiacConfigEntry) -> bool:
    """Unload entities; HA handles coordinator shutdown via the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
