# Termux Installation Guide (Android)

Fetchtastic can be installed on your Android device using Termux, allowing you to download Meshtastic firmware and APKs directly to your phone.

## Prerequisites

Install these apps from F-Droid (recommended) or Google Play Store:

1. **[Termux](https://f-droid.org/en/packages/com.termux/)** - Terminal emulator for Android
2. **[Termux:Boot](https://f-droid.org/en/packages/com.termux.boot/)** - Run scripts on device boot (optional)
3. **[Termux:API](https://f-droid.org/en/packages/com.termux.api/)** - Access Android APIs (optional)
4. **[ntfy](https://f-droid.org/en/packages/io.heckel.ntfy/)** - Push notifications (optional)

**Note:** F-Droid versions are recommended as they receive more frequent updates and have fewer restrictions.

## Quick Installation (Recommended)

Open Termux and run:

```bash
curl -sSL https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh | bash
```

> **Security Note:** For security-conscious users, you can [download and inspect the script](https://raw.githubusercontent.com/jeremiah-k/fetchtastic/main/src/fetchtastic/tools/setup_fetchtastic.sh) before running it.

This script will:

- Install Python and required packages
- Install pipx for better package isolation
- Install Fetchtastic via pipx
- Run the initial setup process
- Set up storage access permissions

## Manual Installation

If you prefer to install manually:

### Step 1: Update Termux and Install Dependencies

```bash
# Update package lists
pkg update

# Install required packages
pkg install python python-pip openssl -y
```

### Step 2: Install pipx (Recommended)

```bash
# Install pipx
pip install --user pipx
python -m pipx ensurepath

# Restart Termux or run:
source ~/.bashrc
```

### Step 3: Install Fetchtastic

```bash
pipx install fetchtastic
```

### Step 4: Set Up Storage Access

```bash
termux-setup-storage
```

Grant storage permissions when prompted. This allows Fetchtastic to save files to your device's storage.

### Step 5: Run Setup

```bash
fetchtastic setup
```

## Termux-Specific Features

### Wi-Fi Only Downloads

During setup, you can enable "Wi-Fi only" mode to avoid using cellular data for downloads.

### Boot Scripts

You can set up Fetchtastic to run automatically when your device boots:

1. Install Termux:Boot from F-Droid
2. Open Termux:Boot once to enable it
3. During Fetchtastic setup, choose to enable boot scripts

The generated Fetchtastic boot script also starts `termux-services` before the
one-time download. This is important when you use cron: `sv-enable crond`
enables the service under Termux's runit supervisor, but the supervisor itself
must be started again after an Android reboot. If you do not use the boot
script, cron will resume after you next open a Termux login shell.

### Cron Jobs

Termux supports cron for scheduled tasks. During setup, you can schedule
Fetchtastic to run automatically. Fetchtastic installs/enables `cronie` and
`termux-services` as needed and writes the absolute Fetchtastic executable path
into the crontab. The absolute path matters for the recommended pipx install:
pipx normally places the command in `~/.local/bin`, while Termux cronie uses a
minimal `$PREFIX/bin` PATH for cron jobs.

After setup, verify all three pieces independently:

```bash
# The schedule exists.
crontab -l

# The runit service is up. Open a new Termux session first if SVDIR is unset.
sv status crond

# crond itself is running.
pgrep -a crond
```

If `sv status crond` reports that the service directory is unavailable in the
current shell, start a new Termux session or run:

```bash
export SVDIR="$PREFIX/var/service"
export LOGDIR="$PREFIX/var/log"
service-daemon start
sv-enable crond
```

Cron is still subject to Android background execution and Doze behavior. For
reliable screen-off operation, disable Android battery optimization for Termux.
Even then, Android and some OEM firmware can terminate long-running Termux
processes, so daemon-based cron should be treated as best-effort rather than an
exact Android alarm. You can also acquire a Termux wake lock, at the cost of
additional battery use:

```bash
termux-wake-lock
# Later, when you no longer need continuous background execution:
termux-wake-unlock
```

For troubleshooting, inspect the service log when present and Android's crond
log messages:

```bash
tail -n 100 "$PREFIX/var/log/sv/crond/current"
logcat -d -s CROND
```

`termux-job-scheduler` is an Android JobScheduler-based alternative available
through Termux:API. It is useful when approximate, battery-aware scheduling is
preferred over a continuously running cron daemon, but periodic execution is
not exact and depends on Android/device scheduling policy. Fetchtastic's setup
currently configures cronie.

## Upgrading

### pipx Installation (Recommended)

```bash
pipx upgrade fetchtastic
```

### Legacy pip Installation

```bash
pip install --upgrade fetchtastic
```

### Migrating from pip to pipx

If you have an existing pip installation and want to migrate:

```bash
# Run setup to get migration prompts
fetchtastic setup

# Or manually migrate:
pip uninstall fetchtastic -y
pip install --user pipx
python -m pipx ensurepath
pipx install fetchtastic
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

You can also access downloads from Android's file manager at:

```text
/storage/emulated/0/Download/Meshtastic/
```

## Troubleshooting

### Storage Permission Issues

If Fetchtastic can't save files:

```bash
termux-setup-storage
```

Make sure to grant all storage permissions.

### Python Package Installation Fails

If you get compilation errors:

```bash
# Install build dependencies
pkg install clang make libjpeg-turbo-dev

# Try installing again
pip install --user pipx
```

### Network Issues

If downloads fail due to network issues:

1. Check your internet connection
2. Try switching between Wi-Fi and cellular data
3. Some networks block certain downloads - try a different network

### Boot Scripts Not Working

If automatic startup doesn't work:

1. Make sure Termux:Boot is installed from F-Droid
2. Open Termux:Boot at least once
3. Grant all requested permissions
4. Restart your device to test

If the boot script exists but cron does not resume after reboot, verify that it
contains the `start-services.sh` line and then check `sv status crond` after
boot.

### Termux Session Killed

Android may kill Termux sessions to save battery. To prevent this:

1. Disable battery optimization for Termux
2. Use Termux:Boot for automatic startup
3. Use `termux-wake-lock` when continuous background execution is required
   (and `termux-wake-unlock` when it is no longer needed)

## Storage Locations

### Internal Storage

- Termux home: `/data/data/com.termux/files/home/`
- Shared storage: `/storage/emulated/0/`

### External Storage (if available)

- SD card: `/storage/[UUID]/`

### Recommended Setup

Save downloads to shared storage so you can access them from other Android apps:

```bash
# During setup, set base directory to:
/storage/emulated/0/Download/Meshtastic
```

## Uninstalling

To completely remove Fetchtastic:

```bash
# Remove the application
pipx uninstall fetchtastic

# Remove configuration and downloads (optional)
rm -rf ~/.config/fetchtastic
rm -rf ~/Downloads/Meshtastic

# Remove boot script (if you set one up)
rm -f ~/.termux/boot/fetchtastic.sh

# Remove cron job (if you set one up)
crontab -e
# Delete the fetchtastic line and save
```

## Tips for Android Users

1. **Use Wi-Fi**: Enable Wi-Fi only mode to avoid cellular data charges
2. **Storage Management**: Regularly clean old firmware versions to save space
3. **Battery Optimization**: Disable battery optimization for Termux if using automatic downloads
4. **File Access**: Use a file manager app to easily access downloaded files
5. **Notifications**: Set up NTFY for push notifications when new files are downloaded
