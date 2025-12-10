"""
Cache Management for Fetchtastic Download Subsystem

This module provides caching infrastructure for release metadata,
commit timestamps, and other download-related data.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from fetchtastic.log_utils import logger


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

        return platformdirs.user_cache_dir("fetchtastic", "meshtastic")

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
            suffix: Suffix to use for the temporary file

        Returns:
            bool: True if the content was written successfully, False otherwise
        """
        try:
            temp_fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(file_path), prefix="tmp-", suffix=suffix
            )
        except OSError as e:
            logger.error(f"Could not create temporary file for {file_path}: {e}")
            return False

        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                writer_func(temp_f)
            os.replace(temp_path, file_path)
        except (IOError, UnicodeEncodeError, OSError) as e:
            logger.error(f"Could not write to {file_path}: {e}")
            return False
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass  # Ignore cleanup errors
        return True

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

        def _write_json_content(f):
            json.dump(data, f, indent=2)

        return self.atomic_write(file_path, _write_json_content, suffix=".json")

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
                expires_at = datetime.fromisoformat(expires_at_str)
                if datetime.now(timezone.utc) > expires_at:
                    logger.debug(f"Cache expired for {cache_file}")
                    return None

            return cache_data.get("data")
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid cache format in {cache_file}: {e}")
            return None

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
