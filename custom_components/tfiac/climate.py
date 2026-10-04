"""TFIAC climate entity backed by a shared immutable state snapshot."""

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MIDDLE,
    PRESET_BOOST,
    PRESET_NONE,
    PRESET_SLEEP,
    SWING_OFF,
    SWING_ON,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TfiacConfigEntry, TfiacCoordinator
from .entity import TfiacEntity
from .models import (
    MAX_TEMP,
    MIN_TEMP,
    Fan,
    Operation,
    Power,
    Preset,
    TfiacChanges,
    sleep_allowed,
)

HVAC_MAP = {
    HVACMode.HEAT: Operation.HEAT,
    HVACMode.AUTO: Operation.AUTO,
    HVACMode.DRY: Operation.DRY,
    HVACMode.FAN_ONLY: Operation.FAN,
    HVACMode.COOL: Operation.COOL,
}
HVAC_MAP_REV = {value: key for key, value in HVAC_MAP.items()}
FAN_MAP = {
    FAN_AUTO: Fan.AUTO,
    FAN_LOW: Fan.LOW,
    FAN_MIDDLE: Fan.MIDDLE,
    FAN_HIGH: Fan.HIGH,
}
FAN_MAP_REV = {value: key for key, value in FAN_MAP.items()}
SWING_MAP = {SWING_OFF: False, SWING_ON: True}
SWING_MAP_REV = {value: key for key, value in SWING_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TfiacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the main entity without opening a second device connection."""
    async_add_entities([TfiacClimate(config_entry.runtime_data)])


class TfiacClimate(TfiacEntity, ClimateEntity):
    """Expose the existing climate controls through Home Assistant APIs."""

    _attr_translation_key = "climate"
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    # Unknown hardware increment: Degree_Half is only a decoded status flag.
    # Omitting a step is intentional, not an assertion of 1 °F or 0.5 °C support.
    _attr_target_temperature_step = None
    _attr_fan_modes = list(FAN_MAP)
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_MAP]
    _attr_swing_modes = list(SWING_MAP)
    _attr_swing_horizontal_modes = list(SWING_MAP)

    def __init__(self, coordinator: TfiacCoordinator) -> None:
        super().__init__(coordinator)
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        state = coordinator.data
        self._attr_preset_modes = []
        if state.sleep is not None:
            self._attr_preset_modes.append(PRESET_SLEEP)
        if state.turbo is not None:
            self._attr_preset_modes.append(PRESET_BOOST)
        if self._attr_preset_modes:
            self._attr_preset_modes.insert(0, PRESET_NONE)
            features |= ClimateEntityFeature.PRESET_MODE
        if state.swing_vertical is not None:
            features |= ClimateEntityFeature.SWING_MODE
        if state.swing_horizontal is not None:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        self._attr_supported_features = features

    @property
    def target_temperature(self) -> float:
        return self.coordinator.data.target_temperature

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.data.current_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        state = self.coordinator.data
        return (
            HVACMode.OFF if state.power == Power.OFF else HVAC_MAP_REV[state.operation]
        )

    @property
    def fan_mode(self) -> str:
        return FAN_MAP_REV[self.coordinator.data.fan]

    @property
    def swing_mode(self) -> str | None:
        return SWING_MAP_REV.get(self.coordinator.data.swing_vertical)

    @property
    def swing_horizontal_mode(self) -> str | None:
        return SWING_MAP_REV.get(self.coordinator.data.swing_horizontal)

    @property
    def preset_mode(self) -> str | None:
        return self.coordinator.data.preset

    @property
    def preset_modes(self) -> list[str]:
        """Filter choices by current operation, without hiding actual flags."""
        return [
            preset
            for preset in self._attr_preset_modes
            if preset != PRESET_SLEEP or sleep_allowed(self.coordinator.data.operation)
        ]

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Apply temperature and optional HVAC mode in one full-state command."""
        mode = kwargs.get(ATTR_HVAC_MODE)
        try:
            operation = HVAC_MAP[mode] if mode and mode != HVACMode.OFF else None
        except KeyError as err:
            raise ServiceValidationError("Unsupported HVAC mode") from err
        await self.coordinator.async_apply_changes(
            TfiacChanges(
                target_temperature=kwargs.get(ATTR_TEMPERATURE),
                operation=operation,
                power=Power.OFF if mode == HVACMode.OFF else None,
            )
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            changes = TfiacChanges(power=Power.OFF)
        else:
            try:
                changes = TfiacChanges(operation=HVAC_MAP[hvac_mode])
            except KeyError as err:
                raise ServiceValidationError("Unsupported HVAC mode") from err
        await self.coordinator.async_apply_changes(changes)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        try:
            fan = FAN_MAP[fan_mode]
        except KeyError as err:
            raise ServiceValidationError("Unsupported fan mode") from err
        await self.coordinator.async_apply_changes(TfiacChanges(fan=fan))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Change vertical swing without changing the fresh horizontal state."""
        try:
            vertical = SWING_MAP[swing_mode]
        except KeyError as err:
            raise ServiceValidationError("Unsupported vertical swing mode") from err
        await self.coordinator.async_apply_changes(
            TfiacChanges(swing_vertical=vertical)
        )

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Change horizontal swing without changing the fresh vertical state."""
        try:
            horizontal = SWING_MAP[swing_horizontal_mode]
        except KeyError as err:
            raise ServiceValidationError("Unsupported horizontal swing mode") from err
        await self.coordinator.async_apply_changes(
            TfiacChanges(swing_horizontal=horizontal)
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self.preset_modes:
            raise ServiceValidationError("Unsupported preset mode")
        await self.coordinator.async_apply_changes(
            TfiacChanges(preset=Preset(preset_mode))
        )

    async def async_turn_on(self) -> None:
        """Restore the operation reported by the device instead of forcing cool."""
        await self.coordinator.async_apply_changes(TfiacChanges(power=Power.ON))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_apply_changes(TfiacChanges(power=Power.OFF))
