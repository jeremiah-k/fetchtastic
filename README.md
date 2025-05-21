# Fetchtastic

Fetchtastic is a utility for downloading and managing the latest Meshtastic Android app and Firmware releases. It also provides optional notifications via NTFY.

## Table of Contents

- [Installation](#installation)
  - [Linux/Mac Installation](#linuxmac-installation)
  - [Windows Installation](#windows-installation)
  - [Termux Installation (Android)](#termux-installation-android)
- [Usage](#usage)
  - [Setup Process](#setup-process)
  - [Command List](#command-list)
  - [Repository Browser](#repository-browser)
  - [Notifications via NTFY](#notifications-via-ntfy)
  - [Scheduling](#scheduling)
  - [Files and Directories](#files-and-directories)
- [Contributing](#contributing)

## Installation

### Linux/Mac Installation

#### Easy Installation (Recommended)

1. **Run the Installer Script**:

   Open a terminal and run:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

The script installs Python if needed (on macOS, it will install Homebrew if needed), installs pipx, installs Fetchtastic, and runs the Fetchtastic setup.

#### Manual Installation

If you prefer to install manually:

```bash
# Using pipx (recommended)
pipx install fetchtastic

# Or using pip
pip install fetchtastic
```

### Windows Installation

Fetchtastic can be installed on Windows systems with enhanced Windows integration.

#### Easy Installation (Recommended)

This must be run in PowerShell (not Command Prompt).

1. **Run the Installer Script**:

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

The script installs Python if needed, installs pipx, installs Fetchtastic with Windows integration, and runs the Fetchtastic setup.

#### Manual Installation

1. **Install Python**: Download and install Python from the [official Python website](https://www.python.org/downloads/). Make sure to check "Add Python to PATH" during installation.

2. **Install pipx and Fetchtastic**:

```powershell
python -m pip install --upgrade pip
python -m pip install --user pipx
python -m pipx ensurepath
```

Restart PowerShell, then run:

```powershell
pipx install "fetchtastic[win]"
fetchtastic setup
```

This installs Fetchtastic with Windows integration features (Start Menu shortcuts, configuration file shortcuts, and Windows startup integration).

#### Windows Integration Features

When you run `fetchtastic setup` on Windows with the Windows integration dependencies installed, you'll get:

- Shortcuts in the Start Menu for common operations (download, setup, repo browse)
- A shortcut to check for and install Fetchtastic updates
- A shortcut to the configuration file for easy editing
- A shortcut to the Meshtastic downloads folder
- Option to run Fetchtastic automatically at Windows startup

### Termux Installation (Android)

Fetchtastic can also be installed on your Android device using Termux.

#### Prerequisites

1. **Install Termux**: [From F-Droid](https://f-droid.org/en/packages/com.termux/)
2. **Install Termux Boot**: [From F-Droid](https://f-droid.org/en/packages/com.termux.boot/)
3. **Install Termux API**: [From F-Droid](https://f-droid.org/en/packages/com.termux.api/)
4. _(Optional)_ **Install ntfy**: [From F-Droid](https://f-droid.org/en/packages/io.heckel.ntfy/)

#### Install Dependencies

```bash
pkg install python python-pip openssl -y
```

#### Install Fetchtastic

```bash
pip install fetchtastic
```

You can use `pipx` in Termux, but `pip` is usually simpler and works well.

## Upgrading

To upgrade Fetchtastic to the latest version:

### On Linux/Mac/Windows (pipx installations)

```bash
pipx upgrade fetchtastic
```

### On Termux (pip installation)

```bash
pip install --upgrade fetchtastic
```

## Usage

### Setup Process

```bash
fetchtastic setup
```

During setup, you will be able to:

- Choose whether to download APKs, firmware, or both.
- Select specific assets to download.
- Set the number of versions to keep.
- Configure automatic extraction of firmware files (optional).
- Set up notifications via NTFY (optional).
- Add a cron job to run Fetchtastic regularly (optional).

### Command List

```bash
usage: fetchtastic [-h] {setup,download,topic,clean,version,help,repo} ...

Fetchtastic - Meshtastic Firmware and APK Downloader

positional arguments:
  {setup,download,topic,clean,version,help,repo}
    setup               Run the setup process
    download            Download firmware and APKs from GitHub releases
    topic               Display the current NTFY topic
    clean               Remove Fetchtastic configuration, downloads, and cron jobs
    version             Display Fetchtastic version
    help                Display help information
    repo                Interact with the meshtastic.github.io repository

options:
  -h, --help            Show this help message and exit
```

```bash
usage: fetchtastic repo [-h] {browse,clean} ...

positional arguments:
  {browse,clean}
    browse          Browse and download files from the meshtastic.github.io repository
    clean           Clean the repository download directory

options:
  -h, --help        Show this help message and exit
```

### Repository Browser

```bash
fetchtastic repo browse
```

Navigate to a firmware directory, select one or more files (SPACE to select, ENTER to confirm), and they’ll be downloaded to `Downloads/Meshtastic/firmware/repo-dls/<dir>`.

To clean the repo download folder:

```bash
fetchtastic repo clean
```

### Notifications via NTFY

NTFY notifications work on all platforms. You can subscribe via [ntfy app](https://ntfy.sh/app/) or browser. During setup, you can enable notifications only for new file downloads.

### Scheduling

Fetchtastic can be scheduled to run automatically:

#### Linux/Mac

```bash
crontab -e
```

Runs daily at 3AM (if selected in setup).

#### Windows

Adds shortcut to Startup folder (if selected in setup).

#### Termux

Termux cron is configured automatically during setup.

### Files and Directories

Downloads are saved under `Downloads/Meshtastic`:

- `apks/`
- `firmware/<version>/`
- `firmware/repo-dls/`
- `firmware/prerelease/` (if enabled)

Configs:

- Linux/Mac: `~/.config/fetchtastic/fetchtastic.yaml`
- Termux: same
- Windows: `AppData/Local/fetchtastic/...`

Logs go to:

- `~/.local/share/fetchtastic/logs/` or Windows equivalent

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.
