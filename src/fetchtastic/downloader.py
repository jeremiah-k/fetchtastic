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
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    ThreadPoolExecutor,
    wait,
)
from datetime import datetime, timezone
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

import platformdirs
import requests
from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

# Try to import LegacyVersion for type annotations (available in older packaging versions)
if TYPE_CHECKING:
    from concurrent.futures import Future

    try:
        from packaging.version import LegacyVersion  # type: ignore
    except ImportError:
        LegacyVersion = None  # type: ignore
else:
    LegacyVersion = None  # Runtime fallback


from fetchtastic import menu_repo, setup_config
from fetchtastic.constants import (
    APK_PRERELEASES_DIR_NAME,
    COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_CHECK_APK_PRERELEASES,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    DEFAULT_PRERELEASE_ACTIVE,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    DEFAULT_PRERELEASE_STATUS,
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
    EXECUTABLE_PERMISSIONS,
    FILE_TYPE_PREFIXES,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
    FIRMWARE_PRERELEASES_DIR_NAME,
    GITHUB_API_BASE,
    GITHUB_API_TIMEOUT,
    GITHUB_MAX_PER_PAGE,
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    MAX_CONCURRENT_TIMESTAMP_FETCHES,
    MESHTASTIC_ANDROID_RELEASES_URL,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
    MIN_RATE_LIMIT_FOR_COMMIT_DETAILS,
    NTFY_REQUEST_TIMEOUT,
    PRERELEASE_ADD_COMMIT_PATTERN,
    PRERELEASE_COMMIT_HISTORY_FILE,
    PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
    PRERELEASE_COMMITS_CACHE_FILE,
    PRERELEASE_COMMITS_LEGACY_FILE,
    PRERELEASE_DELETE_COMMIT_PATTERN,
    PRERELEASE_DETAIL_ATTEMPT_MULTIPLIER,
    PRERELEASE_DETAIL_FETCH_WORKERS,
    PRERELEASE_TRACKING_JSON_FILE,
    RELEASE_SCAN_COUNT,
    RELEASES_CACHE_EXPIRY_HOURS,
    REPO_DOWNLOADS_DIR,
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
PRERELEASE_ADD_RX = re.compile(PRERELEASE_ADD_COMMIT_PATTERN, re.IGNORECASE)
PRERELEASE_DELETE_RX = re.compile(PRERELEASE_DELETE_COMMIT_PATTERN, re.IGNORECASE)
PRERELEASE_DIR_SEGMENT_RX = re.compile(
    r"(firmware-\d+\.\d+\.\d+\.[a-f0-9]{6,})", re.IGNORECASE
)


def _normalize_version(
    version: Optional[str],
) -> Optional[Union[Version, Any]]:  # Use Any when LegacyVersion not available
    """
    Normalize repository-style version strings into a PEP 440-compatible form.

    Recognizes and strips a leading "v", converts common prerelease markers (e.g., "alpha"/"beta" with optional numeric fragment)
    into PEP 440 prerelease forms, and converts trailing commit/hash-like suffixes into local version identifiers when possible.
    Returns None for empty, missing, or otherwise unparsable inputs.

    Parameters:
        version (Optional[str]): Raw version string that may include a leading "v", prerelease words, or a hash suffix.

    Returns:
        Optional[Union[Version, Any]]: A parsed `Version` or `LegacyVersion`-like object when parsing succeeds; `None` otherwise.
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
    Return the numeric release components (major, minor, patch, ...) extracted from a version string.

    Parameters:
        version (Optional[str]): Version string to parse. May include a leading "v" and additional metadata; only the numeric leading segments are considered.

    Returns:
        Optional[tuple[int, ...]]: Tuple of integer release components (e.g., (1, 2, 3)) when a numeric release can be determined, or `None` if the input is empty or no numeric release segments can be parsed.
    """
    if version is None:
        return None

    version_stripped = version.strip()
    if not version_stripped:
        return None

    base = (
        version_stripped[1:]
        if version_stripped.lower().startswith("v")
        else version_stripped
    )
    match = VERSION_BASE_RX.match(base)
    base_tuple = (
        tuple(int(part) for part in match.group(1).split(".")) if match else None
    )

    normalized = _normalize_version(version_stripped)
    normalized_tuple = (
        normalized.release
        if isinstance(normalized, Version) and normalized.release
        else None
    )

    if base_tuple and normalized_tuple:
        return (
            base_tuple if len(base_tuple) > len(normalized_tuple) else normalized_tuple
        )
    return base_tuple or normalized_tuple


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

    Scans the firmware/prerelease directory under download_dir and removes prerelease directories or unsafe symlinks whose base version is less than or equal to latest_release_tag. If no prerelease directories remain, associated prerelease tracking files are also removed.

    Parameters:
        download_dir (str): Base download directory containing firmware/prerelease.
        latest_release_tag (str): Latest official release tag (may include a leading 'v').

    Returns:
        bool: `True` if one or more prerelease directories or symlinks were removed, `False` otherwise.
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

    # Path to prerelease directory
    prerelease_dir = os.path.join(download_dir, "firmware", "prerelease")
    if not os.path.exists(prerelease_dir):
        return False

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

            if latest_release_tuple is not None and dir_release_tuple is not None:
                if dir_release_tuple > latest_release_tuple:
                    continue
                # Prerelease is older or same version, so it's superseded.
                should_cleanup = True
                cleanup_reason = (
                    f"it is superseded by release {safe_latest_release_tag}"
                )

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
            # JSON tracking file is stored in the cache directory
            json_tracking_file = os.path.join(
                _ensure_cache_dir(), PRERELEASE_TRACKING_JSON_FILE
            )

            # Remove tracking files (both JSON and legacy text)
            for file_path, is_legacy in [
                (json_tracking_file, False),
                (os.path.join(prerelease_dir, PRERELEASE_COMMITS_LEGACY_FILE), True),
            ]:
                if os.path.exists(file_path):
                    file_type = "legacy prerelease" if is_legacy else "prerelease"
                    try:
                        os.remove(file_path)
                        logger.debug(
                            "Removed %s tracking file: %s", file_type, file_path
                        )
                    except OSError as e:
                        logger.warning(
                            "Could not remove %s tracking file %s: %s",
                            file_type,
                            file_path,
                            e,
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
        logger.error("Could not create temporary file for %s: %s", file_path, e)
        return False
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
            writer_func(temp_f)
        os.replace(temp_path, file_path)
    except (IOError, UnicodeEncodeError, OSError) as e:
        logger.error("Could not write to %s: %s", file_path, e)
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
    Remove a file or directory only if it safely resides under a permitted base directory.

    If `path_to_remove` is a symlink it is unlinked; otherwise the path is resolved to its real path and removed only when that resolved path is located under `base_dir`. `item_name` is used for log messages.

    Parameters:
        path_to_remove (str): The filesystem path to remove.
        base_dir (str): The allowed base directory; removal is skipped if the resolved path is outside this directory.
        item_name (str): Human-readable name for logging.

    Returns:
        bool: `True` if the path was removed successfully; `False` on error or if the resolved path is outside `base_dir`.
    """
    try:
        real_base_dir = os.path.realpath(base_dir)

        if os.path.islink(path_to_remove):
            link_dir = os.path.dirname(os.path.abspath(path_to_remove))
            real_link_dir = os.path.realpath(link_dir)

            if not _is_within_base(real_base_dir, real_link_dir):
                logger.warning(
                    "Skipping removal of symlink %s because its location is outside the base directory",
                    path_to_remove,
                )
                return False

            logger.info("Removing symlink: %s", item_name)
            os.unlink(path_to_remove)
            return True

        real_target = os.path.realpath(path_to_remove)
        if not _is_within_base(real_base_dir, real_target):
            logger.warning(
                "Skipping removal of %s because it resolves outside the base directory",
                path_to_remove,
            )
            return False

        if os.path.isdir(path_to_remove):
            shutil.rmtree(path_to_remove)
        else:
            os.remove(path_to_remove)
    except OSError as e:
        logger.error("Error removing %s: %s", path_to_remove, e)
        return False
    else:
        return True


def compare_file_hashes(file1, file2):
    """
    Determine whether two files have identical SHA-256 hashes.

    Parameters:
        file1 (str): Path to the first file to compare.
        file2 (str): Path to the second file to compare.

    Returns:
        bool: True if both files are readable and their SHA-256 digests match, False otherwise.
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
            logger.warning("File does not exist for hashing: %s", file_path)
            return None

        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                # Read and update hash in chunks of 4K
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except IOError as e:
            logger.error("Error reading file %s for hashing: %s", file_path, e)
            return None

    hash1 = get_file_hash(file1)
    hash2 = get_file_hash(file2)

    if hash1 is None or hash2 is None:
        return False

    return hash1 == hash2


def _read_latest_release_tag(json_file: str) -> Optional[str]:
    """
    Read the latest release tag stored under the top-level `latest_version` key in a JSON file.

    Parameters:
        json_file (str): Path to the JSON file to read.

    Returns:
        Optional[str]: The stripped `latest_version` string if present and non-empty, otherwise `None`.
    """
    if os.path.exists(json_file):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    version = data.get("latest_version")
                    return (
                        version.strip()
                        if isinstance(version, str) and version.strip()
                        else None
                    )
                else:
                    logger.debug(
                        "Unexpected JSON structure in %s (type %s); expected object",
                        json_file,
                        type(data).__name__,
                    )
        except (IOError, json.JSONDecodeError) as e:
            logger.debug(
                "Could not read release tag from JSON file %s: %s", json_file, e
            )
    return None


def _write_latest_release_tag(
    json_file: str, version_tag: str, release_type: str
) -> bool:
    """
    Persist the latest release tag and related metadata to a JSON file for the given release type.

    Parameters:
        json_file (str): Filesystem path where the JSON will be written.
        version_tag (str): Release tag to record (e.g., "v1.2.3").
        release_type (str): Human-readable release category (e.g., "Firmware", "Android APK"); used to derive a file_type slug.

    Returns:
        bool: `True` if the JSON was written successfully, `False` otherwise.

    Notes:
        The written JSON contains `latest_version`, `file_type` (derived slug), and `last_updated` as an ISO 8601 UTC timestamp.
    """
    release_type_l = release_type.lower()
    file_type_slug = (
        "android"
        if "android" in release_type_l
        else (
            "firmware"
            if "firmware" in release_type_l
            else release_type_l.replace(" ", "_")
        )
    )
    data = {
        "latest_version": version_tag,
        "file_type": file_type_slug,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    success = _atomic_write_json(json_file, data)
    if not success:
        logger.warning("Failed to write latest release tag to JSON file: %s", json_file)
    return success


def _ensure_v_prefix_if_missing(version: Optional[str]) -> Optional[str]:
    """
    Ensure a version string begins with a leading "v".

    Parameters:
        version (Optional[str]): Version string to normalize; leading/trailing whitespace is stripped. If `None`, no normalization is performed.

    Returns:
        Optional[str]: `None` if `version` is `None`; otherwise the input string with a leading "v" added if it did not already start with "v" or "V".
    """
    if version is None:
        return None
    version = version.strip()
    if version and not version.lower().startswith("v"):
        return f"v{version}"
    return version


def _matches_exclude(name: str, patterns: List[str]) -> bool:
    """
    Check whether a filename matches any exclude pattern using case-insensitive glob matching.

    Parameters:
        name (str): The filename or path component to test.
        patterns (List[str]): Glob-style exclude patterns to match against (case-insensitive).

    Returns:
        bool: True if `name` matches any pattern in `patterns`, False otherwise.
    """
    name_l = name.lower()
    return any(fnmatch.fnmatch(name_l, p.lower()) for p in patterns)


def _get_json_release_basename(release_type: str) -> str:
    """
    Get the JSON basename used to record the latest release for a release type.

    Parameters:
        release_type (str): Human-readable release type (for example "Android APK" or "Firmware").

    Returns:
        str: Filename basename to use for the latest-release JSON (e.g., the value of LATEST_ANDROID_RELEASE_JSON_FILE/LATEST_ANDROID_PRERELEASE_JSON_FILE for APKs, LATEST_FIRMWARE_RELEASE_JSON_FILE/LATEST_FIRMWARE_PRERELEASE_JSON_FILE for firmware, or "latest_release.json" for other types).
    """
    release_type_lower = release_type.lower()
    # Check for firmware prerelease first (most specific)
    if "firmware prerelease" in release_type_lower:
        return LATEST_FIRMWARE_PRERELEASE_JSON_FILE
    # Check for firmware stable releases
    if "firmware" in release_type_lower:
        return LATEST_FIRMWARE_RELEASE_JSON_FILE
    # Check for APK prerelease
    if "android apk prerelease" in release_type_lower:
        return LATEST_ANDROID_PRERELEASE_JSON_FILE
    # Check for APK stable releases
    if "android" in release_type_lower:
        return LATEST_ANDROID_RELEASE_JSON_FILE
    return "latest_release.json"


def _normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    """
    Normalize a commit identifier into a version-plus-hash form.

    If `commit_id` already contains a numeric version followed by a hex hash (e.g., "2.7.13.abcdef"), it is returned unchanged. If `commit_id` is a hex hash only, `release_version` (when provided) is used to derive a numeric version (leading "v" is removed) and the result is returned as "MAJOR.MINOR.PATCH.hash". Otherwise the original `commit_id` is returned unchanged.

    Parameters:
        commit_id: Commit identifier (hash-only or version+hash).
        release_version: Optional release tag (e.g., "v2.7.13") used to infer the version for hash-only identifiers.

    Returns:
        A normalized commit identifier in `MAJOR.MINOR.PATCH.hash` form when possible; otherwise the original `commit_id`.
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
            "Invalid commits format in tracking file: expected list, got %s. Resetting commits.",
            type(commits_raw).__name__,
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
                "Invalid commit entry in tracking file: expected str, got %s. Skipping.",
                type(commit).__name__,
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
            "Invalid commits format in legacy tracking file: expected list, got %s. Resetting commits.",
            type(commits_raw).__name__,
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
    Parse prerelease tracking JSON and normalize it to a (commits, current_release, last_updated) tuple.

    Supports the current JSON schema (contains "version" and either "hash" or "commits", optional "last_updated" or "timestamp") and a legacy JSON schema (keys like "release" and "commits"). If the file contains non-dict JSON or cannot be read/decoded, returns empty/None values.

    Returns:
        tuple: (commits, current_release, last_updated)
            commits (list[str]): Ordered list of prerelease identifiers (may be empty).
            current_release (str | None): Release tag associated with the commits, or None if not present.
            last_updated (str | None): ISO timestamp from the tracking JSON, or None if unavailable.
    """
    commits = []
    current_release = None
    last_updated = None

    if os.path.exists(tracking_file):
        try:
            with open(tracking_file, "r", encoding="utf-8") as f:
                tracking_data = json.load(f)

                if not isinstance(tracking_data, dict):
                    logger.warning(
                        "Unexpected JSON type in prerelease tracking file %s: %s",
                        tracking_file,
                        type(tracking_data).__name__,
                    )
                    return commits, current_release, last_updated

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
            logger.warning("Could not read prerelease tracking file: %s", e)

    return commits, current_release, last_updated


def _get_existing_prerelease_dirs(prerelease_dir: str) -> list[str]:
    """
    List safe prerelease directory names directly under the given prerelease directory.

    Symlinks are ignored to avoid traversing outside the managed prerelease tree; entries are validated and sanitized before being returned.

    Returns:
        list[str]: Sanitized names of direct subdirectories that start with `FIRMWARE_DIR_PREFIX`. Returns an empty list if the directory does not exist, contains no valid prerelease dirs, or on scan error.
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
        logger.debug("Error scanning prerelease dir %s: %s", prerelease_dir, e)

    return entries


def _get_string_list_from_config(config: Dict[str, Any], key: str) -> List[str]:
    """
    Safely retrieves a list of strings from the configuration.

    Parameters:
        config (Dict[str, Any]): Configuration mapping.
        key (str): The key to retrieve from the configuration.

    Returns:
        List[str]: A list of strings extracted from the configuration.
    """
    value = config.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(p) for p in value if isinstance(p, (str, bytes))]


def _get_prerelease_patterns(config: dict) -> list[str]:
    """
    Get the file-selection patterns used to identify prerelease assets.

    Prefers the `SELECTED_PRERELEASE_ASSETS` key in `config`; if absent, falls back to the legacy
    `EXTRACT_PATTERNS` key and emits a deprecation warning. Always returns a list (empty if no
    patterns are configured).

    Parameters:
        config (dict): Configuration mapping that may contain `SELECTED_PRERELEASE_ASSETS` or the
            legacy `EXTRACT_PATTERNS` key.

    Returns:
        list[str]: The list of prerelease asset selection patterns.
    """
    # Check for new dedicated configuration key first
    if "SELECTED_PRERELEASE_ASSETS" in config:
        return _get_string_list_from_config(config, "SELECTED_PRERELEASE_ASSETS")

    # Fall back to EXTRACT_PATTERNS for backward compatibility
    extract_patterns = _get_string_list_from_config(config, "EXTRACT_PATTERNS")
    if extract_patterns:
        logger.warning(
            "Using EXTRACT_PATTERNS for prerelease file selection is deprecated. "
            "Please re-run 'fetchtastic setup' to update your configuration."
        )

    return extract_patterns


def update_prerelease_tracking(latest_release_tag, current_prerelease):
    """
    Record a single prerelease commit in prerelease_tracking.json for the current prerelease directory.

    Parameters:
        latest_release_tag (str): Latest official release tag used to determine whether existing prerelease tracking should be reset.
        current_prerelease (str): Name of the current prerelease directory (from which the prerelease commit is extracted).

    Returns:
        int: Number of prerelease commits persisted to disk; `0` if no update was written.
    """
    result = _update_tracking_with_newest_prerelease(
        latest_release_tag, current_prerelease
    )
    return result if result is not None else 0


def _update_tracking_with_newest_prerelease(
    latest_release_tag: str, newest_prerelease_dir: str
) -> Optional[int]:
    """
    Record the newest prerelease identifier in the prerelease tracking JSON and update the tracked commits list.

    If the official release tag has changed since last tracking, reset the tracked prerelease commits before adding the newest prerelease. Ignores directories that do not match the firmware prerelease naming pattern.

    Parameters:
        latest_release_tag (str): Official release tag used to determine whether tracking should be reset.
        newest_prerelease_dir (str): Name of the newest prerelease directory (expected to start with the firmware prefix).

    Returns:
        Optional[int]: Number of tracked prerelease commits after the update;
            `0` if `newest_prerelease_dir` is empty or invalid;
            `None` if the tracking file could not be written to disk.
    """
    if not newest_prerelease_dir:
        return 0

    tracking_file = os.path.join(_ensure_cache_dir(), PRERELEASE_TRACKING_JSON_FILE)

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
            "New release %s detected (previously tracking %s). Resetting prerelease tracking.",
            latest_release_tag,
            existing_release,
        )
        existing_commits = []

    # Check if this is a new prerelease ID for the same version
    is_new_id = new_prerelease_id not in set(existing_commits)

    # Only update if it's a new prerelease ID
    if not is_new_id:
        logger.debug(
            "Prerelease %s already tracked, no update needed", new_prerelease_id
        )
        return len(existing_commits)

    # Update tracking with the new prerelease ID
    updated_commits = list(dict.fromkeys([*existing_commits, new_prerelease_id]))
    # Extract commit hash for the newest prerelease directory
    commit_hash = _get_commit_hash_from_dir(newest_prerelease_dir)

    # Write updated tracking data in new format
    now_iso = datetime.now(timezone.utc).isoformat()
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
        "Prerelease tracking updated: %d prerelease IDs tracked, latest: %s",
        len(updated_commits),
        new_prerelease_id,
    )
    return len(updated_commits)


def matches_extract_patterns(filename, extract_patterns, device_manager=None):
    """
    Determine whether a filename matches any of the configured extract patterns.

    Matching is case-insensitive and supports file-type prefix patterns, device identifier patterns
    (as identified by an optional device manager), the special "littlefs-" prefix, and a generic
    substring fallback.

    Parameters:
        device_manager (optional): An object exposing is_device_pattern(pattern) used to identify
            device-style patterns; may be omitted.

    Returns:
        `true` if any pattern matches the filename, `false` otherwise.
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


def _extract_identifier_from_entry(entry: Dict[str, Any]) -> str:
    """
    Return the identifier for a prerelease history entry.

    Checks the mapping for keys in priority order: "identifier", "directory", then "dir", and returns the first non-empty value found. If none are present, returns an empty string.

    Parameters:
        entry (dict): History entry mapping potentially containing identifier fields.

    Returns:
        identifier (str): The extracted identifier or an empty string if not found.
    """
    return entry.get("identifier") or entry.get("directory") or entry.get("dir") or ""


def _is_entry_deleted(entry: Dict[str, Any]) -> bool:
    """
    Check whether a prerelease history entry is marked as deleted.

    Parameters:
        entry (dict): History entry mapping; the presence of `"removed_at"` or `"status"` equal to `"deleted"` indicate deletion.

    Returns:
        `true` if the entry is marked deleted, `false` otherwise.
    """
    return entry.get("status") == "deleted" or bool(entry.get("removed_at"))


def _format_history_entry(
    entry: Dict[str, Any],
    idx: int,
    latest_active_identifier: Optional[str],
) -> Dict[str, Any]:
    """
    Produce a display-ready prerelease history entry augmented with UI markup and status flags.

    Parameters:
        entry (dict): Original prerelease history entry; should include an identifier field
            accessible by keys like "identifier", "directory", or "dir".
        idx (int): Position of the entry in a sorted history list where 0 denotes the newest entry.
        latest_active_identifier (str | None): Identifier of the currently active prerelease, or
            None if there is no active prerelease.

    Returns:
        dict: A copy of the original entry augmented with:
            - "display_name": the extracted identifier,
            - "markup_label": a UI-ready label (may contain markup for deleted/new/latest),
            - "is_deleted": `True` if the entry is marked deleted,
            - "is_newest": `True` if idx == 0,
            - "is_latest": `True` if the entry matches the latest_active_identifier.
    """
    identifier = _extract_identifier_from_entry(entry)
    if not identifier:
        return entry

    is_deleted = _is_entry_deleted(entry)
    is_newest = idx == 0
    is_latest_active = (
        not is_deleted
        and latest_active_identifier is not None
        and identifier == latest_active_identifier
    )

    if is_deleted:
        markup_label = f"[red][strike]{identifier}[/strike][/red]"
    elif is_newest or is_latest_active:
        markup_label = f"[green]{identifier}[/]"
    else:
        markup_label = identifier

    formatted_entry = dict(entry)
    formatted_entry.update(
        {
            "display_name": identifier,
            "markup_label": markup_label,
            "is_deleted": is_deleted,
            "is_newest": is_newest,
            "is_latest": is_latest_active,
        }
    )
    return formatted_entry


def _sort_key(entry: Dict[str, Any]) -> tuple:
    """
    Return a sort key that orders prerelease entries by their most recent activity.

    Parameters:
        entry (Dict[str, Any]): Prerelease entry that may include `added_at`, `removed_at`, and `identifier` keys.

    Returns:
        tuple: `(most_recent_timestamp, identifier)` where `most_recent_timestamp` is the later of `added_at` and `removed_at` (or `""` if missing) and `identifier` is the entry's `identifier` (or `""` if missing).
    """
    added_at = entry.get("added_at") or ""
    removed_at = entry.get("removed_at") or ""
    # Use the most recent timestamp for sorting
    most_recent = max(added_at, removed_at)
    return (most_recent, entry.get("identifier", ""))


def get_prerelease_tracking_info(
    github_token: Optional[str] = None,
    force_refresh: bool = False,
    allow_env_token: bool = True,
):
    """
    Summarizes tracked prerelease metadata and commit-derived prerelease history.

    Reads normalized prerelease tracking from the user's prerelease_tracking.json, augments it with repository-derived prerelease history when available, and returns a consolidated dictionary describing the tracked official release, expected prerelease base version, tracked identifiers, history entries, counts, and timestamps.

    Returns:
        dict: Empty dict if no tracking data is present; otherwise a dictionary with keys:
            - "release" (str | None): Tracked official release tag or None.
            - "expected_version" (str | None): Calculated prerelease base version derived from "release".
            - "commits" (list[str]): Ordered list of tracked prerelease identifiers from tracking data.
            - "prerelease_count" (int): Number of prerelease identifiers discovered (from history when available, otherwise from tracking).
            - "last_updated" (str | None): ISO 8601 timestamp of the last tracking update, or None.
            - "latest_prerelease" (str | None): Best-effort most recent prerelease identifier (history-active, history-most-recent, or tracked fallback).
            - "history" (list[dict]): Newest-first list of commit-derived prerelease history entries; each entry includes at least:
                * "identifier" (str): Prerelease identifier extracted from the entry.
                * "display_name" (str): Human-friendly identifier text.
                * "markup_label" (str): Presentation label (used for UI/markup).
                * "is_deleted" (bool): True if the entry represents a deletion.
                * "is_newest" (bool): True for the newest history entry.
                * "is_latest" (bool): True for the most recent active entry when available.
            - "history_created" (int): Count of history entries discovered.
            - "history_deleted" (int): Count of history entries flagged as deleted.
            - "history_active" (int | None): Count of active prereleases according to history, or None when history is unavailable.
    """
    tracking_file = os.path.join(_ensure_cache_dir(), PRERELEASE_TRACKING_JSON_FILE)
    commits, release, last_updated = _read_prerelease_tracking_data(tracking_file)
    if not commits and not release:
        return {}

    history_entries: List[Dict[str, Any]] = []
    expected_prerelease_version = None
    if release:
        expected_prerelease_version = calculate_expected_prerelease_version(release)

    if expected_prerelease_version:
        try:
            history_entries = _get_prerelease_commit_history(
                expected_prerelease_version,
                github_token=github_token,
                force_refresh=force_refresh,
                allow_env_token=allow_env_token,
            )
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:  # pragma: no cover - defensive safety
            logger.debug(
                "Failed to load prerelease commit history for %s: %s",
                expected_prerelease_version,
                exc,
            )

    # Identify most recent active identifier for highlighting
    latest_active_identifier: Optional[str] = None
    for entry in history_entries:
        identifier = _extract_identifier_from_entry(entry)
        if not identifier:
            continue
        if not _is_entry_deleted(entry):
            latest_active_identifier = identifier
            break

    formatted_history: List[Dict[str, Any]] = []
    deleted_count = 0
    for idx, entry in enumerate(history_entries):
        formatted_entry = _format_history_entry(entry, idx, latest_active_identifier)
        if formatted_entry.get("is_deleted"):
            deleted_count += 1
        formatted_history.append(formatted_entry)

    created_count = len(formatted_history)
    counted_commits = created_count if created_count else len(commits or [])
    active_count = max(created_count - deleted_count, 0) if created_count else None

    # Fallback logic for latest prerelease identifier:
    # 1. Use latest active identifier if available.
    # 2. Otherwise use the most recent entry from history.
    # 3. Otherwise fall back to the tracked commits list.
    latest_prerelease_identifier = (
        latest_active_identifier
        or (formatted_history[0].get("identifier") if formatted_history else None)
        or (commits[-1] if commits else None)
    )

    return {
        "release": release,
        "expected_version": expected_prerelease_version,
        "commits": commits,
        "prerelease_count": counted_commits,
        "last_updated": last_updated,
        "latest_prerelease": latest_prerelease_identifier,
        "history": formatted_history,
        "history_created": created_count,
        "history_deleted": deleted_count,
        "history_active": active_count,
    }


def _iter_matching_prerelease_files(
    dir_name: str,
    selected_patterns: list,
    exclude_patterns_list: list,
    device_manager,
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
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

    files = (
        menu_repo.fetch_directory_contents(
            dir_name, allow_env_token=allow_env_token, github_token=github_token
        )
        or []
    )
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
            logger.debug("Removed existing file: %s", file_path)

        hash_path = get_hash_file_path(file_path)
        if os.path.exists(hash_path):
            os.remove(hash_path)
            logger.debug("Removed stale hash file: %s", hash_path)

        # Also remove any orphaned temp files from previous runs
        for tmp_path in glob.glob(f"{glob.escape(file_path)}.tmp.*"):
            os.remove(tmp_path)
            logger.debug("Removed orphaned temp file: %s", tmp_path)
    except OSError as e:
        logger.error("Error preparing for re-download of %s: %s", file_path, e)
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
    Extract the commit hash embedded in a prerelease directory name.

    Scans the version portion (after removing a leading "firmware-" prefix) for a hexadecimal commit identifier of 6 to 40 characters and returns it in lowercase if found.

    Returns:
        Optional[str]: Lowercase commit hash when present, otherwise None.
    """
    version_part = extract_version(dir_name)  # Removes "firmware-" prefix
    # Use regex to find a hex string of 6-40 characters, which is more robust.
    # This pattern looks for a dot or dash, then the hash, followed by another separator or end of string.
    commit_match = re.search(
        r"[.-]([a-f0-9]{6,40})(?:[.-]|$)", version_part, re.IGNORECASE
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


def _get_entry_display_label(entry: Dict[str, Any]) -> Optional[str]:
    """
    Selects the most user-friendly label available from a prerelease history entry.

    Checks these keys in order and returns the first present and truthy value: "markup_label", "display_name", "identifier", "directory", "dir".

    Parameters:
        entry (dict): A prerelease history entry mapping that may contain keys used for display.

    Returns:
        Optional[str]: The chosen display label, or None if no suitable key is present.
    """
    return (
        entry.get("markup_label")
        or entry.get("display_name")
        or entry.get("identifier")
        or entry.get("directory")
        or entry.get("dir")
    )


def _display_prerelease_summary(tracking_info: Dict[str, Any]) -> None:
    """
    Log a concise summary of prerelease tracking counts and recent history labels.

    Inspects the provided tracking_info mapping and, if present, logs:
    - counts of prereleases created, deleted, and active since the tracked release/version,
    - a comma-separated list of recent prerelease history labels and the base version those commits target.

    Parameters:
        tracking_info (dict): Prerelease tracking data. Recognized keys:
            - "release" (str): reference release/version.
            - "history" (list[dict]): ordered prerelease history entries; entries may include
              "markup_label", "display_name", "identifier", "directory"/"dir", and "base_version".
            - "history_created" (int): number of prereleases created (preferred).
            - "history_deleted" (int): number of prereleases deleted.
            - "history_active" (int|None): number of active prereleases (used as-is if present).
            - "prerelease_count" (int): fallback created count when "history_created" is absent.

    If tracking_info is falsy, the function does nothing.
    """
    if not tracking_info:
        return

    base_version = tracking_info.get("release") or "unknown"
    history_entries: List[Dict[str, Any]] = tracking_info.get("history") or []
    created = tracking_info.get("history_created", 0)
    deleted = tracking_info.get("history_deleted", 0)
    active = tracking_info.get("history_active")

    created_display = created or tracking_info.get("prerelease_count", 0)
    if active is None:
        active = max(created_display - deleted, 0)

    if created_display:
        logger.info(
            "Prereleases since %s: %d created, %d deleted, %d active",
            base_version,
            created_display,
            deleted,
            active,
        )

    history_labels = [
        label for entry in history_entries if (label := _get_entry_display_label(entry))
    ]

    if history_labels:
        history_base = (
            history_entries[0].get("base_version")
            if history_entries
            else calculate_expected_prerelease_version(base_version)
        )
        history_list = ", ".join(history_labels)
        logger.info(
            "Prerelease commits for %s: %s",
            history_base or "next",
            history_list,
        )


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

# Global cache for prerelease commit histories (per expected version)
_prerelease_commit_history_cache: Dict[
    str, Tuple[List[Dict[str, Any]], datetime, datetime, Set[str]]
] = {}
_prerelease_commit_history_file: Optional[str] = None
_prerelease_commit_history_loaded = False

# Global cache for repository commit change summaries keyed by commit SHA
# Global flag to track if downloads were skipped due to Wi-Fi requirements
downloads_skipped = False
_MAX_UNCERTAIN_COMMITS_TO_RESOLVE = 5


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
    Return the filesystem path to the persistent prerelease directory cache JSON file.

    Returns:
        str: Path to the prerelease directories cache JSON file.
    """
    global _prerelease_dir_cache_file
    if _prerelease_dir_cache_file is None:
        _prerelease_dir_cache_file = os.path.join(
            _ensure_cache_dir(), "prerelease_dirs.json"
        )
    return _prerelease_dir_cache_file


def _get_prerelease_commit_history_file() -> str:
    """
    Get the filesystem path to the prerelease commit history cache file.

    Creates and caches the path using the module's per-user cache directory on first call.

    Returns:
        str: Absolute path to the prerelease commit history cache file.
    """

    global _prerelease_commit_history_file
    if _prerelease_commit_history_file is None:
        _prerelease_commit_history_file = os.path.join(
            _ensure_cache_dir(), PRERELEASE_COMMIT_HISTORY_FILE
        )
    return _prerelease_commit_history_file


def _load_json_cache_with_expiry(
    cache_file_path: str,
    expiry_hours: Optional[float],
    cache_entry_validator: Callable[[Dict[str, Any]], bool],
    entry_processor: Callable[[Dict[str, Any], datetime], Any],
    cache_name: str,
) -> Dict[str, Any]:
    """
    Load a JSON cache file and return entries that have not expired.

    Parameters:
        cache_file_path (str): Filesystem path to the JSON cache file.
        expiry_hours (Optional[float]): Maximum age in hours for an entry to be considered valid. When `None`, entries never expire.
        cache_entry_validator (Callable[[Dict[str, Any]], bool]): Predicate that returns `True` for entries that have the expected structure.
        entry_processor (Callable[[Dict[str, Any], datetime], Any]): Function that converts a raw cache entry and its `cached_at` timestamp into the value to be returned for that key.
        cache_name (str): Human-readable name used in debug logs.

    Returns:
        Dict[str, Any]: Mapping of cache keys to processed entries for those entries present in the file, valid according to `cache_entry_validator`, and younger than `expiry_hours` when an expiry is provided.
    """
    try:
        if not os.path.exists(cache_file_path):
            return {}

        with open(cache_file_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        if not isinstance(cache_data, dict):
            return {}

        current_time = datetime.now(timezone.utc)
        loaded: Dict[str, Any] = {}

        for cache_key, cache_entry in cache_data.items():
            try:
                if not cache_entry_validator(cache_entry):
                    logger.debug(
                        "Skipping invalid %s cache entry for %s: incorrect structure",
                        cache_name,
                        cache_key,
                    )
                    continue

                # Check if entry is still valid (not expired)
                cached_at = datetime.fromisoformat(
                    cache_entry["cached_at"].replace("Z", "+00:00")
                )
                age = current_time - cached_at
                if expiry_hours is not None:
                    if age.total_seconds() >= expiry_hours * 60 * 60:
                        logger.debug(
                            "Skipping expired %s cache entry for %s (age %.0fs exceeds %.0fs)",
                            cache_name,
                            cache_key,
                            age.total_seconds(),
                            expiry_hours * 60 * 60,
                        )
                        continue

                loaded[cache_key] = entry_processor(cache_entry, cached_at)
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(
                    "Skipping invalid %s cache entry for %s: %s",
                    cache_name,
                    cache_key,
                    e,
                )
                continue

        if loaded:
            logger.debug("Loaded %d %s entries from cache", len(loaded), cache_name)
        return loaded

    except (IOError, json.JSONDecodeError) as e:
        logger.debug("Could not load %s cache: %s", cache_name, e)
        return {}


def _load_single_blob_cache_with_expiry(
    cache_file_path: str,
    expiry_seconds: float,
    force_refresh: bool = False,
    cache_hit_callback: Optional[Callable[[], None]] = None,
    cache_miss_callback: Optional[Callable[[], None]] = None,
    cache_name: str = "cache",
    data_key: str = "data",
) -> Optional[Any]:
    """
    Load a single-blob JSON cache and return its stored payload when present and not expired.

    Loads a JSON file expected to be a mapping with a "cached_at" ISO timestamp and a payload under `data_key`. If the cached entry is present and its age is less than `expiry_seconds`, returns the stored payload and optionally invokes `cache_hit_callback`. On expiry, parse error, missing keys, absent file, or when `force_refresh` is True, returns None and optionally invokes `cache_miss_callback`.

    Parameters:
        cache_file_path (str): Path to the on-disk JSON cache file.
        expiry_seconds (float): Maximum age in seconds for the cached entry to be considered valid.
        force_refresh (bool): If True, remove the cache file and treat as a cache miss.
        cache_hit_callback (Callable[[], None], optional): Called when a valid, unexpired cache is used.
        cache_miss_callback (Callable[[], None], optional): Called when the cache is missing, expired, invalid, or forced to refresh.
        cache_name (str): Human-readable name for log messages about this cache.
        data_key (str): JSON key under which the cached payload is stored (defaults to "data").

    Returns:
        Optional[Any]: The value stored under `data_key` when the cache is valid, or `None` otherwise.
    """
    # Handle force refresh by deleting cache file
    if force_refresh:
        try:
            os.remove(cache_file_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug(f"Failed to remove {cache_name} cache for refresh: {exc}")

    try:
        if not force_refresh and os.path.exists(cache_file_path):
            with open(cache_file_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # Check if cache has expected structure
            if not isinstance(cache_data, dict):
                logger.debug(f"Invalid {cache_name} cache structure: not a dictionary")
                if cache_miss_callback:
                    cache_miss_callback()
                return None

            # Check if cache is still valid
            raw_cached_at = cache_data.get("cached_at")
            if isinstance(raw_cached_at, str):
                try:
                    cached_at = datetime.fromisoformat(
                        raw_cached_at.replace("Z", "+00:00")
                    )
                    age = datetime.now(timezone.utc) - cached_at

                    if age.total_seconds() < expiry_seconds:
                        logger.debug(
                            f"Using cached {cache_name} data (age: {age.total_seconds():.1f}s)"
                        )
                        if cache_hit_callback:
                            cache_hit_callback()
                        return cache_data.get(data_key)
                    else:
                        logger.debug(
                            f"{cache_name.capitalize()} cache expired (age: {age.total_seconds():.1f}s)"
                        )
                except (ValueError, TypeError):
                    logger.debug(
                        f"Invalid cached_at timestamp in {cache_name} cache, treating as expired"
                    )
            else:
                logger.debug(
                    f"Missing cached_at timestamp in {cache_name} cache, treating as expired"
                )

            if cache_miss_callback:
                cache_miss_callback()
        else:
            if cache_miss_callback:
                cache_miss_callback()

    except (json.JSONDecodeError, KeyError, ValueError, OSError, TypeError) as e:
        logger.debug(f"Error reading {cache_name} cache: {e}")
        if cache_miss_callback:
            cache_miss_callback()

    return None


def _save_single_blob_cache(
    cache_file_path: str,
    data: Any,
    cache_name: str = "cache",
    data_key: str = "data",
) -> bool:
    """
    Write a timestamped single-blob JSON cache file containing the provided data.

    The cache JSON will contain an ISO 8601 UTC `cached_at` timestamp and the given `data` under `data_key`. The write is attempted atomically; failures return False.

    Parameters:
        cache_file_path (str): Filesystem path to write the JSON cache to.
        data (Any): Value to store under `data_key` in the JSON blob.
        cache_name (str): Human-readable name for logging and error messages.
        data_key (str): Key under which `data` is stored in the JSON object.

    Returns:
        bool: `True` if the cache file was written successfully, `False` otherwise.
    """
    try:
        cache_data = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            data_key: data,
        }
        if _atomic_write_json(cache_file_path, cache_data):
            logger.debug(f"Cached data to {cache_file_path}")
            return True
        else:
            logger.debug(f"Failed to cache data to {cache_file_path}")
            return False
    except (OSError, TypeError) as e:
        logger.debug(f"Failed to cache {cache_name} data: {e}")
        return False


def _load_prerelease_dir_cache() -> None:
    """
    Load on-disk prerelease directory cache into the module-level in-memory cache.

    If the cache is already loaded this is a no-op. Reads the persistent cache file (performing validation and expiry checks) outside the module lock and merges valid entries into the module-level `_prerelease_dir_cache` while holding the cache lock to ensure thread safety.
    """
    global _prerelease_dir_cache, _prerelease_dir_cache_loaded

    if _prerelease_dir_cache_loaded:
        return

    # Perform I/O outside of lock to minimize contention
    def validate_prerelease_entry(cache_entry: Dict[str, Any]) -> bool:
        """
        Validate that a mapping represents a prerelease directory cache entry.

        This checks for the presence of the required top-level keys used by the prerelease directory cache:
        - "directories": expected to be a sequence or mapping of prerelease directory names.
        - "cached_at": expected to be an ISO timestamp string or a numeric epoch.

        Parameters:
            cache_entry (dict): Mapping to validate as a prerelease directory cache entry.

        Returns:
            `True` if `cache_entry` is a mapping containing both `"directories"` and `"cached_at"`, `False` otherwise.
        """
        return (
            isinstance(cache_entry, dict)
            and "directories" in cache_entry
            and "cached_at" in cache_entry
        )

    def process_prerelease_entry(
        cache_entry: Dict[str, Any], cached_at: datetime
    ) -> Tuple[List[str], datetime]:
        """
        Validate a prerelease cache record and return its directory list with the original timestamp.

        Parameters:
            cache_entry (Dict[str, Any]): Cache record expected to contain a "directories" key mapping to a list of prerelease directory names.
            cached_at (datetime): Timestamp when the cache entry was recorded.

        Returns:
            Tuple[List[str], datetime]: A tuple containing the directories list and the original cached_at timestamp.

        Raises:
            TypeError: If the "directories" value is not a list.
        """
        directories = cache_entry["directories"]
        if not isinstance(directories, list):
            raise TypeError("directories is not a list")
        return (directories, cached_at)

    loaded_data = _load_json_cache_with_expiry(
        cache_file_path=_get_prerelease_dir_cache_file(),
        expiry_hours=FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS / 3600,
        cache_entry_validator=validate_prerelease_entry,
        entry_processor=process_prerelease_entry,
        cache_name="prerelease directory",
    )

    with _cache_lock:
        if _prerelease_dir_cache_loaded:
            return  # Another thread might have loaded it while we were reading from disk
        _prerelease_dir_cache.update(loaded_data)
        _prerelease_dir_cache_loaded = True


def _save_prerelease_dir_cache() -> None:
    """
    Persist the in-memory prerelease directory cache to the configured cache file.

    Cached timestamps are serialized as ISO 8601 strings. I/O errors are logged and suppressed; the function does not raise on write failures.
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

        # Perform I/O outside lock to reduce contention
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
        logger.warning("Could not save prerelease directory cache: %s", e)


def _clear_prerelease_cache() -> None:
    """
    Clears the in-memory and on-disk prerelease directory cache.

    Also resets the internal loaded flag so the prerelease directory cache will be reloaded on next access.
    """
    global _prerelease_dir_cache_loaded

    _clear_cache_generic(
        cache_dict=_prerelease_dir_cache,
        cache_file_getter=_get_prerelease_dir_cache_file,
        cache_name="prerelease directory",
    )

    with _cache_lock:
        _prerelease_dir_cache_loaded = False


def _load_prerelease_commit_history_cache() -> None:
    """
    Ensure the on-disk prerelease commit history cache is loaded into the in-memory cache.

    Loads validated cached prerelease commit histories keyed by base version from the configured cache file
    and merges them into the in-memory cache if they are not already loaded. Entries must contain an
    "entries" list and a "cached_at" timestamp; invalid or expired entries are ignored by the underlying
    loader. This function is idempotent and safe to call concurrently.
    """

    global _prerelease_commit_history_cache, _prerelease_commit_history_loaded

    if _prerelease_commit_history_loaded:
        return

    def validate_history_entry(cache_entry: Dict[str, Any]) -> bool:
        """
        Check whether an object is a valid prerelease commit history cache entry.

        Parameters:
            cache_entry (dict): Object to validate; expected to be a mapping that includes the keys `"entries"` and `"cached_at"`.

        Returns:
            bool: `True` if `cache_entry` is a dict containing both `"entries"` and `"cached_at"`, `False` otherwise.
        """
        return (
            isinstance(cache_entry, dict)
            and "entries" in cache_entry
            and "cached_at" in cache_entry
        )

    def process_history_entry(
        cache_entry: Dict[str, Any], cached_at: datetime
    ) -> Tuple[List[Dict[str, Any]], datetime, datetime, Set[str]]:
        """
        Validate and extract the entries list and its cached timestamp from a prerelease cache entry.

        Parameters:
            cache_entry (Dict[str, Any]): Cache object expected to contain an "entries" key whose value is a list of dicts.
            cached_at (datetime): Timestamp when the cache entry was created or saved.

        Returns:
            tuple: (entries, cached_at, last_checked, shas)

        Raises:
            TypeError: If the "entries" value in `cache_entry` is not a list.
        """
        entries = cache_entry["entries"]
        if not isinstance(entries, list):
            raise TypeError("entries is not a list")
        last_checked_raw = cache_entry.get("last_checked") or cache_entry.get(
            "cached_at"
        )
        try:
            ts = str(last_checked_raw)
            last_checked = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            last_checked = cached_at

        def _extract_shas(entry: Dict[str, Any]) -> Optional[str]:
            return (
                entry.get("added_sha")
                or entry.get("commit_sha")
                or entry.get("commit_hash")
            )

        shas: Set[str] = set()
        for entry in entries:
            sha = _extract_shas(entry)
            if sha:
                shas.add(str(sha))

        if "shas" in cache_entry and isinstance(cache_entry["shas"], list):
            for sha in cache_entry["shas"]:
                if sha:
                    shas.add(str(sha))

        return (entries, cached_at, last_checked, shas)

    loaded_data = _load_json_cache_with_expiry(
        cache_file_path=_get_prerelease_commit_history_file(),
        expiry_hours=None,
        cache_entry_validator=validate_history_entry,
        entry_processor=process_history_entry,
        cache_name="prerelease commit history",
    )

    with _cache_lock:
        if _prerelease_commit_history_loaded:
            return
        _prerelease_commit_history_cache.update(loaded_data)
        _prerelease_commit_history_loaded = True


def _save_prerelease_commit_history_cache() -> None:
    """
    Write the in-memory prerelease commit history cache to its on-disk JSON file.

    The cache is serialized as a mapping from base version to an object with `entries`
    (the saved history entries) and `cached_at` (an ISO 8601 timestamp). On success
    the function logs a debug message; on failure it logs a warning and does not
    raise an exception.
    """

    cache_file = _get_prerelease_commit_history_file()
    try:
        with _cache_lock:
            cache_data = {
                version: {
                    "entries": entries,
                    "cached_at": cached_at.isoformat(),
                    "last_checked": last_checked.isoformat(),
                    "shas": sorted(shas),
                }
                for version, (
                    entries,
                    cached_at,
                    last_checked,
                    shas,
                ) in _prerelease_commit_history_cache.items()
            }

        if _atomic_write_json(cache_file, cache_data):
            logger.debug(
                "Saved %d prerelease commit history entries to cache",
                len(cache_data),
            )
        else:
            logger.warning(
                "Failed to save prerelease commit history cache to %s",
                cache_file,
            )
    except (IOError, OSError) as e:
        logger.warning("Could not save prerelease commit history cache: %s", e)


def _clear_prerelease_commit_history_cache() -> None:
    """
    Clear the prerelease commit history cache from memory and disk.
    """

    global _prerelease_commit_history_loaded

    _clear_cache_generic(
        cache_dict=_prerelease_commit_history_cache,
        cache_file_getter=_get_prerelease_commit_history_file,
        cache_name="prerelease commit history",
    )

    with _cache_lock:
        _prerelease_commit_history_loaded = False


def _fetch_prerelease_directories(
    force_refresh: bool = False,
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
) -> List[str]:
    """
    Retrieve prerelease directory names from the meshtastic.github.io repository, using a cached listing when still fresh.

    If the cache is missing, expired, or `force_refresh` is True, query the repository and update the on-disk cache.

    Parameters:
        force_refresh (bool): When True, bypass the cache and refetch the directory list from the remote repository.
        github_token (Optional[str]): Explicit GitHub token to use for authenticated requests; if None and `allow_env_token` is True, an environment token may be used.
        allow_env_token (bool): Whether to fall back to the `GITHUB_TOKEN` environment variable when an explicit token is not provided.

    Returns:
        List[str]: Directory names found at the repository root.
    """
    cache_key = _PRERELEASE_DIR_CACHE_ROOT_KEY

    _load_prerelease_dir_cache()

    now = datetime.now(timezone.utc)

    with _cache_lock:
        if not force_refresh and cache_key in _prerelease_dir_cache:
            directories, cached_at = _prerelease_dir_cache[cache_key]
            age = now - cached_at
            if age.total_seconds() < FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS:
                track_api_cache_hit()
                logger.debug(
                    "Using cached prerelease directories (cached %.0fs ago)",
                    age.total_seconds(),
                )
                return list(directories)

            logger.debug(
                "Prerelease directory cache expired (age %.0fs, limit %ds) - refreshing",
                age.total_seconds(),
                FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
            )
            del _prerelease_dir_cache[cache_key]

    logger.debug(
        f"Cache miss for prerelease directories {MESHTASTIC_GITHUB_IO_CONTENTS_URL} - fetching from API"
    )
    track_api_cache_miss()
    directories = menu_repo.fetch_repo_directories(
        allow_env_token=allow_env_token,
        github_token=github_token,
    )
    updated_at = datetime.now(timezone.utc)

    with _cache_lock:
        _prerelease_dir_cache[cache_key] = (list(directories), updated_at)

    _save_prerelease_dir_cache()
    return list(directories)


def _fetch_recent_repo_commits(
    max_commits: int,
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """
    Retrieve recent commits from the meshtastic.github.io repository for prerelease tracking.

    This function paginates GitHub API requests and caches the raw commit list on disk. It will return up to `max_commits` commit objects (as decoded JSON dictionaries). On cache hit the cached result is returned (trimmed to `max_commits` if necessary); on cache miss the function fetches pages of commits until the requested count is reached or the repository history is exhausted. Network, HTTP, and parsing errors are caught and result in an empty list.

    Parameters:
        max_commits (int): Maximum number of commits to return.
        github_token (Optional[str]): GitHub token to use for authenticated requests; if None and `allow_env_token` is True, an environment-provided token may be used.
        allow_env_token (bool): Whether using a token from the environment is permitted when `github_token` is not provided.
        force_refresh (bool): If True, ignore any on-disk cache and fetch fresh data from the API.

    Returns:
        List[Dict[str, Any]]: A list of commit objects (decoded JSON from the GitHub API), up to `max_commits`; returns an empty list on error or if no commits are available.
    """

    max_commits = max(1, max_commits)

    # Check cache first using generic helper
    cache_file = os.path.join(_ensure_cache_dir(), PRERELEASE_COMMITS_CACHE_FILE)

    cached_commits = _load_single_blob_cache_with_expiry(
        cache_file_path=cache_file,
        expiry_seconds=PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
        force_refresh=force_refresh,
        cache_hit_callback=track_api_cache_hit,
        cache_miss_callback=track_api_cache_miss,
        cache_name="commits",
        data_key="commits",
    )

    commits: List[Dict[str, Any]] = []
    seen_shas: Set[str] = set()
    next_page = 1

    if cached_commits is not None:
        commits.extend(cached_commits)
        for commit in cached_commits:
            sha = commit.get("sha")
            if sha:
                seen_shas.add(sha)

        if len(commits) >= max_commits:
            return commits[:max_commits]

        logger.debug(
            "Commits cache has %d items but %d requested; fetching additional pages",
            len(commits),
            max_commits,
        )
        next_page = (len(commits) // GITHUB_MAX_PER_PAGE) + 1

    # Cache miss or expired - fetch from API
    logger.debug("Fetching commits from API (cache miss/expired)")

    all_commits = list(commits)
    per_page = min(GITHUB_MAX_PER_PAGE, max_commits)
    page = max(1, next_page)
    fetched_from_api = False

    url = f"{GITHUB_API_BASE}/meshtastic/meshtastic.github.io/commits"

    try:
        while len(all_commits) < max_commits:
            params = {"per_page": per_page, "page": page}

            response = make_github_api_request(
                url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                params=params,
                timeout=GITHUB_API_TIMEOUT,
            )
            commits = response.json()

            if not isinstance(commits, list):
                logger.warning(
                    "Unexpected response when fetching repo commits: %s",
                    type(commits).__name__,
                )
                break

            if not commits:  # No more commits available
                break

            fetched_from_api = True
            for commit in commits:
                sha = commit.get("sha")
                if sha and sha in seen_shas:
                    continue
                if sha:
                    seen_shas.add(sha)
                all_commits.append(commit)

            # If we got fewer than per_page, we've reached the end
            if len(commits) < per_page:
                break

            # Stop if we have enough commits
            if len(all_commits) >= max_commits:
                break

            page += 1

        logger.debug(
            "Fetched %d repo commits for prerelease tracking (from %d pages)",
            len(all_commits),
            page,
        )

        # Save to cache using generic helper
        if fetched_from_api:
            _save_single_blob_cache(
                cache_file_path=cache_file,
                data=all_commits,
                cache_name="commits",
                data_key="commits",
            )

        return all_commits[:max_commits]

    except requests.HTTPError as exc:
        logger.warning("HTTP error fetching repo commits: %s", exc)
    except requests.RequestException as exc:
        logger.warning("Could not fetch repo commits: %s", exc)
    except (ValueError, KeyError) as exc:
        logger.error("Error parsing repo commits response: %s", exc)

    return []


def _create_default_prerelease_entry(
    directory: str,
    identifier: str,
    base_version: str,
    commit_hash: str,
) -> Dict[str, Any]:
    """
    Create a canonical prerelease history entry for a given prerelease directory.

    Parameters:
        directory (str): Directory name of the prerelease asset.
        identifier (str): Display identifier for the prerelease (e.g., label or tag).
        base_version (str): Base (official) version this prerelease derives from.
        commit_hash (str): Commit hash associated with the prerelease.

    Returns:
        dict: A prerelease entry with keys:
            - directory: same as `directory`
            - identifier: same as `identifier`
            - base_version: same as `base_version`
            - commit_hash: same as `commit_hash`
            - added_at: timestamp when added (None if unknown)
            - removed_at: timestamp when removed (None if not removed)
            - added_sha: commit hash recorded when added (None if unknown)
            - removed_sha: commit hash recorded when removed (None if removed)
            - active: boolean defaulting to DEFAULT_PRERELEASE_ACTIVE
            - status: status string defaulting to DEFAULT_PRERELEASE_STATUS
    """
    return {
        "directory": directory,
        "identifier": identifier,
        "base_version": base_version,
        "commit_hash": commit_hash,
        "added_at": None,
        "removed_at": None,
        "added_sha": None,
        "removed_sha": None,
        "active": DEFAULT_PRERELEASE_ACTIVE,
        "status": DEFAULT_PRERELEASE_STATUS,
    }


def _record_prerelease_addition(
    entries: Dict[str, Dict[str, Any]],
    directory: str,
    identifier: str,
    expected_version: str,
    short_hash: str,
    timestamp: Optional[str],
    sha: Optional[str],
) -> None:
    """
    Ensure a prerelease entry exists and mark it as active with the provided metadata.
    """
    entry = entries.setdefault(
        directory,
        _create_default_prerelease_entry(
            directory, identifier, expected_version, short_hash
        ),
    )

    if timestamp and not entry.get("added_at"):
        entry["added_at"] = timestamp
    if sha and not entry.get("added_sha"):
        entry["added_sha"] = sha

    entry["active"] = True
    entry["status"] = "active"
    entry["removed_at"] = None
    entry["removed_sha"] = None


def _record_prerelease_deletion(
    entries: Dict[str, Dict[str, Any]],
    directory: str,
    identifier: str,
    expected_version: str,
    short_hash: str,
    timestamp: Optional[str],
    sha: Optional[str],
) -> None:
    """
    Ensure a prerelease entry exists for `directory` and mark it deleted.

    If the entry does not exist it is created with defaults. Sets `removed_at` when
    `timestamp` is provided, `removed_sha` when `sha` is provided, marks the entry
    inactive (`active` = False) and sets `status` to `"deleted"`.

    Parameters:
        entries: Mapping of directory names to prerelease entry objects; the entry
            for `directory` will be created or updated in-place.
        directory: Prerelease directory name used as the key in `entries`.
        identifier: Prerelease identifier used when creating a default entry.
        expected_version: Base prerelease version used when creating a default entry.
        short_hash: Short commit hash used when creating a default entry.
        timestamp: ISO-8601 timestamp to record as `removed_at`, or `None` to leave
            `removed_at` unchanged.
        sha: Full commit SHA to record as `removed_sha`, or `None` to leave
            `removed_sha` unchanged.
    """
    entry = entries.setdefault(
        directory,
        _create_default_prerelease_entry(
            directory, identifier, expected_version, short_hash
        ),
    )

    if timestamp:
        entry["removed_at"] = timestamp
    if sha:
        entry["removed_sha"] = sha

    entry["active"] = False
    entry["status"] = "deleted"


def _build_simplified_prerelease_history(
    expected_version: str,
    commits: List[Dict[str, Any]],
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
) -> List[Dict[str, Any]]:
    """
    Build a chronological, simplified prerelease history from repository commits for a given expected prerelease version.

    Parameters:
        expected_version (str): Base prerelease version to filter commits against (e.g., "2.7.14"). If falsy the function returns an empty list.
        commits (List[Dict[str, Any]]): Commit objects as returned by the GitHub API; each should include `sha`, `commit.committer.date`, and `commit.message`.
        github_token (Optional[str]): Optional token used when fetching additional commit details for uncertain commits.
        allow_env_token (bool): Whether to allow falling back to a GitHub token provided via the environment when `github_token` is None.

    Returns:
        List[Dict[str, Any]]: A list of normalized prerelease entry dictionaries sorted newest-first by creation/deletion time. Each entry may include keys such as `directory`, `identifier`, `added_at`, `added_sha`, `removed_at`, `removed_sha`, `active`, and `status`. Returns an empty list when `expected_version` is falsy or `commits` is empty.
    """

    if not expected_version or not commits:
        return []

    entries: Dict[str, Dict[str, Any]] = {}
    uncertain_commits: List[Dict[str, Any]] = []

    # OPTIMIZATION: Parse commit messages instead of making individual API calls
    # The meshtastic.github.io repository has structured commit messages that contain
    # all the information we need: version, operation type, and commit hash

    logger.debug(
        "Building prerelease history from %d commits by parsing messages",
        len(commits),
    )

    # Process oldest first so newer commits override older ones
    for commit in reversed(commits):
        sha = commit.get("sha")
        commit_msg = commit.get("commit", {}).get("message", "").strip()
        timestamp = commit.get("commit", {}).get("committer", {}).get("date")

        if not sha or not timestamp:
            continue

        # Pattern 1: Adding a prerelease
        # Examples: "2.7.14.e959000 meshtastic/firmware@e959000"
        add_match = PRERELEASE_ADD_RX.match(commit_msg)

        if add_match:
            version, short_hash = add_match.groups()
            if version == expected_version:
                identifier = f"{version}.{short_hash}"
                directory = f"firmware-{identifier}"

                _record_prerelease_addition(
                    entries,
                    directory,
                    identifier,
                    expected_version,
                    short_hash,
                    timestamp,
                    sha,
                )
                continue

        # Pattern 2: Deleting a prerelease
        # Examples: "Delete firmware-2.7.13.ffb168b directory"
        delete_match = PRERELEASE_DELETE_RX.match(commit_msg)

        if delete_match:
            version, short_hash = delete_match.groups()
            if version == expected_version:
                identifier = f"{version}.{short_hash}"
                directory = f"firmware-{identifier}"

                _record_prerelease_deletion(
                    entries,
                    directory,
                    identifier,
                    expected_version,
                    short_hash,
                    timestamp,
                    sha,
                )
                continue

        # Track commits we could not categorize for optional detail processing
        uncertain_commits.append(commit)

    if uncertain_commits and _should_fetch_uncertain_commits():
        _enrich_history_from_commit_details(
            entries,
            uncertain_commits,
            expected_version,
            github_token=github_token,
            allow_env_token=allow_env_token,
        )

    # Convert to list and sort by added_at (newest first), then by removed_at
    entry_list = list(entries.values())
    entry_list.sort(key=_sort_key, reverse=True)

    logger.debug(
        "Built prerelease history with %d entries from commit message parsing",
        len(entry_list),
    )

    return entry_list


def _extract_prerelease_dir_info(
    path: str, expected_version: str
) -> Optional[Tuple[str, str, str]]:
    """
    Extract the prerelease directory name, identifier, and short commit hash from a commit file path when it references a prerelease for the expected base version.

    Parameters:
        path (str): The file path or commit path to inspect.
        expected_version (str): The base version (e.g., "1.2.3") that the prerelease directory must match.

    Returns:
        Optional[Tuple[str, str, str]]: A tuple (directory, identifier, short_hash) when `path` contains a prerelease directory of the form `firmware-<version>.<hash>` and the extracted version equals `expected_version`; `None` otherwise.
    """
    if not path:
        return None

    match = PRERELEASE_DIR_SEGMENT_RX.search(path)
    if not match:
        return None

    segment = match.group(1).lower()
    dir_match = re.match(r"firmware-(\d+\.\d+\.\d+)\.([a-f0-9]{6,})", segment)
    if not dir_match:
        return None

    version, short_hash = dir_match.groups()
    if version != expected_version:
        return None

    identifier = f"{version}.{short_hash}"
    directory = f"firmware-{identifier}"
    return directory, identifier, short_hash


def _should_fetch_uncertain_commits() -> bool:
    """
    Decide whether it is safe to fetch detailed commit data given current GitHub API rate-limit and authentication status.

    Returns:
        bool: `true` if fetching commit details is allowed (an authenticated token is in use or there are more than the minimum required unauthenticated requests remaining), `false` otherwise.
    """
    summary = get_api_request_summary()
    remaining = summary.get("rate_limit_remaining")
    auth_used = summary.get("auth_used", False)

    if auth_used:
        # Authenticated tokens have generous limits; proceed even without a cached remaining value
        return True

    if remaining is None:
        logger.debug(
            "Skipping commit detail fetch: unauthenticated rate-limit data unavailable"
        )
        return False

    if remaining <= MIN_RATE_LIMIT_FOR_COMMIT_DETAILS:
        logger.debug(
            "Skipping commit detail fetch: only %d unauthenticated GitHub API requests remaining",
            remaining,
        )
        return False

    return True


def _fetch_commit_files(
    sha: str,
    github_token: Optional[str],
    allow_env_token: bool,
) -> List[Dict[str, Any]]:
    """
    Retrieve the list of file metadata for a given GitHub commit SHA.

    Parameters:
        sha (str): Commit SHA to query.
        github_token (Optional[str]): Explicit GitHub token to use for the request, or None to rely on other token resolution.
        allow_env_token (bool): If True, allow using a token from the environment when an explicit token is not provided.

    Returns:
        List[Dict[str, Any]]: A list of file metadata dictionaries as returned by the GitHub commit API, or an empty list if the request or parsing fails.
    """
    url = f"{GITHUB_API_BASE}/meshtastic/meshtastic.github.io/commits/{sha}"
    try:
        response = make_github_api_request(
            url,
            github_token=github_token,
            allow_env_token=allow_env_token,
            timeout=GITHUB_API_TIMEOUT,
        )
        data = response.json()
        files = data.get("files")
        if isinstance(files, list):
            return files
    except (requests.HTTPError, requests.RequestException) as exc:
        logger.debug("HTTP error fetching commit details for %s: %s", sha[:8], exc)
    except (ValueError, KeyError, TypeError) as exc:
        logger.debug("Failed to parse commit details for %s: %s", sha[:8], exc)
    return []


def _enrich_history_from_commit_details(
    entries: Dict[str, Dict[str, Any]],
    uncertain_commits: List[Dict[str, Any]],
    expected_version: str,
    github_token: Optional[str],
    allow_env_token: bool,
) -> None:
    """
    Classify uncertain prerelease commits by inspecting their file diffs and update `entries` in place to record detected additions or deletions of prerelease directories.

    Inspects file-level changes for commits in `uncertain_commits`, maps changed paths to prerelease directory names for `expected_version`, and records additions or removals in the provided `entries` mapping. Uses `github_token` (or an environment token when `allow_env_token` is True) to fetch commit file details; processing is rate-limit aware and will stop after classifying a bounded number of uncertain commits.

    Parameters:
        entries (dict): Mapping of prerelease directory name -> history entry dict that will be updated in place.
        uncertain_commits (list): Commits that could not be classified by message parsing; each item must include at least a `sha` and `commit` metadata containing a committer `date`.
        expected_version (str): Base prerelease version used to recognize prerelease directory names in file paths.
        github_token (Optional[str]): Explicit GitHub token to use when fetching commit file details; may be None.
        allow_env_token (bool): If True, permit falling back to a token supplied via the environment when fetching commit details.
    """

    candidates: List[Tuple[str, str]] = []
    seen_shas: Set[str] = set()

    for commit in reversed(uncertain_commits):
        sha = commit.get("sha")
        timestamp = commit.get("commit", {}).get("committer", {}).get("date")
        if not sha or not timestamp or sha in seen_shas:
            continue
        seen_shas.add(sha)
        candidates.append((sha, timestamp))

    if not candidates:
        return

    max_workers = max(1, min(PRERELEASE_DETAIL_FETCH_WORKERS, len(candidates)))
    logger.debug(
        "Fetching commit details for uncertain prerelease commits using %d worker(s)",
        max_workers,
    )

    attempt_cap = min(
        len(candidates),
        _MAX_UNCERTAIN_COMMITS_TO_RESOLVE * PRERELEASE_DETAIL_ATTEMPT_MULTIPLIER,
    )
    summary = get_api_request_summary()
    remaining = summary.get("rate_limit_remaining")
    auth_used = summary.get("auth_used", False)
    if not auth_used and isinstance(remaining, int):
        safe_allowance = max(0, remaining - 1)
        attempt_cap = min(attempt_cap, safe_allowance)
    if attempt_cap <= 0:
        logger.debug(
            "Skipping uncertain commit detail fetches because attempt cap resolved to %d",
            attempt_cap,
        )
        return
    successful_classifications = 0
    attempted = 0
    next_idx = 0
    next_result_index = 0
    pending_results: Dict[int, Tuple[str, str, List[Dict[str, Any]]]] = {}

    def _submit_more(
        executor: ThreadPoolExecutor, inflight: Dict["Future", Tuple[int, str, str]]
    ) -> None:
        """
        Schedule additional commit-file fetch tasks while respecting worker and attempt limits.

        Submits up to the available worker slots (bounded by `max_workers`), stops when the total number of fetch attempts reaches `attempt_cap` or when there are no more candidate commits, and records each submitted task in the `inflight` mapping keyed by the returned Future. For each submission, advances the candidate index and increments the attempted counter.

        Parameters:
            executor (ThreadPoolExecutor): Executor used to submit fetch tasks.
            inflight (Dict[Future, Tuple[int, str, str]]): Mapping where each new Future is stored with a tuple (candidate_index, commit_sha, commit_timestamp) to track in-flight work.
        """
        nonlocal attempted, next_idx
        while (
            len(inflight) < max_workers
            and attempted < attempt_cap
            and next_idx < len(candidates)
        ):
            sha, timestamp = candidates[next_idx]
            idx = next_idx
            next_idx += 1
            future = executor.submit(
                _fetch_commit_files,
                sha,
                github_token,
                allow_env_token,
            )
            inflight[future] = (idx, sha, timestamp)
            attempted += 1

    def _process_result(sha: str, timestamp: str, files: List[Dict[str, Any]]) -> bool:
        """
        Classify a commit's file changes to detect prerelease directory additions or removals and record them.

        Parameters:
            sha (str): Commit SHA used for recording and logging.
            timestamp (str): Commit timestamp used when recording history entries.
            files (List[Dict[str, Any]]): List of file-change dictionaries from the commit. Each dictionary is expected to contain at least the keys `filename` and `status`, and may include `previous_filename` for renames.

        Returns:
            bool: `true` if any prerelease addition or deletion was recorded, `false` otherwise.
        """
        directory_changes: Dict[str, Dict[str, Any]] = {}
        for file_info in files:
            path = str(file_info.get("filename") or "")
            status = str(file_info.get("status") or "").lower()
            dir_info = _extract_prerelease_dir_info(path, expected_version)
            if not dir_info:
                continue

            directory, identifier, short_hash = dir_info
            change = directory_changes.setdefault(
                directory,
                {
                    "identifier": identifier,
                    "short_hash": short_hash,
                    "added": False,
                    "removed": False,
                },
            )

            if status == "added":
                change["added"] = True
            elif status == "removed":
                change["removed"] = True
            elif status == "renamed":
                prev_path = str(file_info.get("previous_filename") or "")
                prev_info = _extract_prerelease_dir_info(prev_path, expected_version)

                if prev_info:
                    prev_dir, prev_identifier, prev_short_hash = prev_info
                    if prev_dir != directory:
                        prev_entry = directory_changes.setdefault(
                            prev_dir,
                            {
                                "identifier": prev_identifier,
                                "short_hash": prev_short_hash,
                                "added": False,
                                "removed": False,
                            },
                        )
                        prev_entry["removed"] = True
                if dir_info:
                    change["added"] = True

        if not directory_changes:
            return False

        logger.debug(
            "Fetched commit details for %s to classify %d prerelease directories",
            sha[:8],
            len(directory_changes),
        )

        made_change = False
        for directory, change in directory_changes.items():
            if change["added"] and not change["removed"]:
                _record_prerelease_addition(
                    entries,
                    directory,
                    change["identifier"],
                    expected_version,
                    change["short_hash"],
                    timestamp,
                    sha,
                )
                made_change = True
            elif change["removed"] and not change["added"]:
                _record_prerelease_deletion(
                    entries,
                    directory,
                    change["identifier"],
                    expected_version,
                    change["short_hash"],
                    timestamp,
                    sha,
                )
                made_change = True

        return made_change

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        inflight: Dict["Future", Tuple[int, str, str]] = {}
        _submit_more(executor, inflight)

        while inflight or pending_results:
            while (
                next_result_index in pending_results
                and successful_classifications < _MAX_UNCERTAIN_COMMITS_TO_RESOLVE
            ):
                sha, timestamp, files = pending_results.pop(next_result_index)
                next_result_index += 1
                if not files:
                    continue
                if _process_result(sha, timestamp, files):
                    successful_classifications += 1
                    if successful_classifications >= _MAX_UNCERTAIN_COMMITS_TO_RESOLVE:
                        logger.debug(
                            "Reached maximum uncertain commits to resolve (%d), stopping further processing",
                            _MAX_UNCERTAIN_COMMITS_TO_RESOLVE,
                        )
                        for pending_future in inflight:
                            if not pending_future.done():
                                pending_future.cancel()
                        inflight.clear()
                        break
            if successful_classifications >= _MAX_UNCERTAIN_COMMITS_TO_RESOLVE:
                break

            if not inflight:
                break

            done_futures, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for future in done_futures:
                idx, sha, timestamp = inflight.pop(future)
                try:
                    files = future.result()
                except CancelledError:
                    files = []
                except (
                    requests.RequestException,
                    ValueError,
                    KeyError,
                    TypeError,
                ) as exc:
                    logger.debug(
                        "Failed to obtain commit details for %s: %s", sha[:8], exc
                    )
                    files = []
                pending_results[idx] = (sha, timestamp, files)

            _submit_more(executor, inflight)

    if (
        successful_classifications == 0
        and attempted >= attempt_cap
        and attempt_cap < len(candidates)
    ):
        logger.debug(
            "No uncertain commits classified after %d attempts (cap %d); older commits were not inspected.",
            attempted,
            attempt_cap,
        )


def _refresh_prerelease_commit_history(
    expected_version: str,
    github_token: Optional[str],
    force_refresh: bool,
    max_commits: int,
    allow_env_token: bool = True,
    existing_entries: Optional[List[Dict[str, Any]]] = None,
    existing_shas: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Refresh the cached simplified prerelease commit history for the given expected version.

    Fetch recent repository commits (up to `max_commits`), build a simplified prerelease history for `expected_version`, persist the result to the in-memory and on-disk prerelease commit-history cache, and return the history entries.

    Parameters:
        expected_version (str): Base prerelease version used to filter and normalize history entries (e.g., "1.2.3").
        github_token (Optional[str]): GitHub API token to use for authenticated requests; if None and allowed, an environment token may be used.
        force_refresh (bool): If True, bypass any cached data and force a fresh fetch from the remote repository.
        max_commits (int): Maximum number of recent commits to fetch and consider when building history.
        allow_env_token (bool): If True, permit using a GitHub token sourced from the environment when `github_token` is not provided.

    Returns:
        List[Dict[str, Any]]: Simplified prerelease history entry dictionaries; empty list if no commits are available or the fetch fails.
    """
    commits = _fetch_recent_repo_commits(
        max_commits,
        github_token=github_token,
        allow_env_token=allow_env_token,
        force_refresh=force_refresh,
    )
    if not commits:
        if existing_entries is not None:
            return list(existing_entries)
        logger.debug(
            "No commits returned while refreshing prerelease history for %s; skipping cache update",
            expected_version,
        )
        return []

    seen_shas: Set[str] = {sha for sha in (existing_shas or []) if sha is not None}
    new_commits = [c for c in commits or [] if c.get("sha") not in seen_shas]

    if not new_commits and existing_entries is not None:
        # Nothing new; just bump last_checked
        with _cache_lock:
            now = datetime.now(timezone.utc)
            _prerelease_commit_history_cache[expected_version] = (
                list(existing_entries),
                now,
                now,
                seen_shas,
            )
        _save_prerelease_commit_history_cache()
        return list(existing_entries)

    # Build simplified history from new commits, preserving auth config for any
    # optional commit-detail lookups.
    history_new = _build_simplified_prerelease_history(
        expected_version,
        new_commits,
        github_token=github_token,
        allow_env_token=allow_env_token,
    )

    def _get_entry_key(entry: Dict[str, Any]) -> Optional[str]:
        return entry.get("identifier") or entry.get("directory") or entry.get("dir")

    existing_map: Dict[str, Dict[str, Any]] = (
        {_get_entry_key(e): e for e in existing_entries if _get_entry_key(e)}
        if existing_entries
        else {}
    )

    # Merge new entries into existing_map (newest-first list already from history_new)
    for new_entry in history_new:
        key = _get_entry_key(new_entry)
        if not key:
            continue

        merged_entry = dict(existing_map.get(key, {}))
        for field, value in new_entry.items():
            if value is None:
                continue
            if field in ("added_at", "added_sha") and merged_entry.get(field):
                continue
            merged_entry[field] = value

        if new_entry.get("status") == "active":
            merged_entry["removed_at"] = None
            merged_entry["removed_sha"] = None

        existing_map[key] = merged_entry or new_entry

    # Preserve newest-first ordering using the shared sort key
    merged_history: List[Dict[str, Any]] = sorted(
        existing_map.values(), key=_sort_key, reverse=True
    )

    with _cache_lock:
        final_shas: Set[str] = seen_shas.union(
            {str(c.get("sha")) for c in commits or [] if c.get("sha") is not None}
        )
        now = datetime.now(timezone.utc)
        _prerelease_commit_history_cache[expected_version] = (
            merged_history,
            now,
            now,
            final_shas,
        )

    _save_prerelease_commit_history_cache()
    return [dict(entry) for entry in merged_history]


def _is_within_base(real_base_dir: str, candidate: str) -> bool:
    """
    Return True if candidate is contained within real_base_dir using commonpath.
    """
    try:
        return os.path.commonpath([real_base_dir, candidate]) == real_base_dir
    except ValueError:
        return False


def _get_latest_active_prerelease_from_history(
    expected_version: str,
    github_token: Optional[str] = None,
    force_refresh: bool = False,
    allow_env_token: bool = True,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Return the latest active prerelease directory derived from commit history plus the history entries.

    Centralizes history fetching and selection so callers share the same rules.
    """

    history_entries = _get_prerelease_commit_history(
        expected_version,
        github_token=github_token,
        force_refresh=force_refresh,
        allow_env_token=allow_env_token,
    )

    latest_active_dir = None
    for entry in history_entries:
        if entry.get("status") == "active" and entry.get("directory"):
            latest_active_dir = entry["directory"]
            break

    return latest_active_dir, history_entries


def _get_prerelease_commit_history(
    expected_version: str,
    github_token: Optional[str] = None,
    force_refresh: bool = False,
    max_commits: int = DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    allow_env_token: bool = True,
) -> List[Dict[str, Any]]:
    """
    Get the prerelease commit history for the provided expected prerelease base version, using a cached copy when available.

    If `expected_version` is falsy an empty list is returned. When `force_refresh` is False the function will attempt to read the on-disk/in-memory cache and return it if present and not older than `PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS`; otherwise it will fetch and extend the history by adding new commits.

    Parameters:
        expected_version (str): The base version string used to identify prerelease commits (for example "1.2.3").
        github_token (Optional[str]): GitHub API token to use when fetching commits; may be None to use unauthenticated requests or an environment-provided token if allowed.
        force_refresh (bool): If True, bypass cached data and refresh the history from remote sources.
        max_commits (int): Maximum number of recent commits to fetch when refreshing history.
        allow_env_token (bool): If True, permit using a token from the environment when an explicit `github_token` is not provided.

    Returns:
        list[dict]: A list of prerelease history entry dictionaries for the expected version. Each entry typically includes fields such as `base_version`, `commit_hash`, `display_name`, `markup_label`, `added_at`/`removed_at` (timestamps), and boolean flags like `is_deleted`, `is_newest`, and `is_latest`.
    """

    if not expected_version:
        return []

    cached = None
    if not force_refresh:
        _load_prerelease_commit_history_cache()

        with _cache_lock:
            cached = _prerelease_commit_history_cache.get(expected_version)

        if cached:
            entries, _cached_at, last_checked, shas = cached
            age = datetime.now(timezone.utc) - last_checked
            if age.total_seconds() < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                track_api_cache_hit()
                logger.debug(
                    "Using cached prerelease history for %s (cached %.0fs ago)",
                    expected_version,
                    age.total_seconds(),
                )
                return [dict(entry) for entry in entries]
            logger.debug(
                "Prerelease history cache stale for %s (age %.0fs >= %ss); extending cache",
                expected_version,
                age.total_seconds(),
                PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
            )

    logger.info(
        "Building prerelease history cache for %s (this may take a couple of minutes on initial builds)...",
        expected_version,
    )
    refresh_kwargs: Dict[str, Any] = {}
    if cached:
        entries, _, _, shas = cached
        refresh_kwargs["existing_entries"] = entries
        refresh_kwargs["existing_shas"] = shas

    return _refresh_prerelease_commit_history(
        expected_version,
        github_token,
        force_refresh,
        max_commits,
        allow_env_token,
        **refresh_kwargs,
    )


def _load_commit_cache() -> None:
    """
    Populate the module-level commit timestamp cache from the on-disk cache, loading only well-formed entries whose cached_at is within COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS.

    Skips malformed or expired entries, marks the cache as loaded to avoid repeated initialization across threads, and logs I/O or parsing errors without raising.
    """
    global _commit_timestamp_cache, _commit_cache_loaded

    # Fast path: check if already loaded (double-checked locking pattern)
    if _commit_cache_loaded:
        return

    def validate_commit_entry(cache_entry: Any) -> bool:
        """
        Validate that a cache entry is a two-item sequence.

        Parameters:
                cache_entry (Any): Value to validate.

        Returns:
                True if `cache_entry` is a `list` or `tuple` with exactly two items, False otherwise.
        """
        return isinstance(cache_entry, (list, tuple)) and len(cache_entry) == 2

    def process_commit_entry(
        cache_entry: Any, cached_at: datetime
    ) -> Tuple[datetime, datetime]:
        """
        Parse a cached commit entry's ISO 8601 timestamp and return it together with the original cache time.

        Parameters:
            cache_entry (Any): Sequence-like cached entry whose first element is an ISO 8601 timestamp string; a trailing "Z" is interpreted as UTC.
            cached_at (datetime): The time entry was cached; returned unchanged.

        Returns:
            tuple (datetime, datetime): `(commit_timestamp, cached_at)` where `commit_timestamp` is a timezone-aware datetime parsed from cached ISO 8601 string and `cached_at` is the provided cache time.
        """
        timestamp_str, _ = cache_entry  # cached_at is already parsed
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return (timestamp, cached_at)

    # Perform I/O outside of lock to minimize contention
    cache_file = _get_commit_cache_file()
    loaded: Dict[str, Tuple[datetime, datetime]] = {}

    try:
        if not os.path.exists(cache_file):
            with _cache_lock:
                if _commit_cache_loaded:
                    return  # Another thread might have loaded it while we were checking
                _commit_cache_loaded = True
            return

        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        if not isinstance(cache_data, dict):
            with _cache_lock:
                if _commit_cache_loaded:
                    return  # Another thread might have loaded it while we were checking
                _commit_cache_loaded = True
            return

        current_time = datetime.now(timezone.utc)
        for cache_key, cache_value in cache_data.items():
            try:
                if not validate_commit_entry(cache_value):
                    logger.debug(
                        "Skipping invalid commit cache entry for %s: incorrect structure",
                        cache_key,
                    )
                    continue

                _timestamp_str, cached_at_str = cache_value
                cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))

                # Check if entry is still valid (not expired)
                age = current_time - cached_at
                if age.total_seconds() < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60:
                    loaded[cache_key] = process_commit_entry(cache_value, cached_at)
            except (ValueError, TypeError) as e:
                logger.debug(
                    "Skipping invalid commit cache entry for %s: %s", cache_key, e
                )
                continue

        # Update cache under lock with double-checked pattern
        with _cache_lock:
            if _commit_cache_loaded:
                return  # Another thread might have loaded it while we were reading from disk
            _commit_timestamp_cache.update(loaded)
            _commit_cache_loaded = True
        logger.debug("Loaded %d commit timestamps from cache", len(loaded))

    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Could not load commit cache: %s", e)
        with _cache_lock:
            _commit_cache_loaded = True


def _load_releases_cache() -> None:
    """
    Load non-expired releases from the on-disk cache into the module-level in-memory releases cache.

    This populates the module cache with cached release entries that have not expired, performing I/O only when needed and making subsequent calls a no-op once the cache is loaded.
    """
    global _releases_cache, _releases_cache_loaded

    if _releases_cache_loaded:
        return

    # Define validators outside of lock; perform I/O outside of lock
    def validate_releases_entry(cache_entry: Dict[str, Any]) -> bool:
        """
        Validate that a cache entry has the expected structure for stored releases.

        Parameters:
            cache_entry (dict): Candidate cache object to check.

        Returns:
            True if `cache_entry` is a dict containing with keys `"releases"` and `"cached_at"`, False otherwise.
        """
        return (
            isinstance(cache_entry, dict)
            and "releases" in cache_entry
            and "cached_at" in cache_entry
        )

    def process_releases_entry(
        cache_entry: Dict[str, Any], cached_at: datetime
    ) -> Tuple[List[Dict[str, Any]], datetime]:
        """
        Extracts the stored releases list and its original cache timestamp from a cache entry.

        Parameters:
            cache_entry (Dict[str, Any]): A cache record expected to contain a "releases" key with a list of release dictionaries.
            cached_at (datetime): The timestamp when the cache entry was created or saved.

        Returns:
            Tuple[List[Dict[str, Any]], datetime]: A tuple containing the releases list and the original `cached_at` timestamp.
        """
        releases_data = cache_entry["releases"]
        return (releases_data, cached_at)

    loaded_data = _load_json_cache_with_expiry(
        cache_file_path=_get_releases_cache_file(),
        expiry_hours=RELEASES_CACHE_EXPIRY_HOURS,
        cache_entry_validator=validate_releases_entry,
        entry_processor=process_releases_entry,
        cache_name="releases",
    )

    with _cache_lock:
        if _releases_cache_loaded:
            return
        _releases_cache.update(loaded_data)
        _releases_cache_loaded = True


def _save_releases_cache() -> None:
    """
    Persist in-memory releases cache to platform cache file.

    Writes a JSON object mapping cache keys to records with two fields:
    - `releases`: cached releases data
    - `cached_at`: ISO 8601 timestamp when entry was cached

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

        # Perform I/O outside lock to reduce contention
        if _atomic_write_json(cache_file, cache_data):
            logger.debug("Saved %d releases entries to cache", len(cache_data))
        else:
            logger.warning("Failed to save releases cache to %s", cache_file)

    except OSError as e:
        logger.warning("Could not save releases cache: %s", e)


def _clear_cache_generic(
    cache_dict: Dict[str, Any],
    cache_file_getter: Callable[[], str],
    cache_name: str,
    lock: Any = _cache_lock,
) -> None:
    """
    Clear an in-memory cache and remove its on-disk file if present.

    Clears the provided cache dictionary while holding the given lock, then attempts
    to remove the corresponding cache file returned by `cache_file_getter`. Any
    OS-level errors when removing the file are caught and logged at debug level.

    Parameters:
        cache_dict (Dict[str, Any]): In-memory cache to be cleared.
        cache_file_getter (Callable[[], str]): Callable that returns the path to the on-disk cache file.
        cache_name (str): Human-readable name for logging.
        lock (Any): Lock used to protect the in-memory cache during clearing (defaults to module `_cache_lock`).
    """
    with lock:
        cache_dict.clear()

    try:
        cache_file = cache_file_getter()
        if os.path.exists(cache_file):
            os.remove(cache_file)
            logger.debug("Removed %s cache file", cache_name)
    except OSError as e:
        logger.debug("Error clearing %s cache file: %s", cache_name, e)


def _clear_commit_cache() -> None:
    """
    Clear the in-memory and on-disk commit timestamp cache.

    Also resets the internal loaded flag so the commit cache will be reloaded on next access.
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
    Clear all in-memory and on-disk caches used for releases, commit timestamps, and prerelease data.

    Resets internal cache dictionaries and their "loaded" flags and removes associated persistent cache files so subsequent operations will fetch fresh data from remote sources.
    """
    global _releases_cache, _releases_cache_loaded

    # Clear commit cache using helper
    _clear_commit_cache()

    # Clear prerelease directory cache
    _clear_prerelease_cache()

    # Clear prerelease commit history cache
    _clear_prerelease_commit_history_cache()

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
    allow_env_token: bool = True,
    skip_history_lookup: bool = False,
) -> Optional[str]:
    """
    Select the newest remote prerelease directory that matches the given prerelease base version.

    Parameters:
        expected_version (str): Base prerelease version to match (for example, "2.7.13").
        github_token (Optional[str]): GitHub API token to use for fetching commit timestamps and prerelease history; if None and allow_env_token is True, an environment token may be used.
        force_refresh (bool): If True, bypass cached prerelease history and commit timestamps and fetch fresh values.
        allow_env_token (bool): If True, allow using a GitHub token sourced from the environment when `github_token` is None.
        skip_history_lookup (bool): If True, skip the commit history lookup and go directly to directory scanning fallback.

    Returns:
        Optional[str]: Name of the newest matching prerelease directory (for example "firmware-2.7.13-abcdef"), or `None` if no matching directory is found or an error occurs.
    """
    # First attempt: use commit history (cheap and cached, no per-directory API calls)
    if not skip_history_lookup:
        try:
            latest_dir, _history = _get_latest_active_prerelease_from_history(
                expected_version,
                github_token=github_token,
                force_refresh=force_refresh,
                allow_env_token=allow_env_token,
            )
            if latest_dir:
                logger.debug(
                    "Latest prerelease %s resolved from commit history", latest_dir
                )
                return latest_dir
        except requests.RequestException as e:
            logger.warning(
                "Commit-history prerelease lookup failed due to a network error; "
                "falling back to prerelease directory scan: %s",
                e,
            )
    else:
        logger.debug("Skipping commit history lookup as requested")

    # Fallback: legacy directory scan that may need commit timestamp lookups
    try:
        directories = _fetch_prerelease_directories(
            force_refresh=force_refresh,
            github_token=github_token,
            allow_env_token=allow_env_token,
        )
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
            logger.debug(
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
            Get the commit timestamp for a Meshtastic firmware commit and indicate whether it was newly cached.

            Parameters:
                commit_hash (str | None): Commit SHA to look up. If None or empty, no lookup is performed.

            Returns:
                tuple: (timestamp, fetched_new)
                    timestamp (datetime | None): Commit datetime if available, otherwise None.
                    fetched_new (bool): `true` if a fresh API lookup populated the internal cache for this commit during this call, `false` otherwise.
            """
            if not commit_hash:
                return None, False

            cache_key = f"meshtastic/firmware/{commit_hash}"
            with _cache_lock:
                had_entry = cache_key in _commit_timestamp_cache

            timestamp = get_commit_timestamp(
                "meshtastic",
                "firmware",
                commit_hash,
                github_token,
                force_refresh,
                allow_env_token,
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
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
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
        remote_dir,
        selected_patterns,
        exclude_patterns_list,
        device_manager,
        github_token=github_token,
        allow_env_token=allow_env_token,
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
        if not _is_within_base(real_prerelease_dir, real_target):
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
    allow_env_token: bool = True,
) -> Tuple[bool, List[str]]:
    """
    Detect and download matching prerelease firmware assets for the expected prerelease version.

    Computes the expected prerelease version from latest_release_tag, locates a matching remote prerelease directory (using commit-history or directory scanning), downloads assets that match selected_patterns (respecting exclude_patterns and device_manager), updates on-disk prerelease tracking, and prunes superseded prerelease directories when appropriate.

    Parameters:
        download_dir (str): Base download directory containing firmware/prerelease subdirectory.
        latest_release_tag (str): Official release tag used to compute the expected prerelease version.
        selected_patterns (Optional[List[str]]): Asset selection patterns; if None or empty, no prerelease downloads are attempted.
        exclude_patterns (Optional[List[str]]): Patterns to exclude from matching assets.
        device_manager: Optional device pattern resolver used for device-aware matching.
        github_token (Optional[str]): GitHub API token used for remote lookups when available.
        force_refresh (bool): When True, force remote checks and update tracking even if cached data exists.
        allow_env_token (bool): When True, allow using a token provided via environment variables if an explicit token is not supplied.

    Returns:
        True if any new prerelease assets were downloaded, False otherwise; and a list of relevant prerelease directory name(s) — the downloaded directory when downloads occurred, otherwise existing/inspected directory names; empty list if none.
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

    # Check for existing prereleases locally first
    existing_dirs = _get_existing_prerelease_dirs(prerelease_base_dir)

    # Try to get commit history first (new approach)
    try:
        latest_active_dir, _history_entries = (
            _get_latest_active_prerelease_from_history(
                expected_version,
                github_token=github_token,
                force_refresh=force_refresh,
                allow_env_token=allow_env_token,
            )
        )

        if latest_active_dir:
            logger.info("Using commit history for prerelease detection")

        # Determine which directory to use
        if latest_active_dir and latest_active_dir in existing_dirs:
            # Latest active prerelease already exists locally
            remote_dir = latest_active_dir
            newest_dir = latest_active_dir
            logger.debug(f"Using existing active prerelease: {remote_dir}")
        elif latest_active_dir:
            # Latest active prerelease found remotely but not local
            remote_dir = latest_active_dir
            newest_dir = None
            logger.debug(f"Found remote active prerelease: {remote_dir}")
        else:
            # No active prerelease found, fall back to directory scanning
            remote_dir = None
            newest_dir = None
            logger.debug("No active prerelease found in commit history")
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        logger.debug(f"Failed to get prerelease commit history: {exc}")
        remote_dir = None
        newest_dir = None

    # Fallback to directory scanning if commit history approach failed
    if not remote_dir:
        logger.debug("Falling back to directory scanning approach")

        # Find newest matching prerelease directory
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

        # Find the latest remote prerelease directory (old approach)
        # Skip history lookup since we already tried it above
        remote_dir = _find_latest_remote_prerelease_dir(
            expected_version,
            github_token,
            force_refresh,
            allow_env_token,
            skip_history_lookup=True,
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
        github_token=github_token,
        allow_env_token=allow_env_token,
    )

    # Update tracking information if files were downloaded
    if files_downloaded or force_refresh:
        update_prerelease_tracking(latest_release_tag, remote_dir)

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
    elif newest_dir:
        # Existing prerelease found but no new files downloaded
        return False, [newest_dir]
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
    Persist the in-memory commit timestamp cache to the configured on-disk cache file.

    Timestamps are stored as ISO 8601 strings keyed by commit identifier. On failure the function logs a warning.
    """
    global _commit_timestamp_cache
    cache_file = _get_commit_cache_file()

    try:
        with _cache_lock:
            cache_data = {
                cache_key: (timestamp.isoformat(), cached_at.isoformat())
                for cache_key, (timestamp, cached_at) in _commit_timestamp_cache.items()
            }

        # Perform I/O outside lock to reduce contention
        if _atomic_write_json(cache_file, cache_data):
            logger.debug(f"Saved {len(cache_data)} commit timestamps to cache")
        else:
            logger.warning(f"Failed to save commit timestamp cache to {cache_file}")

    except OSError as e:
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
                release_type_str = (
                    f"{effective_release_type} " if effective_release_type else ""
                )
                logger.debug(
                    f"Using cached {release_type_str}releases for {url} (cached {age.total_seconds():.0f}s ago)"
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

    releases: List[Dict[str, Any]] = []  # Initialize to prevent NameError
    per_page = max(1, min(GITHUB_MAX_PER_PAGE, scan_count))
    page = 1
    seen_release_ids: Set[Any] = set()
    try:
        # Add progress feedback
        if effective_release_type:
            logger.info(f"Fetching {effective_release_type} releases from GitHub...")
        else:
            # Fallback for generic case
            logger.info("Fetching releases from GitHub...")

        while len(releases) < scan_count:
            response: requests.Response = make_github_api_request(
                url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                params={"per_page": per_page, "page": page},
                timeout=GITHUB_API_TIMEOUT,
            )

            try:
                page_releases: List[Dict[str, Any]] = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON from {url}: {e}")
                return []

            if not isinstance(page_releases, list):
                logger.warning(
                    "Unexpected response when fetching releases from %s: %s",
                    url,
                    type(page_releases).__name__,
                )
                break

            if not page_releases:
                break

            for release in page_releases:
                release_id = release.get("id")
                if release_id is not None and release_id in seen_release_ids:
                    continue
                if release_id is not None:
                    seen_release_ids.add(release_id)
                releases.append(release)

            if len(releases) >= scan_count:
                break

            page += 1

        # Log how many releases were fetched
        logger.debug(
            "Fetched %d releases from GitHub API (across %d page%s)",
            len(releases),
            page,
            "" if page == 1 else "s",
        )

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

    cache_dir = _ensure_cache_dir()
    paths_and_urls: Dict[str, str] = {
        "download_dir": download_dir,
        "firmware_dir": firmware_dir,
        "apks_dir": apks_dir,
        "cache_dir": cache_dir,
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
    Manage firmware release downloads, enforce retention policies, and optionally discover and download firmware prereleases.

    Parameters:
        config (Dict[str, Any]): Runtime configuration containing feature flags, selection patterns, retention counts, GitHub/token options, and related behavior toggles.
        paths_and_urls (Dict[str, str]): Precomputed filesystem paths and remote URLs used for caches, downloads, and storage (keys used include cache_dir, firmware_dir, download_dir, firmware_releases_url).
        force_refresh (bool): If true, bypass on-disk and remote caches and force fresh remote queries.

    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
            - downloaded firmwares: list of firmware versions that were newly downloaded; prerelease entries are prefixed with "pre-release ".
            - newly detected release versions: release tags discovered that were not previously tracked.
            - failed download details: list of records describing each failed asset download (each record is a dict with context and error info).
            - latest firmware release tag: the most recent firmware release tag discovered, or `None` if none found.
            - latest prerelease version: the currently tracked latest prerelease version, or `None` if not tracked.
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
    latest_firmware_prerelease_version: Optional[str] = None

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
                paths_and_urls["cache_dir"],
                "Firmware",
                paths_and_urls["firmware_dir"],
                config.get(
                    "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
                ),
                config.get("EXTRACT_PATTERNS", []),
                selected_patterns=config.get("SELECTED_FIRMWARE_ASSETS", []),  # type: ignore
                auto_extract=config.get("AUTO_EXTRACT", False),
                exclude_patterns=_get_string_list_from_config(
                    config, "EXCLUDE_PATTERNS"
                ),
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

        # Read latest release tag from the JSON tracking file
        firmware_json_file = os.path.join(
            paths_and_urls["cache_dir"], LATEST_FIRMWARE_RELEASE_JSON_FILE
        )
        latest_release_tag = _read_latest_release_tag(firmware_json_file)

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
                        exclude_patterns=_get_string_list_from_config(
                            config, "EXCLUDE_PATTERNS"
                        ),
                        device_manager=device_manager,
                        github_token=config.get("GITHUB_TOKEN"),
                        force_refresh=force_refresh,
                        allow_env_token=config.get("ALLOW_ENV_TOKEN", True),
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
                tracking_info = get_prerelease_tracking_info(
                    github_token=config.get("GITHUB_TOKEN"),
                    force_refresh=force_refresh,
                    allow_env_token=config.get("ALLOW_ENV_TOKEN", True),
                )
                _display_prerelease_summary(tracking_info)
                latest_firmware_prerelease_version = (
                    tracking_info.get("latest_prerelease")
                    if isinstance(tracking_info, dict)
                    else None
                )
            else:
                logger.info("No latest release tag found. Skipping pre-release check.")
    elif not config.get("SELECTED_FIRMWARE_ASSETS", []):
        logger.info("No firmware assets selected. Skipping firmware download.")

    return (
        downloaded_firmwares,
        new_firmware_versions,
        all_failed_firmware_downloads,
        latest_firmware_version,
        latest_firmware_prerelease_version,
    )


def _download_release_type(
    releases_to_process: List[Dict[str, Any]],
    release_type: str,
    cache_dir: str,
    download_dir: str,
    keep_count: int,
    exclude_patterns: Optional[List[str]],
    selected_patterns: Optional[List[str]],
    force_refresh: bool = False,
    perform_cleanup: bool = True,
) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    """
    Download and process a specific release type (stable or prerelease), returning what was obtained and any failures.

    Parameters:
        releases_to_process (List[Dict[str, Any]]): Releases considered for download (most recent first).
        release_type (str): Human-readable label for the release type used in logs.
        cache_dir (str): Path to the on-disk cache directory.
        download_dir (str): Path where downloaded release assets will be saved.
        keep_count (int): Maximum number of releases from `releases_to_process` to attempt.
        exclude_patterns (List[str]): Glob/regex patterns for assets to exclude.
        selected_patterns (List[str]): Asset selection patterns to include.
        force_refresh (bool): If true, bypass cached validation and re-download when applicable.
        perform_cleanup (bool): If true, perform version-based cleanup of old releases. When False, skip cleanup.

    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]]]:
            - downloaded: List of version strings successfully downloaded.
            - new_versions: List of version strings that are newer than the currently recorded/latest versions.
            - failed_downloads: List of dictionaries describing failures (e.g., contains keys such as `"version"` and `"reason"`).
    """
    downloaded: List[str]
    new_versions_list: List[str]
    failed_downloads_details: List[Dict[str, str]]

    downloaded, new_versions_list, failed_downloads_details = check_and_download(
        releases_to_process[:keep_count],
        cache_dir,
        release_type,
        download_dir,
        keep_count,
        [],  # no extract_patterns for APKs
        selected_patterns=selected_patterns or [],
        auto_extract=False,
        exclude_patterns=exclude_patterns or [],
        force_refresh=force_refresh,
        perform_cleanup=perform_cleanup,
    )

    if downloaded:
        logger.info(f"Downloaded {release_type} versions: {', '.join(downloaded)}")

    return downloaded, new_versions_list, failed_downloads_details


def _process_apk_downloads(
    config: Dict[str, Any], paths_and_urls: Dict[str, str], force_refresh: bool = False
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
    """
    Download and prune Android APK releases according to the provided configuration.

    Fetches Android release metadata, downloads matching APK assets into the configured directory, retains the configured number of stable releases, and manages APK prereleases separately (prereleases are downloaded to a dedicated prerelease directory, do not count against stable retention, and are removed when a corresponding full release exists).

    Parameters:
        config (Dict[str, Any]): Configuration mapping. Relevant keys:
            - SAVE_APKS: whether to save APKs.
            - SELECTED_APK_ASSETS: list of asset filename patterns to download.
            - ANDROID_VERSIONS_TO_KEEP: number of stable APK releases to retain.
            - CHECK_APK_PRERELEASES: whether to process APK prereleases.
            - EXCLUDE_PATTERNS: list of filename patterns to exclude.
            - GITHUB_TOKEN: optional GitHub token for API requests.
        paths_and_urls (Dict[str, str]): Paths and endpoints. Relevant keys:
            - "android_releases_url": GitHub API URL for Android releases.
            - "cache_dir": directory for caching remote metadata/blobs.
            - "apks_dir": base directory where APKs (and prereleases) are stored.
        force_refresh (bool): If True, bypass cached release data and fetch fresh metadata.

    Returns:
        Tuple[List[str], List[str], List[Dict[str, str]], Optional[str], Optional[str]]:
        - downloaded_apk_versions: List of release tags that had assets downloaded during this run.
        - new_apk_versions: List of release tags discovered (including prereleases) during scanning.
        - failed_downloads: List of dicts describing failed asset downloads (each dict contains failure details).
        - latest_apk_version: Tag of the most recent stable APK release found, or `None` if none available.
        - latest_prerelease_version: Tag of the most recent APK prerelease found, or `None` if none available or prerelease handling is disabled.
    """
    global downloads_skipped
    downloaded_apks: List[str] = []
    new_apk_versions: List[str] = []
    all_failed_apk_downloads: List[Dict[str, str]] = (
        []
    )  # Initialize all_failed_apk_downloads
    latest_apk_version: Optional[str] = None
    latest_prerelease_version: Optional[str] = None

    if config.get("SAVE_APKS", False) and config.get("SELECTED_APK_ASSETS", []):
        # Increase scan count so prereleases cannot starve stable releases,
        # even when APK prerelease downloads are disabled.
        min_stable_releases_to_find = config.get(
            "ANDROID_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT
        )

        # Use improved scan logic to prevent stable APK starvation.
        # Start with a window twice the keep count but never exceed the per-page cap.
        max_scan = GITHUB_MAX_PER_PAGE
        scan_count = min(max_scan, min_stable_releases_to_find * 2)
        latest_android_releases: List[Dict[str, Any]] = []
        regular_releases: List[Dict[str, Any]] = []
        prerelease_releases: List[Dict[str, Any]] = []

        while scan_count <= max_scan:
            latest_android_releases = _get_latest_releases_data(
                paths_and_urls["android_releases_url"],
                scan_count,
                config.get("GITHUB_TOKEN"),
                force_refresh=force_refresh,
                release_type="Android APK",
            )
            regular_releases = []
            prerelease_releases = []
            for release in latest_android_releases:
                tag_name = release.get("tag_name", "")
                if not _is_supported_android_release(tag_name):
                    logger.debug(
                        "Skipping legacy Android release %s (pre-2.7.0 tagging scheme)",
                        tag_name or "<unknown>",
                    )
                    continue

                if _is_apk_prerelease(release):
                    prerelease_releases.append(release)
                else:
                    regular_releases.append(release)

            if (
                len(regular_releases) >= min_stable_releases_to_find
                or len(latest_android_releases) < scan_count
            ):
                # Either we have enough stable releases, or we hit the end of history.
                break

            # Not enough stable releases yet and there might be more history; widen the window.
            if scan_count >= max_scan:
                logger.debug(
                    "Reached maximum APK scan window (%d) without finding %d stable "
                    "releases; proceeding with %d stable release(s).",
                    max_scan,
                    min_stable_releases_to_find,
                    len(regular_releases),
                )
                break

            scan_count = min(max_scan, scan_count * 2)

        # Set keep_count_apk to actual config value for download logic
        keep_count_apk = config.get(
            "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
        )

        # Handle regular releases
        if regular_releases:
            logger.info(
                _summarise_release_scan(
                    "Android APK", len(regular_releases), keep_count_apk
                )
            )

            # Extract the actual latest APK version
            latest_apk_version = regular_releases[0].get("tag_name")

            apk_downloaded, apk_new_versions_list, failed_apk_downloads_details = (
                _download_release_type(
                    regular_releases,
                    "Android APK",
                    paths_and_urls["cache_dir"],
                    paths_and_urls["apks_dir"],
                    keep_count_apk,
                    _get_string_list_from_config(config, "EXCLUDE_PATTERNS"),
                    selected_patterns=_get_string_list_from_config(
                        config, "SELECTED_APK_ASSETS"
                    ),
                    force_refresh=force_refresh,
                )
            )
            downloaded_apks.extend(apk_downloaded)
            new_apk_versions.extend(apk_new_versions_list)
            all_failed_apk_downloads.extend(failed_apk_downloads_details)
        else:
            latest_apk_version = None

        # Check if we have a full release that would make prereleases obsolete
        has_full_release = bool(regular_releases)

        # Clean up APK prereleases if we have full releases, regardless of prerelease setting
        if has_full_release:
            prerelease_dir = os.path.join(
                paths_and_urls["apks_dir"], APK_PRERELEASES_DIR_NAME
            )
            _cleanup_apk_prereleases(
                prerelease_dir,
                regular_releases[0].get("tag_name"),
            )

        # Handle prereleases if enabled
        if (
            config.get("CHECK_APK_PRERELEASES", DEFAULT_CHECK_APK_PRERELEASES)
            and prerelease_releases
        ):
            prerelease_dir = os.path.join(
                paths_and_urls["apks_dir"], APK_PRERELEASES_DIR_NAME
            )
            os.makedirs(prerelease_dir, exist_ok=True)

            # Filter out obsolete prereleases before downloading to avoid unnecessary work
            releases_to_download = prerelease_releases
            if has_full_release:
                latest_full_release_tag = regular_releases[0].get("tag_name")
                if latest_full_release_tag:
                    latest_release_tuple = _get_release_tuple(latest_full_release_tag)
                    if latest_release_tuple:
                        releases_to_download = []
                        for r in prerelease_releases:
                            prerelease_tuple = _get_release_tuple(r.get("tag_name", ""))
                            if (
                                prerelease_tuple is None
                                or prerelease_tuple > latest_release_tuple
                            ):
                                # Keep pre-releases with non-standard versioning or newer than latest release
                                releases_to_download.append(r)
                        obsolete_count = len(prerelease_releases) - len(
                            releases_to_download
                        )
                        if obsolete_count > 0:
                            logger.debug(
                                "Skipping download of %d APK prerelease(s) superseded by release %s.",
                                obsolete_count,
                                latest_full_release_tag,
                            )

            prerelease_downloaded: List[str] = []
            prerelease_new_versions_list: List[str] = []
            failed_prerelease_downloads_details: List[Dict[str, str]] = []
            if releases_to_download:
                (
                    prerelease_downloaded,
                    prerelease_new_versions_list,
                    failed_prerelease_downloads_details,
                ) = _download_release_type(
                    releases_to_download,
                    "Android APK Prerelease",
                    paths_and_urls["cache_dir"],
                    prerelease_dir,
                    len(releases_to_download),
                    _get_string_list_from_config(config, "EXCLUDE_PATTERNS"),
                    selected_patterns=_get_string_list_from_config(
                        config, "SELECTED_APK_ASSETS"
                    ),
                    force_refresh=force_refresh,
                    perform_cleanup=False,
                )
            downloaded_apks.extend(prerelease_downloaded)
            new_apk_versions.extend(prerelease_new_versions_list)
            all_failed_apk_downloads.extend(failed_prerelease_downloads_details)

            # Set latest prerelease version only if we have prereleases to download
            if releases_to_download:
                latest_prerelease_version = releases_to_download[0].get("tag_name")
    elif not config.get("SELECTED_APK_ASSETS", []):
        logger.info("No APK assets selected. Skipping APK download.")

    return (
        downloaded_apks,
        new_apk_versions,
        all_failed_apk_downloads,
        latest_apk_version,
        latest_prerelease_version,
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
    latest_firmware_prerelease_version: Optional[str] = None,
    latest_apk_prerelease_version: Optional[str] = None,
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
        latest_firmware_prerelease_version (Optional[str]): Latest firmware prerelease identifier discovered, if available.
        latest_apk_prerelease_version (Optional[str]): Latest APK prerelease identifier discovered, if available.

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
    if latest_firmware_prerelease_version:
        logger.info(f"Latest firmware prerelease: {latest_firmware_prerelease_version}")
    if latest_apk_prerelease_version:
        logger.info(f"Latest APK prerelease: {latest_apk_prerelease_version}")

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
    real_extract_dir = os.path.realpath(extract_dir)
    prospective_path = os.path.join(real_extract_dir, file_path)
    normalized_path = os.path.realpath(prospective_path)

    if not _is_within_base(real_extract_dir, normalized_path):
        raise ValueError(
            f"Unsafe extraction path '{file_path}' is outside base '{extract_dir}'"
        )

    return normalized_path


def extract_files(
    zip_path: str, extract_dir: str, patterns: List[str], exclude_patterns: List[str]
) -> None:
    """
    Extract selected files from a ZIP archive into the given directory.

    Only archive members whose base filename matches one of the provided inclusion patterns and do not match any exclusion patterns are extracted; if `patterns` is empty, extraction is skipped. The archive's internal directory structure is preserved and missing target directories are created. Files whose base name ends with the configured shell-script extension are given executable permissions after extraction. Unsafe extraction paths are skipped; a corrupted ZIP file will be removed. IO, OS, and ZIP errors are handled and logged internally.

    Parameters:
        zip_path (str): Path to the ZIP archive to read.
        extract_dir (str): Destination directory where matching files will be extracted.
        patterns (List[str]): Inclusion patterns used to select files; an empty list causes no extraction.
        exclude_patterns (List[str]): Glob-style patterns applied to the base filename to exclude matching entries.
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
                if file_info.is_dir():
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
                            SHELL_SCRIPT_EXTENSION
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
    Prune immediate subdirectories of `directory`, preserving only the specified release basenames and a small set of protected internal directories.

    Removes any child directory whose basename is not in `releases_to_keep` and not one of the protected internal names (REPO_DOWNLOADS_DIR, FIRMWARE_PRERELEASES_DIR_NAME, APK_PRERELEASES_DIR_NAME). Deletion failures are logged and not propagated.

    Parameters:
        directory (str): Path whose immediate subdirectories represent versioned releases.
        releases_to_keep (List[str]): Basenames of subdirectories that must be preserved.
    """
    excluded_dirs: Set[str] = {
        REPO_DOWNLOADS_DIR,
        FIRMWARE_PRERELEASES_DIR_NAME,
        APK_PRERELEASES_DIR_NAME,
    }
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


MIN_ANDROID_TRACKED_VERSION = (2, 7, 0)


def _is_supported_android_release(tag_name: str) -> bool:
    """
    Return True when the tag_name represents an Android release at or beyond the
    version where the new tagging scheme began (2.7.0+).

    Older prerelease tags (e.g., 2.6.x-open) should be ignored so they are not
    treated as current prereleases. Unparsable tags are allowed through to
    avoid blocking future formats.
    """
    version_tuple = _get_release_tuple(tag_name)
    if not version_tuple:
        return True

    max_len = max(len(version_tuple), len(MIN_ANDROID_TRACKED_VERSION))
    padded_version = version_tuple + (0,) * (max_len - len(version_tuple))
    padded_minimum = MIN_ANDROID_TRACKED_VERSION + (0,) * (
        max_len - len(MIN_ANDROID_TRACKED_VERSION)
    )

    return padded_version >= padded_minimum


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    """
    Determine if an APK release represents a prerelease.

    Prereleases are identified by either:
    1. Containing '-open' or '-closed' in the tag name (legacy pattern)
    2. GitHub's prerelease flag being True (standard GitHub prereleases)

    Parameters:
        release (Dict[str, Any]): The release object containing tag_name and prerelease fields.

    Returns:
        bool: True if the release represents a prerelease, False otherwise.
    """
    tag_name = release.get("tag_name", "")
    # Check legacy pattern (-open/-closed) OR GitHub's prerelease flag
    is_legacy_prerelease = _is_apk_prerelease_by_name(tag_name)
    is_github_prerelease = release.get("prerelease", False)
    return is_legacy_prerelease or is_github_prerelease


def _is_apk_prerelease_by_name(tag_name: str) -> bool:
    """
    Determine if an APK tag name represents a prerelease (for directory name checks).

    This function only checks the tag name pattern since directory names don't have
    the GitHub prerelease flag.

    Parameters:
        tag_name (str): The tag name to check.

    Returns:
        bool: True if the tag name represents a prerelease, False otherwise.
    """
    return "-open" in tag_name.lower() or "-closed" in tag_name.lower()


def _cleanup_apk_prereleases(
    prerelease_dir: str, full_release_tag: Optional[str]
) -> None:
    """
    Remove APK prerelease subdirectories that are superseded by a provided full release tag.

    Does nothing if `full_release_tag` is falsy. Looks for subdirectories in `prerelease_dir`
    that are identified as APK prereleases and removes them if their version is less than or
    equal to the version of the `full_release_tag`.

    Parameters:
        prerelease_dir (str): Path containing prerelease subdirectories to inspect.
        full_release_tag (Optional[str]): Full release tag (e.g., "v2.7.7"); used for version comparison.
    """
    if not full_release_tag:
        return

    latest_release_tuple = _get_release_tuple(full_release_tag)
    if not latest_release_tuple:
        logger.debug(
            "Could not parse latest full release tag '%s' for APK prerelease cleanup.",
            full_release_tag,
        )
        return

    if not os.path.isdir(prerelease_dir):
        return

    try:
        for item in os.listdir(prerelease_dir):
            item_path = os.path.join(prerelease_dir, item)
            if not os.path.isdir(item_path):
                continue

            # Cleanup if the prerelease version is less than or equal to the full release version
            prerelease_tuple = _get_release_tuple(item)
            if (
                prerelease_tuple is not None
                and prerelease_tuple <= latest_release_tuple
            ):
                if _safe_rmtree(item_path, prerelease_dir, item):
                    logger.info(f"Removed obsolete prerelease directory: {item_path}")
    except OSError as e:
        logger.warning(f"Error cleaning up prerelease directories: {e}")


def strip_unwanted_chars(text: str) -> str:
    """
    Remove all non-ASCII characters from a string.

    Parameters:
        text (str): Input string that may contain non-ASCII characters (e.g., emojis, accented letters).

    Returns:
        str: A new string containing only the ASCII characters from the original input.
    """
    return NON_ASCII_RX.sub("", text)


def _is_release_complete(
    release_data: Dict[str, Any],
    release_dir: str,
    selected_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> bool:
    """
    Check whether a local release directory contains all expected assets that match the provided include/exclude patterns and pass basic integrity and size checks.

    This evaluates the release metadata's "assets" list against files present in release_dir: it filters assets by selected_patterns and exclude_patterns, requires each expected file to exist, verifies ZIP files are not corrupted, and compares file sizes to the expected sizes when available.

    Parameters:
        release_data (Dict[str, Any]): Release metadata containing an "assets" list; each asset should include a "name" and may include "size" for verification.
        release_dir (str): Filesystem path to the local directory holding the downloaded release assets.
        selected_patterns (Optional[List[str]]): Inclusion patterns; when provided, only assets matching these patterns are considered expected.
        exclude_patterns (List[str]): fnmatch-style patterns; any asset matching one of these is ignored.

    Returns:
        bool: `True` if every expected asset is present and passes integrity and size checks, `False` otherwise.
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
        if asset_name.lower().endswith(ZIP_EXTENSION):
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
                        if expected_size is not None:
                            if actual_size != expected_size:
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
    cache_dir: str,
    release_type: str,
    download_dir_path: str,
    versions_to_keep: int,
    extract_patterns: List[str],
    selected_patterns: Optional[List[str]] = None,
    auto_extract: bool = False,
    exclude_patterns: Optional[List[str]] = None,
    force_refresh: bool = False,
    perform_cleanup: bool = True,
) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    """
    Check releases for missing or corrupted assets, download matching assets, optionally extract ZIPs, and prune old release directories.

    Processes up to `versions_to_keep` newest entries from `releases`. For each release it:
    - Skips releases that are already complete.
    - Schedules and downloads assets that match `selected_patterns` (if provided) and do not match `exclude_patterns`.
    - Optionally extracts files from ZIP assets when `auto_extract` is True and `release_type == "Firmware"`, using `extract_patterns` to select files.
    - Writes release notes, sets executable bits on shell scripts, and prunes old release subdirectories outside the retention window.
    - Atomically updates the release tracking file when a newer release has been successfully processed.

    Side effects:
    - Creates per-release subdirectories and may write release tracking files and release notes.
    - May remove corrupted ZIP files and delete older release directories.
    - Honors a global Wi-Fi gating flag: if downloads are skipped globally, the function will not perform downloads and instead returns newer release tags.

    Parameters:
    - releases: List of release dictionaries (expected newest-first order) as returned by the API.
    - cache_dir: Directory where release tracking files are stored.
    - release_type: Human-readable type used in logs and failure records (e.g., "Firmware" or "APK").
    - download_dir_path: Root directory where per-release subdirectories are created.
    - versions_to_keep: Number of newest releases to consider for download/retention.
    - extract_patterns: Patterns used to select files to extract from ZIP archives.
    - selected_patterns: Optional list of asset name patterns to include; if omitted all assets are considered.
    - auto_extract: When True and `release_type == "Firmware"`, perform extraction of matching ZIP contents.
    - exclude_patterns: Optional list of patterns; matching filenames are excluded from download and extraction.
    - force_refresh: If True, bypass cache and fetch fresh data.
    - perform_cleanup: When True, perform version-based cleanup of old releases. When False, skip cleanup.

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
    already_complete_releases: set[str] = set()

    if not os.path.exists(download_dir_path):
        os.makedirs(download_dir_path)

    real_download_base = os.path.realpath(download_dir_path)

    # Read saved release tag from the JSON tracking file
    json_basename = _get_json_release_basename(release_type)
    json_file = os.path.join(cache_dir, json_basename)
    saved_raw_tag = _read_latest_release_tag(json_file)
    saved_release_tag = (
        _sanitize_path_component(saved_raw_tag) if saved_raw_tag else None
    )
    if saved_raw_tag is not None and saved_release_tag is None:
        logger.warning(
            "Ignoring unsafe contents in latest release file %s",
            json_file,
        )

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
                # Track that this release was already complete
                already_complete_releases.add(release_tag)
                # Update latest_release_file if this is the most recent release
                if release_tag != saved_release_tag and idx == 1:
                    # Use json_file calculated at function start
                    if _write_latest_release_tag(json_file, release_tag, release_type):
                        logger.debug(
                            "Updated latest release tag to %s (complete release)",
                            release_tag,
                        )
                # Don't add to new_versions_available for already-complete releases
                # This prevents showing already-downloaded releases as "new"
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

                if file_name.lower().endswith(ZIP_EXTENSION):
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

                    if file_name.lower().endswith(ZIP_EXTENSION):
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
                            # Validate extraction patterns are working correctly
                            _validate_extraction_patterns(
                                zip_path,
                                extract_patterns,
                                exclude_patterns_list,
                                raw_release_tag,
                            )
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
                            # Use json_file calculated at function start
                            if _write_latest_release_tag(
                                json_file, release_tag, release_type
                            ):
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
                # Use json_file calculated at function start
                if _write_latest_release_tag(
                    json_file, latest_release_tag_val, release_type
                ):
                    logger.debug(
                        "Updated latest release tag to %s", latest_release_tag_val
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
    if actions_taken and perform_cleanup:
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
    # This ensures users are notified about new versions even if download failed.
    # Exclude releases that were already complete to avoid showing already-downloaded releases as "new"
    new_candidates: List[str] = [
        t
        for t in newer_tags
        if t not in downloaded_versions and t not in already_complete_releases
    ]

    if not actions_taken and not new_candidates:
        logger.info(f"All {release_type} assets are up to date.")

    # Merge uniquely with any earlier additions
    new_versions_available = list(
        dict.fromkeys(new_versions_available + new_candidates)
    )

    return downloaded_versions, new_versions_available, failed_downloads_details


def set_permissions_on_sh_files(directory: str) -> None:
    """
    Set executable permissions on files ending with the shell script extension under a directory.

    Recursively walks `directory` and makes files whose names end with `SHELL_SCRIPT_EXTENSION` (case-insensitive) executable using `EXECUTABLE_PERMISSIONS`. IO and permission errors are logged and do not propagate.
    """
    root: str
    files: List[str]
    try:
        for root, _dirs, files in os.walk(directory):
            file_in_dir: str
            for file_in_dir in files:
                if file_in_dir.lower().endswith(SHELL_SCRIPT_EXTENSION):
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


def _validate_extraction_patterns(
    zip_path: str, patterns: List[str], exclude_patterns: List[str], release_tag: str
) -> None:
    """
    Validate that extraction patterns are correctly matching files in a ZIP archive.

    Logs which patterns successfully match files and warns when no patterns match.
    This helps identify issues with pattern matching, especially for patterns with trailing
    separators that were fixed in PR #116.

    Parameters:
        zip_path (str): Path to the ZIP archive to validate.
        patterns (List[str]): Inclusion patterns to validate.
        exclude_patterns (List[str]): Exclusion patterns applied during validation to match extraction behavior.
        release_tag (str): Release tag for logging purposes.

    Returns:
        None: Results are logged, no return value.
    """
    if not patterns:
        logger.debug("No extraction patterns to validate for %s", release_tag)
        return

    try:
        pattern_matches: Dict[str, List[str]] = {}
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            file_info: zipfile.ZipInfo
            for file_info in zip_ref.infolist():
                # Skip directory entries in archives
                if file_info.is_dir():
                    continue
                file_name: str = file_info.filename
                base_name: str = os.path.basename(file_name)
                if not base_name:
                    continue
                if _matches_exclude(base_name, exclude_patterns):
                    continue

                # Check which patterns match this file using the same logic as extraction
                for pattern in patterns:
                    trimmed = pattern.strip()
                    if not trimmed:
                        continue
                    if matches_selected_patterns(base_name, [trimmed]):
                        pattern_matches.setdefault(trimmed, []).append(base_name)

        # Log validation results
        if pattern_matches:
            logger.info(
                "Pattern validation for %s: %d patterns matched files",
                release_tag,
                len(pattern_matches),
            )
            for pattern, matched_files in pattern_matches.items():
                logger.debug(
                    "Pattern '%s' matched %d files: %s",
                    pattern,
                    len(matched_files),
                    (
                        [*matched_files[:3], "..."]
                        if len(matched_files) > 3
                        else matched_files
                    ),
                )
        else:
            logger.warning(
                "Pattern validation for %s: No patterns matched any files in ZIP archive",
                release_tag,
            )
            logger.debug("Available patterns: %s", patterns)

    except zipfile.BadZipFile:
        logger.error(
            "Cannot validate patterns for %s: %s is corrupted", release_tag, zip_path
        )
    except (OSError, ValueError) as e:
        logger.error("Error validating patterns for %s: %s", release_tag, e)


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
        bool: True if at least one matched file in the ZIP is missing from `extract_dir` (extraction needed), False otherwise.

    Notes:
        - If the ZIP is corrupted (`zipfile.BadZipFile`), the function attempts to remove the ZIP file and returns False.
        - If an IO/OSError or another unexpected exception occurs while inspecting the ZIP, the function conservatively returns True (assumes extraction is needed).
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
                if file_info.is_dir():
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

    # Highlight API requests that weren't tied to a cache lookup (e.g., pagination)
    uncached_requests = max(0, summary["total_requests"] - summary["cache_misses"])
    if uncached_requests > 0 and total_cache_lookups > 0:
        direct_label = (
            "direct API request" if uncached_requests == 1 else "direct API requests"
        )
        log_parts.append(
            f"{uncached_requests} {direct_label} (pagination/non-cacheable)"
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


def _cleanup_legacy_files(
    config: Dict[str, Any], paths_and_urls: Dict[str, str]
) -> None:
    """
    Remove legacy tracking and release files left in download directories after JSON-based tracking is used.

    Deletes legacy prerelease tracking text files from the prerelease directory and legacy latest-release files from the firmware and apks download subdirectories. No on-disk migration is performed; missing paths are ignored and failures are logged as warnings.

    Parameters:
        config (Dict[str, Any]): Configuration mapping; may provide "PRERELEASE_DIR" to locate prerelease files.
        paths_and_urls (Dict[str, str]): Mapping that must include "download_dir" to locate firmware and apks directories.
    """
    try:
        # Clean up legacy files from download directories
        download_dir = paths_and_urls.get("download_dir")
        if not download_dir:
            return

        # Support both legacy config-based and direct path approaches for prerelease dir
        prerelease_dir = config.get("PRERELEASE_DIR") or os.path.join(
            download_dir, "firmware", FIRMWARE_PRERELEASES_DIR_NAME
        )
        if prerelease_dir and os.path.exists(prerelease_dir):
            # Remove specific legacy text tracking files
            legacy_files = [
                PRERELEASE_COMMITS_LEGACY_FILE,
                PRERELEASE_TRACKING_JSON_FILE,
            ]
            for filename in legacy_files:
                legacy_file = os.path.join(prerelease_dir, filename)
                if os.path.exists(legacy_file):
                    try:
                        os.remove(legacy_file)
                        logger.debug(
                            "Removed legacy prerelease tracking file: %s", legacy_file
                        )
                    except OSError as e:
                        logger.warning(
                            "Could not remove legacy file %s: %s", legacy_file, e
                        )

        # Remove legacy release files from download directories (not cache)
        firmware_dir = os.path.join(download_dir, "firmware")
        apks_dir = os.path.join(download_dir, "apks")
        legacy_files_to_remove = {
            "firmware": os.path.join(firmware_dir, LATEST_FIRMWARE_RELEASE_FILE),
            "android": os.path.join(apks_dir, LATEST_ANDROID_RELEASE_FILE),
        }
        for release_type, legacy_file in legacy_files_to_remove.items():
            if legacy_file and os.path.exists(legacy_file):
                try:
                    os.remove(legacy_file)
                    logger.debug(
                        "Removed legacy %s release file: %s",
                        release_type,
                        legacy_file,
                    )
                except OSError as e:
                    logger.warning(
                        "Could not remove legacy %s file %s: %s",
                        release_type,
                        legacy_file,
                        e,
                    )

    except OSError as e:
        logger.warning("Error removing legacy files: %s", e)


def main(force_refresh: bool = False) -> None:
    """
    Run the main Fetchtastic workflow: perform startup/configuration, optionally clear caches, process firmware and APK downloads with a retry pass for failures, clean legacy files, and send final notifications.

    Parameters:
        force_refresh (bool): If True, clear all persistent caches and the device hardware cache before fetching remote data.
    """
    start_time: float = time.time()
    logger.info("Starting Fetchtastic...")  # Changed to logger.info

    # Reset Wi-Fi gating flag for each run
    global downloads_skipped
    downloads_skipped = False

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
        latest_firmware_prerelease_version,
    ) = _process_firmware_downloads(config, paths_and_urls, force_refresh)
    (
        downloaded_apks,
        new_apk_versions,
        failed_apk_list,
        latest_apk_version,
        latest_apk_prerelease_version,
    ) = _process_apk_downloads(config, paths_and_urls, force_refresh)

    # Clean up legacy files - we fetch fresh data instead of migrating old data
    logger.debug("Cleaning up legacy files")
    _cleanup_legacy_files(config, paths_and_urls)

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
                elif failure_detail["type"] in (
                    "Android APK",
                    "Android APK Prerelease",
                ):
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
        latest_firmware_prerelease_version,
        latest_apk_prerelease_version,
    )

    # Log API request summary at debug level
    summary = get_api_request_summary()
    if summary["total_requests"] > 0:
        logger.debug(_format_api_summary(summary))
    else:
        logger.debug(
            "📊 GitHub API Summary: No API requests made (all data served from cache)"
        )


if __name__ == "__main__":
    main()
