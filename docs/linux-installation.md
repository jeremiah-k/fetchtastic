# Linux Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on Linux is using our automated installer script:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

For a new installation, the script installs uv when needed and installs Fetchtastic
in its isolated tool environment. If Fetchtastic is already installed by uv, pipx, or
a detectable pip environment, re-running the script upgrades through that manager
instead of switching ownership. Migration to uv is explicit.

## Manual Installation with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
uv tool install --python '>=3.10' fetchtastic
uv tool update-shell
```

Restart your terminal if PATH was updated, then run `fetchtastic setup`.
uv installs Fetchtastic in an isolated environment and supplies a suitable
Python when needed.

## Install with pip

Use Python 3.10+ in a virtual environment. On Debian/Ubuntu, install
`python3-venv` if creating the environment fails.

```bash
python3 -m venv ~/.local/share/fetchtastic/venv
~/.local/share/fetchtastic/venv/bin/python -m pip install fetchtastic
~/.local/share/fetchtastic/venv/bin/fetchtastic setup
```

The shell installer also supports `bash setup_fetchtastic.sh pip`, which creates
this environment and links the command in `~/.local/bin`. Existing pipx
installations remain supported (`bash setup_fetchtastic.sh pipx`). A no-argument
rerun preserves the recognized manager; it does not migrate pip or pipx to uv.

## Upgrading

Use your installation's manager:

```bash
uv tool upgrade fetchtastic
# pip:
~/.local/share/fetchtastic/venv/bin/python -m pip install --upgrade fetchtastic
# pipx:
pipx upgrade fetchtastic
```

## Migrating to uv

Configuration and downloads are stored outside the package environment. Keep
those directories when switching installers. Remove the package using the
manager that installed it (`pipx uninstall fetchtastic`, or the existing
environment's `python -m pip uninstall fetchtastic`), then install with uv as
above. Refresh scheduled commands and shortcuts with `fetchtastic setup` so they
use the uv executable path. Do not remove your configuration or downloads.

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

If you encounter permission issues, run uv or pip in a virtual environment as your regular user.

### PATH Issues

If `fetchtastic` command is not found after installation, update uv's executable directory in your PATH:

```bash
uv tool update-shell
source ~/.bashrc
```

## Uninstalling

To completely remove Fetchtastic:

```bash
# Remove the application
uv tool uninstall fetchtastic

# Remove configuration and downloads (optional)
rm -rf ~/.config/fetchtastic
rm -rf ~/Downloads/Meshtastic

# Remove cron job (if you set one up)
crontab -e
# Delete the fetchtastic line and save
```
