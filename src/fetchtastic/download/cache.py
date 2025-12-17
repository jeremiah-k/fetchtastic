"""
Cache Management for Fetchtastic Download Subsystem

This module provides caching infrastructure for release metadata,
commit timestamps, and other download-related data.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import requests

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
    """
    Parse an ISO 8601 datetime value and produce a timezone-aware UTC datetime.

    Parameters:
        value (Any): An ISO 8601 datetime representation (commonly a string). Falsey values or values that cannot be parsed will be treated as absent.

    Returns:
        datetime: A timezone-aware `datetime` normalized to UTC if parsing succeeds, `None` otherwise.
    """
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

    def get_cache_file_path(self, cache_name: str, suffix: str = ".json") -> str:
        """
        Return the full path for a cache file stored under the cache directory.

        Accepts an optional suffix that is appended when the base name does not
        already end with it, allowing callers to pass either raw basenames or
        full filenames directly.
        """
        suffix = suffix or ""
        suffix_to_append = ""
        if suffix and not cache_name.lower().endswith(suffix.lower()):
            suffix_to_append = suffix
        return os.path.join(self.cache_dir, f"{cache_name}{suffix_to_append}")

    def _get_default_cache_dir(self) -> str:
        """
        Return the default cache directory path used by Fetchtastic.

        Returns:
            cache_dir (str): Absolute path to the platform-appropriate user cache directory for "fetchtastic".
        """
        import platformdirs

        # Legacy fetchtastic uses platformdirs.user_cache_dir("fetchtastic")
        # and older cache/tracking files live there; keep parity.
        return platformdirs.user_cache_dir("fetchtastic")

    def _ensure_cache_dir_exists(self) -> None:
        """
        Ensure the manager's cache directory exists, creating it if necessary.

        Raises:
            OSError: If the directory cannot be created or is otherwise inaccessible.
        """
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create cache directory {self.cache_dir}: {e}")
            raise

    def atomic_write(
        self, file_path: str, writer_func: Callable[[Any], None], suffix: str = ".tmp"
    ) -> bool:
        """
        Write content to a file atomically.

        Parameters:
            file_path (str): Destination filesystem path for the final file.
            writer_func (Callable[[Any], None]): Callable that receives an open text file-like object and writes the desired content to it.
            suffix (str): Suffix to use for the temporary working file.

        Returns:
            bool: `True` if the file was written and moved into place successfully, `False` otherwise.
        """
        return _atomic_write(file_path, writer_func, suffix)

    def atomic_write_text(self, file_path: str, content: str) -> bool:
        """
        Atomically write text content to a file.

        Returns:
            bool: True if the file was written successfully, False otherwise.
        """

        def _write_text_content(f):
            """
            Write the preset text content to the provided writable file-like object.

            Parameters:
                f (io.TextIOBase): A writable file-like object opened for text writing.
            """
            f.write(content)

        return self.atomic_write(file_path, _write_text_content, suffix=".txt")

    def atomic_write_json(self, file_path: str, data: Dict) -> bool:
        """
        Atomically write the given mapping to the specified path as JSON.

        Parameters:
            file_path (str): Destination filesystem path for the JSON file.
            data (Dict): Mapping to serialize to JSON.

        Returns:
            bool: `True` if the file was written successfully, `False` otherwise.
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
        Read a JSON file and return its contents with legacy keys remapped to new keys.

        Parameters:
            file_path (str): Path to the JSON file.
            key_mapping (Optional[Dict[str, str]]): Mapping from legacy key names to new key names. If a legacy key exists and the corresponding new key is absent, the value is copied to the new key.

        Returns:
            Optional[Dict]: Parsed JSON object with remapped keys, or None if the file could not be read.
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
        Return the parsed rate-limit summary stored at the given cache file path.

        Parameters:
            cache_file (str): Path to the JSON cache file containing the rate-limit summary.

        Returns:
            dict or None: The parsed JSON object from the cache file, or None if the file is missing, unreadable, or malformed.
        """
        return self.read_json(cache_file)

    def cache_with_expiry(
        self, cache_file: str, data: Dict, expiry_hours: float
    ) -> bool:
        """
        Writes the provided data to a cache file and records when it was cached and when it will expire.

        Parameters:
            cache_file (str): Path to the cache file to write.
            data (Dict): Value to store under the `"data"` key in the cache file.
            expiry_hours (float): Number of hours from now after which the cache is considered expired.

        Returns:
            bool: `true` if the cache file was written successfully, `false` otherwise.
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
        Return directory names under the meshtastic.github.io repository path, using a short TTL on-disk cache.

        Retrieves directory entries for the given repository path; if a fresh cached entry exists it will be returned, otherwise the GitHub Contents API is queried and the cache is updated. On malformed responses or request failures an empty list is returned.

        Parameters:
            path (str): Repository path relative to the site root (leading/trailing slashes are ignored).
            force_refresh (bool): If True, skip any on-disk cache and fetch fresh data from the API.
            github_token (Optional[str]): Personal access token to use for the GitHub API call; if None an environment token may be used.
            allow_env_token (bool): Whether to allow using a token from the environment when `github_token` is not provided.

        Returns:
            List[str]: A list of directory names (strings) found at the requested path; returns an empty list on error or if no directories are present.
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
        except requests.RequestException as exc:
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
        Return the repository contents for a meshtastic.github.io path, using a TTL-backed on-disk cache.

        Parameters:
            path (str): Repository path relative to the site root. Leading/trailing slashes are ignored.
            force_refresh (bool): If True, bypass the on-disk cache and fetch from the GitHub API.
            github_token (Optional[str]): Personal access token to use for the GitHub API request, if provided.
            allow_env_token (bool): If True, permit using an authentication token sourced from the environment when no explicit token is provided.

        Returns:
            List[Dict[str, Any]]: A list of dictionary entries as returned by the GitHub Contents API for the path.
            Returns an empty list if the API response is malformed, the request fails, or no entries are available.
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
        except requests.RequestException as exc:
            logger.debug("Could not fetch repo contents for %s: %s", api_url, exc)
            return []

    def clear_cache(self, cache_file: str) -> bool:
        """
        Delete the specified cache file from disk.

        Returns:
            True if the file was removed or did not exist, False if an error occurred.
        """
        try:
            if os.path.exists(cache_file):
                os.remove(cache_file)
            return True
        except OSError as e:
            logger.error(f"Could not clear cache file {cache_file}: {e}")
            return False

    @staticmethod
    def build_url_cache_key(url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a stable cache key by appending URL-encoded query parameters to a base URL.

        Parameters:
            params (Optional[Dict[str, Any]]): Mapping of query parameter names to values; entries with value `None` are omitted.

        Returns:
            str: The original `url` if `params` is None or contains no non-None values, otherwise `url` followed by `?` and the URL-encoded parameters.
        """
        if not params:
            return url
        filtered = {k: v for k, v in params.items() if v is not None}
        if not filtered:
            return url
        return f"{url}?{urlencode(filtered)}"

    def _get_releases_cache_file(self) -> str:
        """
        Selects the releases cache file path, preferring the current primary file and falling back to a legacy filename when appropriate.

        Returns:
            str: Path to the chosen releases cache file. Returns the primary "releases.json" path if it exists or if the legacy "releases_cache.json" does not exist; otherwise returns the legacy path.
        """
        primary = os.path.join(self.cache_dir, "releases.json")
        legacy = os.path.join(self.cache_dir, "releases_cache.json")
        return (
            primary if os.path.exists(primary) or not os.path.exists(legacy) else legacy
        )

    def read_releases_cache_entry(
        self, url_cache_key: str, *, expiry_seconds: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Read a cached GitHub releases entry for a specific request key, validating expiry.

        Reads the legacy multi-entry releases cache and returns the stored releases list if the
        entry for `url_cache_key` exists, is well-formed, and its cached timestamp is newer than
        `expiry_seconds` ago.

        Cache file schema:
          { "<url>?per_page=n": { "releases": [...], "cached_at": "<iso-8601 UTC>" }, ... }

        Parameters:
            url_cache_key (str): The stable cache key for the request (typically a URL with query).
            expiry_seconds (int): Maximum allowed age of the cache entry in seconds.

        Returns:
            Optional[List[Dict[str, Any]]]: The cached `releases` list if present and not expired, `None` otherwise.
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
        """
        Store a list of release entries under a URL-derived cache key in the releases cache file.

        Writes the provided releases list into the releases cache, keyed by `url_cache_key`, and records the current UTC timestamp as `cached_at` to indicate when the entry was saved.

        Parameters:
            url_cache_key (str): Stable cache key derived from a request URL and parameters.
            releases (List[Dict[str, Any]]): List of release objects to persist in the cache.
        """
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
        Atomically write a JSON file containing the provided data and a UTC ISO 8601 timestamp.

        Parameters:
            file_path (str): Destination path for the JSON file.
            data (Dict): Mapping to serialize into the JSON file.
            timestamp_key (str): Key under which the current UTC ISO 8601 timestamp will be stored in the data.

        Returns:
            `true` if the file was written successfully, `false` otherwise.
        """
        # Add timestamp to data
        data_with_timestamp = data.copy()
        data_with_timestamp[timestamp_key] = datetime.now(timezone.utc).isoformat()

        return self.atomic_write_json(file_path, data_with_timestamp)

    def read_with_expiry(self, file_path: str, expiry_hours: float) -> Optional[Dict]:
        """
        Read cached data from a JSON file and return it only if it is present and not expired.

        Parameters:
            file_path (str): Path to the cache JSON file to read.
            expiry_hours (float): Expiration threshold in hours; entries older than this are considered expired.

        Returns:
            Optional[Dict]: The parsed cache data when present and not expired, otherwise `None`.
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
        Compute the UTC expiry timestamp for a cache entry expiry_hours hours from now.

        Parameters:
            cache_file (str): Path to the cache file (not used when calculating the timestamp).
            expiry_hours (float): Number of hours from now when the cache should expire.

        Returns:
            str: Expiry timestamp in UTC as an ISO 8601 formatted string.
        """
        return (datetime.now(timezone.utc) + timedelta(hours=expiry_hours)).isoformat()

    def validate_cache_format(self, cache_data: Dict, required_keys: List[str]) -> bool:
        """
        Check that the given cache mapping contains all required top-level keys.

        Parameters:
            cache_data (Dict): Mapping representing cached data to validate.
            required_keys (List[str]): Keys that must be present in cache_data.

        Returns:
            bool: `True` if every key in `required_keys` exists in `cache_data`, `False` otherwise.
        """
        for key in required_keys:
            if key not in cache_data:
                logger.warning(f"Missing required key in cache data: {key}")
                return False
        return True

    def read_commit_timestamp_cache(self) -> Dict[str, Any]:
        """
        Return cached commit timestamps that have not expired.

        Reads the on-disk commit_timestamps.json, accepts both legacy dict entries
        (`{"timestamp": "...", "cached_at": "..."}`) and the newer list form
        (`[timestamp_iso, cached_at_iso]`), filters out entries older than
        COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS, and normalizes retained entries to the
        new list format.

        Returns:
            Dict[str, list]: Mapping of cache key to `[timestamp_iso, cached_at_iso]`
            for entries still within the expiry window.
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
        Retrieve the committer timestamp for a GitHub commit, using an on-disk cache when available.

        Looks up a cached timestamp in commit_timestamps.json and returns it if present and not expired; otherwise fetches the commit from the GitHub API, caches the ISO timestamp together with the fetch time, and returns the parsed UTC datetime. Cache entries expire after COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS; setting `force_refresh` bypasses the cache.

        Parameters:
            owner (str): Repository owner (GitHub user or organization).
            repo (str): Repository name.
            commit_hash (str): Full or short commit SHA to query.
            github_token (Optional[str]): Personal access token to use for the GitHub API request; if omitted and `allow_env_token` is True, an environment token may be used.
            allow_env_token (bool): If True, allow using a token from environment-based configuration when `github_token` is not provided.
            force_refresh (bool): If True, ignore any valid cached entry and fetch from the GitHub API.

        Returns:
            Optional[datetime]: The commit committer datetime in UTC if available and parseable, `None` on fetch or parse failure.
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
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            logger.debug(
                "Could not fetch commit timestamp for %s: %s", commit_hash, exc
            )
            return None


def _load_json_cache_with_expiry(
    cache_file_path: str,
    expiry_hours: Optional[float],
    cache_entry_validator: Callable[[Dict[str, Any]], bool],
    entry_processor: Callable[[Dict[str, Any], datetime], Any],
    cache_name: str,
) -> Dict[str, Any]:
    """
    Load JSON cache entries from a file, validate each entry, and return processed entries that are not expired.

    Parameters:
        cache_file_path (str): Path to the JSON cache file.
        expiry_hours (Optional[float]): Maximum age in hours for entries; if None, entries do not expire.
        cache_entry_validator (Callable[[Dict[str, Any]], bool]): Function that returns True for entries with the expected structure.
        entry_processor (Callable[[Dict[str, Any], datetime], Any]): Function that converts a valid cache entry and its parsed `cached_at` datetime into the value stored in the returned mapping.
        cache_name (str): Human-readable name for the cache used for log messages.

    Returns:
        Dict[str, Any]: Mapping of cache keys to processed entry values for entries that passed validation and are not expired.
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
