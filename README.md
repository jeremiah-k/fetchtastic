# Fetchtastic

Fetchtastic is a Python tool that automatically downloads the latest Meshtastic firmware, Android apps, and other assets. Keep your local collection up-to-date without manual intervention.

## Features

- **Automated Downloads**: Latest Meshtastic firmware and Android apps
- **Cross-Platform**: Linux, macOS, Windows, and Android (via Termux)
- **Smart Organization**: Organize firmware by manufacturer and device model
- **Comprehensive Files**: Downloads all files needed for complete device flashing
- **Version Management**: Keep multiple versions with automatic cleanup
- **Repository Browsing**: Browse and download files from Meshtastic repository
- **Scheduling**: Automatic downloads via cron/Task Scheduler
- **Notifications**: Optional NTFY notifications for new releases

## Quick Start

### Linux/Mac

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

### Windows

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

### Termux (Android)

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

## Manual Installation

```bash
# Using pipx (recommended)
pipx install fetchtastic
fetchtastic setup

# Using pip
pip install fetchtastic
fetchtastic setup
```

## Basic Usage

```bash
fetchtastic setup      # Interactive configuration
fetchtastic download   # Download latest releases
fetchtastic repo browse # Browse Meshtastic repository
```

## What Gets Downloaded

### Firmware (Comprehensive File Support)

- **ESP32 devices**: firmware, littlefs, bleota.bin, install/update scripts
- **nRF52 devices**: firmware (.uf2), factory erase files (optional)
- **All platforms**: Windows (.bat) and Linux/Mac (.sh) scripts

### Android Apps

- **Meshtastic Official Client**: Google Play APK/AAB, F-Droid APK
- **Nordic DFU App**: For nRF52 device flashing over Bluetooth

### Device Bootloaders

- **Stock bootloaders**: One-time downloads for nRF52840 devices
- **Enhanced bootloaders**: Version-tracked OTA-fix bootloaders

## Documentation

- **[Installation Guides](docs/installation/)** - Platform-specific installation instructions
- **[Usage Guide](docs/usage/)** - Detailed setup and command reference

## Configuration

**Config file locations:**

- Linux/Mac/Termux: `~/.config/fetchtastic/fetchtastic.yaml`
- Windows: `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

**Default download location:**

- `~/Downloads/Meshtastic/` (customizable during setup)

## Platform Features

### Windows

- Start Menu shortcuts and automatic startup
- Enhanced Windows integration

### Termux (Android)

- WiFi-only downloads (cellular data protection)
- Background execution and boot integration

## Upgrading

```bash
pipx upgrade fetchtastic
# If issues: pipx install fetchtastic --force
```

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Meshtastic](https://meshtastic.org/) for the amazing mesh networking platform
- The open-source community for tools and libraries
