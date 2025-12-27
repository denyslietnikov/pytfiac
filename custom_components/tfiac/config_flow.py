"""Config flow for TFIAC integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from pytfiac import Tfiac

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TFIAC."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            tfiac_client = Tfiac(user_input[CONF_HOST])
            await tfiac_client.update()
        except Exception:
            errors["base"] = "cannot_connect"
        else:
            device_name = tfiac_client.name
            if self._host_already_configured(user_input[CONF_HOST]):
                return self.async_abort(reason="already_configured")

            return self.async_create_entry(
                title=device_name or user_input[CONF_HOST], data=user_input
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration."""
        entry = self._get_reconfigure_entry()

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    }
                ),
            )

        errors = {}

        try:
            tfiac_client = Tfiac(user_input[CONF_HOST])
            await tfiac_client.update()
        except Exception:
            errors["base"] = "cannot_connect"
        else:
            if self._host_already_configured(
                user_input[CONF_HOST], exclude_entry_id=entry.entry_id
            ):
                return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(
                entry, data=user_input, title=tfiac_client.name or user_input[CONF_HOST]
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                }
            ),
            errors=errors,
        )

    def _host_already_configured(
        self, host: str, exclude_entry_id: str | None = None
    ) -> bool:
        """Check if a host is already configured."""
        for entry in self._async_current_entries():
            if exclude_entry_id and entry.entry_id == exclude_entry_id:
                continue
            if entry.data.get(CONF_HOST) == host:
                return True
            if entry.options.get(CONF_HOST) == host:
                return True
        return False


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
