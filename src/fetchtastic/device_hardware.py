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
import requests

from fetchtastic.constants import (
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
)
from fetchtastic.utils import get_user_agent

logger = logging.getLogger(__name__)

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
        Initialize the DeviceHardwareManager.

        Args:
            cache_dir: Directory for caching API responses
            api_url: URL for the device hardware API
            cache_hours: Hours to cache API responses
            timeout_seconds: Timeout for API requests
            enabled: Whether to use API fetching (False = fallback only)
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
        Get the set of device patterns (platformioTarget values).

        Returns cached data if available and fresh, otherwise fetches from API.
        Falls back to hardcoded patterns if API is unavailable.

        Returns:
            Set of device pattern strings (e.g., {"rak4631", "tbeam", ...})
        """
        if self._device_patterns is None or self._is_cache_expired():
            self._device_patterns = self._load_device_patterns()

        return self._device_patterns

    def is_device_pattern(self, user_pattern: str) -> bool:
        """
        Check if a user pattern matches any known device pattern.

        Args:
            user_pattern: Pattern from user config (e.g., "tbeam-", "rak4631-")

        Returns:
            True if this appears to be a device pattern
        """
        device_patterns = self.get_device_patterns()

        # Remove trailing dash/underscore for comparison if present and normalize case
        clean_pattern = user_pattern.rstrip("-_ ").lower()

        for device_pattern in device_patterns:
            # Check if pattern matches exactly or is contained in device pattern (case-insensitive)
            device_pattern_lower = device_pattern.lower()
            if clean_pattern == device_pattern_lower or (
                len(clean_pattern) >= 2
                and (
                    device_pattern_lower.startswith(f"{clean_pattern}-")
                    or device_pattern_lower.startswith(f"{clean_pattern}_")
                )
            ):
                return True

        return False

    def _load_device_patterns(self) -> Set[str]:
        """
        Load device patterns from cache or API.

        Returns:
            Set of device pattern strings
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
        Fetch device hardware data from the Meshtastic API.

        Returns:
            Set of device patterns or None if fetch failed
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

            headers = {"User-Agent": get_user_agent()}
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
        except Exception:
            logger.exception("Unexpected error fetching device hardware data")
            return None
        else:
            self._last_fetch_time = time.time()
            return device_patterns

    def _load_from_cache(self) -> Optional[Set[str]]:
        """
        Load device patterns from cache file.

        Returns:
            Set of device patterns or None if cache invalid/missing
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

            if not patterns or not timestamp:
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
        Save device patterns to cache file.

        Args:
            device_patterns: Set of device patterns to cache
        """
        try:
            cache_data = {
                "device_patterns": list(device_patterns),
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
        Check if the cache has expired.

        Returns:
            True if cache is expired or no fetch time recorded
        """
        if self._last_fetch_time is None:
            return True

        age_hours = (time.time() - self._last_fetch_time) / 3600
        return age_hours >= self.cache_hours

    def clear_cache(self) -> None:
        """Clear the cached device hardware data."""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
            self._device_patterns = None
            self._last_fetch_time = None
            logger.info("Device hardware cache cleared")
        except IOError as e:
            logger.warning(f"Failed to clear device hardware cache: {e}")
