"""Privacy-safe diagnostics from the existing coordinator snapshot."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import TfiacConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TfiacConfigEntry
) -> dict[str, Any]:
    """Do not send network requests or export arbitrary firmware/config values."""
    base = {
        "entry_data": async_redact_data(dict(entry.data), set(entry.data)),
        "entry_options": async_redact_data(dict(entry.options), set(entry.options)),
        "runtime_loaded": False,
        "available": False,
        "status": None,
        "capabilities": {},
        "reported_fields": [],
        "optional_issues": [],
        "last_error_type": None,
        "optional_command_contract": None,
    }
    if not hasattr(entry, "runtime_data"):
        return base
    coordinator = entry.runtime_data
    state = coordinator.data
    return {
        **base,
        "runtime_loaded": True,
        "available": coordinator.last_update_success,
        # Exception messages can contain a hostname/IP; export only the type.
        "last_error_type": (
            type(coordinator.last_exception).__name__
            if coordinator.last_exception is not None
            else None
        ),
        "capabilities": asdict(state.capabilities),
        "optional_command_contract": {
            "profile": coordinator.client.command_profile.value,
            # No identity/firmware detection certifies the connected device.
            # Limited Livingroom validation does not make all models validated.
            "hardware_validated": False,
            "command_fields": dict(coordinator.client.command_profile.command_fields),
        },
        "status": {
            "name": REDACTED if state.name is not None else None,
            "power": state.power,
            "operation": state.operation,
            "target_temperature": state.target_temperature,
            "current_temperature": state.current_temperature,
            "swing_horizontal": state.swing_horizontal,
            "swing_vertical": state.swing_vertical,
            "fan": state.fan,
            "sleep_active": None if state.sleep is None else state.sleep != "off",
            "eco": state.eco,
            "turbo": state.turbo,
            "display": state.display,
            "beep": state.beep,
            "outdoor_temperature": state.outdoor_temperature,
            "degree_half": state.degree_half,
        },
        "reported_fields": [name for name, _ in state.raw_fields],
        "optional_issues": list(state.optional_issues),
        "notes": {
            "decoded_status_does_not_prove_writability": True,
            "temperature_unit_assumption": "legacy Fahrenheit",
            "outdoor_zero_is_treated_as_unknown": True,
            "raw_values_are_not_exported": True,
        },
    }
