# Setup Guide

## Initial Setup

Run the setup process to configure Fetchtastic:

```bash
fetchtastic setup
```

## Setup Process Overview

During setup, you will configure:

### 1. Base Directory

Choose where to save downloaded files (default: `~/Downloads/Meshtastic`)

### 2. Asset Types

Select what to download:

- **Meshtastic Firmware** - Official firmware releases for all supported devices
- **Meshtastic Android Apps** - Official Android app releases with multiple variants
- **Device Bootloaders** - Stock and enhanced bootloaders for nRF52840 devices
- **DFU/Firmware Flashing Apps** - Nordic DFU app for flashing firmware over Bluetooth

### 3. Firmware Configuration

#### System Selection

- **API-Based System** (recommended) - Organize by manufacturer and device model
- **Legacy Pattern System** - Use regex patterns for custom builds

#### Device Selection

For API-based system:

- Browse manufacturers (LilyGo, RAK, Heltec, etc.)
- Select specific device models
- Shopping cart style selection

#### Version Management

- Number of firmware versions to keep (default: 3 on desktop, 2 on Termux)
- Factory erase files for nRF52 devices (optional)
- Pre-release firmware downloads (optional)

### 4. Android App Configuration

#### App Selection

- **Meshtastic Official Client** - Select APK/AAB variants
  - Google Play Store Release APK
  - F-Droid Release APK
  - Google Play Store Release AAB
- **Nordic DFU App** - For nRF52 device flashing

### 5. Additional Options

#### Notifications

- NTFY notifications for new downloads
- Custom topic or auto-generated topic

#### Scheduling

- **Linux/Mac**: Cron job (daily at 3AM)
- **Windows**: Startup folder shortcut
- **Termux**: Termux cron integration

#### WiFi-Only (Termux)

- Prevent downloads over cellular data
- Protect against data usage charges

## Configuration File

Settings are saved to:

- **Linux/Mac/Termux**: `~/.config/fetchtastic/fetchtastic.yaml`
- **Windows**: `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

## Re-running Setup

You can run setup again at any time to:

- Change asset selections
- Update device configurations
- Modify notification settings
- Adjust scheduling options

Previous settings are preserved and shown as defaults.

## Migration

Fetchtastic automatically handles configuration migrations when upgrading between versions. Old settings are preserved and converted to new formats as needed.

## Troubleshooting Setup

### Configuration Issues

If setup fails or configuration is corrupted:

```bash
fetchtastic clean
fetchtastic setup
```

This removes all configuration and starts fresh.

### Permission Issues

On Linux/Mac, ensure you have write permissions to:

- Configuration directory (`~/.config/fetchtastic/`)
- Download directory (default: `~/Downloads/Meshtastic/`)
- Log directory (`~/.local/share/fetchtastic/logs/`)

### Network Issues

Setup requires internet access to:

- Fetch hardware lists from Meshtastic API
- Validate GitHub repository access
- Test notification services (if enabled)
