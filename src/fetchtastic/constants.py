"""
Constants and string literals for Fetchtastic.

This module centralizes all user-facing strings and constants to ensure consistency
across the application and enable future internationalization.
"""

# Asset Type Display Names (User-facing)
ASSET_FIRMWARE = "Firmware"
ASSET_ANDROID_APPS = "Android Apps"  # Generic for multiple apps
ASSET_MESHTASTIC_ANDROID_APP = "Meshtastic Android App"  # Specific app
ASSET_BOOTLOADERS = "Bootloaders"
ASSET_DFU_APPS = "DFU Apps"

# Asset Type Internal IDs
ASSET_ID_FIRMWARE = "firmware"
ASSET_ID_ANDROID = "android"
ASSET_ID_BOOTLOADERS = "bootloaders"
ASSET_ID_DFU_APPS = "dfu_apps"

# Logging Messages - Download Status
LOG_FETCHING_FIRMWARE_RELEASES = "Fetching firmware releases from GitHub..."
LOG_FETCHING_ANDROID_APP_RELEASES = (
    "Fetching Meshtastic Android App releases from GitHub..."
)
LOG_FETCHING_BOOTLOADER_RELEASES = "Fetching OTA-fix bootloader releases from GitHub..."
LOG_FETCHING_DFU_RELEASES = "Checking Nordic DFU Library..."

LOG_ALL_FIRMWARE_UP_TO_DATE = "All Firmware assets are up to date."
LOG_ALL_ANDROID_UP_TO_DATE = "All Meshtastic Android App assets are up to date."
LOG_ALL_BOOTLOADERS_UP_TO_DATE = "All Bootloader assets are up to date."
LOG_ALL_DFU_UP_TO_DATE = "All DFU App assets are up to date."

# Logging Messages - Processing
LOG_PROCESSING_BOOTLOADERS = "Processing device bootloaders..."
LOG_PROCESSING_OTA_BOOTLOADERS = "Processing OTA-fix bootloaders..."
LOG_PROCESSING_STOCK_BOOTLOADERS = "Processing stock bootloaders..."
LOG_PROCESSING_DFU_APPS = "Processing DFU/flashing apps..."

LOG_BOOTLOADER_PROCESSING_COMPLETE = "Device bootloader processing complete"
LOG_STOCK_BOOTLOADER_PROCESSING_COMPLETE = "Stock bootloader processing complete"
LOG_DFU_PROCESSING_COMPLETE = "DFU/flashing app processing complete"

# Latest Version Display (Summary) - without "Latest" prefix since it's added in code
LATEST_FIRMWARE_LABEL = "Firmware"
LATEST_ANDROID_APPS_LABEL = "Android Apps"  # Consistent with asset name
LATEST_BOOTLOADERS_LABEL = "Bootloaders"
LATEST_DFU_APPS_LABEL = "DFU Apps"

# Asset Type Mappings for Latest Versions
LATEST_VERSION_LABELS = {
    ASSET_ID_FIRMWARE: LATEST_FIRMWARE_LABEL,
    ASSET_ID_ANDROID: LATEST_ANDROID_APPS_LABEL,
    ASSET_ID_BOOTLOADERS: LATEST_BOOTLOADERS_LABEL,
    ASSET_ID_DFU_APPS: LATEST_DFU_APPS_LABEL,
}

# Enabled Assets List (for summary)
ENABLED_ASSETS_LABELS = {
    "SAVE_FIRMWARE": ASSET_FIRMWARE,
    "SAVE_APKS": ASSET_ANDROID_APPS,  # Note: Using generic "Android Apps" since we handle multiple
    "SAVE_BOOTLOADERS": ASSET_BOOTLOADERS,
    "SAVE_DFU_APPS": ASSET_DFU_APPS,
}

# Processing Messages
LOG_PROCESSING_BOOTLOADERS = "Processing device bootloaders..."
LOG_PROCESSING_OTA_BOOTLOADERS = "Processing OTA-fix bootloaders..."
LOG_PROCESSING_STOCK_BOOTLOADERS = "Processing stock bootloaders..."
LOG_PROCESSING_DFU_APPS = "Processing DFU/flashing apps..."

LOG_BOOTLOADER_PROCESSING_COMPLETE = "Device bootloader processing complete"
LOG_STOCK_BOOTLOADER_PROCESSING_COMPLETE = "Stock bootloader processing complete"
LOG_DFU_PROCESSING_COMPLETE = "DFU/flashing app processing complete"

# Error Messages
ERROR_NO_ASSETS_ENABLED = "No asset types are enabled for download."
ERROR_SETUP_SUGGESTION = (
    "Run 'fetchtastic setup' to configure which assets you want to download."
)

# Success Messages
SUCCESS_ALL_ASSETS_UP_TO_DATE = "All assets are up to date."
SUCCESS_COMPLETED_IN = "Completed in {time:.1f}s"
SUCCESS_DOWNLOADED_COUNT = "Downloaded {count} new files"

# Specific App Names
APP_NORDIC_DFU_LIBRARY = "Nordic DFU Library"
APP_MESHTASTIC_ANDROID = "Meshtastic Android"

# Repository/Source Names
REPO_MESHTASTIC_FIRMWARE = "meshtastic/firmware"
REPO_MESHTASTIC_ANDROID = "meshtastic/Meshtastic-Android"
REPO_NORDIC_DFU = "NordicSemiconductor/Android-DFU-Library"

# File Extensions
EXT_APK = ".apk"
EXT_ZIP = ".zip"
EXT_HEX = ".hex"
EXT_UF2 = ".uf2"

# Default Values
DEFAULT_SCAN_COUNT = 10
DEFAULT_TIMEOUT = 5
DEFAULT_RETRY_COUNT = 3

# Configuration Keys
CONFIG_SAVE_FIRMWARE = "SAVE_FIRMWARE"
CONFIG_SAVE_APKS = "SAVE_APKS"
CONFIG_SAVE_BOOTLOADERS = "SAVE_BOOTLOADERS"
CONFIG_SAVE_DFU_APPS = "SAVE_DFU_APPS"

CONFIG_SELECTED_FIRMWARE = "SELECTED_FIRMWARE_ASSETS"
CONFIG_SELECTED_APKS = "SELECTED_APK_ASSETS"
CONFIG_SELECTED_BOOTLOADERS = "SELECTED_BOOTLOADER_ASSETS"
CONFIG_SELECTED_DFU_APPS = "SELECTED_DFU_APPS"

# Bootloader Types
BOOTLOADER_TYPE_OTA_FIX = "ota_fix_bootloaders"
BOOTLOADER_TYPE_STOCK = "stock_bootloaders"

# Stock Bootloader Names
STOCK_RAK4631 = "RAK4631"
STOCK_T1000E = "T1000-E"

# Status Messages for Stock Bootloaders
LOG_RAK4631_EXISTS = f"{STOCK_RAK4631} stock bootloader already exists"
LOG_T1000E_EXISTS = f"{STOCK_T1000E} stock bootloader already exists"
