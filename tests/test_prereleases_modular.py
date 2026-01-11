"""
Prerelease functionality tests for the new modular Fetchtastic architecture.

This module contains tests for prerelease discovery, tracking, cleanup,
and related functionality using the new modular components.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import (
    PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS,
    PRERELEASE_COMMITS_CACHE_FILE,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager
from fetchtastic.download.version import VersionManager


@pytest.fixture
def test_config():
    """Test configuration for new modular components."""
    return {
        "DOWNLOAD_DIR": "/tmp/test",
        "CHECK_FIRMWARE_PRERELEASES": True,
        "SELECTED_PRERELEASE_ASSETS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
    }


@pytest.fixture
def cache_manager(tmp_path):
    """
    Provide a CacheManager configured to use the given temporary directory.

    Parameters:
        tmp_path (pathlib.Path): Temporary directory (pytest fixture) to be used as the on-disk cache directory.

    Returns:
        CacheManager: A CacheManager instance with its cache_dir set to the string form of `tmp_path`.
    """
    return CacheManager(cache_dir=str(tmp_path))


@pytest.fixture
def version_manager():
    """Version manager instance."""
    return VersionManager()


@pytest.fixture
def prerelease_manager():
    """Prerelease history manager instance."""
    return PrereleaseHistoryManager()


@pytest.fixture
def firmware_downloader(test_config, tmp_path):
    """
    Create a FirmwareReleaseDownloader configured with the provided test configuration and a temporary cache directory.

    Parameters:
        test_config: Test fixture providing downloader configuration (download directory, prerelease checks, selected assets, exclude patterns, token).
        tmp_path: Temporary filesystem path used as the cache directory.

    Returns:
        FirmwareReleaseDownloader: Downloader instance backed by a CacheManager rooted at `tmp_path`.
    """
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    return FirmwareReleaseDownloader(test_config, cache_manager)


def test_cleanup_superseded_prereleases(firmware_downloader, tmp_path):
    """Test the cleanup of superseded pre-releases."""
    download_dir = tmp_path
    firmware_dir = download_dir / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # This pre-release has been "promoted" so it should be deleted
    (prerelease_dir / "firmware-2.1.0").mkdir()
    # This one is still a pre-release, so it should be kept
    (prerelease_dir / "firmware-2.2.0").mkdir()

    # The latest official release
    latest_release_tag = "v2.1.0"

    # Use the new FirmwareReleaseDownloader directly
    config = {"DOWNLOAD_DIR": str(download_dir)}
    cache_manager = CacheManager()
    firmware_downloader = FirmwareReleaseDownloader(config, cache_manager)
    removed = firmware_downloader.cleanup_superseded_prereleases(latest_release_tag)
    assert removed is True

    assert not (prerelease_dir / "firmware-2.1.0").exists()
    assert (prerelease_dir / "firmware-2.2.0").exists()


def test_cleanup_superseded_prereleases_handles_commit_suffix(
    firmware_downloader, tmp_path
):
    """Ensure prereleases sharing the release base version are cleaned up."""
    download_dir = tmp_path
    firmware_dir = download_dir / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    promoted_dir = prerelease_dir / "firmware-2.7.12.fcb1d64"
    promoted_dir.mkdir()

    future_dir = prerelease_dir / "firmware-2.7.13.abcd123"
    future_dir.mkdir()

    # Use the new FirmwareReleaseDownloader directly
    config = {"DOWNLOAD_DIR": str(download_dir)}
    cache_manager = CacheManager()
    firmware_downloader = FirmwareReleaseDownloader(config, cache_manager)
    removed = firmware_downloader.cleanup_superseded_prereleases("v2.7.12.45f15b8")

    assert removed is True
    assert not promoted_dir.exists()
    assert future_dir.exists()


def test_version_manager_extract_clean_version(version_manager):
    """Test version extraction and cleaning."""
    # Test various version formats
    assert version_manager.extract_clean_version("2.7.14.abc123") == "v2.7.14"
    assert version_manager.extract_clean_version("v2.7.14") == "v2.7.14"
    assert version_manager.extract_clean_version("2.7.14") == "v2.7.14"


def test_prerelease_manager_create_default_entry(prerelease_manager):
    """Test creating default prerelease entries."""
    entry = prerelease_manager._create_default_prerelease_entry(
        directory="firmware-2.7.14.abc123",
        identifier="2.7.14.abc123",
        base_version="2.7.14",
        commit_hash="abc123",
    )

    expected = {
        "directory": "firmware-2.7.14.abc123",
        "identifier": "2.7.14.abc123",
        "base_version": "2.7.14",
        "commit_hash": "abc123",
        "added_at": None,
        "removed_at": None,
        "added_sha": None,
        "removed_sha": None,
        "active": False,
        "status": "unknown",
    }

    assert entry == expected


def test_extract_prerelease_directory_timestamps(prerelease_manager):
    """Test extracting prerelease directory timestamps from commit history."""
    commits = [
        "not-a-dict",
        {"commit": {"message": 123, "committer": {"date": "2025-01-01T00:00:00Z"}}},
        {
            "commit": {
                "message": "Unrelated commit message",
                "committer": {"date": "2025-01-01T00:00:00Z"},
            }
        },
        {
            "commit": {
                "message": "2.7.14.e959000 meshtastic/firmware@e959000",
                "committer": {},
            }
        },
        {
            "commit": {
                "message": "2.7.14.e959000 meshtastic/firmware@e959000",
                "committer": {"date": "2025-01-01T00:00:00Z"},
            }
        },
        {
            "commit": {
                "message": "2.7.14.e959000 meshtastic/firmware@e959000",
                "committer": {"date": "2025-01-02T00:00:00Z"},
            }
        },
    ]

    timestamps = prerelease_manager.extract_prerelease_directory_timestamps(commits)

    assert list(timestamps.keys()) == ["firmware-2.7.14.e959000"]
    assert timestamps["firmware-2.7.14.e959000"] == datetime(
        2025, 1, 2, tzinfo=timezone.utc
    )


def test_cache_manager_commit_timestamp(cache_manager):
    """Test commit timestamp caching."""
    # Mock response for successful API call
    mock_response = Mock()
    mock_response.json.return_value = {
        "commit": {"committer": {"date": "2025-01-20T12:00:00Z"}}
    }
    mock_response.raise_for_status.return_value = None
    mock_response.status_code = 200
    mock_response.ok = True
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}

    # Clear any existing cache
    cache_manager.clear_all_caches()

    with patch(
        "fetchtastic.download.cache.make_github_api_request", return_value=mock_response
    ) as mock_get:
        # First call should make API request and cache result
        result1 = cache_manager.get_commit_timestamp(
            "meshtastic", "firmware", "abcdef123"
        )
        assert result1 is not None
        assert isinstance(result1, datetime)
        assert mock_get.call_count == 1

        # Second call should use cache
        result2 = cache_manager.get_commit_timestamp(
            "meshtastic", "firmware", "abcdef123"
        )
        assert result2 == result1
        # Should still be 1 call since it was cached
        assert mock_get.call_count == 1


def test_prerelease_history_build_simplified_history(prerelease_manager):
    """Test building simplified prerelease history."""
    # Sample commit data that mimics real GitHub API response
    sample_commits = [
        {
            "sha": "abc123def456",
            "commit": {
                "message": "2.7.14.e959000 meshtastic/firmware@e959000",
                "committer": {"date": "2025-01-02T10:00:00Z"},
            },
        },
        {
            "sha": "def456ghi789",
            "commit": {
                "message": "Delete firmware-2.7.13.ffb168b directory",
                "committer": {"date": "2025-01-03T10:00:00Z"},
            },
        },
        {
            "sha": "ghi789jkl012",
            "commit": {
                "message": "2.7.14.1c0c6b2 meshtastic/firmware@1c0c6b2",
                "committer": {"date": "2025-01-01T10:00:00Z"},
            },
        },
    ]

    # Test with expected version "2.7.14"
    result, seen_shas = prerelease_manager.build_simplified_prerelease_history(
        "2.7.14", sample_commits
    )

    # Should have 2 entries for version 2.7.14 (one added, one deleted)
    assert len(result) == 2

    # Check that entries have the expected structure
    for entry in result:
        assert "directory" in entry  # The method uses "directory" not "identifier"
        assert "status" in entry
        assert entry["status"] in ["active", "deleted"]


def test_firmware_downloader_prerelease_cleanup(firmware_downloader, tmp_path):
    """Test prerelease directory cleanup functionality."""
    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Create some old prerelease directories with same version but different hashes
    old_dir1 = prerelease_dir / "firmware-2.7.6.abc123"
    old_dir2 = prerelease_dir / "firmware-2.7.6.def456"
    old_dir1.mkdir()
    old_dir2.mkdir()

    # Add some files to the old directories
    (old_dir1 / "test_file.bin").write_bytes(b"old data")
    (old_dir2 / "test_file.bin").write_bytes(b"old data")

    # Verify old directories exist
    assert old_dir1.exists()
    assert old_dir2.exists()

    # Test cleanup (this would need to be implemented in FirmwareReleaseDownloader)
    # For now, just test that the directories exist
    assert len(list(prerelease_dir.iterdir())) == 2


def _make_cache_manager(tmp_path):
    return CacheManager(str(tmp_path))


def _make_manager():
    return PrereleaseHistoryManager()


def _fresh_cache_entry():
    return {
        "commits": [{"sha": "abc123"}],
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


def _stale_cache_entry():
    return {
        "commits": [],
        "cached_at": (
            datetime.now(timezone.utc)
            - timedelta(seconds=PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS + 30)
        ).isoformat(),
    }


def test_fetch_recent_repo_commits_uses_cached_data(tmp_path):
    cache_manager = _make_cache_manager(tmp_path)
    manager = _make_manager()
    cache_file = os.path.join(cache_manager.cache_dir, PRERELEASE_COMMITS_CACHE_FILE)
    entry = _fresh_cache_entry()

    with (
        patch.object(cache_manager, "read_json", return_value=entry) as mock_read,
        patch(
            "fetchtastic.download.prerelease_history.make_github_api_request"
        ) as mock_request,
    ):
        commits = manager.fetch_recent_repo_commits(
            5, cache_manager=cache_manager, github_token=None
        )

    assert commits == entry["commits"]
    mock_request.assert_not_called()
    mock_read.assert_called_once_with(cache_file)


def test_fetch_recent_repo_commits_refreshes_expired_cache(tmp_path):
    cache_manager = _make_cache_manager(tmp_path)
    manager = _make_manager()
    entry = _stale_cache_entry()
    mock_response = Mock()
    mock_response.json.return_value = [{"sha": "fresh"}]

    with (
        patch.object(cache_manager, "read_json", return_value=entry),
        patch(
            "fetchtastic.download.prerelease_history.make_github_api_request",
            return_value=mock_response,
        ) as mock_request,
        patch.object(
            cache_manager, "atomic_write_json", return_value=True
        ) as mock_write,
    ):
        commits = manager.fetch_recent_repo_commits(
            1,
            cache_manager=cache_manager,
            github_token="token",
            allow_env_token=False,
        )

    assert commits == [{"sha": "fresh"}]
    mock_request.assert_called_once()
    mock_write.assert_called_once()


def test_prerelease_history_uses_cached_entries(tmp_path):
    cache_manager = _make_cache_manager(tmp_path)
    manager = _make_manager()
    version = "2.7.14"
    cache_value = {
        version: {
            "entries": [{"status": "active", "directory": "firmware-2.7.14.abcd"}],
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }
    }

    with (
        patch.object(cache_manager, "read_json", return_value=cache_value) as mock_read,
        patch.object(manager, "fetch_recent_repo_commits") as mock_fetch,
    ):
        entries = manager.get_prerelease_commit_history(
            version,
            cache_manager=cache_manager,
            github_token=None,
            allow_env_token=True,
        )

    assert entries == cache_value[version]["entries"]
    mock_fetch.assert_not_called()
    mock_read.assert_called()


def test_prerelease_history_refreshes_stale_cache(tmp_path):
    cache_manager = _make_cache_manager(tmp_path)
    manager = _make_manager()
    version = "2.7.15"
    stale_value = {
        version: {
            "entries": [],
            "last_checked": (
                datetime.now(timezone.utc)
                - timedelta(seconds=PRERELEASE_COMMITS_CACHE_EXPIRY_SECONDS + 30)
            ).isoformat(),
        }
    }

    with (
        patch.object(cache_manager, "read_json", return_value=stale_value),
        patch.object(
            manager, "fetch_recent_repo_commits", return_value=[{"sha": "newsha"}]
        ) as mock_fetch,
        patch.object(
            manager,
            "build_simplified_prerelease_history",
            return_value=([{"sha": "newsha"}], {"newsha"}),
        ) as mock_build,
        patch.object(
            cache_manager, "atomic_write_json", return_value=True
        ) as mock_write,
    ):
        entries = manager.get_prerelease_commit_history(
            version,
            cache_manager=cache_manager,
            github_token="token",
            allow_env_token=False,
        )

    assert entries == [{"sha": "newsha"}]
    mock_fetch.assert_called_once()
    mock_build.assert_called_once()
    mock_write.assert_called_once()


def test_find_latest_remote_prerelease_dir_prefers_commit_history(
    tmp_path, prerelease_manager
):
    cache_manager = CacheManager(str(tmp_path))
    directories = [
        "firmware-2.7.17.acomm1",
        "firmware-2.7.17.def456",
    ]
    history_entries = [
        {"identifier": "2.7.17.def456"},
    ]

    with (
        patch.object(
            cache_manager,
            "get_repo_directories",
            return_value=directories,
        ),
        patch.object(
            prerelease_manager,
            "get_prerelease_commit_history",
            return_value=history_entries,
        ),
    ):
        result = prerelease_manager.find_latest_remote_prerelease_dir(
            "2.7.17",
            cache_manager=cache_manager,
            github_token="token",
            allow_env_token=False,
        )

    assert result == "firmware-2.7.17.def456"


def test_find_latest_remote_prerelease_dir_returns_none_when_no_match(
    tmp_path, prerelease_manager
):
    cache_manager = CacheManager(str(tmp_path))
    with (
        patch.object(
            cache_manager,
            "get_repo_directories",
            return_value=["firmware-2.7.16.acomm1"],
        ),
        patch.object(
            prerelease_manager,
            "get_prerelease_commit_history",
            return_value=[],
        ),
    ):
        result = prerelease_manager.find_latest_remote_prerelease_dir(
            "2.7.17",
            cache_manager=cache_manager,
            github_token="token",
            allow_env_token=False,
        )

    assert result is None
