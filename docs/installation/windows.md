# Windows Installation

Fetchtastic provides enhanced Windows integration with Start Menu shortcuts and automatic startup options.

## Easy Installation (Recommended)

**Important:** This must be run in PowerShell (not Command Prompt).

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

The script will:

- Install Python if needed
- Install pipx for better package isolation
- Install Fetchtastic with Windows integration
- Run the setup process

## Manual Installation

1. **Install Python**: Download from the [official Python website](https://www.python.org/downloads/).
   Make sure to check "Add Python to PATH" during installation.

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

## Windows Integration Features

When you install with Windows integration, you get:

- **Start Menu shortcuts** for common operations (download, setup, repo browse)
- **Update shortcut** to check for and install Fetchtastic updates
- **Configuration shortcut** to the configuration file for easy editing
- **Downloads shortcut** to the Meshtastic downloads folder
- **Startup option** to run Fetchtastic automatically at Windows startup

## Upgrading

### Option 1: Use the installation script (handles upgrade issues automatically)

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

### Option 2: Use Start Menu shortcut

Open Start Menu → Fetchtastic → "Check for Updates"

### Option 3: Manual pipx upgrade

```powershell
pipx upgrade fetchtastic
# If it says "already at latest version" but you know there's a newer version:
pipx install fetchtastic[win] --force
```

### Troubleshooting Upgrades

**Complete reinstall (if needed):**

```powershell
pipx uninstall fetchtastic
pipx install "fetchtastic[win]"
```

## Scheduling

During setup, you can choose to add Fetchtastic to Windows startup. This adds a shortcut to your Startup folder that runs Fetchtastic automatically when Windows starts.

## File Locations

**Configuration:**

- `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

**Downloads:**

- `%USERPROFILE%\Downloads\Meshtastic\` (or custom path set during setup)

**Logs:**

- `%LOCALAPPDATA%\fetchtastic\logs\`
