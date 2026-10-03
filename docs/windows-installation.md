# Windows Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on Windows is using our automated PowerShell installer script.

**Important:** This must be run in PowerShell (not Command Prompt).

```powershell
irm https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1 | iex
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.ps1) before running it.

For a new installation, this script will:

- Install uv when needed
- Let uv provide a suitable Python in an isolated tool environment
- Install Fetchtastic with Windows integration features
- Run the initial setup process

If Fetchtastic is already installed by a recognized uv, pipx, or pip environment,
a no-argument rerun upgrades through that manager instead of silently migrating it.
Migration to uv is explicit.

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

## Manual Installation with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
in PowerShell:

```powershell
uv tool install --python '>=3.10' 'fetchtastic[win]'
uv tool update-shell
```

Restart PowerShell if PATH was updated, then run `fetchtastic setup`.
The `[win]` extra installs the dependencies for Windows integrations.

## Install with pip

Install Python 3.10+ from [python.org](https://www.python.org/downloads/) and
select **Add Python to PATH**, then use a virtual environment:

```powershell
python -m venv "$env:LOCALAPPDATA\Fetchtastic\venv"
& "$env:LOCALAPPDATA\Fetchtastic\venv\Scripts\python.exe" -m pip install 'fetchtastic[win]'
& "$env:LOCALAPPDATA\Fetchtastic\venv\Scripts\fetchtastic.exe" setup
```

A downloaded installer also accepts `./setup_fetchtastic.ps1 -Installer pip`,
which creates the environment and adds its Scripts directory to your user PATH.
`-Installer pipx` supports pipx, while the default `auto` mode preserves a recognized
existing manager and uses uv only for a new installation.

## Upgrading

The Start Menu **Check for Updates** shortcut upgrades through the manager
that owns the active installation. For a manual upgrade:

```powershell
uv tool upgrade fetchtastic
# pip:
& "$env:LOCALAPPDATA\Fetchtastic\venv\Scripts\python.exe" -m pip install --upgrade 'fetchtastic[win]'
# pipx:
pipx upgrade fetchtastic
```

## Migrating to uv

Keep your configuration and downloads. Uninstall the package with the manager
that installed it (`pipx uninstall fetchtastic`, or the existing environment's
`python -m pip uninstall fetchtastic`), then install with uv as above. Run
`fetchtastic setup --update-integrations` to refresh Start Menu and startup
shortcuts to use the uv executable path.

## Scheduling (Optional)

During setup, you can choose to run Fetchtastic automatically at Windows startup. This adds a shortcut to your Startup folder.

To manually manage startup:

1. Press `Win + R`, type `shell:startup`, press Enter
2. Add or remove the Fetchtastic shortcut as needed

## Configuration

Configuration is stored at:

```text
%LOCALAPPDATA%\fetchtastic\fetchtastic\fetchtastic.yaml
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

### Command Not Found

If `fetchtastic` is not found after a uv installation:

```powershell
uv tool update-shell
```

Then restart PowerShell.

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
uv tool uninstall fetchtastic

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
