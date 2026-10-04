"""Reported Display/Beep controls, sharing the climate coordinator."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TfiacConfigEntry, TfiacCoordinator
from .entity import TfiacEntity
from .models import TfiacChanges


@dataclass(frozen=True, kw_only=True)
class TfiacSwitchDescription(SwitchEntityDescription):
    """One independently observed boolean, without invented preset rules."""

    state_field: str


SWITCHES = tuple(
    TfiacSwitchDescription(
        key=field,
        translation_key=field,
        state_field=field,
    )
    for field in ("display", "beep")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TfiacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create usable controls and retire integration-imposed beta disablement."""
    coordinator = entry.runtime_data
    descriptions = [
        description
        for description in SWITCHES
        if getattr(coordinator.data.capabilities, description.state_field)
    ]
    registry = er.async_get(hass)
    if not entry.pref_disable_new_entities:
        for description in descriptions:
            entity_id = registry.async_get_entity_id(
                "switch", "tfiac", f"{entry.entry_id}_{description.key}"
            )
            if (
                entity_id is not None
                and (entity := registry.async_get(entity_id)) is not None
                and entity.config_entry_id == entry.entry_id
                and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION
            ):
                registry.async_update_entity(entity_id, disabled_by=None)
    async_add_entities(
        TfiacOptionalSwitch(coordinator, description) for description in descriptions
    )


class TfiacOptionalSwitch(TfiacEntity, SwitchEntity):
    """Publish only the subsequent status read, never the desired flag or ACK."""

    entity_description: TfiacSwitchDescription

    def __init__(
        self, coordinator: TfiacCoordinator, description: TfiacSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Missing/invalid status is unknown; UDP failures are unavailable."""
        return getattr(self.coordinator.data, self.entity_description.state_field)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        await self.coordinator.async_apply_changes(
            TfiacChanges(**{self.entity_description.state_field: value})
        )
