# Command Reference

## Main Commands

### `fetchtastic download`

Download new releases for all configured asset types.

```bash
fetchtastic download
```

This is the main command that:

- Checks for new firmware releases
- Downloads new Android app versions
- Updates bootloaders and DFU apps
- Cleans up old versions based on retention settings
- Sends notifications (if configured)

### `fetchtastic setup`

Run the interactive setup process.

```bash
fetchtastic setup
```

Use this to:

- Initial configuration
- Change asset selections
- Update device configurations
- Modify notification settings
- Adjust scheduling options

### `fetchtastic repo browse`

Browse and download files from the Meshtastic repository.

```bash
fetchtastic repo browse
```

Interactive menu to:

- Browse repository directories
- Select multiple files with spacebar
- Download files to `repo-dls` directory
- Explore documentation and resources

### `fetchtastic clean`

Clean up old files and reset configuration.

```bash
fetchtastic clean
```

Options:

- Remove old firmware versions
- Clean up temporary files
- Reset configuration (with confirmation)

## Advanced Commands

### `fetchtastic version`

Show version information.

```bash
fetchtastic version
```

### `fetchtastic --help`

Show help information.

```bash
fetchtastic --help
```

## Configuration Commands

### View Configuration

Configuration files are located at:

- **Linux/Mac/Termux**: `~/.config/fetchtastic/fetchtastic.yaml`
- **Windows**: `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

You can edit these files directly or use `fetchtastic setup` for guided configuration.

### Reset Configuration

To completely reset configuration:

```bash
fetchtastic clean
# Confirm when prompted
fetchtastic setup
```

## Scheduling

### Linux/Mac Cron

View current cron jobs:

```bash
crontab -l
```

Edit cron jobs:

```bash
crontab -e
```

Example daily run at 3AM:

```bash
0 3 * * * /home/username/.local/bin/fetchtastic download
```

### Windows Task Scheduler

Fetchtastic can be added to Windows startup during setup, or you can create a scheduled task manually using Windows Task Scheduler.

### Termux Cron

Termux cron is configured automatically during setup. Check status:

```bash
crontab -l
```

## Logging

Logs are saved to:

- **Linux/Mac/Termux**: `~/.local/share/fetchtastic/logs/`
- **Windows**: `%LOCALAPPDATA%\fetchtastic\logs\`

View recent logs:

```bash
# Linux/Mac/Termux
tail -f ~/.local/share/fetchtastic/logs/fetchtastic.log

# Windows (PowerShell)
Get-Content "$env:LOCALAPPDATA\fetchtastic\logs\fetchtastic.log" -Tail 20 -Wait
```

## Troubleshooting Commands

### Check Installation

Verify Fetchtastic is properly installed:

```bash
which fetchtastic
fetchtastic version
```

### Test Configuration

Run a dry-run to test configuration without downloading:

```bash
fetchtastic download --dry-run
```

(Note: `--dry-run` flag may not be available in all versions)

### Network Connectivity

Test GitHub API access:

```bash
curl -s https://api.github.com/repos/meshtastic/firmware/releases/latest
```

### Permission Issues

Check file permissions:

```bash
# Linux/Mac/Termux
ls -la ~/.config/fetchtastic/
ls -la ~/Downloads/Meshtastic/

# Windows (PowerShell)
Get-Acl "$env:LOCALAPPDATA\fetchtastic"
```
