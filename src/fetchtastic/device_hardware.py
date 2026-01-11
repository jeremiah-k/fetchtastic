"""
Device Hardware Management Module

This module handles fetching, caching, and managing device hardware data
from the Meshtastic API to enable dynamic pattern matching for firmware downloads.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Set
from urllib.parse import urlparse

import platformdirs
import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
)
from fetchtastic.utils import get_user_agent

logger = logging.getLogger(__name__)

# Minimum length for device pattern prefix matching to prevent overly broad matches
MIN_DEVICE_PATTERN_PREFIX_LEN = 2

# Default configuration
DEFAULT_API_URL = DEVICE_HARDWARE_API_URL
DEFAULT_CACHE_HOURS = DEVICE_HARDWARE_CACHE_HOURS
DEFAULT_TIMEOUT_SECONDS = 10

# Fallback device patterns if API is unavailable
FALLBACK_DEVICE_PATTERNS = {
    "rak4631",
    "tbeam",
    "t1000-e",
    "tlora-v2-1-1_6",
    "heltec",
    "nano-g1",
    "station-g1",
    "station-g2",
    "t-deck",
    "canaryone",
    "tracker-t1000-e",
    "seeed",
    "pico",
    "wio",
    "m5stack",
    "hydra",
    "chatter2",
    "unphone",
    "tracksenger",
    "radiomaster",
    "thinknode",
}


class DeviceHardwareManager:
    """
    Manages device hardware data from the Meshtastic API.

    Provides caching, error handling, and fallback mechanisms for
    fetching device information used in pattern matching.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        api_url: str = DEFAULT_API_URL,
        cache_hours: int = DEFAULT_CACHE_HOURS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        enabled: bool = True,
    ):
        """
        Initialize a DeviceHardwareManager that fetches, caches, and serves device hardware patterns.

        Creates (if necessary) the on-disk cache directory and file (device_hardware.json), and initializes in-memory cache state.

        Parameters:
            cache_dir: Directory where the cache file "device_hardware.json" will be stored. If None, a per-user cache directory is used.
            api_url: Meshtastic device hardware API URL to query for platform targets.
            cache_hours: Hours that cached data remains valid before a refresh is attempted.
            timeout_seconds: HTTP request timeout (seconds) when fetching data from the API.
            enabled: When False, API fetching is disabled and the manager will rely on cached data or built-in fallbacks only.
        """
        self.api_url = api_url
        self.cache_hours = cache_hours
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled

        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path(platformdirs.user_cache_dir("fetchtastic"))
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "device_hardware.json"

        # Cached data
        self._device_patterns: Optional[Set[str]] = None
        self._last_fetch_time: Optional[float] = None

    def get_device_patterns(self) -> Set[str]:
        """
        Get the current set of device patterns (platformioTarget values).

        If an in-memory cache is missing or expired this loads patterns (from disk cache, the API, or the built-in fallback), updates the in-memory cache, and returns the result. The returned set is a copy to prevent external mutation of the internal cache.

        Returns:
            Set[str]: A set of normalized device pattern strings (e.g., {"rak4631", "tbeam"}).
        """
        if self._device_patterns is None or self._is_cache_expired():
            self._device_patterns = self._load_device_patterns()

        # hand back a copy to keep cache safe from outside mutation
        return set(self._device_patterns)

    def is_device_pattern(self, user_pattern: str) -> bool:
        """
        Return True if the given user-supplied pattern matches a known device pattern.

        The input is normalized by trimming trailing dashes, underscores, and spaces and lowercased.
        Matches succeed if the normalized pattern exactly equals a known pattern (case-insensitive)
        or if a known pattern begins with the normalized pattern followed by a dash or underscore
        (e.g., "tbeam" matches "tbeam-something" or "tbeam_something").

        Parameters:
            user_pattern (str): User-provided pattern to test (may include trailing '-' or '_').

        Returns:
            bool: True when the pattern matches a known device pattern, otherwise False.
        """
        device_patterns = self.get_device_patterns()

        # Remove trailing dash/underscore for comparison if present and normalize case
        clean_pattern = user_pattern.rstrip("-_ ").lower()

        for device_pattern in device_patterns:
            # Check if pattern matches exactly or is contained in device pattern (case-insensitive)
            device_pattern_lower = device_pattern.lower()
            if clean_pattern == device_pattern_lower or (
                len(clean_pattern) >= MIN_DEVICE_PATTERN_PREFIX_LEN
                and (
                    device_pattern_lower.startswith(f"{clean_pattern}-")
                    or device_pattern_lower.startswith(f"{clean_pattern}_")
                )
            ):
                return True

        return False

    def _load_device_patterns(self) -> Set[str]:
        """
        Load and return known device hardware pattern strings, preferring fresh cache and falling back to the API or built-in defaults.

        Attempts the following in order:
        1. Return on-disk cached patterns if present and not expired.
        2. If enabled, fetch patterns from the configured API, save them to cache, and return them.
        3. If the API is unavailable but a cache exists (even if expired), return the cached patterns.
        4. As a last resort, return a copy of FALLBACK_DEVICE_PATTERNS.

        Side effects:
        - May call _fetch_from_api() and _save_to_cache() when refreshing from the API.
        - May update self._last_fetch_time (e.g., when falling back to built-in defaults).

        Returns:
            Set[str]: A set of device pattern strings.
        """
        # Try to load from cache first
        cached_data = self._load_from_cache()
        if cached_data and not self._is_cache_expired():
            logger.debug("Using cached device hardware data")
            return cached_data

        # Try to fetch from API if enabled
        if self.enabled:
            api_data = self._fetch_from_api()
            if api_data:
                logger.info(
                    f"Fetched {len(api_data)} device patterns from Meshtastic API"
                )
                self._save_to_cache(api_data)
                return api_data

        # Fall back to cached data even if expired
        if cached_data:
            logger.warning(
                "Using expired cached device hardware data (API unavailable)"
            )
            return cached_data

        # Final fallback to hardcoded patterns
        logger.warning("Using fallback device patterns (API and cache unavailable)")
        # Set timestamp to prevent repeated warnings in same process
        self._last_fetch_time = time.time()
        return FALLBACK_DEVICE_PATTERNS.copy()

    def _fetch_from_api(self) -> Optional[Set[str]]:
        """
        Fetch device hardware data from the Meshtastic API and return discovered device pattern strings.

        Validates that the configured API URL uses HTTP/HTTPS and has a network location, sends a GET
        request with a User-Agent header, and parses the JSON response. Extracts non-empty string
        values of the `platformioTarget` field from objects in the top-level JSON array and returns
        them as a set.

        Returns:
            A set of device pattern strings on success, or None if the URL is invalid, the HTTP
            request fails, the response is not valid JSON, or no valid `platformioTarget` values are
            found.

        Side effects:
            On success updates self._last_fetch_time to the current timestamp; does not modify the
            cache file (saving is handled elsewhere).
        """
        try:
            logger.debug(f"Fetching device hardware data from {self.api_url}")

            # Validate URL scheme to prevent SSRF attacks
            parsed_url = urlparse(self.api_url)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                logger.error(
                    f"Unsupported or invalid URL for device hardware API: {self.api_url}"
                )
                return None

            headers = {
                "User-Agent": get_user_agent(),
                "Accept": "application/json",
            }
            response = requests.get(
                self.api_url, headers=headers, timeout=self.timeout_seconds
            )
            response.raise_for_status()
            data = response.json()

            # Extract platformioTarget values
            device_patterns = set()
            for device in data:
                if isinstance(device, dict) and "platformioTarget" in device:
                    target = device["platformioTarget"]
                    if target and isinstance(target, str):
                        device_patterns.add(target)

            if not device_patterns:
                logger.error("No valid device patterns found in API response")
                return None

        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch device hardware data: {e}")
            return None
        except json.JSONDecodeError:
            logger.exception("Invalid JSON response from API")
            return None
        except (TypeError, KeyError) as e:
            logger.exception(f"Error processing device hardware data: {e}")
            return None
        except Exception:
            logger.exception("Unexpected error fetching device hardware data")
            return None
        else:
            self._last_fetch_time = time.time()
            return device_patterns

    def _load_from_cache(self) -> Optional[Set[str]]:
        """
        Load device patterns from the on-disk cache file if it exists and is valid.

        Reads JSON from self.cache_file and validates that it is a dict containing
        a non-empty "device_patterns" list of non-empty strings and a numeric
        "timestamp". On success, sets self._last_fetch_time to the cached timestamp
        and returns the patterns as a set of strings. Returns None if the cache
        file is missing, malformed, missing required fields, contains no valid
        patterns, or if any I/O/JSON decoding errors occur.
        """
        try:
            if not self.cache_file.exists():
                return None

            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # Validate cache structure
            if not isinstance(cache_data, dict):
                return None

            patterns = cache_data.get("device_patterns")
            timestamp = cache_data.get("timestamp")

            if not patterns or timestamp is None:
                return None

            # Validate patterns is a list of strings
            if not isinstance(patterns, list):
                return None

            # Filter out invalid patterns and ensure all are non-empty strings
            valid_patterns = {p for p in patterns if isinstance(p, str) and p.strip()}
            if not valid_patterns:
                return None

            # Validate timestamp is numeric
            try:
                self._last_fetch_time = float(timestamp)
            except (ValueError, TypeError):
                return None

        except (json.JSONDecodeError, IOError, KeyError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to load device hardware cache: {e}")
            return None
        else:
            return valid_patterns

    def _save_to_cache(self, device_patterns: Set[str]) -> None:
        """
        Write the given device patterns to the manager's cache file using an atomic replace.

        The cache is written as JSON with keys:
        - "device_patterns": list of pattern strings (converted from the provided set),
        - "timestamp": the manager's last fetch time or the current time if unset,
        - "api_url": the API URL used.

        The write is performed to a temporary file and then atomically replaced into place; IO errors are caught and logged.
        """
        try:
            cache_data = {
                "device_patterns": sorted(device_patterns),
                "timestamp": self._last_fetch_time or time.time(),
                "api_url": self.api_url,
            }

            tmp = self.cache_file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
            os.replace(tmp, self.cache_file)

            logger.debug(f"Cached {len(device_patterns)} device patterns")

        except IOError as e:
            logger.warning(f"Failed to save device hardware cache: {e}")

    def _is_cache_expired(self) -> bool:
        """
        Return whether the cached device patterns are considered expired.

        Returns:
            bool: True if no previous fetch time is recorded or the elapsed time since
            the last successful fetch (in hours) is greater than or equal to the
            configured cache_hours; otherwise False.
        """
        if self._last_fetch_time is None:
            return True

        age_hours = (time.time() - self._last_fetch_time) / 3600
        return age_hours >= self.cache_hours

    def clear_cache(self) -> None:
        """
        Clear the on-disk and in-memory device hardware cache.

        Removes the cache file (if present) and resets the in-memory device pattern set and last-fetch timestamp.
        Does not raise on filesystem errors; failures are logged and the in-memory state may still be reset.
        """
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
            self._device_patterns = None
            self._last_fetch_time = None
            logger.info("Device hardware cache cleared")
        except OSError as e:
            logger.warning(f"Failed to clear device hardware cache: {e}")
