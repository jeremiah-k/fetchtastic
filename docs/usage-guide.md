# Usage Guide

This guide covers how to use Fetchtastic after installation.

## Quick Start

1. **Run setup** (first time only):

   ```bash
   fetchtastic setup
   ```

2. **Download latest releases**:

   ```bash
   fetchtastic download
   ```

3. **Browse repository files**:
   ```bash
   fetchtastic repo browse
   ```

## Commands Overview

```bash
fetchtastic --help
```

Available commands:

- `setup` - Run the setup process
- `download` - Download firmware and APKs from GitHub releases
- `topic` - Display the current NTFY topic
- `clean` - Remove Fetchtastic configuration, downloads, and cron jobs
- `version` - Display Fetchtastic version
- `help` - Display help information
- `repo` - Interact with the meshtastic.github.io repository

## Setup Process

The setup process configures Fetchtastic for your needs:

```bash
fetchtastic setup
```

### Configuration Options

**Base Directory**: Where downloads are saved (default: `~/Downloads/Meshtastic`)

**Asset Types**: Choose what to download:

- Firmware
- Android APKs
- Both firmware and APKs

**Asset Selection**: Choose specific firmware devices or APK variants

**Version Management**: How many versions to keep (default: 2)

**Auto-extraction**: Automatically extract specific files from firmware zip archives

**Pre-releases**: Download pre-release firmware from meshtastic.github.io

**Notifications**: Set up NTFY push notifications

**Scheduling**: Automatically run downloads on a schedule

**Platform-specific options**:

- **Windows**: Start Menu shortcuts, startup integration
- **Termux**: Wi-Fi only downloads, boot scripts

## Downloading Releases

```bash
fetchtastic download
```

This command:

1. Checks for new firmware and APK releases on GitHub
2. Downloads missing or updated files
3. Extracts firmware files (if configured)
4. Cleans up old versions
5. Sends notifications (if configured)

### What Gets Downloaded

**Firmware**: Latest releases from [meshtastic/firmware](https://github.com/meshtastic/firmware)

- Device-specific firmware files
- Bootloaders (if selected)
- Installation scripts
- Release notes

**Android APKs**: Latest releases from [meshtastic/Meshtastic-Android](https://github.com/meshtastic/Meshtastic-Android)

- Main APK files
- Debug variants (if selected)
- Release notes

## Repository Browser

Browse and download files from the [meshtastic.github.io](https://meshtastic.github.io) repository:

```bash
fetchtastic repo browse
```

### How to Use

1. Navigate through directories using ENTER
2. Use SPACE to select files for download
3. Press ENTER to confirm selection and download
4. Use "[Go back]" to navigate to parent directories
5. Use "[Quit]" to exit

### Downloaded Files Location

Repository files are saved to:

```text
~/Downloads/Meshtastic/firmware/repo-dls/
```

### Cleaning Repository Downloads

```bash
fetchtastic repo clean
```

This removes all files from the repository download directory.

## File Organization

Fetchtastic organizes downloads in a structured way:

```text
~/Downloads/Meshtastic/
├── apks/
│   ├── v2.3.2/
│   │   ├── app-release.apk
│   │   └── release_notes.md
│   └── v2.3.1/
├── firmware/
│   ├── v2.3.2/
│   │   ├── firmware-heltec-v3-2.3.2.xxxxxxxx.bin
│   │   ├── firmware-tbeam-2.3.2.xxxxxxxx.bin
│   │   └── release_notes.md
│   ├── v2.3.1/
│   ├── repo-dls/
│   │   └── firmware/
│   └── prerelease/ (if enabled)
│       └── v2.3.3.abcdef/
```

## Notifications

Fetchtastic can send push notifications via NTFY when new files are downloaded.

### Setup NTFY

1. During setup, choose to enable notifications
2. Note the generated topic name
3. Subscribe to notifications:
   - **Mobile**: Install [ntfy app](https://ntfy.sh/app/) and subscribe to your topic
   - **Desktop**: Visit [ntfy.sh](https://ntfy.sh) and subscribe to your topic
   - **Browser**: Bookmark `https://ntfy.sh/your-topic-name`

### View Your Topic

```bash
fetchtastic topic
```

This displays your current NTFY topic name.

## Scheduling

Fetchtastic can run automatically on a schedule.

### Linux/macOS

Uses cron jobs. During setup, you can choose to run daily at 3 AM.

**View cron jobs**:

```bash
crontab -l
```

**Edit cron jobs**:

```bash
crontab -e
```

### Windows

Uses startup shortcuts. During setup, you can choose to run at Windows startup.

**Manual management**:

1. Press `Win + R`, type `shell:startup`, press Enter
2. Add or remove the Fetchtastic shortcut

### Termux

Uses cron jobs and boot scripts.

**Boot scripts**: Run when device starts (requires Termux:Boot)
**Cron jobs**: Run on schedule

## Configuration Management

### Configuration File Location

- **Linux/macOS/Termux**: `~/.config/fetchtastic/fetchtastic.yaml`
- **Windows**: `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

### Editing Configuration

You can manually edit the configuration file or re-run setup:

```bash
fetchtastic setup
```

## Advanced Usage

### Custom Extraction Patterns

During setup, you can specify patterns for automatic firmware extraction:

Example patterns:

- `rak4631-` - Extract RAK4631 firmware
- `tbeam` - Extract T-Beam firmware
- `device-` - Extract device installation scripts

### Pre-release Downloads

Enable pre-release downloads to get the latest development firmware from meshtastic.github.io.

### Multiple Asset Types

You can configure different retention policies for firmware vs APKs by running setup multiple times and adjusting settings.

## Getting Help

- Run `fetchtastic help` for command information
- Check the [GitHub repository](https://github.com/jeremiah-k/fetchtastic) for issues and documentation
- Review log files for detailed error information
