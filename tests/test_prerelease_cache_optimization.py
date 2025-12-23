"""
Tests for cache optimization in prerelease_history.py and cache.py.

This module tests new cache unchanged optimization that skips
disk writes when data hasn't changed.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager


class TestPrereleaseCacheOptimization:
    """Test suite for cache optimization in prerelease_history.py."""

    def test_fetch_recent_repo_commits_cache_unchanged(self):
        """Test that fetch_recent_repo_commits skips write when cache is unchanged."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        commits_data = [
            {
                "sha": "abc123",
                "commit": {
                    "message": "Commit 1",
                    "author": {"date": "2025-01-20T12:00:00Z"},
                },
            },
            {
                "sha": "def456",
                "commit": {
                    "message": "Commit 2",
                    "author": {"date": "2025-01-21T12:00:00Z"},
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "prerelease_commits_cache.json"
            cache_file.write_text(
                json.dumps(
                    {
                        "commits": commits_data,
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )

            cache_manager.cache_dir = tmpdir

            with patch(
                "fetchtastic.download.prerelease_history.make_github_api_request"
            ) as mock_get:
                mock_response = MagicMock()
                mock_response.json.return_value = commits_data
                mock_get.return_value = mock_response

                result = manager.fetch_recent_repo_commits(
                    limit=2, cache_manager=cache_manager, github_token=None
                )

                assert len(result) == 2
                assert result[0]["sha"] == "abc123"
                assert result[1]["sha"] == "def456"

    def test_fetch_recent_repo_commits_cache_created(self):
        """Test that fetch_recent_repo_commits creates cache when missing."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        new_commits = [
            {
                "sha": "abc123",
                "commit": {
                    "message": "Commit 1",
                    "author": {"date": "2025-01-20T12:00:00Z"},
                },
            },
            {
                "sha": "def456",
                "commit": {
                    "message": "Commit 2",
                    "author": {"date": "2025-01-21T12:00:00Z"},
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir

            with patch(
                "fetchtastic.download.prerelease_history.make_github_api_request"
            ) as mock_get:
                mock_response = MagicMock()
                mock_response.json.return_value = new_commits
                mock_get.return_value = mock_response

                result = manager.fetch_recent_repo_commits(
                    limit=2, cache_manager=cache_manager, github_token=None
                )

                assert len(result) == 2
                assert result[0]["sha"] == "abc123"
                assert result[1]["sha"] == "def456"

                cache_file = Path(tmpdir) / "prerelease_commits_cache.json"
                assert cache_file.exists()
                cached_data = json.loads(cache_file.read_text())
                assert len(cached_data["commits"]) == 2
                assert cached_data["commits"][0]["sha"] == "abc123"


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
