# pytfiac Python package for tfi-AC units
![licence](https://img.shields.io/pypi/l/pytfiac) ![pypi-version](https://img.shields.io/pypi/v/pytfiac) ![pypi-dl](https://img.shields.io/pypi/dw/pytfiac)

Python3 library to communicate with AC-units that follows the tfiac protocol.

The `tfiac` integration integrates several vendors air conditioning systems, that uses the Tfiac mobile app, into [Home Assistant](https://www.home-assistant.io/integrations/tfiac). App currently available at [Play Store](https://play.google.com/store/apps/details?id=com.tcl.export) and [App Store](https://itunes.apple.com/app/tfiac/id1059938398).

## Installation via HACS

Since the tfiac integration was removed from Home Assistant core, you can install it as a custom integration via HACS.

1. In Home Assistant, go to HACS > Integrations.
2. Click the three dots in the top right corner and select "Custom repositories".
3. Add this repository URL: `https://github.com/denyslietnikov/pytfiac`
4. Select "Integration" as the category.
5. Click "Add".
6. Search for "TFIAC" in HACS and install it.
7. Restart Home Assistant.
8. Go to Settings > Devices & Services > Add Integration.
9. Search for "TFIAC" and enter your AC unit's IP address.

## Manual Installation

If you prefer manual installation:

1. Copy the `custom_components/tfiac` folder to your Home Assistant's `custom_components` directory.
2. Install the pytfiac package: `pip install pytfiac>=0.4`
3. Restart Home Assistant.
4. Add the integration as above.



