# TFIAC for Home Assistant

Local control of TFIAC air conditioners in Home Assistant. No cloud account required.

Requires **Home Assistant 2026.9.4+** and local network access to the AC (UDP 7777).
`develop` contains the unpublished `0.7.0b1` candidate with limited hardware testing.

## Features

- Cool, Heat, Dry, Fan Only and Auto modes
- Fan speed, independent vertical/horizontal swing and Sleep/Boost (Turbo) presets
- Current and target temperature
- Display and Beep switches; experimental outdoor temperature
- Apple Home and Siri through [HomeKit Bridge](https://www.home-assistant.io/integrations/homekit/)

## Installation

### HACS

1. Add `https://github.com/denyslietnikov/pytfiac` to **HACS → Custom repositories**
   with category **Integration**.
2. Install **TFIAC** and restart Home Assistant.
3. Open **Settings → Devices & services → Add Integration → TFIAC** and enter the AC's IP.

HACS uses published releases. Install the unpublished development candidate manually.

### Manual

Copy `custom_components/tfiac` to your HA configuration's `custom_components`
directory, restart HA and add TFIAC. No separate `pytfiac` package is needed.

## Usage

Use the climate entity for normal control. Presets: `sleep`, `boost` (Turbo),
or `none` to disable both. Sleep and Turbo are mutually exclusive.
Sleep is unavailable in Fan Only. Boost uses 16 °C in Cool or 31 °C in Heat;
select one of these modes first. Fan Only has no target temperature.
**Reconfigure** changes the AC's IP address.

Display and Beep switches appear automatically when the AC reports usable status.
Updating enables previously integration-disabled switches, preserving their IDs
and any manual disablement. Loading the integration sends no control commands.
The outdoor sensor remains disabled by default; verify it before use.

Temperatures follow your HA unit settings. The current range is `60.8–88 °F`;
for tenth-degree Celsius requests, use `16.0–31.1 °C`. Hardware limits and steps
still need model-specific validation. Setting temperature alone does not turn
the AC on; test optional controls while it is running. Confirmation may take
up to 25 seconds.

## Updating and rollback

Back up HA before updating. Existing entity/device IDs are retained.
Fan automations using `medium` must change to `middle`.
The former Turbo switch is replaced by `climate.set_preset_mode` with `boost`
or `none`; Eco control is removed. Both old switch entities are removed on setup.
Update affected automations and dashboard cards.

### Swing migration in 0.7

Vertical and horizontal swing now have separate `off/on` controls.
The old `horizontal/vertical/both` values are no longer accepted; vertical
`off` does not disable horizontal swing.
See [migration examples](ROADMAP.md#swing-migration-in-07).

To roll back, restore your HA backup or redownload an available earlier release
in HACS and restart. Also revert changed fan/swing automations.

## Support

Open a [GitHub issue](https://github.com/denyslietnikov/pytfiac/issues) with your
HA/integration versions, AC model, logs and **Download diagnostics** report.
Review diagnostics before sharing.
