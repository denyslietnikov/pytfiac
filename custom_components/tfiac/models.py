"""Pure TFIAC protocol models; no Home Assistant dependency."""

from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from enum import StrEnum
from math import isfinite

MIN_TEMP = 61
MAX_TEMP = 88
SLEEP_MODE_ON = "sleepMode1:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0"
OPTIONAL_CONTROL_FIELDS = ("eco", "turbo", "display", "beep")


class CommandProfile(StrEnum):
    """Explicit opt-in contracts, never inferred from a reported status tag."""

    DISABLED = "disabled"
    LEGACY_EXPERIMENTAL = "legacy_experimental"

    @property
    def command_fields(self) -> tuple[tuple[str, str], ...]:
        """Livingroom-tested spellings; other firmware still requires validation."""
        if self == self.LEGACY_EXPERIMENTAL:
            return (
                ("eco", "Opt_ECO"),
                ("turbo", "Opt_super"),
                ("display", "Opt_display"),
                ("beep", "BeepEnable"),
            )
        return ()


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
class TfiacCapabilities:
    """Decoded status capabilities, not evidence that a field is writable."""

    swing_horizontal: bool
    swing_vertical: bool
    sleep: bool
    eco: bool
    turbo: bool
    display: bool
    beep: bool
    outdoor_temperature: bool
    degree_half: bool


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
    eco: bool | None = None
    turbo: bool | None = None
    display: bool | None = None
    beep: bool | None = None
    outdoor_temperature: float | None = None
    degree_half: bool | None = None
    # Never publish raw XML values as entity attributes or log them by default.
    raw_fields: tuple[tuple[str, str], ...] = dataclass_field(
        default=(), compare=False, repr=False
    )
    optional_issues: tuple[str, ...] = dataclass_field(default=(), compare=False)

    @property
    def capabilities(self) -> TfiacCapabilities:
        return TfiacCapabilities(
            swing_horizontal=self.swing_horizontal is not None,
            swing_vertical=self.swing_vertical is not None,
            sleep=self.sleep is not None,
            eco=self.eco is not None,
            turbo=self.turbo is not None,
            display=self.display is not None,
            beep=self.beep is not None,
            outdoor_temperature=self.outdoor_temperature is not None,
            degree_half=self.degree_half is not None,
        )


@dataclass(frozen=True, slots=True)
class TfiacChanges:
    """Requested fields; optional writes also require a client command profile."""

    power: Power | None = None
    operation: Operation | None = None
    target_temperature: float | None = None
    fan: Fan | None = None
    swing_horizontal: bool | None = None
    swing_vertical: bool | None = None
    sleep: bool | None = None
    eco: bool | None = None
    turbo: bool | None = None
    display: bool | None = None
    beep: bool | None = None


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
    for field in OPTIONAL_CONTROL_FIELDS:
        if (value := getattr(changes, field)) is not None:
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be a boolean")
            if getattr(state, field) is None:
                raise ValueError(f"Device does not report a usable {field} status")
            updates[field] = value
    return replace(state, **updates)
