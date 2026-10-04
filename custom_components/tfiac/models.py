"""Pure TFIAC protocol models; no Home Assistant dependency."""

from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from enum import StrEnum

MIN_TEMP = 60.8  # 16 °C, including the documented Super cooling setpoint.
MAX_TEMP = 88
BOOST_TEMPERATURES = {"cool": 60.8, "heat": 87.8}  # 16 / 31 °C in native °F.
# Existing status decoder precision, not a hardware setpoint increment.
TEMPERATURE_DECIMAL_PLACES = 2
SLEEP_MODE_ON = "sleepMode1:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0"
OPTIONAL_CONTROL_FIELDS = ("display", "beep")
# Fixed Livingroom-tested writers, never guessed from status aliases.
OPTIONAL_COMMAND_FIELDS = (("display", "Opt_display"), ("beep", "BeepEnable"))


class Preset(StrEnum):
    """Mutually exclusive Sleep/Turbo, confirmed by the maintainer's device."""

    NONE = "none"
    SLEEP = "sleep"
    BOOST = "boost"


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


def sleep_allowed(operation: Operation) -> bool:
    """Fan Only cannot activate Sleep (confirmed with the Ballu remote)."""
    return operation != Operation.FAN


def boost_allowed(operation: Operation) -> bool:
    """Only advertise Super in the two operations described by Ballu."""
    return operation in BOOST_TEMPERATURES


def target_temperature_allowed(operation: Operation) -> bool:
    """Fan Only circulates air without a temperature target."""
    return operation != Operation.FAN


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

    @property
    def preset(self) -> Preset | None:
        """Report actual flags; a conflicting status has no invented priority."""
        sleeping = self.sleep is not None and self.sleep != "off"
        if sleeping and self.turbo is True:
            return None
        if sleeping:
            return Preset.SLEEP
        if self.turbo is True:
            return Preset.BOOST
        if self.sleep is not None or self.turbo is not None:
            return Preset.NONE
        return None


@dataclass(frozen=True, slots=True)
class TfiacChanges:
    """Requested fields; optional writes require usable fresh device status."""

    power: Power | None = None
    operation: Operation | None = None
    target_temperature: float | None = None
    fan: Fan | None = None
    swing_horizontal: bool | None = None
    swing_vertical: bool | None = None
    preset: Preset | None = None
    display: bool | None = None
    beep: bool | None = None


def normalize_target_temperature(temperature: float) -> float:
    """Validate native legacy Fahrenheit and match the status decoder precision.

    Do not infer a physical temperature step, quantize to whole degrees, clamp an
    out-of-range request, or convert units a second time after HA has done so.
    """
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not MIN_TEMP <= temperature <= MAX_TEMP
    ):
        raise ValueError(
            f"Target temperature must be a finite number in {MIN_TEMP}–{MAX_TEMP} °F"
        )
    return round(float(temperature), TEMPERATURE_DECIMAL_PLACES)


def apply_changes(state: TfiacState, changes: TfiacChanges) -> TfiacState:
    """Apply explicit intents and documented Ballu Super setpoints."""
    updates = {}
    for field, enum in (("power", Power), ("operation", Operation), ("fan", Fan)):
        if (value := getattr(changes, field)) is not None:
            updates[field] = enum(value)
    if changes.operation is not None:
        if changes.power == Power.OFF:
            raise ValueError("Cannot select an operation and turn off together")
        updates["power"] = Power.ON
    if (temperature := changes.target_temperature) is not None:
        if not target_temperature_allowed(updates.get("operation", state.operation)):
            raise ValueError("Target temperature is not available in Fan Only mode")
        updates["target_temperature"] = normalize_target_temperature(temperature)
    for field in ("swing_horizontal", "swing_vertical"):
        if (value := getattr(changes, field)) is not None:
            if getattr(state, field) is None:
                raise ValueError(f"Device does not report {field}")
            updates[field] = value
    if changes.preset is not None:
        preset = Preset(changes.preset)
        if preset == Preset.SLEEP and not sleep_allowed(
            updates.get("operation", state.operation)
        ):
            raise ValueError("Sleep is not available in Fan Only mode")
        if preset == Preset.SLEEP and state.sleep is None:
            raise ValueError("Device does not report sleep mode")
        if preset == Preset.BOOST and state.turbo is None:
            raise ValueError("Device does not report a usable turbo status")
        if preset == Preset.BOOST:
            operation = updates.get("operation", state.operation)
            if not boost_allowed(operation):
                raise ValueError("Boost is available only in Cool or Heat mode")
            updates["target_temperature"] = BOOST_TEMPERATURES[operation]
        if state.sleep is None and state.turbo is None:
            raise ValueError("Device does not report presets")
        if state.sleep is not None:
            updates["sleep"] = SLEEP_MODE_ON if preset == Preset.SLEEP else "off"
        if state.turbo is not None:
            updates["turbo"] = preset == Preset.BOOST
    for field in OPTIONAL_CONTROL_FIELDS:
        if (value := getattr(changes, field)) is not None:
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be a boolean")
            if getattr(state, field) is None:
                raise ValueError(f"Device does not report a usable {field} status")
            updates[field] = value
    return replace(state, **updates)
