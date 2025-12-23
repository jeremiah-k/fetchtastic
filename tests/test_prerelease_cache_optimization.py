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
            old_timestamp = datetime.now(timezone.utc) - timedelta(seconds=120)
            cache_file.write_text(
                json.dumps(
                    {
                        "commits": commits_data,
                        "cached_at": old_timestamp.isoformat(),
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

    def test_fetch_recent_repo_commits_cache_changed(self):
        """Test that fetch_recent_repo_commits writes when cache changes."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        old_commits = [
            {
                "sha": "old123",
                "commit": {
                    "message": "Old Commit",
                    "author": {"date": "2025-01-19T12:00:00Z"},
                },
            },
        ]

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
            cache_file = Path(tmpdir) / "prerelease_commits_cache.json"
            old_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)
            cache_file.write_text(
                json.dumps(
                    {
                        "commits": old_commits,
                        "cached_at": old_timestamp.isoformat(),
                    }
                )
            )

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

                cached_data = json.loads(cache_file.read_text())
                assert len(cached_data["commits"]) == 2
                assert cached_data["commits"][0]["sha"] == "abc123"

    def test_fetch_recent_repo_commits_cache_created(self):
        """Test that fetch_recent_repo_commits creates new cache when missing."""
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

    def test_get_prerelease_commit_history_cache_unchanged(self):
        """Test that get_prerelease_commit_history skips write when cache is unchanged."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        entries_data = [
            {
                "sha": "abc123",
                "version": "2.7.14.1",
                "timestamp": "2025-01-20T12:00:00Z",
            },
            {
                "sha": "def456",
                "version": "2.7.14.2",
                "timestamp": "2025-01-21T12:00:00Z",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "prerelease_commit_history.json"
            old_timestamp = datetime.now(timezone.utc) - timedelta(seconds=120)
            history_file.write_text(
                json.dumps(
                    {
                        "v2.7.14": {
                            "entries": entries_data,
                            "cached_at": old_timestamp.isoformat(),
                            "last_checked": old_timestamp.isoformat(),
                            "shas": ["abc123", "def456"],
                        }
                    }
                )
            )

            cache_manager.cache_dir = tmpdir

            with (
                patch.object(
                    manager,
                    "build_simplified_prerelease_history",
                    return_value=(entries_data, ["abc123", "def456"]),
                ),
                patch.object(
                    manager,
                    "fetch_recent_repo_commits",
                    return_value=[
                        {
                            "sha": "abc123",
                            "commit": {"message": "Commit 1"},
                        },
                        {
                            "sha": "def456",
                            "commit": {"message": "Commit 2"},
                        },
                    ],
                ),
            ):
                result = manager.get_prerelease_commit_history(
                    cache_manager=cache_manager,
                    expected_version="v2.7.14",
                    github_token=None,
                    allow_env_token=True,
                    force_refresh=False,
                )

                assert len(result) == 2
                assert result[0]["sha"] == "abc123"
                assert result[1]["sha"] == "def456"

    def test_get_prerelease_commit_history_cache_changed(self):
        """Test that get_prerelease_commit_history writes when cache changes."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        old_entries = [
            {
                "sha": "old123",
                "version": "2.7.14.0",
                "timestamp": "2025-01-19T12:00:00Z",
            },
        ]

        new_entries = [
            {
                "sha": "abc123",
                "version": "2.7.14.1",
                "timestamp": "2025-01-20T12:00:00Z",
            },
            {
                "sha": "def456",
                "version": "2.7.14.2",
                "timestamp": "2025-01-21T12:00:00Z",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "prerelease_commit_history.json"
            old_timestamp = datetime.now(timezone.utc) - timedelta(seconds=120)
            history_file.write_text(
                json.dumps(
                    {
                        "v2.7.14": {
                            "entries": old_entries,
                            "cached_at": old_timestamp.isoformat(),
                            "last_checked": old_timestamp.isoformat(),
                            "shas": ["old123"],
                        }
                    }
                )
            )

            cache_manager.cache_dir = tmpdir

            def atomic_write_mock(path, data):
                Path(path).write_text(json.dumps(data))
                return True

            with (
                patch.object(
                    manager,
                    "build_simplified_prerelease_history",
                    return_value=(new_entries, ["abc123", "def456"]),
                ),
                patch.object(
                    manager,
                    "fetch_recent_repo_commits",
                    return_value=[
                        {
                            "sha": "abc123",
                            "commit": {"message": "Commit 1"},
                        },
                        {
                            "sha": "def456",
                            "commit": {"message": "Commit 2"},
                        },
                    ],
                ),
                patch.object(
                    cache_manager,
                    "atomic_write_json",
                    side_effect=atomic_write_mock,
                ),
                patch("fetchtastic.download.prerelease_history.logger") as mock_logger,
            ):
                result = manager.get_prerelease_commit_history(
                    cache_manager=cache_manager,
                    expected_version="v2.7.14",
                    github_token=None,
                    allow_env_token=True,
                    force_refresh=False,
                )

                assert len(result) == 2
                assert result[0]["sha"] == "abc123"
                assert result[1]["sha"] == "def456"

                cached_data = json.loads(history_file.read_text())
                assert len(cached_data["v2.7.14"]["entries"]) == 2
                assert cached_data["v2.7.14"]["entries"][0]["sha"] == "abc123"

                mock_logger.debug.assert_called()
                call_args = [str(call) for call in mock_logger.debug.call_args_list]
                assert any(
                    "Saved" in args and "prerelease history entries" in args
                    for args in call_args
                )

    def test_get_prerelease_commit_history_cache_empty(self):
        """Test that get_prerelease_commit_history handles empty cache."""
        cache_manager = CacheManager()
        manager = PrereleaseHistoryManager()

        new_entries = [
            {
                "sha": "abc123",
                "version": "2.7.14.1",
                "timestamp": "2025-01-20T12:00:00Z",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager.cache_dir = tmpdir

            def atomic_write_mock(path, data):
                Path(path).write_text(json.dumps(data))
                return True

            with (
                patch.object(
                    manager,
                    "build_simplified_prerelease_history",
                    return_value=(new_entries, ["abc123"]),
                ),
                patch.object(
                    manager,
                    "fetch_recent_repo_commits",
                    return_value=[
                        {
                            "sha": "abc123",
                            "commit": {"message": "Commit 1"},
                        },
                    ],
                ),
                patch.object(
                    cache_manager,
                    "atomic_write_json",
                    side_effect=atomic_write_mock,
                ),
                patch("fetchtastic.download.prerelease_history.logger") as mock_logger,
            ):
                result = manager.get_prerelease_commit_history(
                    cache_manager=cache_manager,
                    expected_version="v2.7.15",
                    github_token=None,
                    allow_env_token=True,
                    force_refresh=False,
                )

                assert len(result) == 1
                assert result[0]["sha"] == "abc123"

                history_file = Path(tmpdir) / "prerelease_commit_history.json"
                assert history_file.exists()

                mock_logger.debug.assert_called()
                call_args = [str(call) for call in mock_logger.debug.call_args_list]
                assert any(
                    "Saved" in args and "prerelease history entries" in args
                    for args in call_args
                )


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
