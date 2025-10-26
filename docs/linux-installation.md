# Linux Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on Linux is using our automated installer script:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

This script will:

- Check if Python is installed and install it if needed
- Install pipx for better package isolation
- Install Fetchtastic via pipx
- Run the initial setup process

## Manual Installation

If you prefer to install manually or need more control over the process:

### Prerequisites

- Python 3.10 or higher
- pip (usually comes with Python)

### Step 1: Install pipx (Recommended)

```bash
# Install pipx
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# Restart your shell or run:
source ~/.bashrc
```

### Step 2: Install Fetchtastic

```bash
pipx install fetchtastic
```

### Step 3: Run Setup

```bash
fetchtastic setup
```

## Alternative: Install with pip

If you prefer using pip directly (not recommended for isolation):

```bash
pip install --user fetchtastic
fetchtastic setup
```

## Upgrading

To upgrade Fetchtastic to the latest version:

```bash
pipx upgrade fetchtastic
```

If pipx reports "already at latest version" but you know there's a newer version:

```bash
pipx install fetchtastic --force
```

## Scheduling (Optional)

During setup, you can choose to automatically schedule Fetchtastic to run daily. This adds a cron job that runs at 3 AM:

```bash
# To manually edit the cron job later:
crontab -e

# To view current cron jobs:
crontab -l
```

## Configuration

Configuration is stored at:

```text
~/.config/fetchtastic/fetchtastic.yaml
```

Downloads are saved to:

```text
~/Downloads/Meshtastic/
```

See the [Usage Guide](usage-guide.md#file-organization) for detailed file organization.

## Troubleshooting

### Python Not Found

If you get "python3: command not found":

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install python3 python3-pip
```

**Fedora/RHEL/CentOS:**

```bash
sudo dnf install python3 python3-pip
```

**Arch Linux:**

```bash
sudo pacman -S python python-pip
```

### Permission Issues

If you encounter permission issues, make sure you're not using `sudo` with pip or pipx. These tools should be run as your regular user.

### PATH Issues

If `fetchtastic` command is not found after installation, ensure pipx's bin directory is in your PATH:

```bash
python3 -m pipx ensurepath
source ~/.bashrc
```

## Uninstalling

To completely remove Fetchtastic:

```bash
# Remove the application
pipx uninstall fetchtastic

# Remove configuration and downloads (optional)
rm -rf ~/.config/fetchtastic
rm -rf ~/Downloads/Meshtastic

# Remove cron job (if you set one up)
crontab -e
# Delete the fetchtastic line and save
```
