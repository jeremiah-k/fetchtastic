"""
Prerelease History Management

This module handles the tracking, fetching, and management of prerelease history
from GitHub commits and repository directories.
"""

import fnmatch
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    DEFAULT_PRERELEASE_ACTIVE,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    DEFAULT_PRERELEASE_STATUS,
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_BASE,
    GITHUB_MAX_PER_PAGE,
    PRERELEASE_ADD_COMMIT_PATTERN,
    PRERELEASE_COMMIT_HISTORY_FILE,
    PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
    PRERELEASE_COMMITS_CACHE_FILE,
    PRERELEASE_DELETE_COMMIT_PATTERN,
    PRERELEASE_REQUEST_TIMEOUT,
    PRERELEASE_TRACKING_JSON_FILE,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .cache import parse_iso_datetime_utc
from .version import VersionManager


class PrereleaseHistoryManager:
    """
    Manages prerelease history, commit tracking, and directory scanning.
    """

    _PRERELEASE_ADD_RX = re.compile(PRERELEASE_ADD_COMMIT_PATTERN)
    _PRERELEASE_DELETE_RX = re.compile(PRERELEASE_DELETE_COMMIT_PATTERN)

    def __init__(self) -> None:
        """
        Initialize the PrereleaseHistoryManager and its version utilities.

        Also creates a VersionManager instance and initializes the in-memory commit cache and its timestamp to None.
        """
        self.version_manager = VersionManager()
        self._in_memory_commits_cache: Optional[Dict[str, Any]] = None
        self._in_memory_commits_timestamp: Optional[datetime] = None

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
        Fetch recent commits for meshtastic.github.io repository, using a local cache with expiry to avoid unnecessary API requests.

        Parameters:
            limit (int): Maximum number of commits to return; values less than 1 are treated as 1.
            cache_manager (Any): Cache manager providing `cache_dir`, `read_json`, and `atomic_write_json` used for storing/retrieving cached commits.
            github_token (Optional[str]): GitHub token to use for API requests; if omitted, an environment token may be used when allowed.
            allow_env_token (bool): Whether an environment-provided GitHub token may be used when `github_token` is not supplied.
            force_refresh (bool): If True, ignore any cached data and fetch fresh commits from the API.

        Returns:
            List[Dict[str, Any]]: A list of commit objects (as returned by GitHub API), up to `limit`. Returns an empty list on failure.
        """
        limit = max(1, int(limit))
        cache_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_COMMITS_CACHE_FILE
        )

        now = datetime.now(timezone.utc)

        if not force_refresh:
            if (
                self._in_memory_commits_cache is not None
                and self._in_memory_commits_timestamp is not None
                and (now - self._in_memory_commits_timestamp).total_seconds()
                < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS
            ):
                logger.debug(
                    "Using in-memory prerelease commit cache (cached %.0fs ago)",
                    (now - self._in_memory_commits_timestamp).total_seconds(),
                )
                commits = self._in_memory_commits_cache.get("commits", [])
                if not isinstance(commits, list) or not all(
                    isinstance(c, dict) for c in commits
                ):
                    logger.warning("Invalid commits cache structure, ignoring")
                    self._in_memory_commits_cache = None
                    self._in_memory_commits_timestamp = None
                    # Fall through to fetch from file cache
                else:
                    return commits[:limit]

        cached = cache_manager.read_json(cache_file)
        if isinstance(cached, dict):
            cached_at = cached.get("cached_at")
            commits = cached.get("commits")
            if cached_at and isinstance(commits, list):
                cached_at_dt = parse_iso_datetime_utc(cached_at)
                if cached_at_dt:
                    age_seconds = (now - cached_at_dt).total_seconds()
                    if age_seconds < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                        logger.debug("Using cached prerelease commit history")
                        self._in_memory_commits_cache = cached
                        self._in_memory_commits_timestamp = cached_at_dt
                        return commits[:limit]
                    logger.debug("Commits cache expired (age: %.1fs)", age_seconds)

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
                    timeout=PRERELEASE_REQUEST_TIMEOUT,
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

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
        ) as e:
            logger.warning("Could not fetch repo commits (%s): %s", type(e).__name__, e)
            return []

        now_after_fetch = datetime.now(timezone.utc)
        cache_data = {
            "commits": all_commits,
            "cached_at": now_after_fetch.isoformat(),
        }
        if cache_manager.atomic_write_json(cache_file, cache_data):
            logger.debug("Saved %d prerelease commits to cache", len(all_commits))
        self._in_memory_commits_cache = cache_data
        self._in_memory_commits_timestamp = now_after_fetch

        return all_commits[:limit]

    def extract_prerelease_directory_timestamps(
        self, commits: List[Dict[str, Any]]
    ) -> Dict[str, datetime]:
        """
        Build a mapping of prerelease firmware directory names to their add-commit timestamps.

        Scans the provided commit list for commit messages that match the prerelease
        add pattern and records the committer timestamp for each corresponding
        firmware directory (keyed by the lowercase directory name).

        Parameters:
            commits (List[Dict[str, Any]]): Commit objects as returned by the GitHub API.

        Returns:
            Dict[str, datetime]: Mapping of lowercase firmware directory name to commit datetime.
        """
        timestamps: Dict[str, datetime] = {}

        for commit in commits or []:
            if not isinstance(commit, dict):
                continue
            commit_info = commit.get("commit") or {}
            message = commit_info.get("message") or ""
            if not isinstance(message, str):
                continue
            m_add = self._PRERELEASE_ADD_RX.match(message.strip())
            if not m_add:
                continue
            base_version, short_hash = m_add.group(1), m_add.group(2)
            directory = f"{FIRMWARE_DIR_PREFIX}{base_version}.{short_hash}".lower()
            timestamp_str = commit_info.get("committer", {}).get("date")
            timestamp = parse_iso_datetime_utc(timestamp_str)
            if timestamp is None:
                continue
            existing = timestamps.get(directory)
            if existing is None or timestamp > existing:
                timestamps[directory] = timestamp

        return timestamps

    @staticmethod
    def _create_default_prerelease_entry(
        *, directory: str, identifier: str, base_version: str, commit_hash: str
    ) -> Dict[str, Any]:
        """
        Create a default prerelease history entry populated with the provided identifiers and unset metadata fields.

        Returns:
            dict: Prerelease entry with keys:
                - directory (str): directory name or path
                - identifier (str): prerelease identifier
                - base_version (str): base version string
                - commit_hash (str): associated commit hash
                - added_at (None): timestamp when added (unset)
                - removed_at (None): timestamp when removed (unset)
                - added_sha (None): SHA when added (unset)
                - removed_sha (None): SHA when removed (unset)
                - active (bool): default active flag (DEFAULT_PRERELEASE_ACTIVE)
                - status (str): default status (DEFAULT_PRERELEASE_STATUS)
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
        """
        Record a prerelease addition for a directory in the provided entries mapping.

        If an entry for the directory does not exist, a default prerelease entry is created. When a timestamp or SHA is provided and the corresponding fields are not already set, they are recorded as `added_at` and `added_sha`. The entry is marked active with `status` set to "active" and any removal fields (`removed_at`, `removed_sha`) are cleared.

        Parameters:
            entries (Dict[str, Dict[str, Any]]): Mapping of directory names to prerelease entry dictionaries to update.
            directory (str): Directory key under which to record the prerelease.
            identifier (str): Identifier for the prerelease (e.g., directory suffix or version).
            expected_version (str): Base version expected for this prerelease; used when creating a default entry.
            short_hash (str): Short commit hash or identifier to store on default entry creation.
            timestamp (Optional[str]): Timestamp string to set as `added_at` if the entry does not already have it.
            sha (Optional[str]): Full commit SHA to set as `added_sha` if the entry does not already have it.
        """
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
        """
        Record that a prerelease directory was removed and update or create its history entry.

        Parameters:
            entries (Dict[str, Dict[str, Any]]): Mapping of directory names to prerelease history entries to update.
            directory (str): Directory key under which the prerelease entry is stored/created.
            identifier (str): Prerelease identifier (e.g., directory suffix or ID) to store on a new entry.
            expected_version (str): Base version that the prerelease is associated with; used when creating a default entry.
            short_hash (str): Short commit hash to store in a newly created default entry.
            timestamp (Optional[str]): ISO-8601 timestamp when the deletion occurred; set to `removed_at` if not already present.
            sha (Optional[str]): Full commit SHA associated with the deletion; set to `removed_sha` if not already present.
        """
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
        Construct a simplified prerelease history for a given base version by parsing commit messages.

        Scans commits from oldest to newest and records prerelease addition and deletion events that match expected_version; entries are merged per directory and sorted by added timestamp then directory.

        Parameters:
            expected_version (str): Base firmware version to filter prerelease events (e.g., "1.2.3").
            commits (List[Dict[str, Any]]): List of commit objects to scan for prerelease add/remove messages.

        Returns:
            entries (List[Dict[str, Any]]): Sorted list of simplified prerelease entry dictionaries aggregated by directory.
            seen_shas (set[str]): Set of commit SHAs observed while scanning the commits.
        """
        entries_by_dir: Dict[str, Dict[str, Any]] = {}
        seen_shas: set[str] = set()

        # Reverse commits to process from oldest to newest
        for commit in reversed(commits):
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
        Retrieve simplified prerelease history for a given base version, using a cached value when it is fresh.

        Parameters:
            expected_version (str): Base firmware version to build history for.
            cache_manager (Any): Cache manager providing `cache_dir`, `read_json(path)` and `atomic_write_json(path, obj)` used to load and persist history.
            github_token (Optional[str]): GitHub token to use for API requests, if any.
            allow_env_token (bool): Whether to allow a token sourced from the environment when `github_token` is not provided.
            force_refresh (bool): If True, bypass any cached history and fetch commits from GitHub.
            max_commits (int): Maximum number of recent repository commits to fetch when rebuilding history.

        Returns:
            List[Dict[str, Any]]: Simplified prerelease history entries for the specified base version.
        """
        history_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_COMMIT_HISTORY_FILE
        )
        now = datetime.now(timezone.utc)

        cache = cache_manager.read_json(history_file)
        if not isinstance(cache, dict):
            cache = {}

        cached_entry = cache.get(expected_version) if not force_refresh else None
        cache_was_stale = False
        if isinstance(cached_entry, dict) and not force_refresh:
            entries = cached_entry.get("entries")
            last_checked_raw = cached_entry.get("last_checked") or cached_entry.get(
                "cached_at"
            )
            if isinstance(entries, list) and last_checked_raw:
                last_checked = parse_iso_datetime_utc(last_checked_raw)
                if last_checked:
                    age_s = (now - last_checked).total_seconds()
                    if age_s < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                        logger.debug(
                            "Using cached prerelease history for %s (cached %.0fs ago)",
                            expected_version,
                            age_s,
                        )
                        return entries
                    cache_was_stale = True
                    logger.debug(
                        "Prerelease history cache stale for %s (age %.0fs >= %ss); extending cache",
                        expected_version,
                        age_s,
                        PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
                    )

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

        old_version_data = cache.get(expected_version)
        old_entries = cache.get(expected_version, {}).get("entries")

        # Only write if data has changed
        if old_entries == entries:
            if cache_was_stale and isinstance(old_version_data, dict):
                logger.debug(
                    "Prerelease history data unchanged for %s; updating last_checked",
                    expected_version,
                )
                now_after_build = datetime.now(timezone.utc)
                cache[expected_version] = {
                    "entries": entries,
                    "cached_at": old_version_data.get("cached_at"),
                    "last_checked": now_after_build.isoformat(),
                    "shas": sorted(shas),
                }
                if cache_manager.atomic_write_json(history_file, cache):
                    logger.debug(
                        "Extended prerelease history cache freshness for %s",
                        expected_version,
                    )
                return entries
            logger.debug(
                "Prerelease history cache unchanged for %s (total %d entries)",
                expected_version,
                len(cache),
            )
            return entries

        now_after_build = datetime.now(timezone.utc)
        cache[expected_version] = {
            "entries": entries,
            "cached_at": now_after_build.isoformat(),
            "last_checked": now_after_build.isoformat(),
            "shas": sorted(shas),
        }
        if cache_manager.atomic_write_json(history_file, cache):
            logger.debug(
                "Saved %d prerelease history entries to cache for %s",
                len(entries),
                expected_version,
            )
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
        """
        Return the most recent active prerelease directory for a base version and the full prerelease history.

        Parameters:
            expected_version (str): Base release version to match when selecting prerelease entries.

        Returns:
            tuple: `latest_dir` is the directory string of the newest active prerelease for `expected_version`, or `None` if no active prerelease exists; `entries` is the list of prerelease history entry dictionaries.
        """
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
        return str(active[-1]["directory"]), entries

    def summarize_prerelease_history(
        self, entries: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Produce counts of created, deleted, and active prerelease entries from a list of history records.

        Parameters:
            entries (List[Dict[str, Any]]): List of prerelease history records. Each record may include keys such as
                `added_at`, `added_sha`, `removed_at`, `status`, and `active` which are used to determine counts.

        Returns:
            Dict[str, int]: A dictionary with keys:
                - `created`: number of entries with `added_at` or `added_sha`.
                - `deleted`: number of entries with `status` equal to `"deleted"` or with `removed_at`.
                - `active`: number of entries with `status` equal to `"active"` or with a truthy `active` flag.
        """
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
        Update the legacy prerelease_tracking.json with a newest prerelease identifier.

        If newest_prerelease_dir does not start with the firmware prefix this is a no-op.
        Reads existing tracking data, resets tracked commits when the provided latest release differs from the tracked release, appends the new prerelease id if not already present, and writes the updated payload atomically.

        Parameters:
            latest_release_tag (str): Release tag to record as the tracked release.
            newest_prerelease_dir (str): Directory name of the newest prerelease (must start with the firmware directory prefix).
            cache_manager (Any): Object providing `cache_dir`, `read_json(path)` and `atomic_write_json(path, obj)`.

        Returns:
            int: Number of prerelease ids recorded after the update, or `0` if no update was performed or the write failed.
        """
        if not newest_prerelease_dir or not newest_prerelease_dir.startswith(
            FIRMWARE_DIR_PREFIX
        ):
            return 0

        tracking_file = os.path.join(
            cache_manager.cache_dir, PRERELEASE_TRACKING_JSON_FILE
        )
        prerelease_id = newest_prerelease_dir.removeprefix(FIRMWARE_DIR_PREFIX).lower()
        clean_latest_release = self.version_manager.extract_clean_version(
            latest_release_tag
        )

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

    def manage_prerelease_tracking_files(
        self,
        tracking_dir: str,
        current_prereleases: List[Dict[str, Any]],
        cache_manager: Any,
    ) -> None:
        """
        Maintain prerelease tracking JSON files by removing superseded or expired entries and writing/updating files for the current prereleases.

        This function:
        - Returns immediately if the tracking directory does not exist.
        - Reads existing tracking files named "prerelease_*.json" from tracking_dir and keeps only those that pass version_manager.validate_version_tracking_data (must include "prerelease_version" and "base_version").
        - For each existing tracking entry, deletes any on-disk tracking files whose prerelease is considered superseded or expired according to should_cleanup_superseded_prerelease.
        - Writes or updates a tracking file for each validated entry in current_prereleases using a filename of the form "prerelease_{safe_prerelease_version}_{safe_base_version}.json" (non-alphanumeric characters except dot and dash are replaced with underscores) via cache_manager.atomic_write_json.

        Parameters:
            tracking_dir (str): Filesystem directory that stores prerelease tracking JSON files.
            current_prereleases (List[Dict[str, Any]]): Iterable of prerelease tracking objects; each must include `prerelease_version` and `base_version`.
            cache_manager (Any): Object providing file helpers used by this function; must implement `read_json(path)` returning parsed JSON or falsy, and `atomic_write_json(path, data)` to write JSON atomically.
        """
        if not os.path.exists(tracking_dir):
            return

        # Get existing tracking files
        tracking_files = []
        try:
            with os.scandir(tracking_dir) as it:
                for entry in it:
                    if entry.name.startswith("prerelease_") and entry.name.endswith(
                        ".json"
                    ):
                        tracking_files.append(entry.path)
        except FileNotFoundError:
            pass

        # Read existing prerelease tracking data
        existing_prereleases = []
        for file_path in tracking_files:
            tracking_data = cache_manager.read_json(file_path)
            if tracking_data and self.version_manager.validate_version_tracking_data(
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

                try:
                    with os.scandir(tracking_dir) as it:
                        for entry in it:
                            if fnmatch.fnmatch(entry.name, filename_pattern):
                                try:
                                    os.remove(entry.path)
                                    logger.info(
                                        f"Cleaned up superseded prerelease tracking: {entry.name}"
                                    )
                                except OSError as e:
                                    logger.error(
                                        f"Error cleaning up prerelease tracking {entry.name}: {e}"
                                    )
                except FileNotFoundError:
                    pass

        # Write/update tracking files for current prereleases
        for current in current_prereleases:
            if not self.version_manager.validate_version_tracking_data(
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

    def create_prerelease_tracking_data(
        self,
        prerelease_version: str,
        base_version: str,
        expiry_hours: float,
        commit_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Builds a prerelease tracking payload that includes expiry and creation timestamps.

        Parameters:
            prerelease_version (str): Prerelease identifier (e.g., "1.2.3-rc.4").
            base_version (str): Base release version that this prerelease targets.
            expiry_hours (float): Number of hours from now when the tracking entry should expire.
            commit_hash (Optional[str]): Optional commit SHA associated with the prerelease.

        Returns:
            dict: Tracking data with keys:
                - "prerelease_version": the provided prerelease_version
                - "base_version": the provided base_version
                - "expiry_timestamp": ISO 8601 UTC timestamp when the entry expires
                - "created_at": ISO 8601 UTC timestamp when the entry was created
                - "commit_hash": included only if `commit_hash` was provided
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
        Decide whether a current prerelease should be removed because it is superseded.

        Checks two conditions: if the new prerelease has a newer `base_version` than the current prerelease, or if the current prerelease includes an `expiry_timestamp` (ISO 8601 string) that is in the past.

        Parameters:
            current_prerelease (dict): Prerelease record; expected keys used: `base_version` (str) and optional `expiry_timestamp` (ISO 8601 str).
            new_prerelease (dict): New prerelease record; expected key used: `base_version` (str).

        Returns:
            `True` if the current prerelease should be cleaned up, `False` otherwise.
        """
        # Check if new prerelease is based on a newer version
        current_base = current_prerelease.get("base_version")
        new_base = new_prerelease.get("base_version")

        if current_base and new_base:
            comparison = self.version_manager.compare_versions(new_base, current_base)
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

    def scan_directory_for_prerelease_versions(
        self, directory_path: str, pattern: str = "*"
    ) -> List[str]:
        """
        Find prerelease version identifiers from filenames in a directory.

        Scans files in the given directory using the provided glob pattern (non-recursively),
        extracts version-like substrings from filenames, and returns those that are
        recognized as prerelease versions by the manager's version checker. If the
        directory does not exist, an empty list is returned.

        Parameters:
            directory_path (str): Path to the directory to scan.
            pattern (str): Glob pattern to match filenames (default "*").

        Returns:
            List[str]: A list of prerelease version strings found in matching filenames.
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
                    if self.version_manager.is_prerelease_version(version):
                        found_versions.append(version)
            except (OSError, ValueError, TypeError):
                logger.debug("Failed to extract version from file: %s", file_path)
                continue

        return found_versions

    def scan_prerelease_directories(
        self, directories: List[str], expected_version: str
    ) -> List[str]:
        """
        Collect prerelease directory identifiers from a list of directory names that follow the meshtastic.github.io naming convention.

        Parameters:
            directories (List[str]): Iterable of directory names to inspect (e.g., "firmware-1.2.3.abcd12").
            expected_version (str): Base version to match (e.g., "1.2.3").

        Returns:
            List[str]: List of directory suffixes matching the expected base version (the part after the "firmware-" prefix).
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

    def find_latest_remote_prerelease_dir(
        self,
        expected_version: str,
        *,
        cache_manager: Any,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
        force_refresh: bool = False,
        max_commits: int = DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    ) -> Optional[str]:
        """
        Determine the newest prerelease directory that exists remotely in the repository.

        Prefers directories whose hash suffixes match identifiers seen in recent prerelease commit history, and falls back to repository directory listings when scoring candidates.

        Parameters:
            expected_version (str): Base version to match against prerelease directory names.
            cache_manager: Cache manager used to retrieve repository directories and history.

        Returns:
            The newest prerelease directory name prefixed with `FIRMWARE_DIR_PREFIX` (e.g., `"firmware-..."`), or `None` if no matching remote prerelease is found.
        """
        preferred_hashes: set[str] = set()
        try:
            history_entries = self.get_prerelease_commit_history(
                expected_version,
                cache_manager=cache_manager,
                github_token=github_token,
                allow_env_token=allow_env_token,
                force_refresh=force_refresh,
                max_commits=max_commits,
            )
        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.debug(
                "Failed to build prerelease commit history for %s: %s",
                expected_version,
                exc,
            )
            history_entries = []

        for entry in history_entries:
            identifier = _extract_identifier_from_entry(entry)
            if identifier and "." in identifier:
                preferred_hashes.add(identifier.rsplit(".", 1)[-1].lower())

        try:
            repo_dirs = cache_manager.get_repo_directories(
                "",
                force_refresh=force_refresh,
                github_token=github_token,
                allow_env_token=allow_env_token,
            )
            if not isinstance(repo_dirs, list):
                logger.debug(
                    "Expected list of repo directories from cache manager, got %s",
                    type(repo_dirs),
                )
                return None
        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.debug(
                "Failed to fetch prerelease directories for %s: %s",
                expected_version,
                exc,
            )
            return None

        candidate_suffixes = self.scan_prerelease_directories(
            [d for d in repo_dirs if isinstance(d, str)], expected_version
        )
        if not candidate_suffixes:
            return None

        def _score_suffix(suffix: str) -> Tuple[int, Tuple[int, ...], str]:
            """
            Compute a sort key for a prerelease directory suffix used to rank candidate suffixes.

            Parameters:
                suffix (str): A prerelease directory suffix, typically containing a version and an optional hash (e.g., "1.2.3.abcd").

            Returns:
                tuple: A three-element tuple used for sorting:
                    - int: `1` if the suffix's trailing fragment after the last dot is present in the closure's preferred_hashes set, `0` otherwise.
                    - tuple[int, ...]: A numeric release tuple derived from the suffix for version comparison, or an empty tuple if not available.
                    - str: The original suffix string (used as a final tiebreaker).
            """
            cmp_tuple = self.version_manager.get_release_tuple(suffix) or ()
            suffix_hash = suffix.rsplit(".", 1)[-1].lower() if "." in suffix else ""
            return (
                int(suffix_hash in preferred_hashes),
                cmp_tuple,
                suffix,
            )

        candidate_suffixes.sort(key=_score_suffix, reverse=True)
        return f"{FIRMWARE_DIR_PREFIX}{candidate_suffixes[0]}"


def _extract_identifier_from_entry(entry: Dict[str, Any]) -> str:
    """
    Retrieve the identifier for a prerelease history entry, preferring the "identifier" key, then "directory", then "dir".

    Returns:
        The identifier string if present, otherwise an empty string.
    """
    return entry.get("identifier") or entry.get("directory") or entry.get("dir") or ""


def _is_entry_deleted(entry: Dict[str, Any]) -> bool:
    """
    Determine whether a prerelease history entry is deleted.

    Parameters:
        entry (Dict[str, Any]): A prerelease history entry dictionary.

    Returns:
        bool: True if the entry's status is "deleted" or it has a truthy `removed_at` value, False otherwise.
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
