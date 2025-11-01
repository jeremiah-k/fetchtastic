# macOS Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on macOS is using our automated installer script:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

This script will:

- Check if Homebrew is installed and install it if needed
- Check if Python is installed and install it via Homebrew if needed
- Install pipx for better package isolation
- Install Fetchtastic via pipx
- Run the initial setup process

## Manual Installation

If you prefer to install manually or need more control over the process:

### Prerequisites

- macOS 10.15 (Catalina) or later
- Homebrew (recommended) or Python 3.10+

### Step 1: Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Step 2: Install Python

```bash
brew install python
```

### Step 3: Install pipx

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# Restart your terminal or run:
source ~/.zshrc  # or ~/.bash_profile if using bash
```

### Step 4: Install Fetchtastic

```bash
pipx install fetchtastic
```

### Step 5: Run Setup

```bash
fetchtastic setup
```

## Alternative Installation Methods

### Using pip directly (not recommended)

```bash
pip3 install --user fetchtastic
fetchtastic setup
```

### Using system Python (if available)

If you have Python installed via Xcode Command Line Tools:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install fetchtastic
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

### Homebrew Installation Issues

If Homebrew installation fails, try installing Xcode Command Line Tools first:

```bash
xcode-select --install
```

### Python Version Issues

macOS comes with an older Python version. Make sure you're using Python 3.10+:

```bash
python3 --version
```

If the version is too old, install a newer version via Homebrew:

```bash
brew install python@3.11
```

### PATH Issues

If `fetchtastic` command is not found after installation:

```bash
python3 -m pipx ensurepath
source ~/.zshrc  # or ~/.bash_profile
```

### Permission Issues

If you encounter permission issues, avoid using `sudo`. Use the `--user` flag with pip or use pipx as recommended.

### M1/M2 Mac Considerations

On Apple Silicon Macs, make sure you're using the native ARM64 version of Python and Homebrew. The installer script handles this automatically.

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
