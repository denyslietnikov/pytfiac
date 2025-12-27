"""Options flow for TFIAC integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)


class TFIACOptionsFlowHandler(OptionsFlow):
    """Handle TFIAC options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize TFIAC options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the TFIAC options."""
        if user_input is not None:
            new_host = user_input[CONF_HOST]
            options = {"friendly_name": user_input.get("friendly_name", "")}
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_HOST: new_host},
                options=options,
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self._options_schema(),
        )

    @callback
    def _options_schema(self):
        """Return the options schema."""
        from voluptuous import Required, Schema

        return Schema(
            {
                Required(
                    CONF_HOST,
                    default=self.config_entry.options.get(
                        CONF_HOST, self.config_entry.data.get(CONF_HOST)
                    ),
                ): str,
                Required(
                    "friendly_name",
                    default=self.config_entry.options.get("friendly_name", ""),
                ): str,
            }
        )


async def async_get_options_flow(config_entry: ConfigEntry) -> TFIACOptionsFlowHandler:
    """Get the options flow for this handler."""
    return TFIACOptionsFlowHandler(config_entry)
