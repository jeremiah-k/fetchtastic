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
    Parse an ISO 8601 timestamp and normalize it to UTC.

    Parameters:
        value (Any): An ISO 8601 datetime representation (commonly a string). Falsey values or unparsable values are treated as absent.

    Returns:
        A timezone-aware datetime in UTC if parsing succeeds, `None` otherwise.
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
        Initialize the CacheManager with a cache directory.

        Parameters:
            cache_dir (Optional[str]): Path to use for on-disk caches. If None, a default user cache directory is selected and created if missing.
        """
        self.cache_dir = cache_dir or self._get_default_cache_dir()
        self._ensure_cache_dir_exists()

    def get_cache_file_path(self, cache_name: str, suffix: str = ".json") -> str:
        """
        Builds the full filesystem path for a cache file located in the manager's cache directory.

        Parameters:
            cache_name (str): Base name or filename for the cache entry; may be a raw basename or already include a suffix.
            suffix (str): Suffix to ensure on the returned filename (e.g., ".json"); appended only if `cache_name` does not already end with it.

        Returns:
            str: Absolute path to the cache file within the configured cache directory.
        """
        suffix = suffix or ""
        suffix_to_append = ""
        if suffix and not cache_name.lower().endswith(suffix.lower()):
            suffix_to_append = suffix
        return os.path.join(self.cache_dir, f"{cache_name}{suffix_to_append}")

    def _get_default_cache_dir(self) -> str:
        """
        Get the platform-appropriate user cache directory for "fetchtastic".

        Returns:
            cache_dir (str): Absolute path to the user cache directory used by Fetchtastic.
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
        Write text content to a file atomically.

        Performs an atomic write (via a temporary file and rename) to ensure the target
        file is replaced only after the complete content has been written.

        Returns:
            bool: `True` if the file was written and moved into place, `False` otherwise.
        """

        def _write_text_content(f):
            """
            Write preset text content into the provided writable text file-like object.

            Parameters:
                f (io.TextIOBase): A writable text file-like object that will receive the content.
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
        Load and parse JSON from the given file path.

        Returns:
            dict: Parsed JSON object, or `None` if the file is missing or cannot be read/decoded.
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
        Load and parse a rate-limit summary from a JSON cache file.

        Parameters:
            cache_file (str): Path to the JSON cache file containing the rate-limit summary.

        Returns:
            dict or None: Parsed JSON object with the rate-limit summary, or `None` if the file is missing, unreadable, or malformed.
        """
        return self.read_json(cache_file)

    def cache_with_expiry(
        self, cache_file: str, data: Dict, expiry_hours: float
    ) -> bool:
        """
        Store `data` in `cache_file` along with UTC `cached_at` and `expires_at` ISO‑8601 timestamps.

        Parameters:
            cache_file (str): Path to the JSON cache file to write.
            data (Dict): Value to store under the "data" key in the file.
            expiry_hours (float): Hours from now after which `expires_at` is set.

        Returns:
            bool: True if the cache file was written successfully, False otherwise.
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
        Retrieve the cached value stored under the "data" key if the cache file exists and has not expired.

        If the cache record contains a missing or malformed "expires_at" timestamp, the entry is treated as non-expiring. If the file is missing, unreadable, or the expiry time has passed, the function returns None.

        Returns:
            The dict stored under the "data" key, or `None` if the cache is absent or expired.
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
            # If expiry is malformed, treat the entry as non-expiring (legacy tolerant) and log it for diagnostics.
            logger.debug(
                "Malformed expiry timestamp in cache file %s; treating entry as non-expiring",
                cache_file,
            )

        return cache_data.get("data")

    def _get_cached_github_data(
        self,
        cache_key: str,
        cache_file: str,
        data_field_name: str,
        fetcher_func: Callable[[], Any],
        *,
        force_refresh: bool = False,
        cache_expiry_seconds: int = FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
        path_description: str = "",
    ) -> Any:
        """
        Fetch GitHub-derived data using a TTL-backed cache and update the cache on miss or expiry.

        Parameters:
            cache_key (str): Key identifying the entry inside the JSON cache file.
            cache_file (str): Path to the JSON cache file storing multiple entries.
            data_field_name (str): Field name under the cache entry where the fetched data is stored.
            fetcher_func (Callable[[], Any]): Zero-argument function that fetches fresh data from the GitHub API.
            force_refresh (bool): If True, bypass any existing cached entry and fetch fresh data.
            cache_expiry_seconds (int): Time-to-live for cache entries in seconds.
            path_description (str): Short description for logging context (e.g., "repo contents for /path").

        Returns:
            Any: The data returned by `fetcher_func` and stored under `data_field_name` in the cache, or an empty list on fetch/parse errors.
        """
        now = datetime.now(timezone.utc)

        cache = self.read_json(cache_file)
        if not isinstance(cache, dict):
            cache = {}

        cached = cache.get(cache_key) if not force_refresh else None
        if isinstance(cached, dict) and not force_refresh:
            data = cached.get(data_field_name)
            cached_at_raw = cached.get("cached_at")
            if data is not None and cached_at_raw:
                cached_at = _parse_iso_datetime_utc(cached_at_raw)
                if cached_at:
                    age_s = (now - cached_at).total_seconds()
                    if age_s < cache_expiry_seconds:
                        logger.debug(
                            "Using cached %s (cached %.0fs ago)",
                            path_description or "data",
                            age_s,
                        )
                        return data
                    logger.debug(
                        "Cache stale for %s (age %.0fs >= %ss); refreshing",
                        path_description or "data",
                        age_s,
                        cache_expiry_seconds,
                    )

        try:
            fresh_data = fetcher_func()
            cache[cache_key] = {
                data_field_name: fresh_data,
                "cached_at": now.isoformat(),
            }
            self.atomic_write_json(cache_file, cache)
            return fresh_data
        except (ValueError, KeyError, TypeError) as e:
            # Note: The specific error message will be logged by the fetcher_func
            # to maintain context about what operation failed
            logger.debug(
                "Error parsing response for %s: %s", path_description or "data", e
            )
            return []
        except requests.RequestException as exc:
            logger.debug("Could not fetch %s: %s", path_description or "data", exc)
            return []

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
        api_url = (
            f"{MESHTASTIC_GITHUB_IO_CONTENTS_URL}/{normalized_path}"
            if normalized_path
            else MESHTASTIC_GITHUB_IO_CONTENTS_URL
        )

        def fetch_directories() -> List[str]:
            """
            Extract directory names from a GitHub repository contents API response.

            Returns:
                list[str]: Directory names found in the last fetched API response; empty list if the response is not a list or contains no directories.
            """
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
            return [d for d in directories if isinstance(d, str)]

        try:
            return self._get_cached_github_data(
                cache_key=cache_key,
                cache_file=cache_file,
                data_field_name="directories",
                fetcher_func=fetch_directories,
                force_refresh=force_refresh,
                cache_expiry_seconds=FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
                path_description=f"prerelease directories for {normalized_path or '/'}",
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Invalid JSON or structure in GitHub response for %s: %s", api_url, e
            )
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
        Retrieve repository contents for a meshtastic.github.io path using a TTL-backed on-disk cache.

        Parameters:
            path (str): Repository path relative to the site root; leading and trailing slashes are ignored.
            force_refresh (bool): If True, bypass the on-disk cache and fetch fresh data from the GitHub API.
            github_token (Optional[str]): Personal access token to use for the GitHub API request, if provided.
            allow_env_token (bool): If True, permit using an authentication token sourced from the environment when no explicit token is provided.

        Returns:
            List[Dict[str, Any]]: A list of dictionary entries as returned by the GitHub Contents API for the path. Returns an empty list if the API response is malformed, the request fails, or no entries are available.
        """
        normalized_path = (path or "").strip("/")
        cache_key = f"contents:{normalized_path or '/'}"
        cache_file = os.path.join(self.cache_dir, "repo_contents.json")
        api_url = (
            f"{MESHTASTIC_GITHUB_IO_CONTENTS_URL}/{normalized_path}"
            if normalized_path
            else MESHTASTIC_GITHUB_IO_CONTENTS_URL
        )

        def fetch_contents() -> List[Dict[str, Any]]:
            """
            Fetches JSON data from a GitHub API endpoint and returns the parsed entries.

            Returns:
                List[Dict[str, Any]]: List of JSON objects from the response; empty list if the response is not a JSON list.
            """
            response = make_github_api_request(
                api_url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                timeout=GITHUB_API_TIMEOUT,
            )
            contents = response.json()
            if not isinstance(contents, list):
                return []
            return [c for c in contents if isinstance(c, dict)]

        try:
            return self._get_cached_github_data(
                cache_key=cache_key,
                cache_file=cache_file,
                data_field_name="contents",
                fetcher_func=fetch_contents,
                force_refresh=force_refresh,
                cache_expiry_seconds=FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS,
                path_description=f"repo contents for {normalized_path or '/'}",
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.error(
                "Invalid JSON or structure in GitHub response for %s: %s", api_url, e
            )
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
        Create a stable cache key by appending URL-encoded query parameters to the base URL.

        Parameters:
            params (Optional[Dict[str, Any]]): Mapping of query parameter names to values; entries with value `None` are omitted.

        Returns:
            The original `url` if `params` is None or contains no non-None values, otherwise the `url` followed by `?` and the URL-encoded parameters.
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
        Remove all cache files with .json or .tmp extensions from the cache directory.

        Returns:
            bool: `True` if all targeted files were removed successfully or none were present, `False` if any removal or directory access failed.
        """
        try:
            with os.scandir(self.cache_dir) as it:
                for entry in it:
                    if entry.name.endswith((".json", ".tmp")):
                        try:
                            os.remove(entry.path)
                        except OSError as e:
                            logger.error(
                                f"Could not remove cache file {entry.name}: {e}"
                            )
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
        Return the parsed JSON cache if it exists and its timestamp is not older than expiry_hours.

        Checks for a timestamp under one of the keys "last_updated", "timestamp", or "cached_at"; if present, parses it as an ISO-8601 UTC datetime and treats the entry as expired when that timestamp is more than expiry_hours in the past. If no timestamp key is present the cache is treated as valid.

        Parameters:
            file_path (str): Path to the JSON cache file to read.
            expiry_hours (float): Number of hours before a cached entry is considered expired.

        Returns:
            Optional[Dict]: The parsed cache data when present and not expired, `None` otherwise.
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
        Migrate a legacy cache file to the new cache format by remapping keys and atomically writing the result.

        Parameters:
            legacy_file_path (str): Path to the existing legacy cache file to read.
            new_file_path (str): Destination path for the migrated cache file.
            legacy_to_new_mapping (Dict[str, str]): Mapping from legacy key names to new key names; keys present in the legacy file will be copied into the new file under their mapped names when the target name is not already present.

        Returns:
            bool: `True` if the migration wrote the new cache file successfully; `False` if the legacy file was missing, the migration failed, or an error occurred.
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

    def get_cache_expiry_timestamp(self, expiry_hours: float) -> str:
        """
        Compute the UTC expiry timestamp for a cache entry expiry_hours hours from now.

        Parameters:
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
        Load and return non-expired commit timestamp entries from the on-disk cache.

        Reads commit_timestamps.json, accepts both legacy dict entries (`{"timestamp": "...", "cached_at": "..."}`)
        and the newer list form (`[timestamp_iso, cached_at_iso]`), filters out entries older than
        COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS, and normalizes retained entries to the list format.

        Returns:
            Dict[str, list]: Mapping of cache key to `[timestamp_iso, cached_at_iso]` for entries still within the expiry window.
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
            entry = cache[cache_key]
            entry_valid = (
                isinstance(entry, (list, tuple))
                and len(entry) == 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], str)
            )
            if entry_valid:
                timestamp_str, cached_at_str = entry
                cached_at = _parse_iso_datetime_utc(cached_at_str)
                timestamp = _parse_iso_datetime_utc(timestamp_str)
                if cached_at is not None and timestamp is not None:
                    age = now - cached_at
                    if (
                        age.total_seconds()
                        < COMMIT_TIMESTAMP_CACHE_EXPIRY_HOURS * 60 * 60
                    ):
                        track_api_cache_hit()
                        logger.debug(
                            "Using cached commit timestamp for %s (cached %.0fs ago)",
                            commit_hash,
                            age.total_seconds(),
                        )
                        return timestamp
                else:
                    entry_valid = False
            if not entry_valid:
                logger.debug(
                    "Ignoring invalid commit timestamp cache entry for %s", cache_key
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
    Load and return validated, non-expired entries from a JSON cache file.

    Each top-level entry is validated with `cache_entry_validator` and converted by
    `entry_processor` using the entry and its parsed `cached_at` timestamp. Entries
    with malformed structure, missing/invalid `cached_at`, or older than
    `expiry_hours` are skipped. If the file is missing, unreadable, or not a JSON
    object, an empty dict is returned.

    Parameters:
        cache_file_path (str): Path to the JSON cache file.
        expiry_hours (Optional[float]): Maximum age in hours for entries; if `None`,
            entries do not expire.
        cache_entry_validator (Callable[[Dict[str, Any]], bool]): Returns `True` for
            entries that have the expected structure and should be processed.
        entry_processor (Callable[[Dict[str, Any], datetime], Any]): Converts a
            valid cache entry and its parsed `cached_at` datetime into the value
            stored in the returned mapping.
        cache_name (str): Human-readable name for the cache used in debug messages.

    Returns:
        Dict[str, Any]: Mapping of cache keys to processed values for entries that
        passed validation and are not expired.
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
