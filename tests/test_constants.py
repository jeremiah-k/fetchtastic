"""
Test constants and mock data for Fetchtastic tests.

This module contains all test-specific constants, mock data, and configuration
values used in the test suite.
"""

# Test version strings
TEST_VERSION_OLD = "2.1.0"
TEST_VERSION_NEW = "2.7.4.c1f4f79"
TEST_VERSION_NEWER = "2.8.0.a1b2c3d"

# Test device names (realistic Meshtastic devices)
TEST_DEVICES = [
    "rak4631",
    "heltec-v3",
    "tbeam",
    "rak11200",
    "tlora-v2-1-1_6",
    "t1000-e",
]

# Test firmware file patterns
TEST_FIRMWARE_FILES = [
    f"firmware-{device}-{TEST_VERSION_NEW}.zip" for device in TEST_DEVICES
]

# Test littlefs files (ESP32 devices only)
TEST_ESP32_DEVICES = ["rak11200", "tlora-v2-1-1_6", "t1000-e"]
TEST_LITTLEFS_FILES = [
    f"littlefs-{device}-{TEST_VERSION_NEW}.bin" for device in TEST_ESP32_DEVICES
]

# Test bleota files (nRF52 devices)
TEST_NRF52_DEVICES = ["rak4631", "heltec-v3", "tbeam"]
TEST_BLEOTA_FILES = [
    f"bleota-{device}-{TEST_VERSION_NEW}.bin" for device in TEST_NRF52_DEVICES
]

# Test script files
TEST_SCRIPT_FILES = [f"device-install-{device}.sh" for device in TEST_DEVICES] + [
    f"device-update-{device}.sh" for device in TEST_DEVICES
]

# Test APK files
TEST_APK_FILES = [
    "app-release.apk",
    "app-debug.apk",
    "app-armeabi-v7a-release.apk",
    "app-arm64-v8a-release.apk",
    "app-x86_64-release.apk",
]

# Test directory structures
TEST_FIRMWARE_DIR = "firmware"
TEST_PRERELEASE_DIR = "prerelease"
TEST_REPO_DIR = "repo-dls"
TEST_APKS_DIR = "apks"

# Test file sizes (in bytes)
TEST_SMALL_FILE_SIZE = 1024  # 1KB
TEST_MEDIUM_FILE_SIZE = 1024 * 1024  # 1MB
TEST_LARGE_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Test timeout values (shorter for tests)
TEST_TIMEOUT_SHORT = 1  # 1 second
TEST_TIMEOUT_MEDIUM = 5  # 5 seconds
TEST_TIMEOUT_LONG = 10  # 10 seconds

# Test retry counts
TEST_MAX_RETRIES = 2
TEST_BACKOFF_FACTOR = 0.1

# Mock GitHub API responses
MOCK_GITHUB_RELEASE = {
    "tag_name": TEST_VERSION_NEW,
    "name": f"Meshtastic Firmware {TEST_VERSION_NEW}",
    "assets": [
        {
            "name": f"firmware-{device}-{TEST_VERSION_NEW}.zip",
            "size": TEST_MEDIUM_FILE_SIZE,
            "browser_download_url": f"https://github.com/meshtastic/firmware/releases/download/{TEST_VERSION_NEW}/firmware-{device}-{TEST_VERSION_NEW}.zip",
        }
        for device in TEST_DEVICES
    ],
}

MOCK_ANDROID_RELEASE = {
    "tag_name": TEST_VERSION_NEW,
    "name": f"Meshtastic Android {TEST_VERSION_NEW}",
    "assets": [
        {
            "name": apk_file,
            "size": TEST_MEDIUM_FILE_SIZE,
            "browser_download_url": f"https://github.com/meshtastic/Meshtastic-Android/releases/download/{TEST_VERSION_NEW}/{apk_file}",
        }
        for apk_file in TEST_APK_FILES
    ],
}

# Test configuration values
TEST_CONFIG = {
    "BASE_DIR": "/home/test/test_meshtastic",  # nosec B108
    "FIRMWARE_VERSIONS_TO_KEEP": 2,
    "ANDROID_VERSIONS_TO_KEEP": 2,
    "AUTO_EXTRACT": False,
    "EXTRACT_PATTERNS": ["rak4631-", "tbeam"],
    "SELECTED_FIRMWARE_ASSETS": ["rak4631", "heltec-v3"],
    "SELECTED_APK_ASSETS": ["app-release.apk"],
    "ENABLE_FIRMWARE_DOWNLOADS": True,
    "ENABLE_APK_DOWNLOADS": True,
    "ENABLE_PRERELEASE_DOWNLOADS": False,
}

# Test file content
TEST_FILE_CONTENT = b"Test file content for Fetchtastic tests"
TEST_ZIP_CONTENT = b"PK\x03\x04"  # ZIP file magic bytes

# Test hash values
TEST_FILE_HASH = "a1b2c3d4e5f6"
TEST_DIFFERENT_HASH = "f6e5d4c3b2a1"

# Test error messages
TEST_ERROR_NETWORK = "Network error occurred"
TEST_ERROR_FILE_NOT_FOUND = "File not found"
TEST_ERROR_PERMISSION_DENIED = "Permission denied"

# Test log messages
TEST_LOG_INFO = "Test info message"
TEST_LOG_WARNING = "Test warning message"
TEST_LOG_ERROR = "Test error message"
