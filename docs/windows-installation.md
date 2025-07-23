# Windows Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on Windows is using our automated PowerShell installer script.

**Important:** This must be run in PowerShell (not Command Prompt).

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1) before running it.

This script will:

- Install Python if needed (silently, with PATH configuration)
- Install pipx for better package isolation
- Install Fetchtastic with Windows integration features
- Run the initial setup process

## Windows Integration Features

When you install Fetchtastic with Windows integration, you get:

- **Start Menu shortcuts** for common operations:
  - Fetchtastic - Download
  - Fetchtastic - Setup
  - Fetchtastic - Repository Browser
  - Fetchtastic - Check for Updates
- **Configuration shortcuts**:
  - Quick access to edit the configuration file
  - Shortcut to the Meshtastic downloads folder
- **Startup integration** (optional):
  - Run Fetchtastic automatically when Windows starts

## Manual Installation

If you prefer to install manually:

### Step 1: Install Python

1. Download Python from the [official Python website](https://www.python.org/downloads/)
2. **Important:** Check "Add Python to PATH" during installation
3. Choose "Install for all users" if you want system-wide installation

### Step 2: Install pipx and Fetchtastic

Open PowerShell and run:

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

The `[win]` extra installs Windows-specific dependencies for enhanced integration.

## Upgrading

### Option 1: Use the Installation Script (Recommended)

This handles upgrade issues automatically:

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

### Option 2: Use Start Menu Shortcut

Open Start Menu → Fetchtastic → "Check for Updates"

### Option 3: Manual pipx Upgrade

```powershell
pipx upgrade fetchtastic
```

If it says "already at latest version" but you know there's a newer version:

```powershell
pipx install "fetchtastic[win]" --force
```

## Scheduling (Optional)

During setup, you can choose to run Fetchtastic automatically at Windows startup. This adds a shortcut to your Startup folder.

To manually manage startup:

1. Press `Win + R`, type `shell:startup`, press Enter
2. Add or remove the Fetchtastic shortcut as needed

## Configuration

Configuration is stored at:

```text
%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml
```

Downloads are saved to:

```text
%USERPROFILE%\Downloads\Meshtastic\
```

See the [Usage Guide](usage-guide.md#file-organization) for detailed file organization.

## Troubleshooting

### Python Not Found

If you get "python is not recognized":

1. Reinstall Python and make sure to check "Add Python to PATH"
2. Or manually add Python to your PATH environment variable

### PowerShell Execution Policy

If you get an execution policy error:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### pipx Not Found

If `pipx` command is not found after installation:

```powershell
python -m pipx ensurepath
```

Then restart PowerShell.

### Windows Defender/Antivirus

Some antivirus software may flag the installer. This is a false positive. You can:

1. Temporarily disable real-time protection
2. Add an exception for the Python Scripts folder
3. Use the manual installation method instead

### Start Menu Shortcuts Not Working

If shortcuts don't appear or work:

```powershell
fetchtastic setup --update-integrations
```

This recreates all Windows integration features.

## Uninstalling

To completely remove Fetchtastic:

```powershell
# Remove the application
pipx uninstall fetchtastic

# Remove Start Menu shortcuts
# Navigate to: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Fetchtastic
# Delete the Fetchtastic folder

# Remove configuration and downloads (optional)
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\fetchtastic"
Remove-Item -Recurse -Force "$env:USERPROFILE\Downloads\Meshtastic"

# Remove startup shortcut (if you set one up)
# Navigate to: shell:startup
# Delete the Fetchtastic shortcut
```

## Command Line Usage

After installation, you can use Fetchtastic from any Command Prompt or PowerShell window:

```cmd
fetchtastic download
fetchtastic setup
fetchtastic repo browse
fetchtastic --help
```
