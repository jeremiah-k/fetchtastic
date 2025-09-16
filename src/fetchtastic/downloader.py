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
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version

from fetchtastic import menu_repo, setup_config

# Import constants from constants module
from fetchtastic.constants import (
    API_CALL_DELAY,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    EXECUTABLE_PERMISSIONS,
    GITHUB_API_TIMEOUT,
    LATEST_ANDROID_RELEASE_FILE,
    LATEST_FIRMWARE_RELEASE_FILE,
    MESHTASTIC_ANDROID_RELEASES_URL,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    NTFY_REQUEST_TIMEOUT,
    RELEASE_SCAN_COUNT,
    SHELL_SCRIPT_EXTENSION,
    VERSION_REGEX_PATTERN,
    ZIP_EXTENSION,
)

# Removed log_info, setup_logging
from fetchtastic.log_utils import logger  # Import new logger
from fetchtastic.setup_config import display_version_info, get_upgrade_command
from fetchtastic.utils import (
    download_file_with_retry,
    matches_selected_patterns,
)

# Compiled regex for performance
NON_ASCII_RX = re.compile(r"[^\x00-\x7F]+")


def _summarise_release_scan(kind: str, total_found: int, keep_limit: int) -> str:
    """Return a concise log message describing how many releases will be scanned."""

    scan_count = min(total_found, keep_limit)
    message = f"Found {total_found} {kind} releases; scanning newest {scan_count}"
    if keep_limit > scan_count:
        message += f" (keep limit {keep_limit})"
    return message


def _summarise_scan_window(release_type: str, scan_count: int) -> str:
    """
    Build a concise log message describing the scanning window for a release type.

    Returns a message suitable for logging:
    - If scan_count == 0 returns "No <release_type> releases to scan".
    - Uses singular "release" when scan_count == 1, otherwise "releases".

    Parameters:
        release_type (str): Human-readable name of the release kind (e.g., "firmware", "Android APK").
        scan_count (int): Number of releases discovered for scanning.

    Returns:
        str: The formatted scan-window message.
    """

    if scan_count == 0:
        return f"No {release_type} releases to scan"
    descriptor = "release" if scan_count == 1 else "releases"
    return f"Scanning {release_type} {descriptor}"


def _newer_tags_since_saved(
    tags_order: List[str], saved_release_tag: Optional[str]
) -> List[str]:
    """
    Return the subset of tags from newest to oldest that are strictly newer than the saved release tag.

    If tags_order is ordered newest-first, this returns all tags preceding the first occurrence of saved_release_tag. If saved_release_tag is None, missing, or not found in tags_order, the full tags_order is returned (treated as all newer).
    """
    try:
        idx_saved = tags_order.index(saved_release_tag)
    except (ValueError, TypeError):
        idx_saved = len(tags_order)
    return tags_order[:idx_saved]


def compare_versions(version1, version2):
    """
    Compare two version strings and determine their ordering.

    Attempts to parse both inputs as PEP 440 versions (using packaging); before parsing it normalizes
    common non-PEP-440 forms (e.g., dotted/dashed prerelease markers like "2.3.0.rc1" → "2.3.0rc1",
    or trailing hash-like segments "1.2.3.abcd" → "1.2.3+abcd"). If both versions parse, they are
    compared according to PEP 440 semantics (including pre-releases and local version segments).
    If one or both cannot be parsed, a conservative natural-sort fallback is used that splits strings
    into numeric and alphabetic runs for human-friendly ordering.

    Parameters:
        version1 (str): First version string to compare.
        version2 (str): Second version string to compare.

    Returns:
        int: 1 if version1 > version2, 0 if equal, -1 if version1 < version2.
    """

    def _try_parse(v: str):
        """
        Attempt to parse a version string into a packaging.version object, returning None if it cannot be normalized.

        This function tries to coerce several common non-PEP 440 version formats into parseable forms before calling packaging.version.parse:
        - Normalizes dotted or dashed prerelease markers (e.g., "2.3.0.rc1" or "2.3.0-beta2") into PEP 440 prerelease notation ("2.3.0rc1", "2.3.0b2").
        - Converts trailing hash-like segments such as "1.2.3.abcd123" into a PEP 440 local version ("1.2.3+abcd123").

        Parameters:
            v (str): Input version string.

        Returns:
            packaging.version.Version or packaging.version.LegacyVersion or None:
                A parsed version object on success, or None if the input could not be parsed or normalized.
        """
        try:
            return parse_version(v)
        except InvalidVersion:
            # 1) Coerce dot/dash prerelease: e.g., 2.3.0.rc1 -> 2.3.0rc1, 2.3.0-beta2 -> 2.3.0b2
            m_pr = re.match(
                r"^(\d+(?:\.\d+){2})[.-](rc|dev|alpha|beta|b)\.?(\d*)$",
                v,
                re.IGNORECASE,
            )
            if m_pr:
                kind = m_pr.group(2).lower().replace("alpha", "a").replace("beta", "b")
                num = m_pr.group(3) or "0"
                try:
                    return parse_version(f"{m_pr.group(1)}{kind}{num}")
                except InvalidVersion:
                    pass
            # 2) Coerce common pattern "X.Y.Z.<hash>" to PEP 440 local version "X.Y.Z+<hash>"
            m = re.match(r"^(\d+(?:\.\d+)*)\.([A-Za-z0-9][A-Za-z0-9.-]*)$", v)
            if m:
                try:
                    return parse_version(f"{m.group(1)}+{m.group(2)}")
                except InvalidVersion:
                    pass
            return None

    v1 = _try_parse(version1)
    v2 = _try_parse(version2)
    if v1 is not None and v2 is not None:
        if v1 > v2:
            return 1
        elif v1 < v2:
            return -1
        else:
            return 0

    # Natural comparison fallback for truly non-standard versions
    def _nat_key(s: str):
        # Split into digit or alpha runs; drop punctuation to avoid lexical noise
        """
        Return a natural-sort key for a string by splitting it into digit and alphabetic runs.

        The function lowercases the input, removes punctuation by only capturing contiguous digits or letters,
        and returns a list where numeric runs are converted to int and alphabetic runs remain as strings.
        This key can be used with sorted(..., key=_nat_key) to achieve human-friendly ordering (e.g., "v2" < "v10").
        """
        parts = re.findall(r"\d+|[A-Za-z]+", s.lower())
        return [int(p) if p.isdigit() else p for p in parts]

    k1, k2 = _nat_key(version1), _nat_key(version2)
    if k1 > k2:
        return 1
    elif k1 < k2:
        return -1
    return 0


def check_promoted_prereleases(
    download_dir, latest_release_tag
):  # log_message_func parameter removed
    """
    Check for prerelease firmware directories that have been promoted to an official release.

    Scans the firmware/prerelease subdirectory under download_dir for entries named
    "firmware-<version>". For any prerelease whose version (the "v" prefix, if present,
    is ignored) matches latest_release_tag, the function compares file contents with the
    corresponding regular release directory using SHA-256 via compare_file_hashes. If all
    files match, the prerelease directory is removed. If the official release directory
    does not exist, the prerelease directory is removed (it is expected the official
    release will be downloaded separately). Invalidly formatted prerelease directory
    names (not matching VERSION_REGEX_PATTERN) are skipped.

    Parameters:
        download_dir (str): Base download directory containing firmware/{prerelease, <tag>}.
        latest_release_tag (str): Latest official release tag (may include a leading 'v').

    Returns:
        bool: True if one or more prerelease directories were promoted/removed; False otherwise.
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

            # Validate version format before processing (hash part is optional)
            if not re.match(VERSION_REGEX_PATTERN, dir_version):
                logger.warning(
                    f"Invalid version format in prerelease directory {dir_name}, skipping"
                )
                continue

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
                        else:
                            # File exists in prerelease but not in release - they don't match
                            files_match = False
                            logger.warning(
                                f"File {file_name} exists in pre-release but not in release directory"
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
    Return True if the two files have identical SHA-256 hashes.

    Computes SHA-256 digests by reading each file in 4KB chunks. If either file does not exist or cannot be read, a warning/error is logged and the function returns False.

    Returns:
        bool: True when both files were read successfully and their SHA-256 hashes match; False otherwise.
    """
    import hashlib

    def get_file_hash(file_path: str) -> Optional[str]:
        # Check if file exists first
        """
        Compute the SHA-256 hash of a file and return it as a hex string.

        Parameters:
            file_path (str): Path to the file to hash.

        Returns:
            Optional[str]: Hexadecimal SHA-256 digest of the file, or None if the file does not exist or cannot be read.
        """
        if not os.path.exists(file_path):
            logger.warning(f"File does not exist for hashing: {file_path}")
            return None

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

    if hash1 is None or hash2 is None:
        return False

    return hash1 == hash2


def check_for_prereleases(
    download_dir,
    latest_release_tag,
    selected_patterns,
    exclude_patterns=None,  # log_message_func parameter removed
):
    """
    Discover firmware prerelease directories newer than the provided latest release, clean up stale local prerelease folders, and download missing prerelease assets into <download_dir>/firmware/prerelease.

    The function:
    - Treats a leading "v" in latest_release_tag as optional and strips it for comparisons.
    - Scans the remote repository for directories named "firmware-<version>" and keeps only those newer than latest_release_tag.
    - Removes stale local prerelease directories that are not present in the repository or not newer than the official release.
    - For each relevant prerelease, downloads assets that match selected_patterns (using the repository's back-compat matching helper) and do not match any exclude_patterns; sets executable permissions for shell scripts when possible.
    - Returns whether any prerelease assets were downloaded and a list of prerelease directory names that had files downloaded.

    Parameters:
        download_dir (str): Base path where firmware/prerelease directories live.
        latest_release_tag (str): Latest official release tag (e.g., "v2.6.8" or "2.6.8.ef9d0d7"); a leading "v" is tolerated and stripped for comparison.
        selected_patterns (Iterable[str]): Patterns/substrings used to select which asset basenames should be downloaded (applied with the module's back-compat matcher).
        exclude_patterns (Optional[Iterable[str]]): Optional fnmatch-style patterns; any filename matching an exclude pattern will be skipped.

    Returns:
        tuple[bool, list[str]]: (found_and_downloaded, downloaded_versions)
          - found_and_downloaded: True if any prerelease files were downloaded.
          - downloaded_versions: List of prerelease directory names (e.g., "firmware-2.6.9.f93d031") that had files downloaded.
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

                    # Validate version format (should be X.Y.Z or X.Y.Z.hash)
                    if not re.match(VERSION_REGEX_PATTERN, dir_version):
                        logger.warning(
                            f"Invalid version format in directory {dir_name}, removing"
                        )
                        should_keep = False
                    elif dir_name in repo_firmware_dirs:
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
                ValueError,
                KeyError,
                TypeError,
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

                            # Apply same filtering logic as download (back-compat matching)
                            if not matches_selected_patterns(
                                file_name, selected_patterns
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
    total_pr = len(prerelease_dirs)
    logger.info(f"Scanning {total_pr} prerelease directories")
    for i, dir_name in enumerate(prerelease_dirs, start=1):
        logger.info(f"Checking {dir_name} ({i}/{total_pr})…")

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
            # Backward-compatible pattern matching (modern + legacy normalization)
            if not matches_selected_patterns(file_name, selected_patterns):
                logger.debug(
                    "Skipping pre-release file %s (no pattern match)", file_name
                )
                continue  # Skip this file

            # Skip files that match exclude patterns
            if any(
                fnmatch.fnmatch(file_name, exclude) for exclude in exclude_patterns_list
            ):
                logger.debug(
                    "Skipping pre-release file %s (matched exclude pattern)",
                    file_name,
                )
                continue  # Skip this file

            if not os.path.exists(file_path):
                try:
                    logger.debug(
                        f"Downloading pre-release file: {file_name} from {download_url}"
                    )
                    if not download_file_with_retry(download_url, file_path):
                        # Download_file_with_retry logs the specific error, so we just continue.
                        continue

                    # Set executable permissions for .sh files
                    if file_name.lower().endswith(SHELL_SCRIPT_EXTENSION.lower()):
                        try:
                            os.chmod(file_path, EXECUTABLE_PERMISSIONS)
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
            if any(
                os.path.basename(os.path.dirname(p)) == dir_name
                for p in downloaded_files
            ):
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
    Return a list of the most recent releases fetched from a GitHub releases API endpoint.

    Fetches up to `scan_count` releases from the provided GitHub API `url` (clamped to 1–100),
    parses the JSON response, and returns releases sorted by their `published_at` timestamp
    in descending order. Respects a short polite delay after the request and logs GitHub
    rate-limit remaining when available.

    Parameters:
        url (str): GitHub API URL that returns a list of releases (JSON).
        scan_count (int): Maximum number of releases to return (clamped to GitHub's per_page bounds).

    Returns:
        List[Dict[str, Any]]: Sorted list of release dictionaries (newest first). Returns an empty
        list on network or JSON parse errors. If sorting by `published_at` is not possible due
        to missing or invalid keys, the unsorted JSON list is returned.
    """
    try:
        # Add progress feedback
        url_l = url.lower()
        if "firmware" in url_l:
            logger.info("Fetching firmware releases from GitHub...")
        elif "android" in url_l:
            logger.info("Fetching Android APK releases from GitHub...")
        else:
            logger.info("Fetching releases from GitHub...")

        # Clamp scan_count to GitHub's per_page bounds
        scan_count = max(1, min(100, scan_count))
        response: requests.Response = requests.get(
            url,
            timeout=GITHUB_API_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Fetchtastic",
            },
            params={"per_page": scan_count},
        )
        response.raise_for_status()

        # Small delay to be respectful to GitHub API
        time.sleep(API_CALL_DELAY)
        try:
            rl = response.headers.get("X-RateLimit-Remaining")
            if rl is not None:
                logger.debug(f"GitHub API rate-limit remaining: {rl}")
        except (KeyError, ValueError, AttributeError) as e:
            logger.debug(f"Could not parse rate-limit header: {e}")

        releases: List[Dict[str, Any]] = response.json()

        # Log how many releases were fetched
        logger.debug(f"Fetched {len(releases)} releases from GitHub API")

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch releases data from {url}: {e}")
        return []  # Return empty list on error
    except (ValueError, json.JSONDecodeError) as e:
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
    Perform initial startup tasks: display version information, load configuration, ensure download directories exist, and assemble key paths/URLs.

    Returns:
        A tuple of five elements:
        - config (Optional[Dict[str, Any]]): Loaded configuration dictionary, or None if configuration could not be loaded.
        - current_version (Optional[str]): The running application version (may be None if unknown).
        - latest_version (Optional[str]): The latest available application version (may be None if unknown).
        - update_available (bool): True if a newer version is available, False otherwise.
        - paths_and_urls (Optional[Dict[str, str]]): Dictionary of important paths and repository/release URLs (download_dir, firmware_dir, apks_dir, latest release file paths, and releases URLs), or None if setup failed.

    Side effects:
        - Logs version and upgrade guidance.
        - Creates the download, firmware, and apks directories if they do not exist (may log errors on failure).
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
            apks_dir, LATEST_ANDROID_RELEASE_FILE
        ),
        "latest_firmware_release_file": os.path.join(
            firmware_dir, LATEST_FIRMWARE_RELEASE_FILE
        ),
        "android_releases_url": MESHTASTIC_ANDROID_RELEASES_URL,
        "firmware_releases_url": MESHTASTIC_FIRMWARE_RELEASES_URL,
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
    Process firmware release downloads and optional prerelease handling.

    When enabled in config (SAVE_FIRMWARE and SELECTED_FIRMWARE_ASSETS), fetches the latest firmware releases,
    downloads missing assets (honoring selected and excluded asset patterns), optionally extracts archives,
    updates the latest-release tracking file, and performs cleanup/retention according to configured
    FIRMWARE_VERSIONS_TO_KEEP. If a latest release tag exists, checks for promoted prereleases and — when
    CHECK_PRERELEASES is enabled and downloads have not been skipped due to Wi-Fi gating — attempts to
    discover and download newer prerelease firmware that matches the selection criteria.

    Configuration keys referenced: SAVE_FIRMWARE, SELECTED_FIRMWARE_ASSETS, FIRMWARE_VERSIONS_TO_KEEP,
    EXTRACT_PATTERNS, AUTO_EXTRACT, EXCLUDE_PATTERNS, CHECK_PRERELEASES.

    Returns a tuple of:
    - downloaded firmware versions (List[str]) — includes strings like "pre-release X.Y.Z" for prereleases,
    - newly detected release versions that were not previously recorded (List[str]),
    - list of dictionaries with details for failed downloads (List[Dict[str, str]]),
    - the latest firmware release tag string or None if no releases were found.
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

        keep_count = config.get(
            "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
        )
        logger.info(
            _summarise_release_scan(
                "firmware", len(latest_firmware_releases), keep_count
            )
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
                config.get(
                    "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
                ),
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
                        config.get("SELECTED_FIRMWARE_ASSETS", []),  # type: ignore
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
    Process Android APK releases: fetch release metadata, download selected APK assets, and report what changed.

    When SAVE_APKS is true and SELECTED_APK_ASSETS is non-empty in `config`, this function:
    - fetches up to ANDROID_VERSIONS_TO_KEEP releases from the configured Android releases URL,
    - determines the latest release tag,
    - invokes the shared download workflow to download APK assets that match SELECTED_APK_ASSETS,
    - respects the configured keep count when pruning old versions.

    Expected keys used from inputs:
    - config: SAVE_APKS, SELECTED_APK_ASSETS, ANDROID_VERSIONS_TO_KEEP (optional).
    - paths_and_urls: "android_releases_url", "latest_android_release_file", "apks_dir".

    Returns:
    A tuple (downloaded_apk_versions, new_apk_versions, failed_downloads, latest_apk_version):
    - downloaded_apk_versions (List[str]): versions successfully downloaded in this run.
    - new_apk_versions (List[str]): newly discovered release versions (may include ones not downloaded).
    - failed_downloads (List[Dict[str, str]]): details for each failed asset download.
    - latest_apk_version (Optional[str]): tag/name of the most recent release found, or None if none were available.
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
            config.get("ANDROID_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT),
        )

        keep_count_apk = config.get(
            "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
        )
        logger.info(
            _summarise_release_scan(
                "Android APK", len(latest_android_releases), keep_count_apk
            )
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
                config.get(
                    "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
                ),
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
    Finalize processing: log a concise summary, display/app-update messaging, and send NTFY notifications about download results.

    Computes total runtime from start_time, logs the number of downloaded firmware/APK files and the latest firmware/APK versions when available, and logs an upgrade command if update_available is True. Sends a notification via _send_ntfy_notification based on three states:
    - downloads_skipped (global): reports available new versions but indicates downloads were skipped,
    - downloaded items present: reports the downloaded firmware/APK versions,
    - no downloads and not skipped: reports that assets are up to date (suppressed when config['NOTIFY_ON_DOWNLOAD_ONLY'] is True).

    Reads notification settings from config keys "NTFY_SERVER", "NTFY_TOPIC", and "NOTIFY_ON_DOWNLOAD_ONLY". Side effects: logging and conditional network notifications.
    """
    global downloads_skipped
    end_time: float = time.time()
    total_time: float = end_time - start_time

    # Create clean summary
    downloaded_count = len(downloaded_firmwares) + len(downloaded_apks)

    logger.info(f"\nCompleted in {total_time:.1f}s")
    if downloaded_count > 0:
        logger.info(f"Downloaded {downloaded_count} new versions")

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
        notification_message = (
            "\n".join(message_lines)
            + f"\n{datetime.now().astimezone().isoformat(timespec='seconds')}"
        )
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
        notification_message = (
            "\n".join(notification_messages)
            + f"\n{datetime.now().astimezone().isoformat(timespec='seconds')}"
        )
        _send_ntfy_notification(
            ntfy_server,
            ntfy_topic,
            notification_message,
            title="Fetchtastic Download Completed",
        )
    else:
        message: str = (
            f"All assets are up to date.\n{datetime.now().astimezone().isoformat(timespec='seconds')}"
        )
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
    Extract selected files from a ZIP archive into a target directory.

    Only entries whose base filename (matched via the centralized legacy-aware matcher)
    match the provided `patterns` and do not match any `exclude_patterns` are extracted.
    If `patterns` is empty, no files are extracted. This preserves the historical behavior
    where an empty extraction pattern list means "do not auto-extract".
    Preserves archive subdirectories when extracting, creates target directories as needed, and sets
    executable permissions on extracted files ending with SHELL_SCRIPT_EXTENSION.
    Uses safe_extract_path to prevent directory traversal; unsafe entries are skipped. If the archive
    is corrupted it will be removed.

    Parameters:
        zip_path (str): Path to the ZIP archive to read.
        extract_dir (str): Destination directory where files will be extracted.
        patterns (List[str]): Substring patterns to include (matched via centralized matcher).
            An empty list means “extract nothing.”
        exclude_patterns (List[str]): Glob-style patterns (fnmatch) to exclude based on the base filename.

    Side effects:
        - Creates directories and writes files under extract_dir.
        - May set executable permissions on shell scripts.
        - On a BadZipFile error, attempts to remove the corrupted zip file.

    Exceptions:
        This function handles and logs IO, OS, and ZIP errors internally; it does not raise on these
        conditions.
    """
    # Historical behavior: empty pattern list means "do not extract anything".
    if not patterns:
        logger.debug(
            "extract_files called with empty patterns; skipping extraction entirely"
        )
        return

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            file_info: zipfile.ZipInfo
            for file_info in zip_ref.infolist():
                # Skip directory entries in archives
                if hasattr(file_info, "is_dir") and file_info.is_dir():
                    continue
                file_name: str = file_info.filename  # may include subdirectories
                base_name: str = os.path.basename(file_name)
                if not base_name:
                    continue
                if any(
                    fnmatch.fnmatch(base_name, exclude) for exclude in exclude_patterns
                ):
                    continue

                # Use the same back-compat matcher used for selection (modern + legacy normalization)
                if matches_selected_patterns(base_name, patterns):
                    try:
                        # Preserve directory structure from within the archive
                        target_path: str = safe_extract_path(extract_dir, file_name)
                        if not os.path.exists(target_path):
                            target_dir_for_file: str = os.path.dirname(target_path)
                            if not os.path.exists(target_dir_for_file):
                                os.makedirs(
                                    target_dir_for_file, exist_ok=True
                                )  # Can raise OSError
                            with zip_ref.open(file_info) as source, open(
                                target_path, "wb"
                            ) as target_file:
                                shutil.copyfileobj(
                                    source, target_file, length=1024 * 64
                                )
                            logger.debug(f"  Extracted: {file_name}")
                        if base_name.lower().endswith(
                            SHELL_SCRIPT_EXTENSION.lower()
                        ) and not os.access(target_path, os.X_OK):
                            os.chmod(
                                target_path, EXECUTABLE_PERMISSIONS
                            )  # Can raise OSError
                            logger.debug(f"Set executable permissions for {file_name}")
                        # Proceed to next entry after extracting this one
                        continue
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
                    ) as e_inner_extract:  # noqa: BLE001 - Catch-all is intentional for resilience
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


def cleanup_old_versions(directory: str, releases_to_keep: List[str]) -> None:
    """
    Remove versioned subdirectories under `directory` except for the ones specified in `releases_to_keep`.

    This scans `directory` for immediate subdirectories and deletes any that are not listed in `releases_to_keep` and are not in the internal exclusion list ("repo-dls", "prerelease"). Deletion is performed with shutil.rmtree; failures are caught and logged but not re-raised.

    Parameters:
        directory (str): Path containing versioned subdirectories to prune.
        releases_to_keep (List[str]): Release names (subdirectory basenames) that must be preserved.

    Side effects:
        Permanently removes directories on disk (and their contents) for versions not kept; logs actions and warnings on errors.
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
    Return a copy of `text` with all non-ASCII characters removed (e.g., emojis, accented letters).

    Parameters:
        text: String potentially containing non-ASCII characters to be stripped.

    Returns:
        A string containing only ASCII characters from the original input.
    """
    return NON_ASCII_RX.sub("", text)


def _is_release_complete(
    release_data: Dict[str, Any],
    release_dir: str,
    selected_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> bool:
    """
    Return True if the local release directory contains all expected assets (filtered by include/exclude patterns)
    and those assets pass basic integrity checks; otherwise False.

    This verifies presence and basic integrity of release assets as declared in release_data["assets"]:
    - Assets are selected if they match selected_patterns (when provided, via the centralized matcher)
      and do not match any fnmatch pattern in exclude_patterns.
    - For each expected asset:
      - Existence is required.
      - Zip files are opened and tested with ZipFile.testzip(); file size is compared to the declared asset size when available.
      - Non-zip files have their on-disk size compared to the declared asset size when available.

    Parameters:
        release_data: Release metadata dict containing an "assets" list (each asset should include "name"
            and may include "size") used to determine expected filenames and sizes.
        release_dir: Filesystem path to the local release directory to inspect.
        selected_patterns: Optional list of inclusion patterns; when provided only assets matching these
            (via matches_selected_patterns) are considered expected.
        exclude_patterns: List of fnmatch-style patterns; any asset matching one of these is ignored.

    Returns:
        True if all expected assets are present and pass integrity/size checks; False otherwise.
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
        if selected_patterns and not matches_selected_patterns(
            file_name, selected_patterns
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
        if asset_name.lower().endswith(ZIP_EXTENSION.lower()):
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
    Check a list of releases, download any missing or corrupted assets, optionally extract ZIPs, and prune old release directories.

    Processes up to `versions_to_keep` newest entries from `releases`. For each release it:
    - Skips releases that are already complete.
    - Downloads assets that match `selected_patterns` (if provided) and do not match `exclude_patterns`.
    - Optionally extracts files from ZIP assets when `auto_extract` is True and `release_type` == "Firmware"; extraction uses `extract_patterns` to select files.
    - Saves release notes, ensures `.sh` files are executable, and removes older release subdirectories not in the retention window.
    - Updates `latest_release_file` when a newer release has been successfully processed.

    Side effects:
    - Creates release directories and may write `latest_release_file`.
    - May remove corrupted ZIP files and delete old release directories during cleanup.
    - Honors a global Wi-Fi gating flag: if downloads were skipped, the function will not download and instead returns newer release tags.

    Parameters:
    - releases: Iterable of release dicts from the API (newest-first order expected).
    - latest_release_file: Path to a file storing the most recently recorded release tag.
    - release_type: Human-readable type used in logs and failure records (e.g., "Firmware" or "APK").
    - download_dir_path: Root directory where per-release subdirectories are created.
    - versions_to_keep: Number of newest releases to consider for download/retention.
    - extract_patterns: Patterns used to select files to extract from ZIP archives.
    - selected_patterns: If provided, only assets whose names match any of these patterns are considered for download.
    - auto_extract: When True and `release_type` == "Firmware`, ZIP assets will be extracted when needed.
    - exclude_patterns: Optional patterns; matching filenames are excluded from download and extraction.

    Returns:
    - Tuple(downloaded_versions, new_versions_available, failed_downloads_details)
      - downloaded_versions: list of release tags for which at least one asset was successfully downloaded.
      - new_versions_available: list of release tags newer than the saved/latest tag that remain pending or were not downloaded.
      - failed_downloads_details: list of dicts describing individual failed downloads (keys include url, path_to_download, release_tag, file_name, reason, and type).
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

    total_to_scan = len(releases_to_download)
    logger.info(_summarise_scan_window(release_type, total_to_scan))

    if downloads_skipped:
        # Mirror the “newer than saved” computation used later (newest-first list).
        tags_order: List[str] = [
            rd.get("tag_name") for rd in releases_to_download if rd.get("tag_name")
        ]
        newer_tags: List[str] = _newer_tags_since_saved(tags_order, saved_release_tag)
        new_versions_available = list(dict.fromkeys(newer_tags))
        return (downloaded_versions, new_versions_available, failed_downloads_details)

    release_data: Dict[str, Any]
    for idx, release_data in enumerate(releases_to_download, start=1):
        try:
            release_tag: str = release_data[
                "tag_name"
            ]  # Potential KeyError if API response changes
            if total_to_scan > 1:
                logger.debug("Checking %s (%d of %d)", release_tag, idx, total_to_scan)
            logger.info("Checking %s…", release_tag)
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

                if file_name.lower().endswith(ZIP_EXTENSION.lower()):
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

                if selected_patterns and not matches_selected_patterns(
                    file_name, selected_patterns
                ):
                    logger.debug(
                        "Skipping %s asset %s (no pattern match)",
                        release_type,
                        file_name,
                    )
                    continue
                # Honor exclude patterns at download-time as well
                if any(fnmatch.fnmatch(file_name, ex) for ex in exclude_patterns_list):
                    logger.debug(
                        "Skipping %s asset %s (matched exclude pattern)",
                        release_type,
                        file_name,
                    )
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
            logger.info("Processing release: %s", release_tag)
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

                    if file_name.lower().endswith(ZIP_EXTENSION.lower()):
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

        else:
            # If this is a newer release than what we've saved but no assets
            # matched the user's patterns, surface a helpful note.
            try:
                if saved_release_tag is None or release_tag != saved_release_tag:
                    logger.info(
                        f"Release {release_tag} found, but no assets matched the current selection/exclude filters."
                    )
                    # Consider the latest release processed even without downloads to avoid re-scanning
                    try:
                        if idx == 1:
                            with open(latest_release_file, "w") as f:
                                f.write(release_tag)
                            saved_release_tag = release_tag
                            logger.debug(
                                f"Updated latest release tag to {release_tag} (no matching assets)"
                            )
                    except IOError as e:
                        logger.debug(
                            f"Could not record latest release tag {release_tag}: {e}"
                        )
            except TypeError:
                # Avoid breaking flow on unexpected edge cases in saved tag reading
                logger.debug(
                    "Could not determine saved release tag state when evaluating matched assets due to a type issue."
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
        # Determine tags newer than the saved tag by position (list is newest-first)
        tags_order: List[str] = [
            rd.get("tag_name") for rd in releases_to_download if rd.get("tag_name")
        ]
        newer_tags: List[str] = _newer_tags_since_saved(tags_order, saved_release_tag)
        new_candidates: List[str] = [
            t for t in newer_tags if t not in downloaded_versions
        ]

        if not new_candidates:
            logger.info(f"All {release_type} assets are up to date.")

        # Merge uniquely with any earlier additions
        new_versions_available = list(
            dict.fromkeys(new_versions_available + new_candidates)
        )

    return downloaded_versions, new_versions_available, failed_downloads_details


def set_permissions_on_sh_files(directory: str) -> None:
    """
    Ensure all files ending with the shell script extension under `directory` are executable.

    Recursively walks `directory` and sets executable permissions (using EXECUTABLE_PERMISSIONS) on files whose names end with `SHELL_SCRIPT_EXTENSION` (case-insensitive) when they lack execute permission. IO and permission errors are caught and logged; the function does not raise on such errors.
    """
    root: str
    files: List[str]
    try:
        for root, _dirs, files in os.walk(directory):
            file_in_dir: str
            for file_in_dir in files:
                if file_in_dir.lower().endswith(SHELL_SCRIPT_EXTENSION.lower()):
                    file_path: str = os.path.join(root, file_in_dir)
                    try:
                        if not os.access(file_path, os.X_OK):
                            os.chmod(file_path, EXECUTABLE_PERMISSIONS)
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
    Return whether a ZIP archive contains any files matching `patterns` that are not yet present in `extract_dir`.

    Checks archive entries (skipping directories) and filters out entries whose base filename matches any pattern in `exclude_patterns`. Matching against `patterns` uses the module's back-compat matcher (matches_selected_patterns). If `patterns` is empty this function returns False.

    Returns:
        bool: True if at least one matched file in the ZIP is missing from `extract_dir` (extraction needed); False otherwise.

    Notes:
    - If the ZIP is corrupted (zipfile.BadZipFile) the function will attempt to remove the ZIP file and returns False.
    - On IO/OSError or other unexpected exceptions the function conservatively returns True (assume extraction is needed).
    """
    # Preserve historical behavior: empty list of patterns means "do not extract".
    if not patterns:
        logger.debug(
            "check_extraction_needed called with empty patterns; returning False"
        )
        return False

    files_to_extract: List[str] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            file_info: zipfile.ZipInfo
            for file_info in zip_ref.infolist():
                # Skip directory entries in archives
                if hasattr(file_info, "is_dir") and file_info.is_dir():
                    continue
                file_name: str = file_info.filename  # may include subdirectories
                base_name: str = os.path.basename(file_name)
                if not base_name:
                    continue
                if any(
                    fnmatch.fnmatch(base_name, exclude) for exclude in exclude_patterns
                ):
                    continue
                if matches_selected_patterns(base_name, patterns):
                    # Preserve path for existence checks
                    files_to_extract.append(file_name)
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
        return False  # Extraction cannot proceed; ZIP removed or invalid
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
