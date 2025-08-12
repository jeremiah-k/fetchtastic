# src/fetchtastic/downloader.py

import fnmatch
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from fetchtastic import menu_repo, setup_config

# Removed log_info, setup_logging
from fetchtastic.log_utils import logger  # Import new logger
from fetchtastic.setup_config import display_version_info, get_upgrade_command
from fetchtastic.utils import download_file_with_retry

# Constants for downloader operations
RELEASE_SCAN_COUNT: int = 10
NTFY_REQUEST_TIMEOUT: int = 10  # seconds
# Constants for check_for_prereleases internal download
PRERELEASE_REQUEST_TIMEOUT: int = 30
PRERELEASE_CHUNK_SIZE: int = 8 * 1024


def compare_versions(version1, version2):
    """
    Compare two version strings and determine their ordering.
    
    Strips a leading 'v' from each input, then compares the major, minor, and patch components numerically.
    - If either version has fewer than three dot-separated components, a simple string comparison is used.
    - Non-numeric components in the first three segments fall back to lexicographic comparison.
    - Any additional segments (e.g., a commit hash in a fourth segment) are ignored for ordering.
    
    Parameters:
        version1 (str): First version string to compare.
        version2 (str): Second version string to compare.
    
    Returns:
        int: 1 if version1 > version2, 0 if equal, -1 if version1 < version2.
    """
    # Strip 'v' prefix if it exists
    v1 = version1.lstrip("v")
    v2 = version2.lstrip("v")

    # Handle exact matches immediately
    if v1 == v2:
        return 0

    # Split versions into components
    v1_parts = v1.split(".")
    v2_parts = v2.split(".")

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


def check_promoted_prereleases(
    download_dir, latest_release_tag
):  # log_message_func parameter removed
    """
    Checks if any pre-releases have been promoted to regular releases.
    If a pre-release matches the latest release, it verifies the files match
    and either moves them to the regular release directory or deletes them.

    Args:
        download_dir: Base download directory
        latest_release_tag: The latest official release tag (e.g., v2.6.8.ef9d0d7)
        # log_message_func parameter removed

    Returns:
        Boolean indicating if any pre-releases were promoted
    """
    # Removed local log_message_func definition

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
                logger.info(
                    f"Found pre-release {dir_name} that matches latest release {latest_release_tag}"
                )
                prerelease_path = os.path.join(prerelease_dir, dir_name)

                # If the release directory doesn't exist yet, we can't compare files
                # We'll just remove the pre-release directory since it will be downloaded as a regular release
                if not os.path.exists(release_dir):
                    logger.info(
                        f"Pre-release {dir_name} has been promoted to release {latest_release_tag}, "
                        f"but the release directory doesn't exist yet. Removing pre-release."
                    )
                    try:
                        shutil.rmtree(prerelease_path)
                        logger.info(f"Removed pre-release directory: {prerelease_path}")
                        promoted = True
                    except OSError as e:
                        logger.error(
                            f"Error removing pre-release directory {prerelease_path}: {e}"
                        )
                    continue

                # Verify files match by comparing hashes
                files_match = True
                try:
                    for file_name in os.listdir(prerelease_path):
                        prerelease_file = os.path.join(prerelease_path, file_name)
                        release_file = os.path.join(release_dir, file_name)

                        if os.path.exists(release_file):
                            # Compare file hashes
                            if not compare_file_hashes(prerelease_file, release_file):
                                files_match = False
                                logger.warning(
                                    f"File {file_name} in pre-release doesn't match the release version"
                                )
                                break
                except OSError as e:
                    logger.error(
                        f"Error listing files in {prerelease_path} for hash comparison: {e}"
                    )
                    files_match = (
                        False  # Assume files don't match if we can't check them
                    )

                if files_match:
                    logger.info(
                        f"Pre-release {dir_name} has been promoted to release {latest_release_tag}"
                    )
                    # Remove the pre-release directory since it's now a regular release
                    try:
                        shutil.rmtree(prerelease_path)
                        logger.info(f"Removed pre-release directory: {prerelease_path}")
                        promoted = True
                    except OSError as e:
                        logger.error(
                            f"Error removing promoted pre-release directory {prerelease_path}: {e}"
                        )

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

    def get_file_hash(file_path: str) -> Optional[str]:
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                # Read and update hash in chunks of 4K
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except IOError as e:
            logger.error(f"Error reading file {file_path} for hashing: {e}")
            return None

    hash1 = get_file_hash(file1)
    hash2 = get_file_hash(file2)

    return hash1 is not None and hash2 is not None and hash1 == hash2


def check_for_prereleases(
    download_dir,
    latest_release_tag,
    selected_patterns,
    exclude_patterns=None,  # log_message_func parameter removed
):
    """
    Checks for pre-release firmware in the meshtastic.github.io repository.
    Also cleans up stale pre-releases that no longer exist in the repository.

    Args:
        download_dir: Base download directory
        latest_release_tag: The latest official release tag (e.g., v2.6.8.ef9d0d7)
        selected_patterns: List of firmware patterns to download
        exclude_patterns: Optional list of filename patterns to exclude from downloading
        # log_message_func parameter removed

    Returns:
        Tuple of (boolean indicating if any pre-releases were found and downloaded,
                 list of pre-release versions that were downloaded)
    """
    # Removed local log_message_func definition

    # Initialize exclude patterns list
    exclude_patterns_list = exclude_patterns or []

    # Strip the 'v' prefix if present
    if latest_release_tag.startswith("v"):
        latest_release_version = latest_release_tag[1:]
    else:
        latest_release_version = latest_release_tag

    # Fetch directories from the meshtastic.github.io repository
    directories = menu_repo.fetch_repo_directories()

    if not directories:
        logger.info("No firmware directories found in the repository.")
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
                    logger.info(
                        f"Removing stale file from prerelease directory: {item}"
                    )
                    os.remove(item_path)
                except OSError as e:
                    logger.warning(
                        f"Error removing stale file {item_path} from prerelease directory: {e}"
                    )

        # Now clean up directories
        for dir_name in existing_prerelease_dirs:
            should_keep = False
            dir_path = os.path.join(
                prerelease_dir, dir_name
            )  # Define dir_path here for use in except block

            try:
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
                    logger.info(f"Removing stale pre-release directory: {dir_name}")
                    shutil.rmtree(dir_path)
            except OSError as e:
                logger.warning(
                    f"Error processing or removing directory {dir_path} during prerelease cleanup: {e}"
                )
            except (
                Exception
            ) as e_general:  # Catch other potential errors like from compare_versions
                logger.error(
                    f"Unexpected error processing directory {dir_path} for prerelease cleanup: {e_general}",
                    exc_info=True,
                )

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

                # Check if we need to download this pre-release
                # Either the directory doesn't exist, or it exists but is missing files
                should_process = False

                if dir_name not in existing_prerelease_dirs:
                    # Directory doesn't exist at all
                    should_process = True
                else:
                    # Directory exists, but check if all expected files are present
                    dir_path = os.path.join(prerelease_dir, dir_name)

                    # Fetch the list of files that should be in this directory
                    expected_files = menu_repo.fetch_directory_contents(dir_name)
                    if expected_files:
                        # Filter expected files based on patterns (same logic as download)
                        expected_matching_files = []
                        for file in expected_files:
                            file_name = file["name"]

                            # Apply same filtering logic as download
                            stripped_file_name = strip_version_numbers(file_name)
                            if not any(
                                pattern in stripped_file_name
                                for pattern in selected_patterns
                            ):
                                continue  # Skip this file

                            # Skip files that match exclude patterns
                            if any(
                                fnmatch.fnmatch(file_name, exclude)
                                for exclude in exclude_patterns_list
                            ):
                                continue  # Skip this file

                            expected_matching_files.append(file_name)

                        # Check if all expected files are present locally
                        missing_files = []
                        for expected_file in expected_matching_files:
                            local_file_path = os.path.join(dir_path, expected_file)
                            if not os.path.exists(local_file_path):
                                missing_files.append(expected_file)

                        if missing_files:
                            logger.debug(
                                f"Pre-release {dir_name} is missing {len(missing_files)} files, will re-download"
                            )
                            should_process = True
                        else:
                            logger.debug(
                                f"Pre-release {dir_name} is complete with all expected files"
                            )
                    else:
                        # Could not fetch expected files list, assume we need to process
                        logger.debug(
                            f"Could not fetch file list for {dir_name}, will attempt download"
                        )
                        should_process = True

                if should_process:
                    prerelease_dirs.append(dir_name)

    if not prerelease_dirs:
        return False, []

    # Create prerelease directory if it doesn't exist
    if not os.path.exists(prerelease_dir):
        try:
            os.makedirs(prerelease_dir)
        except OSError as e:
            logger.error(f"Error creating pre-release directory {prerelease_dir}: {e}")
            return False, []  # Cannot proceed if directory creation fails

    downloaded_files = []

    # Process each pre-release directory
    for dir_name in prerelease_dirs:
        logger.info(f"Found pre-release: {dir_name}")

        # Fetch files from the directory
        files = menu_repo.fetch_directory_contents(dir_name)

        if not files:
            logger.info(f"No files found in {dir_name}.")
            continue

        # Create directory for this pre-release
        dir_path = os.path.join(prerelease_dir, dir_name)
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path)
            except OSError as e:
                logger.error(
                    f"Error creating directory for pre-release {dir_name} at {dir_path}: {e}"
                )
                continue  # Skip this pre-release if its directory cannot be created

        # Filter files based on selected patterns
        for file in files:
            file_name = file["name"]
            download_url = file["download_url"]
            file_path = os.path.join(dir_path, file_name)

            # Only download files that match the selected patterns and don't match exclude patterns
            stripped_file_name = strip_version_numbers(file_name)
            if not any(pattern in stripped_file_name for pattern in selected_patterns):
                continue  # Skip this file

            # Skip files that match exclude patterns
            if any(
                fnmatch.fnmatch(file_name, exclude) for exclude in exclude_patterns_list
            ):
                continue  # Skip this file

            if not os.path.exists(file_path):
                try:
                    logger.debug(
                        f"Downloading pre-release file: {file_name} from {download_url}"
                    )
                    response = requests.get(
                        download_url, stream=True, timeout=PRERELEASE_REQUEST_TIMEOUT
                    )
                    response.raise_for_status()

                    with open(file_path, "wb") as f:
                        for chunk in response.iter_content(
                            chunk_size=PRERELEASE_CHUNK_SIZE
                        ):
                            if chunk:
                                f.write(chunk)

                    # Set executable permissions for .sh files
                    if file_name.endswith(".sh"):
                        try:
                            os.chmod(file_path, 0o755)
                            logger.debug(f"Set executable permissions for {file_name}")
                        except OSError as e:
                            logger.warning(
                                f"Error setting executable permissions for {file_name}: {e}"
                            )

                    logger.info(f"Downloaded: {file_name}")
                    downloaded_files.append(file_path)
                except requests.exceptions.RequestException as e:
                    logger.error(
                        f"Network error downloading pre-release file {file_name} from {download_url}: {e}"
                    )
                except IOError as e:
                    logger.error(
                        f"File I/O error while downloading pre-release file {file_name} to {file_path}: {e}"
                    )
                except Exception as e:  # Catch any other unexpected errors
                    logger.error(
                        f"Unexpected error downloading pre-release file {file_name}: {e}",
                        exc_info=True,
                    )

    downloaded_versions = []
    if downloaded_files:
        logger.info(
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

# _log_message function removed


def _send_ntfy_notification(
    ntfy_server: Optional[str],
    ntfy_topic: Optional[str],
    message: str,
    title: Optional[str] = None,
) -> None:
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
                ntfy_url,
                data=message.encode("utf-8"),
                headers=headers,
                timeout=NTFY_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            logger.debug(f"Notification sent to {ntfy_url}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error sending notification to {ntfy_url}: {e}")
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
    try:
        # Add progress feedback
        if "firmware" in url:
            logger.info("Fetching firmware releases from GitHub...")
        elif "Android" in url:
            logger.info("Fetching Android APK releases from GitHub...")
        else:
            logger.info("Fetching releases from GitHub...")

        response: requests.Response = requests.get(url, timeout=NTFY_REQUEST_TIMEOUT)
        response.raise_for_status()
        releases: List[Dict[str, Any]] = response.json()

        # Log how many releases were fetched
        logger.debug(f"Fetched {len(releases)} releases from GitHub API")

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch releases data from {url}: {e}")
        return []  # Return empty list on error
    except requests.exceptions.JSONDecodeError as e:  # Or ValueError for older requests
        logger.error(f"Failed to decode JSON response from {url}: {e}")
        return []

    # Sort releases by published date, descending order
    try:
        sorted_releases: List[Dict[str, Any]] = sorted(
            releases, key=lambda r: r["published_at"], reverse=True
        )
    except (
        TypeError,
        KeyError,
    ) as e:  # Handle cases where 'published_at' might be missing or not comparable
        logger.warning(
            f"Error sorting releases, 'published_at' key might be missing or invalid: {e}"
        )
        return (
            releases  # Return unsorted or partially sorted if error occurs during sort
        )

    # Limit the number of releases to be scanned
    return sorted_releases[
        :scan_count
    ]  # scan_count is a parameter, no constant needed here.


def _initial_setup_and_config() -> Tuple[
    Optional[Dict[str, Any]],
    Optional[str],
    Optional[str],
    bool,
    Optional[Dict[str, str]],
]:
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
        logger.error(
            "Configuration not found. Please run 'fetchtastic setup' first."
        )  # Changed to logger.error
        return None, current_version, latest_version, update_available, None

    download_dir: str = config.get(
        "DOWNLOAD_DIR",
        os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"),
    )
    # setup_logging(download_dir) # Removed call to old setup_logging

    logger.info(
        f"Fetchtastic v{current_version if current_version else 'unknown'}"
    )  # Changed to logger.info
    if update_available and latest_version:
        logger.info(
            f"A newer version (v{latest_version}) is available!"
        )  # Changed to logger.info
        upgrade_cmd: str = get_upgrade_command()
        logger.info(f"Run '{upgrade_cmd}' to upgrade.")  # Changed to logger.info

    firmware_dir: str = os.path.join(download_dir, "firmware")
    apks_dir: str = os.path.join(download_dir, "apks")
    dir_path_to_create: str
    for dir_path_to_create in [download_dir, firmware_dir, apks_dir]:
        if not os.path.exists(dir_path_to_create):
            try:
                os.makedirs(dir_path_to_create)
                logger.debug(
                    f"Created directory: {dir_path_to_create}"
                )  # Changed to logger.debug
            except OSError as e:
                logger.error(
                    f"Error creating directory {dir_path_to_create}: {e}"
                )  # Changed to logger.error
                # Depending on severity, might want to return None or raise error
                # For now, log and continue, some functionality might be impaired.

    paths_and_urls: Dict[str, str] = {
        "download_dir": download_dir,
        "firmware_dir": firmware_dir,
        "apks_dir": apks_dir,
        "latest_android_release_file": os.path.join(
            apks_dir, "latest_android_release.txt"
        ),
        "latest_firmware_release_file": os.path.join(
            firmware_dir, "latest_firmware_release.txt"
        ),
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
            logger.warning(
                "Not connected to Wi-Fi. Skipping all downloads."
            )  # Changed to logger.warning


def _process_firmware_downloads(
    config: Dict[str, Any], paths_and_urls: Dict[str, str]
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]:
    """
    Handles the firmware download process, including pre-releases.

    Args:
        config (Dict[str, Any]): The application configuration.
        paths_and_urls (Dict[str, str]): Dictionary of important paths and URLs.

    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]: A tuple containing:
            - List of downloaded firmware versions.
            - List of new firmware versions detected.
            - List of dictionaries with details of failed firmware downloads.
            - Latest firmware version (or None if no releases found).
    """
    global downloads_skipped
    downloaded_firmwares: List[str] = []
    new_firmware_versions: List[str] = []
    all_failed_firmware_downloads: List[Dict[str, str]] = []
    latest_firmware_version: Optional[str] = None

    if config.get("SAVE_FIRMWARE", False) and config.get(
        "SELECTED_FIRMWARE_ASSETS", []
    ):
        latest_firmware_releases: List[Dict[str, Any]] = _get_latest_releases_data(
            paths_and_urls["firmware_releases_url"],
            config.get(
                "FIRMWARE_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT
            ),  # Use RELEASE_SCAN_COUNT if versions_to_keep not in config
        )

        # Extract the actual latest firmware version
        if latest_firmware_releases:
            latest_firmware_version = latest_firmware_releases[0].get("tag_name")
        fw_downloaded: List[str]
        fw_new_versions: List[str]
        failed_fw_downloads_details: List[Dict[str, str]]  # Explicitly declare type
        fw_downloaded, fw_new_versions, failed_fw_downloads_details = (
            check_and_download(  # Corrected unpacking
                latest_firmware_releases,
                paths_and_urls["latest_firmware_release_file"],
                "Firmware",
                paths_and_urls["firmware_dir"],
                config.get("FIRMWARE_VERSIONS_TO_KEEP", 2),
                config.get("EXTRACT_PATTERNS", []),
                selected_patterns=config.get("SELECTED_FIRMWARE_ASSETS", []),  # type: ignore
                auto_extract=config.get("AUTO_EXTRACT", False),
                exclude_patterns=config.get("EXCLUDE_PATTERNS", []),  # type: ignore
            )
        )
        downloaded_firmwares.extend(fw_downloaded)
        new_firmware_versions.extend(fw_new_versions)
        all_failed_firmware_downloads.extend(
            failed_fw_downloads_details
        )  # Ensure this line is present
        if fw_downloaded:
            logger.info(f"Downloaded Firmware versions: {', '.join(fw_downloaded)}")

        latest_release_tag: Optional[str] = None
        if os.path.exists(paths_and_urls["latest_firmware_release_file"]):
            with open(paths_and_urls["latest_firmware_release_file"], "r") as f:
                latest_release_tag = f.read().strip()

        if latest_release_tag:
            promoted: bool = check_promoted_prereleases(
                paths_and_urls["download_dir"],
                latest_release_tag,  # logger.info removed
            )
            if promoted:
                logger.info(
                    "Detected pre-release(s) that have been promoted to regular release."
                )

        if config.get("CHECK_PRERELEASES", False) and not downloads_skipped:
            if latest_release_tag:
                logger.info("Checking for pre-release firmware...")
                prerelease_found: bool
                prerelease_versions: List[str]
                prerelease_found, prerelease_versions = (
                    check_for_prereleases(  # logger.info removed
                        paths_and_urls["download_dir"],
                        latest_release_tag,
                        config.get("EXTRACT_PATTERNS", []),  # type: ignore
                        exclude_patterns=config.get("EXCLUDE_PATTERNS", []),  # type: ignore
                    )
                )
                if prerelease_found:
                    logger.info(
                        f"Pre-release firmware downloaded successfully: {', '.join(prerelease_versions)}"
                    )
                    version: str
                    for version in prerelease_versions:
                        downloaded_firmwares.append(f"pre-release {version}")
                else:
                    logger.info("No new pre-release firmware found or downloaded.")
            else:
                logger.info("No latest release tag found. Skipping pre-release check.")
    elif not config.get("SELECTED_FIRMWARE_ASSETS", []):
        logger.info("No firmware assets selected. Skipping firmware download.")

    return (
        downloaded_firmwares,
        new_firmware_versions,
        all_failed_firmware_downloads,
        latest_firmware_version,
    )


def _process_apk_downloads(
    config: Dict[str, Any], paths_and_urls: Dict[str, str]
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]:
    """
    Handles the APK download process.

    Args:
        config (Dict[str, Any]): The application configuration.
        paths_and_urls (Dict[str, str]): Dictionary of important paths and URLs.

    Returns:
        Tuple[List[str], List[str], List[Dict[str,str]], Optional[str]]: A tuple containing:
            - List of downloaded APK versions.
            - List of new APK versions detected.
            - List of dictionaries with details of failed APK downloads.
            - Latest APK version (or None if no releases found).
    """
    global downloads_skipped
    downloaded_apks: List[str] = []
    new_apk_versions: List[str] = []
    all_failed_apk_downloads: List[Dict[str, str]] = (
        []
    )  # Initialize all_failed_apk_downloads
    latest_apk_version: Optional[str] = None

    if config.get("SAVE_APKS", False) and config.get("SELECTED_APK_ASSETS", []):
        latest_android_releases: List[Dict[str, Any]] = _get_latest_releases_data(
            paths_and_urls["android_releases_url"],
            config.get(
                "ANDROID_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT
            ),  # Use RELEASE_SCAN_COUNT if versions_to_keep not in config
        )

        # Extract the actual latest APK version
        if latest_android_releases:
            latest_apk_version = latest_android_releases[0].get("tag_name")
        apk_downloaded: List[str]
        apk_new_versions_list: List[str]
        failed_apk_downloads_details: List[Dict[str, str]]  # Declare for unpacking
        apk_downloaded, apk_new_versions_list, failed_apk_downloads_details = (
            check_and_download(  # Unpack 3 values
                latest_android_releases,
                paths_and_urls["latest_android_release_file"],
                "Android APK",
                paths_and_urls["apks_dir"],
                config.get("ANDROID_VERSIONS_TO_KEEP", 2),
                [],
                selected_patterns=config.get("SELECTED_APK_ASSETS", []),  # type: ignore
                auto_extract=False,
                exclude_patterns=[],
            )
        )
        downloaded_apks.extend(apk_downloaded)
        new_apk_versions.extend(apk_new_versions_list)
        all_failed_apk_downloads.extend(
            failed_apk_downloads_details
        )  # Extend with failed details
        if apk_downloaded:
            logger.info(f"Downloaded Android APK versions: {', '.join(apk_downloaded)}")
    elif not config.get("SELECTED_APK_ASSETS", []):
        logger.info("No APK assets selected. Skipping APK download.")

    return (
        downloaded_apks,
        new_apk_versions,
        all_failed_apk_downloads,
        latest_apk_version,
    )


def _finalize_and_notify(
    start_time: float,
    config: Dict[str, Any],
    downloaded_firmwares: List[str],
    downloaded_apks: List[str],
    new_firmware_versions: List[str],
    new_apk_versions: List[str],
    current_version: Optional[str],
    latest_version: Optional[str],
    update_available: bool,
    latest_firmware_version: Optional[str] = None,
    latest_apk_version: Optional[str] = None,
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

    # Create clean summary
    downloaded_count = len(downloaded_firmwares) + len(downloaded_apks)

    logger.info(f"\nCompleted in {total_time:.1f}s")
    if downloaded_count > 0:
        logger.info(f"Downloaded {downloaded_count} new files")

    # Show latest versions if available
    if latest_firmware_version:
        logger.info(f"Latest firmware: {latest_firmware_version}")
    if latest_apk_version:
        logger.info(f"Latest APK: {latest_apk_version}")

    if update_available and latest_version:
        upgrade_cmd: str = get_upgrade_command()
        logger.info("\nUpdate Available")
        logger.info(f"A newer version (v{latest_version}) of Fetchtastic is available!")
        logger.info(f"Run '{upgrade_cmd}' to upgrade.")

    ntfy_server: Optional[str] = config.get("NTFY_SERVER", "")
    ntfy_topic: Optional[str] = config.get("NTFY_TOPIC", "")
    notify_on_download_only: bool = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)

    notification_message: str
    message_lines: List[str]

    if downloads_skipped:
        message_lines = [
            "New releases are available but downloads were skipped because the device is not connected to Wi-Fi."
        ]
        if new_firmware_versions:
            message_lines.append(
                f"Firmware versions available: {', '.join(new_firmware_versions)}"
            )
        if new_apk_versions:
            message_lines.append(
                f"Android APK versions available: {', '.join(new_apk_versions)}"
            )
        notification_message = "\n".join(message_lines) + f"\n{datetime.now()}"
        logger.info("\n".join(message_lines))
        _send_ntfy_notification(
            ntfy_server,
            ntfy_topic,
            notification_message,
            title="Fetchtastic Downloads Skipped",
        )
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
        _send_ntfy_notification(
            ntfy_server,
            ntfy_topic,
            notification_message,
            title="Fetchtastic Download Completed",
        )
    else:
        message: str = f"All assets are up to date.\n{datetime.now()}"
        logger.info(message)
        if not notify_on_download_only:
            _send_ntfy_notification(
                ntfy_server, ntfy_topic, message, title="Fetchtastic Up to Date"
            )


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
            if not result:
                return False
            data: Dict[str, Any] = json.loads(result)
            supplicant_state: str = data.get("supplicant_state", "")  # type: ignore
            ip_address: str = data.get("ip", "")  # type: ignore
            return supplicant_state == "COMPLETED" and ip_address != ""
        except json.JSONDecodeError as e:
            logger.warning(f"Error decoding JSON from termux-wifi-connectioninfo: {e}")
            return False
        except OSError as e:  # For os.popen issues
            logger.warning(f"OSError checking Wi-Fi connection with os.popen: {e}")
            return False
        except Exception as e:  # Catch any other unexpected error
            logger.error(
                f"Unexpected error checking Wi-Fi connection: {e}", exc_info=True
            )
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

    if (
        not safe_path.startswith(abs_extract_dir + os.sep)
        and safe_path != abs_extract_dir
    ):
        if safe_path == abs_extract_dir and (file_path == "" or file_path == "."):
            pass
        else:
            raise ValueError(
                f"Unsafe path detected: '{file_path}' attempts to write outside of '{extract_dir}'"
            )
    return safe_path


def extract_files(
    zip_path: str, extract_dir: str, patterns: List[str], exclude_patterns: List[str]
) -> None:
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
                if not base_name:
                    continue
                if any(
                    fnmatch.fnmatch(base_name, exclude) for exclude in exclude_patterns
                ):
                    continue

                stripped_base_name: str = strip_version_numbers(base_name)
                pattern: str
                for pattern in patterns:
                    if pattern in stripped_base_name:
                        try:
                            target_path: str = safe_extract_path(extract_dir, base_name)
                            if not os.path.exists(target_path):
                                target_dir_for_file: str = os.path.dirname(target_path)
                                if not os.path.exists(target_dir_for_file):
                                    os.makedirs(
                                        target_dir_for_file, exist_ok=True
                                    )  # Can raise OSError
                                source: Any = zip_ref.open(
                                    file_info
                                )  # Can raise BadZipFile, LargeZipFile
                                with open(
                                    target_path, "wb"
                                ) as target_file:  # Can raise IOError
                                    target_file.write(source.read())
                                logger.info(f"  Extracted: {base_name}")
                            if base_name.endswith(".sh"):
                                if not os.access(target_path, os.X_OK):
                                    os.chmod(target_path, 0o755)  # Can raise OSError
                                    logger.debug(
                                        f"Set executable permissions for {base_name}"
                                    )
                            break
                        except ValueError as e_val:  # From safe_extract_path
                            logger.warning(
                                f"Skipping extraction of '{base_name}' due to unsafe path: {e_val}"
                            )
                        except (IOError, OSError) as e_io_os:
                            logger.warning(
                                f"File/OS error during extraction of '{base_name}': {e_io_os}"
                            )
                        except (
                            zipfile.BadZipFile
                        ) as e_bzf_inner:  # Should ideally be caught by outer, but just in case
                            logger.warning(
                                f"Bad zip file encountered while processing member '{base_name}' of '{zip_path}': {e_bzf_inner}"
                            )
                        except (
                            Exception
                        ) as e_inner_extract:  # Catch other unexpected errors for this specific file
                            logger.error(
                                f"Unexpected error extracting file '{base_name}' from '{zip_path}': {e_inner_extract}",
                                exc_info=True,
                            )
                        continue  # Continue to next pattern or file in zip
    except zipfile.BadZipFile:
        logger.error(
            f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file."
        )
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except (IOError, OSError) as e_rm:
            logger.error(f"Error removing corrupted zip file {zip_path}: {e_rm}")
    except (IOError, OSError) as e_io_main:
        logger.error(f"IO/OS error opening or reading zip file {zip_path}: {e_io_main}")
    except (
        Exception
    ) as e_outer_extract:  # Catch other unexpected errors during the overall extraction process
        logger.error(
            f"An unexpected error occurred while processing zip file {zip_path}: {e_outer_extract}",
            exc_info=True,
        )


def strip_version_numbers(filename: str) -> str:
    """
    Strip embedded version numbers and optional commit-hash segments from a filename.
    
    Removes common version patterns (for example: "v1.2.3", "_v1.2.3", "-1.2.3" and variants with a fourth dot-separated commit/hash like ".1a2b3c4") including an optional leading '-' or '_' separator. Returns the filename with those version portions removed while leaving other parts of the name intact.
    
    Args:
        filename (str): The input filename.
    
    Returns:
        str: The filename with version and short commit-hash segments removed.
    """
    # This regex removes separators and version numbers like -2.3.2.1a2b3c4 or _v1.2.3
    base_name: str = re.sub(r"[-_]v?\d+\.\d+\.\d+(?:\.[\da-f]+)?", "", filename)
    return base_name


def cleanup_old_versions(directory: str, releases_to_keep: List[str]) -> None:
    """
    Removes old version directories, keeping only specified releases.

    Args:
        directory (str): The directory containing versioned subdirectories.
        releases_to_keep (List[str]): A list of release tag names to keep.
    """
    excluded_dirs: List[str] = ["repo-dls", "prerelease"]
    versions: List[str] = [
        d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))
    ]
    version: str
    for version in versions:
        if version in excluded_dirs:
            continue
        if version not in releases_to_keep:
            version_path: str = os.path.join(directory, version)
            try:
                # Using shutil.rmtree for robustness, but logging individual files first for more detail if preferred.
                # For simplicity here, just rmtree. If detailed logging of each file/dir removal is needed,
                # the original os.walk approach with individual os.remove/os.rmdir is fine, wrapped in try-except.
                shutil.rmtree(version_path)
                logger.info(f"Removed directory and its contents: {version_path}")
            except OSError as e:
                logger.warning(
                    f"Error removing old version directory {version_path}: {e}"
                )


def strip_unwanted_chars(text: str) -> str:
    """
    Removes non-ASCII characters, including emojis, from a string.

    Args:
        text: The input string to clean.

    Returns:
        The input string with all non-ASCII characters removed.
    """
    printable_regex = re.compile(r"[^\x00-\x7F]+")
    return printable_regex.sub("", text)


def _is_release_complete(
    release_data: Dict[str, Any],
    release_dir: str,
    selected_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> bool:
    """
    Return True if the given release directory contains all expected assets (filtered by patterns)
    and those assets pass basic integrity checks; otherwise False.
    
    Detailed behavior:
    - Builds the list of expected asset filenames from release_data["assets"], keeping only assets
      whose stripped names match any string in selected_patterns (if provided) and that do not
      match any fnmatch pattern in exclude_patterns.
    - For each expected asset:
      - Verifies the file exists in release_dir.
      - If the file is a ZIP, runs a ZIP integrity check (testzip) and compares the actual file size
        to the asset's declared size (when available).
      - For non-ZIP files, compares actual file size to the asset's declared size (when available).
    - Any missing file, ZIP corruption, size mismatch, or I/O error causes the function to return False.
    
    Parameters:
        release_data: Release metadata (dict) containing an "assets" list with entries that include
            "name" and optionally "size". Only used to determine expected filenames and expected sizes.
        release_dir: Path to the local release directory to inspect.
        selected_patterns: Optional list of substrings; an asset is considered only if its
            version-stripped filename contains any of these substrings. If None, no inclusion filtering is applied.
        exclude_patterns: List of fnmatch-style patterns; any asset whose original filename matches
            one of these patterns will be ignored.
    
    Returns:
        bool: True if all expected assets are present and pass integrity/size checks; False otherwise.
    """
    if not os.path.exists(release_dir):
        return False

    # Get list of expected assets based on patterns
    expected_assets = []
    for asset in release_data.get("assets", []):
        file_name = asset.get("name", "")
        if not file_name:
            continue

        # Apply same filtering logic as download
        stripped_file_name = strip_version_numbers(file_name)
        if selected_patterns and not any(
            pattern in stripped_file_name for pattern in selected_patterns
        ):
            continue

        # Skip files that match exclude patterns
        if any(fnmatch.fnmatch(file_name, exclude) for exclude in exclude_patterns):
            continue

        expected_assets.append(file_name)

    # If no assets match the patterns, return False (not complete)
    if not expected_assets:
        logger.debug(f"No assets match selected patterns for release in {release_dir}")
        return False

    # Check if all expected assets exist in the release directory
    for asset_name in expected_assets:
        asset_path = os.path.join(release_dir, asset_name)
        if not os.path.exists(asset_path):
            logger.debug(
                f"Missing asset {asset_name} in release directory {release_dir}"
            )
            return False

        # For zip files, verify they're not corrupted
        if asset_name.endswith(".zip"):
            try:
                with zipfile.ZipFile(asset_path, "r") as zf:
                    if zf.testzip() is not None:
                        logger.debug(f"Corrupted zip file detected: {asset_path}")
                        return False
                # Also check file size for zip files
                try:
                    actual_size = os.path.getsize(asset_path)
                    asset_data = next(
                        (
                            a
                            for a in release_data.get("assets", [])
                            if a.get("name") == asset_name
                        ),
                        None,
                    )
                    if asset_data:
                        expected_size = asset_data.get("size")
                        if expected_size is not None and actual_size != expected_size:
                            logger.debug(
                                f"File size mismatch for {asset_path}: expected {expected_size}, got {actual_size}"
                            )
                            return False
                except (OSError, TypeError):
                    logger.debug(f"Error checking file size for {asset_path}")
                    return False
            except zipfile.BadZipFile:
                logger.debug(f"Bad zip file detected: {asset_path}")
                return False
            except (IOError, OSError):
                logger.debug(f"Error checking zip file: {asset_path}")
                return False
        else:
            # For non-zip files, verify file size matches expected size from GitHub
            try:
                actual_size = os.path.getsize(asset_path)
                # Find the corresponding asset in release_data to get expected size
                for asset in release_data.get("assets", []):
                    if asset.get("name") == asset_name:
                        expected_size = asset.get("size")
                        if expected_size is not None and actual_size != expected_size:
                            logger.debug(
                                f"File size mismatch for {asset_path}: expected {expected_size}, got {actual_size}"
                            )
                            return False
                        break
            except (OSError, TypeError):
                logger.debug(f"Error checking file size for {asset_path}")
                return False

    return True


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
) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    """
    Check for missing or incomplete releases, download required assets, optionally extract them, and clean up old versions.
    
    For the newest `versions_to_keep` releases, verifies completeness, downloads any missing assets (respecting `selected_patterns` and `exclude_patterns`), saves release notes, sets executable permission on `.sh` files, and removes older release directories. If `auto_extract` is True and `release_type` is "Firmware", zip assets matching `extract_patterns` will be extracted into the release directory. Returns lists of successfully downloaded release tags, new release tags that are available but not downloaded, and details of failed downloads.
     
    Parameters:
        versions_to_keep (int): Number of most recent releases to consider for download/retention.
        extract_patterns (List[str]): Patterns used to select files to extract from zip archives.
        selected_patterns (Optional[List[str]]): If provided, only assets whose stripped filename contains any of these patterns are downloaded.
        auto_extract (bool): If True and `release_type` == "Firmware", zip assets will be checked and extracted when needed.
        exclude_patterns (Optional[List[str]]): Patterns to exclude from downloads and extraction.
    
    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]]]: 
            - downloaded_versions: tags of releases where at least one asset was successfully downloaded.
            - new_versions_available: tags of newer releases detected but not downloaded.
            - failed_downloads_details: list of failure records with keys like `url`, `path_to_download`, `release_tag`, `file_name`, `reason`, and `type`.
    """
    global downloads_skipped
    downloaded_versions: List[str] = []
    new_versions_available: List[str] = []
    failed_downloads_details: List[Dict[str, str]] = []
    actions_taken: bool = False
    exclude_patterns_list: List[str] = exclude_patterns or []

    if not os.path.exists(download_dir_path):
        os.makedirs(download_dir_path)

    saved_release_tag: Optional[str] = None
    if os.path.exists(latest_release_file):
        try:
            with open(latest_release_file, "r") as f:
                saved_release_tag = f.read().strip()
        except IOError as e:
            logger.warning(
                f"Error reading latest release file {latest_release_file}: {e}"
            )
            # Potentially critical, could lead to re-downloading, but proceed for now.

    releases_to_download: List[Dict[str, Any]] = releases[:versions_to_keep]

    if downloads_skipped:
        release_data: Dict[str, Any]
        for release_data in releases_to_download:
            if release_data["tag_name"] != saved_release_tag:
                new_versions_available.append(release_data["tag_name"])
        return (
            downloaded_versions,
            new_versions_available,
            failed_downloads_details,
        )  # Added failed_downloads_details

    release_data: Dict[str, Any]
    for release_data in releases_to_download:
        try:
            release_tag: str = release_data[
                "tag_name"
            ]  # Potential KeyError if API response changes
            release_dir: str = os.path.join(download_dir_path, release_tag)
            release_notes_file: str = os.path.join(
                release_dir, f"release_notes-{release_tag}.md"
            )

            # Check if this release has already been downloaded and is complete
            if _is_release_complete(
                release_data, release_dir, selected_patterns, exclude_patterns_list
            ):
                logger.debug(
                    f"Release {release_tag} already exists and is complete, skipping download"
                )
                # Update latest_release_file if this is the most recent release
                if (
                    release_tag != saved_release_tag
                    and release_data == releases_to_download[0]
                ):
                    try:
                        with open(latest_release_file, "w") as f:
                            f.write(release_tag)
                        logger.debug(
                            f"Updated latest release tag to {release_tag} (complete release)"
                        )
                    except IOError as e:
                        logger.warning(f"Error updating latest release file: {e}")
                # Still add to new_versions_available if it's different from saved tag
                elif release_tag != saved_release_tag:
                    new_versions_available.append(release_tag)
                continue

            if not os.path.exists(release_dir):
                try:
                    os.makedirs(release_dir, exist_ok=True)
                except OSError as e:
                    logger.error(
                        f"Error creating release directory {release_dir}: {e}. Skipping version {release_tag}."
                    )
                    continue  # Skip this release if its directory cannot be created

            if not os.path.exists(release_notes_file) and release_data.get("body"):
                logger.debug(f"Downloading release notes for version {release_tag}.")
                release_notes_content: str = strip_unwanted_chars(release_data["body"])
                try:
                    with open(release_notes_file, "w", encoding="utf-8") as notes_file:
                        notes_file.write(release_notes_content)
                    logger.debug(f"Saved release notes to {release_notes_file}")
                except IOError as e:
                    logger.warning(
                        f"Error writing release notes to {release_notes_file}: {e}"
                    )

            asset: Dict[str, Any]
            for asset in release_data.get(
                "assets", []
            ):  # Use .get for assets for safety
                file_name: str = asset.get("name", "")  # Use .get for name
                if not file_name:
                    logger.warning(
                        f"Asset found with no name for release {release_tag}. Skipping."
                    )
                    continue

                if file_name.endswith(".zip"):
                    asset_download_path: str = os.path.join(release_dir, file_name)
                    if os.path.exists(asset_download_path):
                        try:
                            with zipfile.ZipFile(asset_download_path, "r") as zf:
                                if zf.testzip() is not None:  # Check integrity
                                    raise zipfile.BadZipFile(
                                        "Corrupted zip file detected during pre-check."
                                    )
                        except zipfile.BadZipFile:
                            logger.warning(
                                f"Removing corrupted zip file: {asset_download_path}"
                            )
                            try:
                                os.remove(asset_download_path)
                            except OSError as e_rm:
                                logger.error(
                                    f"Error removing corrupted zip {asset_download_path}: {e_rm}"
                                )
                        except (
                            IOError,
                            OSError,
                        ) as e_check:  # For issues opening/reading the zip during check
                            logger.warning(
                                f"Error checking existing zip file {asset_download_path}: {e_check}. Attempting re-download."
                            )
                            try:
                                os.remove(asset_download_path)
                            except OSError as e_rm:
                                logger.error(
                                    f"Error removing zip {asset_download_path} before re-download: {e_rm}"
                                )

            assets_to_download: List[Tuple[str, str]] = []
            for asset in release_data.get("assets", []):
                file_name = asset.get("name", "")
                if not file_name:
                    continue  # Already logged
                browser_download_url = asset.get("browser_download_url")
                if not browser_download_url:
                    logger.warning(
                        f"Asset '{file_name}' in release '{release_tag}' has no download URL. Skipping."
                    )
                    failed_downloads_details.append(
                        {
                            "url": "Unknown - No download URL",
                            "path_to_download": os.path.join(release_dir, file_name),
                            "release_tag": release_tag,
                            "file_name": file_name,
                            "reason": "Missing browser_download_url",
                            "type": release_type,  # Added type
                        }
                    )
                    continue

                stripped_file_name: str = strip_version_numbers(file_name)
                if selected_patterns and not any(
                    pattern in stripped_file_name for pattern in selected_patterns
                ):
                    continue
                asset_download_path = os.path.join(release_dir, file_name)
                if not os.path.exists(asset_download_path):
                    assets_to_download.append(
                        (browser_download_url, asset_download_path)
                    )
        except (KeyError, TypeError) as e_data:
            logger.error(
                f"Error processing release data structure for a release (possibly malformed API response or unexpected structure): {e_data}. Skipping this release."
            )
            continue  # Skip to the next release if current one is malformed

        if assets_to_download:  # This check is correct based on the first loop.
            actions_taken = True
            logger.info(f"Processing release: {release_tag}")
            any_downloaded: bool = False
            url: str
            # The assets_to_download list contains (url, path_to_download) tuples.
            # We iterate through this list to attempt downloads.

            for (
                url,
                asset_dl_path,
            ) in assets_to_download:  # asset_dl_path is the full path for download
                # Try to find the original asset to get file_name for logging more accurately
                asset_file_name_for_log = os.path.basename(
                    asset_dl_path
                )  # Fallback to basename of path
                for asset_dict_for_name_lookup in release_data.get("assets", []):
                    if asset_dict_for_name_lookup.get("browser_download_url") == url:
                        asset_file_name_for_log = asset_dict_for_name_lookup.get(
                            "name", asset_file_name_for_log
                        )
                        break

                if download_file_with_retry(url, asset_dl_path):
                    any_downloaded = True
                else:
                    # download_file_with_retry failed
                    failed_downloads_details.append(
                        {
                            "url": url,
                            "path_to_download": asset_dl_path,
                            "release_tag": release_tag,
                            "file_name": asset_file_name_for_log,
                            "reason": "download_file_with_retry returned False",
                            "type": release_type,  # Added type
                        }
                    )

            if any_downloaded and release_tag not in downloaded_versions:
                # Add to downloaded_versions only if at least one asset from this release was successfully downloaded
                downloaded_versions.append(release_tag)

            if auto_extract and release_type == "Firmware":
                for asset_data in release_data.get(
                    "assets", []
                ):  # Iterate over asset_data from release_data
                    file_name = asset_data.get("name", "")  # Use .get for safety
                    if not file_name:
                        continue

                    if file_name.endswith(".zip"):
                        zip_path: str = os.path.join(release_dir, file_name)
                        if os.path.exists(zip_path):
                            extraction_needed: bool = check_extraction_needed(
                                zip_path,
                                release_dir,
                                extract_patterns,
                                exclude_patterns_list,
                            )
                            if extraction_needed:
                                logger.info(f"Extracting: {os.path.basename(zip_path)}")
                                extract_files(
                                    zip_path,
                                    release_dir,
                                    extract_patterns,
                                    exclude_patterns_list,
                                )

        set_permissions_on_sh_files(release_dir)

    # Only update the latest release file if we actually downloaded something
    if releases_to_download and downloaded_versions:
        try:
            latest_release_tag_val: str = releases_to_download[0]["tag_name"]
            if latest_release_tag_val != saved_release_tag:
                try:
                    with open(latest_release_file, "w") as f:
                        f.write(latest_release_tag_val)
                    logger.debug(
                        f"Updated latest release tag to {latest_release_tag_val}"
                    )
                except IOError as e:
                    logger.warning(
                        f"Error writing latest release tag to {latest_release_file}: {e}"
                    )
        except (
            IndexError,
            KeyError,
            TypeError,
        ) as e:  # If releases_to_download is empty or structure is wrong
            logger.warning(
                f"Could not determine latest release tag to save due to data issue: {e}"
            )

    # Run cleanup after all downloads are complete, but only if actions were taken
    if actions_taken:
        try:
            release_tags_to_keep: List[str] = [
                r["tag_name"] for r in releases_to_download
            ]
            cleanup_old_versions(download_dir_path, release_tags_to_keep)
        except (KeyError, TypeError) as e:
            logger.warning(
                f"Error preparing list of tags to keep for cleanup: {e}. Cleanup might be skipped or incomplete."
            )

    if not actions_taken:
        logger.info(f"All {release_type} assets are up to date.")

    for release_data in releases_to_download:
        release_tag = release_data["tag_name"]
        if release_tag != saved_release_tag and release_tag not in downloaded_versions:
            # This logic for new_versions_available might need refinement if a release has partial success
            # For now, if it wasn't fully downloaded (not in downloaded_versions), it's "new" or "still pending".
            new_versions_available.append(release_tag)

    return downloaded_versions, new_versions_available, failed_downloads_details


def set_permissions_on_sh_files(directory: str) -> None:
    """
    Sets executable permissions on .sh files if they do not already have them.

    Args:
        directory (str): The directory to search for .sh files (recursively).
    """
    root: str
    files: List[str]
    try:
        for root, _dirs, files in os.walk(directory):
            file_in_dir: str
            for file_in_dir in files:
                if file_in_dir.endswith(".sh"):
                    file_path: str = os.path.join(root, file_in_dir)
                    try:
                        if not os.access(file_path, os.X_OK):
                            os.chmod(file_path, 0o755)
                            logger.debug(
                                f"Set executable permissions for {file_in_dir}"
                            )
                    except OSError as e:
                        logger.warning(f"Error setting permissions on {file_path}: {e}")
    except OSError as e_walk:  # os.walk itself can fail
        logger.warning(
            f"Error walking directory {directory} to set permissions: {e_walk}"
        )


def check_extraction_needed(
    zip_path: str, extract_dir: str, patterns: List[str], exclude_patterns: List[str]
) -> bool:
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
                if not base_name:
                    continue
                if any(
                    fnmatch.fnmatch(base_name, exclude) for exclude in exclude_patterns
                ):
                    continue
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
        logger.error(
            f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file."
        )
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except (IOError, OSError) as e_rm:
            logger.error(
                f"Error removing corrupted zip file {zip_path} (in check_extraction_needed): {e_rm}"
            )
        return False  # Indicate extraction is needed as we couldn't verify or had to remove
    except (IOError, OSError) as e_io_check:  # For other IO errors with the zip file
        logger.warning(
            f"IO/OS error checking extraction needed for {zip_path}: {e_io_check}"
        )
        return True  # Assume extraction is needed if we can't check
    except Exception as e_unexp_check:  # Catch-all for other unexpected errors
        logger.error(
            f"Unexpected error checking extraction needed for {zip_path}: {e_unexp_check}",
            exc_info=True,
        )
        return True  # Default to needing extraction on unknown error


def main() -> None:
    """
    Main function to orchestrate the Fetchtastic downloader process.
    """
    start_time: float = time.time()
    logger.info("Starting Fetchtastic...")  # Changed to logger.info

    config: Optional[Dict[str, Any]]
    current_version: Optional[str]
    latest_version: Optional[str]
    update_available: bool
    paths_and_urls: Optional[Dict[str, str]]

    config, current_version, latest_version, update_available, paths_and_urls = (
        _initial_setup_and_config()
    )

    if not config or not paths_and_urls:  # Check if setup failed
        logger.error("Initial setup failed. Exiting.")  # Changed to logger.error
        return

    _check_wifi_connection(config)

    downloaded_firmwares: List[str]
    new_firmware_versions: List[str]
    failed_firmware_list: List[Dict[str, str]]
    latest_firmware_version: Optional[str]
    downloaded_apks: List[str]
    new_apk_versions: List[str]
    failed_apk_list: List[Dict[str, str]]
    latest_apk_version: Optional[str]

    (
        downloaded_firmwares,
        new_firmware_versions,
        failed_firmware_list,
        latest_firmware_version,
    ) = _process_firmware_downloads(config, paths_and_urls)
    downloaded_apks, new_apk_versions, failed_apk_list, latest_apk_version = (
        _process_apk_downloads(config, paths_and_urls)
    )

    if failed_firmware_list:
        logger.debug(f"Collected failed firmware downloads: {failed_firmware_list}")
    if failed_apk_list:
        logger.debug(f"Collected failed APK downloads: {failed_apk_list}")

    all_failed_downloads = failed_firmware_list + failed_apk_list

    if all_failed_downloads:
        logger.info(f"Retrying {len(all_failed_downloads)} failed downloads...")
        for failure_detail in all_failed_downloads:
            logger.info(
                f"Retrying download of {failure_detail['file_name']} for release {failure_detail['release_tag']} from {failure_detail['url']}"
            )
            if download_file_with_retry(
                failure_detail["url"], failure_detail["path_to_download"]
            ):
                logger.info(
                    f"Successfully retried download of {failure_detail['file_name']} for release {failure_detail['release_tag']}"
                )
                # Update tracking lists
                if failure_detail["type"] == "Firmware":
                    if failure_detail["release_tag"] not in downloaded_firmwares:
                        downloaded_firmwares.append(failure_detail["release_tag"])
                elif failure_detail["type"] == "Android APK":
                    if failure_detail["release_tag"] not in downloaded_apks:
                        downloaded_apks.append(failure_detail["release_tag"])
            else:
                logger.error(
                    f"Retry failed for {failure_detail['file_name']} for release {failure_detail['release_tag']}"
                )

    _finalize_and_notify(
        start_time,
        config,
        downloaded_firmwares,
        downloaded_apks,
        new_firmware_versions,
        new_apk_versions,
        current_version,
        latest_version,
        update_available,
        latest_firmware_version,
        latest_apk_version,
    )


if __name__ == "__main__":
    main()
