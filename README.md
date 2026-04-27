# Fetchtastic

**A simple, cross-platform utility for downloading and managing Meshtastic firmware and client app releases.**

Fetchtastic automatically downloads the latest Meshtastic firmware and selected client app assets from GitHub, with support for notifications, scheduling, and repository browsing.

## ✨ Features

- 🔄 **Automatic Downloads**: Latest firmware and Meshtastic client app assets
- ⚡ **Async Download Engine**: `aiohttp`-based downloads with connection pooling and retry/backoff
- 📱 **Cross-Platform**: Linux, macOS, Windows, and Android (Termux)
- 🗂️ **Repository Browser**: Browse and download files from meshtastic.github.io
- 🔔 **Notifications**: Push notifications via NTFY
- ⏰ **Scheduling**: Automatic downloads via cron/startup scripts
- 🎯 **Smart Selection**: Choose specific devices, APKs, and desktop installers
- 📦 **Auto-extraction**: Extract firmware files from zip archives
- ✅ **Stronger Integrity Checks**: Hash-based verification and ZIP integrity validation
- 🚦 **GitHub API Resilience**: Centralized release fetching, defensive parsing, caching, and rate-limit-aware behavior
- 🧵 **Parallel Validation**: Release completeness checks run in parallel for faster scans
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
fetchtastic download      # Download firmware and client app assets
fetchtastic cache clear   # Clear cached API data
fetchtastic repo browse   # Browse repository files
fetchtastic repo clean    # Clean repository downloads
fetchtastic topic         # Show NTFY topic
fetchtastic version       # Show version
fetchtastic clean         # Remove all configuration
```

## 🏗️ Architecture Highlights

- **Shared GitHub release source**: Release parsing and validation are centralized for consistency across firmware and Android paths.
- **Async + sync compatibility**: Async download paths are first-class, with sync fallbacks when async libraries are unavailable.
- **Defensive verification flow**: Size checks, hash baselines, and ZIP integrity checks are combined to reduce false positives.
- **Better retry semantics**: Retryable vs non-retryable errors are preserved to improve behavior and diagnostics.

## 📁 File Organization

Downloads are organized in a clean structure:

```text
~/Downloads/Meshtastic/
├── app/
│   ├── v2.7.14/
│   │   ├── app-fdroid-universal-release.apk
│   │   ├── Meshtastic-2.7.14.dmg
│   │   └── release_notes-v2.7.14.md
│   └── prerelease/   # Pre-release client app builds (optional)
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

## 🧪 Development & Testing

```bash
# create and activate environment
python3 -m venv .venv
. .venv/bin/activate

# install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# run tests
python -m pytest tests/
```

Test suite organization uses markers such as `unit`, `integration`, `core_downloads`, `user_interface`, `configuration`, and `infrastructure`.

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.
