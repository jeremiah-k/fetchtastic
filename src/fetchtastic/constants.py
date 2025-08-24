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
DEFAULT_REQUEST_TIMEOUT = 30
API_CALL_DELAY = 0.1  # Small delay to be respectful to GitHub API

# Download and retry settings
RELEASE_SCAN_COUNT = 10
DEFAULT_CONNECT_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 1.0
DEFAULT_CHUNK_SIZE = 8 * 1024  # 8KB

# Windows-specific retry settings
WINDOWS_MAX_REPLACE_RETRIES = 3
WINDOWS_INITIAL_RETRY_DELAY = 1.0  # seconds

# File and directory names
REPO_DOWNLOADS_DIR = "repo-dls"
PRERELEASE_DIR = "prerelease"
LATEST_ANDROID_RELEASE_FILE = "latest_android_release.txt"
LATEST_FIRMWARE_RELEASE_FILE = "latest_firmware_release.txt"

# Default configuration values
DEFAULT_FIRMWARE_VERSIONS_TO_KEEP = 2
DEFAULT_ANDROID_VERSIONS_TO_KEEP = 2
DEFAULT_AUTO_EXTRACT = False

# File extensions and patterns
APK_EXTENSION = ".apk"
ZIP_EXTENSION = ".zip"
SHELL_SCRIPT_EXTENSION = ".sh"
EXECUTABLE_PERMISSIONS = 0o755

# Logging configuration
LOGGER_NAME = "fetchtastic"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
INFO_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DEBUG_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"
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

# Environment variable names
LOG_LEVEL_ENV_VAR = "FETCHTASTIC_LOG_LEVEL"
