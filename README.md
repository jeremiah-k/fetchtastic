# Fetchtastic Termux Setup

Fetchtastic is a tool to download the latest Meshtastic Android app and Firmware releases to your phone via Termux. It also provides optional notifications via an NTFY server.

## Prerequisites

### Install Termux and Add-ons

1. **Install Termux**: Download and install [Termux](https://f-droid.org/en/packages/com.termux/) from F-Droid.
2. **Install Termux Boot**: Download and install [Termux Boot](https://f-droid.org/en/packages/com.termux.boot/) from F-Droid.
3. **Install Termux API**: Download and install [Termux API](https://f-droid.org/en/packages/com.termux.api/) from F-Droid.
4. *(Optional)* **Install ntfy**: Download and install [ntfy](https://f-droid.org/en/packages/io.heckel.ntfy/) from F-Droid.

### Request Storage Access for Termux

Open Termux and run the following command to grant storage access:

```bash
termux-setup-storage
```
## Installation

### Step 1: Install Python

```bash
pkg install python -y
```

### Step 2: Install Fetchtastic

```bash
pip install fetchtastic
```

## Usage

### Run the Setup Process

Run the setup command and follow the prompts to configure Fetchtastic:

```bash
fetchtastic setup
```

During setup, you will be able to:

- Choose whether to download APKs, firmware, or both.
- Select specific assets to download.
- Set the number of versions to keep.
- Configure automatic extraction of firmware files. (Optional)
- Set up notifications via NTFY. (Optional)
- Add a cron job to run Fetchtastic regularly. (Optional)

### Perform Downloads

To manually start the download process, run:

```bash
fetchtastic download
```

This will download the latest versions of the selected assets and store them in the specified directories.

### Command list

- **setup**: Run the setup process.
- **download**: Download firmware and APKs.
- **topic**: Display the current NTFY topic.
- **clean**: Remove configuration, downloads, and cron jobs.
- **--help**: Show help and usage instructions.

### Files and Directories

By default, Fetchtastic saves files and configuration in the `Downloads/Fetchtastic` directory:

 - **Configuration File**: `Downloads/Meshtastic/fetchtastic.yaml`
 - **Log File**: `Downloads/Meshtastic/fetchtastic.log`
 - **APKs**: `Downloads/Meshtastic/apks`
 - **Firmware**: `Downloads/Meshtastic/firmware`

You can manually edit the configuration file to change the settings.


### Scheduling with Cron

During setup, you have the option to add a cron job that runs Fetchtastic daily at 3 AM.

To modify the cron job, you can run:
```bash
crontab -e
```

### Notifications via NTFY

If you choose to set up notifications, Fetchtastic will send updates to your specified NTFY topic.

### Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.
