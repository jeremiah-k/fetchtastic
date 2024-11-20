# Fetchtastic

Fetchtastic is a utility for downloading and managing the latest Meshtastic Android app and Firmware releases. It also provides optional notifications via NTFY.

## Table of Contents

- [Installation](#installation)
  - [Termux Installation (Android)](#termux-installation-android)
  - [Linux/Mac Installation](#linuxmac-installation)
- [Usage](#usage)
  - [Setup Process](#setup-process)
  - [Command List](#command-list)
  - [Files and Directories](#files-and-directories)
  - [Scheduling with Cron](#scheduling-with-cron)
  - [Notifications via NTFY](#notifications-via-ntfy)
- [Contributing](#contributing)

## Installation

### Termux Installation (Android)

Fetchtastic can be installed on your Android device using Termux.

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

### Linux/Mac Installation

Fetchtastic can also be installed on Linux or macOS systems.

#### Install with pipx (Recommended)

It's recommended to use `pipx` to install Fetchtastic in an isolated environment. (If you prefer, you can use `pip` too.)

1. **Install pipx**:

   Follow the installation instructions for your platform on the [pipx documentation page](https://pypa.github.io/pipx/installation/).

   Restart your terminal after installing pipx.

2. **Install Fetchtastic with pipx**:

   ```bash
   pipx install fetchtastic
   ```

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

- **setup**: Run the setup process.
- **download**: Download firmware and APKs.
- **topic**: Display the current NTFY topic.
- **clean**: Remove configuration, downloads, and cron jobs.
- **version**: Display Fetchtastic version.
- **help**: Show help and usage instructions.

### Files and Directories

By default, Fetchtastic saves files and configuration in the `Downloads/Meshtastic` directory:

- **Configuration File**: `Downloads/Meshtastic/fetchtastic.yaml`
- **Log File**: `Downloads/Meshtastic/fetchtastic.log`
- **APKs**: `Downloads/Meshtastic/apks`
- **Firmware**: `Downloads/Meshtastic/firmware`

You can manually edit the configuration file to change the settings.

### Scheduling with Cron

During setup, you have the option to add a cron job that runs Fetchtastic daily at 3 AM.

The setup process will configure the cron job using Termux's cron implementation.

To modify the cron job, you can run:

```bash
crontab -e
```

### Notifications via NTFY

If you choose to set up notifications, Fetchtastic will send updates to your specified NTFY topic.

- You can subscribe to the topic using the ntfy app or by visiting the topic URL in a browser.
- You can choose to receive notifications **only when new files are downloaded**.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.
