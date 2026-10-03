"""Read-only optional temperature sensors for TFIAC."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TfiacConfigEntry, TfiacCoordinator
from .entity import TfiacEntity

OUTDOOR_TEMPERATURE = SensorEntityDescription(
    key="outdoor_temperature",
    translation_key="outdoor_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
    suggested_display_precision=1,
    # The OutdoorTemp unit/sentinel semantics still require hardware validation.
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TfiacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the optional sensor only after a usable initial measurement."""
    if entry.runtime_data.data.capabilities.outdoor_temperature:
        async_add_entities([TfiacOutdoorTemperatureSensor(entry.runtime_data)])


class TfiacOutdoorTemperatureSensor(TfiacEntity, SensorEntity):
    """Read the shared snapshot; never poll or write the AC independently."""

    entity_description = OUTDOOR_TEMPERATURE

    def __init__(self, coordinator: TfiacCoordinator) -> None:
        super().__init__(coordinator, OUTDOOR_TEMPERATURE.key)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.outdoor_temperature
