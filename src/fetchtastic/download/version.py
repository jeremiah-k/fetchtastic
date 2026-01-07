"""
Version Management for Fetchtastic Download Subsystem

This module provides version parsing, comparison, and tracking utilities
that are used across all downloaders for consistent version handling.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

from fetchtastic.constants import (
    FIRMWARE_DIR_PREFIX,
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    PRERELEASE_ADD_COMMIT_PATTERN,
    PRERELEASE_DELETE_COMMIT_PATTERN,
    VERSION_REGEX_PATTERN,
)
from fetchtastic.log_utils import logger

from .files import _atomic_write_json


class VersionManager:
    """
    Manages version parsing, comparison, and tracking for Meshtastic releases.

    This class encapsulates all version-related logic including:
    - Version normalization and parsing
    - Version comparison with PEP 440 semantics
    - Version tuple extraction for efficient comparisons
    - Prerelease version handling
    """

    # Compiled regex for performance
    NON_ASCII_RX = re.compile(r"[^\x00-\x7F]+")
    VERSION_VALIDATION_RX = re.compile(VERSION_REGEX_PATTERN, re.IGNORECASE)
    PRERELEASE_VERSION_RX = re.compile(
        r"^(\d+(?:\.\d+)*)[.-](rc|dev|alpha|beta|b)\.?(\d*)$", re.IGNORECASE
    )
    HASH_SUFFIX_VERSION_RX = re.compile(
        r"^(\d+\.\d+\.\d+)\.([a-f0-9]{6,})$",
        re.IGNORECASE,
    )
    VERSION_BASE_RX = re.compile(r"^(\d+(?:\.\d+)*)")
    PRERELEASE_DIR_SEGMENT_RX = re.compile(
        r"(firmware-\d+\.\d+\.\d+\.[a-f0-9]{6,})", re.IGNORECASE
    )
    _PRERELEASE_ADD_RX = re.compile(PRERELEASE_ADD_COMMIT_PATTERN)
    _PRERELEASE_DELETE_RX = re.compile(PRERELEASE_DELETE_COMMIT_PATTERN)

    def normalize_version(
        self, version: Optional[str]
    ) -> Optional[Union[Version, Any]]:
        """
        Normalize a repository-style version string into a PEP 440-compatible Version object.

        Converts and strips common repository formatting such as a leading "v", common
        prerelease words ("alpha", "beta", "rc", "dev") into PEP 440 prerelease
        notations, and converts trailing commit/hash-like suffixes into a local version
        identifier when possible. Returns None for empty, missing, or unparsable input.

        Parameters:
            version (Optional[str]): Raw version string that may include a leading "v",
                prerelease words, or a hash suffix.

        Returns:
            Optional[Version|Any]: A parsed Version (or LegacyVersion-like) object on
            success, or `None` if the input is empty or cannot be parsed.
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
            m_pr = self.PRERELEASE_VERSION_RX.match(trimmed)
            if m_pr:
                pr_kind_lower = m_pr.group(2).lower()
                kind = {"alpha": "a", "beta": "b"}.get(pr_kind_lower, pr_kind_lower)
                num = m_pr.group(3) or "0"
                try:
                    return parse_version(f"{m_pr.group(1)}{kind}{num}")
                except InvalidVersion:
                    return None

            m_hash = self.HASH_SUFFIX_VERSION_RX.match(trimmed)
            if m_hash:
                try:
                    return parse_version(f"{m_hash.group(1)}+{m_hash.group(2)}")
                except InvalidVersion:
                    return None

        return None

    def get_release_tuple(self, version: Optional[str]) -> Optional[Tuple[int, ...]]:
        """
        Extract numeric release components from a version string.

        Parses a version like "v1.2.3", "1.2.3+meta", or "1.2.3.<hash>" and returns the leading numeric components as a tuple of integers. Prefers a normalized PEP 440 release tuple when available; otherwise uses the numeric base found directly in the string. Returns None for empty, None, or unparsable inputs.

        Parameters:
            version (Optional[str]): Version string that may include a leading "v" and additional metadata.

        Returns:
            Optional[Tuple[int, ...]]: Tuple of integer release components (e.g., (1, 2, 3)) when determinable, or `None` if no numeric release can be parsed.
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
        match = self.HASH_SUFFIX_VERSION_RX.match(base) or self.VERSION_BASE_RX.match(
            base
        )
        base_tuple = (
            tuple(int(part) for part in match.group(1).split(".")) if match else None
        )

        normalized = self.normalize_version(version_stripped)
        normalized_tuple = (
            normalized.release
            if isinstance(normalized, Version) and normalized.release
            else None
        )

        if base_tuple and normalized_tuple:
            return (
                base_tuple
                if len(base_tuple) > len(normalized_tuple)
                else normalized_tuple
            )
        return base_tuple or normalized_tuple

    def compare_versions(self, version1: str, version2: str) -> int:
        """
        Compare two version strings using PEP 440 semantics when possible.

        This function attempts to normalize and parse inputs as PEP 440 versions
        and, if both parse, compares them according to PEP 440 rules. If one or
        both inputs cannot be parsed as PEP 440, a conservative natural-sort
        fallback is used.

        Args:
            version1: First version string to compare
            version2: Second version string to compare

        Returns:
            int: 1 if version1 > version2, 0 if equal, -1 if version1 < version2
        """
        v1 = self.normalize_version(version1)
        v2 = self.normalize_version(version2)
        if v1 is not None and v2 is not None:
            if v1 > v2:
                return 1
            elif v1 < v2:
                return -1
            else:
                return 0

        # Natural comparison fallback for truly non-standard versions
        def _nat_key(s: str) -> List[Tuple[int, Union[int, str]]]:
            """
            Compute a natural-sort key by splitting a string into consecutive numeric and alphabetic runs.

            Parameters:
                s (str): Input string to convert into a natural-sort key.

            Returns:
                List[Tuple[int, Union[int, str]]]: A list of tuples representing consecutive runs; numeric runs are returned as `(1, int_value)` and alphabetic runs as `(0, lowercase_string)`.
            """
            parts = re.findall(r"\d+|[A-Za-z]+", s.lower())
            return [(1, int(p)) if p.isdigit() else (0, p) for p in parts]

        k1, k2 = _nat_key(version1), _nat_key(version2)

        if k1 > k2:
            return 1
        elif k1 < k2:
            return -1
        return 0

    def ensure_v_prefix_if_missing(self, version: Optional[str]) -> Optional[str]:
        """
        Ensure the version string starts with a leading "v".

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

    def extract_clean_version(self, version_with_hash: Optional[str]) -> Optional[str]:
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
        parts = version_part.split(".")
        if len(parts) >= 3:
            # Take first 3 parts as version (major.minor.patch)
            clean_version = ".".join(parts[:3])
            return f"v{clean_version}"

        # If it doesn't look like version+hash, return as-is with v prefix
        return self.ensure_v_prefix_if_missing(version_with_hash)

    def calculate_expected_prerelease_version(
        self, release_version: str
    ) -> Optional[str]:
        """
        Compute the next base version for prereleases by incrementing the patch component of a release version.

        Parameters:
            release_version (str): Release version string (e.g., "v1.2.3" or "1.2.3"); leading "v" is allowed.

        Returns:
            Optional[str]: The next base version for prereleases with the patch bumped (e.g., "1.2.4"), or `None` if a next version cannot be determined.
        """
        if not release_version:
            return None

        normalized_version = (
            self.extract_clean_version(release_version) or release_version
        )
        clean_version = normalized_version.lstrip("vV")

        parts = clean_version.split(".")
        if len(parts) >= 2:
            try:
                version = parse_version(clean_version)
                if version.release and len(version.release) >= 2:
                    major = version.release[0]
                    minor = version.release[1]
                    patch = version.release[2] if len(version.release) > 2 else 0
                    return f"{major}.{minor}.{patch + 1}"
            except (InvalidVersion, IndexError):
                pass

            try:
                major, minor = int(parts[0]), int(parts[1])
                patch_int = int(parts[2]) if len(parts) > 2 else 0
                return f"{major}.{minor}.{patch_int + 1}"
            except (ValueError, IndexError):
                pass

        return None

    def parse_commit_history_for_prerelease_version(
        self, commit_history: List[str], base_version: str
    ) -> Optional[str]:
        """
        Infer the expected prerelease version from a list of commit messages using the provided base version.

        Searches commit messages for explicit prerelease suffixes and returns the first match. Commits matching the prerelease-add pattern are checked first; if none are found the function looks for any version-like suffix adjacent to the base version (ignoring common non-version tokens). If no suffix is discovered, falls back to calculating the next patch-based prerelease version.

        Parameters:
            commit_history (List[str]): Commit message strings to inspect.
            base_version (str): Base version to use as the prefix (may include a leading 'v').

        Returns:
            Optional[str]: A string combining the cleaned base version and the inferred suffix (suffix is lowercased), or `None` if `commit_history` or `base_version` is empty.
        """
        if not commit_history or not base_version:
            return None

        # Clean base version by removing 'v' prefix if present
        clean_base = base_version.lstrip("v")

        # Look for ADD pattern commits first (highest priority)
        for commit in commit_history:
            if self._PRERELEASE_ADD_RX.search(commit):
                # Extract version from ADD commit
                add_match = re.search(
                    rf"{re.escape(clean_base)}\.(\w+)", commit, re.IGNORECASE
                )
                if add_match:
                    return f"{clean_base}.{add_match.group(1).lower()}"

        # Look for any version-like pattern in commits
        version_pattern = re.compile(
            rf"{re.escape(clean_base)}[.-](\w+)", re.IGNORECASE
        )
        for commit in commit_history:
            match = version_pattern.search(commit)
            if match:
                suffix = match.group(1).lower()
                # Filter out common non-version suffixes
                if not any(x in suffix for x in ["merge", "pull", "branch", "tag"]):
                    return f"{clean_base}.{suffix}"

        # If no explicit match, fall back to incremented patch
        return self.calculate_expected_prerelease_version(base_version)

    def summarize_rate_limit(self, response: Any) -> Optional[Dict[str, Any]]:
        """
        Extract rate-limit information from an HTTP response for reporting.

        Returns:
            dict: Mapping with keys:
                - remaining (int): number of remaining requests.
                - reset (int): UNIX epoch second when the rate limit resets.
                - limit (int or None): total request limit, or `None` if not present.
            `None` if the response does not expose the expected headers or values cannot be parsed.
        """
        try:
            headers = response.headers
            remaining = headers.get("X-RateLimit-Remaining")
            reset = headers.get("X-RateLimit-Reset")
            limit = headers.get("X-RateLimit-Limit")
            if remaining and reset:
                return {
                    "remaining": int(remaining),
                    "reset": int(reset),
                    "limit": int(limit) if limit else None,
                }
        except (AttributeError, TypeError, ValueError):
            return None
        return None

    def get_commit_hash_suffix(self, commit_hash: str) -> str:
        """
        Produce a short, alphanumeric commit-hash suffix suitable for version identifiers.

        Keeps up to the first seven characters of the provided commit identifier and removes any non-alphanumeric characters.

        Parameters:
            commit_hash (str): Full commit hash or identifier; may be empty or contain non-alphanumeric characters.

        Returns:
            str: Short alphanumeric hash suitable as a version suffix, or an empty string if `commit_hash` is falsy.
        """
        if not commit_hash:
            return ""

        # Use short hash (first 7 characters) for version suffix
        short_hash = commit_hash[:7] if len(commit_hash) >= 7 else commit_hash

        # Clean hash (remove non-alphanumeric characters)
        clean_hash = re.sub(r"[^a-zA-Z0-9]", "", short_hash)

        return clean_hash

    def create_prerelease_version_with_hash(
        self, base_version: str, commit_hash: str, prerelease_type: str = "rc"
    ) -> str:
        """
        Builds a prerelease version string from a base version and a commit hash suffix.

        Parameters:
            base_version (str): Base version (e.g., "1.2.3"); returns an empty string if falsy.
            commit_hash (str): Full commit hash used to generate the short suffix; may be empty.
            prerelease_type (str): Prerelease identifier to use (default: "rc").

        Returns:
            str: Prerelease version like "1.2.3-rc1+abcdef0" or "1.2.3-rc1" if no hash is provided; empty string if base_version is falsy.
        """
        if not base_version:
            return ""

        # Get clean base version without leading 'v'
        clean_base = base_version.lstrip("vV")

        # Get commit hash suffix
        hash_suffix = self.get_commit_hash_suffix(commit_hash)
        suffix = f"{prerelease_type}1"
        if hash_suffix:
            return f"{clean_base}-{suffix}+{hash_suffix}"
        return f"{clean_base}-{suffix}"

    def is_prerelease_version(self, version: str) -> bool:
        """
        Determine whether a version string denotes a prerelease.

        Uses PEP 440 parsing when possible; if parsing is not available, falls back to common prerelease indicators (rc, alpha, beta, dev) and short commit-hash patterns.

        Parameters:
            version (str): Version string to evaluate.

        Returns:
            `true` if the version denotes a prerelease, `false` otherwise.
        """
        if not version:
            return False

        # First try to use parsed Version.pre signal for accuracy
        normalized = self.normalize_version(version)
        if isinstance(normalized, Version) and normalized.pre is not None:
            return True

        # Fallback to substring matching for non-standard versions or dev releases
        prerelease_indicators = [
            "-rc",
            "-alpha",
            "-beta",
            "-dev",
            "rc",
            "alpha",
            "beta",
            "dev",
        ]

        version_lower = version.lower()

        # Check for explicit prerelease indicators with word boundaries
        for indicator in prerelease_indicators:
            # Use word boundaries to avoid matching within words
            if re.search(rf"\b{re.escape(indicator.lstrip('-'))}\b", version_lower):
                return True

        # Check for commit hash patterns (indicates prerelease)
        if re.search(r"[a-f0-9]{6,}", version_lower):
            return True

        return False

    def get_prerelease_metadata_from_version(self, version: str) -> Dict[str, Any]:
        """
        Extract prerelease metadata from a version string.

        Parameters:
            version (str): Version text to analyze; may include PEP 440 prerelease/local segments or commit-hash suffixes.

        Returns:
            dict: Metadata containing:
                - original_version (str): The input version string or empty string if falsy.
                - is_prerelease (bool): `true` if the version denotes a prerelease, `false` otherwise.
                - base_version (str): Numeric base version like "1.2.3" when available, otherwise an empty string.
                - prerelease_type (str): Prerelease identifier such as "rc", "a", "b", or "dev", or empty string if none.
                - prerelease_number (str): Numeric or string component following the prerelease type, or empty string if none.
                - commit_hash (str): Local/commit-hash portion extracted from the version when present, or empty string if none.
        """
        metadata = {
            "original_version": version if version else "",
            "is_prerelease": False,
            "base_version": "",
            "prerelease_type": "",
            "prerelease_number": "",
            "commit_hash": "",
        }

        if not version:
            return metadata

        # Check if it's a prerelease
        if not self.is_prerelease_version(version):
            return metadata

        metadata["is_prerelease"] = True

        normalized = self.normalize_version(version)
        if isinstance(normalized, Version):
            # Extract base version
            if normalized.release:
                metadata["base_version"] = ".".join(
                    str(part) for part in normalized.release
                )

            # Extract prerelease information
            if normalized.pre:
                pre_parts = [str(part) for part in normalized.pre]
                if pre_parts:
                    metadata["prerelease_type"] = pre_parts[0]
                    if len(pre_parts) > 1:
                        metadata["prerelease_number"] = pre_parts[1]

            # Extract local version (commit hash)
            if normalized.local:
                # Join local parts without dots to get the full hash
                metadata["commit_hash"] = "".join(
                    str(part) for part in normalized.local
                )

        return metadata

    def filter_prereleases_by_pattern(
        self,
        prereleases: List[str],
        include_patterns: List[str],
        exclude_patterns: List[str],
    ) -> List[str]:
        """
        Filter prerelease versions by case-insensitive substring include/exclude patterns.

        Parameters:
            prereleases (List[str]): Candidate prerelease version strings.
            include_patterns (List[str]): Substring patterns; if empty, all prereleases are considered included.
            exclude_patterns (List[str]): Substring patterns; any prerelease containing an exclude pattern is omitted.

        Returns:
            List[str]: Prereleases that match at least one include pattern (or all when include_patterns is empty) and do not match any exclude pattern. Matching is case-insensitive and uses simple substring containment.
        """
        filtered = []

        for prerelease in prereleases:
            version_lower = prerelease.lower()

            # Check include patterns
            include_match = False
            if not include_patterns:
                include_match = True
            else:
                for pattern in include_patterns:
                    if pattern.lower() in version_lower:
                        include_match = True
                        break

            # Check exclude patterns
            exclude_match = False
            for pattern in exclude_patterns:
                if pattern.lower() in version_lower:
                    exclude_match = True
                    break

            if include_match and not exclude_match:
                filtered.append(prerelease)

        return filtered

    def create_version_tracking_json(
        self,
        version: str,
        release_type: str,
        timestamp: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
        include_latest_key: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a version-tracking JSON object compatible with the legacy tracking format.

        Parameters:
            version (str): Version string to record (e.g., "v1.2.3").
            release_type (str): Release category used to infer legacy fields (e.g., "android-prerelease", "firmware-release").
            timestamp (Optional[str]): ISO-8601 timestamp to store; if None, the current UTC time is used.
            additional_data (Optional[Dict[str, Any]]): Extra key/value pairs merged into the resulting JSON.
            include_latest_key (bool): If True, include legacy keys "latest_version" and "last_updated" mirroring the version and timestamp.

        Returns:
            Dict[str, Any]: A dictionary containing version, type, timestamp, source, and legacy compatibility fields such as
            "latest_version", "last_updated", and "file_type" when applicable.
        """
        tracking_data = {
            "version": version,
            "type": release_type,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "source": "fetchtastic-downloader",
        }

        # Legacy compatibility fields
        if include_latest_key:
            tracking_data["latest_version"] = version
            tracking_data["last_updated"] = tracking_data["timestamp"]

        # Add file_type for legacy compatibility
        if "android" in release_type.lower():
            tracking_data["file_type"] = "android"
        elif "firmware" in release_type.lower():
            tracking_data["file_type"] = "firmware"

        if additional_data:
            tracking_data.update(additional_data)

        return tracking_data

    def write_version_tracking_file(
        self,
        file_path: str,
        version: str,
        release_type: str,
        cache_manager: Any,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Write version-tracking JSON (including timestamp and metadata) to the given path using an atomic write.

        Parameters:
            file_path (str): Destination file path for the tracking JSON.
            version (str): Version string to record.
            release_type (str): Release type used to derive file metadata.
            cache_manager (CacheManager): Cache manager providing `atomic_write_json`.
            additional_data (Optional[Dict[str, Any]]): Optional data to merge into the tracking JSON.

        Returns:
            bool: `True` if the file was written successfully, `False` otherwise.
        """
        tracking_data = self.create_version_tracking_json(
            version, release_type, additional_data=additional_data
        )

        return cache_manager.atomic_write_json(file_path, tracking_data)

    def read_version_tracking_file(
        self,
        file_path: str,
        cache_manager: Any,
        backward_compatible_keys: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Read a version tracking JSON file and apply legacy key mappings for backward compatibility.

        Parameters:
            file_path (str): Path to the tracking JSON file.
            cache_manager: Object providing `read_json_with_backward_compatibility` to read the file.
            backward_compatible_keys (Optional[Dict[str, str]]): Mapping from new keys to legacy keys to accept;
                defaults to {"version": "latest_version", "last_updated": "timestamp"}.

        Returns:
            Optional[Dict[str, Any]]: Parsed tracking data with keys normalized according to `backward_compatible_keys`,
            or `None` if the file does not exist or cannot be read.
        """
        return cache_manager.read_json_with_backward_compatibility(
            file_path,
            backward_compatible_keys
            or {
                "version": "latest_version",
                "last_updated": "timestamp",
            },
        )

    def migrate_legacy_version_tracking(
        self,
        legacy_file_path: str,
        new_file_path: str,
        legacy_to_new_mapping: Dict[str, str],
        cache_manager: Any,
    ) -> bool:
        """
        Migrate a legacy version-tracking JSON file to a new format at the specified destination.

        Parameters:
            legacy_file_path (str): Path to the existing legacy tracking file.
            new_file_path (str): Destination path for the migrated tracking file.
            legacy_to_new_mapping (Dict[str, str]): Mapping from legacy keys to their new key names.

        Returns:
            bool: `True` if migration succeeded, `False` otherwise.
        """
        return cache_manager.migrate_legacy_cache_file(
            legacy_file_path, new_file_path, legacy_to_new_mapping
        )

    def validate_version_tracking_data(
        self, tracking_data: Dict[str, Any], required_keys: List[str]
    ) -> bool:
        """
        Validate that a version-tracking mapping contains all required keys.

        Parameters:
            tracking_data (Dict[str, Any]): Mapping representing version tracking data to inspect.
            required_keys (List[str]): Keys that must be present in tracking_data.

        Returns:
            bool: `True` if every key in `required_keys` exists in `tracking_data`, `False` otherwise.
        """
        for key in required_keys:
            if key not in tracking_data:
                return False
        return True

    def get_latest_version_from_tracking_files(
        self, tracking_files: List[str], cache_manager: Any
    ) -> Optional[str]:
        """
        Selects the most recent version recorded in the provided tracking files.

        Reads each tracking file via the provided cache manager, considers only entries that contain a "version" key, and compares version strings using the manager's comparison rules to determine the latest version. If a file's version string does not match the manager's version-validation pattern it is still compared but a debug message is emitted.

        Parameters:
            tracking_files (List[str]): Paths to tracking JSON files to examine.
            cache_manager (Any): Cache/IO helper used to read tracking files.

        Returns:
            The latest version string found across the provided tracking files, or None if no valid version was found.
        """
        latest_version = None

        for file_path in tracking_files:
            tracking_data = self.read_version_tracking_file(file_path, cache_manager)
            if tracking_data and self.validate_version_tracking_data(
                tracking_data, ["version"]
            ):
                current_version = tracking_data["version"]
                cleaned_version = str(current_version).lstrip("vV")
                if not self.VERSION_VALIDATION_RX.fullmatch(cleaned_version):
                    logger.debug(
                        "Version string %s does not match expected pattern; comparing anyway.",
                        current_version,
                    )
                if (
                    latest_version is None
                    or self.compare_versions(current_version, latest_version) > 0
                ):
                    latest_version = current_version

        return latest_version

    def create_prerelease_tracking_json(
        self,
        current_release: str,
        commits: List[str],
        expected_version: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build a prerelease tracking JSON object in the legacy format.

        Parameters:
            current_release (str): Stable release version recorded as the tracked release.
            commits (List[str]): Ordered commit identifiers associated with the prerelease.
            expected_version (Optional[str]): Optional expected prerelease version to include.
            additional_data (Optional[Dict[str, Any]]): Optional extra keys to merge into the tracking data.

        Returns:
            Dict[str, Any]: Tracking dictionary containing `version`, `commits`, `timestamp`, and `source`; includes legacy keys `latest_version` and `last_updated`, and includes `hash` when a hex commit hash can be derived from the first commit.
        """
        tracking_data = {
            "version": current_release,
            "commits": commits,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "fetchtastic-downloader",
        }

        if expected_version:
            tracking_data["expected_version"] = expected_version

        # Add legacy compatibility fields
        tracking_data["latest_version"] = current_release
        tracking_data["last_updated"] = tracking_data["timestamp"]

        # Add commit hash if available
        if commits:
            # Extract hash from first commit (legacy behavior)
            first_commit = commits[0] if commits else ""
            hash_match = re.search(r"[a-f0-9]{6,40}$", first_commit.lower())
            if hash_match:
                tracking_data["hash"] = hash_match.group(0)

        if additional_data:
            tracking_data.update(additional_data)

        return tracking_data

    def parse_legacy_prerelease_tracking(
        self, tracking_data: Dict[str, Any]
    ) -> Tuple[List[str], Optional[str], Optional[str]]:
        """
        Parse legacy prerelease tracking data and return normalized components.

        Parameters:
            tracking_data (Dict[str, Any]): Legacy tracking dictionary; may contain keys like
                "version" or "latest_version" (release tag), "commits" (list of commit identifiers),
                "hash" (single commit hash), and "last_updated" or "timestamp".

        Returns:
            Tuple[List[str], Optional[str], Optional[str]]:
                - commits: Ordered list of normalized, lowercase commit identifiers with duplicates removed.
                - current_release: Release tag prefixed with 'v' when present, otherwise None.
                - last_updated: Timestamp string from "last_updated" or "timestamp", or None if absent.
        """
        version = tracking_data.get("version") or tracking_data.get("latest_version")
        hash_val = tracking_data.get("hash")
        current_release = self.ensure_v_prefix_if_missing(version)

        commits_raw = tracking_data.get("commits")
        if commits_raw is None and hash_val:
            expected = self.calculate_expected_prerelease_version(current_release or "")
            commits_raw = [f"{expected}.{hash_val}"] if expected else [hash_val]

        if not isinstance(commits_raw, list):
            logger.warning(
                "Invalid commits format in tracking file: expected list, got %s. Resetting commits.",
                type(commits_raw).__name__,
            )
            commits_raw = []

        commits: List[str] = []
        for commit in commits_raw:
            if isinstance(commit, str):
                normalized = _normalize_commit_identifier(
                    commit.lower(), current_release
                )
                if normalized:
                    commits.append(normalized)
            else:
                logger.warning(
                    "Invalid commit entry in tracking file: expected str, got %s. Skipping.",
                    type(commit).__name__,
                )

        commits = list(dict.fromkeys(commits))
        last_updated = tracking_data.get("last_updated") or tracking_data.get(
            "timestamp"
        )
        return commits, current_release, last_updated

    def should_cleanup_prerelease(
        self,
        commit_identifier: str,
        active_commits: List[str],
        delete_patterns: List[str],
    ) -> bool:
        """
        Decide whether a prerelease identified by commit_identifier should be removed.

        Checks membership against active_commits first; if not active, scans each pattern in delete_patterns for a hexadecimal commit hash (6-40 chars) and returns True if any found hash appears in commit_identifier (case-insensitive).

        Parameters:
            commit_identifier (str): Commit identifier or directory name for the prerelease.
            active_commits (List[str]): Currently active commit identifiers that must not be removed.
            delete_patterns (List[str]): Patterns that may contain a hexadecimal commit hash indicating deletion.

        Returns:
            bool: True if the prerelease should be cleaned up, False otherwise.
        """
        # Check if it's in active commits
        if commit_identifier in active_commits:
            return False

        # Check delete patterns
        for pattern in delete_patterns:
            if self._PRERELEASE_DELETE_RX.search(pattern):
                # Extract hash from pattern and compare
                pattern_hash = re.search(r"[a-f0-9]{6,40}", pattern.lower())
                if pattern_hash and pattern_hash.group(0) in commit_identifier.lower():
                    return True

        return False


# ==============================================================================
# Legacy / Module-Level Compatibility Functions
#
# These are maintained for compatibility with existing tests and other modules
# that import functions directly from this module. New code should use the
# VersionManager class.
# ==============================================================================

_version_manager = VersionManager()


def _normalize_version(version: Optional[str]) -> Optional[Union[Version, Any]]:
    """
    Normalize a version string into a canonical Version-like form.

    Parameters:
        version (Optional[str]): Version string to normalize (may include leading "v", prerelease tags, or hash suffixes); may be None.

    Returns:
        Optional[Union[Version, Any]]: A Version-like object for the normalized version, or `None` if the input cannot be parsed.
    """
    return _version_manager.normalize_version(version)


def _get_release_tuple(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """
    Extracts the numeric release components (major, minor, patch, ...) from a version string.

    Parameters:
        version (Optional[str]): Version string to parse; may include a leading 'v', prerelease tag, or trailing hash.

    Returns:
        Optional[Tuple[int, ...]]: Tuple of integer release components (e.g., (1, 2, 3)) if determinable, otherwise `None`.
    """
    return _version_manager.get_release_tuple(version)


def _ensure_v_prefix_if_missing(version: Optional[str]) -> Optional[str]:
    """
    Ensure a version string begins with a leading "v" if a version is provided.

    Returns:
        The input string prefixed with "v" if it was not None and did not already start with "v" (preserving other content), or `None` if the input was `None`.
    """
    return _version_manager.ensure_v_prefix_if_missing(version)


def _extract_clean_version(version_with_hash: Optional[str]) -> Optional[str]:
    """
    Produce a cleaned version string by removing trailing commit-hash suffixes and ensuring a leading 'v' when appropriate.

    Parameters:
        version_with_hash (Optional[str]): A version identifier that may include a leading "v"/"V" and an appended commit-hash (e.g., "v1.2.3.abcdef" or "1.2.3-abcdef").

    Returns:
        Optional[str]: A cleaned version string prefixed with "v" and containing the numeric base (e.g., "v1.2.3") when a base version can be determined; if the input is None returns None; if no numeric base is found returns the input normalized with a "v" prefix.
    """
    return _version_manager.extract_clean_version(version_with_hash)


def _normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    # This was a standalone helper, might need to be in VersionManager or kept here
    """
    Normalize a commit identifier into a version-like identifier when appropriate.

    Parameters:
        commit_id (str): Commit identifier or candidate string; may be a version+hash, a hex commit hash, or other text.
        release_version (Optional[str]): Release version to use as the base when `commit_id` is a pure hex hash; ignored otherwise.

    Returns:
        str: Lowercased normalized identifier. If `commit_id` already has the form "<major>.<minor>.<patch>.<hash>" it is returned unchanged (lowercased). If `commit_id` is a hex hash and `release_version` yields a clean base (e.g., "v1.2.3"), returns "<major>.<minor>.<patch>.<hash>" with no leading "v". Otherwise returns the lowercased `commit_id`.
    """
    commit_id = (commit_id or "").lower()

    if re.search(r"^\d+\.\d+\.\d+\.[a-f0-9]{6,40}$", commit_id):
        return commit_id

    if re.match(r"^[a-f0-9]{6,40}$", commit_id):
        if release_version:
            clean_version = _version_manager.extract_clean_version(release_version)
            if clean_version:
                version_without_v = clean_version.lstrip("v")
                return f"{version_without_v}.{commit_id}"
        return commit_id

    return commit_id


def _parse_new_json_format(
    tracking_data: Dict[str, Any],
) -> Tuple[List[str], Optional[str], Optional[str]]:
    """
    Parse prerelease tracking JSON in the new format and return its normalized components.

    Parses a prerelease tracking JSON object and extracts:
    - commits: normalized list of commit identifiers,
    - current_release: normalized current prerelease version prefixed with "v" when present, or None,
    - last_updated: ISO 8601 timestamp string when the tracking was last updated, or None.

    Parameters:
        tracking_data (Dict[str, Any]): Parsed prerelease tracking JSON object.

    Returns:
        Tuple[List[str], Optional[str], Optional[str]]: (commits, current_release, last_updated)
    """
    return _version_manager.parse_legacy_prerelease_tracking(tracking_data)


def _read_prerelease_tracking_data(
    tracking_file: str,
) -> tuple[list[str], Optional[str], Optional[str]]:
    # This function is used by tests
    """
    Read a prerelease tracking JSON file and extract the commits list, current prerelease tag, and last-updated timestamp.

    Parameters:
        tracking_file (str): Path to the prerelease tracking JSON file.

    Returns:
        tuple[list[str], Optional[str], Optional[str]]: A 3-tuple (commits, current_release, last_updated) where
            commits is a list of commit identifiers (empty if none, missing, or on error),
            current_release is the current prerelease version tag (or `None` if not present or on error),
            last_updated is the ISO timestamp string from the file (or `None` if not present or on error).
    """
    commits: List[str] = []
    current_release: Optional[str] = None
    last_updated: Optional[str] = None

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

            if "version" in tracking_data and (
                "hash" in tracking_data or "commits" in tracking_data
            ):
                commits, current_release, last_updated = _parse_new_json_format(
                    tracking_data
                )
            else:
                logger.warning(
                    "Unexpected prerelease tracking format in %s; ignoring file.",
                    tracking_file,
                )
        except (IOError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Could not read prerelease tracking file: %s", e)

    return commits, current_release, last_updated


def _read_latest_release_tag(json_file: str) -> Optional[str]:
    """
    Extracts the 'latest_version' value from a JSON file if present and valid.

    Parameters:
        json_file (str): Path to a JSON file expected to contain a top-level "latest_version" key.

    Returns:
        str: The trimmed `"latest_version"` string when present and non-empty, `None` if the file is missing, unreadable, malformed, or the key is absent/empty.
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
        except (IOError, json.JSONDecodeError):
            return None
    return None


def _write_latest_release_tag(
    json_file: str, version_tag: str, release_type: str
) -> bool:
    """
    Write a small JSON file recording the latest release tag, file type, and update timestamp.

    Parameters:
        json_file (str): Path to the JSON file to write.
        version_tag (str): Version string to record under `latest_version`.
        release_type (str): Human-readable release type; used to derive the `file_type` slug
            (contains "android" -> "android", contains "firmware" -> "firmware", otherwise
            whitespace is replaced with underscores).

    Returns:
        success (bool): `True` if the file was written successfully, `False` otherwise.
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


def _get_json_release_basename(release_type: str) -> str:
    """
    Selects the JSON filename basename that corresponds to a human-readable release type.

    Parameters:
        release_type (str): Human-readable release type; matching is case-insensitive and checks for the substrings
            "firmware prerelease", "firmware", "android apk prerelease", and "android" (in that order of precedence).

    Returns:
        str: The filename basename to use for the given release type. Returns "latest_release.json" when no known
        release-type substring is present.
    """
    release_type_lower = release_type.lower()
    if "firmware prerelease" in release_type_lower:
        return LATEST_FIRMWARE_PRERELEASE_JSON_FILE
    if "firmware" in release_type_lower:
        return LATEST_FIRMWARE_RELEASE_JSON_FILE
    if "android apk prerelease" in release_type_lower:
        return LATEST_ANDROID_PRERELEASE_JSON_FILE
    if "android" in release_type_lower:
        return LATEST_ANDROID_RELEASE_JSON_FILE
    return "latest_release.json"


def extract_version(dir_name: str) -> str:
    """
    Strip the firmware directory prefix from a directory name to obtain the version portion.

    Parameters:
        dir_name (str): Directory name that may start with the firmware prefix (FIRMWARE_DIR_PREFIX).

    Returns:
        str: The directory name with the firmware prefix removed.
    """
    return dir_name.removeprefix(FIRMWARE_DIR_PREFIX)


def _get_commit_hash_from_dir(dir_name: str) -> Optional[str]:
    """
    Extracts a commit hash from a directory name that contains a version plus an embedded hash suffix.

    Parameters:
        dir_name (str): Directory name or path segment that may include a version and a trailing commit hash (e.g., "v1.2.3-abcdef1" or "firmware_v1.2.3.abcdef1").

    Returns:
        Optional[str]: The extracted commit hash in lowercase (6-40 hex characters) if present, otherwise `None`.
    """
    version_part = extract_version(dir_name)
    commit_match = re.search(
        r"[.-]([a-f0-9]{6,40})(?:[.-]|$)", version_part, re.IGNORECASE
    )
    if commit_match:
        return commit_match.group(1).lower()
    return None


def calculate_expected_prerelease_version(latest_version: str) -> Optional[str]:
    """
    Derives the expected next base version for prereleases from a latest release version.

    Parameters:
        latest_version (str): The latest release version tag (for example "1.2.3" or "v1.2.3").

    Returns:
        expected_base_version (Optional[str]): The computed base version for prereleases (for example "1.2.4"); returns None if an expected version cannot be determined.
    """
    return _version_manager.calculate_expected_prerelease_version(latest_version)


def is_prerelease_directory(dir_name: str) -> bool:
    """
    Determine if the directory name represents a prerelease version.

    Returns:
        `true` if the directory name contains common prerelease indicators ("alpha", "beta", "dev", "rc") or a hexadecimal commit hash, `false` otherwise.
    """
    version_part = extract_version(dir_name).lower()

    # Check for prerelease indicators
    prerelease_patterns = [
        r".*alpha.*",
        r".*beta.*",
        r".*dev.*",
        r".*rc.*",  # Release Candidate
        r".*[a-f0-9]{6,40}.*",  # Contains commit hash
    ]

    return any(re.search(pattern, version_part) for pattern in prerelease_patterns)


def normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    """
    Normalize a commit identifier into a version+hash form for consistent tracking.

    Parameters:
        commit_id (str): Commit identifier or directory/version string to normalize.
        release_version (Optional[str]): Optional base release version to use when deriving a version portion.

    Returns:
        str: A normalized identifier in "version+hash" form when determinable, otherwise the original input.
    """
    return _normalize_commit_identifier(commit_id, release_version)
