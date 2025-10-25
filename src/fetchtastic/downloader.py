# src/fetchtastic/downloader.py

import fnmatch
import glob
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from functools import cmp_to_key
from typing import IO, Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

# Try to import LegacyVersion for type annotations (available in older packaging versions)
try:
    from packaging.version import LegacyVersion
except ImportError:
    LegacyVersion = type(None)  # type: ignore


from fetchtastic import menu_repo, setup_config
from fetchtastic.constants import (
    API_CALL_DELAY,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
    EXECUTABLE_PERMISSIONS,
    FILE_TYPE_PREFIXES,
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
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.log_utils import logger
from fetchtastic.setup_config import display_version_info, get_upgrade_command
from fetchtastic.utils import (
    download_file_with_retry,
    get_hash_file_path,
    matches_selected_patterns,
    verify_file_integrity,
)

"""
Version Handling for Meshtastic Releases

This module provides utilities for handling version strings and comparisons for Meshtastic
firmware and Android APK releases. The versioning approach accounts for:

Expected Version Formats:
- Stable releases: "v2.7.8", "2.7.8"
- Prereleases: "v2.7.8-rc1", "2.7.8.a0c0388", "1.2-rc1"
- Development versions: "2.7.8-dev", "2.7.8-alpha1"

Key Design Principles:
1. Prereleases and stable releases come from separate repositories
2. Prerelease versions are newer than their base stable version but older than the next stable version
3. Version normalization handles various formats consistently for comparisons
4. Tuple-based optimizations provide performance while maintaining correctness

Helper Functions:
- _normalize_version(): Converts version strings to packaging.Version objects with format coercion
- _get_release_tuple(): Extracts numeric tuples for efficient comparisons
- compare_versions(): Performs full version comparisons when tuple optimization isn't sufficient
"""

# Compiled regex for performance
NON_ASCII_RX = re.compile(r"[^\x00-\x7F]+")

# Compiled regular expressions for version parsing performance
PRERELEASE_VERSION_RX = re.compile(
    r"^(\d+(?:\.\d+)*)[.-](rc|dev|alpha|beta|b)\.?(\d*)$", re.IGNORECASE
)
HASH_SUFFIX_VERSION_RX = re.compile(r"^(\d+(?:\.\d+)*)\.([A-Za-z0-9][A-Za-z0-9.-]*)$")
VERSION_BASE_RX = re.compile(r"^(\d+(?:\.\d+)*)")


def _normalize_version(
    version: Optional[str],
) -> Optional[Union[Version, LegacyVersion]]:
    """
    Normalize a version string into a packaging `Version` object when possible.

    Attempts to coerce common repository-style forms into PEP 440-compatible versions:
    strips a leading "v", recognizes common prerelease markers (e.g. "alpha"/"beta" and numeric fragments),
    and converts trailing commit/hash-like suffixes into local version identifiers. Returns None when the
    input is empty, None, or cannot be parsed into a Version.

    Parameters:
        version (Optional[str]): A raw version string (may include leading "v", prerelease words, or hash suffixes).

    Returns:
        Optional[Union[Version, LegacyVersion]]: A parsed `Version` / `LegacyVersion` if parsing succeeds, otherwise `None`.
    """
    if version is None:
        return None

    trimmed = version.strip()
    if not trimmed:
        return None

    if trimmed.lower().startswith("v"):
        trimmed = trimmed[1:]

    try:
        return parse_version(trimmed)
    except InvalidVersion:
        m_pr = PRERELEASE_VERSION_RX.match(trimmed)
        if m_pr:
            pr_kind_lower = m_pr.group(2).lower()
            kind = {"alpha": "a", "beta": "b"}.get(pr_kind_lower, pr_kind_lower)
            num = m_pr.group(3) or "0"
            try:
                return parse_version(f"{m_pr.group(1)}{kind}{num}")
            except InvalidVersion:
                logger.debug(
                    "Could not parse '%s' as a standard prerelease version.",
                    trimmed,
                    exc_info=True,
                )

        m_hash = HASH_SUFFIX_VERSION_RX.match(trimmed)
        if m_hash:
            try:
                return parse_version(f"{m_hash.group(1)}+{m_hash.group(2)}")
            except InvalidVersion:
                logger.debug(
                    "Could not parse '%s' as a version with a local version identifier.",
                    trimmed,
                    exc_info=True,
                )

    return None


def _get_release_tuple(version: Optional[str]) -> Optional[tuple[int, ...]]:
    """
    Get the numeric release tuple (major, minor, patch, ...) from a version string.

    Parameters:
        version (Optional[str]): Version string to parse (may include a leading "v").

    Returns:
        Optional[tuple[int, ...]]: A tuple of integer release components (e.g., (1, 2, 3)) when the version can be interpreted as a numeric release, or `None` if the input is empty or cannot be parsed.
    """
    if version is None:
        return None

    version_stripped = version.strip()
    if not version_stripped:
        return None

    normalized = _normalize_version(version_stripped)
    if isinstance(normalized, Version) and normalized.release:
        return normalized.release

    base = (
        version_stripped[1:]
        if version_stripped.lower().startswith("v")
        else version_stripped
    )
    match = VERSION_BASE_RX.match(base)
    if match:
        return tuple(int(part) for part in match.group(1).split("."))

    return None


def _summarise_release_scan(kind: str, total_found: int, keep_limit: int) -> str:
    """
    Create a concise log message describing how many releases will be scanned.

    Parameters:
        kind (str): Type of releases (e.g., "firmware" or "apk").
        total_found (int): Total number of releases discovered.
        keep_limit (int): Maximum number of newest releases to scan/keep.

    Returns:
        str: A human-readable message like "Found <total_found> <kind> releases; scanning newest <scan_count>"
             with an appended " (keep limit <keep_limit>)" when the keep limit exceeds the scan count.
    """

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
    common non-PEP-440 forms (e.g., Meshtastic tags like "v2.7.8.a0c0388" → "2.7.8+a0c0388",
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

    v1 = _normalize_version(version1)
    v2 = _normalize_version(version2)
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


def cleanup_superseded_prereleases(
    download_dir, latest_release_tag
):  # log_message_func parameter removed
    """
    Remove prerelease firmware directories when official releases are available to avoid confusion and save space.

    Scans download_dir/firmware/prerelease for directories named "firmware-<version>" (optionally including a commit/hash suffix).
    For any prerelease that matches the base version of an official release, this function will remove the prerelease directory
    since the official release is generally preferred for production use.

    Note: Prereleases and official releases are created independently from the same commit but packaged differently.
    This cleanup is performed for user convenience and storage management, not because prereleases are "promoted" to releases.

    Invalidly formatted prerelease directory names (not matching VERSION_REGEX_PATTERN) are skipped.

    Parameters:
        download_dir (str): Base download directory containing firmware/{prerelease, <tag>}.
        latest_release_tag (str): Latest official release tag (may include a leading 'v').

    Returns:
        bool: True if one or more prerelease directories were removed; False otherwise.
    """
    # Removed local log_message_func definition

    # Strip the 'v' prefix if present
    safe_latest_release_tag = _sanitize_path_component(latest_release_tag)
    if safe_latest_release_tag is None:
        logger.warning(
            "Unsafe latest release tag provided (%s); skipping promoted prerelease check",
            latest_release_tag,
        )
        return False

    latest_release_version = safe_latest_release_tag.lstrip("v")
    latest_release_tuple = _get_release_tuple(latest_release_version)
    v_latest_norm = _normalize_version(latest_release_version)

    # This function cleans up prereleases superseded by an official release.
    # If the latest release is itself a prerelease, no superseding has occurred.
    if getattr(v_latest_norm, "is_prerelease", False):
        logger.debug(
            "Skipping prerelease cleanup; latest release '%s' is a prerelease.",
            safe_latest_release_tag,
        )
        return False

    # Path to prerelease directory
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    if not os.path.exists(prerelease_dir):
        return False

    # Check for matching pre-release directories
    cleaned_up = False
    for raw_dir_name in os.listdir(prerelease_dir):
        if raw_dir_name.startswith("firmware-"):
            dir_name = _sanitize_path_component(raw_dir_name)
            if dir_name is None:
                logger.warning(
                    "Skipping unsafe prerelease directory encountered during cleanup: %s",
                    raw_dir_name,
                )
                continue

            dir_version = dir_name[9:]  # Remove 'firmware-' prefix

            # Validate version format before processing (hash part is optional)
            if not re.match(VERSION_REGEX_PATTERN, dir_version):
                logger.warning(
                    f"Invalid version format in prerelease directory {dir_name}, skipping"
                )
                continue

            # If this pre-release matches the latest release version
            prerelease_path = os.path.join(prerelease_dir, dir_name)
            dir_release_tuple = _get_release_tuple(dir_version)

            # Determine if this prerelease should be cleaned up
            should_cleanup = False
            cleanup_reason = ""

            can_compare_tuples = (
                latest_release_tuple
                and dir_release_tuple
                and not getattr(v_latest_norm, "is_prerelease", False)
            )

            if can_compare_tuples:
                if dir_release_tuple > latest_release_tuple:
                    logger.debug(
                        "Skipping prerelease %s; version is newer than latest release %s",
                        dir_name,
                        safe_latest_release_tag,
                    )
                    continue
                # Prerelease is older or same version, so it's superseded.
                should_cleanup = True
                cleanup_reason = (
                    f"it is superseded by release {safe_latest_release_tag}"
                )
            elif dir_version == latest_release_version:
                # Fallback to exact string match if we can't compare tuples.
                should_cleanup = True
                cleanup_reason = (
                    f"it has the same version as release {safe_latest_release_tag}"
                )
            else:
                # Can't compare and versions are not identical, so we keep it to be safe.
                continue

            if should_cleanup:
                logger.info(
                    "Removing prerelease %s because %s.", dir_name, cleanup_reason
                )
                if _safe_rmtree(prerelease_path, prerelease_dir, dir_name):
                    cleaned_up = True
                continue

    # Reset tracking info if no prerelease directories exist
    if os.path.exists(prerelease_dir):
        # Check if any prerelease directories remain
        remaining_prereleases = bool(_get_existing_prerelease_dirs(prerelease_dir))
        if not remaining_prereleases:
            # Remove tracking files since no prereleases remain
            json_tracking_file = os.path.join(
                prerelease_dir, "prerelease_tracking.json"
            )
            text_tracking_file = os.path.join(prerelease_dir, "prerelease_commits.txt")

            for tracking_file in [json_tracking_file, text_tracking_file]:
                if os.path.exists(tracking_file):
                    try:
                        os.remove(tracking_file)
                        logger.debug(
                            "Removed prerelease tracking file: %s", tracking_file
                        )
                    except OSError as e:
                        logger.warning(
                            "Could not remove tracking file %s: %s", tracking_file, e
                        )

    return cleaned_up


def _atomic_write(
    file_path: str, writer_func: Callable[[IO[str]], None], suffix: str
) -> bool:
    """
    Write text to a file atomically by writing to a temporary file in the same directory and replacing the target on success.

    Parameters:
        file_path (str): Destination path to write.
        writer_func (Callable[[IO[str]], None]): Callable that receives an open text file-like object (UTF-8) and writes the desired content to it.
        suffix (str): Suffix to use for the temporary file (for example, ".json" or ".txt").

    Returns:
        bool: `True` if the content was written and the temporary file atomically replaced the target; `False` otherwise.
    """
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(file_path), prefix="tmp-", suffix=suffix
        )
    except OSError as e:
        logger.error(f"Could not create temporary file for {file_path}: {e}")
        return False
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
            writer_func(temp_f)
        os.replace(temp_path, file_path)
    except (IOError, UnicodeEncodeError, OSError) as e:
        logger.error(f"Could not write to {file_path}: {e}")
        return False
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return True


def _atomic_write_text(file_path: str, content: str) -> bool:
    """
    Atomically write text content to a file with a ".txt" temporary suffix.

    Writes `content` to `file_path` by delegating to the atomic writer helper; the write is performed to a temporary file and renamed into place to avoid partial writes. Returns True on success and False on error.
    """
    return _atomic_write(file_path, lambda f: f.write(content), suffix=".txt")


def _atomic_write_json(file_path: str, data: dict) -> bool:
    """
    Atomically write a Python mapping to a JSON file.

    Writes `data` (must be JSON-serializable) to `file_path` using a temporary file and an atomic rename, ensuring the target file is never left in a partially-written state. The JSON is written with an indentation of 2 spaces and the helper enforces a ".json" suffix for the temporary file.

    Parameters:
        file_path (str): Destination path for the JSON file.
        data (dict): Mapping to serialize to JSON.

    Returns:
        bool: True on successful write, False on error.
    """
    return _atomic_write(
        file_path, lambda f: json.dump(data, f, indent=2), suffix=".json"
    )


def _sanitize_path_component(component: Optional[str]) -> Optional[str]:
    """
    Return a filesystem-safe single path component or None if the input is unsafe.

    The function accepts a string (or None) and returns a trimmed component that is safe
    to use as a single path segment. It returns None for unsafe inputs, including:
    - None or empty strings after trimming
    - "." or ".."
    - absolute paths
    - strings containing a null byte
    - strings containing path separators (os.sep or os.altsep)

    Returns:
        The sanitized component string, or None when the input is unsafe.
    """

    if component is None:
        return None

    sanitized = component.strip()
    if not sanitized or sanitized in {".", ".."}:
        return None

    if os.path.isabs(sanitized):
        return None

    if "\x00" in sanitized:
        return None

    for separator in (os.sep, os.altsep):
        if separator and separator in sanitized:
            return None

    return sanitized


def _safe_rmtree(path_to_remove: str, base_dir: str, item_name: str) -> bool:
    """
    Safely remove a filesystem path (file or directory), preventing symlink traversal outside a permitted base directory.

    If the target is a symlink it is unlinked immediately. Otherwise the function resolves the real path and ensures it lies under base_dir before removing; directories are removed with shutil.rmtree and files with os.remove. item_name is used only for log messages. Returns True when removal succeeded, False on any error or when the resolved path is outside base_dir.
    """
    try:
        if os.path.islink(path_to_remove):
            logger.info(f"Removing symlink: {item_name}")
            os.unlink(path_to_remove)
            return True

        real_target = os.path.realpath(path_to_remove)
        real_base_dir = os.path.realpath(base_dir)

        try:
            common_base = os.path.commonpath([real_base_dir, real_target])
        except ValueError:
            common_base = None

        if common_base != real_base_dir:
            logger.warning(
                f"Skipping removal of {path_to_remove} because it resolves outside the base directory"
            )
            return False

        if os.path.isdir(path_to_remove):
            shutil.rmtree(path_to_remove)
        else:
            os.remove(path_to_remove)
    except OSError as e:
        logger.error(f"Error removing {path_to_remove}: {e}")
        return False
    else:
        logger.info(f"Removed path: {path_to_remove}")
        return True


def compare_file_hashes(file1, file2):
    """
    Compare two files by computing their SHA-256 hashes.

    Reads each file in 4KB chunks and returns True if both files can be read and their SHA-256 digests are identical. If either file does not exist or cannot be read, the function returns False.

    Parameters:
        file1, file2 (str): Paths to the two files to compare.

    Returns:
        bool: True when both files were successfully read and their SHA-256 hashes match; False otherwise.
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


def _read_text_tracking_file(tracking_file):
    """
    Read legacy text-format prerelease tracking data.

    Looks for a sibling file named "prerelease_commits.txt" next to the provided
    tracking_file path and parses it. Supported formats:
    - Modern text format: first non-empty line begins with "Release: <tag>", subsequent
      lines are commit hashes.
    - Legacy format: every non-empty line is treated as a commit and the release is
      reported as "unknown".

    Parameters:
        tracking_file (str): Path to the (JSON) tracking file used to locate the
            sibling "prerelease_commits.txt" file.

    Returns:
        tuple[list[str], str | None]: (commits, current_release)
            - commits: list of commit hashes (empty if file missing or unreadable).
            - current_release: release tag if present, "unknown" for legacy format,
              or None if the text file is missing/unreadable.
    """
    try:
        text_file = os.path.join(
            os.path.dirname(tracking_file), "prerelease_commits.txt"
        )
        with open(text_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
            if not lines:
                return [], None
            if lines[0].startswith("Release: "):
                current_release = lines[0][9:]  # Remove "Release: " prefix
                commits = lines[1:]  # Rest are commit hashes
                return commits, current_release
            # Legacy format: treat all lines as commits; release unknown
            return lines, "unknown"
    except (IOError, UnicodeDecodeError) as e:
        logger.debug(f"Could not read legacy prerelease tracking file: {e}")

    return [], None


def _read_prerelease_tracking_data(tracking_file):
    """
    Read prerelease tracking data from JSON, falling back to the legacy text format.

    Parses prerelease_tracking.json at tracking_file and returns a tuple (commits, current_release).
    If the JSON file is missing or cannot be parsed, falls back to the legacy text reader
    (_read_text_tracking_file) which reads prerelease_commits.txt-style data. Returns an empty
    commit list and None for the release when no valid tracking information is available.

    Returns:
        tuple: (commits, current_release)
            commits (list[str]): Ordered list of prerelease commit hashes (may be empty).
            current_release (str | None): Release tag associated with the commits, or None.
    """
    commits = []
    current_release = None
    read_from_json_success = False

    if os.path.exists(tracking_file):
        try:
            with open(tracking_file, "r", encoding="utf-8") as f:
                tracking_data = json.load(f)
                current_release = tracking_data.get("release")
                commits = tracking_data.get("commits", [])
            read_from_json_success = True
        except (IOError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Could not read prerelease tracking file: {e}")

    if not read_from_json_success:
        # Fallback to legacy text format if JSON read failed or file didn't exist
        commits, current_release = _read_text_tracking_file(tracking_file)

    return commits, current_release


def _extract_commit_from_dir_name(dir_name: str) -> Optional[str]:
    """
    Extract a commit hash from a prerelease directory name.

    Recognizes a trailing hex commit fragment of 6–12 characters in names like
    "firmware-2.7.7.abcdef" or "firmware-2.7.7.abcdef-extra". Returns the hex
    string normalized to lowercase, or None if no commit-like fragment is found.
    """
    commit_match = re.search(r"\.([a-f0-9]{6,12})(?:[.-]|$)", dir_name, re.IGNORECASE)
    if commit_match:
        return commit_match.group(1).lower()  # Normalize to lowercase
    else:
        logger.debug(f"Could not extract commit hash from directory name: '{dir_name}'")
        return None


def _get_existing_prerelease_dirs(prerelease_dir: str) -> list[str]:
    """
    Return safe prerelease directory names directly under ``prerelease_dir``.

    Symlinks are ignored to avoid traversing outside the managed prerelease tree.
    """

    if not os.path.exists(prerelease_dir):
        return []

    entries: list[str] = []
    try:
        with os.scandir(prerelease_dir) as iterator:
            for entry in iterator:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if not entry.name.startswith("firmware-"):
                    continue
                safe_name = _sanitize_path_component(entry.name)
                if safe_name is None:
                    logger.warning(
                        "Ignoring unsafe prerelease directory name: %s", entry.name
                    )
                    continue
                entries.append(safe_name)
    except OSError as e:
        logger.debug(f"Error scanning prerelease dir {prerelease_dir}: {e}")

    return entries


def _get_prerelease_patterns(config: dict) -> list[str]:
    """
    Return the list of file-selection patterns used for prerelease assets.

    Prefers the new SELECTED_PRERELEASE_ASSETS key from config. If that key is absent,
    falls back to the legacy EXTRACT_PATTERNS key (and logs a deprecation warning).
    Always returns a list (empty if no patterns are configured).

    Parameters:
        config (dict): Configuration mapping that may contain SELECTED_PRERELEASE_ASSETS
            or the legacy EXTRACT_PATTERNS key.
    """
    # Check for new dedicated configuration key first
    if "SELECTED_PRERELEASE_ASSETS" in config:
        return config["SELECTED_PRERELEASE_ASSETS"] or []

    # Fall back to EXTRACT_PATTERNS for backward compatibility
    extract_patterns = config.get("EXTRACT_PATTERNS", [])
    if extract_patterns:
        logger.warning(
            "Using EXTRACT_PATTERNS for prerelease file selection is deprecated. "
            "Please re-run 'fetchtastic setup' to update your configuration."
        )

    return extract_patterns


def update_prerelease_tracking(prerelease_dir, latest_release_tag, current_prerelease):
    """
    Update or create prerelease_tracking.json to record a single prerelease commit.

    This is a convenience wrapper around `batch_update_prerelease_tracking` for handling
    a single prerelease directory. It provides the same functionality with a simpler
    interface for single-directory updates.

    Parameters:
        prerelease_dir (str): Path to the prerelease directory (parent for prerelease_tracking.json).
        latest_release_tag (str): Latest official release tag used to determine whether to reset tracking.
        current_prerelease (str): Name of the current prerelease directory (used to extract the prerelease commit).

    Returns:
        int: Number of tracked prerelease commits for the current release (1-based count). On write failure the function logs an error and returns 1.
    """
    return batch_update_prerelease_tracking(
        prerelease_dir, latest_release_tag, [current_prerelease]
    )


def batch_update_prerelease_tracking(
    prerelease_dir, latest_release_tag, prerelease_dirs
):
    """
    Update prerelease_tracking.json by adding any new commit hashes found in the provided prerelease directory names and (if the official release tag changed) reset tracked commits to start from the new release.

    If prerelease_dirs is empty this is a no-op and returns 0. Reads existing tracking from prerelease_tracking.json (falling back to legacy text tracking), appends any previously-untracked commit hashes extracted from names like "firmware-1.2.3.<commit>", and writes a single updated prerelease_tracking.json containing keys "release", "commits", and "last_updated".

    Parameters:
        prerelease_dir (str): Directory containing prerelease_tracking.json.
        latest_release_tag (str): Current official release tag; when this differs from the stored release the tracked commits are reset.
        prerelease_dirs (list[str]): Prerelease directory names to scan for commit hashes; entries without a commit are ignored.

    Returns:
        int: Total number of tracked prerelease commits after the update.
             Returns 0 immediately if prerelease_dirs is empty.
             Returns 1 if writing the tracking file fails (fallback value).
    """
    if not prerelease_dirs:
        return 0

    tracking_file = os.path.join(prerelease_dir, "prerelease_tracking.json")

    # Extract commit hashes from all prerelease directory names (filter out None values)
    new_commits = [
        commit
        for pr_dir in prerelease_dirs
        if (commit := _extract_commit_from_dir_name(pr_dir))
    ]

    # Read existing tracking data using helper function
    commits, current_release = _read_prerelease_tracking_data(tracking_file)

    # If release changed, reset the commit list
    if current_release != latest_release_tag:
        logger.info(
            f"New release detected ({latest_release_tag}), resetting prerelease tracking"
        )
        commits = []
        current_release = latest_release_tag

    # Add all new commits that aren't already tracked
    # Use set for O(1) lookup performance instead of O(n) list lookup
    tracked_commits_set = set(commits)
    newly_added_commits = [
        c for c in dict.fromkeys(new_commits) if c not in tracked_commits_set
    ]
    for commit_hash in newly_added_commits:
        logger.info(f"Added prerelease commit {commit_hash} to tracking")
    commits.extend(newly_added_commits)
    added_count = len(newly_added_commits)

    # Write updated tracking data only once
    tracking_data = {
        "release": current_release,
        "commits": commits,
        "last_updated": datetime.now().astimezone().isoformat(),
    }
    if not _atomic_write_json(tracking_file, tracking_data):
        return 1  # Default to 1 if we can't track

    prerelease_number = len(commits)
    if added_count > 0:
        logger.info(f"Batch updated {added_count} prerelease commit(s)")
        logger.info(f"Prerelease #{prerelease_number} since {current_release}")
    else:
        logger.debug(
            "Prerelease tracking unchanged (#%s since %s)",
            prerelease_number,
            current_release,
        )
    return prerelease_number


def matches_extract_patterns(filename, extract_patterns, device_manager=None):
    """
    Return True if the given filename matches any of the configured extract patterns.

    Matches are case-insensitive and support several pattern styles:
    - Exact substring patterns (default).
    - File-type prefixes (from FILE_TYPE_PREFIXES): treated as file-type patterns and matched by substring.
    - Device patterns: recognized either by trailing '-'/'_' or via device_manager.is_device_pattern(); these match when the device name appears anywhere in the filename (e.g., 'tbeam-' matches 'firmware-tbeam-...' and 'littlefs-tbeam-...').
    - Special-case 'littlefs-' pattern matches filenames starting with 'littlefs-'.

    Parameters:
        filename (str): The file name to test.
        extract_patterns (Iterable[str]): Patterns from configuration to match against.

    Returns:
        bool: True if any pattern matches the filename, False otherwise.
    """
    # Known file type prefixes that indicate this is a file type pattern, not device pattern
    file_type_prefixes = FILE_TYPE_PREFIXES

    # Convert filename to lowercase for case-insensitive matching
    filename_lower = filename.lower()

    for pattern in extract_patterns:
        # Convert pattern to lowercase for case-insensitive matching
        pattern_lower = pattern.lower()

        # Handle special case: generic 'littlefs-' pattern
        if pattern_lower == "littlefs-":
            if filename_lower.startswith("littlefs-"):
                return True
            continue

        # File type patterns: exact substring matching
        if any(pattern_lower.startswith(prefix) for prefix in file_type_prefixes):
            if pattern_lower in filename_lower:
                return True
            continue

        # Determine if it's a device pattern
        is_device_pattern_match = False
        if device_manager and device_manager.is_device_pattern(pattern):
            is_device_pattern_match = True
        # Fallback for patterns ending with '-' or '_' (likely device patterns)
        elif pattern_lower.endswith(("-", "_")):
            is_device_pattern_match = True

        if is_device_pattern_match:
            clean_pattern = pattern_lower.rstrip("-_ ")
            # For very short patterns (1-2 chars), require exact word boundaries to avoid false positives
            if len(clean_pattern) <= 2:
                if re.search(rf"\b{re.escape(clean_pattern)}\b", filename_lower):
                    return True
            else:
                # For longer patterns, use the original boundary logic
                if re.search(
                    rf"(^|[-_]){re.escape(clean_pattern)}([-_]|$)", filename_lower
                ):
                    return True
            # It was identified as a device pattern but didn't match strictly.
            # Do not fall through to generic substring match.
            continue

        # Fallback: simple substring matching for any other patterns
        if pattern_lower in filename_lower:
            return True

    return False


def get_prerelease_tracking_info(prerelease_dir):
    """
    Return prerelease tracking information gathered from prerelease_tracking.json or a legacy text file.

    Attempts to read prerelease_tracking.json inside prerelease_dir first; if the JSON file is missing or unreadable, falls back to the legacy prerelease_commits.txt parser via _read_text_tracking_file. This function always returns a dict and does not raise.

    Parameters:
        prerelease_dir (str): Directory that may contain prerelease tracking files; may not exist.

    Returns:
        dict: Empty dict if no tracking data is available; otherwise contains:
            - release (str): Tracked official release tag or "unknown" when not recorded.
            - commits (list[str]): Tracked prerelease commit identifiers (may be empty).
            - prerelease_count (int): Number of tracked prerelease commits.
            - last_updated (str|None): ISO timestamp present only when loaded from JSON tracking file.
    """
    # Try JSON format first
    json_tracking_file = os.path.join(prerelease_dir, "prerelease_tracking.json")
    if os.path.exists(json_tracking_file):
        try:
            with open(json_tracking_file, "r", encoding="utf-8") as f:
                tracking_data = json.load(f)
                return {
                    "release": tracking_data.get("release", "unknown"),
                    "commits": tracking_data.get("commits", []),
                    "prerelease_count": len(tracking_data.get("commits", [])),
                    "last_updated": tracking_data.get("last_updated"),
                }
        except (IOError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Could not read JSON prerelease tracking file: {e}")

    # Fall back to text format using helper function
    commits, release = _read_text_tracking_file(json_tracking_file)
    if commits or release:
        return {
            "release": release or "unknown",
            "commits": commits,
            "prerelease_count": len(commits),
        }

    return {}


def _iter_matching_prerelease_files(
    dir_name: str,
    selected_patterns: list,
    exclude_patterns_list: list,
    device_manager,
) -> List[Dict[str, str]]:
    """
    Return a list of prerelease assets in a directory that match selection rules.

    Scans the remote directory named by dir_name (via menu_repo), filters entries by
    selected_patterns (using matches_extract_patterns, which applies device-aware
    matching when a DeviceHardwareManager is provided), and excludes any filenames
    matching patterns in exclude_patterns_list. Filenames that are unsafe for use
    as a single path component are skipped.

    Parameters:
        dir_name (str): Remote prerelease directory to inspect.
        selected_patterns (list): Patterns used to select matching assets.
        exclude_patterns_list (list): fnmatch-style patterns; any match causes an asset to be skipped.
        device_manager: Optional device manager used by matches_extract_patterns for device-specific pattern handling.

    Returns:
        List[Dict[str, str]]: A list of dicts with keys:
            - "name": sanitized filename (safe single path component)
            - "download_url": URL string for downloading the asset
    """

    files = menu_repo.fetch_directory_contents(dir_name) or []
    matching: List[Dict[str, str]] = []
    for entry in files:
        file_name = entry.get("name")
        download_url = entry.get("download_url")
        if not file_name or not download_url:
            continue

        safe_file_name = _sanitize_path_component(file_name)
        if safe_file_name is None:
            logger.warning(
                "Skipping prerelease asset with unsafe name %s in %s",
                file_name,
                dir_name,
            )
            continue

        # matches_extract_patterns keeps prerelease filtering aligned with extraction rules,
        # including device-aware patterns resolved via DeviceHardwareManager.
        if not matches_extract_patterns(file_name, selected_patterns, device_manager):
            continue

        if any(
            fnmatch.fnmatch(file_name, pattern) for pattern in exclude_patterns_list
        ):
            logger.debug(
                "Skipping pre-release file %s (matched exclude pattern)",
                file_name,
            )
            continue

        matching.append({"name": safe_file_name, "download_url": download_url})

    return matching


def _prepare_for_redownload(file_path: str) -> bool:
    """
    Prepare a file for re-download by removing the existing file, its associated hash file (via `get_hash_file_path`), and any orphaned temporary files matching `<file_path>.tmp.*`.

    Returns:
        bool: True if all removals succeeded (or nothing needed removal), False if an OS error occurred while attempting removals.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug(f"Removed existing file: {file_path}")

        hash_path = get_hash_file_path(file_path)
        if os.path.exists(hash_path):
            os.remove(hash_path)
            logger.debug(f"Removed stale hash file: {hash_path}")

        # Also remove any orphaned temp files from previous runs
        for tmp_path in glob.glob(f"{glob.escape(file_path)}.tmp.*"):
            os.remove(tmp_path)
            logger.debug(f"Removed orphaned temp file: {tmp_path}")
    except OSError as e:
        logger.error(f"Error preparing for re-download of {file_path}: {e}")
        return False
    else:
        return True


def _prerelease_needs_download(file_path: str) -> bool:
    """
    Determine whether a prerelease file at `file_path` needs to be (re)downloaded.

    Checks for existence and verifies integrity. Returns True when the file is missing
    or fails integrity validation and has been prepared for re-download. If integrity
    fails but preparation for re-download does not succeed, returns False.

    Parameters:
        file_path (str): Path to the local prerelease asset file.

    Returns:
        bool: True if the caller should download the file; False otherwise.
    """
    if not os.path.exists(file_path):
        return True

    if verify_file_integrity(file_path):
        return False

    logger.warning(
        f"Existing prerelease file {os.path.basename(file_path)} failed integrity check; re-downloading"
    )
    if not _prepare_for_redownload(file_path):
        return False
    return True


def check_for_prereleases(
    download_dir,
    latest_release_tag,
    selected_patterns,
    exclude_patterns=None,  # log_message_func parameter removed
    device_manager=None,
):
    """
    Discover prerelease firmware that are newer than the provided official release tag and download assets matching the given selection patterns into download_dir/firmware/prerelease.

    If latest_release_tag is unsafe (fails sanitization) the call is a no-op and returns (False, []). The function also updates local prerelease tracking and prunes older prerelease directories as part of maintaining the prerelease download area.

    Parameters:
        download_dir (str): Base directory under which prerelease assets are stored.
        latest_release_tag (str): Official release tag used as the cutoff; prereleases considered must be newer than this tag.
        selected_patterns (Iterable[str]): Patterns used to select which prerelease assets to download.
        exclude_patterns (Iterable[str] | None): Optional patterns to exclude assets from selection.
        device_manager: Optional device manager used for device-specific pattern matching (omitted from detailed docs as a common service).

    Returns:
        tuple[bool, list[str]]: (downloaded, versions)
            - downloaded: `True` if at least one prerelease asset was downloaded during this run, `False` otherwise.
            - versions: List of prerelease directory names that were downloaded or tracked; empty if none.
    """

    raw_latest_release_tag = latest_release_tag
    latest_release_tag = _sanitize_path_component(latest_release_tag)
    if latest_release_tag is None:
        logger.warning(
            "Unsafe latest release tag provided to check_for_prereleases: %s",
            raw_latest_release_tag,
        )
        return False, []

    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    tracking_info = get_prerelease_tracking_info(prerelease_dir)
    tracked_release = tracking_info.get("release")

    if tracked_release and tracked_release != latest_release_tag:
        logger.info(
            f"New release {latest_release_tag} detected (previously tracking {tracked_release})."
            " Cleaning pre-release directory."
        )
        if os.path.exists(prerelease_dir):
            try:
                with os.scandir(prerelease_dir) as iterator:
                    for entry in iterator:
                        if entry.name in (
                            "prerelease_tracking.json",
                            "prerelease_commits.txt",
                        ) and entry.is_file(follow_symlinks=False):
                            continue
                        _safe_rmtree(entry.path, prerelease_dir, entry.name)
            except OSError as e:
                logger.warning(
                    f"Error cleaning prerelease directory {prerelease_dir}: {e}"
                )

        tracking_file = os.path.join(prerelease_dir, "prerelease_tracking.json")
        os.makedirs(prerelease_dir, exist_ok=True)
        if not _atomic_write_json(
            tracking_file,
            {
                "release": latest_release_tag,
                "commits": [],
                "last_updated": datetime.now().astimezone().isoformat(),
            },
        ):
            logger.debug(f"Could not reset prerelease tracking file: {tracking_file}")

    def extract_version(dir_name: str) -> str:
        """Return the portion after the 'firmware-' prefix in prerelease directory names."""
        return dir_name[9:] if dir_name.startswith("firmware-") else dir_name

    exclude_patterns_list = exclude_patterns or []
    latest_release_version = latest_release_tag.lstrip("v")
    latest_release_tuple = _get_release_tuple(latest_release_version)
    v_latest_norm = _normalize_version(latest_release_version)

    directories = menu_repo.fetch_repo_directories()
    if not directories:
        logger.info("No firmware directories found in the repository.")
        return False, []

    repo_prerelease_dirs: List[str] = []
    for raw_dir_name in directories:
        if not raw_dir_name.startswith("firmware-"):
            continue

        dir_name = _sanitize_path_component(raw_dir_name)
        if dir_name is None:
            logger.warning(
                "Skipping unsafe prerelease directory name from repository: %s",
                raw_dir_name,
            )
            continue

        dir_version = extract_version(dir_name)
        if not re.match(VERSION_REGEX_PATTERN, dir_version):
            logger.debug(
                "Repository prerelease directory %s uses a non-standard version format; attempting best-effort comparison",
                dir_name,
            )

        dir_release_tuple = _get_release_tuple(dir_version)
        # Only use tuple comparison if latest release is not a prerelease
        if (
            latest_release_tuple
            and dir_release_tuple
            and dir_release_tuple <= latest_release_tuple
            and not getattr(v_latest_norm, "is_prerelease", False)
        ):
            logger.debug(
                "Skipping prerelease %s; version %s is not newer than latest release %s",
                dir_name,
                dir_version,
                latest_release_tag,
            )
            continue

        try:
            if compare_versions(dir_version, latest_release_version) > 0:
                repo_prerelease_dirs.append(dir_name)
        except (InvalidVersion, ValueError, TypeError) as exc:
            logger.warning(
                f"Could not compare prerelease version {dir_version} against {latest_release_version}: {exc}"
            )

    if repo_prerelease_dirs:
        repo_prerelease_dirs = sorted(
            repo_prerelease_dirs,
            key=cmp_to_key(
                lambda a, b: compare_versions(extract_version(a), extract_version(b))
            ),
            reverse=True,
        )

    # Process all available prereleases to detect hash changes
    target_prereleases = repo_prerelease_dirs

    os.makedirs(prerelease_dir, exist_ok=True)
    # Resolve once for containment checks
    real_prerelease_base = os.path.realpath(prerelease_dir)

    # Remove stray files and symlinks while preserving tracking files
    try:
        with os.scandir(prerelease_dir) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    continue
                if entry.is_symlink():
                    logger.warning(
                        "Removing symlink in prerelease dir to prevent traversal: %s",
                        entry.name,
                    )
                    _safe_rmtree(entry.path, prerelease_dir, entry.name)
                    continue
                if not entry.is_dir() and entry.name not in (
                    "prerelease_tracking.json",
                    "prerelease_commits.txt",
                ):
                    try:
                        os.remove(entry.path)
                        logger.info(
                            f"Removed stale file from prerelease directory: {entry.name}"
                        )
                    except OSError as e:
                        logger.warning(
                            f"Error removing stale file {entry.path} from prerelease directory: {e}"
                        )
    except OSError as e:
        logger.debug(f"Error scanning prerelease dir {prerelease_dir} for cleanup: {e}")

    existing_prerelease_dirs = _get_existing_prerelease_dirs(prerelease_dir)

    # If no prereleases found in repo, clean up existing ones and exit
    if not target_prereleases:
        for dir_name in existing_prerelease_dirs:
            dir_path = os.path.join(prerelease_dir, dir_name)
            _safe_rmtree(dir_path, prerelease_dir, dir_name)
        return False, []

    # Get the newest prerelease from repo (first in sorted list)
    newest_repo_prerelease = target_prereleases[0]
    newest_version = extract_version(newest_repo_prerelease)
    newest_hash = _extract_commit_from_dir_name(newest_repo_prerelease)

    # Find existing prerelease with same version (if any)
    existing_same_version = None
    existing_hash = None

    for dir_name in existing_prerelease_dirs:
        existing_version = extract_version(dir_name)
        if existing_version == newest_version:
            existing_same_version = dir_name
            existing_hash = _extract_commit_from_dir_name(dir_name)
            break

    # Determine if we need to download (new version or same version with different hash)
    should_download = True
    if existing_same_version and existing_hash == newest_hash:
        should_download = False
        logger.info(
            f"Prerelease {newest_version} with hash {newest_hash} already exists, no update needed"
        )

    # Clean up old prereleases that don't match the newest version
    for dir_name in existing_prerelease_dirs:
        existing_version = extract_version(dir_name)
        if existing_version != newest_version:
            dir_path = os.path.join(prerelease_dir, dir_name)
            _safe_rmtree(dir_path, prerelease_dir, dir_name)
            logger.info(
                f"Removed old prerelease {dir_name} (version {existing_version})"
            )

    # Only proceed with download if we have a new prerelease or hash change
    if not should_download:
        # Still need to update tracking for existing prereleases
        if target_prereleases:
            batch_update_prerelease_tracking(
                prerelease_dir, latest_release_tag, target_prereleases
            )
        return False, [newest_repo_prerelease]

    # Set target to only the newest prerelease for downloading
    target_prereleases = [newest_repo_prerelease]

    downloaded_files: List[str] = []

    for dir_name in target_prereleases:
        dir_path = os.path.join(prerelease_dir, dir_name)
        # Do not allow the target prerelease directory itself to be a symlink or a non-directory
        if os.path.islink(dir_path) or (
            os.path.exists(dir_path) and not os.path.isdir(dir_path)
        ):
            logger.warning(
                "Prerelease entry is not a real directory (%s); removing to avoid escaping base",
                dir_name,
            )
            if not _safe_rmtree(dir_path, prerelease_dir, dir_name):
                logger.error("Could not safely remove %s; skipping", dir_name)
                continue
        try:
            os.makedirs(dir_path, exist_ok=True)
        except OSError as e:
            logger.error(
                f"Error creating directory for pre-release {dir_name} at {dir_path}: {e}"
            )
            continue

        matching_files = _iter_matching_prerelease_files(
            dir_name, selected_patterns, exclude_patterns_list, device_manager
        )
        if not matching_files:
            logger.debug(
                f"No prerelease files matched selection for {dir_name}; skipping downloads"
            )
            continue

        for file_info in matching_files:
            file_name = file_info["name"]
            download_url = file_info["download_url"]
            file_path = os.path.join(dir_path, file_name)
            # Containment check: ensure resolved path stays within prerelease base
            try:
                common_base = os.path.commonpath(
                    [real_prerelease_base, os.path.realpath(file_path)]
                )
            except ValueError:
                common_base = None
            if common_base != real_prerelease_base:
                logger.warning(
                    "Skipping pre-release file %s: resolved path escapes prerelease base",
                    file_name,
                )
                continue

            if not _prerelease_needs_download(file_path):
                logger.debug(
                    f"Pre-release file already present and verified: {file_name}"
                )
                continue

            try:
                logger.debug(
                    f"Downloading pre-release file: {file_name} from {download_url}"
                )
                if not download_file_with_retry(download_url, file_path):
                    continue

                if file_name.lower().endswith(SHELL_SCRIPT_EXTENSION.lower()):
                    try:
                        os.chmod(file_path, EXECUTABLE_PERMISSIONS)
                        logger.debug(
                            f"Set executable permissions for prerelease file {file_name}"
                        )
                    except OSError as e:
                        logger.warning(
                            f"Error setting executable permissions for {file_name}: {e}"
                        )

                downloaded_files.append(file_path)
            except requests.exceptions.RequestException as e:
                logger.error(
                    f"Network error downloading pre-release file {file_name} from {download_url}: {e}"
                )
            except IOError as e:
                logger.error(
                    f"File I/O error while downloading pre-release file {file_name} to {file_path}: {e}"
                )
            except Exception as e:  # noqa: BLE001 - unexpected errors
                logger.error(
                    f"Unexpected error downloading pre-release file {file_name}: {e}",
                    exc_info=True,
                )

    downloaded_versions: List[str] = []
    if downloaded_files:
        logger.info(f"Downloaded {len(downloaded_files)} new pre-release files.")
        files_by_dir: Dict[str, List[str]] = defaultdict(list)
        for path in downloaded_files:
            dir_name = os.path.basename(os.path.dirname(path))
            files_by_dir[dir_name].append(path)

        for version, files in files_by_dir.items():
            logger.info(f"Pre-release {version}: {len(files)} new file(s) downloaded")
            downloaded_versions.append(version)

    if target_prereleases:
        prerelease_number = batch_update_prerelease_tracking(
            prerelease_dir, latest_release_tag, target_prereleases
        )
        tracked_label = target_prereleases[0]
        if downloaded_files:
            logger.info(
                f"Downloaded prereleases tracked up to #{prerelease_number}: {tracked_label}"
            )
        else:
            logger.info(
                f"Tracked prereleases up to #{prerelease_number}: {tracked_label}"
            )

    if downloaded_files:
        return True, downloaded_versions
    if target_prereleases:
        return False, target_prereleases
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
    EXTRACT_PATTERNS (dual purpose: file extraction AND prerelease file selection), AUTO_EXTRACT,
    EXCLUDE_PATTERNS, CHECK_PRERELEASES.

    Returns a tuple of:
    - downloaded firmware versions (List[str]) — includes strings like "pre-release X.Y.Z" for prereleases,
    - newly detected release versions that were not previously recorded (List[str]),
    - list of dictionaries with details for failed downloads (List[Dict[str, str]]),
    - the latest firmware release tag string or None if no releases were found.
    """
    global downloads_skipped

    # Create device hardware manager for smart pattern matching
    device_manager = DeviceHardwareManager(
        enabled=config.get("DEVICE_HARDWARE_API", {}).get("enabled", True),
        cache_hours=config.get("DEVICE_HARDWARE_API", {}).get(
            "cache_hours", DEVICE_HARDWARE_CACHE_HOURS
        ),
        api_url=config.get("DEVICE_HARDWARE_API", {}).get(
            "api_url", DEVICE_HARDWARE_API_URL
        ),
    )
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
            cleaned_up: bool = cleanup_superseded_prereleases(
                paths_and_urls["download_dir"],
                latest_release_tag,  # logger.info removed
            )
            if cleaned_up:
                logger.info(
                    "Cleaned up pre-release(s) since official release(s) are available."
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
                        _get_prerelease_patterns(config),
                        exclude_patterns=config.get("EXCLUDE_PATTERNS", []),  # type: ignore
                        device_manager=device_manager,
                    )
                )
                if prerelease_found:
                    logger.info(
                        f"Pre-release firmware downloaded successfully: {', '.join(prerelease_versions)}"
                    )
                    version: str
                    for version in prerelease_versions:
                        downloaded_firmwares.append(f"pre-release {version}")
                elif prerelease_versions:
                    logger.info(
                        "Found an existing pre-release, but no new files to download."
                    )
                    # Don't add existing prereleases to downloaded_firmwares to avoid
                    # misleading "Downloaded..." notifications when no new files were downloaded
                else:
                    logger.info("No new pre-release firmware found.")

                # Display prerelease tracking information
                prerelease_dir = os.path.join(
                    paths_and_urls["download_dir"], "firmware", "prerelease"
                )
                tracking_info = get_prerelease_tracking_info(prerelease_dir)
                if tracking_info:
                    count = tracking_info.get("prerelease_count", 0)
                    base_version = tracking_info.get("release", "unknown")
                    # Only show count if there are actually prerelease directories present
                    if count > 0 and _get_existing_prerelease_dirs(prerelease_dir):
                        logger.info(f"Total prereleases since {base_version}: {count}")
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
    Prune versioned subdirectories under `directory`, keeping only the specified releases.

    Scans immediate child directories of `directory` and removes any subdirectory whose basename is not in `releases_to_keep` and not one of the internal exclusions ("repo-dls", "prerelease"). Deletion is performed with a safe removal helper that protects against symlink/traversal attacks; failures are logged but not propagated.

    Parameters:
        directory (str): Path whose immediate subdirectories represent versioned releases.
        releases_to_keep (List[str]): Basenames of subdirectories that must be preserved.
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
            if _safe_rmtree(version_path, directory, version):
                logger.info(f"Removed directory and its contents: {version_path}")


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
    Check releases for missing or corrupted assets, download matching assets, optionally extract ZIPs, and prune old release directories.

    Processes up to `versions_to_keep` newest entries from `releases`. For each release it:
    - Skips releases that are already complete.
    - Schedules and downloads assets that match `selected_patterns` (if provided) and do not match `exclude_patterns`.
    - Optionally extracts files from ZIP assets when `auto_extract` is True and `release_type == "Firmware"`, using `extract_patterns` to select files.
    - Writes release notes, sets executable bits on shell scripts, and prunes old release subdirectories outside the retention window.
    - Atomically updates `latest_release_file` when a newer release has been successfully processed.

    Side effects:
    - Creates per-release subdirectories and may write `latest_release_file` and release notes.
    - May remove corrupted ZIP files and delete older release directories.
    - Honors a global Wi‑Fi gating flag: if downloads are skipped globally, the function will not perform downloads and instead returns newer release tags.

    Parameters:
    - releases: List of release dictionaries (expected newest-first order) as returned by the API.
    - latest_release_file: Path to a file that stores the most recently recorded release tag.
    - release_type: Human-readable type used in logs and failure records (e.g., "Firmware" or "APK").
    - download_dir_path: Root directory where per-release subdirectories are created.
    - versions_to_keep: Number of newest releases to consider for download/retention.
    - extract_patterns: Patterns used to select files to extract from ZIP archives.
    - selected_patterns: Optional list of asset name patterns to include; if omitted all assets are considered.
    - auto_extract: When True and `release_type == "Firmware"`, perform extraction of matching ZIP contents.
    - exclude_patterns: Optional list of patterns; matching filenames are excluded from download and extraction.

    Returns:
    Tuple(downloaded_versions, new_versions_available, failed_downloads_details)
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

    real_download_base = os.path.realpath(download_dir_path)

    saved_release_tag: Optional[str] = None
    if os.path.exists(latest_release_file):
        try:
            with open(latest_release_file, "r") as f:
                saved_release_tag = _sanitize_path_component(f.read())
                if saved_release_tag is None:
                    logger.warning(
                        "Ignoring unsafe contents in latest release file %s",
                        latest_release_file,
                    )
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
            tag
            for rd in releases_to_download
            if (tag := _sanitize_path_component(rd.get("tag_name"))) is not None
        ]
        newer_tags: List[str] = _newer_tags_since_saved(tags_order, saved_release_tag)
        new_versions_available = list(dict.fromkeys(newer_tags))
        return (downloaded_versions, new_versions_available, failed_downloads_details)

    release_data: Dict[str, Any]
    for idx, release_data in enumerate(releases_to_download, start=1):
        try:
            raw_release_tag: str = release_data[
                "tag_name"
            ]  # Potential KeyError if API response changes
            release_tag = _sanitize_path_component(raw_release_tag)
            if release_tag is None:
                logger.warning(
                    "Skipping release with unsafe tag name: %s", raw_release_tag
                )
                continue
            if total_to_scan > 1:
                logger.debug(
                    "Checking %s (%d of %d)", raw_release_tag, idx, total_to_scan
                )
            logger.info("Checking %s…", raw_release_tag)
            release_dir: str = os.path.join(download_dir_path, release_tag)
            release_notes_file: str = os.path.join(
                release_dir, f"release_notes-{release_tag}.md"
            )

            if os.path.islink(release_dir) or (
                os.path.exists(release_dir) and not os.path.isdir(release_dir)
            ):
                logger.warning(
                    "Release entry is not a real directory (%s); removing to avoid escaping base",
                    raw_release_tag,
                )
                if not _safe_rmtree(release_dir, download_dir_path, release_tag):
                    logger.error(
                        "Could not safely remove %s; skipping", raw_release_tag
                    )
                    continue

            # Check if this release has already been downloaded and is complete
            if _is_release_complete(
                release_data, release_dir, selected_patterns, exclude_patterns_list
            ):
                logger.debug(
                    f"Release {raw_release_tag} already exists and is complete, skipping download"
                )
                # Update latest_release_file if this is the most recent release
                if (
                    release_tag != saved_release_tag
                    and release_data == releases_to_download[0]
                ):
                    if not _atomic_write_text(latest_release_file, release_tag):
                        logger.warning(
                            f"Error updating latest release file: {latest_release_file}"
                        )
                    else:
                        logger.debug(
                            f"Updated latest release tag to {release_tag} (complete release)"
                        )
                # Still add to new_versions_available if it's different from saved tag
                elif release_tag != saved_release_tag:
                    new_versions_available.append(release_tag)
                continue

            if not os.path.exists(release_dir):
                try:
                    os.makedirs(release_dir, exist_ok=True)
                except OSError as e:
                    logger.error(
                        f"Error creating release directory {release_dir}: {e}. Skipping version {raw_release_tag}."
                    )
                    continue  # Skip this release if its directory cannot be created

            if not os.path.exists(release_notes_file) and release_data.get("body"):
                logger.debug(
                    f"Downloading release notes for version {raw_release_tag}."
                )
                release_notes_content: str = strip_unwanted_chars(release_data["body"])
                try:
                    try:
                        notes_common = os.path.commonpath(
                            [real_download_base, os.path.realpath(release_notes_file)]
                        )
                    except ValueError:
                        notes_common = None

                    if notes_common != real_download_base:
                        logger.warning(
                            "Skipping write of release notes for %s: path escapes download base",
                            raw_release_tag,
                        )
                    else:
                        with open(
                            release_notes_file, "w", encoding="utf-8"
                        ) as notes_file:
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
                        f"Asset found with no name for release {raw_release_tag}. Skipping."
                    )
                    continue

                safe_file_name = _sanitize_path_component(file_name)
                if safe_file_name is None:
                    logger.warning(
                        "Skipping %s asset with unsafe filename %s for release %s",
                        release_type,
                        file_name,
                        raw_release_tag,
                    )
                    continue

                if file_name.lower().endswith(ZIP_EXTENSION.lower()):
                    asset_download_path: str = os.path.join(release_dir, safe_file_name)
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
                safe_file_name = _sanitize_path_component(file_name)
                if safe_file_name is None:
                    logger.warning(
                        "Skipping %s asset with unsafe filename %s for release %s",
                        release_type,
                        file_name,
                        raw_release_tag,
                    )
                    continue

                browser_download_url = asset.get("browser_download_url")
                if not browser_download_url:
                    logger.warning(
                        f"Asset '{file_name}' in release '{raw_release_tag}' has no download URL. Skipping."
                    )
                    failed_downloads_details.append(
                        {
                            "url": "Unknown - No download URL",
                            "path_to_download": os.path.join(
                                release_dir, safe_file_name
                            ),
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
                asset_download_path = os.path.join(release_dir, safe_file_name)
                if not os.path.exists(asset_download_path):
                    assets_to_download.append(
                        (browser_download_url, asset_download_path)
                    )
                else:
                    expected_size = asset.get("size")
                    if expected_size is not None:
                        try:
                            actual_size = os.path.getsize(asset_download_path)
                        except OSError:
                            actual_size = -1
                        if actual_size != expected_size:
                            logger.warning(
                                f"Existing {release_type} asset {asset_download_path} has size {actual_size}, expected {expected_size}; scheduling re-download"
                            )
                            try:
                                asset_common = os.path.commonpath(
                                    [
                                        real_download_base,
                                        os.path.realpath(asset_download_path),
                                    ]
                                )
                            except ValueError:
                                asset_common = None
                            if asset_common != real_download_base:
                                logger.warning(
                                    "Skipping re-download of %s asset with path %s due to escaping base",
                                    release_type,
                                    asset_download_path,
                                )
                                continue
                            if _prepare_for_redownload(asset_download_path):
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
            logger.info("Processing release: %s", raw_release_tag)
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
                        safe_zip_name = _sanitize_path_component(file_name)
                        if safe_zip_name is None:
                            logger.warning(
                                "Skipping extraction check for unsafe filename %s in release %s",
                                file_name,
                                raw_release_tag,
                            )
                            continue
                        zip_path: str = os.path.join(release_dir, safe_zip_name)
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
                        f"Release {raw_release_tag} found, but no assets matched the current selection/exclude filters."
                    )
                    # Consider the latest release processed even without downloads to avoid re-scanning
                    try:
                        if idx == 1:
                            if _atomic_write_text(latest_release_file, release_tag):
                                saved_release_tag = release_tag
                                logger.debug(
                                    f"Updated latest release tag to {release_tag} (no matching assets)"
                                )
                            else:
                                logger.warning(
                                    f"Could not record latest release tag {release_tag}: atomic write failed"
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
            raw_latest_release_tag_val: str = releases_to_download[0]["tag_name"]
            latest_release_tag_val = _sanitize_path_component(
                raw_latest_release_tag_val
            )
            if latest_release_tag_val is None:
                logger.warning(
                    "Skipping write of unsafe latest release tag: %s",
                    raw_latest_release_tag_val,
                )
                latest_release_tag_val = saved_release_tag or None
            if (
                latest_release_tag_val is not None
                and latest_release_tag_val != saved_release_tag
            ):
                if not _atomic_write_text(latest_release_file, latest_release_tag_val):
                    logger.warning(
                        f"Error writing latest release tag to {latest_release_file}"
                    )
                else:
                    logger.debug(
                        f"Updated latest release tag to {latest_release_tag_val}"
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
                tag
                for r in releases_to_download
                if (tag := _sanitize_path_component(r.get("tag_name"))) is not None
            ]
            cleanup_old_versions(download_dir_path, release_tags_to_keep)
        except (KeyError, TypeError) as e:
            logger.warning(
                f"Error preparing list of tags to keep for cleanup: {e}. Cleanup might be skipped or incomplete."
            )

    if not actions_taken:
        # Determine tags newer than the saved tag by position (list is newest-first)
        tags_order: List[str] = [
            tag
            for rd in releases_to_download
            if (tag := _sanitize_path_component(rd.get("tag_name"))) is not None
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
            try:
                extracted_file_path: str = safe_extract_path(
                    extract_dir, base_name_to_check
                )
            except ValueError:
                logger.warning(
                    "Skipping unsafe archive member %s during extraction check",
                    base_name_to_check,
                )
                continue
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
