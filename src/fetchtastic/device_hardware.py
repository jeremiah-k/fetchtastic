"""
Device Hardware Management Module

This module handles fetching, caching, and managing device hardware data
from the Meshtastic API to enable dynamic pattern matching for firmware downloads.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_API_URL = "https://api.meshtastic.org/resource/deviceHardware"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 10

# Fallback device patterns if API is unavailable
FALLBACK_DEVICE_PATTERNS = {
    "rak4631-",
    "tbeam-",
    "t1000-e-",
    "tlora-v2-1-1_6-",
    "heltec-",
    "nano-g1-",
    "station-g1-",
    "station-g2-",
    "t-deck-",
    "canaryone-",
    "tracker-t1000-e-",
    "seeed-",
    "pico-",
    "wio-",
    "m5stack-",
    "hydra-",
    "chatter2-",
    "unphone-",
    "tracksenger-",
    "radiomaster_",
    "thinknode_",
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
            cache_dir = Path.home() / ".cache" / "fetchtastic"
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

        # Remove trailing dash for comparison if present
        clean_pattern = user_pattern.rstrip("-")

        for device_pattern in device_patterns:
            # Check if pattern matches exactly or is contained in device pattern
            if clean_pattern == device_pattern or clean_pattern in device_pattern:
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
        return FALLBACK_DEVICE_PATTERNS.copy()

    def _fetch_from_api(self) -> Optional[Set[str]]:
        """
        Fetch device hardware data from the Meshtastic API.

        Returns:
            Set of device patterns or None if fetch failed
        """
        try:
            logger.debug(f"Fetching device hardware data from {self.api_url}")

            request = urllib.request.Request(self.api_url)
            request.add_header("User-Agent", "fetchtastic/1.0")

            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                if response.status != 200:
                    logger.error(f"API returned status {response.status}")
                    return None

                data = json.loads(response.read().decode("utf-8"))

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

            self._last_fetch_time = time.time()
            return device_patterns

        except urllib.error.URLError as e:
            logger.warning(f"Failed to fetch device hardware data: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from API: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching device hardware data: {e}")
            return None

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

            self._last_fetch_time = timestamp
            return set(patterns)

        except (json.JSONDecodeError, IOError, KeyError) as e:
            logger.warning(f"Failed to load device hardware cache: {e}")
            return None

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

            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)

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
