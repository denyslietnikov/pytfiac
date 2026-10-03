"""Shared polling and command lifecycle for one TFIAC config entry."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TfiacClient, TfiacCommandNotConfirmedError, TfiacError
from .const import DOMAIN
from .models import TfiacChanges, TfiacState

_LOGGER = logging.getLogger(__name__)

type TfiacConfigEntry = ConfigEntry[TfiacCoordinator]


class TfiacCoordinator(DataUpdateCoordinator[TfiacState]):
    """Publish only device-read snapshots; use HA's availability machinery."""

    def __init__(
        self, hass: HomeAssistant, entry: TfiacConfigEntry, client: TfiacClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=30),
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> TfiacState:
        try:
            return await self.client.async_update()
        except TfiacError as err:
            raise UpdateFailed(str(err)) from err

    async def async_apply_changes(self, changes: TfiacChanges) -> None:
        """Perform the atomic transaction and publish its final status read."""
        try:
            state = await self.client.async_apply_changes(changes)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except TfiacCommandNotConfirmedError as err:
            # A failed action is not a failed connection. Publish the last valid
            # device read, keep entities available, and report failure to HA.
            self.async_set_updated_data(err.last_state)
            raise HomeAssistantError(f"TFIAC command failed: {err}") from err
        except TfiacError as err:
            self.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(f"TFIAC command failed: {err}") from err
        # This is confirmed by SyncStatusReq, not an optimistic write result.
        # No coordinator refresh is awaited while the client lock is held.
        self.async_set_updated_data(state)
