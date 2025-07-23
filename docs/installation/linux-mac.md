# Linux/Mac Installation

## Easy Installation (Recommended)

Run the installer script in your terminal:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

The script will:

- Install Python if needed (on macOS, it will install Homebrew if needed)
- Install pipx for better package isolation
- Install Fetchtastic
- Run the setup process

## Manual Installation

If you prefer to install manually:

```bash
# Using pipx (recommended)
pipx install fetchtastic

# Or using pip
pip install fetchtastic
```

## Upgrading

To upgrade Fetchtastic to the latest version:

```bash
pipx upgrade fetchtastic
```

### Troubleshooting Upgrades

If `pipx upgrade` reports "already at latest version" but you know a newer version exists:

```bash
pipx install fetchtastic --force
```

**Complete reinstall (if needed):**

```bash
pipx uninstall fetchtastic
pipx install fetchtastic
```

## Scheduling

Fetchtastic can be scheduled to run automatically using cron:

```bash
crontab -e
```

Add a line like this to run daily at 3AM:

```bash
0 3 * * * /home/username/.local/bin/fetchtastic download
```

## File Locations

**Configuration:**

- `~/.config/fetchtastic/fetchtastic.yaml`

**Downloads:**

- `~/Downloads/Meshtastic/` (or custom path set during setup)

**Logs:**

- `~/.local/share/fetchtastic/logs/`
