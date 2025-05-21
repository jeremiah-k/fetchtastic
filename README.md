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
  - [Files and Directories](#files-and-directories)
  - [Scheduling](#scheduling)
  - [Notifications via NTFY](#notifications-via-ntfy)
  - [Repository Browser](#repository-browser)
- [Contributing](#contributing)

## Installation

### Linux/Mac Installation

#### Easy Installation (Recommended)

1. **Run the Installer Script**:

   Open a terminal and run:

   ```bash
   curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
   ```

   The script will:

   - Install Python if needed (on macOS, it will install Homebrew if needed)
   - Install pipx
   - Install Fetchtastic
   - Run the Fetchtastic setup

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

1. **Run the Installer Script**:

   Open PowerShell and run:

   ```powershell
   irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
   ```

   The script will:

   - Install Python if needed
   - Install pipx
   - Install Fetchtastic with Windows integration
   - Run the Fetchtastic setup

#### Manual Installation

If you prefer to install manually:

1. **Install Python**:

   Download and install Python from the [official Python website](https://www.python.org/downloads/).

   Make sure to check "Add Python to PATH" during installation.

2. **Install Fetchtastic with Windows integration**:

   Open Command Prompt or PowerShell and run:

   ```powershell
   pip install pipx
   pipx install fetchtastic[win]
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

1. **Install Termux**: Download and install [Termux](https://f-droid.org/en/packages/com.termux/) from F-Droid.
2. **Install Termux Boot**: Download and install [Termux Boot](https://f-droid.org/en/packages/com.termux.boot/) from F-Droid.
3. **Install Termux API**: Download and install [Termux API](https://f-droid.org/en/packages/com.termux.api/) from F-Droid.
4. _(Optional)_ **Install ntfy**: Download and install [ntfy](https://f-droid.org/en/packages/io.heckel.ntfy/) from F-Droid.

#### Install Dependencies

Open Termux and run:

```bash
pkg install python python-pip openssl -y
```

#### Install Fetchtastic

```bash
pip install fetchtastic
```

## Upgrading

To upgrade Fetchtastic to the latest version:

### For pipx installations (recommended)

```bash
pipx upgrade fetchtastic
```

### For pip installations

```bash
pip install --upgrade fetchtastic
```

### For Windows users

Windows users can use the "Fetchtastic - Check for Updates" shortcut in the Start Menu.

## Usage

### Setup Process

Run the setup command and follow the prompts to configure Fetchtastic:

```bash
fetchtastic setup
```

During setup, you will be able to:

- Choose whether to download APKs, firmware, or both.
- Select specific assets to download.
- Set the number of versions to keep (default is 2 on Termux, 3 on desktop platforms).
- Configure automatic extraction of firmware files. (Optional)
- Set up notifications via NTFY. (Optional)
  - Choose to receive notifications only when new files are downloaded. (Optional)
- Add a cron job to run Fetchtastic regularly. (Optional)
  - On Termux, Fetchtastic can be scheduled to run daily at 3 AM using Termux's cron.
  - On Linux/Mac, Fetchtastic can be scheduled using the system's cron scheduler.

### Command List

Fetchtastic provides several commands to manage your Meshtastic firmware and APK downloads:

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

The `repo` command has additional subcommands:

```bash
usage: fetchtastic repo [-h] {browse,clean} ...

positional arguments:
  {browse,clean}
    browse          Browse and download files from the meshtastic.github.io repository
    clean           Clean the repository download directory

options:
  -h, --help        Show this help message and exit
```

### Files and Directories

#### Default Locations

By default, Fetchtastic saves downloaded files in the `Downloads/Meshtastic` directory:

- **APKs**: `Downloads/Meshtastic/apks`
- **Firmware**: `Downloads/Meshtastic/firmware`
  - **GitHub Releases**: `Downloads/Meshtastic/firmware/<version>` (managed by `download` command)
  - **Repository Files**: `Downloads/Meshtastic/firmware/repo-dls` (managed by `repo browse` command)
  - **Pre-releases**: `Downloads/Meshtastic/firmware/prerelease` (if enabled in setup)

#### Configuration and Log Files

Fetchtastic uses standard platform-specific directories for configuration and log files:

- **Linux/Mac**:

  - Configuration: `~/.config/fetchtastic/fetchtastic.yaml`
  - Logs: `~/.local/share/fetchtastic/logs/fetchtastic.log`

- **Windows**:

  - Configuration: `C:\Users\<username>\AppData\Local\fetchtastic\fetchtastic\fetchtastic.yaml`
  - Logs: `C:\Users\<username>\AppData\Local\fetchtastic\fetchtastic\logs\fetchtastic.log`
  - Shortcuts: Created in Start Menu and base directory during setup

- **Termux**:
  - Configuration: `~/.config/fetchtastic/fetchtastic.yaml`
  - Logs: `~/.local/share/fetchtastic/logs/fetchtastic.log`

You can manually edit the configuration file to change the settings. On Windows with Windows integration enabled, you can access the configuration file through the shortcut created in the Start Menu.

### Scheduling

Fetchtastic can be scheduled to run automatically on different platforms:

#### Linux/Mac Scheduling with Cron

During setup on Linux or Mac, you have the option to add a cron job that runs Fetchtastic daily at 3 AM.

To modify the cron job, you can run:

```bash
crontab -e
```

#### Windows Scheduling

On Windows, Fetchtastic can be set to run automatically at startup:

- During setup, you'll be asked if you want to run Fetchtastic automatically on Windows startup
- This creates a shortcut in the Windows Startup folder
- The shortcut runs in minimized mode to avoid disrupting your workflow
- You can manually add or remove this shortcut from the Windows Startup folder

#### Termux Scheduling

On Termux, the setup process will configure a cron job using Termux's cron implementation.

### Notifications via NTFY

If you choose to set up notifications, Fetchtastic will send updates to your specified NTFY topic.

- You can subscribe to the topic using the ntfy app or by visiting the topic URL in a browser.
- You can choose to receive notifications **only when new files are downloaded**.

### Repository Browser

Fetchtastic can browse and download files directly from the [meshtastic.github.io](https://github.com/meshtastic/meshtastic.github.io/) repository.

#### Using the Repository Browser

1. Run the repository browser:

   ```bash
   fetchtastic repo browse
   ```

2. Navigate through the menu:

   - First, select a firmware directory (e.g., `firmware-2.6.8.ef9d0d7`)
   - Then, select one or more files to download (press SPACE to select, ENTER to confirm)

3. The selected files will be downloaded to the `Downloads/Meshtastic/firmware/repo-dls/<directory>` folder.

4. To clean the repository download directory:
   ```bash
   fetchtastic repo clean
   ```

#### Differences from Regular Downloads

- The `download` command gets firmware and APKs from GitHub releases and manages version rotation.
- The `repo browse` command gets specific files from the meshtastic.github.io repository and keeps them until manually deleted.
- If pre-releases are enabled, Fetchtastic will also check the meshtastic.github.io repository for firmware versions newer than the latest official release.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.
