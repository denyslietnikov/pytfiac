"""Climate platform that offers a climate device for the TFIAC protocol."""

import logging
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pytfiac import Tfiac

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HVAC_MAP = {
    HVACMode.HEAT: "heat",
    HVACMode.AUTO: "selfFeel",
    HVACMode.DRY: "dehumi",
    HVACMode.FAN_ONLY: "fan",
    HVACMode.COOL: "cool",
    HVACMode.OFF: "off",
}

HVAC_MAP_REV = {v: k for k, v in HVAC_MAP.items()}

CURR_TEMP = "current_temp"
TARGET_TEMP = "target_temp"
OPERATION_MODE = "operation"
FAN_MODE = "fan_mode"
SWING_MODE = "swing_mode"
ON_MODE = "is_on"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TFIAC climate device."""
    host = config_entry.options.get(CONF_HOST, config_entry.data[CONF_HOST])
    tfiac_client = Tfiac(host)
    try:
        await tfiac_client.update()
    except Exception:
        _LOGGER.warning(
            "Initial update failed for %s, proceeding anyway",
            host,
        )
    async_add_entities(
        [
            TfiacClimate(
                tfiac_client,
                config_entry.entry_id,
                config_entry.options.get("friendly_name", ""),
            )
        ]
    )


class TfiacClimate(ClimateEntity):
    """TFIAC class."""

    _attr_supported_features = (
        ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = 61
    _attr_max_temp = 88
    _attr_fan_modes = [FAN_AUTO, FAN_HIGH, FAN_MEDIUM, FAN_LOW]
    _attr_hvac_modes = list(HVAC_MAP)
    _attr_swing_modes = [SWING_OFF, SWING_HORIZONTAL, SWING_VERTICAL, SWING_BOTH]

    def __init__(self, client: Tfiac, entry_id: str, friendly_name: str) -> None:
        """Init class."""
        self._client = client
        self._attr_unique_id = entry_id
        self._friendly_name = friendly_name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": friendly_name or client.name or "TFIAC",
        }
        self._attr_should_poll = True

    async def async_update(self) -> None:
        """Update status via socket polling."""
        try:
            await self._client.update()
            self._attr_available = True
        except Exception:
            self._attr_available = False

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._friendly_name or self._client.name or f"TFIAC {self._client._host}"

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._client.status.get(TARGET_TEMP)

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._client.status.get(CURR_TEMP)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac operation ie. heat, cool mode.

        Need to be one of HVAC_MODE_*.
        """
        if self._client.status.get(ON_MODE) != "on":
            return HVACMode.OFF

        state = self._client.status.get(OPERATION_MODE)
        return HVAC_MAP_REV.get(state)

    @property
    def fan_mode(self) -> str:
        """Return the fan setting."""
        return self._client.status.get(FAN_MODE, "").lower()

    @property
    def swing_mode(self) -> str:
        """Return the swing setting."""
        return self._client.status.get(SWING_MODE, "").lower()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temp := kwargs.get("temperature")) is not None:
            await self._client.set_state(TARGET_TEMP, temp)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._client.set_state(ON_MODE, "off")
        else:
            await self._client.set_state(OPERATION_MODE, HVAC_MAP[hvac_mode])

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        await self._client.set_state(FAN_MODE, fan_mode.capitalize())

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new swing mode."""
        await self._client.set_swing(swing_mode.capitalize())

    async def async_turn_on(self) -> None:
        """Turn device on."""
        await self._client.set_state(OPERATION_MODE, "cool")  # Default to cool

    async def async_turn_off(self) -> None:
        """Turn device off."""
        await self._client.set_state(ON_MODE, "off")
