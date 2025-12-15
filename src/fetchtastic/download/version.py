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
)
from fetchtastic.log_utils import logger

from .files import _atomic_write_json

# Import for type annotations only (available in older packaging versions)
try:
    from packaging.version import LegacyVersion  # type: ignore
except ImportError:
    LegacyVersion = None  # type: ignore


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
    PRERELEASE_VERSION_RX = re.compile(
        r"^(\d+(?:\.\d+)*)[.-](rc|dev|alpha|beta|b)\.?(\d*)$", re.IGNORECASE
    )
    HASH_SUFFIX_VERSION_RX = re.compile(
        r"^(\d+(?:\.\d+)*)\.([A-Za-z0-9][A-Za-z0-9.-]*)$"
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
        Normalize repository-style version strings into a PEP 440-compatible form.

        Recognizes and strips a leading "v", converts common prerelease markers
        (e.g., "alpha"/"beta" with optional numeric fragment) into PEP 440
        prerelease forms, and converts trailing commit/hash-like suffixes into
        local version identifiers when possible.

        Args:
            version: Raw version string that may include a leading "v",
                   prerelease words, or a hash suffix.

        Returns:
            A parsed Version or LegacyVersion-like object when parsing succeeds;
            None for empty, missing, or unparsable inputs.
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
        Return the numeric release components extracted from a version string.

        Args:
            version: Version string to parse. May include a leading "v" and
                   additional metadata; only the numeric leading segments are considered.

        Returns:
            Tuple of integer release components (e.g., (1, 2, 3)) when a numeric
            release can be determined, or None if the input is empty or no numeric
            release segments can be parsed.
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
        match = self.VERSION_BASE_RX.match(base)
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
            """Produce a natural-sort key by splitting into digit and alphabetic runs."""
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
        Ensure a version string begins with a leading "v".

        Args:
            version: Version string to normalize; leading/trailing whitespace is stripped.
                   If None, no normalization is performed.

        Returns:
            None if version is None; otherwise the input string with a leading "v"
            added if it did not already start with "v" or "V".
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

    def calculate_expected_prerelease_version(self, release_version: str) -> str:
        """
        Calculate the expected prerelease version for a given release.

        This is used to determine what the next prerelease version should be
        based on the current release version.

        Args:
            release_version: Current release version (e.g., "v1.2.3")

        Returns:
            Expected prerelease version (e.g., "1.2.4")
        """
        if not release_version:
            return ""

        # Remove leading 'v'/'V' for processing
        clean_version = release_version.lstrip("vV")

        try:
            # Parse version and increment patch version
            version = parse_version(clean_version)
            if version.release and len(version.release) >= 2:
                major = version.release[0]
                minor = version.release[1]
                patch = version.release[2] if len(version.release) > 2 else 0
                return f"{major}.{minor}.{patch + 1}"
        except (InvalidVersion, IndexError):
            pass

        # Fallback: simple string manipulation
        if "." in clean_version:
            parts = clean_version.split(".")
            if len(parts) >= 3:
                try:
                    fallback_major, fallback_minor, fallback_patch = (
                        parts[0],
                        parts[1],
                        int(parts[2]),
                    )
                    return f"{fallback_major}.{fallback_minor}.{fallback_patch + 1}"
                except ValueError:
                    pass

        # If we can't parse it, just return empty string
        return ""

    def parse_commit_history_for_prerelease_version(
        self, commit_history: List[str], base_version: str
    ) -> Optional[str]:
        """
        Parse commit history to determine expected prerelease version.

        This method analyzes commit messages and history to determine what the
        expected prerelease version should be based on commit patterns.
        Matches legacy behavior for prerelease version detection.

        Expected commit message shapes: base_version followed by dot and suffix
        (e.g., "2.7.13.abc123" for commit hash.)
        The regex may capture unintended tokens if commit formats vary widely.

        Args:
            commit_history: List of commit messages/history entries
            base_version: Base version to use as starting point (without 'v' prefix)

        Returns:
            Optional[str]: Expected prerelease version or None if cannot be determined
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
        """Extract rate-limit info from a GitHub API response for logging/reporting."""
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
        Extract and format commit hash suffix for version strings.

        Args:
            commit_hash: Full commit hash

        Returns:
            str: Formatted commit hash suffix (e.g., short hash)
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
        Create a prerelease version string with commit hash suffix.

        Args:
            base_version: Base version (e.g., "1.2.3")
            commit_hash: Commit hash for suffix
            prerelease_type: Type of prerelease (rc, alpha, beta, etc.)

        Returns:
            str: Full prerelease version with hash suffix
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
        Check if a version string represents a prerelease.

        Args:
            version: Version string to check

        Returns:
            bool: True if version is a prerelease, False otherwise
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

        # Check for explicit prerelease indicators
        if any(indicator in version_lower for indicator in prerelease_indicators):
            return True

        # Check for commit hash patterns (indicates prerelease)
        import re

        if re.search(r"[a-f0-9]{6,}", version_lower):
            return True

        return False

    def get_prerelease_metadata_from_version(self, version: str) -> Dict[str, Any]:
        """
        Extract prerelease metadata from a version string.

        Args:
            version: Version string to parse

        Returns:
            Dict: Prerelease metadata including base version, prerelease type, etc.
        """
        if not version:
            return {}

        metadata = {
            "original_version": version,
            "is_prerelease": False,
            "base_version": "",
            "prerelease_type": "",
            "prerelease_number": "",
            "commit_hash": "",
        }

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
        Filter prereleases based on include/exclude patterns.

        Args:
            prereleases: List of prerelease versions
            include_patterns: Patterns to include
            exclude_patterns: Patterns to exclude

        Returns:
            List[str]: Filtered list of prerelease versions
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
        Create a version tracking JSON structure matching legacy format.

        Args:
            version: Version string to track
            release_type: Type of release (e.g., 'latest', 'prerelease')
            timestamp: Optional timestamp, uses current time if None
            additional_data: Optional additional data to include
            include_latest_key: When True, also include legacy "latest_version" key

        Returns:
            Dict: Version tracking JSON structure
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
        Write a version tracking file with atomic write and timestamp.

        Args:
            file_path: Path to write the tracking file
            version: Version string to track
            release_type: Type of release
            cache_manager: CacheManager instance for atomic writes
            additional_data: Optional additional data

        Returns:
            bool: True if write succeeded, False otherwise
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
        Read a version tracking file with backward compatibility.

        Args:
            file_path: Path to the tracking file
            cache_manager: CacheManager instance for reading
            backward_compatible_keys: Optional legacy key mapping

        Returns:
            Optional[Dict]: Version tracking data or None if file doesn't exist
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
        Migrate legacy version tracking file to new format.

        Args:
            legacy_file_path: Path to legacy tracking file
            new_file_path: Path for new tracking file
            legacy_to_new_mapping: Mapping of legacy keys to new keys
            cache_manager: CacheManager instance for migration

        Returns:
            bool: True if migration succeeded, False otherwise
        """
        return cache_manager.migrate_legacy_cache_file(
            legacy_file_path, new_file_path, legacy_to_new_mapping
        )

    def validate_version_tracking_data(
        self, tracking_data: Dict[str, Any], required_keys: List[str]
    ) -> bool:
        """
        Validate version tracking data structure.

        Args:
            tracking_data: Version tracking data to validate
            required_keys: List of required keys

        Returns:
            bool: True if data is valid, False otherwise
        """
        for key in required_keys:
            if key not in tracking_data:
                return False
        return True

    def get_latest_version_from_tracking_files(
        self, tracking_files: List[str], cache_manager: Any
    ) -> Optional[str]:
        """
        Get the latest version from multiple tracking files.

        Args:
            tracking_files: List of tracking file paths
            cache_manager: CacheManager instance for reading

        Returns:
            Optional[str]: Latest version found, or None if no valid tracking files
        """
        latest_version = None

        for file_path in tracking_files:
            tracking_data = self.read_version_tracking_file(file_path, cache_manager)
            if tracking_data and self.validate_version_tracking_data(
                tracking_data, ["version"]
            ):
                current_version = tracking_data["version"]
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
        Create prerelease tracking JSON matching legacy format.

        Args:
            current_release: Current stable release version
            commits: List of commit identifiers
            expected_version: Expected prerelease version
            additional_data: Optional additional data

        Returns:
            Dict: Prerelease tracking JSON structure
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
        Parse legacy prerelease tracking data format.

        Args:
            tracking_data: Legacy tracking data dictionary

        Returns:
            Tuple containing:
            - List of commit identifiers
            - Current release version
            - Last updated timestamp
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
        Determine if a prerelease should be cleaned up based on legacy logic.

        Args:
            commit_identifier: Commit identifier to check
            active_commits: List of currently active commits
            delete_patterns: Patterns that indicate deletion

        Returns:
            bool: True if prerelease should be cleaned up
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
    return _version_manager.normalize_version(version)


def _get_release_tuple(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    return _version_manager.get_release_tuple(version)


def _ensure_v_prefix_if_missing(version: Optional[str]) -> Optional[str]:
    return _version_manager.ensure_v_prefix_if_missing(version)


def _extract_clean_version(version_with_hash: Optional[str]) -> Optional[str]:
    return _version_manager.extract_clean_version(version_with_hash)


def _normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    # This was a standalone helper, might need to be in VersionManager or kept here
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
    """Parse new JSON format using VersionManager for better consistency."""
    return _version_manager.parse_legacy_prerelease_tracking(tracking_data)


def _read_prerelease_tracking_data(tracking_file: str):
    # This function is used by tests
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
    return dir_name.removeprefix(FIRMWARE_DIR_PREFIX)


def _get_commit_hash_from_dir(dir_name: str) -> Optional[str]:
    version_part = extract_version(dir_name)
    commit_match = re.search(
        r"[.-]([a-f0-9]{6,40})(?:[.-]|$)", version_part, re.IGNORECASE
    )
    if commit_match:
        return commit_match.group(1).lower()
    return None


def calculate_expected_prerelease_version(latest_version: str) -> str:
    return _version_manager.calculate_expected_prerelease_version(latest_version)


def is_prerelease_directory(dir_name: str) -> bool:
    """Check if directory name represents a prerelease version."""
    version_part = extract_version(dir_name).lower()

    # Check for prerelease indicators
    prerelease_patterns = [
        r".*alpha.*",
        r".*beta.*",
        r".*dev.*",
        r".*[a-f0-9]{6,40}.*",  # Contains commit hash
    ]

    return any(re.search(pattern, version_part) for pattern in prerelease_patterns)


def normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    """Normalize commit identifier to version+hash format."""
    return _normalize_commit_identifier(commit_id, release_version)
