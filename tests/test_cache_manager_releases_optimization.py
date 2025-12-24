"""
Tests for cache optimization in cache.py.
This module tests new cache unchanged optimization that skips
disk writes when data hasn't changed.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fetchtastic.download.cache import CacheManager


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

            with patch("fetchtastic.download.cache.logger") as mock_logger:
                cache_manager.write_releases_cache_entry(url_cache_key, releases_data)

                assert mock_logger.debug.called
                found_log = False
                for call in mock_logger.debug.call_args_list:
                    msg = call.args[0] if call.args else ""
                    if "extended" in msg.lower() and "cache freshness" in msg.lower():
                        found_log = True
                        break
                assert (
                    found_log
                ), "Expected 'Extended cache freshness' debug log not found"

            cached_data = json.loads(cache_file.read_text())
            assert cached_data[url_cache_key]["cached_at"] != original_cached_at

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
        """
        Verify that write_releases_cache_entry records a debug log when an atomic write succeeds.

        Asserts that a debug message containing both "Saved" and "releases entries" is emitted.
        """
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
                found_log = False
                for call in mock_logger.debug.call_args_list:
                    msg = call.args[0] if call.args else ""
                    if "Saved" in msg and "releases" in msg and "cache entry" in msg:
                        found_log = True
                        break
                assert (
                    found_log
                ), "Expected 'Saved X releases to cache entry' debug log not found"

    def test_build_url_cache_key_excludes_pagination_params(self):
        """Test that build_url_cache_key excludes page but keeps per_page."""
        base_url = "https://api.github.com/repos/meshtastic/firmware/releases"

        key1 = CacheManager.build_url_cache_key(base_url, {"per_page": 8})
        key2 = CacheManager.build_url_cache_key(base_url, {"per_page": 10})
        key3 = CacheManager.build_url_cache_key(base_url, {"page": 2, "per_page": 10})
        key4 = CacheManager.build_url_cache_key(base_url)

        assert (
            key1 == f"{base_url}?per_page=8"
        ), "per_page should be included in cache key"
        assert (
            key2 == f"{base_url}?per_page=10"
        ), "per_page should be included in cache key"
        assert (
            key3 == f"{base_url}?per_page=10"
        ), "page should be excluded, per_page should be included"
        assert key4 == base_url, "no params should return base URL"
        assert key1 != key2, "Different per_page values should generate different keys"
        assert key1 != key3, "page should be excluded from cache key"
