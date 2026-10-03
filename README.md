# TFIAC for Home Assistant

Custom Home Assistant integration for local control of air conditioners that use
the TFIAC protocol and mobile app.

The `develop` branch contains the unpublished `0.6.0b1` development candidate.
Its HA lifecycle and command transactions are covered by automated tests, but
the new implementation has not yet been tested on real air conditioners.

## Features

- HVAC modes: Cool, Heat, Dry, Fan Only, and Auto
- Fan-speed control
- Horizontal, vertical, and combined swing modes
- Sleep mode as a climate preset
- Current and target temperature
- Local polling over UDP; no cloud account is required

## Requirements

- Home Assistant 2026.9.4 or newer for the `0.6` development candidate
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

## Updating and rollback

Back up the Home Assistant configuration before updating. After an update, restart
Home Assistant and verify power, mode, temperature, fan, swing, and Sleep controls.

To roll back, open TFIAC in HACS, select **Redownload**, choose the previous stable
version, and restart Home Assistant.

Development plans, local test commands, and release criteria are documented in
[ROADMAP.md](ROADMAP.md).

## Support

Report reproducible problems in
[GitHub Issues](https://github.com/denyslietnikov/pytfiac/issues). Include the
integration version, Home Assistant version, relevant logs, and the device model.
