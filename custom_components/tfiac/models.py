"""Pure TFIAC protocol models; no Home Assistant dependency."""

from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

MIN_TEMP = 61
MAX_TEMP = 88
SLEEP_MODE_ON = "sleepMode1:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0"


class Power(StrEnum):
    """Protocol power values."""

    ON = "on"
    OFF = "off"


class Operation(StrEnum):
    """Protocol operation values."""

    HEAT = "heat"
    AUTO = "selfFeel"
    DRY = "dehumi"
    FAN = "fan"
    COOL = "cool"


class Fan(StrEnum):
    """Protocol fan values."""

    AUTO = "Auto"
    LOW = "Low"
    MIDDLE = "Middle"
    HIGH = "High"


@dataclass(frozen=True, slots=True)
class TfiacState:
    """An immutable snapshot read from the device."""

    power: Power
    operation: Operation
    target_temperature: float
    fan: Fan
    current_temperature: float | None = None
    name: str | None = None
    swing_horizontal: bool | None = None
    swing_vertical: bool | None = None
    sleep: str | None = None


@dataclass(frozen=True, slots=True)
class TfiacChanges:
    """Only writable fields; None means leave unchanged."""

    power: Power | None = None
    operation: Operation | None = None
    target_temperature: float | None = None
    fan: Fan | None = None
    swing_horizontal: bool | None = None
    swing_vertical: bool | None = None
    sleep: bool | None = None


def apply_changes(state: TfiacState, changes: TfiacChanges) -> TfiacState:
    """Validate requested changes without inventing device-specific rules."""
    updates = {}
    for field, enum in (("power", Power), ("operation", Operation), ("fan", Fan)):
        if (value := getattr(changes, field)) is not None:
            updates[field] = enum(value)
    if changes.operation is not None:
        if changes.power == Power.OFF:
            raise ValueError("Cannot select an operation and turn off together")
        updates["power"] = Power.ON
    if (temperature := changes.target_temperature) is not None:
        if not isfinite(temperature) or not MIN_TEMP <= temperature <= MAX_TEMP:
            raise ValueError(f"Target temperature must be {MIN_TEMP}–{MAX_TEMP} °F")
        updates["target_temperature"] = float(temperature)
    for field in ("swing_horizontal", "swing_vertical"):
        if (value := getattr(changes, field)) is not None:
            if getattr(state, field) is None:
                raise ValueError(f"Device does not report {field}")
            updates[field] = value
    if changes.sleep is not None:
        if state.sleep is None:
            raise ValueError("Device does not report sleep mode")
        updates["sleep"] = SLEEP_MODE_ON if changes.sleep else "off"
    return replace(state, **updates)
