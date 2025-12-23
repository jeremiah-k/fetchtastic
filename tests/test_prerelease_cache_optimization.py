"""
Tests for cache optimization in prerelease_history.py and cache.py.
This module tests new cache unchanged optimization that skips
disk writes when data hasn't changed.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager


@pytest.mark.unit
@pytest.mark.core_downloads
class TestCacheManagerReleasesOptimization:
    """Test suite for cache optimization in cache.py write_releases_cache_entry."""

    def test_write_releases_cache_entry_unchanged(self):
        """Test that write_releases_cache_entry skips write when data is unchanged."""
        cache_manager = CacheManager()

        releases_data = [
            {"tag_name": "v2.7.14", "published_at": "2025-01-20T12:00:00Z"},
            {"tag_name": "v2.7.13", "published_at": "2025-01-10T12:00:00Z"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir
            url_cache_key = "https://api.github.com/repos/meshtastic/firmware/releases"

            cache_manager.write_releases_cache_entry(url_cache_key, releases_data)

            cache_file = Path(tmpdir) / "releases.json"
            cached_data = json.loads(cache_file.read_text())
            assert url_cache_key in cached_data
            assert cached_data[url_cache_key]["releases"] == releases_data

            original_cached_at = cached_data[url_cache_key]["cached_at"]

            cache_manager.write_releases_cache_entry(url_cache_key, releases_data)

            cached_data = json.loads(cache_file.read_text())
            assert cached_data[url_cache_key]["cached_at"] == original_cached_at

    def test_write_releases_cache_entry_changed(self):
        """Test that write_releases_cache_entry writes when data changes."""
        cache_manager = CacheManager()

        old_releases = [
            {"tag_name": "v2.7.13", "published_at": "2025-01-10T12:00:00Z"},
        ]

        new_releases = [
            {"tag_name": "v2.7.14", "published_at": "2025-01-20T12:00:00Z"},
            {"tag_name": "v2.7.13", "published_at": "2025-01-10T12:00:00Z"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir
            url_cache_key = "https://api.github.com/repos/meshtastic/firmware/releases"

            cache_manager.write_releases_cache_entry(url_cache_key, old_releases)
            cache_manager.write_releases_cache_entry(url_cache_key, new_releases)

            cache_file = Path(tmpdir) / "releases.json"
            cached_data = json.loads(cache_file.read_text())
            assert url_cache_key in cached_data
            assert len(cached_data[url_cache_key]["releases"]) == 2
            assert cached_data[url_cache_key]["releases"][0]["tag_name"] == "v2.7.14"

    def test_write_releases_cache_entry_empty(self):
        """Test that write_releases_cache_entry handles empty cache."""
        cache_manager = CacheManager()

        releases_data = [
            {"tag_name": "v2.7.14", "published_at": "2025-01-20T12:00:00Z"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir
            url_cache_key = "https://api.github.com/repos/meshtastic/firmware/releases"

            cache_manager.write_releases_cache_entry(url_cache_key, releases_data)

            cache_file = Path(tmpdir) / "releases.json"
            assert cache_file.exists()

            cached_data = json.loads(cache_file.read_text())
            assert url_cache_key in cached_data
            assert len(cached_data[url_cache_key]["releases"]) == 1

    def test_write_releases_cache_entry_atomic_write_success(self):
        """Test that write_releases_cache_entry logs on successful atomic write."""
        cache_manager = CacheManager()

        releases_data = [
            {"tag_name": "v2.7.14", "published_at": "2025-01-20T12:00:00Z"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir
            url_cache_key = "https://api.github.com/repos/meshtastic/firmware/releases"

            with patch("fetchtastic.download.cache.logger") as mock_logger:
                cache_manager.write_releases_cache_entry(url_cache_key, releases_data)

                assert mock_logger.debug.called
                call_args = [str(call) for call in mock_logger.debug.call_args_list]
                assert any(
                    "Saved" in args and "releases entries" in args for args in call_args
                )
