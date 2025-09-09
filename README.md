# ha-eg4-battery

Home Assistant Integration for the EG4 Battery

## Description

This Home Assistant integration allows you to monitor and control your EG4 battery system via Bluetooth. Get real-time data about your battery's state, including charge levels, power flow, and system status.

## Compatibility

This integration has only been tested with:
- EG4 LifePower4 Lithium Battery | 12V 400AH (Model: SR-12|400-LP4-00)

Other EG4 battery models may work but are not officially supported. Support for additional models can be added through pull requests.

## Features

- Battery state of charge monitoring
- Power flow metrics (charging/discharging)
- System status and health information
- Temperature monitoring
- Voltage and current readings

## Installation

### Using HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz) is installed in your Home Assistant instance
2. Add this repository as a custom repository in HACS:
   - Click on HACS in the sidebar
   - Click on "Integrations"
   - Click the three dots in the top right corner
   - Select "Custom repositories"
   - Add `https://github.com/k3vmcd/ha-eg4-battery` as an Integration
3. Install the integration:
   - Click on "Integrations"
   - Click the "+" button
   - Search for "EG4 Battery"
   - Click "Download"

### Manual Installation

1. Download the latest release from this repository
2. Copy the `custom_components/eg4_battery` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to Settings -> Devices & Services
2. Click "Add Integration"
3. Search for "EG4 Battery"
4. The integration will automatically discover nearby EG4 batteries via Bluetooth
5. Select your battery from the list and follow the configuration steps

## Requirements

- Home Assistant 2023.11.0 or newer
- An EG4 battery system with Bluetooth connectivity
- Bluetooth adapter on your Home Assistant device

## Support

- For bugs and feature requests, please open an issue on GitHub
- For questions and support, please use the Home Assistant community forums

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
