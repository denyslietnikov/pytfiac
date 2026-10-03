"""Configure and reconfigure TFIAC using a verified status response."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import AbortFlow

from .api import TfiacClient, TfiacError
from .const import DOMAIN
from .models import TfiacState

_LOGGER = logging.getLogger(__name__)


class TfiacConfigFlow(ConfigFlow, domain=DOMAIN):
    """No stable hardware ID is known; keep identity local to each config entry."""

    VERSION = 1

    def _abort_if_host_configured(self, host: str) -> None:
        self._async_abort_entries_match({CONF_HOST: host})
        # Older entries may hold the effective host in options instead of data.
        for entry in self._async_current_entries(include_ignore=False):
            if entry.entry_id == self.context.get("entry_id"):
                continue
            if entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)) == host:
                raise AbortFlow("already_configured")

    async def _validate_host(
        self, host: str, errors: dict[str, str]
    ) -> TfiacState | None:
        try:
            return await TfiacClient(host).async_update()
        except TfiacError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception validating TFIAC connection")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            self._abort_if_host_configured(host)
            if not host:
                errors[CONF_HOST] = "cannot_connect"
            elif (state := await self._validate_host(host, errors)) is not None:
                self._abort_if_host_configured(host)
                return self.async_create_entry(
                    title=state.name or host, data={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            self._abort_if_host_configured(host)
            if not host:
                errors[CONF_HOST] = "cannot_connect"
            elif (state := await self._validate_host(host, errors)) is not None:
                self._abort_if_host_configured(host)
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host},
                    options={
                        key: val
                        for key, val in entry.options.items()
                        if key != CONF_HOST
                    },
                    title=state.name or entry.title,
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST,
                        default=entry.options.get(CONF_HOST, entry.data[CONF_HOST]),
                    ): str
                }
            ),
            errors=errors,
        )
