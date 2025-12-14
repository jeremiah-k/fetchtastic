"""
Prerelease functionality tests for the new modular Fetchtastic architecture.

This module contains tests for prerelease discovery, tracking, cleanup,
and related functionality using the new modular components.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

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
def cache_manager():
    """Cache manager instance."""
    return CacheManager()


@pytest.fixture
def version_manager():
    """Version manager instance."""
    return VersionManager()


@pytest.fixture
def prerelease_manager():
    """Prerelease history manager instance."""
    return PrereleaseHistoryManager()


@pytest.fixture
def firmware_downloader(test_config):
    """Firmware release downloader instance."""
    return FirmwareReleaseDownloader(test_config)


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
    firmware_downloader = FirmwareReleaseDownloader(config)
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
    firmware_downloader = FirmwareReleaseDownloader(config)
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
