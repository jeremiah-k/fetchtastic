# Configuration Reference

Fetchtastic stores configuration in YAML. Re-run `fetchtastic setup` for guided changes, or edit the file directly when you need an advanced option that setup does not prompt for.

Configuration file locations:

- Linux, macOS, and Termux: `~/.config/fetchtastic/fetchtastic.yaml`
- Windows: `%LOCALAPPDATA%\fetchtastic\fetchtastic.yaml`

Boolean values accept normal YAML booleans and common strings such as `true`, `false`, `yes`, `no`, `1`, and `0`.

## Common Options

| Key                      | Default                  | Description                                                                                                                                                                                                            |
| ------------------------ | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DOWNLOAD_DIR`           | `~/Downloads/Meshtastic` | Base directory for all downloaded files.                                                                                                                                                                               |
| `GITHUB_TOKEN`           | unset                    | Optional GitHub token. Helps avoid unauthenticated API rate limits.                                                                                                                                                    |
| `ALLOW_ENV_TOKEN`        | unset                    | Allows token lookup from environment-driven flows when supported by the caller.                                                                                                                                        |
| `LOG_LEVEL`              | `INFO`                   | Log verbosity. Can also be overridden with `FETCHTASTIC_LOG_LEVEL`.                                                                                                                                                    |
| `CREATE_LATEST_SYMLINKS` | `true`                   | Creates best-effort `latest` symlinks for completed firmware, repo-prerelease firmware, stable client app releases, and client app prereleases. Client app prerelease pointers are written as `app/prerelease/latest`. |
| `WIFI_ONLY`              | platform-dependent       | On Termux, skip downloads unless connected to Wi-Fi.                                                                                                                                                                   |
| `DEVICE_HARDWARE_API`    | unset                    | Optional override for device hardware metadata lookups.                                                                                                                                                                |

`latest` symlinks are convenience pointers only. If the platform or filesystem cannot create or update them safely, downloads still continue.

## Client App Downloads

Client app assets include Android APKs and desktop installers from the Meshtastic Android release feed. They are stored together under `app/<version>/`.

| Key                     | Default      | Description                                                                                  |
| ----------------------- | ------------ | -------------------------------------------------------------------------------------------- |
| `SAVE_CLIENT_APPS`      | setup choice | Enables unified client app downloads.                                                        |
| `SELECTED_APP_ASSETS`   | setup choice | Preferred selection list for APKs and desktop installers. Supports exact names and patterns. |
| `APP_VERSIONS_TO_KEEP`  | `2`          | Number of stable client app releases to retain.                                              |
| `CHECK_APP_PRERELEASES` | `true`       | Enables client app prerelease processing when client app downloads are enabled.              |

Legacy keys are still accepted and normalized:

- `SAVE_APKS`
- `SAVE_DESKTOP_APP`
- `SELECTED_APK_ASSETS`
- `SELECTED_DESKTOP_ASSETS`
- `SELECTED_DESKTOP_PLATFORMS`
- `ANDROID_VERSIONS_TO_KEEP`
- `DESKTOP_VERSIONS_TO_KEEP`
- `CHECK_APK_PRERELEASES`
- `CHECK_DESKTOP_PRERELEASES`

Use `SELECTED_APP_ASSETS` for new configs. It is the authoritative client app selection key when present.

## Firmware Downloads

| Key                                   | Default      | Description                                                                                  |
| ------------------------------------- | ------------ | -------------------------------------------------------------------------------------------- |
| `SAVE_FIRMWARE`                       | setup choice | Enables firmware release downloads.                                                          |
| `SELECTED_FIRMWARE_ASSETS`            | setup choice | Firmware asset patterns to download, such as device names or board identifiers.              |
| `FIRMWARE_VERSIONS_TO_KEEP`           | `2`          | Number of stable firmware releases to retain.                                                |
| `KEEP_LAST_BETA`                      | `false`      | Retain the newest beta release in addition to the stable retention window.                   |
| `FILTER_REVOKED_RELEASES`             | `true`       | Excludes revoked firmware releases from normal selection and latest eligibility.             |
| `ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES` | `true`       | Adds channel suffixes such as `-beta` or `-rc` to firmware directory names where needed.     |
| `PRESERVE_LEGACY_FIRMWARE_BASE_DIRS`  | `true`       | Keeps legacy firmware base directories during cleanup when channel-suffixed storage is used. |

Firmware `latest` points only to a complete release with at least one selected non-manifest firmware payload asset. Manifest-only releases and revoked-release skips do not qualify.

## Firmware Extraction

| Key                 | Default                     | Description                                                                                               |
| ------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------- |
| `AUTO_EXTRACT`      | setup choice                | Extracts selected files from downloaded firmware zip archives.                                            |
| `EXTRACT_PATTERNS`  | setup choice                | Include patterns for extracted firmware files.                                                            |
| `EXCLUDE_PATTERNS`  | recommended list from setup | Exclude patterns for extraction and selected download paths.                                              |
| `SELECTED_PATTERNS` | unset                       | Generic compatibility selection key used by shared downloader helpers when a more specific key is absent. |

Extraction failures are treated as release failures for latest-pointer eligibility when extraction is enabled.

## Prereleases

There are two prerelease paths:

- Client app prereleases from GitHub releases, stored under `app/prerelease/<version>/`.
- Firmware repo-prereleases discovered from `meshtastic.github.io`, stored under `firmware/prerelease/<dir>/`.

| Key                                    | Default             | Description                                                           |
| -------------------------------------- | ------------------- | --------------------------------------------------------------------- |
| `CHECK_PRERELEASES`                    | setup choice        | Legacy firmware prerelease switch.                                    |
| `CHECK_FIRMWARE_PRERELEASES`           | `CHECK_PRERELEASES` | Enables firmware repo-prerelease discovery and downloads.             |
| `SELECTED_PRERELEASE_ASSETS`           | setup choice        | Firmware prerelease asset patterns.                                   |
| `FIRMWARE_PRERELEASE_INCLUDE_PATTERNS` | unset               | Optional include filter for firmware repo-prerelease directory names. |
| `FIRMWARE_PRERELEASE_EXCLUDE_PATTERNS` | unset               | Optional exclude filter for firmware repo-prerelease directory names. |

Repo-prerelease latest is chronology-first. Fetchtastic prefers prerelease history `added_at` / commit chronology for real active history entries, and falls back to deterministic prerelease directory ordering only when chronology is unavailable.

## Download Reliability

| Key                           | Default | Description                                                           |
| ----------------------------- | ------- | --------------------------------------------------------------------- |
| `MAX_RETRIES`                 | `3`     | Number of orchestrator retry attempts for retryable failed downloads. |
| `RETRY_DELAY_SECONDS`         | `0`     | Base delay before retrying failed downloads.                          |
| `RETRY_BACKOFF_FACTOR`        | `2.0`   | Exponential backoff multiplier for orchestrator retries.              |
| `MAX_PARALLEL_RELEASE_CHECKS` | `4`     | Worker count for parallel release completeness checks.                |
| `MAX_CONCURRENT_DOWNLOADS`    | `5`     | Async download concurrency limit.                                     |
| `MAX_DOWNLOAD_RETRIES`        | `5`     | Async download retry count.                                           |
| `DOWNLOAD_RETRY_DELAY`        | `1.0`   | Async download retry delay.                                           |
| `NTFY_REQUEST_TIMEOUT`        | `10`    | Notification request timeout override.                                |

Invalid numeric values fall back to safe defaults or are clamped to valid ranges.

## Notifications And Automation

| Key                       | Description                                                        |
| ------------------------- | ------------------------------------------------------------------ |
| `NTFY_TOPIC`              | Topic used for push notifications.                                 |
| `NTFY_SERVER`             | NTFY server URL when customized.                                   |
| `NOTIFY_ON_DOWNLOAD_ONLY` | When true, sends notifications only when new files are downloaded. |

Platform integration options such as Windows shortcuts, startup entries, Termux boot scripts, and cron jobs are usually safer to manage through `fetchtastic setup` because they affect files outside the YAML configuration.

## Compatibility And Metadata Keys

Fetchtastic may write metadata such as `LAST_SETUP_DATE` and `LAST_SETUP_VERSION` after setup. Older configs can also contain `BASE_DIR` or `VERSIONS_TO_KEEP`; these are retained for compatibility but new configs should prefer `DOWNLOAD_DIR`, `APP_VERSIONS_TO_KEEP`, and `FIRMWARE_VERSIONS_TO_KEEP`.

## Example

```yaml
DOWNLOAD_DIR: ~/Downloads/Meshtastic
SAVE_CLIENT_APPS: true
SELECTED_APP_ASSETS:
  - app-fdroid-universal-release.apk
  - Meshtastic*.dmg
APP_VERSIONS_TO_KEEP: 2
CHECK_APP_PRERELEASES: true

SAVE_FIRMWARE: true
SELECTED_FIRMWARE_ASSETS:
  - rak4631
  - tbeam
FIRMWARE_VERSIONS_TO_KEEP: 2
KEEP_LAST_BETA: false
FILTER_REVOKED_RELEASES: true
CREATE_LATEST_SYMLINKS: true

AUTO_EXTRACT: true
EXTRACT_PATTERNS:
  - rak4631-
EXCLUDE_PATTERNS:
  - "*debug*"

MAX_RETRIES: 3
MAX_PARALLEL_RELEASE_CHECKS: 4
```
