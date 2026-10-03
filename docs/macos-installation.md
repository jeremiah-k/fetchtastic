# macOS Installation Guide

## Quick Installation (Recommended)

The easiest way to install Fetchtastic on macOS is using our automated installer script:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

The script installs uv when needed, installs Fetchtastic in its tool environment,
and prints the setup command. Homebrew is optional on macOS.

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
installations remain supported (`bash setup_fetchtastic.sh pipx`).

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
~/Library/Application Support/fetchtastic/fetchtastic.yaml
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
uv tool update-shell
source ~/.zshrc  # or ~/.bash_profile
```

### Permission Issues

If you encounter permission issues, avoid using `sudo`. Use uv or pip in a virtual environment.

### M1/M2 Mac Considerations

On Apple Silicon Macs, make sure you're using the native ARM64 version of Python and Homebrew. uv chooses the native platform build.

## Uninstalling

To completely remove Fetchtastic:

```bash
# Remove the application
uv tool uninstall fetchtastic

# Remove configuration and downloads (optional)
rm -rf ~/Library/Application\ Support/fetchtastic
rm -rf ~/Downloads/Meshtastic

# Remove cron job (if you set one up)
crontab -e
# Delete the fetchtastic line and save
```
