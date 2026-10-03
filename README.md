# TFIAC for Home Assistant

Local control of TFIAC air conditioners in Home Assistant. No cloud account required.

Requires **Home Assistant 2026.9.4+** and local network access to the AC (UDP 7777).
`develop` contains the unpublished `0.7.0b1` candidate with limited hardware testing.

## Features

- Cool, Heat, Dry, Fan Only and Auto modes
- Fan speed, independent vertical/horizontal swing and Sleep preset
- Current and target temperature
- Experimental Eco, Turbo, Display, Beep and outdoor temperature
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

Use the climate entity for normal control. Select the `sleep` preset to enable
Sleep, or `none` to disable it. **Reconfigure** changes the AC's IP address.

For optional switches, select **Configure → Optional command profile →
Experimental commands (model-specific)**, then enable individual entities on the
device page. These switches and the outdoor sensor are disabled by default;
test them on your model before using them in automations.

Temperatures follow your HA unit settings. The current range is `61–88 °F`;
for tenth-degree Celsius requests, use `16.2–31.1 °C`. Hardware limits and steps
still need model-specific validation. Setting temperature alone does not turn
the AC on; test optional controls while it is running. Confirmation may take
up to 25 seconds.

## Updating and rollback

Back up HA before updating. Existing entity/device IDs are retained.
Fan automations using `medium` must change to `middle`.

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
