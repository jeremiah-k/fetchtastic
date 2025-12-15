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

from fetchtastic.constants import (
    DEFAULT_PRERELEASE_ACTIVE,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    DEFAULT_PRERELEASE_STATUS,
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_BASE,
    GITHUB_API_TIMEOUT,
    GITHUB_MAX_PER_PAGE,
    PRERELEASE_ADD_COMMIT_PATTERN,
    PRERELEASE_COMMIT_HISTORY_FILE,
    PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
    PRERELEASE_COMMITS_CACHE_FILE,
    PRERELEASE_DELETE_COMMIT_PATTERN,
    PRERELEASE_TRACKING_JSON_FILE,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .version import VersionManager


class PrereleaseHistoryManager:
    """
    Manages prerelease history, commit tracking, and directory scanning.
    """

    _PRERELEASE_ADD_RX = re.compile(PRERELEASE_ADD_COMMIT_PATTERN)
    _PRERELEASE_DELETE_RX = re.compile(PRERELEASE_DELETE_COMMIT_PATTERN)

    def __init__(self):
        self.version_manager = VersionManager()

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
                        if cached_at_dt.tzinfo is None:
                            cached_at_dt = cached_at_dt.replace(tzinfo=timezone.utc)
                        age_seconds = (
                            datetime.now(timezone.utc) - cached_at_dt
                        ).total_seconds()
                        if age_seconds < PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS:
                            logger.debug("Using cached prerelease commit history")
                            return commits[:limit]
                        logger.debug("Commits cache expired (age: %.1fs)", age_seconds)
                    except (ValueError, TypeError):
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
            logger.warning("Could not fetch repo commits (%s): %s", type(e).__name__, e)
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
        Manage prerelease tracking files including cleanup of superseded prereleases.
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

                for filename in os.listdir(tracking_dir):
                    if fnmatch.fnmatch(filename, filename_pattern):
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
        Create prerelease tracking data structure.
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
        Scan directory for prerelease version files.
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
            except Exception:
                logger.debug("Failed to extract version from file: %s", file_path)
                continue

        return found_versions

    def scan_prerelease_directories(
        self, directories: List[str], expected_version: str
    ) -> List[str]:
        """
        Scan prerelease directories (meshtastic.github.io style) to collect prerelease identifiers.
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


def _extract_identifier_from_entry(entry: Dict[str, Any]) -> str:
    """
    Return the identifier for a prerelease history entry.

    Checks for keys in priority order: "identifier", "directory", then "dir",
    and returns the first non-empty value found. If none are present, returns an empty string.

    Parameters:
        entry (dict): History entry mapping potentially containing identifier fields.

    Returns:
        identifier (str): The extracted identifier or an empty string if not found.
    """
    return entry.get("identifier") or entry.get("directory") or entry.get("dir") or ""


def _is_entry_deleted(entry: Dict[str, Any]) -> bool:
    """
    Check if a prerelease history entry is marked as deleted.

    Parameters:
        entry (dict): History entry to check.

    Returns:
        bool: True if the entry is marked as deleted, False otherwise.
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
