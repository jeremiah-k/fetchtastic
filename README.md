# Fetchtastic

**A simple, cross-platform utility for downloading and managing Meshtastic firmware and Android app releases.**

Fetchtastic automatically downloads the latest Meshtastic firmware and Android APK releases from GitHub, with support for notifications, scheduling, and repository browsing.

## ✨ Features

- 🔄 **Automatic Downloads**: Latest firmware and Android APK releases
- 📱 **Cross-Platform**: Linux, macOS, Windows, and Android (Termux)
- 🗂️ **Repository Browser**: Browse and download files from meshtastic.github.io
- 🔔 **Notifications**: Push notifications via NTFY
- ⏰ **Scheduling**: Automatic downloads via cron/startup scripts
- 🎯 **Smart Selection**: Choose specific devices and APK variants
- 📦 **Auto-extraction**: Extract firmware files from zip archives
- 🔧 **Easy Setup**: One-command installation with guided setup

## 🚀 Quick Start

### One-Line Installation

**Linux/MacOS/Android (Termux):**

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

> **Security Note:** For security-conscious users, you can [inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1) before running it.

### Basic Usage

```bash
# Run setup (first time)
fetchtastic setup

# Download latest releases
fetchtastic download

# Browse repository files
fetchtastic repo browse
```

## 📖 Documentation

### Installation Guides

- **[Linux Installation](docs/linux-installation.md)** - Complete installation guide for Linux distributions
- **[macOS Installation](docs/macos-installation.md)** - Installation guide for macOS with Homebrew
- **[Windows Installation](docs/windows-installation.md)** - Windows installation with integration features
- **[Termux Installation](docs/termux-installation.md)** - Android installation using Termux

### Usage

- **[Usage Guide](docs/usage-guide.md)** - Complete guide to using Fetchtastic

## 🔧 Commands

```bash
fetchtastic setup         # Run the setup process
fetchtastic download      # Download firmware and APKs
fetchtastic cache update  # Clear cached API data
fetchtastic repo browse   # Browse repository files
fetchtastic repo clean    # Clean repository downloads
fetchtastic topic         # Show NTFY topic
fetchtastic version       # Show version
fetchtastic clean         # Remove all configuration
```

## 📁 File Organization

Downloads are organized in a clean structure:

```text
~/Downloads/Meshtastic/
├── apks/
│   ├── v2.3.2/
│   └── v2.3.1/
├── firmware/
│   ├── v2.3.2/
│   ├── v2.3.1/
│   ├── repo-dls/      # Repository browser downloads
│   └── prerelease/    # Pre-release firmware (optional)
```

## 🔔 Notifications

Get notified when new releases are downloaded:

1. Enable NTFY during setup
2. Install the [ntfy app](https://ntfy.sh/app/) or use the web interface
3. Subscribe to your unique topic
4. Receive push notifications for new downloads

## ⏰ Scheduling

Set up automatic downloads:

- **Linux/macOS**: Cron jobs (daily at 3 AM)
- **Windows**: Startup folder shortcuts
- **Termux**: Boot scripts and cron jobs

## 🆙 Upgrading

**Automatic (recommended):**

- **Windows**: Use Start Menu → Fetchtastic → "Check for Updates"
- **Linux/macOS/Termux**: Re-run the installation script

**Manual:**

```bash
pipx upgrade fetchtastic
```

## 🤝 Contributing

Contributions are welcome! Please feel free to:

- Report bugs and issues
- Suggest new features
- Submit pull requests
- Improve documentation

Visit the [GitHub repository](https://github.com/jeremiah-k/fetchtastic) to get started.

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.
