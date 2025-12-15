"""
Cache Management for Fetchtastic Download Subsystem

This module provides caching infrastructure for release metadata,
commit timestamps, and other download-related data.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

try:
    import requests

    _RequestException = requests.RequestException
except ImportError:  # pragma: no cover
    _RequestException = Exception  # type: ignore[assignment]

from fetchtastic.constants import (
    COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS,
    FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
    GITHUB_API_BASE,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    make_github_api_request,
    track_api_cache_hit,
    track_api_cache_miss,
)

from .files import _atomic_write, _atomic_write_json


def _parse_iso_datetime_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class CacheManager:
    """
    Manages caching of download-related data including releases, commit timestamps,
    and prerelease tracking information.

    Provides atomic write operations and cache expiry functionality.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the cache manager.

        Args:
            cache_dir: Optional cache directory path. If None, uses default.
        """
        self.cache_dir = cache_dir or self._get_default_cache_dir()
        self._ensure_cache_dir_exists()

    def _get_default_cache_dir(self) -> str:
        """Get the default cache directory path."""
        import platformdirs

        # Legacy fetchtastic uses platformdirs.user_cache_dir("fetchtastic")
        # and older cache/tracking files live there; keep parity.
        return platformdirs.user_cache_dir("fetchtastic")

    def _ensure_cache_dir_exists(self) -> None:
        """Ensure the cache directory exists."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create cache directory {self.cache_dir}: {e}")
            raise

    def atomic_write(
        self, file_path: str, writer_func: Callable[[Any], None], suffix: str = ".tmp"
    ) -> bool:
        """
        Write text to a file atomically by writing to a temporary file and replacing the target on success.

        Args:
            file_path: Destination path to write
            writer_func: Callable that receives an open text file-like object and writes content
            suffix: Suffix to use for temporary file

        Returns:
            bool: True if content was written successfully, False otherwise
        """
        return _atomic_write(file_path, writer_func, suffix)

    def atomic_write_text(self, file_path: str, content: str) -> bool:
        """
        Atomically write text content to a file.

        Args:
            file_path: Destination path for the text file
            content: Text content to write

        Returns:
            bool: True on successful write, False on error
        """

        def _write_text_content(f):
            f.write(content)

        return self.atomic_write(file_path, _write_text_content, suffix=".txt")

    def atomic_write_json(self, file_path: str, data: Dict) -> bool:
        """
        Atomically write a Python mapping to a JSON file.

        Args:
            file_path: Destination path for the JSON file
            data: Mapping to serialize to JSON

        Returns:
            bool: True on successful write, False on error
        """
        return _atomic_write_json(file_path, data)

    def read_json(self, file_path: str) -> Optional[Dict]:
        """
        Read and parse a JSON file.

        Args:
            file_path: Path to the JSON file to read

        Returns:
            Optional[Dict]: Parsed JSON data, or None if file doesn't exist or can't be read
        """
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Could not read JSON file {file_path}: {e}")
            return None

    def read_json_with_backward_compatibility(
        self, file_path: str, key_mapping: Optional[Dict[str, str]] = None
    ) -> Optional[Dict]:
        """
        Read JSON and map legacy keys to new keys for backward compatibility.

        Args:
            file_path: Path to JSON file
            key_mapping: Optional mapping of legacy_key -> new_key

        Returns:
            Optional[Dict]: Parsed and normalized JSON data
        """
        data = self.read_json(file_path)
        if data is None or not key_mapping:
            return data

        normalized = data.copy()
        for legacy_key, new_key in key_mapping.items():
            if legacy_key in data and new_key not in data:
                normalized[new_key] = data[legacy_key]
        return normalized

    def read_rate_limit_summary(self, cache_file: str) -> Optional[Dict[str, Any]]:
        """
        Read cached rate-limit summary if present.
        """
        return self.read_json(cache_file)

    def cache_with_expiry(
        self, cache_file: str, data: Dict, expiry_hours: float
    ) -> bool:
        """
        Write data to cache with expiry information.

        Args:
            cache_file: Path to the cache file
            data: Data to cache
            expiry_hours: Number of hours until cache expires

        Returns:
            bool: True if cache was written successfully, False otherwise
        """
        cache_data = {
            "data": data,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
            ).isoformat(),
        }
        return self.atomic_write_json(cache_file, cache_data)

    def read_cache_with_expiry(self, cache_file: str) -> Optional[Dict]:
        """
        Read cached data if it hasn't expired.

        Args:
            cache_file: Path to the cache file

        Returns:
            Optional[Dict]: Cached data if valid and not expired, None otherwise
        """
        cache_data = self.read_json(cache_file)
        if not cache_data:
            return None

        try:
            expires_at_str = cache_data.get("expires_at")
            if expires_at_str:
                expires_at = _parse_iso_datetime_utc(expires_at_str)
                if expires_at and datetime.now(timezone.utc) > expires_at:
                    logger.debug(f"Cache expired for {cache_file}")
                    return None
        except (ValueError, TypeError):
            # If expiry is malformed, treat the entry as non-expiring (legacy tolerant).
            pass

        return cache_data.get("data")

    def get_repo_directories(
        self,
        path: str = "",
        *,
        force_refresh: bool = False,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
    ) -> List[str]:
        """
        List directory names at a meshtastic.github.io repository path with short TTL caching.

        Cache format is compatible with the module-level prerelease directory cache helpers:
        `{cache_key: {"directories": [...], "cached_at": "<iso>"}}`.
        """
        normalized_path = (path or "").strip("/")
        cache_key = f"repo:{normalized_path or '/'}"
        cache_file = os.path.join(self.cache_dir, "prerelease_dirs.json")
        now = datetime.now(timezone.utc)

        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            cache = {}

        cached = cache.get(cache_key) if not force_refresh else None
        if isinstance(cached, dict) and not force_refresh:
            directories = cached.get("directories")
            cached_at_raw = cached.get("cached_at")
            if isinstance(directories, list) and cached_at_raw:
                cached_at = _parse_iso_datetime_utc(cached_at_raw)
                if cached_at:
                    age_s = (now - cached_at).total_seconds()
                    if age_s < FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS:
                        logger.debug(
                            "Using cached prerelease directories for %s (cached %.0fs ago)",
                            normalized_path or "/",
                            age_s,
                        )
                        return [d for d in directories if isinstance(d, str)]
                    logger.debug(
                        "Prerelease directory cache stale for %s (age %.0fs >= %ss); refreshing",
                        normalized_path or "/",
                        age_s,
                        FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
                    )

        api_url = (
            f"{MESHTASTIC_GITHUB_IO_CONTENTS_URL}/{normalized_path}"
            if normalized_path
            else MESHTASTIC_GITHUB_IO_CONTENTS_URL
        )
        try:
            response = make_github_api_request(
                api_url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                timeout=GITHUB_API_TIMEOUT,
            )
            contents = response.json()
            if not isinstance(contents, list):
                return []
            directories = [
                item.get("name")
                for item in contents
                if isinstance(item, dict)
                and item.get("type") == "dir"
                and item.get("name")
            ]
            cache[cache_key] = {
                "directories": directories,
                "cached_at": now.isoformat(),
            }
            self.atomic_write_json(cache_file, cache)
            return [d for d in directories if isinstance(d, str)]
        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Invalid JSON or structure in GitHub response for %s: %s", api_url, e
            )
            return []
        except _RequestException as exc:
            logger.debug("Could not fetch repo directories for %s: %s", api_url, exc)
            return []

    def get_repo_contents(
        self,
        path: str = "",
        *,
        force_refresh: bool = False,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Fetch raw directory contents at a meshtastic.github.io repository path with TTL caching.

        Returns the JSON list produced by the GitHub contents API for the given path.
        Entries are filtered to dictionaries for type safety.
        """
        normalized_path = (path or "").strip("/")
        cache_key = f"contents:{normalized_path or '/'}"
        cache_file = os.path.join(self.cache_dir, "repo_contents.json")
        now = datetime.now(timezone.utc)

        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            cache = {}

        cached = cache.get(cache_key) if not force_refresh else None
        if isinstance(cached, dict) and not force_refresh:
            contents = cached.get("contents")
            cached_at_raw = cached.get("cached_at")
            if isinstance(contents, list) and cached_at_raw:
                cached_at = _parse_iso_datetime_utc(cached_at_raw)
                if cached_at:
                    age_s = (now - cached_at).total_seconds()
                    if age_s < FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS:
                        logger.debug(
                            "Using cached repo contents for %s (cached %.0fs ago)",
                            normalized_path or "/",
                            age_s,
                        )
                        return [c for c in contents if isinstance(c, dict)]
                    logger.debug(
                        "Repo contents cache stale for %s (age %.0fs >= %ss); refreshing",
                        normalized_path or "/",
                        age_s,
                        FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
                    )

        api_url = (
            f"{MESHTASTIC_GITHUB_IO_CONTENTS_URL}/{normalized_path}"
            if normalized_path
            else MESHTASTIC_GITHUB_IO_CONTENTS_URL
        )
        try:
            response = make_github_api_request(
                api_url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                timeout=GITHUB_API_TIMEOUT,
            )
            contents = response.json()
            if not isinstance(contents, list):
                return []
            cache[cache_key] = {
                "contents": contents,
                "cached_at": now.isoformat(),
            }
            self.atomic_write_json(cache_file, cache)
            return [c for c in contents if isinstance(c, dict)]
        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Invalid JSON or structure in GitHub response for %s: %s", api_url, e
            )
            return []
        except _RequestException as exc:
            logger.debug("Could not fetch repo contents for %s: %s", api_url, exc)
            return []

    def clear_cache(self, cache_file: str) -> bool:
        """
        Clear a specific cache file.

        Args:
            cache_file: Path to the cache file to clear

        Returns:
            bool: True if cache was cleared successfully, False otherwise
        """
        try:
            if os.path.exists(cache_file):
                os.remove(cache_file)
            return True
        except OSError as e:
            logger.error(f"Could not clear cache file {cache_file}: {e}")
            return False

    def get_cache_file_path(self, cache_name: str, suffix: str = ".json") -> str:
        """
        Get the full path for a cache file.

        Args:
            cache_name: Name of the cache file (without extension)
            suffix: File extension

        Returns:
            str: Full path to the cache file
        """
        return os.path.join(self.cache_dir, f"{cache_name}{suffix}")

    @staticmethod
    def build_url_cache_key(url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Build a stable cache key matching legacy url?param=value formatting."""
        if not params:
            return url
        filtered = {k: v for k, v in params.items() if v is not None}
        if not filtered:
            return url
        return f"{url}?{urlencode(filtered)}"

    def _get_releases_cache_file(self) -> str:
        primary = os.path.join(self.cache_dir, "releases.json")
        legacy = os.path.join(self.cache_dir, "releases_cache.json")
        return (
            primary if os.path.exists(primary) or not os.path.exists(legacy) else legacy
        )

    def read_releases_cache_entry(
        self, url_cache_key: str, *, expiry_seconds: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Read a cached GitHub releases entry (legacy multi-entry cache file).

        Cache file schema:
          { "<url>?per_page=n": { "releases": [...], "cached_at": "<iso>" }, ... }
        """
        cache_file = self._get_releases_cache_file()
        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            track_api_cache_miss()
            return None

        now = datetime.now(timezone.utc)

        # Log any expired entries we notice (parity with legacy logs)
        for key, entry in list(cache.items()):
            if not isinstance(entry, dict):
                continue
            cached_at_raw = entry.get("cached_at")
            if not cached_at_raw:
                continue
            cached_at = _parse_iso_datetime_utc(cached_at_raw)
            if not cached_at:
                continue
            age_s = (now - cached_at).total_seconds()
            if age_s >= expiry_seconds:
                logger.debug(
                    "Skipping expired releases cache entry for %s (age %.0fs exceeds %ss)",
                    key,
                    age_s,
                    expiry_seconds,
                )

        entry = cache.get(url_cache_key)
        if not isinstance(entry, dict):
            track_api_cache_miss()
            return None

        cached_at_raw = entry.get("cached_at")
        releases = entry.get("releases")
        if not cached_at_raw or not isinstance(releases, list):
            track_api_cache_miss()
            return None

        cached_at = _parse_iso_datetime_utc(cached_at_raw)
        if not cached_at:
            track_api_cache_miss()
            return None

        age_s = (now - cached_at).total_seconds()
        if age_s >= expiry_seconds:
            track_api_cache_miss()
            return None

        track_api_cache_hit()
        return releases

    def write_releases_cache_entry(
        self, url_cache_key: str, releases: List[Dict[str, Any]]
    ) -> None:
        cache_file = self._get_releases_cache_file()
        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            cache = {}

        cache[url_cache_key] = {
            "releases": releases,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.atomic_write_json(cache_file, cache):
            logger.debug("Saved %d releases entries to cache", len(cache))

    def clear_all_caches(self) -> bool:
        """
        Clear all cache files in the cache directory.

        Returns:
            bool: True if all caches were cleared successfully, False otherwise
        """
        try:
            for cache_file in os.listdir(self.cache_dir):
                if cache_file.endswith((".json", ".tmp")):
                    try:
                        os.remove(os.path.join(self.cache_dir, cache_file))
                    except OSError as e:
                        logger.error(f"Could not remove cache file {cache_file}: {e}")
                        return False
            return True
        except OSError as e:
            logger.error(f"Could not clear cache directory {self.cache_dir}: {e}")
            return False

    def atomic_write_with_timestamp(
        self, file_path: str, data: Dict, timestamp_key: str = "last_updated"
    ) -> bool:
        """
        Atomically write data to a JSON file with timestamp tracking.

        Args:
            file_path: Destination path for the JSON file
            data: Data dictionary to write
            timestamp_key: Key to use for timestamp in the data

        Returns:
            bool: True on successful write, False on error
        """
        # Add timestamp to data
        data_with_timestamp = data.copy()
        data_with_timestamp[timestamp_key] = datetime.now(timezone.utc).isoformat()

        return self.atomic_write_json(file_path, data_with_timestamp)

    def read_with_expiry(self, file_path: str, expiry_hours: float) -> Optional[Dict]:
        """
        Read cached data and return None if expired.

        Args:
            file_path: Path to cache file
            expiry_hours: Number of hours before data is considered stale
        """
        cache_data = self.read_json(file_path)
        if not cache_data:
            return None

        ts_key = None
        for candidate in ("last_updated", "timestamp", "cached_at"):
            if candidate in cache_data:
                ts_key = candidate
                break

        if ts_key:
            try:
                ts_val = _parse_iso_datetime_utc(cache_data[ts_key])
                if ts_val is None:
                    return None
                if datetime.now(timezone.utc) - ts_val > timedelta(hours=expiry_hours):
                    return None
            except (ValueError, TypeError):
                return None

        return cache_data

    def migrate_legacy_cache_file(
        self,
        legacy_file_path: str,
        new_file_path: str,
        legacy_to_new_mapping: Dict[str, str],
    ) -> bool:
        """
        Migrate a legacy cache file to the new format.

        Args:
            legacy_file_path: Path to the legacy cache file
            new_file_path: Path for the new cache file
            legacy_to_new_mapping: Mapping of legacy keys to new keys

        Returns:
            bool: True if migration succeeded, False otherwise
        """
        try:
            # Read legacy file
            legacy_data = self.read_json(legacy_file_path)
            if not legacy_data:
                return False

            # Apply mapping
            new_data = legacy_data.copy()
            for legacy_key, new_key in legacy_to_new_mapping.items():
                if legacy_key in legacy_data and new_key not in new_data:
                    new_data[new_key] = legacy_data[legacy_key]

            # Write new file atomically
            success = self.atomic_write_with_timestamp(new_file_path, new_data)

            if success:
                logger.info(
                    f"Successfully migrated legacy cache from {legacy_file_path} to {new_file_path}"
                )
            else:
                logger.error(
                    f"Failed to migrate cache from {legacy_file_path} to {new_file_path}"
                )

            return success

        except (
            IOError,
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            TypeError,
        ) as e:
            logger.error(
                "Error migrating legacy cache file from %s to %s: %s",
                legacy_file_path,
                new_file_path,
                e,
            )
            return False

    def get_cache_expiry_timestamp(self, cache_file: str, expiry_hours: float) -> str:
        """
        Calculate the expiry timestamp for a cache file.

        Args:
            cache_file: Path to the cache file
            expiry_hours: Number of hours until cache expires

        Returns:
            str: ISO 8601 formatted expiry timestamp
        """
        return (datetime.now(timezone.utc) + timedelta(hours=expiry_hours)).isoformat()

    def validate_cache_format(self, cache_data: Dict, required_keys: List[str]) -> bool:
        """
        Validate that cache data contains required keys.

        Args:
            cache_data: Cache data to validate
            required_keys: List of required keys

        Returns:
            bool: True if cache data is valid, False otherwise
        """
        for key in required_keys:
            if key not in cache_data:
                logger.warning(f"Missing required key in cache data: {key}")
                return False
        return True

    def read_commit_timestamp_cache(self) -> Dict[str, Any]:
        """
        Read the commit timestamp cache with expiry.

        This is the unified expiry-aware implementation that should be used by all
        commit timestamp cache readers for consistency.

        Returns:
            Dict: Cached commit timestamps that are still valid.
        """
        cache_file = os.path.join(self.cache_dir, "commit_timestamps.json")
        cache_data = self.read_json(cache_file)
        if not isinstance(cache_data, dict):
            return {}

        now = datetime.now(timezone.utc)
        keep: Dict[str, Any] = {}

        for cache_key, cache_value in cache_data.items():
            # Support both legacy format and new format for backward compatibility
            if isinstance(cache_value, (list, tuple)) and len(cache_value) == 2:
                # New format: [timestamp_iso, cached_at_iso]
                try:
                    timestamp_str, cached_at_str = cache_value
                    cached_at = _parse_iso_datetime_utc(cached_at_str)
                    if cached_at is None:
                        continue
                    age = now - cached_at
                    if (
                        age.total_seconds()
                        < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60
                    ):
                        keep[cache_key] = cache_value
                except (ValueError, TypeError):
                    continue
            elif isinstance(cache_value, dict):
                # Legacy format: {"timestamp": "...", "cached_at": "..."}
                try:
                    timestamp_str = cache_value.get("timestamp")
                    cached_at_str = cache_value.get("cached_at")
                    if timestamp_str and cached_at_str:
                        cached_at = _parse_iso_datetime_utc(cached_at_str)
                        if cached_at is None:
                            continue
                        age = now - cached_at
                        if (
                            age.total_seconds()
                            < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60
                        ):
                            # Convert to new format for consistency
                            keep[cache_key] = [timestamp_str, cached_at_str]
                except (ValueError, TypeError):
                    continue

        return keep

    def get_commit_timestamp(
        self,
        owner: str,
        repo: str,
        commit_hash: str,
        *,
        github_token: Optional[str] = None,
        allow_env_token: bool = True,
        force_refresh: bool = False,
    ) -> Optional[datetime]:
        """
        Get a commit timestamp with on-disk cache parity to legacy.
        """
        cache_key = f"{owner}/{repo}/{commit_hash}"
        cache_file = os.path.join(self.cache_dir, "commit_timestamps.json")
        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            cache = {}

        now = datetime.now(timezone.utc)

        if not force_refresh and cache_key in cache:
            try:
                timestamp_str, cached_at_str = cache[cache_key]
                cached_at = _parse_iso_datetime_utc(cached_at_str)
                timestamp = _parse_iso_datetime_utc(timestamp_str)
                if cached_at is None or timestamp is None:
                    raise ValueError("Invalid commit timestamp cache entry")
                age = now - cached_at
                if age.total_seconds() < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60:
                    track_api_cache_hit()
                    logger.debug(
                        "Using cached commit timestamp for %s (cached %.0fs ago)",
                        commit_hash,
                        age.total_seconds(),
                    )
                    return timestamp
            except (ValueError, TypeError, KeyError) as exc:
                logger.debug(
                    "Ignoring invalid commit timestamp cache entry for %s: %s",
                    cache_key,
                    exc,
                )

        track_api_cache_miss()
        url = f"{GITHUB_API_BASE}/{owner}/{repo}/commits/{commit_hash}"
        try:
            response = make_github_api_request(
                url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                timeout=GITHUB_API_TIMEOUT,
            )
            commit_data = response.json()
            timestamp_str = (
                commit_data.get("commit", {}).get("committer", {}).get("date")
            )
            if not timestamp_str:
                return None
            timestamp = _parse_iso_datetime_utc(timestamp_str)
            if timestamp is None:
                return None
            cache[cache_key] = [timestamp.isoformat(), now.isoformat()]
            self.atomic_write_json(cache_file, cache)
            return timestamp
        except (_RequestException, ValueError, TypeError, KeyError) as exc:
            logger.debug(
                "Could not fetch commit timestamp for %s: %s", commit_hash, exc
            )
            return None


_cache_lock = None
_commit_cache_file: Optional[str] = None
_releases_cache_file: Optional[str] = None
_commit_cache_loaded = False
_releases_cache_loaded = False
_commit_timestamp_cache: Dict[str, Any] = {}
_releases_cache: Dict[str, Any] = {}
_prerelease_dir_cache_file: Optional[str] = None
_prerelease_dir_cache_loaded = False
_prerelease_dir_cache: Dict[str, Any] = {}


def _ensure_cache_dir() -> str:
    import platformdirs

    cache_dir = platformdirs.user_cache_dir("fetchtastic")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _get_commit_cache_file() -> str:
    global _commit_cache_file
    if _commit_cache_file is None:
        _commit_cache_file = os.path.join(_ensure_cache_dir(), "commit_timestamps.json")
    return _commit_cache_file


def _get_releases_cache_file() -> str:
    global _releases_cache_file
    if _releases_cache_file is None:
        _releases_cache_file = os.path.join(_ensure_cache_dir(), "releases.json")
    return _releases_cache_file


def _get_prerelease_dir_cache_file() -> str:
    global _prerelease_dir_cache_file
    if _prerelease_dir_cache_file is None:
        _prerelease_dir_cache_file = os.path.join(
            _ensure_cache_dir(), "prerelease_dirs.json"
        )
    return _prerelease_dir_cache_file


def _load_json_cache_with_expiry(
    cache_file_path: str,
    expiry_hours: Optional[float],
    cache_entry_validator: Callable[[Dict[str, Any]], bool],
    entry_processor: Callable[[Dict[str, Any], datetime], Any],
    cache_name: str,
) -> Dict[str, Any]:
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

                cached_at = _parse_iso_datetime_utc(cache_entry.get("cached_at"))
                if cached_at is None:
                    continue
                age = current_time - cached_at
                if (
                    expiry_hours is not None
                    and age.total_seconds() >= expiry_hours * 3600
                ):
                    continue

                loaded[cache_key] = entry_processor(cache_entry, cached_at)
            except (ValueError, TypeError, KeyError):
                continue

        return loaded
    except (IOError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _get_cache_lock():
    global _cache_lock
    if _cache_lock is None:
        import threading

        _cache_lock = threading.Lock()
    return _cache_lock


def _load_commit_cache() -> None:
    """
    Load commit timestamp cache with expiry checking for parity with CacheManager.

    This function now uses the unified expiry-aware implementation to ensure
    consistency between module-level and CacheManager-based cache access.
    """
    global _commit_cache_loaded, _commit_timestamp_cache
    with _get_cache_lock():
        if _commit_cache_loaded:
            return
        _commit_cache_loaded = True

        try:
            manager = CacheManager(_ensure_cache_dir())
            _commit_timestamp_cache = manager.read_commit_timestamp_cache()
        except (IOError, OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            _commit_timestamp_cache = {}


def _load_releases_cache() -> None:
    global _releases_cache_loaded, _releases_cache
    with _get_cache_lock():
        if _releases_cache_loaded:
            return
        _releases_cache_loaded = True

        try:
            manager = CacheManager(_ensure_cache_dir())
            cache_file = _get_releases_cache_file()
            data = manager.read_json(cache_file)
            _releases_cache = data if isinstance(data, dict) else {}
        except (IOError, OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            _releases_cache = {}


def _clear_cache_generic(
    cache: Dict[str, Any], file_getter: Callable[[], str], name: str
) -> None:
    cache.clear()
    try:
        cache_file = file_getter()
        if os.path.exists(cache_file):
            os.remove(cache_file)
    except OSError as exc:
        logger.debug("Could not clear %s cache: %s", name, exc)


def _load_prerelease_dir_cache() -> None:
    global _prerelease_dir_cache, _prerelease_dir_cache_loaded

    if _prerelease_dir_cache_loaded:
        return

    def validate_prerelease_entry(cache_entry: Dict[str, Any]) -> bool:
        return (
            isinstance(cache_entry, dict)
            and "directories" in cache_entry
            and "cached_at" in cache_entry
        )

    def process_prerelease_entry(cache_entry: Dict[str, Any], cached_at: datetime):
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

    with _get_cache_lock():
        if _prerelease_dir_cache_loaded:
            return
        if isinstance(loaded_data, dict):
            _prerelease_dir_cache.update(loaded_data)
        _prerelease_dir_cache_loaded = True


def _save_prerelease_dir_cache() -> None:
    cache_file = _get_prerelease_dir_cache_file()
    try:
        with _get_cache_lock():
            cache_data = {
                cache_key: {
                    "directories": directories,
                    "cached_at": cached_at.isoformat(),
                }
                for cache_key, (directories, cached_at) in _prerelease_dir_cache.items()
            }

        if not _atomic_write_json(cache_file, cache_data):
            logger.warning(
                "Failed to save prerelease directory cache to %s", cache_file
            )
    except (IOError, OSError):
        logger.warning("Could not save prerelease directory cache")
