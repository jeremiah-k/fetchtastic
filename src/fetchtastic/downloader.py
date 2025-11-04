# src/fetchtastic/downloader.py

import fnmatch
import glob
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import IO, TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

import platformdirs
import requests
from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

# Try to import LegacyVersion for type annotations (available in older packaging versions)
if TYPE_CHECKING:
    try:
        from packaging.version import LegacyVersion  # type: ignore
    except ImportError:
        LegacyVersion = None  # type: ignore
else:
    LegacyVersion = None  # Runtime fallback


from fetchtastic import menu_repo, setup_config
from fetchtastic.constants import (
    COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
    EXECUTABLE_PERMISSIONS,
    FILE_TYPE_PREFIXES,
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_BASE,
    GITHUB_API_TIMEOUT,
    LATEST_ANDROID_RELEASE_FILE,
    LATEST_FIRMWARE_RELEASE_FILE,
    MAX_CONCURRENT_TIMESTAMP_FETCHES,
    MESHTASTIC_ANDROID_RELEASES_URL,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    NTFY_REQUEST_TIMEOUT,
    PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
    RELEASE_SCAN_COUNT,
    RELEASES_CACHE_EXPIRY_HOURS,
    SHELL_SCRIPT_EXTENSION,
    VERSION_REGEX_PATTERN,
    ZIP_EXTENSION,
)
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.log_utils import logger
from fetchtastic.setup_config import display_version_info, get_upgrade_command
from fetchtastic.utils import (
    _show_token_warning_if_needed,
    download_file_with_retry,
    get_api_request_summary,
    get_effective_github_token,
    get_hash_file_path,
    make_github_api_request,
    matches_selected_patterns,
    track_api_cache_hit,
    track_api_cache_miss,
    verify_file_integrity,
)

"""
Version Handling for Meshtastic Releases

This module provides utilities for handling version strings and comparisons for Meshtastic
firmware and Android APK releases. The versioning approach accounts for:

Expected Version Formats:
- Stable releases: "v2.7.8", "2.7.8"
- Prereleases: "2.7.13.abcdef" (next patch version + commit hash)

Key Design Principles:
1. Prereleases and stable releases come from separate repositories
2. Prerelease versions are the next patch version with commit hash suffix
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
) -> Optional[Union[Version, Any]]:  # Use Any when LegacyVersion not available
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
        if saved_release_tag is not None:
            idx_saved = tags_order.index(saved_release_tag)
        else:
            idx_saved = len(tags_order)
    except (ValueError, TypeError):
        idx_saved = len(tags_order)
    return tags_order[:idx_saved]


def compare_versions(version1, version2):
    """
    Compare two version strings, preferring PEP 440 semantics when possible and falling back to a human-friendly natural ordering for nonstandard forms.

    This function attempts to normalize and parse inputs as PEP 440 versions (handling common variants like a leading "v" or trailing hash-like local segments) and, if both parse, compares them according to PEP 440 rules (including prerelease and local-version semantics). If one or both inputs cannot be parsed as PEP 440, a conservative natural-sort fallback is used that splits each string into numeric and alphabetic runs for human-friendly ordering.

    Parameters:
        version1 (str): First version string to compare.
        version2 (str): Second version string to compare.

    Returns:
        int: `1` if version1 is greater than version2, `0` if they are equal, `-1` if version1 is less than version2.
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
        Produce a natural-sort key by splitting a string into contiguous digit and alphabetic runs.

        The input is lowercased and punctuation is ignored by only capturing sequences of digits or letters. Numeric runs are converted to integers and alphabetic runs remain as lowercase strings. The function returns a list of tagged tuples that can be used as a sorting key for human-friendly ordering (e.g., "v2" < "v10").

        Returns:
            list[tuple[int, int | str]]: Tagged components (1,int) for digits and (0,str) for letters to ensure type-safe comparisons.
        """
        parts = re.findall(r"\d+|[A-Za-z]+", s.lower())
        # Tag parts to ensure comparable types: (1, int) > (0, str)
        return [(1, int(p)) if p.isdigit() else (0, p) for p in parts]

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
    Remove prerelease firmware directories that are superseded by an official release.

    Scans download_dir/firmware/prerelease for directories named "firmware-<version>" (optionally with a commit/hash suffix).
    If a prerelease's base version matches or is older than the provided latest official release tag, the prerelease directory
    (or unsafe symlink) is removed. Invalidly formatted names are skipped. If no prerelease directories remain, associated
    prerelease tracking files are also removed.

    Parameters:
        download_dir (str): Base download directory containing firmware/prerelease.
        latest_release_tag (str): Latest official release tag (may include a leading 'v').

    Returns:
        bool: `True` if one or more prerelease directories were removed, `False` otherwise.
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
        return False

    # Path to prerelease directory
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    if not os.path.exists(prerelease_dir):
        return False

    # Migrate any legacy text tracking files before cleanup
    _migrate_legacy_text_tracking_file(prerelease_dir)

    # Check for matching pre-release directories
    cleaned_up = False
    for raw_dir_name in os.listdir(prerelease_dir):
        if raw_dir_name.startswith(FIRMWARE_DIR_PREFIX):
            dir_name = _sanitize_path_component(raw_dir_name)
            if dir_name is None:
                logger.warning(
                    "Skipping unsafe prerelease directory encountered during cleanup: %s",
                    raw_dir_name,
                )
                continue

            dir_version = extract_version(dir_name)

            # Validate version format before processing (hash part is optional)
            if not re.match(VERSION_REGEX_PATTERN, dir_version):
                logger.warning(
                    f"Invalid version format in prerelease directory {dir_name}, skipping"
                )
                continue

            # If this pre-release matches the latest release version
            prerelease_path = os.path.join(prerelease_dir, dir_name)

            # Check if this is a symlink and remove it for security
            if os.path.islink(prerelease_path):
                logger.warning(
                    "Removing symlink in prerelease dir to prevent traversal: %s",
                    dir_name,
                )
                if _safe_rmtree(prerelease_path, prerelease_dir, dir_name):
                    cleaned_up = True
                else:
                    logger.error(
                        "Failed to remove symlink %s in prerelease dir", dir_name
                    )
                    return False
                continue
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
                # Both tuples are guaranteed non-None by can_compare_tuples check
                assert (
                    dir_release_tuple is not None and latest_release_tuple is not None
                )
                if dir_release_tuple > latest_release_tuple:
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

            # Remove tracking files (both JSON and legacy text)
            for file_path, is_legacy in [
                (json_tracking_file, False),
                (os.path.join(prerelease_dir, "prerelease_commits.txt"), True),
            ]:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        if is_legacy:
                            logger.debug(
                                "Removed legacy prerelease tracking file: %s", file_path
                            )
                        else:
                            logger.debug(
                                "Removed prerelease tracking file: %s", file_path
                            )
                    except OSError as e:
                        if is_legacy:
                            logger.warning(
                                "Could not remove legacy tracking file %s: %s",
                                file_path,
                                e,
                            )
                        else:
                            logger.warning(
                                "Could not remove tracking file %s: %s", file_path, e
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

    def _write_text_content(f: IO[str]) -> None:
        f.write(content)

    return _atomic_write(file_path, _write_text_content, suffix=".txt")


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


def _migrate_legacy_text_tracking_file(prerelease_dir: str) -> bool:
    """
    Migrate legacy text-format prerelease tracking data to new JSON format and remove the old file.

    This function should be called during startup to convert any remaining old text files
    to the new JSON format, then clean up the old files.

    Parameters:
        prerelease_dir (str): Directory containing prerelease tracking files.

    Returns:
        bool: True if migration was attempted (regardless of success), False if no old file found.
    """
    text_file = os.path.join(prerelease_dir, "prerelease_commits.txt")
    json_file = os.path.join(prerelease_dir, "prerelease_tracking.json")

    if not os.path.exists(text_file):
        return False

    try:
        with open(text_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        if not lines:
            # Empty file, just remove it
            os.remove(text_file)
            logger.debug("Removed empty legacy prerelease tracking file: %s", text_file)
            return True

        # Parse the old format
        if lines[0].startswith("Release: "):
            current_release = lines[0][9:]  # Remove "Release: " prefix
            commits_raw = lines[1:]
        else:
            # Legacy format: treat all lines as commits; release unknown
            current_release = "unknown"
            commits_raw = lines

        # Normalize commits to lowercase
        commits = [commit.lower() for commit in commits_raw]

        # Create new JSON format data
        migration_data = {
            "version": current_release if current_release != "unknown" else None,
            "commits": commits,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        # Write to new JSON format
        def write_json_data(f):
            json.dump(migration_data, f, indent=2)

        if _atomic_write(json_file, write_json_data, ".json"):
            # Successfully wrote JSON, now remove old text file
            os.remove(text_file)
            logger.info(
                "Migrated legacy prerelease tracking from text to JSON format: %s → %s",
                text_file,
                json_file,
            )
            return True
        else:
            logger.warning("Failed to write migrated JSON data, keeping old text file")
            return False

    except (IOError, UnicodeDecodeError, OSError) as e:
        logger.warning(
            "Failed to migrate legacy prerelease tracking file %s: %s", text_file, e
        )
        return False


def _ensure_v_prefix_if_missing(version: Optional[str]) -> Optional[str]:
    """Add 'v' prefix to version if missing (case-insensitive)."""
    if version is None:
        return None
    version = version.strip()
    if version and not version.lower().startswith("v"):
        return f"v{version}"
    return version


def _matches_exclude(name: str, patterns: List[str]) -> bool:
    """Case-insensitive fnmatch against any exclude pattern."""
    name_l = name.lower()
    return any(fnmatch.fnmatch(name_l, p.lower()) for p in patterns)


def _normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    """
    Normalize a commit identifier to version+hash format for consistency.

    Args:
        commit_id: The commit identifier (may be hash-only or version+hash)
        release_version: The release version (e.g., "v2.7.13") to use for hash-only entries

    Returns:
        Normalized commit identifier in version+hash format (e.g., "2.7.13.abcdef")
    """
    commit_id = commit_id.lower()

    # If it already looks like version+hash (contains version numbers and hex chars), return as-is
    if re.search(r"^\d+\.\d+\.\d+\.[a-f0-9]{6,40}$", commit_id):
        return commit_id

    # If it's just a hash, try to extract version from release_version
    if re.match(r"^[a-f0-9]{6,40}$", commit_id):
        if release_version:
            # Extract version part (remove 'v' prefix and any hash)
            clean_version = _extract_clean_version(release_version)
            if clean_version:
                version_without_v = clean_version.lstrip("v")
                return f"{version_without_v}.{commit_id}"
        # If we can't determine version, just return hash as-is
        return commit_id

    # Fallback: return as-is
    return commit_id


def _extract_clean_version(version_with_hash: Optional[str]) -> Optional[str]:
    """
    Extract clean version from a string that may contain version+hash.

    Args:
        version_with_hash: String that may be like "v2.7.13" or "v2.7.13.abcdef"

    Returns:
        Clean version string (e.g., "v2.7.13") or None if input is None
    """
    if not version_with_hash:
        return None

    # Remove leading 'v'/'V' for processing
    version_part = version_with_hash.lstrip("vV")

    # Split on first dot after version numbers to separate version from hash
    # Pattern: major.minor.patch[.hash]
    parts = version_part.split(".")
    if len(parts) >= 3:
        # Take first 3 parts as version (major.minor.patch)
        clean_version = ".".join(parts[:3])
        return f"v{clean_version}"

    # If it doesn't look like version+hash, return as-is with v prefix
    return _ensure_v_prefix_if_missing(version_with_hash)


def _parse_new_json_format(
    tracking_data: Dict[str, Any],
) -> tuple[list[str], str | None, str | None]:
    """
    Parse prerelease tracking JSON in the new format into normalized commits, the current release, and a last-updated timestamp.

    Accepts a mapping produced from the new prerelease tracking JSON. Expected keys include:
    - "version": the release version (e.g., "v1.2.3" or "1.2.3"),
    - "hash": a commit hash string used as a fallback when "commits" is absent,
    - "commits": an optional list of commit identifiers (strings),
    - "last_updated" or "timestamp": an optional timestamp string.

    Behavior:
    - Ensures the returned current release has a leading "v" when possible.
    - When "commits" is missing but "hash" is present, attempts to synthesize a version+hash commit using the expected prerelease version.
    - Validates that "commits" is a list; non-list values are treated as an empty list.
    - Normalizes each commit identifier to a consistent lowercase version+hash form when possible, skips invalid entries, and preserves original order while removing duplicates.

    Parameters:
        tracking_data (dict): Parsed JSON object from a prerelease tracking file.

    Returns:
        tuple[list[str], str | None, str | None]:
            - commits: list of normalized commit identifiers (version+hash strings) in preserved order without duplicates,
            - current_release: the release version with a leading "v" when available, or `None` if unspecified,
            - last_updated: timestamp string from "last_updated" or "timestamp" if present, otherwise `None`.
    """
    version = tracking_data.get("version")
    hash_val = tracking_data.get("hash")
    current_release = _ensure_v_prefix_if_missing(version)

    # Check if commits list exists, otherwise use single hash
    commits_raw = tracking_data.get("commits")
    if commits_raw is None and hash_val:
        expected = calculate_expected_prerelease_version(current_release or "")
        if expected:
            commits_raw = [f"{expected}.{hash_val}"]
        else:
            commits_raw = [hash_val]

    # Validate commits_raw is a list to prevent data corruption
    if not isinstance(commits_raw, list):
        logger.warning(
            f"Invalid commits format in tracking file: expected list, got {type(commits_raw)}. Resetting commits."
        )
        commits_raw = []

    # Normalize commits to version+hash format for consistency
    commits = []
    for commit in commits_raw:
        if isinstance(commit, str):
            normalized = _normalize_commit_identifier(commit.lower(), current_release)
            if normalized:
                commits.append(normalized)
        else:
            logger.warning(
                f"Invalid commit entry in tracking file: expected str, got {type(commit)}. Skipping."
            )

    # Ensure uniqueness while preserving order
    commits = list(dict.fromkeys(commits))
    last_updated = tracking_data.get("last_updated") or tracking_data.get("timestamp")
    return commits, current_release, last_updated


def _parse_legacy_json_format(
    tracking_data: Dict[str, Any],
) -> tuple[list[str], str | None, str | None]:
    """
    Parse legacy prerelease tracking JSON that uses top-level `release`, `commits`, and optional timestamp keys.

    This normalizes commit identifiers into `version+hash` form, pairing bare commit hashes with an inferred next-prerelease base when possible, and preserves the original release string (ensuring a leading "v" if missing).

    Parameters:
        tracking_data (dict): Parsed JSON object expected to contain:
            - "release": optional release string (e.g., "v1.2.3" or "1.2.3")
            - "commits": optional list of commit identifiers (hashes or version+hash)
            - "last_updated" or "timestamp": optional timestamp string

    Returns:
        tuple: (commits, current_release, last_updated)
            - commits (list[str]): Ordered, deduplicated list of normalized `version+hash` commit identifiers.
            - current_release (str | None): Release string from the tracking data with a leading "v" ensured, or None.
            - last_updated (str | None): Timestamp string from "last_updated" or "timestamp", or None.
    """
    current_release = _ensure_v_prefix_if_missing(tracking_data.get("release"))
    commits_raw = tracking_data.get("commits", [])

    # Validate commits_raw is a list to prevent data corruption
    if not isinstance(commits_raw, list):
        logger.warning(
            f"Invalid commits format in legacy tracking file: expected list, got {type(commits_raw).__name__}. Resetting commits."
        )
        commits_raw = []

    # Normalize commits to version+hash; pair bare hashes with expected next patch base
    commits: list[str] = []
    expected_base_v: Optional[str] = None
    if current_release:
        expected = calculate_expected_prerelease_version(current_release)
        if expected:
            expected_base_v = f"v{expected}"
    for commit in commits_raw or []:
        c = commit.lower()
        # If it's a bare hash, use expected next patch base; otherwise use saved release
        base_for_norm = (
            expected_base_v if re.fullmatch(r"[a-f0-9]{6,40}", c) else current_release
        )
        normalized = _normalize_commit_identifier(c, base_for_norm)
        if normalized:
            commits.append(normalized)

    # Ensure uniqueness while preserving order
    commits = list(dict.fromkeys(commits))
    last_updated = tracking_data.get("last_updated") or tracking_data.get("timestamp")
    return commits, current_release, last_updated


def _read_prerelease_tracking_data(tracking_file):
    """
    Read prerelease tracking information from a JSON tracking file.

    Supports both newer schema (keys like "version", "hash", "commits", "last_updated"/"timestamp")
    and legacy JSON schema ("release", "commits"). Legacy text format support has been removed
    - migration should be handled by _migrate_legacy_text_tracking_file before calling this function.

    Returns:
        tuple: (commits, current_release, last_updated)
            commits (list[str]): Ordered list of prerelease IDs (may be empty).
            current_release (str | None): Release tag associated with commits, or None if unknown.
            last_updated (str | None): ISO timestamp of last update from JSON (or None if unavailable).
    """
    commits = []
    current_release = None
    last_updated = None

    if os.path.exists(tracking_file):
        try:
            with open(tracking_file, "r", encoding="utf-8") as f:
                tracking_data = json.load(f)

                # Check for new format (version, hash, count)
                # Note: "count" is not used; reserved for future aggregation.
                if "version" in tracking_data and (
                    "hash" in tracking_data or "commits" in tracking_data
                ):
                    # New format: parse into standard internal representation.
                    commits, current_release, last_updated = _parse_new_json_format(
                        tracking_data
                    )
                else:
                    # Legacy JSON format
                    commits, current_release, last_updated = _parse_legacy_json_format(
                        tracking_data
                    )

        except (IOError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Could not read prerelease tracking file: {e}")

    return commits, current_release, last_updated


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
                if not entry.name.startswith(FIRMWARE_DIR_PREFIX):
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

    This is a convenience wrapper around `_update_tracking_with_newest_prerelease` for handling
    a single prerelease directory. It provides the same functionality with a simpler
    interface for single-directory updates.

    Parameters:
        prerelease_dir (str): Path to the prerelease directory (parent for prerelease_tracking.json).
        latest_release_tag (str): Latest official release tag used to determine whether to reset tracking.
        current_prerelease (str): Name of the current prerelease directory (used to extract the prerelease commit).

     Returns:
         int: Number of tracked prerelease commits actually persisted to disk (0 on write failure).
    """
    result = _update_tracking_with_newest_prerelease(
        prerelease_dir, latest_release_tag, current_prerelease
    )
    return result if result is not None else 0


def _update_tracking_with_newest_prerelease(
    prerelease_dir: str, latest_release_tag: str, newest_prerelease_dir: str
) -> Optional[int]:
    """
    Update prerelease_tracking.json by recording the newest prerelease identifier.

    This function maintains two levels of state:
    - **On disk**: Keeps only the newest prerelease directory; older directories are automatically removed.
    - **In tracking file**: Preserves a cumulative list of all prerelease identifiers for the current release version (full identifiers like '2.7.7.abcd123' including version and commit hash).

    If the official release tag changes, tracked prerelease IDs are reset to start fresh for the new release.

    Parameters:
        prerelease_dir (str): Directory containing prerelease_tracking.json.
        latest_release_tag (str): Current official release tag; when this differs from the stored release, tracked prerelease IDs are reset.
        newest_prerelease_dir (str): Newest prerelease directory name to process; entries without a commit are ignored.

      Returns:
          Optional[int]: Total number of tracked prerelease commits after update.
               Returns 0 immediately if newest_prerelease_dir is empty or invalid.
               Returns None if the tracking file could not be written to disk.
               Returns the number of commits actually persisted to disk on success.
    """
    if not newest_prerelease_dir:
        return 0

    tracking_file = os.path.join(prerelease_dir, "prerelease_tracking.json")

    # Extract single prerelease version and hash from directory name
    # Validate that directory name follows expected pattern: firmware-<version>
    if newest_prerelease_dir.startswith(FIRMWARE_DIR_PREFIX):
        new_prerelease_id = newest_prerelease_dir.removeprefix(
            FIRMWARE_DIR_PREFIX
        ).lower()
    else:
        logger.debug("Ignoring non-firmware prerelease dir: %s", newest_prerelease_dir)
        new_prerelease_id = None

    if not new_prerelease_id:
        logger.debug("No valid firmware prerelease directory found")
        return 0

    # Read current tracking data
    existing_commits, existing_release, _ = _read_prerelease_tracking_data(
        tracking_file
    )

    # Check if we need to reset due to new official release
    clean_latest_release = _extract_clean_version(latest_release_tag)
    if existing_release and existing_release != clean_latest_release:
        logger.info(
            f"New release {latest_release_tag} detected (previously tracking {existing_release}). Resetting prerelease tracking."
        )
        existing_commits = []

    # Check if this is a new prerelease ID for the same version
    is_new_id = new_prerelease_id not in set(existing_commits)

    # Only update if it's a new prerelease ID
    if not is_new_id:
        logger.debug(
            f"Prerelease {new_prerelease_id} already tracked, no update needed"
        )
        return len(existing_commits)

    # Update tracking with the new prerelease ID
    updated_commits = list(dict.fromkeys([*existing_commits, new_prerelease_id]))
    # Extract commit hash for the newest prerelease directory
    commit_hash = _get_commit_hash_from_dir(newest_prerelease_dir)

    # Write updated tracking data in new format
    now_iso = datetime.now().astimezone().isoformat()
    new_tracking_data = {
        "version": _extract_clean_version(
            latest_release_tag
        ),  # base version without hash
        "commits": updated_commits,  # full prerelease IDs in version+hash format
        "hash": commit_hash,  # optional single latest hash
        "count": len(updated_commits),  # total tracked prereleases
        "timestamp": now_iso,  # per PR format
        "last_updated": now_iso,  # maintain compatibility with existing readers
    }

    if not _atomic_write_json(tracking_file, new_tracking_data):
        return None  # Return None on write failure

    logger.info(
        f"Prerelease tracking updated: {len(updated_commits)} prerelease IDs tracked, latest: {new_prerelease_id}"
    )
    return len(updated_commits)


def matches_extract_patterns(filename, extract_patterns, device_manager=None):
    """
    Determine whether a filename matches any of the configured extract patterns.

    Matching is case-insensitive and supports:
    - file-type prefix patterns (from FILE_TYPE_PREFIXES) matched by substring,
    - device patterns (identified by trailing '-' or '_' or via device_manager.is_device_pattern()) that match device names with boundary-aware regexes (short patterns use word-boundary matching),
    - the special "littlefs-" pattern which matches filenames starting with "littlefs-",
    - and general substring patterns as a fallback.

    Parameters:
        filename (str): The file name to test.
        extract_patterns (Iterable[str]): Patterns from configuration to match against.
        device_manager (optional): Object providing is_device_pattern(pattern) to identify device patterns.

    Returns:
        bool: `True` if any pattern matches the filename, `False` otherwise.
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
    Return a summary of prerelease tracking data found in prerelease_tracking.json within the given directory.

    Reads prerelease tracking data and returns a dictionary with the tracked official release, ordered prerelease commit identifiers, a count, and the last-updated timestamp. If no tracking data is present, returns an empty dict.

    Returns:
        dict: Summary with keys:
            - "release" (str | None): Latest official release tag, or None.
            - "commits" (list[str]): Ordered list of tracked prerelease identifiers (may be empty).
            - "prerelease_count" (int): Number of tracked prerelease commits.
            - "last_updated" (str | None): ISO 8601 timestamp of the last update, or None.
            - "latest_prerelease" (str | None): Most recent prerelease identifier from `commits`, or None.
    """
    tracking_file = os.path.join(prerelease_dir, "prerelease_tracking.json")
    commits, release, last_updated = _read_prerelease_tracking_data(tracking_file)
    if not commits and not release:
        return {}

    return {
        "release": release,
        "commits": commits,
        "prerelease_count": len(commits),
        "last_updated": last_updated,
        "latest_prerelease": commits[-1] if commits else None,
    }


def _iter_matching_prerelease_files(
    dir_name: str,
    selected_patterns: list,
    exclude_patterns_list: list,
    device_manager,
) -> List[Dict[str, str]]:
    """
    Return prerelease assets in a remote directory that match selection patterns and do not match any exclusion patterns.

    Filters the remote directory named by dir_name using selected_patterns (resolved with device_manager when provided), excludes any filenames matching patterns in exclude_patterns_list, and skips entries with unsafe path components.

    Parameters:
        dir_name (str): Remote prerelease directory to inspect.
        selected_patterns (list): Patterns used to select matching assets; may include device-aware patterns.
        exclude_patterns_list (list): fnmatch-style patterns; any match causes an asset to be skipped.
        device_manager: Optional DeviceHardwareManager used to resolve device-specific patterns.

    Returns:
        list: A list of dictionaries, each with keys:
            - "name": sanitized filename safe as a single path component
            - "download_url": URL for downloading the asset
            - "path": repository-relative path of the asset
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

        if _matches_exclude(file_name, exclude_patterns_list):
            logger.debug(
                "Skipping pre-release file %s (matched exclude pattern)",
                file_name,
            )
            continue

        record = {
            "name": safe_file_name,
            "download_url": download_url,
        }
        # Preserve repository-relative path if provided by menu_repo
        asset_path = entry.get("path")
        if asset_path:
            record["path"] = asset_path
        matching.append(record)

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

    Returns True if file is missing or fails integrity verification
    and can be prepared for re-download, False otherwise.

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


def extract_version(dir_name: str) -> str:
    """
    Extract the version substring from a prerelease directory name that starts with "firmware-".

    Returns:
        The directory name with the "firmware-" prefix removed.
    """
    return dir_name.removeprefix(FIRMWARE_DIR_PREFIX)


def _get_commit_hash_from_dir(dir_name: str) -> Optional[str]:
    """
    Extracts a commit hash from a prerelease directory name.

    Searches the version portion (after the "firmware-" prefix) for a hexadecimal commit identifier of 4-40 characters and returns it in lowercase if found; returns None when no commit hash is present.

    Returns:
        commit_hash (Optional[str]): Lowercase commit hash when present, otherwise None.
    """
    version_part = extract_version(dir_name)  # Removes "firmware-" prefix
    # Use regex to find a hex string of 4-40 characters, which is more robust.
    # This pattern looks for a dot or dash, then the hash, followed by another separator or end of string.
    commit_match = re.search(
        r"[.-]([a-f0-9]{4,40})(?:[.-]|$)", version_part, re.IGNORECASE
    )
    if commit_match:
        return commit_match.group(1).lower()
    return None


def calculate_expected_prerelease_version(latest_version: str) -> str:
    """
    Compute the expected prerelease version by incrementing the patch component of the given latest version.

    Parameters:
        latest_version (str): Version string of the latest official release (for example "2.7.6" or "v2.7.6").

    Returns:
        str: Expected prerelease version in the form "MAJOR.MINOR.PATCH" where PATCH is incremented by one, or an empty string if the input cannot be parsed to determine major and minor components.
    """
    latest_tuple = _get_release_tuple(latest_version)
    if not latest_tuple or len(latest_tuple) < 2:
        logger.warning(
            "Could not calculate expected prerelease version from: %s", latest_version
        )
        return ""

    # Increment the patch version (third position) by 1
    major, minor = latest_tuple[0], latest_tuple[1]
    patch = latest_tuple[2] if len(latest_tuple) > 2 else 0
    expected_patch = patch + 1
    return f"{major}.{minor}.{expected_patch}"


# Global cache for commit timestamps to avoid repeated API calls
_commit_timestamp_cache: Dict[str, Tuple[datetime, datetime]] = {}
_commit_cache_loaded = False
_cache_lock = threading.Lock()  # Lock for thread-safe cache access

# Cache file path for persistent storage
_commit_cache_file = None

# Global cache for releases data to avoid repeated API calls
_releases_cache: Dict[str, Tuple[List[Dict[str, Any]], datetime]] = {}
_releases_cache_file = None
_releases_cache_loaded = False

# Global cache for prerelease directory listings
_prerelease_dir_cache: Dict[str, Tuple[List[str], datetime]] = {}
_prerelease_dir_cache_file: Optional[str] = None
_prerelease_dir_cache_loaded = False
_PRERELEASE_DIR_CACHE_ROOT_KEY = "__root__"

# Global flag to track if downloads were skipped due to Wi-Fi requirements
downloads_skipped = False


def _ensure_cache_dir() -> str:
    """
    Ensure the per-user cache directory for fetchtastic exists and return its path.

    Returns:
        cache_dir (str): Absolute path to the cache directory.
    """
    cache_dir = platformdirs.user_cache_dir("fetchtastic")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _get_commit_cache_file() -> str:
    """
    Return the filesystem path for the persistent commit timestamp cache file.

    The path points to "commit_timestamps.json" inside the module's cache directory (created via _ensure_cache_dir()).

    Returns:
        str: Path to the commit timestamp cache file.
    """
    global _commit_cache_file
    if _commit_cache_file is None:
        _commit_cache_file = os.path.join(_ensure_cache_dir(), "commit_timestamps.json")
    return _commit_cache_file


def _get_releases_cache_file() -> str:
    """
    Return the filesystem path of the persistent releases cache file.

    Returns:
        str: Path to the releases cache JSON file inside the application's cache directory.
    """
    global _releases_cache_file
    if _releases_cache_file is None:
        _releases_cache_file = os.path.join(_ensure_cache_dir(), "releases.json")
    return _releases_cache_file


def _get_prerelease_dir_cache_file() -> str:
    """
    Return the filesystem path of the persistent prerelease directory cache file.

    Returns:
        str: Path to the prerelease directories cache JSON file.
    """
    global _prerelease_dir_cache_file
    if _prerelease_dir_cache_file is None:
        _prerelease_dir_cache_file = os.path.join(
            _ensure_cache_dir(), "prerelease_dirs.json"
        )
    return _prerelease_dir_cache_file


def _load_prerelease_dir_cache() -> None:
    """
    Populate the in-memory prerelease directory cache from disk, respecting expiry.
    """
    global _prerelease_dir_cache_loaded

    with _cache_lock:
        if _prerelease_dir_cache_loaded:
            return

    cache_file = _get_prerelease_dir_cache_file()

    try:
        if not os.path.exists(cache_file):
            with _cache_lock:
                _prerelease_dir_cache_loaded = True
            return

        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        if not isinstance(cache_data, dict):
            with _cache_lock:
                _prerelease_dir_cache_loaded = True
            return

        current_time = datetime.now(timezone.utc)
        loaded: Dict[str, Tuple[List[str], datetime]] = {}

        for cache_key, cache_entry in cache_data.items():
            try:
                if (
                    not isinstance(cache_entry, dict)
                    or "directories" not in cache_entry
                    or "cached_at" not in cache_entry
                ):
                    logger.debug(
                        "Skipping invalid prerelease cache entry for %s: incorrect structure",
                        cache_key,
                    )
                    continue

                directories = cache_entry["directories"]
                cached_at = datetime.fromisoformat(
                    cache_entry["cached_at"].replace("Z", "+00:00")
                )

                if not isinstance(directories, list):
                    logger.debug(
                        "Skipping prerelease cache entry for %s: directories not a list",
                        cache_key,
                    )
                    continue

                age = current_time - cached_at
                if age.total_seconds() < PRERELEASE_DIR_CACHE_EXPIRY_SECONDS:
                    loaded[cache_key] = (list(directories), cached_at)
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(
                    "Skipping invalid prerelease cache entry for %s: %s",
                    cache_key,
                    e,
                )
                continue

        with _cache_lock:
            _prerelease_dir_cache.update(loaded)
            _prerelease_dir_cache_loaded = True

        if loaded:
            logger.debug(
                "Loaded %d prerelease directory cache entries from disk", len(loaded)
            )

    except (IOError, json.JSONDecodeError) as e:
        logger.debug(f"Could not load prerelease directory cache: {e}")


def _save_prerelease_dir_cache() -> None:
    """
    Persist the in-memory prerelease directory cache to disk.
    """
    cache_file = _get_prerelease_dir_cache_file()

    try:
        with _cache_lock:
            cache_data = {
                cache_key: {
                    "directories": directories,
                    "cached_at": cached_at.isoformat(),
                }
                for cache_key, (directories, cached_at) in _prerelease_dir_cache.items()
            }

        if _atomic_write_json(cache_file, cache_data):
            logger.debug(
                "Saved %d prerelease directory cache entries to disk",
                len(cache_data),
            )
        else:
            logger.warning(
                "Failed to save prerelease directory cache to %s", cache_file
            )

    except (IOError, OSError) as e:
        logger.warning(f"Could not save prerelease directory cache: {e}")


def _clear_prerelease_cache() -> None:
    """
    Clear the in-memory and on-disk prerelease directory cache.
    """
    global _prerelease_dir_cache_loaded

    _clear_cache_generic(
        cache_dict=_prerelease_dir_cache,
        cache_file_getter=_get_prerelease_dir_cache_file,
        cache_name="prerelease directory",
    )

    with _cache_lock:
        _prerelease_dir_cache_loaded = False


def _fetch_prerelease_directories(force_refresh: bool = False) -> List[str]:
    """
    Return prerelease directories from cache when fresh, otherwise fetch from GitHub.

    Parameters:
        force_refresh (bool): When True, bypass the cached directories and refetch.

    Returns:
        List[str]: List of directory names from the meshtastic.github.io repository root.
    """
    cache_key = _PRERELEASE_DIR_CACHE_ROOT_KEY

    _load_prerelease_dir_cache()

    now = datetime.now(timezone.utc)

    with _cache_lock:
        if not force_refresh and cache_key in _prerelease_dir_cache:
            directories, cached_at = _prerelease_dir_cache[cache_key]
            age = now - cached_at
            if age.total_seconds() < PRERELEASE_DIR_CACHE_EXPIRY_SECONDS:
                track_api_cache_hit()
                logger.debug(
                    "Using cached prerelease directories (cached %.0fs ago)",
                    age.total_seconds(),
                )
                return list(directories)

            logger.debug(
                "Prerelease directory cache expired (age %.0fs, limit %ds) - refreshing",
                age.total_seconds(),
                PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
            )
            del _prerelease_dir_cache[cache_key]

    logger.debug("Cache miss for prerelease directories - fetching from GitHub API")
    track_api_cache_miss()
    directories = menu_repo.fetch_repo_directories()
    updated_at = datetime.now(timezone.utc)

    with _cache_lock:
        _prerelease_dir_cache[cache_key] = (list(directories), updated_at)

    _save_prerelease_dir_cache()
    return list(directories)


def _load_commit_cache() -> None:
    """
    Populate the in-memory commit timestamp cache from the on-disk cache file, respecting cache expiry.

    Reads the commit cache file, validates and parses cached entries, converts stored timestamps to datetimes,
    and loads only entries that have not expired into the module-level commit timestamp cache. Marks the cache as loaded
    to avoid repeated loads and logs debug information on success or when the cache cannot be read or contains
    invalid entries.
    """
    global _commit_cache_loaded

    # Fast path without lock
    if _commit_cache_loaded:
        return

    # Double-checked locking pattern with minimal lock time
    with _cache_lock:
        if _commit_cache_loaded:
            return

    # Load cache data outside the lock to avoid holding it during I/O
    cache_file = _get_commit_cache_file()
    loaded: Dict[str, Tuple[datetime, datetime]] = {}

    try:
        if not os.path.exists(cache_file):
            with _cache_lock:
                _commit_cache_loaded = True
            return

        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        # Validate cache structure
        if not isinstance(cache_data, dict):
            with _cache_lock:
                _commit_cache_loaded = True
            return

        # Convert string timestamps back to datetime objects (build locally)
        current_time = datetime.now(timezone.utc)
        for cache_key, cache_value in cache_data.items():
            try:
                if not isinstance(cache_value, (list, tuple)) or len(cache_value) != 2:
                    logger.debug(
                        f"Skipping invalid cache entry for {cache_key}: incorrect structure"
                    )
                    continue
                timestamp_str, cached_at_str = cache_value
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))

                # Check if entry is still valid (not expired)
                age = current_time - cached_at
                if age.total_seconds() < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60:
                    loaded[cache_key] = (timestamp, cached_at)
            except (ValueError, TypeError) as e:
                # Skip invalid entries
                logger.debug(f"Skipping invalid cache entry for {cache_key}: {e}")
                continue

        # Update cache and mark loaded atomically under lock
        with _cache_lock:
            _commit_timestamp_cache.update(loaded)
            _commit_cache_loaded = True
        logger.debug(f"Loaded {len(loaded)} commit timestamps from cache")

    except (IOError, json.JSONDecodeError) as e:
        logger.debug(f"Could not load commit timestamp cache: {e}")
        # Don't mark as loaded to allow retry on subsequent API calls


def _load_releases_cache() -> None:
    """
    Populate the in-memory releases cache from the on-disk cache file, respecting cache expiry.

    Reads the releases cache file, validates and parses cached entries, converts stored timestamps to datetimes,
    and loads only entries that have not expired into the module-level releases cache. Marks the cache as loaded
    to avoid repeated loads and logs debug information on success or when the cache cannot be read or contains
    invalid entries.
    """
    global _releases_cache, _releases_cache_loaded

    with _cache_lock:
        if _releases_cache_loaded:
            return

    cache_file = _get_releases_cache_file()

    try:
        if not os.path.exists(cache_file):
            with _cache_lock:
                _releases_cache_loaded = True
            return

        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        # Validate cache structure
        if not isinstance(cache_data, dict):
            with _cache_lock:
                _releases_cache_loaded = True
            return

        # Convert string timestamps back to datetime objects (build locally)
        current_time = datetime.now(timezone.utc)
        loaded: Dict[str, Tuple[List[Dict[str, Any]], datetime]] = {}
        for cache_key, cache_entry in cache_data.items():
            try:
                # Validate cache entry structure
                if (
                    not isinstance(cache_entry, dict)
                    or "releases" not in cache_entry
                    or "cached_at" not in cache_entry
                ):
                    logger.debug(
                        f"Skipping invalid cache entry for {cache_key}: incorrect structure"
                    )
                    continue

                releases_data = cache_entry["releases"]
                cached_at = datetime.fromisoformat(
                    cache_entry["cached_at"].replace("Z", "+00:00")
                )

                # Check if entry is still valid (not expired)
                age = current_time - cached_at
                if age.total_seconds() < RELEASES_CACHE_EXPIRY_HOURS * 60 * 60:
                    loaded[cache_key] = (releases_data, cached_at)
            except (ValueError, TypeError, KeyError) as e:
                # Skip invalid entries
                logger.debug(f"Skipping invalid cache entry for {cache_key}: {e}")
                continue

        with _cache_lock:
            _releases_cache.update(loaded)
            _releases_cache_loaded = True
        logger.debug(f"Loaded {len(loaded)} releases entries from cache")

    except (IOError, json.JSONDecodeError) as e:
        logger.debug(f"Could not load releases cache: {e}")


def _save_releases_cache() -> None:
    """
    Persist the in-memory releases cache to the platform cache file.

    Writes a JSON object mapping cache keys to records with two fields:
    - `releases`: the cached releases data
    - `cached_at`: ISO 8601 timestamp when the entry was cached

    The write is performed atomically to avoid partial files; success or failure is logged at debug level.
    """
    global _releases_cache
    cache_file = _get_releases_cache_file()

    try:
        with _cache_lock:
            cache_data = {
                cache_key: {
                    "releases": releases_data,
                    "cached_at": cached_at.isoformat(),
                }
                for cache_key, (releases_data, cached_at) in _releases_cache.items()
            }

        # Use atomic write to prevent cache corruption
        if _atomic_write_json(cache_file, cache_data):
            logger.debug(f"Saved {len(cache_data)} releases entries to cache")
        else:
            logger.warning(f"Failed to save releases cache to {cache_file}")

    except (IOError, OSError) as e:
        logger.warning(f"Could not save releases cache: {e}")


def _clear_cache_generic(
    cache_dict: Dict[str, Any],
    cache_file_getter: Callable[[], str],
    cache_name: str,
    lock: Any = _cache_lock,
) -> None:
    """
    Generic cache clearing helper to reduce code duplication.

    Parameters:
        cache_dict: The cache dictionary to clear
        cache_file_getter: Function that returns the cache file path
        cache_name: Name of the cache for logging purposes
        lock: Lock to use for thread safety (defaults to _cache_lock)
    """
    with lock:
        cache_dict.clear()

    try:
        cache_file = cache_file_getter()
        if os.path.exists(cache_file):
            os.remove(cache_file)
            logger.debug(f"Removed {cache_name} cache file")
    except OSError as e:
        logger.debug(f"Error clearing {cache_name} cache file: {e}")


def _clear_commit_cache() -> None:
    """
    Clear the in-memory and on-disk commit timestamp cache.

    Helper function that clears the commit timestamp cache from memory
    and removes the persistent cache file from disk.
    """
    global _commit_timestamp_cache, _commit_cache_loaded

    _clear_cache_generic(
        cache_dict=_commit_timestamp_cache,
        cache_file_getter=_get_commit_cache_file,
        cache_name="commit timestamp",
    )

    with _cache_lock:
        _commit_cache_loaded = False


def clear_all_caches() -> None:
    """
    Clear in-memory and on-disk caches for commit timestamps and release data.

    This resets the internal caches and their "loaded" flags, then removes the persistent cache files from disk if present. Intended to force subsequent operations to refresh data from remote sources.
    """
    global _releases_cache, _releases_cache_loaded

    # Clear commit cache using helper
    _clear_commit_cache()

    # Clear prerelease directory cache
    _clear_prerelease_cache()

    # Clear releases cache using generic helper
    _clear_cache_generic(
        cache_dict=_releases_cache,
        cache_file_getter=_get_releases_cache_file,
        cache_name="releases",
    )

    with _cache_lock:
        _releases_cache_loaded = False

    logger.debug("Cleared all caches")


def _find_latest_remote_prerelease_dir(
    expected_version: str,
    github_token: Optional[str] = None,
    force_refresh: bool = False,
) -> Optional[str]:
    """
    Select the newest remote prerelease directory that matches an expected prerelease base version.

    Prefers directories with Git commit timestamps (newest first) and falls back to lexicographic ordering of commit hashes when timestamps are unavailable.

    Parameters:
        expected_version (str): Base prerelease version to match (e.g., "2.7.13").
        github_token (Optional[str]): GitHub API token to use when fetching commit timestamps; may be None to use no token or an environment-provided token.
        force_refresh (bool): When True, bypass cached commit timestamps and fetch fresh values.

    Returns:
        Optional[str]: Name of the newest matching prerelease directory (for example "firmware-2.7.13-abcdef"), or None if no matching directory is found.
    """
    try:
        directories = _fetch_prerelease_directories(force_refresh=force_refresh)
        if not directories:
            logger.info("No firmware directories found in the repository.")
            return None

        # Only look for prerelease directories matching the expected version
        matching_prerelease_dirs: List[str] = []
        for raw_dir_name in directories:
            if not raw_dir_name.startswith(FIRMWARE_DIR_PREFIX):
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
                    "Repository prerelease directory %s uses a non-standard version format; skipping",
                    dir_name,
                )
                continue

            # Extract base version (without hash) to check if it matches expected
            match = re.match(r"(\d+\.\d+\.\d+)", dir_version)
            if not match:
                continue
            dir_base_version = match.group(1)

            # Only include directories that match the expected prerelease version
            if dir_base_version == expected_version:
                matching_prerelease_dirs.append(dir_name)

        if not matching_prerelease_dirs:
            logger.info(
                f"No prerelease directories found for expected version {expected_version}"
            )
            return None

        # Sort by commit timestamp to get the newest
        # Get timestamps for each directory
        commit_hashes: List[Optional[str]] = [
            _get_commit_hash_from_dir(d) for d in matching_prerelease_dirs
        ]

        def _safe_get_timestamp(
            commit_hash: Optional[str],
        ) -> Tuple[Optional[datetime], bool]:
            """
            Return the commit timestamp for a Meshtastic firmware commit hash along with whether it was freshly fetched.

            Parameters:
                commit_hash (str | None): Commit SHA or identifier to look up; if falsy, no lookup is performed.

            Returns:
                tuple: (timestamp, fetched_new) where `timestamp` is the commit datetime or None and `fetched_new`
                indicates whether a fresh API fetch populated the cache.
            """
            if not commit_hash:
                return None, False

            cache_key = f"meshtastic/firmware/{commit_hash}"
            with _cache_lock:
                had_entry = cache_key in _commit_timestamp_cache

            timestamp = get_commit_timestamp(
                "meshtastic", "firmware", commit_hash, github_token, force_refresh
            )

            fetched_new = False
            if timestamp is not None:
                with _cache_lock:
                    fetched_new = cache_key in _commit_timestamp_cache and not had_entry

            return timestamp, fetched_new

        # Fetch timestamps concurrently to improve performance
        with ThreadPoolExecutor(
            max_workers=max(
                1, min(MAX_CONCURRENT_TIMESTAMP_FETCHES, len(commit_hashes))
            )
        ) as executor:
            timestamp_results = list(executor.map(_safe_get_timestamp, commit_hashes))

        timestamps = [result[0] for result in timestamp_results]
        fetched_any_new = any(result[1] for result in timestamp_results)

        # Save cache after all timestamps are fetched to avoid race conditions
        if fetched_any_new:
            _save_commit_cache()

        dirs_with_timestamps = list(
            zip(matching_prerelease_dirs, commit_hashes, timestamps, strict=True)
        )

        # Sort prereleases by recency: timestamped items first (by timestamp), then non-timestamped (by commit hash)
        def sort_key(item):
            """
            Create a sort key for a tuple (dir_name, commit_hash, timestamp) that prefers items with timestamps and orders newest first.

            Parameters:
                item (tuple): A 3-tuple of (dir_name, commit_hash, timestamp) where `timestamp` is either a datetime or None.

            Returns:
                tuple: A two-element key (priority, value) where `priority` is 1 for items with a timestamp and 0 otherwise; `value` is the UNIX timestamp (float) for timestamped items or the commit hash string for items without a timestamp (empty string if commit_hash is None).
            """
            _dir_name, commit_hash, timestamp = item

            if timestamp is not None:
                # Primary: items with timestamps, sorted by timestamp (newest first)
                return (1, timestamp.timestamp())
            else:
                # Fallback: items without timestamps, sorted by commit hash (lexicographically descending)
                return (0, commit_hash or "")

        dirs_with_timestamps.sort(key=sort_key, reverse=True)

        # Return the newest one
        return dirs_with_timestamps[0][0] if dirs_with_timestamps else None

    except (requests.RequestException, OSError) as e:
        # Network/IO errors - these are expected and recoverable
        logger.warning(f"Network/IO error finding remote prerelease directories: {e}")
        return None
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        # Parsing/logic errors - these indicate bugs or API changes
        logger.error(
            f"Data parsing error finding remote prerelease directories: {e}",
            exc_info=True,
        )
        return None


def _download_prerelease_assets(
    remote_dir: str,
    prerelease_base_dir: str,
    selected_patterns: List[str],
    exclude_patterns_list: List[str],
    device_manager,
    force_refresh: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Download assets for a specific prerelease directory.

    Parameters:
        remote_dir (str): Remote prerelease directory name to fetch assets from.
        prerelease_base_dir (str): Local base directory where the prerelease directory will be created or updated.
        selected_patterns (List[str]): Patterns used to select which remote assets to consider.
        exclude_patterns_list (List[str]): Patterns used to exclude matching assets.
        device_manager: Optional device-pattern resolver used when matching device-specific assets.
        force_refresh (bool): If True, re-download files even when a valid local copy exists.

    Returns:
        files_downloaded (bool): True if one or more files were downloaded, False otherwise.
        downloaded_files (List[str]): List of filenames that were downloaded.
    """
    remote_files = _iter_matching_prerelease_files(
        remote_dir, selected_patterns, exclude_patterns_list, device_manager
    )

    if not remote_files:
        logger.debug(
            f"No matching prerelease files found in remote directory {remote_dir}"
        )
        return False, []

    logger.debug(f"Found {len(remote_files)} matching prerelease files")

    # The prerelease directory name is the remote directory name
    prerelease_dir_name = remote_dir

    prerelease_dir = os.path.join(prerelease_base_dir, prerelease_dir_name)

    # Create prerelease directory if it doesn't exist
    if not os.path.exists(prerelease_dir):
        try:
            os.makedirs(prerelease_dir, exist_ok=True)
            logger.info(f"Created prerelease directory: {prerelease_dir_name}")
        except OSError as e:
            logger.error(f"Failed to create prerelease directory {prerelease_dir}: {e}")
            return False, []

    # Download missing or corrupted files
    files_downloaded = False
    downloaded_files = []

    for file_info in remote_files:
        file_name = file_info["name"]
        download_url = file_info["download_url"]

        # Use hierarchical path if available, otherwise just filename
        asset_path = file_info.get("path")
        if asset_path:
            parts = asset_path.split(f"{remote_dir}/", 1)
            relative_path = (
                parts[1] if len(parts) == 2 else os.path.basename(asset_path)
            )
            file_path = os.path.normpath(os.path.join(prerelease_dir, relative_path))
        else:
            relative_path = file_name
            file_path = os.path.normpath(os.path.join(prerelease_dir, file_name))

        # Security: Prevent path traversal using robust path validation
        real_prerelease_dir = os.path.realpath(prerelease_dir)
        real_target = os.path.realpath(file_path)
        try:
            common_base = os.path.commonpath([real_prerelease_dir, real_target])
        except ValueError:
            common_base = None
        if common_base != real_prerelease_dir:
            logger.warning(
                f"Skipping asset with unsafe path that escapes base directory: {file_path}"
            )
            continue

        # Ensure parent directory exists for hierarchical paths
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
                logger.debug(f"Created parent directory: {parent_dir}")
            except OSError as e:
                logger.error(f"Failed to create parent directory {parent_dir}: {e}")
                continue

        # Check if file needs download (missing or failed integrity check)
        if force_refresh or _prerelease_needs_download(file_path):
            logger.info(f"Downloading prerelease file: {relative_path}")
            if download_file_with_retry(download_url, file_path):
                files_downloaded = True
                downloaded_files.append(relative_path)
                logger.debug(f"Successfully downloaded: {relative_path}")
            else:
                logger.warning(f"Failed to download prerelease file: {relative_path}")
        else:
            logger.debug(
                f"Prerelease file already exists and is valid: {relative_path}"
            )

    # Set executable permissions on shell scripts
    set_permissions_on_sh_files(prerelease_dir)

    return files_downloaded, downloaded_files


def _cleanup_old_prerelease_dirs(
    prerelease_base_dir: str,
    keep_dir: str,
    existing_dirs: List[str],
) -> None:
    """
    Remove prerelease subdirectories under prerelease_base_dir except for keep_dir.

    Each directory in existing_dirs that does not equal keep_dir is removed using
    the module's safe removal routine to prevent directory-traversal or unsafe
    deletions. Successful removals are logged.

    Parameters:
        prerelease_base_dir (str): Path to the directory containing prerelease subdirs.
        keep_dir (str): Name of the subdirectory to preserve.
        existing_dirs (List[str]): Names of prerelease subdirectories present under prerelease_base_dir.
    """
    for old_dir in existing_dirs:
        if old_dir != keep_dir:
            old_path = os.path.join(prerelease_base_dir, old_dir)
            if _safe_rmtree(old_path, prerelease_base_dir, old_dir):
                logger.info(f"Removed older prerelease: {old_dir}")


def check_for_prereleases(
    download_dir: str,
    latest_release_tag: str,
    selected_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    device_manager=None,
    github_token: Optional[str] = None,
    force_refresh: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Check for newer prerelease firmware matching the expected prerelease version and download any assets that match the provided patterns.

    Updates on-disk prerelease tracking and prunes older prerelease directories when appropriate.

    Parameters:
        download_dir (str): Base download directory containing firmware/prerelease subdirectory.
        latest_release_tag (str): Latest official release tag (e.g., "2.7.6"); used to compute the expected prerelease version.
        selected_patterns (Optional[List[str]]): Asset selection patterns; if empty or None, no prerelease downloads are attempted.
        exclude_patterns (Optional[List[str]]): Patterns to exclude from matching assets.
        device_manager: Optional DeviceHardwareManager used for device-aware pattern matching.
        github_token (Optional[str]): GitHub API token to use when querying remote prerelease directories.
        force_refresh (bool): If True, force remote checks and update tracking even when cached or existing prerelease data exists.

    Returns:
        Tuple[bool, List[str]]: `True` and a list containing the downloaded prerelease directory name if new prerelease assets were downloaded; `False` and a list of existing or inspected prerelease directory name(s) otherwise (empty list if none).
    """
    global downloads_skipped

    if downloads_skipped:
        return False, []

    if not selected_patterns:
        logger.debug("No patterns selected for prerelease downloads")
        return False, []

    exclude_patterns_list = exclude_patterns or []

    # Calculate expected prerelease version
    expected_version = calculate_expected_prerelease_version(latest_release_tag)
    if not expected_version:
        logger.warning(
            f"Could not calculate expected prerelease version from {latest_release_tag}"
        )
        return False, []

    logger.debug(f"Expected prerelease version: {expected_version}")

    # Set up prerelease directory
    prerelease_base_dir = os.path.join(download_dir, "firmware", "prerelease")
    if not os.path.exists(prerelease_base_dir):
        try:
            os.makedirs(prerelease_base_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create prerelease directory: {e}")
            return False, []

    # Check for existing prereleases
    existing_dirs = _get_existing_prerelease_dirs(prerelease_base_dir)

    # Find the newest matching prerelease directory
    matching_dirs = [
        d for d in existing_dirs if extract_version(d).startswith(expected_version)
    ]

    if matching_dirs:
        # Sort by numeric version tuple first, fall back to string for tie-break
        matching_dirs.sort(
            key=lambda d: (
                _get_release_tuple(extract_version(d)) or (),
                extract_version(d),
            ),
            reverse=True,
        )
        newest_dir = matching_dirs[0]
        logger.debug(f"Found existing prerelease: {newest_dir}")
    else:
        newest_dir = None

    # Find the latest remote prerelease directory
    remote_dir = _find_latest_remote_prerelease_dir(
        expected_version, github_token, force_refresh
    )
    if not remote_dir:
        return (False, [newest_dir]) if newest_dir else (False, [])

    # Download assets for the selected prerelease
    files_downloaded, _ = _download_prerelease_assets(
        remote_dir,
        prerelease_base_dir,
        selected_patterns,
        exclude_patterns_list,
        device_manager,
        force_refresh,
    )

    # Update tracking information if files were downloaded
    if files_downloaded or force_refresh:
        update_prerelease_tracking(prerelease_base_dir, latest_release_tag, remote_dir)

    # Only clean up old prerelease directories if we successfully downloaded files
    # or if the remote_dir already existed locally (to prevent deleting last good prerelease)
    should_cleanup = False
    if files_downloaded:
        # Successful download - safe to cleanup old directories
        should_cleanup = True
    elif remote_dir in existing_dirs:
        # Remote directory already exists locally - safe to cleanup others
        should_cleanup = True
    else:
        # Download failed and remote_dir doesn't exist locally - don't cleanup to preserve last good prerelease
        logger.debug(
            f"Skipping prerelease cleanup: download failed for {remote_dir} and it doesn't exist locally"
        )

    if should_cleanup:
        _cleanup_old_prerelease_dirs(prerelease_base_dir, remote_dir, existing_dirs)

    if files_downloaded:
        return True, [remote_dir]
    elif matching_dirs:
        # Existing prerelease found but no new files downloaded
        return False, [matching_dirs[0]]
    else:
        return False, []


def get_commit_timestamp(
    owner: str,
    repo: str,
    commit_hash: str,
    github_token: Optional[str] = None,
    force_refresh: bool = False,
    allow_env_token: bool = True,
) -> Optional[datetime]:
    """
    Get the committer timestamp for a specific commit from GitHub.

    Uses an internal timestamp cache (persisted to disk) to avoid repeated API calls; set `force_refresh` to bypass the cache and refetch.

    Parameters:
        owner (str): Repository owner or organization name.
        repo (str): Repository name.
        commit_hash (str): Commit SHA to look up.
        github_token (Optional[str]): Personal access token to use for the GitHub API; if omitted the function may use the `GITHUB_TOKEN` environment variable when `allow_env_token` is True.
        force_refresh (bool): If True, ignore any cached timestamp and fetch a fresh value from GitHub.
        allow_env_token (bool): If True, permit using the `GITHUB_TOKEN` environment variable when no `github_token` is provided.

    Returns:
        datetime: Commit committer timestamp in UTC if found, `None` otherwise.
    """
    global _commit_timestamp_cache, _commit_cache_loaded

    cache_key = f"{owner}/{repo}/{commit_hash}"

    # Load cache on first access (double-checked locking handled in _load_commit_cache)
    _load_commit_cache()

    with _cache_lock:
        if force_refresh and cache_key in _commit_timestamp_cache:
            del _commit_timestamp_cache[cache_key]
        elif not force_refresh and cache_key in _commit_timestamp_cache:
            timestamp, cached_at = _commit_timestamp_cache[cache_key]
            age = datetime.now(timezone.utc) - cached_at
            if age.total_seconds() < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60:
                logger.debug(
                    f"Using cached commit timestamp for {commit_hash} (cached {age.total_seconds():.0f}s ago)"
                )
                track_api_cache_hit()
                return timestamp
            else:
                del _commit_timestamp_cache[cache_key]

    # Fetch from API
    url = f"{GITHUB_API_BASE}/{owner}/{repo}/commits/{commit_hash}"
    track_api_cache_miss()
    logger.debug(
        f"Cache miss for commit timestamp {owner}/{repo}@{commit_hash[:8]} - fetching from GitHub API"
    )
    try:
        response = make_github_api_request(
            url,
            github_token=github_token,
            allow_env_token=allow_env_token,
            timeout=GITHUB_API_TIMEOUT,
        )
        commit_data = response.json()
        timestamp_str = commit_data.get("commit", {}).get("committer", {}).get("date")
        if timestamp_str:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            logger.debug(f"Successfully fetched commit timestamp for {commit_hash[:8]}")
            with _cache_lock:
                _commit_timestamp_cache[cache_key] = (
                    timestamp,
                    datetime.now(timezone.utc),
                )
            # Persist cache for ad-hoc callers to improve durability
            _save_commit_cache()
            return timestamp
    except (requests.HTTPError, requests.RequestException) as e:
        # Network errors - these are expected and recoverable
        logger.warning(f"Network error getting commit timestamp for {commit_hash}: {e}")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # Parsing/logic errors - these indicate bugs or API changes
        logger.error(
            f"Data parsing error getting commit timestamp for {commit_hash}: {e}",
            exc_info=True,
        )

    return None


def _save_commit_cache() -> None:
    """
    Persist the in-memory commit timestamp cache to the configured cache file.

    Writes the module-level _commit_timestamp_cache to disk using an atomic JSON write; timestamps are stored as ISO 8601 strings and failures are logged.
    """
    global _commit_timestamp_cache
    cache_file = _get_commit_cache_file()

    try:
        with _cache_lock:
            cache_data = {
                cache_key: (timestamp.isoformat(), cached_at.isoformat())
                for cache_key, (timestamp, cached_at) in _commit_timestamp_cache.items()
            }

        # Use atomic write to prevent cache corruption
        if _atomic_write_json(cache_file, cache_data):
            logger.debug(f"Saved {len(cache_data)} commit timestamps to cache")
        else:
            logger.warning(f"Failed to save commit timestamp cache to {cache_file}")

    except (IOError, OSError) as e:
        logger.warning(f"Could not save commit timestamp cache: {e}")


def clear_commit_timestamp_cache() -> None:
    """
    Clear the commit timestamp cache.

    Clears the in-memory commit timestamp cache, marks the cache as not loaded, and removes the on-disk commit cache file if it exists. The operation is performed while holding the module cache lock for thread safety; failures removing the file are logged.
    """
    _clear_commit_cache()
    logger.debug("Cleared commit timestamp cache")


def _send_ntfy_notification(
    ntfy_server: Optional[str],
    ntfy_topic: Optional[str],
    message: str,
    title: Optional[str] = None,
) -> None:
    """
    Send a notification to an NTFY server topic.

    If both ntfy_server and ntfy_topic are provided, posts the given message (and optional title) to the constructed NTFY URL. Logs a debug message on success and logs a warning if the HTTP request fails. If either ntfy_server or ntfy_topic is missing, the function does nothing.
    """
    if ntfy_server and ntfy_topic:
        ntfy_url: str = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
        try:
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


def _get_latest_releases_data(
    url: str,
    scan_count: int = 10,
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
    force_refresh: bool = False,
    release_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches and returns recent releases from a GitHub releases API endpoint.

    This function is cache-aware and will use a persisted cache unless `force_refresh` is set or the
    cached entry has expired. It clamps `scan_count` to GitHub's per-page bounds (1-100), requests
    up to that many releases, and prefers releases sorted by `published_at` in descending order.
    On network or JSON decode errors an empty list is returned. If `published_at` is missing or
    invalid, the original (unsorted) list from the API is returned.

    Parameters:
        url (str): GitHub API URL that returns a list of releases (JSON).
        scan_count (int): Maximum number of releases to request and return (clamped to 1-100).
        github_token (Optional[str]): Optional GitHub API token for higher rate limits.
        allow_env_token (bool): Whether to allow using a token from the environment.
        force_refresh (bool): If True, bypass the cache and fetch fresh data from the API.
        release_type (Optional[str]): Human-readable release type (e.g., "firmware", "Android APK") for logging purposes. If None, falls back to URL-based detection.

    Returns:
        List[Dict[str, Any]]: Sorted list of release dictionaries (newest first). Returns an empty
        list on network or JSON parse errors. If sorting by `published_at` is not possible due to
        missing or invalid keys, the unsorted API list is returned.
    """
    global _releases_cache

    # Clamp per-page to GitHub's bounds and build cache key
    scan_count = max(1, min(100, scan_count))
    cache_key = f"{url}?per_page={scan_count}"

    # Load cache from file on first access
    if not _releases_cache_loaded:
        _load_releases_cache()

    # Determine effective release type for logging
    effective_release_type = release_type
    if not effective_release_type:
        # Fallback to URL parsing for backward compatibility
        url_l = url.lower()
        if "firmware" in url_l:
            effective_release_type = "firmware"
        elif "android" in url_l:
            effective_release_type = "Android APK"

    with _cache_lock:
        if force_refresh and cache_key in _releases_cache:
            del _releases_cache[cache_key]
        elif not force_refresh and cache_key in _releases_cache:
            releases_data, cached_at = _releases_cache[cache_key]
            age = datetime.now(timezone.utc) - cached_at
            if age.total_seconds() < RELEASES_CACHE_EXPIRY_HOURS * 60 * 60:
                track_api_cache_hit()
                if effective_release_type:
                    logger.info(f"Using cached {effective_release_type} releases data")
                else:
                    logger.info("Using cached releases data")
                logger.debug(
                    f"Using cached releases for {url} (cached {age.total_seconds():.0f}s ago)"
                )
                return releases_data
            else:
                # Expired cache entry
                logger.debug(
                    f"Cache expired for releases {url} (was {age.total_seconds():.0f}s ago, limit is {RELEASES_CACHE_EXPIRY_HOURS}h)"
                )
                del _releases_cache[cache_key]

    # Fetch from API after cache miss/expiry or forced refresh
    logger.debug(f"Cache miss for releases {url} - fetching from API")
    track_api_cache_miss()

    try:
        # Add progress feedback
        if effective_release_type:
            logger.info(f"Fetching {effective_release_type} releases from GitHub...")
        else:
            # Fallback for generic case
            logger.info("Fetching releases from GitHub...")

        # scan_count already clamped above
        response: requests.Response = make_github_api_request(
            url,
            github_token=github_token,
            allow_env_token=allow_env_token,
            params={"per_page": scan_count},
            timeout=GITHUB_API_TIMEOUT,
        )

        try:
            releases: List[Dict[str, Any]] = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from {url}: {e}")
            return []

        # Log how many releases were fetched
        logger.debug(f"Fetched {len(releases)} releases from GitHub API")

    except requests.HTTPError as e:
        logger.warning(f"HTTP error fetching releases data from {url}: {e}")
        return []  # Return empty list on error
    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed to fetch releases data from {url}: {e}")
        return []  # Return empty list on error
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to decode JSON response from {url}: {e}")
        return []

    # Sort releases by published date, descending order
    try:
        sorted_releases: List[Dict[str, Any]] = sorted(
            releases,
            key=lambda r: datetime.fromisoformat(
                str(r["published_at"]).replace("Z", "+00:00")
            ),
            reverse=True,
        )
    except (
        TypeError,
        KeyError,
        ValueError,
    ) as e:  # Handle cases where 'published_at' might be missing or not comparable
        logger.warning(
            f"Error sorting releases, 'published_at' key might be missing or invalid: {e}"
        )
        return (
            releases  # Return unsorted or partially sorted if error occurs during sort
        )

    # Cache the result (thread-safe)
    with _cache_lock:
        _releases_cache[cache_key] = (
            sorted_releases[:scan_count],
            datetime.now(timezone.utc),
        )
        logger.debug(
            f"Cached {len(sorted_releases[:scan_count])} releases for {url} (fetched from API)"
        )

    # Save cache to persistent storage
    _save_releases_cache()

    # Limit number of releases to be scanned
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
    config: Dict[str, Any], paths_and_urls: Dict[str, str], force_refresh: bool = False
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
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

    Parameters:
        config (Dict[str, Any]): Configuration dictionary containing download settings.
        paths_and_urls (Dict[str, str]): Mapping of path identifiers to URLs.
        force_refresh (bool): If True, bypass cache and fetch fresh data.

    Returns a tuple of:
    - downloaded firmware versions (List[str]) — includes strings like "pre-release X.Y.Z" for prereleases,
    - newly detected release versions that were not previously recorded (List[str]),
    - list of dictionaries with details for failed downloads (List[Dict[str, str]]),
    - the latest firmware release tag string or None if no releases were found,
    - the latest prerelease version string or None if no prereleases are tracked.
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
    latest_prerelease_version: Optional[str] = None

    if config.get("SAVE_FIRMWARE", False) and config.get(
        "SELECTED_FIRMWARE_ASSETS", []
    ):
        latest_firmware_releases: List[Dict[str, Any]] = _get_latest_releases_data(
            paths_and_urls["firmware_releases_url"],
            config.get(
                "FIRMWARE_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT
            ),  # Use RELEASE_SCAN_COUNT if versions_to_keep not in config
            config.get("GITHUB_TOKEN"),
            force_refresh=force_refresh,
            release_type="firmware",
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
                force_refresh=force_refresh,
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
                        github_token=config.get("GITHUB_TOKEN"),
                        force_refresh=force_refresh,
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
                    latest_prerelease_version = tracking_info.get("latest_prerelease")
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
        latest_prerelease_version,
    )


def _process_apk_downloads(
    config: Dict[str, Any], paths_and_urls: Dict[str, str], force_refresh: bool = False
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
    """
    Download and prune Android APK releases according to configuration.

    When APK saving is enabled and selected APK asset patterns are provided, fetch recent Android release metadata, download matching APK assets into the configured directory, and remove old releases according to the configured retention count.

    Parameters:
        config (Dict[str, Any]): Configuration mapping. Uses keys: `SAVE_APKS`, `SELECTED_APK_ASSETS`, `ANDROID_VERSIONS_TO_KEEP`, and `GITHUB_TOKEN`.
        paths_and_urls (Dict[str, str]): Paths and endpoints. Uses keys: `"android_releases_url"`, `"latest_android_release_file"`, and `"apks_dir"`.
        force_refresh (bool): If True, bypass cached release data and fetch fresh metadata.

    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
        - downloaded_apk_versions: List of release versions successfully downloaded during this run.
        - new_apk_versions: List of discovered release versions (may include versions not downloaded).
        - failed_downloads: List of dicts describing each failed asset download.
        - latest_apk_version: Tag or name of the most recent release found, or `None` if none available.
        - latest_prerelease_version: Always `None` for APKs (prereleases not used).
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
            config.get("GITHUB_TOKEN"),
            force_refresh=force_refresh,
            release_type="Android APK",
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
                force_refresh=force_refresh,
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
        None,  # No prereleases for APKs
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
    latest_prerelease_version: Optional[str] = None,
) -> None:
    """
    Finalize the run by logging a concise summary, showing upgrade guidance if applicable, and sending NTFY notifications about download results.

    Parameters:
        start_time (float): Monotonic epoch timestamp when processing began; used to compute total runtime.
        config (Dict[str, Any]): Configuration mapping. Relevant keys read: "NTFY_SERVER", "NTFY_TOPIC", and "NOTIFY_ON_DOWNLOAD_ONLY".
        downloaded_firmwares (List[str]): List of firmware versions that were downloaded during this run.
        downloaded_apks (List[str]): List of APK versions that were downloaded during this run.
        new_firmware_versions (List[str]): List of available firmware versions that were detected but not downloaded.
        new_apk_versions (List[str]): List of available APK versions that were detected but not downloaded.
        current_version (Optional[str]): Currently running Fetchtastic version, if known.
        latest_version (Optional[str]): Latest released Fetchtastic version, if known.
        update_available (bool): True when a newer Fetchtastic release is available and an upgrade message should be shown.
        latest_firmware_version (Optional[str]): Canonical latest firmware version discovered, if available.
        latest_apk_version (Optional[str]): Canonical latest APK version discovered, if available.
        latest_prerelease_version (Optional[str]): Latest prerelease identifier discovered, if available.

    Side effects:
        - Logs summary and upgrade guidance to the configured logger.
        - May send notifications to an NTFY server/topic when configured.
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
    if latest_prerelease_version:
        logger.info(f"Latest prerelease: {latest_prerelease_version}")

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

    try:
        common = os.path.commonpath([abs_extract_dir, safe_path])
    except ValueError:
        common = None
    if common != abs_extract_dir:
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
    Extract selected files from a ZIP archive into the target directory.

    Only archive members whose base filename matches one of the provided `patterns`
    (via the centralized matcher) and do not match any `exclude_patterns` are
    extracted. If `patterns` is empty, extraction is skipped. The archive's
    internal subdirectory structure is preserved and missing target directories
    are created. Files whose base name ends with the configured shell-script
    extension are made executable after extraction. Unsafe extraction paths are
    skipped (validated via safe_extract_path). If the ZIP is corrupted it will be
    removed; IO, OS, and ZIP errors are logged and handled internally.

    Parameters:
        zip_path (str): Path to the ZIP archive to read.
        extract_dir (str): Destination directory where files will be extracted.
        patterns (List[str]): Inclusion patterns used by the centralized matcher.
            An empty list means "do not extract anything."
        exclude_patterns (List[str]): Glob-style patterns (fnmatch) applied to the
            base filename to exclude matching entries.
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
                if _matches_exclude(base_name, exclude_patterns):
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
                            with (
                                zip_ref.open(file_info) as source,
                                open(target_path, "wb") as target_file,
                            ):
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
        if _matches_exclude(file_name, exclude_patterns):
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
    force_refresh: bool = False,
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
    - Honors a global Wi-Fi gating flag: if downloads are skipped globally, the function will not perform downloads and instead returns newer release tags.

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
    - force_refresh: If True, bypass cache and fetch fresh data.

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
            if not force_refresh and _is_release_complete(
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
                        if _atomic_write_text(
                            release_notes_file, release_notes_content
                        ):
                            logger.debug(f"Saved release notes to {release_notes_file}")
                        else:
                            logger.warning(
                                f"Could not atomically write release notes to {release_notes_file}"
                            )
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
                if _matches_exclude(file_name, exclude_patterns_list):
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
                    if force_refresh:
                        if not _prepare_for_redownload(asset_download_path):
                            continue
                        assets_to_download.append(
                            (browser_download_url, asset_download_path)
                        )
                        continue
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
                # Also add to new_versions_available if this is a newer release than what was saved
                if (
                    saved_release_tag is None
                    or compare_versions(release_tag, saved_release_tag) > 0
                ):
                    new_versions_available.append(release_tag)

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

    # Determine tags newer than saved tag by position (list is newest-first)
    tags_order: List[str] = [
        tag
        for rd in releases_to_download
        if (tag := _sanitize_path_component(rd.get("tag_name"))) is not None
    ]
    newer_tags: List[str] = _newer_tags_since_saved(tags_order, saved_release_tag)

    # Report all newer releases that were not successfully downloaded as newly available.
    # This ensures users are notified about new versions even if the download failed.
    new_candidates: List[str] = [t for t in newer_tags if t not in downloaded_versions]

    if not actions_taken and not new_candidates:
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
    Determine whether a ZIP archive contains any files that match the given patterns and are not already present in the extraction directory.

    Checks archive members (skipping directories), ignores entries whose base filename matches any pattern in `exclude_patterns`, and matches remaining base filenames against `patterns`. If `patterns` is empty, the function returns False.

    Parameters:
        zip_path (str): Path to the ZIP archive to inspect.
        extract_dir (str): Target extraction directory used to check for existing files.
        patterns (List[str]): Inclusion patterns to match against each member's base filename.
        exclude_patterns (List[str]): Exclusion patterns; matching base filenames are ignored.

    Returns:
        bool: `true` if at least one matched file in the ZIP is missing from `extract_dir` (extraction needed), `false` otherwise.

    Notes:
        - If the ZIP is corrupted (`zipfile.BadZipFile`), the function attempts to remove the ZIP file and returns `false`.
        - If an IO/OSError or another unexpected exception occurs while inspecting the ZIP, the function conservatively returns `true` (assumes extraction is needed).
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
                if _matches_exclude(base_name, exclude_patterns):
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


def _format_api_summary(summary: Dict[str, Any]) -> str:
    """
    Format API request statistics into a concise, human-readable string for logging.

    Parameters:
    summary (dict): A mapping with API telemetry values. Recognized keys:
            - "auth_used" (bool): Whether authenticated requests were used.
            - "total_requests" (int): Total number of GitHub API requests performed.
            - "cache_hits" (int): Number of cache hits.
            - "cache_misses" (int): Number of cache misses.
            - "rate_limit_remaining" (int, optional): Remaining requests before rate limit.
            - "rate_limit_reset" (datetime.datetime, optional): UTC datetime when the rate limit resets.

    Returns:
        str: A single-line summary string describing GitHub API requests made, cache hit/miss counts and rate limit status.
        Note: This tracks GitHub API calls only, not file downloads from release assets.
    """
    auth_status = "🔐 authenticated" if summary["auth_used"] else "🌐 unauthenticated"
    requests_str = "request" if summary["total_requests"] == 1 else "requests"
    log_parts = [
        f"📊 GitHub API Summary: {summary['total_requests']} API {requests_str} ({auth_status})"
    ]

    # Add cache statistics if there were any cache operations
    total_cache_lookups = summary["cache_hits"] + summary["cache_misses"]
    if total_cache_lookups > 0:
        cache_hit_rate = (summary["cache_hits"] / total_cache_lookups) * 100
        hits_str = "hit" if summary["cache_hits"] == 1 else "hits"
        misses_str = "miss" if summary["cache_misses"] == 1 else "misses"
        log_parts.append(
            f"{total_cache_lookups} cache lookups → "
            f"{summary['cache_hits']} {hits_str} (skipped), "
            f"{summary['cache_misses']} {misses_str} (fetched) "
            f"[{cache_hit_rate:.1f}% hit rate]"
        )

    # Add rate limit info if available
    remaining = summary.get("rate_limit_remaining")
    reset_time = summary.get("rate_limit_reset")

    if remaining is not None:
        remaining_str = "request" if remaining == 1 else "requests"
        if isinstance(reset_time, datetime):
            time_until_reset = reset_time - datetime.now(timezone.utc)
            if time_until_reset.total_seconds() > 0:
                minutes_until_reset = int(time_until_reset.total_seconds() / 60)
                log_parts.append(
                    f"{remaining} {remaining_str} remaining (resets in {minutes_until_reset} min)"
                )
            else:
                log_parts.append(f"{remaining} {remaining_str} remaining")
        else:
            # reset_time is None or not a datetime, just show remaining
            log_parts.append(f"{remaining} {remaining_str} remaining")

    return ", ".join(log_parts)


def main(force_refresh: bool = False) -> None:
    """
    Run the Fetchtastic downloader workflow.

    Performs initial setup, optionally clears caches when force_refresh is True, enforces Wi-Fi gating, processes firmware and APK downloads (including retries for failures), and finalizes by logging summary and sending notifications.

    Parameters:
        force_refresh (bool): If True, clear all persistent caches and device hardware cache before fetching remote data.
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

    # Show token warning consistently at the start before any API calls
    effective_token = get_effective_github_token(config.get("GITHUB_TOKEN"), True)
    _show_token_warning_if_needed(effective_token)

    # Clear caches if force refresh is requested
    if force_refresh:
        logger.info("Force refresh requested - clearing caches...")
        clear_all_caches()
        # Clear device hardware cache
        device_manager = DeviceHardwareManager()
        device_manager.clear_cache()

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
        latest_prerelease_version,
    ) = _process_firmware_downloads(config, paths_and_urls, force_refresh)
    downloaded_apks, new_apk_versions, failed_apk_list, latest_apk_version, _ = (
        _process_apk_downloads(config, paths_and_urls, force_refresh)
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
        latest_prerelease_version,
    )

    # Log API request summary
    summary = get_api_request_summary()
    if summary["total_requests"] > 0:
        logger.info(_format_api_summary(summary))
    else:
        logger.info(
            "📊 GitHub API Summary: No API requests made (all data served from cache)"
        )


if __name__ == "__main__":
    main()
