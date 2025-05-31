# src/fetchtastic/downloader.py

import json
from typing import List, Dict, Any, Optional, Tuple, Callable
import os
import platform
import re
import shutil
import time
import zipfile
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fetchtastic import menu_repo, setup_config
from fetchtastic.log_utils import (
    log_info,
    setup_logging,
)
from fetchtastic.setup_config import display_version_info, get_upgrade_command


def compare_versions(version1, version2):
    """
    Compares two version strings (e.g., 2.6.9.f93d031 vs 2.6.8.ef9d0d7).

    Returns:
        1 if version1 > version2
        0 if version1 == version2
        -1 if version1 < version2
    """
    # Handle exact matches immediately
    if version1 == version2:
        return 0

    # Split versions into components
    v1_parts = version1.split(".")
    v2_parts = version2.split(".")

    # Make sure we have at least 3 parts for each version
    if len(v1_parts) < 3 or len(v2_parts) < 3:
        # If either version doesn't have at least 3 parts, do a simple string comparison
        return 1 if version1 > version2 else (-1 if version1 < version2 else 0)

    # Compare major, minor, patch versions numerically
    for i in range(3):  # Only compare the first 3 parts (major.minor.patch)
        try:
            v1_num = int(v1_parts[i])
            v2_num = int(v2_parts[i])
            if v1_num > v2_num:
                return 1
            elif v1_num < v2_num:
                return -1
        except ValueError:
            # If conversion fails, fall back to string comparison
            if v1_parts[i] > v2_parts[i]:
                return 1
            elif v1_parts[i] < v2_parts[i]:
                return -1

    # If major.minor.patch are equal, versions are considered equal
    # The commit hash (4th part) doesn't affect version ordering
    return 0


def check_promoted_prereleases(download_dir, latest_release_tag, log_message_func=None):
    """
    Checks if any pre-releases have been promoted to regular releases.
    If a pre-release matches the latest release, it verifies the files match
    and either moves them to the regular release directory or deletes them.

    Args:
        download_dir: Base download directory
        latest_release_tag: The latest official release tag (e.g., v2.6.8.ef9d0d7)
        log_message_func: Function to log messages (optional)

    Returns:
        Boolean indicating if any pre-releases were promoted
    """
    if log_message_func is None:

        def log_message_func(message):
            print(message)

    # Strip the 'v' prefix if present
    if latest_release_tag.startswith("v"):
        latest_release_version = latest_release_tag[1:]
    else:
        latest_release_version = latest_release_tag

    # Path to prerelease directory
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    if not os.path.exists(prerelease_dir):
        return False

    # Path to regular release directory
    release_dir = os.path.join(download_dir, "firmware", latest_release_tag)

    # Check for matching pre-release directories
    promoted = False
    for dir_name in os.listdir(prerelease_dir):
        if dir_name.startswith("firmware-"):
            dir_version = dir_name[9:]  # Remove 'firmware-' prefix

            # If this pre-release matches the latest release version
            if dir_version == latest_release_version:
                log_message_func(
                    f"Found pre-release {dir_name} that matches latest release {latest_release_tag}"
                )
                prerelease_path = os.path.join(prerelease_dir, dir_name)

                # If the release directory doesn't exist yet, we can't compare files
                # We'll just remove the pre-release directory since it will be downloaded as a regular release
                if not os.path.exists(release_dir):
                    log_message_func(
                        f"Pre-release {dir_name} has been promoted to release {latest_release_tag}, "
                        f"but the release directory doesn't exist yet. Removing pre-release."
                    )
                    shutil.rmtree(prerelease_path)
                    log_message_func(
                        f"Removed pre-release directory: {prerelease_path}"
                    )
                    promoted = True
                    continue

                # Verify files match by comparing hashes
                files_match = True
                for file_name in os.listdir(prerelease_path):
                    prerelease_file = os.path.join(prerelease_path, file_name)
                    release_file = os.path.join(release_dir, file_name)

                    if os.path.exists(release_file):
                        # Compare file hashes
                        if not compare_file_hashes(prerelease_file, release_file):
                            files_match = False
                            log_message_func(
                                f"File {file_name} in pre-release doesn't match the release version"
                            )
                            break

                if files_match:
                    log_message_func(
                        f"Pre-release {dir_name} has been promoted to release {latest_release_tag}"
                    )
                    # Remove the pre-release directory since it's now a regular release
                    shutil.rmtree(prerelease_path)
                    log_message_func(
                        f"Removed pre-release directory: {prerelease_path}"
                    )
                    promoted = True

    return promoted


def compare_file_hashes(file1, file2):
    """
    Compares the SHA-256 hashes of two files to check if they are identical.

    Args:
        file1: Path to first file
        file2: Path to second file

    Returns:
        Boolean indicating if the files have the same hash
    """
    import hashlib

    def get_file_hash(file_path):
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read and update hash in chunks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    hash1 = get_file_hash(file1)
    hash2 = get_file_hash(file2)

    return hash1 == hash2


def check_for_prereleases(
    download_dir, latest_release_tag, selected_patterns, log_message_func=None
):
    """
    Checks for pre-release firmware in the meshtastic.github.io repository.
    Also cleans up stale pre-releases that no longer exist in the repository.

    Args:
        download_dir: Base download directory
        latest_release_tag: The latest official release tag (e.g., v2.6.8.ef9d0d7)
        selected_patterns: List of firmware patterns to download
        log_message_func: Function to log messages (optional)

    Returns:
        Tuple of (boolean indicating if any pre-releases were found and downloaded,
                 list of pre-release versions that were downloaded)
    """
    if log_message_func is None:

        def log_message_func(message): # This local definition is fine if _log_message is not passed.
            print(message)

    # Strip the 'v' prefix if present
    if latest_release_tag.startswith("v"):
        latest_release_version = latest_release_tag[1:]
    else:
        latest_release_version = latest_release_tag

    # Fetch directories from the meshtastic.github.io repository
    directories = menu_repo.fetch_repo_directories()

    if not directories:
        log_message_func("No firmware directories found in the repository.")
        return False, []

    # Get list of existing firmware directories (both regular and pre-releases)
    firmware_dir = os.path.join(download_dir, "firmware")
    existing_firmware_dirs = []
    if os.path.exists(firmware_dir):
        for item in os.listdir(firmware_dir):
            item_path = os.path.join(firmware_dir, item)
            if os.path.isdir(item_path) and item != "prerelease" and item != "repo-dls":
                # This is a regular firmware directory (e.g., v2.6.8.ef9d0d7)
                existing_firmware_dirs.append(item)

    # Also check existing pre-releases
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    existing_prerelease_dirs = []
    if os.path.exists(prerelease_dir):
        for item in os.listdir(prerelease_dir):
            if os.path.isdir(os.path.join(prerelease_dir, item)):
                existing_prerelease_dirs.append(item)

    # Extract all firmware directory names from the repository
    repo_firmware_dirs = [
        dir_name for dir_name in directories if dir_name.startswith("firmware-")
    ]

    # Clean up the prerelease directory
    # Only keep directories that:
    # 1. Exist in the repository
    # 2. Are newer than the latest release
    if os.path.exists(prerelease_dir):
        # First, clean up any non-directory files in the prerelease directory
        for item in os.listdir(prerelease_dir):
            item_path = os.path.join(prerelease_dir, item)
            if not os.path.isdir(item_path):
                try:
                    log_message_func(
                        f"Removing stale file from prerelease directory: {item}"
                    )
                    os.remove(item_path)
                except Exception as e:
                    log_message_func(f"Error removing file {item_path}: {e}")

        # Now clean up directories
        for dir_name in existing_prerelease_dirs:
            should_keep = False

            # Check if it's a firmware directory
            if dir_name.startswith("firmware-"):
                dir_version = dir_name[9:]  # Remove 'firmware-' prefix

                # Check if it exists in the repository
                if dir_name in repo_firmware_dirs:
                    # Check if it's newer than the latest release
                    comparison_result = compare_versions(
                        dir_version, latest_release_version
                    )
                    if comparison_result > 0:
                        should_keep = True

            if not should_keep:
                dir_path = os.path.join(prerelease_dir, dir_name)
                try:
                    log_message_func(
                        f"Removing stale pre-release directory: {dir_name}"
                    )
                    shutil.rmtree(dir_path)
                except Exception as e:
                    log_message_func(f"Error removing directory {dir_path}: {e}")

    # Find directories in the repository that are newer than the latest release and don't already exist locally
    prerelease_dirs = []
    for dir_name in directories:
        # Extract version from directory name (e.g., firmware-2.6.9.f93d031)
        if dir_name.startswith("firmware-"):
            dir_version = dir_name[9:]  # Remove 'firmware-' prefix

            # Check if this version is newer than the latest release
            comparison_result = compare_versions(dir_version, latest_release_version)
            if comparison_result > 0:

                # Refresh the list of existing prerelease directories after cleanup
                existing_prerelease_dirs = []
                if os.path.exists(prerelease_dir):
                    for item in os.listdir(prerelease_dir):
                        if os.path.isdir(os.path.join(prerelease_dir, item)):
                            existing_prerelease_dirs.append(item)

                # Only add if it doesn't already exist locally
                if dir_name not in existing_prerelease_dirs:
                    prerelease_dirs.append(dir_name)

    if not prerelease_dirs:
        return False, []

    # Create prerelease directory if it doesn't exist
    if not os.path.exists(prerelease_dir):
        os.makedirs(prerelease_dir)

    downloaded_files = []

    # Process each pre-release directory
    for dir_name in prerelease_dirs:
        log_message_func(f"Found pre-release: {dir_name}")

        # Fetch files from the directory
        files = menu_repo.fetch_directory_contents(dir_name)

        if not files:
            log_message_func(f"No files found in {dir_name}.")
            continue

        # Create directory for this pre-release
        dir_path = os.path.join(prerelease_dir, dir_name)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        # Filter files based on selected patterns
        for file in files:
            file_name = file["name"]
            download_url = file["download_url"]
            file_path = os.path.join(dir_path, file_name)

            # Only download files that match the selected patterns
            stripped_file_name = strip_version_numbers(file_name)
            if not any(pattern in stripped_file_name for pattern in selected_patterns):
                continue  # Skip this file

            if not os.path.exists(file_path):
                try:
                    log_message_func(f"Downloading pre-release file: {file_name}")
                    response = requests.get(download_url, stream=True, timeout=30)
                    response.raise_for_status()

                    with open(file_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    # Set executable permissions for .sh files
                    if file_name.endswith(".sh"):
                        os.chmod(file_path, 0o755)
                        log_message_func(f"Set executable permissions for {file_name}")

                    log_message_func(f"Downloaded {file_name} to {file_path}")
                    downloaded_files.append(file_path)
                except Exception as e:
                    log_message_func(f"Error downloading {file_name}: {e}")

    downloaded_versions = []
    if downloaded_files:
        log_message_func(
            f"Successfully downloaded {len(downloaded_files)} pre-release files."
        )
        # Extract unique directory names from downloaded files
        for dir_name in prerelease_dirs:
            if any(dir_name in file_path for file_path in downloaded_files):
                downloaded_versions.append(dir_name)
        return True, downloaded_versions
    else:
        # Don't log here - we'll log once at the caller level
        return False, []


# Use the version check function from setup_config

# Global variable to track if downloads were skipped due to Wi-Fi check
downloads_skipped: bool = False

def _log_message(message: str) -> None:
    """
    Helper log_message function that now uses the new logging system.

    Args:
        message (str): The message to log.
    """
    log_info(message)


def _send_ntfy_notification(ntfy_server: Optional[str], ntfy_topic: Optional[str], message: str, title: Optional[str] = None) -> None:
    """
    Sends a notification via NTFY.

    Args:
        ntfy_server (Optional[str]): The NTFY server URL.
        ntfy_topic (Optional[str]): The NTFY topic name.
        message (str): The message content to send.
        title (Optional[str]): The title of the notification.
    """
    if ntfy_server and ntfy_topic:
        try:
            ntfy_url: str = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
            }
            if title:
                headers["Title"] = title
            response: requests.Response = requests.post(
                ntfy_url, data=message.encode("utf-8"), headers=headers, timeout=10
            )
            response.raise_for_status()
            _log_message(f"Notification sent to {ntfy_url}")
        except requests.exceptions.RequestException as e:
            _log_message(f"Error sending notification to {ntfy_url}: {e}")
    else:
        # Don't log when notifications are not configured
        pass


def _get_latest_releases_data(url: str, scan_count: int = 10) -> List[Dict[str, Any]]:
    """
    Fetches the latest releases from a GitHub API URL and sorts them by date.

    Args:
        url (str): The GitHub API URL for releases.
        scan_count (int): The number of most recent releases to scan.

    Returns:
        List[Dict[str, Any]]: A list of release data dictionaries, sorted by publication date.
    """
    response: requests.Response = requests.get(url, timeout=10)
    response.raise_for_status()
    releases: List[Dict[str, Any]] = response.json()
    # Sort releases by published date, descending order
    sorted_releases: List[Dict[str, Any]] = sorted(
        releases, key=lambda r: r["published_at"], reverse=True
    )
    # Limit the number of releases to be scanned
    return sorted_releases[:scan_count]


def _initial_setup_and_config() -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str], bool, Optional[Dict[str, str]]]:
    """
    Handles initial setup including version display, configuration loading,
    logging setup, and directory creation.

    Returns:
        Tuple containing:
            - Optional[Dict[str, Any]]: Loaded configuration dictionary, or None if setup failed.
            - Optional[str]: Current application version.
            - Optional[str]: Latest available application version.
            - bool: True if an update is available, False otherwise.
            - Optional[Dict[str, str]]: Dictionary of important paths and URLs, or None if setup failed.
    """
    current_version: Optional[str]
    latest_version: Optional[str]
    update_available: bool
    current_version, latest_version, update_available = display_version_info()

    config: Optional[Dict[str, Any]] = setup_config.load_config()
    if not config:
        print("Configuration not found. Please run 'fetchtastic setup' first.")
        return None, current_version, latest_version, update_available, None

    download_dir: str = config.get(
        "DOWNLOAD_DIR",
        os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"),
    )
    setup_logging(download_dir) # type: ignore # setup_logging is not typed in stub

    _log_message(f"Fetchtastic v{current_version if current_version else 'unknown'}")
    if update_available and latest_version:
        _log_message(f"A newer version (v{latest_version}) is available!")
        upgrade_cmd: str = get_upgrade_command()
        _log_message(f"Run '{upgrade_cmd}' to upgrade.")

    firmware_dir: str = os.path.join(download_dir, "firmware")
    apks_dir: str = os.path.join(download_dir, "apks")
    dir_path_to_create: str
    for dir_path_to_create in [download_dir, firmware_dir, apks_dir]:
        if not os.path.exists(dir_path_to_create):
            os.makedirs(dir_path_to_create)
            _log_message(f"Created directory: {dir_path_to_create}")

    paths_and_urls: Dict[str, str] = {
        "download_dir": download_dir,
        "firmware_dir": firmware_dir,
        "apks_dir": apks_dir,
        "latest_android_release_file": os.path.join(apks_dir, "latest_android_release.txt"),
        "latest_firmware_release_file": os.path.join(firmware_dir, "latest_firmware_release.txt"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases",
        "firmware_releases_url": "https://api.github.com/repos/meshtastic/firmware/releases",
    }

    return config, current_version, latest_version, update_available, paths_and_urls


def _check_wifi_connection(config: Dict[str, Any]) -> None:
    """
    Checks Wi-Fi connection if configured, updating the global 'downloads_skipped'.
    Args:
        config (Dict[str, Any]): The application configuration.
    """
    global downloads_skipped
    if setup_config.is_termux() and config.get("WIFI_ONLY", False):
        if not is_connected_to_wifi():
            downloads_skipped = True
            _log_message("Not connected to Wi-Fi. Skipping all downloads.")


def _process_firmware_downloads(config: Dict[str, Any], paths_and_urls: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    Handles the firmware download process, including pre-releases.

    Args:
        config (Dict[str, Any]): The application configuration.
        paths_and_urls (Dict[str, str]): Dictionary of important paths and URLs.

    Returns:
        Tuple[List[str], List[str]]: A tuple containing:
            - List of downloaded firmware versions.
            - List of new firmware versions detected.
    """
    global downloads_skipped
    downloaded_firmwares: List[str] = []
    new_firmware_versions: List[str] = []

    if config.get("SAVE_FIRMWARE", False) and config.get("SELECTED_FIRMWARE_ASSETS", []):
        latest_firmware_releases: List[Dict[str, Any]] = _get_latest_releases_data(
            paths_and_urls["firmware_releases_url"], config.get("FIRMWARE_VERSIONS_TO_KEEP", 2)
        )
        fw_downloaded: List[str]
        fw_new_versions: List[str]
        fw_downloaded, fw_new_versions = check_and_download(
            latest_firmware_releases,
            paths_and_urls["latest_firmware_release_file"],
            "Firmware",
            paths_and_urls["firmware_dir"],
            config.get("FIRMWARE_VERSIONS_TO_KEEP", 2),
            config.get("EXTRACT_PATTERNS", []),
            selected_patterns=config.get("SELECTED_FIRMWARE_ASSETS", []), # type: ignore
            auto_extract=config.get("AUTO_EXTRACT", False),
            exclude_patterns=config.get("EXCLUDE_PATTERNS", []), # type: ignore
        )
        downloaded_firmwares.extend(fw_downloaded)
        new_firmware_versions.extend(fw_new_versions)
        if fw_downloaded:
            _log_message(f"Downloaded Firmware versions: {', '.join(fw_downloaded)}")

        latest_release_tag: Optional[str] = None
        if os.path.exists(paths_and_urls["latest_firmware_release_file"]):
            with open(paths_and_urls["latest_firmware_release_file"], "r") as f:
                latest_release_tag = f.read().strip()

        if latest_release_tag:
            promoted: bool = check_promoted_prereleases(
                paths_and_urls["download_dir"], latest_release_tag, _log_message
            )
            if promoted:
                _log_message("Detected pre-release(s) that have been promoted to regular release.")

        if config.get("CHECK_PRERELEASES", False) and not downloads_skipped:
            if latest_release_tag:
                _log_message("Checking for pre-release firmware...")
                prerelease_found: bool
                prerelease_versions: List[str]
                prerelease_found, prerelease_versions = check_for_prereleases(
                    paths_and_urls["download_dir"],
                    latest_release_tag,
                    config.get("SELECTED_FIRMWARE_ASSETS", []), # type: ignore
                    _log_message,
                )
                if prerelease_found:
                    _log_message(f"Pre-release firmware downloaded successfully: {', '.join(prerelease_versions)}")
                    version: str
                    for version in prerelease_versions:
                        downloaded_firmwares.append(f"pre-release {version}")
                else:
                    _log_message("No new pre-release firmware found or downloaded.")
            else:
                _log_message("No latest release tag found. Skipping pre-release check.")
    elif not config.get("SELECTED_FIRMWARE_ASSETS", []):
        _log_message("No firmware assets selected. Skipping firmware download.")

    return downloaded_firmwares, new_firmware_versions


def _process_apk_downloads(config: Dict[str, Any], paths_and_urls: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    Handles the APK download process.

    Args:
        config (Dict[str, Any]): The application configuration.
        paths_and_urls (Dict[str, str]): Dictionary of important paths and URLs.

    Returns:
        Tuple[List[str], List[str]]: A tuple containing:
            - List of downloaded APK versions.
            - List of new APK versions detected.
    """
    global downloads_skipped
    downloaded_apks: List[str] = []
    new_apk_versions: List[str] = []

    if config.get("SAVE_APKS", False) and config.get("SELECTED_APK_ASSETS", []):
        latest_android_releases: List[Dict[str, Any]] = _get_latest_releases_data(
            paths_and_urls["android_releases_url"], config.get("ANDROID_VERSIONS_TO_KEEP", 2)
        )
        apk_downloaded: List[str]
        apk_new_versions_list: List[str]
        apk_downloaded, apk_new_versions_list = check_and_download(
            latest_android_releases,
            paths_and_urls["latest_android_release_file"],
            "Android APK",
            paths_and_urls["apks_dir"],
            config.get("ANDROID_VERSIONS_TO_KEEP", 2),
            [],
            selected_patterns=config.get("SELECTED_APK_ASSETS", []), # type: ignore
            auto_extract=False,
            exclude_patterns=[],
        )
        downloaded_apks.extend(apk_downloaded)
        new_apk_versions.extend(apk_new_versions_list)
        if apk_downloaded:
            _log_message(f"Downloaded Android APK versions: {', '.join(apk_downloaded)}")
    elif not config.get("SELECTED_APK_ASSETS", []):
        _log_message("No APK assets selected. Skipping APK download.")

    return downloaded_apks, new_apk_versions


def _finalize_and_notify(
    start_time: float,
    config: Dict[str, Any],
    downloaded_firmwares: List[str],
    downloaded_apks: List[str],
    new_firmware_versions: List[str],
    new_apk_versions: List[str],
    current_version: Optional[str],
    latest_version: Optional[str],
    update_available: bool
) -> None:
    """
    Handles final logging, application update messages, and notifications.

    Args:
        start_time (float): The start time of the download process.
        config (Dict[str, Any]): The application configuration.
        downloaded_firmwares (List[str]): List of downloaded firmware versions.
        downloaded_apks (List[str]): List of downloaded APK versions.
        new_firmware_versions (List[str]): List of new firmware versions detected.
        new_apk_versions (List[str]): List of new APK versions detected.
        current_version (Optional[str]): Current application version.
        latest_version (Optional[str]): Latest available application version.
        update_available (bool): True if an update is available.
    """
    global downloads_skipped
    end_time: float = time.time()
    total_time: float = end_time - start_time
    _log_message(f"Finished the Meshtastic downloader. Total time taken: {total_time:.2f} seconds")

    if update_available and latest_version :
        upgrade_cmd: str = get_upgrade_command()
        _log_message("\nUpdate Available")
        _log_message(f"A newer version (v{latest_version}) of Fetchtastic is available!")
        _log_message(f"Run '{upgrade_cmd}' to upgrade.")

    ntfy_server: Optional[str] = config.get("NTFY_SERVER", "")
    ntfy_topic: Optional[str] = config.get("NTFY_TOPIC", "")
    notify_on_download_only: bool = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)

    notification_message: str
    message_lines: List[str]

    if downloads_skipped:
        message_lines = ["New releases are available but downloads were skipped because the device is not connected to Wi-Fi."]
        if new_firmware_versions:
            message_lines.append(f"Firmware versions available: {', '.join(new_firmware_versions)}")
        if new_apk_versions:
            message_lines.append(f"Android APK versions available: {', '.join(new_apk_versions)}")
        notification_message = "\n".join(message_lines) + f"\n{datetime.now()}"
        _log_message("\n".join(message_lines))
        _send_ntfy_notification(ntfy_server, ntfy_topic, notification_message, title="Fetchtastic Downloads Skipped")
    elif downloaded_firmwares or downloaded_apks:
        notification_messages: List[str] = []
        message: str
        if downloaded_firmwares:
            message = f"Downloaded Firmware versions: {', '.join(downloaded_firmwares)}"
            notification_messages.append(message)
        if downloaded_apks:
            message = f"Downloaded Android APK versions: {', '.join(downloaded_apks)}"
            notification_messages.append(message)
        notification_message = "\n".join(notification_messages) + f"\n{datetime.now()}"
        _send_ntfy_notification(ntfy_server, ntfy_topic, notification_message, title="Fetchtastic Download Completed")
    else:
        message: str = f"All assets are up to date.\n{datetime.now()}"
        _log_message(message)
        if not notify_on_download_only:
            _send_ntfy_notification(ntfy_server, ntfy_topic, message, title="Fetchtastic Up to Date")


def download_file(url: str, download_path: str) -> bool:
    """
    Downloads a file with a retry mechanism.

    Args:
        url (str): URL to download from.
        download_path (str): Path to save the file to.

    Returns:
        bool: True if the file was downloaded or already exists and is valid,
              False if the download failed.
    """
    session: requests.Session = requests.Session()
    # Assuming Retry and HTTPAdapter are correctly imported and typed by their libraries
    # If specific types are available/needed, they should be used.
    retry_strategy: Retry = Retry(connect=3, backoff_factor=1, status_forcelist=[502, 503, 504]) # type: ignore
    adapter: HTTPAdapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if os.path.exists(download_path):
        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(download_path, "r"):
                    return True
            except zipfile.BadZipFile:
                _log_message(f"Removing corrupted zip file: {download_path}")
                os.remove(download_path)
            except Exception as e:
                _log_message(f"Error checking zip file {download_path}: {e}. Removing file.")
                os.remove(download_path)
        else:
            if os.path.getsize(download_path) > 0:
                return True
            else:
                _log_message(f"Removing empty file: {download_path}")
                os.remove(download_path)

    temp_path: str = download_path + ".tmp"
    try:
        _log_message(f"Downloading {url}")
        response: requests.Response = session.get(url, stream=True)
        response.raise_for_status()
        with open(temp_path, "wb") as file:
            chunk: bytes
            for chunk in response.iter_content(1024):
                file.write(chunk)
        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(temp_path, "r"):
                    pass # Validates zip
                if platform.system() == "Windows":
                    max_retries: int = 3
                    retry_delay: int = 1
                    retry_count: int
                    for retry_count in range(max_retries):
                        try:
                            import gc
                            gc.collect()
                            os.replace(temp_path, download_path)
                            _log_message(f"Downloaded {download_path}")
                            return True
                        except PermissionError as e:
                            if retry_count < max_retries - 1:
                                _log_message(f"File access error, retrying in {retry_delay} seconds: {e}")
                                time.sleep(retry_delay)
                                retry_delay *= 2
                            else:
                                raise
                else:
                    os.replace(temp_path, download_path)
                    _log_message(f"Downloaded {download_path}")
                    return True
            except zipfile.BadZipFile:
                if os.path.exists(temp_path): os.remove(temp_path)
                _log_message(f"Error: Downloaded zip file is corrupted: {url}")
                return False
        else:
            if platform.system() == "Windows":
                max_retries = 3
                retry_delay = 1
                for retry_count in range(max_retries):
                    try:
                        import gc
                        gc.collect()
                        os.replace(temp_path, download_path)
                        _log_message(f"Downloaded {download_path}")
                        return True
                    except PermissionError as e:
                        if retry_count < max_retries - 1:
                            _log_message(f"File access error, retrying in {retry_delay} seconds: {e}")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                        else:
                            raise
            else:
                os.replace(temp_path, download_path)
                _log_message(f"Downloaded {download_path}")
                return True
    except requests.exceptions.RequestException as e:
        _log_message(f"Error downloading {url}: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)
        return False
    except Exception as e:
        _log_message(f"Error processing download {url}: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)
        return False


def is_connected_to_wifi() -> bool:
    """
    Checks if the device is connected to Wi-Fi.
    For Termux, it uses 'termux-wifi-connectioninfo'.
    For other platforms, it currently assumes connected.

    Returns:
        bool: True if connected to Wi-Fi (or assumed to be), False otherwise.
    """
    if setup_config.is_termux():
        try:
            result: str = os.popen("termux-wifi-connectioninfo").read()
            if not result: return False
            data: Dict[str, Any] = json.loads(result)
            supplicant_state: str = data.get("supplicant_state", "")
            ip_address: str = data.get("ip", "")
            return supplicant_state == "COMPLETED" and ip_address != ""
        except Exception as e:
            _log_message(f"Error checking Wi-Fi connection: {e}")
            return False
    else:
        return True


def safe_extract_path(extract_dir: str, file_path: str) -> str:
    """
    Safely resolves the extraction path for a file to prevent directory traversal.

    It ensures that the resolved path is within the specified extraction directory.

    Args:
        extract_dir (str): The intended base directory for extraction.
        file_path (str): The relative path of the file to be extracted,
                         as obtained from the archive.

    Returns:
        str: The safe, absolute path for extraction.

    Raises:
        ValueError: If the resolved path is outside the `extract_dir`.
    """
    abs_extract_dir: str = os.path.abspath(extract_dir)
    prospective_path: str = os.path.join(abs_extract_dir, file_path)
    safe_path: str = os.path.normpath(prospective_path)

    if not safe_path.startswith(abs_extract_dir + os.sep) and safe_path != abs_extract_dir:
        if safe_path == abs_extract_dir and (file_path == "" or file_path == "."):
            pass
        else:
            raise ValueError(f"Unsafe path detected: '{file_path}' attempts to write outside of '{extract_dir}'")
    return safe_path


def extract_files(zip_path: str, extract_dir: str, patterns: List[str], exclude_patterns: List[str]) -> None:
    """
    Extracts files matching specified patterns from a zip archive, excluding others.

    Args:
        zip_path (str): Path to the zip file.
        extract_dir (str): Directory to extract files into.
        patterns (List[str]): List of keywords to identify files to extract.
        exclude_patterns (List[str]): List of keywords to identify files to exclude.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            file_info: zipfile.ZipInfo
            for file_info in zip_ref.infolist():
                file_name: str = file_info.filename
                base_name: str = os.path.basename(file_name)
                if not base_name: continue
                if any(exclude in base_name for exclude in exclude_patterns): continue

                stripped_base_name: str = strip_version_numbers(base_name)
                pattern: str
                for pattern in patterns:
                    if pattern in stripped_base_name:
                        try:
                            target_path: str = safe_extract_path(extract_dir, base_name)
                            if not os.path.exists(target_path):
                                target_dir_for_file: str = os.path.dirname(target_path)
                                if not os.path.exists(target_dir_for_file):
                                    os.makedirs(target_dir_for_file, exist_ok=True)
                                source: Any = zip_ref.open(file_info)
                                with open(target_path, "wb") as target_file:
                                    target_file.write(source.read())
                                _log_message(f"Extracted {base_name} to {extract_dir}")
                            if base_name.endswith(".sh"):
                                if not os.access(target_path, os.X_OK):
                                    os.chmod(target_path, 0o755)
                                    _log_message(f"Set executable permissions for {base_name}")
                            break
                        except ValueError as e:
                            _log_message(f"Skipping extraction of '{base_name}': {e}")
                            continue
    except zipfile.BadZipFile:
        _log_message(f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file.")
        try:
            os.remove(zip_path)
            _log_message(f"Removed corrupted zip file: {zip_path}")
        except Exception as e:
            _log_message(f"Error removing corrupted zip file {zip_path}: {e}")
    except Exception as e:
        _log_message(f"Error: An unexpected error occurred while extracting files from {zip_path}: {e}")


def strip_version_numbers(filename: str) -> str:
    """
    Removes version numbers and commit hashes from a filename.
    Uses the same regex as in menu_firmware.py for consistency.

    Args:
        filename (str): The filename to strip.

    Returns:
        str: The filename with version numbers and commit hashes removed.
    """
    base_name: str = re.sub(r"([_-])\d+\.\d+\.\d+(?:\.[\da-f]+)?", r"\1", filename)
    return base_name


def cleanup_old_versions(directory: str, releases_to_keep: List[str]) -> None:
    """
    Removes old version directories, keeping only specified releases.

    Args:
        directory (str): The directory containing versioned subdirectories.
        releases_to_keep (List[str]): A list of release tag names to keep.
    """
    excluded_dirs: List[str] = ["repo-dls", "prerelease"]
    versions: List[str] = [d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]
    version: str
    for version in versions:
        if version in excluded_dirs: continue
        if version not in releases_to_keep:
            version_path: str = os.path.join(directory, version)
            root: str
            dirs: List[str]
            files: List[str]
            for root, dirs, files in os.walk(version_path, topdown=False):
                name: str
                for name in files:
                    os.remove(os.path.join(root, name))
                    _log_message(f"Removed file: {os.path.join(root, name)}")
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(version_path)
            _log_message(f"Removed directory: {version_path}")


def strip_unwanted_chars(text: str) -> str:
    """
    Strips out non-printable characters and emojis from a string.

    Args:
        text (str): The input string.

    Returns:
        str: The string with non-printable characters and emojis removed.
    """
    printable_regex = re.compile(r"[^\x00-\x7F]+")
    return printable_regex.sub("", text)


def check_and_download(
    releases: List[Dict[str, Any]],
    latest_release_file: str,
    release_type: str,
    download_dir_path: str,
    versions_to_keep: int,
    extract_patterns: List[str],
    selected_patterns: Optional[List[str]] = None,
    auto_extract: bool = False,
    exclude_patterns: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Checks for missing releases and downloads them if necessary. Handles extraction and cleanup.

    Args:
        releases (List[Dict[str, Any]]): List of release data from GitHub API.
        latest_release_file (str): Path to the file storing the latest downloaded release tag.
        release_type (str): Type of release (e.g., "Firmware", "Android APK").
        download_dir_path (str): Base directory to download releases into.
        versions_to_keep (int): Number of latest versions to keep.
        extract_patterns (List[str]): Patterns for extracting files from zips (if auto_extract is True).
        selected_patterns (Optional[List[str]]): Patterns for selecting specific assets to download.
        auto_extract (bool): Whether to automatically extract files for this release type.
        exclude_patterns (Optional[List[str]]): Patterns to exclude from extraction.

    Returns:
        Tuple[List[str], List[str]]:
            - List of downloaded version tags.
            - List of new versions available but potentially skipped.
    """
    global downloads_skipped
    downloaded_versions: List[str] = []
    new_versions_available: List[str] = []
    actions_taken: bool = False
    exclude_patterns_list: List[str] = exclude_patterns or []


    if not os.path.exists(download_dir_path):
        os.makedirs(download_dir_path)

    saved_release_tag: Optional[str] = None
    if os.path.exists(latest_release_file):
        with open(latest_release_file, "r") as f:
            saved_release_tag = f.read().strip()

    releases_to_download: List[Dict[str, Any]] = releases[:versions_to_keep]

    if downloads_skipped:
        release_data: Dict[str, Any]
        for release_data in releases_to_download:
            if release_data["tag_name"] != saved_release_tag:
                new_versions_available.append(release_data["tag_name"])
        return downloaded_versions, new_versions_available

    release_data: Dict[str, Any]
    for release_data in releases_to_download:
        release_tag: str = release_data["tag_name"]
        release_dir: str = os.path.join(download_dir_path, release_tag)
        release_notes_file: str = os.path.join(release_dir, f"release_notes-{release_tag}.md")

        if not os.path.exists(release_dir):
            os.makedirs(release_dir, exist_ok=True)

        if not os.path.exists(release_notes_file) and release_data.get("body"):
            _log_message(f"Downloading release notes for version {release_tag}.")
            release_notes_content: str = strip_unwanted_chars(release_data["body"])
            with open(release_notes_file, "w", encoding="utf-8") as notes_file:
                notes_file.write(release_notes_content)
            _log_message(f"Saved release notes to {release_notes_file}")

        asset: Dict[str, Any]
        for asset in release_data["assets"]:
            file_name: str = asset["name"]
            if file_name.endswith(".zip"):
                asset_download_path: str = os.path.join(release_dir, file_name)
                if os.path.exists(asset_download_path):
                    try:
                        with zipfile.ZipFile(asset_download_path, "r"): pass
                    except zipfile.BadZipFile:
                        _log_message(f"Removing corrupted zip file: {asset_download_path}")
                        os.remove(asset_download_path)
                    except Exception as e:
                        _log_message(f"Error checking zip file {asset_download_path}: {e}. Removing file.")
                        os.remove(asset_download_path)

        assets_to_download: List[Tuple[str, str]] = []
        for asset in release_data["assets"]:
            file_name = asset["name"]
            stripped_file_name: str = strip_version_numbers(file_name)
            if selected_patterns and not any(pattern in stripped_file_name for pattern in selected_patterns):
                continue
            asset_download_path = os.path.join(release_dir, file_name)
            if not os.path.exists(asset_download_path):
                assets_to_download.append((asset["browser_download_url"], asset_download_path))

        if assets_to_download:
            actions_taken = True
            _log_message(f"Downloading missing assets for version {release_tag}.")
            any_downloaded: bool = False
            url: str
            path_to_download: str
            for url, path_to_download in assets_to_download:
                if download_file(url, path_to_download):
                    any_downloaded = True
            if any_downloaded:
                downloaded_versions.append(release_tag)

            if auto_extract and release_type == "Firmware":
                for asset in release_data["assets"]:
                    file_name = asset["name"]
                    if file_name.endswith(".zip"):
                        zip_path: str = os.path.join(release_dir, file_name)
                        if os.path.exists(zip_path):
                            extraction_needed: bool = check_extraction_needed(
                                zip_path, release_dir, extract_patterns, exclude_patterns_list
                            )
                            if extraction_needed:
                                _log_message(f"Extracting files from {zip_path}...")
                                extract_files(zip_path, release_dir, extract_patterns, exclude_patterns_list)

        set_permissions_on_sh_files(release_dir)

    if releases_to_download:
        latest_release_tag_val: str = releases_to_download[0]["tag_name"]
        if latest_release_tag_val != saved_release_tag:
            with open(latest_release_file, "w") as f:
                f.write(latest_release_tag_val)
            _log_message(f"Updated latest release tag to {latest_release_tag_val}")

    release_tags_to_keep: List[str] = [r["tag_name"] for r in releases_to_download]
    cleanup_old_versions(download_dir_path, release_tags_to_keep)

    if not actions_taken:
        _log_message(f"All {release_type} assets are up to date.")

    for release_data in releases_to_download:
        release_tag = release_data["tag_name"]
        if release_tag != saved_release_tag and release_tag not in downloaded_versions:
            new_versions_available.append(release_tag)

    return downloaded_versions, new_versions_available


def set_permissions_on_sh_files(directory: str) -> None:
    """
    Sets executable permissions on .sh files if they do not already have them.

    Args:
        directory (str): The directory to search for .sh files (recursively).
    """
    root: str
    files: List[str]
    for root, _dirs, files in os.walk(directory):
        file_in_dir: str
        for file_in_dir in files:
            if file_in_dir.endswith(".sh"):
                file_path: str = os.path.join(root, file_in_dir)
                if not os.access(file_path, os.X_OK):
                    os.chmod(file_path, 0o755)
                    _log_message(f"Set executable permissions for {file_in_dir}")


def check_extraction_needed(zip_path: str, extract_dir: str, patterns: List[str], exclude_patterns: List[str]) -> bool:
    """
    Checks if extraction is needed by comparing zip contents against already extracted files
    based on current extraction patterns.

    Args:
        zip_path (str): Path to the zip file.
        extract_dir (str): Directory where files would be extracted.
        patterns (List[str]): List of keywords to identify files that should be extracted.
        exclude_patterns (List[str]): List of keywords to identify files to exclude from consideration.

    Returns:
        bool: True if any files matching patterns are not already extracted, False otherwise.
    """
    files_to_extract: List[str] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            file_info: zipfile.ZipInfo
            for file_info in zip_ref.infolist():
                file_name: str = file_info.filename
                base_name: str = os.path.basename(file_name)
                if not base_name: continue
                if any(exclude in base_name for exclude in exclude_patterns): continue
                stripped_base_name: str = strip_version_numbers(base_name)
                pattern: str
                for pattern in patterns:
                    if pattern in stripped_base_name:
                        files_to_extract.append(base_name)
                        break
        base_name_to_check: str
        for base_name_to_check in files_to_extract:
            extracted_file_path: str = os.path.join(extract_dir, base_name_to_check)
            if not os.path.exists(extracted_file_path):
                return True
        return False
    except zipfile.BadZipFile:
        _log_message(f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file.")
        try:
            os.remove(zip_path)
            _log_message(f"Removed corrupted zip file: {zip_path}")
        except Exception as e:
            _log_message(f"Error removing corrupted zip file {zip_path}: {e}")
        return False
    except Exception as e:
        _log_message(f"Error checking extraction needed for {zip_path}: {e}")
        return False


def main() -> None:
    """
    Main function to orchestrate the Fetchtastic downloader process.
    """
    start_time: float = time.time()
    _log_message("Starting Fetchtastic...")

    config: Optional[Dict[str, Any]]
    current_version: Optional[str]
    latest_version: Optional[str]
    update_available: bool
    paths_and_urls: Optional[Dict[str, str]]

    config, current_version, latest_version, update_available, paths_and_urls = _initial_setup_and_config()

    if not config or not paths_and_urls: # Check if setup failed
        _log_message("Initial setup failed. Exiting.")
        return

    _check_wifi_connection(config)

    downloaded_firmwares: List[str]
    new_firmware_versions: List[str]
    downloaded_apks: List[str]
    new_apk_versions: List[str]

    downloaded_firmwares, new_firmware_versions = _process_firmware_downloads(config, paths_and_urls)
    downloaded_apks, new_apk_versions = _process_apk_downloads(config, paths_and_urls)

    _finalize_and_notify(start_time, config, downloaded_firmwares, downloaded_apks, new_firmware_versions, new_apk_versions, current_version, latest_version, update_available)


if __name__ == "__main__":
    main()
