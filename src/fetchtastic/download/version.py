"""
Version Management for Fetchtastic Download Subsystem

This module provides version parsing, comparison, and tracking utilities
that are used across all downloaders for consistent version handling.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

from fetchtastic.constants import (
    DEFAULT_PRERELEASE_ACTIVE,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    DEFAULT_PRERELEASE_STATUS,
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_BASE,
    GITHUB_API_TIMEOUT,
    GITHUB_MAX_PER_PAGE,
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    PRERELEASE_ADD_COMMIT_PATTERN,
    PRERELEASE_COMMIT_HISTORY_FILE,
    PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
    PRERELEASE_COMMITS_CACHE_FILE,
    PRERELEASE_DELETE_COMMIT_PATTERN,
    PRERELEASE_TRACKING_JSON_FILE,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .files import _atomic_write_json

# Import for type annotations only (available in older packaging versions)
try:
    from packaging.version import LegacyVersion  # type: ignore
except ImportError:
    LegacyVersion = None  # type: ignore


NON_ASCII_RX = re.compile(r"[^\x00-\x7F]+")
PRERELEASE_VERSION_RX = re.compile(
    r"^(\d+(?:\.\d+)*)[.-](rc|dev|alpha|beta|b)\.?(\d*)$", re.IGNORECASE
)
HASH_SUFFIX_VERSION_RX = re.compile(r"^(\d+(?:\.\d+)*)\.([A-Za-z0-9][A-Za-z0-9.-]*)$")
VERSION_BASE_RX = re.compile(r"^(\d+(?:\.\d+)*)")


def _normalize_version(version: Optional[str]) -> Optional[Union[Version, Any]]:
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
                return None

        m_hash = HASH_SUFFIX_VERSION_RX.match(trimmed)
        if m_hash:
            try:
                return parse_version(f"{m_hash.group(1)}+{m_hash.group(2)}")
            except InvalidVersion:
                return None

    return None


def _get_release_tuple(version: Optional[str]) -> Optional[tuple[int, ...]]:
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


def _ensure_v_prefix_if_missing(version: Optional[str]) -> Optional[str]:
    if version is None:
        return None
    version = version.strip()
    if version and not version.lower().startswith("v"):
        return f"v{version}"
    return version


def _extract_clean_version(version_with_hash: Optional[str]) -> Optional[str]:
    if not version_with_hash:
        return None

    version_part = version_with_hash.lstrip("vV")
    parts = version_part.split(".")
    if len(parts) >= 3:
        clean_version = ".".join(parts[:3])
        return f"v{clean_version}"
    return _ensure_v_prefix_if_missing(version_with_hash)


def _normalize_commit_identifier(commit_id: str, release_version: Optional[str]) -> str:
    commit_id = (commit_id or "").lower()

    if re.search(r"^\d+\.\d+\.\d+\.[a-f0-9]{6,40}$", commit_id):
        return commit_id

    if re.match(r"^[a-f0-9]{6,40}$", commit_id):
        if release_version:
            clean_version = _extract_clean_version(release_version)
            if clean_version:
                version_without_v = clean_version.lstrip("v")
                return f"{version_without_v}.{commit_id}"
        return commit_id

    return commit_id


def _parse_new_json_format(
    tracking_data: Dict[str, Any],
) -> tuple[list[str], str | None, str | None]:
    version = tracking_data.get("version")
    hash_val = tracking_data.get("hash")
    current_release = _ensure_v_prefix_if_missing(version)

    commits_raw = tracking_data.get("commits")
    if commits_raw is None and hash_val:
        expected = calculate_expected_prerelease_version(current_release or "")
        commits_raw = [f"{expected}.{hash_val}"] if expected else [hash_val]

    if not isinstance(commits_raw, list):
        logger.warning(
            "Invalid commits format in tracking file: expected list, got %s. Resetting commits.",
            type(commits_raw).__name__,
        )
        commits_raw = []

    commits: list[str] = []
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

    commits = list(dict.fromkeys(commits))
    last_updated = tracking_data.get("last_updated") or tracking_data.get("timestamp")
    return commits, current_release, last_updated


def _read_prerelease_tracking_data(tracking_file: str):
    commits: list[str] = []
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
    latest_tuple = _get_release_tuple(latest_version)
    if not latest_tuple or len(latest_tuple) < 2:
        logger.warning(
            "Could not calculate expected prerelease version from: %s", latest_version
        )
        return ""

    major, minor = latest_tuple[0], latest_tuple[1]
    patch = latest_tuple[2] if len(latest_tuple) > 2 else 0
    expected_patch = patch + 1
    return f"{major}.{minor}.{expected_patch}"


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

    def parse_commit_history_for_prerelease_version(
        self, commit_history: List[str], base_version: str
    ) -> Optional[str]:
        """
        Parse commit history to determine expected prerelease version.

        This method analyzes commit messages and history to determine what the
        expected prerelease version should be based on commit patterns.

        Args:
            commit_history: List of commit messages/history entries
            base_version: Base version to use as starting point

        Returns:
            Optional[str]: Expected prerelease version or None if cannot be determined
        """
        if not commit_history or not base_version:
            return None

        # Extract prerelease-like versions from commits that include the base version
        version_pattern = re.compile(rf"{re.escape(base_version)}\.(\w+)")
        for commit in commit_history:
            match = version_pattern.search(commit)
            if match:
                return f"{base_version}.{match.group(1)}"

        # If no explicit match, fall back to incremented patch
        return self.calculate_expected_prerelease_version(base_version)

    def scan_prerelease_directories(
        self, directories: List[str], expected_version: str
    ) -> List[str]:
        """
        Scan prerelease directories (meshtastic.github.io style) to collect prerelease identifiers.

        Args:
            directories: List of directory names
            expected_version: Base version to match (e.g., 2.7.15)

        Returns:
            List of prerelease identifiers matching the expected version.
        """
        matching = []
        for raw_dir_name in directories:
            if not raw_dir_name.startswith("firmware-"):
                continue
            dir_name = raw_dir_name[len("firmware-") :]
            if not re.match(r"\d+\.\d+\.\d+\.[a-f0-9]{6,}", dir_name, re.IGNORECASE):
                continue
            parts = dir_name.split(".")
            if len(parts) < 4:
                continue
            base_version = ".".join(parts[:3])
            if base_version == expected_version:
                matching.append(dir_name)
        return matching

    def fetch_recent_repo_commits(
        self,
        limit: int,
        *,
        cache_manager: Any,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent commits from meshtastic.github.io with caching and expiry.

        Args:
            limit: Maximum commits to fetch
            cache_manager: CacheManager for reading/writing cache
            force_refresh: Whether to bypass cache

        Returns:
            List of commit dicts
        """
        limit = max(1, int(limit))
        cache_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_COMMITS_CACHE_FILE
        )

        if not force_refresh:
            cached = cache_manager.read_json(cache_file)
            if isinstance(cached, dict):
                cached_at = cached.get("cached_at")
                commits = cached.get("commits")
                if cached_at and isinstance(commits, list):
                    try:
                        cached_at_dt = datetime.fromisoformat(
                            str(cached_at).replace("Z", "+00:00")
                        )
                        age_seconds = (
                            datetime.now(timezone.utc) - cached_at_dt
                        ).total_seconds()
                        if age_seconds < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                            logger.debug("Using cached prerelease commit history")
                            return commits[:limit]
                        logger.debug("Commits cache expired (age: %.1fs)", age_seconds)
                    except ValueError:
                        pass

        logger.debug("Fetching commits from API (cache miss/expired)")

        all_commits: List[Dict[str, Any]] = []
        seen_shas: set[str] = set()
        per_page = min(GITHUB_MAX_PER_PAGE, limit)
        page = 1
        url = f"{GITHUB_API_BASE}/meshtastic/meshtastic.github.io/commits"

        try:
            while len(all_commits) < limit:
                response = make_github_api_request(
                    url,
                    github_token=github_token,
                    allow_env_token=allow_env_token,
                    params={"per_page": per_page, "page": page},
                    timeout=GITHUB_API_TIMEOUT,
                )
                commits_page = response.json()
                if not isinstance(commits_page, list) or not commits_page:
                    break
                for commit in commits_page:
                    sha = commit.get("sha")
                    if sha and sha in seen_shas:
                        continue
                    if sha:
                        seen_shas.add(sha)
                    all_commits.append(commit)
                    if len(all_commits) >= limit:
                        break
                if len(commits_page) < per_page:
                    break
                page += 1

            cache_manager.atomic_write_json(
                cache_file,
                {
                    "commits": all_commits,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return all_commits[:limit]
        except Exception as e:
            logger.warning("Could not fetch repo commits: %s", e)
            return []

    @staticmethod
    def _create_default_prerelease_entry(
        *, directory: str, identifier: str, base_version: str, commit_hash: str
    ) -> Dict[str, Any]:
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
        self,
        entries: Dict[str, Dict[str, Any]],
        *,
        directory: str,
        identifier: str,
        expected_version: str,
        short_hash: str,
        timestamp: Optional[str],
        sha: Optional[str],
    ) -> None:
        entry = entries.setdefault(
            directory,
            self._create_default_prerelease_entry(
                directory=directory,
                identifier=identifier,
                base_version=expected_version,
                commit_hash=short_hash,
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
        self,
        entries: Dict[str, Dict[str, Any]],
        *,
        directory: str,
        identifier: str,
        expected_version: str,
        short_hash: str,
        timestamp: Optional[str],
        sha: Optional[str],
    ) -> None:
        entry = entries.setdefault(
            directory,
            self._create_default_prerelease_entry(
                directory=directory,
                identifier=identifier,
                base_version=expected_version,
                commit_hash=short_hash,
            ),
        )
        if timestamp and not entry.get("removed_at"):
            entry["removed_at"] = timestamp
        if sha and not entry.get("removed_sha"):
            entry["removed_sha"] = sha
        entry["active"] = False
        entry["status"] = "deleted"

    def build_simplified_prerelease_history(
        self, expected_version: str, commits: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], set[str]]:
        """
        Build simplified prerelease history entries from meshtastic.github.io commits.
        """
        entries_by_dir: Dict[str, Dict[str, Any]] = {}
        seen_shas: set[str] = set()

        for commit in commits:
            sha = commit.get("sha")
            if sha:
                seen_shas.add(str(sha))

            timestamp = commit.get("commit", {}).get("committer", {}).get("date")
            message = commit.get("commit", {}).get("message") or ""
            if not isinstance(message, str):
                continue

            for line in message.splitlines():
                line = line.strip()
                if not line:
                    continue

                m_add = self._PRERELEASE_ADD_RX.match(line)
                if m_add:
                    base_version, short_hash = m_add.group(1), m_add.group(2)
                    if base_version != expected_version:
                        continue
                    identifier = f"{base_version}.{short_hash}".lower()
                    directory = f"{FIRMWARE_DIR_PREFIX}{identifier}"
                    self._record_prerelease_addition(
                        entries_by_dir,
                        directory=directory,
                        identifier=identifier,
                        expected_version=expected_version,
                        short_hash=short_hash,
                        timestamp=timestamp,
                        sha=str(sha) if sha else None,
                    )
                    continue

                m_del = self._PRERELEASE_DELETE_RX.match(line)
                if m_del:
                    base_version, short_hash = m_del.group(1), m_del.group(2)
                    if base_version != expected_version:
                        continue
                    identifier = f"{base_version}.{short_hash}".lower()
                    directory = f"{FIRMWARE_DIR_PREFIX}{identifier}"
                    self._record_prerelease_deletion(
                        entries_by_dir,
                        directory=directory,
                        identifier=identifier,
                        expected_version=expected_version,
                        short_hash=short_hash,
                        timestamp=timestamp,
                        sha=str(sha) if sha else None,
                    )

        entries = list(entries_by_dir.values())
        entries.sort(
            key=lambda e: (
                str(e.get("added_at") or ""),
                str(e.get("directory") or ""),
            ),
            reverse=True,
        )
        return entries, seen_shas

    def get_prerelease_commit_history(
        self,
        expected_version: str,
        *,
        cache_manager: Any,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
        force_refresh: bool = False,
        max_commits: int = DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    ) -> List[Dict[str, Any]]:
        """
        Get simplified prerelease history for an expected version (cached).
        """
        history_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_COMMIT_HISTORY_FILE
        )
        now = datetime.now(timezone.utc)

        cache = cache_manager.read_json(history_file)
        if not isinstance(cache, dict):
            cache = {}

        cached_entry = cache.get(expected_version) if not force_refresh else None
        if isinstance(cached_entry, dict) and not force_refresh:
            entries = cached_entry.get("entries")
            last_checked_raw = cached_entry.get("last_checked") or cached_entry.get(
                "cached_at"
            )
            if isinstance(entries, list) and last_checked_raw:
                try:
                    last_checked = datetime.fromisoformat(
                        str(last_checked_raw).replace("Z", "+00:00")
                    )
                    age_s = (now - last_checked).total_seconds()
                    if age_s < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                        logger.debug(
                            "Using cached prerelease history for %s (cached %.0fs ago)",
                            expected_version,
                            age_s,
                        )
                        return entries
                    logger.debug(
                        "Prerelease history cache stale for %s (age %.0fs >= %ss); extending cache",
                        expected_version,
                        age_s,
                        PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
                    )
                except ValueError:
                    pass

        commits = self.fetch_recent_repo_commits(
            max_commits,
            cache_manager=cache_manager,
            github_token=github_token,
            allow_env_token=allow_env_token,
            force_refresh=force_refresh,
        )
        entries, shas = self.build_simplified_prerelease_history(
            expected_version, commits
        )

        cache[expected_version] = {
            "entries": entries,
            "cached_at": now.isoformat(),
            "last_checked": now.isoformat(),
            "shas": sorted(shas),
        }
        cache_manager.atomic_write_json(history_file, cache)
        logger.debug("Saved %d prerelease commit history entries to cache", len(cache))
        return entries

    def get_latest_active_prerelease_from_history(
        self,
        expected_version: str,
        *,
        cache_manager: Any,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
        force_refresh: bool = False,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        entries = self.get_prerelease_commit_history(
            expected_version,
            cache_manager=cache_manager,
            github_token=github_token,
            allow_env_token=allow_env_token,
            force_refresh=force_refresh,
        )
        active = [
            e for e in entries if e.get("status") == "active" and e.get("directory")
        ]
        if not active:
            return None, entries
        return str(active[0]["directory"]), entries

    def summarize_prerelease_history(
        self, entries: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        created = sum(1 for e in entries if e.get("added_at") or e.get("added_sha"))
        deleted = sum(
            1 for e in entries if e.get("status") == "deleted" or e.get("removed_at")
        )
        active = sum(
            1 for e in entries if e.get("status") == "active" or e.get("active")
        )
        return {"created": created, "deleted": deleted, "active": active}

    def update_prerelease_tracking(
        self,
        latest_release_tag: str,
        newest_prerelease_dir: str,
        *,
        cache_manager: Any,
    ) -> int:
        """
        Update legacy-format prerelease_tracking.json with newest prerelease id.
        """
        if not newest_prerelease_dir or not newest_prerelease_dir.startswith(
            FIRMWARE_DIR_PREFIX
        ):
            return 0

        tracking_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_TRACKING_JSON_FILE
        )
        prerelease_id = newest_prerelease_dir.removeprefix(FIRMWARE_DIR_PREFIX).lower()
        clean_latest_release = self.extract_clean_version(latest_release_tag)

        tracking = cache_manager.read_json(tracking_file)
        if not isinstance(tracking, dict):
            tracking = {}

        existing_release = tracking.get("version") or tracking.get("latest_version")
        commits_raw = tracking.get("commits")
        if commits_raw is None and tracking.get("hash"):
            commits_raw = [tracking.get("hash")]
        existing_commits = (
            [c for c in commits_raw if isinstance(c, str)]
            if isinstance(commits_raw, list)
            else []
        )

        if (
            existing_release
            and clean_latest_release
            and existing_release != clean_latest_release
        ):
            logger.info(
                "New release %s detected (previously tracking %s). Resetting prerelease tracking.",
                latest_release_tag,
                existing_release,
            )
            existing_commits = []

        if prerelease_id in set(existing_commits):
            return len(existing_commits)

        updated_commits = list(dict.fromkeys([*existing_commits, prerelease_id]))
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "version": clean_latest_release,
            "commits": updated_commits,
            "hash": (
                prerelease_id.split(".")[-1] if "." in prerelease_id else prerelease_id
            ),
            "count": len(updated_commits),
            "timestamp": now_iso,
            "last_updated": now_iso,
        }
        if not cache_manager.atomic_write_json(tracking_file, payload):
            return 0
        return len(updated_commits)

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
        except Exception:
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
        if hash_suffix:
            return f"{clean_base}-{prerelease_type}.1+{hash_suffix}"
        else:
            return f"{clean_base}-{prerelease_type}.1"

    def scan_directory_for_prerelease_versions(
        self, directory_path: str, pattern: str = "*"
    ) -> List[str]:
        """
        Scan directory for prerelease version files.

        Args:
            directory_path: Path to directory to scan
            pattern: File pattern to match

        Returns:
            List[str]: List of found prerelease versions
        """
        import glob
        import os

        if not os.path.exists(directory_path):
            return []

        found_versions = []
        pattern_with_path = os.path.join(directory_path, pattern)

        for file_path in glob.glob(pattern_with_path):
            try:
                # Extract version from filename
                filename = os.path.basename(file_path)
                # Look for version patterns in filename
                version_match = re.search(
                    r"(?:v|version|release)?[_-]?(\d+\.\d+\.\d+[^\/]*)",
                    filename,
                    re.IGNORECASE,
                )
                if version_match:
                    version = version_match.group(1)
                    # Check if it's a prerelease
                    if self.is_prerelease_version(version):
                        found_versions.append(version)
            except Exception:
                continue

        return found_versions

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

        # Check for prerelease indicators
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
        return any(indicator in version_lower for indicator in prerelease_indicators)

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

    def manage_prerelease_tracking_files(
        self,
        tracking_dir: str,
        current_prereleases: List[Dict[str, Any]],
        cache_manager: Any,
    ) -> None:
        """
        Manage prerelease tracking files including cleanup of superseded prereleases.

        Args:
            tracking_dir: Directory where tracking files are stored
            current_prereleases: List of current prerelease tracking data
            cache_manager: CacheManager instance for file operations
        """
        if not os.path.exists(tracking_dir):
            return

        # Get existing tracking files
        tracking_files = [
            os.path.join(tracking_dir, f)
            for f in os.listdir(tracking_dir)
            if f.startswith("prerelease_") and f.endswith(".json")
        ]

        # Read existing prerelease tracking data
        existing_prereleases = []
        for file_path in tracking_files:
            tracking_data = cache_manager.read_json(file_path)
            if tracking_data and self.validate_version_tracking_data(
                tracking_data, ["prerelease_version", "base_version"]
            ):
                existing_prereleases.append(tracking_data)

        # Cleanup superseded/expired prereleases by comparing to current set
        for existing in existing_prereleases:
            should_cleanup = False
            for current in current_prereleases:
                if self.should_cleanup_superseded_prerelease(existing, current):
                    should_cleanup = True
                    break

            if should_cleanup:
                prerelease_version = existing.get("prerelease_version", "")
                if not prerelease_version:
                    continue
                safe_version = re.sub(r"[^a-zA-Z0-9.-]", "_", prerelease_version)
                filename_pattern = f"prerelease_{safe_version}_*.json"

                for filename in os.listdir(tracking_dir):
                    if re.fullmatch(filename_pattern, filename):
                        try:
                            os.remove(os.path.join(tracking_dir, filename))
                            logger.info(
                                f"Cleaned up superseded prerelease tracking: {filename}"
                            )
                        except OSError as e:
                            logger.error(
                                f"Error cleaning up prerelease tracking {filename}: {e}"
                            )

        # Write/update tracking files for current prereleases
        for current in current_prereleases:
            if not self.validate_version_tracking_data(
                current, ["prerelease_version", "base_version"]
            ):
                logger.warning(
                    f"Invalid prerelease tracking data skipped: {current.get('prerelease_version')}"
                )
                continue

            prerelease_version = current.get("prerelease_version")
            base_version = current.get("base_version")
            if not prerelease_version or not base_version:
                continue

            safe_version = re.sub(r"[^a-zA-Z0-9.-]", "_", prerelease_version)
            safe_base = re.sub(r"[^a-zA-Z0-9.-]", "_", base_version)
            filename = f"prerelease_{safe_version}_{safe_base}.json"
            file_path = os.path.join(tracking_dir, filename)

            cache_manager.atomic_write_json(file_path, current)

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
