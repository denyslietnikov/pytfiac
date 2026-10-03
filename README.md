# TFIAC for Home Assistant

Custom Home Assistant integration for local control of air conditioners that use
the TFIAC protocol and mobile app.

The `develop` branch contains the unpublished `0.7.0b1` development candidate.
Its HA lifecycle and command transactions are covered by automated tests, but
the new implementation has not yet been tested on real air conditioners.

## Features

- HVAC modes: Cool, Heat, Dry, Fan Only, and Auto
- Fan-speed control
- Independent horizontal and vertical swing controls, when reported by the device
- Sleep mode as a climate preset
- Experimental Eco, Turbo, Panel light, and Beep feedback switches (explicit opt-in)
- Current and target temperature
- Optional experimental outdoor-temperature sensor (disabled by default)
- Privacy-safe diagnostics for reported capabilities and optional status issues
- Local polling over UDP; no cloud account is required

## Requirements

- Home Assistant 2026.9.4 or newer for the development candidate
- The air conditioner and Home Assistant must be able to reach each other on the
  local network over UDP port 7777

## Installation with HACS

1. In Home Assistant, open **HACS > Integrations**.
2. Open the menu and select **Custom repositories**.
3. Add `https://github.com/denyslietnikov/pytfiac` with category
   **Integration**.
4. Find and install **TFIAC**.
5. Restart Home Assistant.
6. Open **Settings > Devices & services > Add Integration**, search for
   **TFIAC**, and enter the air conditioner's IP address.

Stable versions are distributed as GitHub Releases. The default branch is hidden
in HACS so that normal installations do not accidentally track development
commits.

## Manual installation

Copy `custom_components/tfiac` into the `custom_components` directory of your
Home Assistant configuration, restart Home Assistant, and add TFIAC from
**Settings > Devices & services**.

The protocol client is bundled inside the integration. Do not install the legacy
`pytfiac` PyPI package for the Home Assistant integration.

## Usage

The integration creates one climate entity. Sleep mode, when reported by the
device, is available in its **Preset** selector as `sleep`; select `none` to
disable it. Sleep and Turbo are
not changed together by this integration until their interaction is confirmed
on real devices.

Fan speeds use `auto`, `low`, `middle`, and `high`. Update existing automations
that request `medium` to use `middle`. Turning the AC on preserves
its reported operation instead of forcing Cool.

Existing config entries, climate unique IDs, and device identifiers are retained.
Use **Reconfigure** to update a device host; an old host override in options is
removed while other options are preserved.

To use the entity from an iPhone, expose it with Home Assistant's standard
[HomeKit Bridge](https://www.home-assistant.io/integrations/homekit/) integration.

### Swing migration in 0.7

Swing uses two independent HA controls, each with `"off"` and `"on"` modes:

- Vertical: `climate.set_swing_mode`, with `swing_mode`.
- Horizontal: `climate.set_swing_horizontal_mode`, with `swing_horizontal_mode`.

Only reported directions are exposed. Changing one direction preserves the
other direction from a fresh device read, including changes made with a remote.

This is a breaking change from the old combined swing selector. Values
`horizontal`, `vertical`, and `both` are no longer accepted by `set_swing_mode`.
Existing `off` calls now disable **vertical only**, not both directions. Update
automations to set both directions explicitly where they previously set a
combined mode:

| Old combined mode | Vertical `swing_mode` | Horizontal `swing_horizontal_mode` |
| --- | --- | --- |
| `off` | `"off"` | `"off"` |
| `horizontal` | `"off"` | `"on"` |
| `vertical` | `"on"` | `"off"` |
| `both` | `"on"` | `"on"` |

For example, enable both directions in an automation/script action sequence:

```yaml
- action: climate.set_swing_mode
  target:
    entity_id: climate.your_ac
  data:
    swing_mode: "on"
- action: climate.set_swing_horizontal_mode
  target:
    entity_id: climate.your_ac
  data:
    swing_horizontal_mode: "on"
```

Keep `"on"`/`"off"` quoted in YAML. These are two serialized device transactions,
not one atomic combined command. Entity IDs and config entries do not change.

### Optional status and outdoor temperature

Eco, Turbo, Display, Beep, and `Degree_Half` are decoded when their status fields
are reported. Diagnostics include these decoded states even with controls disabled.
Status support does not prove that a similarly named command field is writable.
Conflicting aliases or invalid optional values are reported without making the
main climate entity unavailable.

An **Outdoor temperature** sensor is registered only when the first refresh has
a finite, nonzero `OutdoorTemp`. It is disabled by default because this field's
unit and sentinel semantics still need device validation. The implementation
currently uses the integration's legacy Fahrenheit assumption; HA converts it to
your selected temperature unit. Do not rely on it for automations until verified
on your model.

`OutdoorTemp=0` is conservatively treated as unknown, not a confirmed measurement.
If the field first becomes usable later, reload the integration to create the
sensor. After creation, missing/invalid/zero values become `unknown`; communication
failure makes it `unavailable`. The sensor shares the climate coordinator and
does not open an extra device connection.

### Experimental optional controls

Normal installations keep **Optional command profile: Disabled**. For the future
hardware test cycle, **Configure** on the TFIAC integration offers an explicit
**Experimental legacy commands (unverified)** profile. Selecting it reloads the
integration with a status read, but sends no write commands. No real-device test
is required at this stage of development.

The profile creates **Eco**, **Turbo**, **Panel light**, and **Beep feedback**
switches only for usable initial statuses. Each switch is also disabled by default;
enable individual entities on the device page only when ready to test them.
Missing/conflicting/invalid initial flags create no switch; reload if the field
becomes usable later. Existing switches show `unknown` for unusable statuses and
`unavailable` for communication errors. Disabling the profile unloads these
controls; their registry IDs remain for a later opt-in.

This profile is an explicit hypothesis, not automatic firmware detection:

| Switch | Read status | Assumed command field |
|---|---|---|
| Eco | `Opt_ECO` / `Opt_eco` | `Opt_eco` |
| Turbo | `Opt_super` | `Opt_super` |
| Panel light | `Opt_display` | `Opt_display` |
| Beep feedback | `BeepEnable` / `Opt_beep` | `Opt_beep` |

Command spellings come from the legacy Homebridge writer; the profile assumes
`on/off` flags and **persistent** beep feedback, not a one-shot beep button. Their
spelling, payload shape, persistence, and mode interactions still need hardware
validation. A reported status field alone never enables writes.

Each action uses the shared read/write/read transaction: fresh power, operation,
temperature, fan and existing Sleep profile plus **one requested optional flag**.
Unrelated optional flags, swing, `Degree_Half`, and unknown firmware fields are
not echoed into the command. It does not force power on, reset another mode, or
invent Sleep/Eco/Turbo exclusion rules. The subsequent status read is the only
source of the published result; an ACK does not prove the change was applied and
writes are never retried automatically.

Sleep retains its existing climate preset; Eco and Turbo are deliberately not
additional presets until mutual exclusion is verified. Do not rely on these
experimental controls for automations before validation on your model.

### Diagnostics

Use the integration's **Download diagnostics** action in **Settings > Devices &
services** when reporting a problem. Diagnostics use the existing snapshot and
do not send AC commands. Host/IP, device name, all stored configuration values,
and arbitrary firmware values are omitted or redacted. Reported field names,
decoded statuses, capabilities, optional parse issues, and explicit protocol
assumptions (including the active optional command contract) are included. Review
the complete HA-generated report before sharing.

## Updating and rollback

Back up the Home Assistant configuration before updating. After an update, restart
Home Assistant and verify power, mode, temperature, fan, swing, and Sleep controls.

To roll back, restore the Home Assistant backup or, if the previous stable version
is available in HACS, use **Redownload** and restart Home Assistant. If you changed
swing automations for 0.7, restore their earlier versions when returning to the
old combined swing model.

Development plans, local test commands, and release criteria are documented in
[ROADMAP.md](ROADMAP.md).

## Support

Report reproducible problems in
[GitHub Issues](https://github.com/denyslietnikov/pytfiac/issues). Include the
integration version, Home Assistant version, relevant logs, and the device model.
