# src/fetchtastic/downloader.py

import json
import os
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
from fetchtastic.setup_config import display_version_info


def compare_versions(version1, version2):
    """
    Compares two version strings (e.g., 2.6.9.f93d031 vs 2.6.8.ef9d0d7).

    Returns:
        1 if version1 > version2
        0 if version1 == version2
        -1 if version1 < version2
    """
    # Split versions into components
    v1_parts = version1.split(".")
    v2_parts = version2.split(".")

    # Compare major, minor, patch versions
    for i in range(min(len(v1_parts), len(v2_parts))):
        if i < 3:  # Major, minor, patch are numeric
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
        else:
            # For commit hash, just do string comparison
            if v1_parts[i] > v2_parts[i]:
                return 1
            elif v1_parts[i] < v2_parts[i]:
                return -1

    # If we get here and versions have different lengths, the longer one is newer
    if len(v1_parts) > len(v2_parts):
        return 1
    elif len(v1_parts) < len(v2_parts):
        return -1

    # Versions are equal
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
    if not os.path.exists(release_dir):
        return False

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

        def log_message_func(message):
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

    # Find directories that are newer than the latest release
    prerelease_dirs = []
    for dir_name in directories:
        # Extract version from directory name (e.g., firmware-2.6.9.f93d031)
        if dir_name.startswith("firmware-"):
            dir_version = dir_name[9:]  # Remove 'firmware-' prefix

            # Compare versions (assuming format x.y.z.commit)
            if compare_versions(dir_version, latest_release_version) > 0:
                prerelease_dirs.append(dir_name)

    if not prerelease_dirs:
        return False, []

    # Create prerelease directory if it doesn't exist
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
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
        log_message_func("No new pre-release files were downloaded.")
        return False, []


# Use the version check function from setup_config


def main():
    # Get version information
    current_version, latest_version, update_available = display_version_info()

    # Load configuration
    config = setup_config.load_config()
    if not config:
        print("Configuration not found. Please run 'fetchtastic setup' first.")
        return

    # Get download directory from config
    download_dir = config.get(
        "DOWNLOAD_DIR",
        os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"),
    )

    # Set up logging
    setup_logging(download_dir)

    # Log version information at startup
    log_info(f"Fetchtastic v{current_version}")
    if update_available and latest_version:
        log_info(f"A newer version (v{latest_version}) is available!")
        log_info("Run 'pipx upgrade fetchtastic' to upgrade.")

    # Configuration file location is already displayed in cli.py

    # Get configuration values
    save_apks = config.get("SAVE_APKS", False)
    save_firmware = config.get("SAVE_FIRMWARE", False)
    ntfy_server = config.get("NTFY_SERVER", "")
    ntfy_topic = config.get("NTFY_TOPIC", "")
    android_versions_to_keep = config.get("ANDROID_VERSIONS_TO_KEEP", 2)
    firmware_versions_to_keep = config.get("FIRMWARE_VERSIONS_TO_KEEP", 2)
    auto_extract = config.get("AUTO_EXTRACT", False)
    extract_patterns = config.get("EXTRACT_PATTERNS", [])
    exclude_patterns = config.get("EXCLUDE_PATTERNS", [])
    wifi_only = config.get("WIFI_ONLY", False) if setup_config.is_termux() else False
    notify_on_download_only = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)
    check_prereleases = config.get("CHECK_PRERELEASES", False)

    selected_apk_patterns = config.get("SELECTED_APK_ASSETS", [])
    selected_firmware_patterns = config.get("SELECTED_FIRMWARE_ASSETS", [])

    download_dir = config.get(
        "DOWNLOAD_DIR",
        os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"),
    )
    firmware_dir = os.path.join(download_dir, "firmware")
    apks_dir = os.path.join(download_dir, "apks")
    latest_android_release_file = os.path.join(apks_dir, "latest_android_release.txt")
    latest_firmware_release_file = os.path.join(
        firmware_dir, "latest_firmware_release.txt"
    )

    # Create necessary directories
    for dir_path in [download_dir, firmware_dir, apks_dir]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

    def log_message(message):
        """Legacy log_message function that now uses the new logging system"""
        log_info(message)

    def send_ntfy_notification(message, title=None):
        if ntfy_server and ntfy_topic:
            try:
                ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
                headers = {
                    "Content-Type": "text/plain; charset=utf-8",
                }
                if title:
                    headers["Title"] = title
                response = requests.post(
                    ntfy_url, data=message.encode("utf-8"), headers=headers, timeout=10
                )
                response.raise_for_status()
                log_message(f"Notification sent to {ntfy_url}")
            except requests.exceptions.RequestException as e:
                log_message(f"Error sending notification to {ntfy_url}: {e}")
        else:
            # Don't log when notifications are not configured
            pass

    # Function to get the latest releases and sort by date
    def get_latest_releases(url, scan_count=10):
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        releases = response.json()
        # Sort releases by published date, descending order
        sorted_releases = sorted(
            releases, key=lambda r: r["published_at"], reverse=True
        )
        # Limit the number of releases to be scanned
        return sorted_releases[:scan_count]

    # Function to download a file with retry mechanism
    def download_file(url, download_path):
        """
        Downloads a file with retry mechanism.

        Args:
            url: URL to download from
            download_path: Path to save the file to

        Returns:
            bool: True if the file was downloaded or already exists and is valid,
                  False if the download failed
        """
        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Remove file if it exists but is corrupted (e.g., from a previous interrupted download)
        if os.path.exists(download_path):
            if download_path.endswith(".zip"):
                try:
                    # Try to open the zip file to verify it's valid
                    with zipfile.ZipFile(download_path, "r"):
                        # If we get here, the zip file is valid
                        return (
                            True  # File exists and is valid, no need to download again
                        )
                except zipfile.BadZipFile:
                    # File is corrupted, remove it
                    log_message(f"Removing corrupted zip file: {download_path}")
                    os.remove(download_path)
                    # Continue with download
                except Exception as e:
                    # Some other error occurred, remove the file to be safe
                    log_message(
                        f"Error checking zip file {download_path}: {e}. Removing file."
                    )
                    os.remove(download_path)
                    # Continue with download
            else:
                # For non-zip files, just check if the file size is > 0
                if os.path.getsize(download_path) > 0:
                    return (
                        True  # File exists and has content, no need to download again
                    )
                else:
                    # Empty file, remove it
                    log_message(f"Removing empty file: {download_path}")
                    os.remove(download_path)
                    # Continue with download

        # Download the file
        temp_path = download_path + ".tmp"
        try:
            log_message(f"Downloading {url}")
            response = session.get(url, stream=True)
            response.raise_for_status()

            # Use a temporary file for downloading
            with open(temp_path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)

            # If it's a zip file, verify it's valid before moving
            if download_path.endswith(".zip"):
                try:
                    with zipfile.ZipFile(temp_path, "r"):
                        # Zip file is valid, move it to the final location
                        os.replace(temp_path, download_path)
                        log_message(f"Downloaded {download_path}")
                        return True  # Successfully downloaded
                except zipfile.BadZipFile:
                    # Zip file is corrupted
                    os.remove(temp_path)
                    log_message(f"Error: Downloaded zip file is corrupted: {url}")
                    return False  # Failed to download
            else:
                # For non-zip files, just move the temp file to the final location
                os.replace(temp_path, download_path)
                log_message(f"Downloaded {download_path}")
                return True  # Successfully downloaded
        except requests.exceptions.RequestException as e:
            log_message(f"Error downloading {url}: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False  # Failed to download
        except Exception as e:
            log_message(f"Error processing download {url}: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False  # Failed to download

    def is_connected_to_wifi():
        if setup_config.is_termux():
            # Termux-specific Wi-Fi check
            try:
                result = os.popen("termux-wifi-connectioninfo").read()
                if not result:
                    # If result is empty, assume not connected
                    return False
                data = json.loads(result)
                supplicant_state = data.get("supplicant_state", "")
                ip_address = data.get("ip", "")
                if supplicant_state == "COMPLETED" and ip_address != "":
                    return True
                else:
                    return False
            except Exception as e:
                log_message(f"Error checking Wi-Fi connection: {e}")
                return False
        else:
            # For non-Termux environments, assume connected
            return True

    # Function to extract files from zip archives
    def extract_files(zip_path, extract_dir, patterns, exclude_patterns):
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                matched_files = []
                for file_info in zip_ref.infolist():
                    file_name = file_info.filename
                    base_name = os.path.basename(file_name)
                    # Skip directories
                    if not base_name:
                        continue
                    # Check if file matches exclude patterns
                    if any(exclude in base_name for exclude in exclude_patterns):
                        continue
                    # Strip version numbers from the file name
                    stripped_base_name = strip_version_numbers(base_name)
                    for pattern in patterns:
                        if pattern in stripped_base_name:
                            # Check if the file already exists
                            target_path = os.path.join(extract_dir, base_name)
                            if not os.path.exists(target_path):
                                # Extract and flatten directory structure
                                source = zip_ref.open(file_info)
                                with open(target_path, "wb") as target_file:
                                    target_file.write(source.read())
                                log_message(f"Extracted {base_name} to {extract_dir}")
                            # If the file is a .sh script, check permissions
                            if base_name.endswith(".sh"):
                                # Check if the file has executable permissions
                                if not os.access(target_path, os.X_OK):
                                    os.chmod(target_path, 0o755)
                                    log_message(
                                        f"Set executable permissions for {base_name}"
                                    )
                            matched_files.append(base_name)
                            break  # Stop checking patterns for this file
        except zipfile.BadZipFile:
            log_message(
                f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file."
            )
            try:
                os.remove(zip_path)
                log_message(f"Removed corrupted zip file: {zip_path}")
            except Exception as e:
                log_message(f"Error removing corrupted zip file {zip_path}: {e}")
        except Exception as e:
            log_message(
                f"Error: An unexpected error occurred while extracting files from {zip_path}: {e}"
            )

    # Function to strip version numbers from filenames
    def strip_version_numbers(filename):
        """
        Removes version numbers and commit hashes from the filename.
        Uses the same regex as in menu_firmware.py to ensure consistency.
        """
        # Regular expression matching version numbers and commit hashes
        base_name = re.sub(r"([_-])\d+\.\d+\.\d+(?:\.[\da-f]+)?", r"\1", filename)
        return base_name

    # Cleanup function to keep only specific versions based on release tags
    def cleanup_old_versions(directory, releases_to_keep):
        # Directories to exclude from cleanup
        excluded_dirs = ["repo-dls", "prerelease"]

        versions = [
            d
            for d in os.listdir(directory)
            if os.path.isdir(os.path.join(directory, d))
        ]
        for version in versions:
            # Skip excluded directories
            if version in excluded_dirs:
                continue

            if version not in releases_to_keep:
                version_path = os.path.join(directory, version)
                for root, dirs, files in os.walk(version_path, topdown=False):
                    for name in files:
                        os.remove(os.path.join(root, name))
                        log_message(f"Removed file: {os.path.join(root, name)}")
                    for name in dirs:
                        os.rmdir(os.path.join(root, name))
                os.rmdir(version_path)
                log_message(f"Removed directory: {version_path}")

    # Function to strip out non-printable characters and emojis
    def strip_unwanted_chars(text):
        """
        Strips out non-printable characters and emojis from a string.
        """
        # Regex for printable characters (including some extended ASCII)
        printable_regex = re.compile(r"[^\x00-\x7F]+")
        return printable_regex.sub("", text)

    # Function to check for missing releases and download them if necessary
    def check_and_download(
        releases,
        latest_release_file,
        release_type,
        download_dir,
        versions_to_keep,
        extract_patterns,
        selected_patterns=None,
    ):
        downloaded_versions = []
        new_versions_available = []
        actions_taken = False  # Track if any actions were taken

        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        # Load the latest release tag from file if available
        saved_release_tag = None
        if os.path.exists(latest_release_file):
            with open(latest_release_file, "r") as f:
                saved_release_tag = f.read().strip()

        # Determine which releases to download
        releases_to_download = releases[:versions_to_keep]

        if downloads_skipped:
            # Collect new versions available
            for release in releases_to_download:
                release_tag = release["tag_name"]
                if release_tag != saved_release_tag:
                    new_versions_available.append(release_tag)
            return downloaded_versions, new_versions_available

        for release in releases_to_download:
            release_tag = release["tag_name"]
            release_dir = os.path.join(download_dir, release_tag)
            release_notes_file = os.path.join(
                release_dir, f"release_notes-{release_tag}.md"
            )

            # Create release directory if it doesn't exist
            if not os.path.exists(release_dir):
                os.makedirs(release_dir, exist_ok=True)

            # Download release notes if missing
            if not os.path.exists(release_notes_file) and release.get("body"):
                log_message(f"Downloading release notes for version {release_tag}.")
                release_notes_content = strip_unwanted_chars(release["body"])
                with open(release_notes_file, "w", encoding="utf-8") as notes_file:
                    notes_file.write(release_notes_content)
                log_message(f"Saved release notes to {release_notes_file}")

            # First pass: check for corrupted zip files and remove them
            for asset in release["assets"]:
                file_name = asset["name"]
                if file_name.endswith(".zip"):
                    download_path = os.path.join(release_dir, file_name)
                    if os.path.exists(download_path):
                        try:
                            # Try to open the zip file to verify it's valid
                            with zipfile.ZipFile(download_path, "r"):
                                # File is valid, nothing to do
                                pass
                        except zipfile.BadZipFile:
                            # File is corrupted, remove it
                            log_message(f"Removing corrupted zip file: {download_path}")
                            os.remove(download_path)
                        except Exception as e:
                            # Some other error occurred, remove the file to be safe
                            log_message(
                                f"Error checking zip file {download_path}: {e}. Removing file."
                            )
                            os.remove(download_path)

            # Second pass: build list of files to download
            assets_to_download = []
            for asset in release["assets"]:
                file_name = asset["name"]
                # Strip version numbers from the file name
                stripped_file_name = strip_version_numbers(file_name)
                # Matching logic
                if selected_patterns:
                    if not any(
                        pattern in stripped_file_name for pattern in selected_patterns
                    ):
                        continue  # Skip this asset
                download_path = os.path.join(release_dir, file_name)
                if not os.path.exists(download_path):
                    assets_to_download.append(
                        (asset["browser_download_url"], download_path)
                    )

            if assets_to_download:
                actions_taken = True
                log_message(f"Downloading missing assets for version {release_tag}.")

                # Track if any files were successfully downloaded for this release
                any_downloaded = False

                for url, path in assets_to_download:
                    # download_file now returns a boolean indicating success
                    if download_file(url, path):
                        any_downloaded = True

                # Only mark the version as downloaded if at least one file was successfully downloaded
                if any_downloaded:
                    downloaded_versions.append(release_tag)

                # Extraction logic
                if auto_extract and release_type == "Firmware":
                    for asset in release["assets"]:
                        file_name = asset["name"]
                        if file_name.endswith(".zip"):
                            zip_path = os.path.join(release_dir, file_name)
                            if os.path.exists(zip_path):
                                extraction_needed = check_extraction_needed(
                                    zip_path,
                                    release_dir,
                                    extract_patterns,
                                    exclude_patterns,
                                )
                                if extraction_needed:
                                    log_message(f"Extracting files from {zip_path}...")
                                    extract_files(
                                        zip_path,
                                        release_dir,
                                        extract_patterns,
                                        exclude_patterns,
                                    )
            else:
                # No action needed for this release
                pass

            # Set permissions on .sh files if needed
            set_permissions_on_sh_files(release_dir)

        # Only update latest_release_file if downloads occurred
        if downloaded_versions:
            with open(latest_release_file, "w") as f:
                f.write(downloaded_versions[0])

        # Create a list of all release tags to keep
        release_tags_to_keep = [release["tag_name"] for release in releases_to_download]

        # Clean up old versions
        cleanup_old_versions(download_dir, release_tags_to_keep)

        if not actions_taken:
            log_message(f"All {release_type} assets are up to date.")

        # Collect new versions available
        for release in releases_to_download:
            release_tag = release["tag_name"]
            if (
                release_tag != saved_release_tag
                and release_tag not in downloaded_versions
            ):
                new_versions_available.append(release_tag)

        return downloaded_versions, new_versions_available

    def set_permissions_on_sh_files(directory):
        """
        Sets executable permissions on .sh files if they do not already have them.
        """
        for root, _dirs, files in os.walk(directory):
            for file in files:
                if file.endswith(".sh"):
                    file_path = os.path.join(root, file)
                    if not os.access(file_path, os.X_OK):
                        os.chmod(file_path, 0o755)
                        log_message(f"Set executable permissions for {file}")

    def check_extraction_needed(zip_path, extract_dir, patterns, exclude_patterns):
        """
        Checks if extraction is needed based on the current extraction patterns.
        Returns True if any files matching the patterns are not already extracted.
        """
        files_to_extract = []
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for file_info in zip_ref.infolist():
                    file_name = file_info.filename
                    base_name = os.path.basename(file_name)
                    # Skip directories
                    if not base_name:
                        continue
                    # Check if file matches exclude patterns
                    if any(exclude in base_name for exclude in exclude_patterns):
                        continue
                    # Strip version numbers from the file name
                    stripped_base_name = strip_version_numbers(base_name)
                    for pattern in patterns:
                        if pattern in stripped_base_name:
                            files_to_extract.append(base_name)
                            break  # Stop checking patterns for this file
            # Now check if any of the files to extract are missing
            for base_name in files_to_extract:
                extracted_file_path = os.path.join(extract_dir, base_name)
                if not os.path.exists(extracted_file_path):
                    return True  # Extraction needed
            return False  # All files already extracted
        except zipfile.BadZipFile:
            # If the zip file is corrupted, remove it and return False
            log_message(
                f"Error: {zip_path} is a bad zip file and cannot be opened. Removing file."
            )
            try:
                os.remove(zip_path)
                log_message(f"Removed corrupted zip file: {zip_path}")
            except Exception as e:
                log_message(f"Error removing corrupted zip file {zip_path}: {e}")
            return False
        except Exception as e:
            log_message(f"Error checking extraction needed for {zip_path}: {e}")
            return False

    start_time = time.time()
    log_message("Starting Fetchtastic...")

    # Check Wi-Fi connection before starting downloads (Termux only)
    downloads_skipped = False
    if setup_config.is_termux():
        if wifi_only and not is_connected_to_wifi():
            downloads_skipped = True

    # Initialize variables
    downloaded_firmwares = []
    downloaded_apks = []
    new_firmware_versions = []
    new_apk_versions = []

    # URLs for releases
    android_releases_url = (
        "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    )
    firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"

    # Increase scan count to cover more releases for cleanup
    releases_to_scan = 10

    latest_firmware_releases = []
    latest_android_releases = []

    if save_firmware and selected_firmware_patterns:
        latest_firmware_releases = get_latest_releases(
            firmware_releases_url, releases_to_scan
        )
        fw_downloaded, fw_new_versions = check_and_download(
            latest_firmware_releases,
            latest_firmware_release_file,
            "Firmware",
            firmware_dir,
            firmware_versions_to_keep,
            extract_patterns,
            selected_patterns=selected_firmware_patterns,
        )
        downloaded_firmwares.extend(fw_downloaded)
        new_firmware_versions.extend(fw_new_versions)
        if fw_downloaded:
            log_message(f"Downloaded Firmware versions: {', '.join(fw_downloaded)}")

        # Check if any pre-releases have been promoted to regular releases
        latest_release_tag = None
        if os.path.exists(latest_firmware_release_file):
            with open(latest_firmware_release_file, "r") as f:
                latest_release_tag = f.read().strip()

        if latest_release_tag:
            # Check if any pre-releases have been promoted to regular releases
            promoted = check_promoted_prereleases(
                download_dir, latest_release_tag, log_message
            )
            if promoted:
                log_message(
                    "Detected pre-release(s) that have been promoted to regular release."
                )

        # Check for pre-releases if enabled
        if check_prereleases and not downloads_skipped:
            # We already have the latest release tag from above
            if latest_release_tag:
                log_message("Checking for pre-release firmware...")
                prerelease_found, prerelease_versions = check_for_prereleases(
                    download_dir,
                    latest_release_tag,
                    selected_firmware_patterns,
                    log_message,
                )
                if prerelease_found:
                    log_message(
                        f"Pre-release firmware downloaded successfully: {', '.join(prerelease_versions)}"
                    )
                    # Add specific pre-release versions to notification messages
                    for version in prerelease_versions:
                        downloaded_firmwares.append(f"pre-release {version}")
                else:
                    log_message("No new pre-release firmware found or downloaded.")
            else:
                log_message("No latest release tag found. Skipping pre-release check.")
    elif not selected_firmware_patterns:
        log_message("No firmware assets selected. Skipping firmware download.")

    if save_apks and selected_apk_patterns:
        latest_android_releases = get_latest_releases(
            android_releases_url, releases_to_scan
        )
        apk_downloaded, apk_new_versions = check_and_download(
            latest_android_releases,
            latest_android_release_file,
            "Android APK",
            apks_dir,
            android_versions_to_keep,
            extract_patterns,  # Assuming extract_patterns is needed here
            selected_patterns=selected_apk_patterns,
        )
        downloaded_apks.extend(apk_downloaded)
        new_apk_versions.extend(apk_new_versions)
        if apk_downloaded:
            log_message(f"Downloaded Android APK versions: {', '.join(apk_downloaded)}")
    elif not selected_apk_patterns:
        log_message("No APK assets selected. Skipping APK download.")

    end_time = time.time()
    total_time = end_time - start_time
    log_message(
        f"Finished the Meshtastic downloader. Total time taken: {total_time:.2f} seconds"
    )

    # Display version information again at the end of the run
    if update_available:
        print("\n" + "=" * 80)
        print(
            f"Reminder: A newer version (v{latest_version}) of Fetchtastic is available!"
        )
        print("Run 'pipx upgrade fetchtastic' to upgrade.")
        print("=" * 80)

    if downloads_skipped:
        log_message("Not connected to Wi-Fi. Skipping all downloads.")
        # Prepare notification message
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
        log_message("\n".join(message_lines))
        send_ntfy_notification(
            notification_message, title="Fetchtastic Downloads Skipped"
        )
    elif downloaded_firmwares or downloaded_apks:
        # Prepare notification messages
        notification_messages = []
        if downloaded_firmwares:
            message = f"Downloaded Firmware versions: {', '.join(downloaded_firmwares)}"
            notification_messages.append(message)
        if downloaded_apks:
            message = f"Downloaded Android APK versions: {', '.join(downloaded_apks)}"
            notification_messages.append(message)
        notification_message = "\n".join(notification_messages) + f"\n{datetime.now()}"
        send_ntfy_notification(
            notification_message, title="Fetchtastic Download Completed"
        )
    else:
        # No new downloads; everything is up to date
        message = f"All assets are up to date.\n" f"{datetime.now()}"
        log_message(message)
        if not notify_on_download_only:
            send_ntfy_notification(message, title="Fetchtastic Up to Date")


if __name__ == "__main__":
    main()
