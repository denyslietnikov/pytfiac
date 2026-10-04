"""Explicitly opted-in optional controls, sharing the climate coordinator."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
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
        entity_registry_enabled_default=False,
    )
    for field in ("display", "beep")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TfiacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Require both an explicit command contract and usable initial status."""
    coordinator = entry.runtime_data
    writable = dict(coordinator.client.command_profile.command_fields)
    async_add_entities(
        TfiacOptionalSwitch(coordinator, description)
        for description in SWITCHES
        if description.state_field in writable
        and getattr(coordinator.data.capabilities, description.state_field)
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
