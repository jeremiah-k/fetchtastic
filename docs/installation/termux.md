# Termux Installation (Android)

Fetchtastic can be installed on your Android device using Termux for automated Meshtastic firmware and app downloads.

## Prerequisites

Install these apps from F-Droid:

1. **[Termux](https://f-droid.org/en/packages/com.termux/)** - Terminal emulator for Android
2. **[Termux Boot](https://f-droid.org/en/packages/com.termux.boot/)** - For automatic startup (optional)
3. **[Termux API](https://f-droid.org/en/packages/com.termux.api/)** - For system integration (optional)
4. **[ntfy](https://f-droid.org/en/packages/io.heckel.ntfy/)** - For notifications (optional)

## Easy Installation (Recommended)

Run the installer script in Termux:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

This script will:

- Install Python and pipx
- Install Fetchtastic via pipx for better package isolation
- Run the setup process

## Manual Installation

### Using pipx (recommended)

```bash
# Install required packages
pkg install python python-pip openssl -y

# Install pipx
pip install --user pipx
python -m pipx ensurepath

# Install Fetchtastic
pipx install fetchtastic

# Run setup
fetchtastic setup
```

### Using pip (legacy method)

```bash
# Install required packages
pkg install python python-pip openssl -y

# Install Fetchtastic
pip install fetchtastic

# Run setup
fetchtastic setup
```

## Migration from pip to pipx

If you have an existing pip installation and want to migrate to pipx for better package isolation:

```bash
fetchtastic setup
# Follow the migration prompts, or manually:
pip uninstall fetchtastic -y
pip install --user pipx
python -m pipx ensurepath
pipx install fetchtastic
```

## Upgrading

**pipx installation (recommended):**

```bash
pipx upgrade fetchtastic
```

**pip installation (legacy):**

```bash
pip install --upgrade fetchtastic
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

Termux cron is configured automatically during setup. Fetchtastic can run in the background to check for new releases.

## Termux-Specific Features

- **WiFi-only downloads** - Protect against cellular data usage
- **Background execution** - Continue running when Termux is not active
- **Boot integration** - Start automatically when device boots (requires Termux Boot)

## File Locations

**Configuration:**

- `~/.config/fetchtastic/fetchtastic.yaml`

**Downloads:**

- `~/Downloads/Meshtastic/` (or custom path set during setup)

**Logs:**

- `~/.local/share/fetchtastic/logs/`
