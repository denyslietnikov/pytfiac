"""Common TFIAC entity identity and device metadata."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_FRIENDLY_NAME, DOMAIN
from .coordinator import TfiacCoordinator


class TfiacEntity(CoordinatorEntity[TfiacCoordinator]):
    """Read state and availability from the shared coordinator."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: TfiacCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=(
                entry.options.get(CONF_FRIENDLY_NAME)
                or coordinator.data.name
                or entry.title
                or "TFIAC"
            ),
        )
