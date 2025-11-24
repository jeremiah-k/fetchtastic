"""
Constants and configuration values for Fetchtastic.

This module contains all hardcoded values, URLs, timeouts, and other constants
used throughout the application.
"""

# GitHub API URLs
GITHUB_API_BASE = "https://api.github.com/repos"
MESHTASTIC_ANDROID_RELEASES_URL = (
    f"{GITHUB_API_BASE}/meshtastic/Meshtastic-Android/releases"
)
MESHTASTIC_FIRMWARE_RELEASES_URL = f"{GITHUB_API_BASE}/meshtastic/firmware/releases"
MESHTASTIC_GITHUB_IO_CONTENTS_URL = (
    f"{GITHUB_API_BASE}/meshtastic/meshtastic.github.io/contents"
)

# Network timeouts and delays (in seconds)
GITHUB_API_TIMEOUT = 10
NTFY_REQUEST_TIMEOUT = 10
PRERELEASE_REQUEST_TIMEOUT = 30

API_CALL_DELAY = 0.1  # Small delay to be respectful to GitHub API
GITHUB_MAX_PER_PAGE = 100
MIN_RATE_LIMIT_FOR_COMMIT_DETAILS = 10
PRERELEASE_DETAIL_ATTEMPT_MULTIPLIER = 3
PRERELEASE_DETAIL_FETCH_WORKERS = 4

# Download and retry settings
RELEASE_SCAN_COUNT = 10


# Windows-specific retry settings
WINDOWS_MAX_REPLACE_RETRIES = 3
WINDOWS_INITIAL_RETRY_DELAY = 1.0  # seconds

# File and directory names
REPO_DOWNLOADS_DIR = "repo-dls"
FIRMWARE_PRERELEASES_DIR_NAME = "prerelease"
APK_PRERELEASES_DIR_NAME = "prerelease"
FIRMWARE_DIR_PREFIX = "firmware-"
FIRMWARE_DIR_NAME = "firmware"
APKS_DIR_NAME = "apks"
LATEST_ANDROID_RELEASE_FILE = "latest_android_release.txt"
LATEST_FIRMWARE_RELEASE_FILE = "latest_firmware_release.txt"
LATEST_ANDROID_RELEASE_JSON_FILE = "latest_android_release.json"
LATEST_ANDROID_PRERELEASE_JSON_FILE = "latest_android_prerelease.json"
LATEST_FIRMWARE_PRERELEASE_JSON_FILE = "latest_firmware_prerelease.json"
LATEST_FIRMWARE_RELEASE_JSON_FILE = "latest_firmware_release.json"
PRERELEASE_TRACKING_JSON_FILE = "prerelease_tracking.json"
PRERELEASE_COMMITS_CACHE_FILE = "prerelease_commits_cache.json"
PRERELEASE_COMMITS_LEGACY_FILE = "prerelease_commits.txt"
PRERELEASE_COMMIT_HISTORY_FILE = "prerelease_commit_history.json"
WINDOWS_SHORTCUT_FILE = "fetchtastic_yaml.lnk"

# Regex patterns for parsing prerelease commit messages
PRERELEASE_ADD_COMMIT_PATTERN = (
    r"^(\d+\.\d+\.\d+)\.([a-f0-9]{6,})\s+meshtastic/firmware@(?:[a-f0-9]{6,})"
)
PRERELEASE_DELETE_COMMIT_PATTERN = (
    r"^Delete firmware-(\d+\.\d+\.\d+)\.([a-f0-9]{6,})\s+directory"
)

# Default values for prerelease entries
DEFAULT_PRERELEASE_ACTIVE = False
DEFAULT_PRERELEASE_STATUS = "unknown"
DEFAULT_PRERELEASE_COMMITS_TO_FETCH = 40
DEFAULT_FIRMWARE_VERSIONS_TO_KEEP = 2
DEFAULT_ANDROID_VERSIONS_TO_KEEP = 2
DEFAULT_CHECK_APK_PRERELEASES = True
EXECUTABLE_PERMISSIONS = 0o755

# Download configuration defaults
DEFAULT_CONNECT_RETRIES = 5
DEFAULT_BACKOFF_FACTOR = 0.3
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_CHUNK_SIZE = 8192


# Directories that Fetchtastic manages and can safely clean
MANAGED_DIRECTORIES = (
    REPO_DOWNLOADS_DIR,
    FIRMWARE_DIR_NAME,
    APKS_DIR_NAME,
)

# Default configuration values


# File extensions and patterns
APK_EXTENSION = ".apk"
ZIP_EXTENSION = ".zip"
SHELL_SCRIPT_EXTENSION = ".sh"

# Clean operation messages
MSG_REMOVED_MANAGED_DIR = "Removed managed directory: {path}"
MSG_REMOVED_MANAGED_FILE = "Removed managed file: {path}"
MSG_FAILED_DELETE_MANAGED_FILE = "Failed to delete managed file {path}. Reason: {error}"
MSG_FAILED_DELETE_MANAGED_DIR = (
    "Failed to delete managed directory {path}. Reason: {error}"
)
MSG_CLEANED_MANAGED_DIRS = "Cleaned managed directories from: {path}"
MSG_PRESERVE_OTHER_FILES = "Note: Only Fetchtastic-managed directories were removed. Other files were preserved."

# Logging configuration
LOGGER_NAME = "fetchtastic"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
INFO_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DEBUG_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s: %(message)s"
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_FILE_BACKUP_COUNT = 5

# Version validation regex - supports semantic versions with optional prerelease and build metadata
VERSION_REGEX_PATTERN = (
    r"^\d+\.\d+\.\d+"  # core version (major.minor.patch)
    r"(?:\.[a-f0-9]+)?"  # optional hex hash (e.g., .f93d031)
    r"(?:[-\.]?(?:rc|dev|b|beta|alpha)\d+)?"  # optional prerelease (rc1, dev1, b1, beta1, alpha1)
    r"(?:\+[0-9A-Za-z]+)?"  # optional local/build metadata (e.g., +local)
    r"$"
)

# Default extraction patterns (examples for documentation)
DEFAULT_EXTRACTION_PATTERNS = [
    "rak4631-",
    "tbeam",
    "t1000-e-",
    "tlora-v2-1-1_6-",
    "device-",
    "littlefs-",
    "bleota",
]

# Configuration file names
CONFIG_FILE_NAME = "fetchtastic.yaml"
MESHTASTIC_DIR_NAME = "Meshtastic"

# Files that Fetchtastic manages and can safely clean
# Note: CONFIG_FILE_NAME is included for safety, though it's typically in CONFIG_DIR
MANAGED_FILES = (
    LATEST_ANDROID_RELEASE_FILE,
    LATEST_FIRMWARE_RELEASE_FILE,
    CONFIG_FILE_NAME,
    WINDOWS_SHORTCUT_FILE,
)

# Environment variable names
LOG_LEVEL_ENV_VAR = "FETCHTASTIC_LOG_LEVEL"

# Device Hardware API Configuration
DEVICE_HARDWARE_API_URL = "https://api.meshtastic.org/resource/deviceHardware"
DEVICE_HARDWARE_CACHE_HOURS = 24

# Cache configuration
COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS = 24
RELEASES_CACHE_EXPIRY_HOURS = 1 / 60  # 1 minute
FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS = 60  # 1 minute
# Keep prerelease commit history fresh so we see new prereleases within a minute.
PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS = 60

# Concurrent operations limits
MAX_CONCURRENT_TIMESTAMP_FETCHES = 5

# File Type Patterns (non-device-specific patterns)
FILE_TYPE_PREFIXES = {
    "device-",  # device-install.sh, device-update.sh
    "bleota",  # bleota.bin, bleota-c3.bin, bleota-s3.bin
}
