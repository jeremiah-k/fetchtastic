"""
Version Management for Fetchtastic Download Subsystem

This module provides version parsing, comparison, and tracking utilities
that are used across all downloaders for consistent version handling.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

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
            if version.release:
                major, minor, patch = (
                    version.release[0],
                    version.release[1],
                    version.release[2],
                )
                return f"{major}.{minor}.{patch + 1}"
        except (InvalidVersion, IndexError):
            pass

        # Fallback: simple string manipulation
        if "." in clean_version:
            parts = clean_version.split(".")
            if len(parts) >= 3:
                try:
                    major, minor, patch = parts[0], parts[1], int(parts[2])
                    return f"{major}.{minor}.{patch + 1}"
                except ValueError:
                    pass

        # If we can't parse it, just return empty string
        return ""

    def create_version_tracking_json(
        self,
        version: str,
        release_type: str,
        timestamp: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a version tracking JSON structure matching legacy format.

        Args:
            version: Version string to track
            release_type: Type of release (e.g., 'latest', 'prerelease')
            timestamp: Optional timestamp, uses current time if None
            additional_data: Optional additional data to include

        Returns:
            Dict: Version tracking JSON structure
        """
        tracking_data = {
            "version": version,
            "type": release_type,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "source": "fetchtastic-downloader",
        }

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
            file_path, backward_compatible_keys
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

    def create_prerelease_tracking_data(
        self,
        prerelease_version: str,
        base_version: str,
        expiry_hours: float,
        commit_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create prerelease tracking data structure.

        Args:
            prerelease_version: Full prerelease version string
            base_version: Base version this prerelease is based on
            expiry_hours: Hours until this prerelease tracking expires
            commit_hash: Optional commit hash

        Returns:
            Dict: Prerelease tracking data structure
        """
        expiry_timestamp = (
            datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
        ).isoformat()

        tracking_data = {
            "prerelease_version": prerelease_version,
            "base_version": base_version,
            "expiry_timestamp": expiry_timestamp,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if commit_hash:
            tracking_data["commit_hash"] = commit_hash

        return tracking_data

    def should_cleanup_superseded_prerelease(
        self, current_prerelease: Dict[str, Any], new_prerelease: Dict[str, Any]
    ) -> bool:
        """
        Determine if a current prerelease should be cleaned up as superseded.

        Args:
            current_prerelease: Current prerelease tracking data
            new_prerelease: New prerelease tracking data

        Returns:
            bool: True if current should be cleaned up, False otherwise
        """
        # Check if new prerelease is based on a newer version
        current_base = current_prerelease.get("base_version")
        new_base = new_prerelease.get("base_version")

        if current_base and new_base:
            comparison = self.compare_versions(new_base, current_base)
            if comparison > 0:  # New base version is newer
                return True

        # Check if current prerelease has expired
        expiry_str = current_prerelease.get("expiry_timestamp")
        if expiry_str:
            try:
                expiry_time = datetime.fromisoformat(expiry_str)
                if datetime.now(timezone.utc) > expiry_time:
                    return True
            except ValueError:
                pass

        return False
