"""
Prerelease-related functionality tests for Fetchtastic downloader module.

This module contains tests for prerelease discovery, tracking, cleanup,
and related functionality.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from fetchtastic import downloader
from fetchtastic.downloader import (
    _commit_timestamp_cache,
    _get_commit_cache_file,
    _get_commit_hash_from_dir,
    _load_commit_cache,
    _normalize_version,
    _save_commit_cache,
    clear_commit_timestamp_cache,
    get_commit_timestamp,
    get_prerelease_tracking_info,
    matches_extract_patterns,
    update_prerelease_tracking,
)

# Constant for blocked network message
_BLOCKED_NETWORK_MSG = "Network access is blocked in tests"


@pytest.fixture(autouse=True)
def _deny_network():
    def _no_net(*_args, **_kwargs):
        raise AssertionError(_BLOCKED_NETWORK_MSG)

    with patch("fetchtastic.downloader.requests.get", _no_net):
        with patch("fetchtastic.downloader.requests.post", _no_net):
            with patch("fetchtastic.utils.requests.get", _no_net):
                with patch("fetchtastic.utils.requests.post", _no_net):
                    yield


@pytest.fixture(autouse=True)
def _mock_commit_history(monkeypatch):
    """Avoid real commit-history fetches during tests by returning empty history."""

    monkeypatch.setattr(
        downloader,
        "_get_prerelease_commit_history",
        lambda *args, **kwargs: [],
    )


@pytest.fixture(autouse=True)
def _use_isolated_cache(tmp_path_factory, monkeypatch):
    """Ensure cache writes go to an isolated temp directory for each test."""

    cache_dir = tmp_path_factory.mktemp("fetchtastic-cache")
    monkeypatch.setattr(
        downloader.platformdirs,
        "user_cache_dir",
        lambda *_args, **_kwargs: str(cache_dir),
    )

    # Reset cached file path globals so newly patched cache dir is used
    downloader._commit_cache_file = None
    downloader._releases_cache_file = None
    downloader._prerelease_dir_cache_file = None
    downloader._prerelease_commit_history_file = None
    downloader._repo_commit_change_cache_file = None
    return cache_dir


def mock_github_commit_timestamp(commit_timestamps):
    """
    Create a requests.get-compatible mock that returns commit timestamp data for specified commit hashes.

    When the requested URL contains "commits/{hash}" for a hash present in commit_timestamps, the mock response's json() returns {"commit": {"committer": {"date": "<ISO timestamp>"}}} and raise_for_status() is a no-op. For other URLs the mock response's json() returns an empty dict and raise_for_status() is a no-op.

    Parameters:
        commit_timestamps (dict): Mapping of commit hash (str) to ISO 8601 timestamp string.

    Returns:
        function: A callable suitable for use as a side_effect for mocks of requests.get; it accepts (url, **kwargs) and returns a Mock response object.
    """

    def mock_get_response(url, **_kwargs):
        """
        Return a requests-like mock response for GitHub commit timestamp endpoints used in tests.

        When the URL contains "commits/{commit_hash}" for a commit_hash present in
        the surrounding `commit_timestamps` mapping, the mock's `json()` returns
        {"commit": {"committer": {"date": <timestamp>}}}. For all other URLs the
        mock's `json()` returns an empty dict. The mock's `raise_for_status()` is a no-op.

        Parameters:
            url (str): The requested URL.

        Returns:
            unittest.mock.Mock: A mock object implementing `json()` and `raise_for_status()`
            that simulates a GitHub commit-timestamp API response.
        """

        # Extract commit hash from URL
        for commit_hash, timestamp in commit_timestamps.items():
            if f"/commits/{commit_hash}" in url or f"/git/commits/{commit_hash}" in url:
                return Mock(
                    json=lambda ts=timestamp: {"commit": {"committer": {"date": ts}}},
                    raise_for_status=lambda: None,
                    status_code=200,
                    ok=True,
                )

        # Default response for other URLs
        return Mock(
            json=lambda: {}, raise_for_status=lambda: None, status_code=404, ok=False
        )

    return mock_get_response


@pytest.fixture
def write_dummy_file():
    """Fixture that provides a function to write dummy files for download mocking."""

    def _write(dest, data=b"data"):
        """
        Create parent directories for `dest`, write binary `data` to `dest`, and return True.

        Parameters:
            dest (str or Path): Destination file path to write.
            data (bytes): Binary content to write; defaults to b"data".

        Returns:
            bool: Always returns True on successful write.
        """
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True

    return _write


def test_cleanup_superseded_prereleases(tmp_path):
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

    removed = downloader.cleanup_superseded_prereleases(
        str(download_dir), latest_release_tag
    )
    assert removed is True

    assert not (prerelease_dir / "firmware-2.1.0").exists()
    assert (prerelease_dir / "firmware-2.2.0").exists()


def test_cleanup_superseded_prereleases_handles_commit_suffix(tmp_path):
    """Ensure prereleases sharing the release base version are cleaned up."""
    download_dir = tmp_path
    firmware_dir = download_dir / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    promoted_dir = prerelease_dir / "firmware-2.7.12.fcb1d64"
    promoted_dir.mkdir()

    future_dir = prerelease_dir / "firmware-2.7.13.abcd123"
    future_dir.mkdir()

    removed = downloader.cleanup_superseded_prereleases(
        str(download_dir), "v2.7.12.45f15b8"
    )

    assert removed is True
    assert not promoted_dir.exists()
    assert future_dir.exists()


@patch("fetchtastic.menu_repo.fetch_repo_directories")
@patch("fetchtastic.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("fetchtastic.downloader.make_github_api_request")
def test_check_for_prereleases_download_and_cleanup(
    mock_api, mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path, write_dummy_file
):
    """Check that prerelease discovery downloads matching assets and cleans stale entries."""
    # Clear any cached prerelease directories to ensure fresh mock data
    downloader._clear_prerelease_cache()

    # Repo has a newer prerelease and some other dirs
    mock_fetch_dirs.return_value = [
        "firmware-2.7.7.abcdef",
        "random-not-firmware",
    ]
    # The prerelease contains a matching asset and a non-matching one
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-rak4631-2.7.7.abcdef.uf2",
            "path": "firmware-2.7.7.abcdef/firmware-rak4631-2.7.7.abcdef.uf2",
            "download_url": "https://example.invalid/firmware-2.7.7.abcdef/firmware-rak4631-2.7.7.abcdef.uf2",
        },
        {
            "name": "firmware-heltec-v3-2.7.7.abcdef.zip",
            "path": "firmware-2.7.7.abcdef/firmware-heltec-v3-2.7.7.abcdef.zip",
            "download_url": "https://example.invalid/firmware-2.7.7.abcdef/firmware-heltec-v3-2.7.7.abcdef.zip",
        },
    ]

    mock_dl.side_effect = lambda _url, dest: write_dummy_file(dest)

    download_dir = tmp_path
    firmware_dir = download_dir / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Create a stale prerelease that is older than the latest release; function should remove it
    stale_dir = prerelease_dir / "firmware-2.6.0.zzz"
    stale_dir.mkdir()
    # Also drop a stray file to verify file cleanup
    stray = prerelease_dir / "stray.txt"
    stray.write_text("stale")

    # Mock GitHub API response for commit timestamp
    resp = Mock()
    resp.json.return_value = {"commit": {"committer": {"date": "2025-01-20T12:00:00Z"}}}
    resp.raise_for_status.return_value = None
    mock_api.return_value = resp

    latest_release_tag = "v2.7.6.111111"
    downloaded, versions = downloader.check_for_prereleases(
        str(download_dir),
        latest_release_tag=latest_release_tag,
        selected_patterns=["rak4631-"],
        exclude_patterns=[],
    )

    assert downloaded is True
    assert versions == ["firmware-2.7.7.abcdef"]
    assert mock_dl.call_count == 1
    mock_fetch_contents.assert_called_once_with("firmware-2.7.7.abcdef")
    assert not stale_dir.exists()  # Verify stale prerelease was cleaned up


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_check_for_prereleases_no_directories(mock_fetch_dirs, tmp_path):
    """If repo has no firmware directories, function returns False, []."""
    mock_fetch_dirs.return_value = []
    downloaded, versions = downloader.check_for_prereleases(
        str(tmp_path), "v1.0.0", ["rak4631-"], exclude_patterns=[]
    )
    assert downloaded is False
    assert versions == []


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("fetchtastic.downloader.make_github_api_request")
def test_prerelease_tracking_functionality(
    mock_api, mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path, write_dummy_file
):
    """Test that prerelease tracking file is created and updated correctly."""
    # Setup mock data
    mock_fetch_dirs.return_value = [
        "firmware-2.7.7.abcdef",
        "firmware-2.7.8.fedcba",
    ]
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-rak4631-2.7.7.abcdef.uf2",
            "download_url": "https://example.invalid/rak4631.uf2",
        }
    ]

    mock_dl.side_effect = lambda _url, dest: write_dummy_file(dest)

    download_dir = tmp_path
    latest_release_tag = "v2.7.6.111111"

    # Mock GitHub API responses for commit timestamps
    resp = Mock()
    resp.json.return_value = {"commit": {"committer": {"date": "2025-01-20T12:00:00Z"}}}
    resp.raise_for_status.return_value = None
    mock_api.return_value = resp

    # Run prerelease check
    downloaded, versions = downloader.check_for_prereleases(
        str(download_dir), latest_release_tag, ["rak4631-"], exclude_patterns=[]
    )

    assert downloaded is True
    assert len(versions) > 0

    # Check that tracking file was created (now JSON format in cache directory)
    from fetchtastic.downloader import _ensure_cache_dir

    cache_dir = Path(_ensure_cache_dir())
    tracking_file = cache_dir / "prerelease_tracking.json"
    assert tracking_file.exists()

    # Check tracking file contents (JSON format)
    with open(tracking_file, "r") as f:
        tracking_data = json.load(f)

    # Check JSON tracking file format (new format)
    assert "version" in tracking_data
    assert "commits" in tracking_data
    assert "last_updated" in tracking_data
    # Version field should be the clean base version without hash
    expected_clean_version = (
        downloader._extract_clean_version(latest_release_tag) or latest_release_tag
    )
    assert tracking_data["version"] == expected_clean_version

    # Add shape check for last_updated to validate ISO-8601 format

    iso8601_pattern = (
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?$"
    )
    assert re.match(
        iso8601_pattern, tracking_data["last_updated"]
    ), f"last_updated not in ISO-8601 format: {tracking_data['last_updated']}"

    # Commits should be a list of strings, normalized to lowercase and unique.
    assert tracking_data.get("commits"), "commits should not be empty"
    assert all(c == c.lower() for c in tracking_data["commits"])
    assert len(set(tracking_data["commits"])) == len(tracking_data["commits"])

    # Test get_prerelease_tracking_info function
    info = downloader.get_prerelease_tracking_info()
    expected_clean_version = (
        downloader._extract_clean_version(latest_release_tag) or latest_release_tag
    )
    assert info["release"] == expected_clean_version
    assert info["prerelease_count"] > 0
    assert len(info["commits"]) > 0
    assert "history" in info


def test_prerelease_smart_pattern_matching():
    """Test that prerelease downloads use smart pattern matching for EXTRACT_PATTERNS."""
    # matches_extract_patterns already imported at module level

    # Test files and patterns
    test_files = [
        "firmware-rak4631-2.7.9.70724be-ota.zip",  # should match 'rak4631-'
        "device-install.sh",  # should match 'device-'
        "littlefs-rak4631-2.7.9.70724be.bin",  # should match both 'rak4631-' and 'littlefs-'
        "bleota.bin",  # should match 'bleota'
        "bleota-c3.bin",  # should match 'bleota'
        "firmware-canaryone-2.7.9.70724be-ota.zip",  # should NOT match any pattern
        "some-random-file.bin",  # should NOT match any pattern
    ]

    extract_patterns = ["rak4631-", "device-", "littlefs-", "bleota"]

    # Test the smart pattern matching logic used in prereleases
    for filename in test_files:
        matches = matches_extract_patterns(filename, extract_patterns)

        if filename in [
            "firmware-rak4631-2.7.9.70724be-ota.zip",
            "device-install.sh",
            "littlefs-rak4631-2.7.9.70724be.bin",
            "bleota.bin",
            "bleota-c3.bin",
        ]:
            assert matches, f"File {filename} should match patterns {extract_patterns}"
        else:
            assert (
                not matches
            ), f"File {filename} should NOT match patterns {extract_patterns}"


def test_prerelease_directory_cleanup(tmp_path, write_dummy_file):
    """Test that old prerelease directories are cleaned up when new ones arrive."""
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

    # Clear any cached prerelease directories to ensure fresh mock data
    downloader._clear_prerelease_cache()

    # Mock the repo to return a newer prerelease with same version but new hash
    with patch("fetchtastic.menu_repo.fetch_repo_directories") as mock_dirs:
        with patch("fetchtastic.menu_repo.fetch_directory_contents") as mock_contents:
            mock_dirs.return_value = ["firmware-2.7.7.789abc"]

            def _dir_aware_contents(dir_name: str):
                """
                Return a mock directory listing containing a single prerelease firmware asset whose path and download_url incorporate the provided directory name.

                Parameters:
                    dir_name (str): Directory name used as the prerelease directory component in the returned asset's `path` and `download_url`.

                Returns:
                    list[dict]: A list with one asset mapping containing the keys `name`, `path`, and `download_url`. The `path` and `download_url` reflect a hierarchical prerelease location that includes `dir_name`.
                """
                asset_name = "firmware-rak4631-2.7.7.789abc.uf2"
                return [
                    {
                        "name": asset_name,
                        "path": f"{dir_name}/{asset_name}",
                        "download_url": f"https://example.invalid/{dir_name}/{asset_name}",
                    }
                ]

            mock_contents.side_effect = _dir_aware_contents

            with patch("fetchtastic.downloader.download_file_with_retry") as mock_dl:
                mock_dl.side_effect = lambda _url, dest: write_dummy_file(
                    dest, b"new data"
                )

                with patch(
                    "fetchtastic.downloader.make_github_api_request"
                ) as mock_api:
                    mock_api.side_effect = mock_github_commit_timestamp(
                        {"789abc": "2025-01-20T12:00:00Z"}
                    )

                    # Run prerelease check - this should clean up old directories
                    downloaded, versions = downloader.check_for_prereleases(
                        str(download_dir),
                        "v2.7.6.111111",
                        ["rak4631-"],
                        exclude_patterns=[],
                    )

                    # Should report as "downloaded" since new prerelease was downloaded
                    assert downloaded is True  # New download occurred
                assert (
                    "firmware-2.7.7.789abc" in versions
                )  # But directory is still tracked

            # And tracking JSON should reflect that commit
            info = downloader.get_prerelease_tracking_info()
            assert "2.7.7.789abc" in info.get("commits", [])

    # Verify old directories were cleaned up
    assert not old_dir1.exists(), "Old prerelease directory should be removed"
    assert not old_dir2.exists(), "Old prerelease directory should be removed"


def test_get_prerelease_tracking_info_error_handling():
    """Test error handling in get_prerelease_tracking_info."""

    # uses top-level imports: Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        prerelease_dir = Path(tmp_dir)

        # Test with non-existent cache directory
        with patch("fetchtastic.downloader._ensure_cache_dir") as mock_cache_dir:
            # Test with non-existent cache directory
            mock_cache_dir.return_value = str(prerelease_dir / "nonexistent")
            result = get_prerelease_tracking_info()
            assert result == {}

            # Test with corrupted JSON tracking file
            cache_dir = prerelease_dir / "cache"
            cache_dir.mkdir()
            mock_cache_dir.return_value = str(cache_dir)

            tracking_file = cache_dir / "prerelease_tracking.json"
            tracking_file.write_bytes(b"\xff\xfe\x00\x00")  # Invalid UTF-8

            result = get_prerelease_tracking_info()
            assert result == {}  # Should handle decode errors gracefully


def test_get_prerelease_tracking_info_includes_history(monkeypatch, tmp_path):
    """Ensure commit history data is surfaced even when commits list is empty."""

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    tracking_file = cache_dir / "prerelease_tracking.json"
    tracking_file.write_text(
        json.dumps(
            {
                "version": "2.7.13",
                "hash": "abc1234",
                "commits": [],
                "last_updated": "2025-01-01T00:00:00Z",
            }
        )
    )

    monkeypatch.setattr(downloader, "_ensure_cache_dir", lambda: str(cache_dir))

    sample_history = [
        {
            "identifier": "2.7.14.e959000",
            "dir": "firmware-2.7.14.e959000",
            "base_version": "2.7.14",
            "active": True,
            "added_at": "2025-01-02T00:00:00Z",
            "removed_at": None,
        },
        {
            "identifier": "2.7.14.1c0c6b2",
            "dir": "firmware-2.7.14.1c0c6b2",
            "base_version": "2.7.14",
            "active": False,
            "added_at": "2025-01-01T00:00:00Z",
            "removed_at": "2025-01-03T00:00:00Z",
        },
    ]

    monkeypatch.setattr(
        downloader,
        "_get_prerelease_commit_history",
        lambda *args, **kwargs: sample_history,
    )

    info = downloader.get_prerelease_tracking_info()

    # Check that history entries have the new display formatting fields
    formatted_history = info["history"]
    assert len(formatted_history) == len(sample_history)

    # Check first entry (active)
    first_entry = formatted_history[0]
    assert first_entry["identifier"] == sample_history[0]["identifier"]
    assert (
        first_entry["display_name"] == sample_history[0]["identifier"]
    )  # No strikethrough for active
    assert not first_entry["is_deleted"]

    # Check second entry (deleted)
    second_entry = formatted_history[1]
    assert second_entry["identifier"] == sample_history[1]["identifier"]
    assert (
        second_entry["display_name"] == f"~~{sample_history[1]['identifier']}~~"
    )  # Strikethrough for deleted
    assert second_entry["is_deleted"]

    assert info["prerelease_count"] == len(sample_history)
    assert info["commits"] == []


def test_update_prerelease_tracking_error_handling():
    """Test error handling in update_prerelease_tracking."""

    # uses top-level imports: Path

    if os.name == "nt":
        pytest.skip("Permission bits unreliable on Windows")

    # Test error handling by mocking the cache directory to be unwritable
    with patch("fetchtastic.downloader._ensure_cache_dir") as mock_cache_dir:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir) / "cache"
            cache_dir.mkdir()
            cache_dir.chmod(0o444)  # Read-only
            mock_cache_dir.return_value = str(cache_dir)

            try:
                # Should handle write errors gracefully and return count of existing commits
                result = update_prerelease_tracking("v2.7.8", "firmware-2.7.9.abc123")
                assert (
                    result == 0
                )  # Should return actual persisted count (0 on failure)
            finally:
                # Restore permissions for cleanup
                cache_dir.chmod(0o755)


@pytest.mark.core_downloads
def test_get_commit_hash_from_dir():
    """Test extracting commit hash from prerelease directory names."""

    # Test valid directory names with commit hashes
    assert _get_commit_hash_from_dir("firmware-2.7.7.abcdef") == "abcdef"
    assert (
        _get_commit_hash_from_dir("firmware-1.2.3.1234567890abcdef")
        == "1234567890abcdef"
    )
    assert (
        _get_commit_hash_from_dir("firmware-2.7.7.ABCDEF") == "abcdef"
    )  # Case insensitive

    # Test directory names without commit hashes
    assert _get_commit_hash_from_dir("firmware-2.7.7") is None
    assert _get_commit_hash_from_dir("firmware-2.7.7-rc1") is None
    assert _get_commit_hash_from_dir("firmware-2.7.7.alpha") is None

    # Test edge cases
    assert (
        _get_commit_hash_from_dir("firmware-2.7.7.123") is None
    )  # Too short (3 chars)
    assert (
        _get_commit_hash_from_dir(
            "firmware-2.7.7.12345678901234567890123456789012345678901"
        )
        is None
    )  # Too long (41 chars)


@pytest.mark.core_downloads
def test_get_commit_timestamp_cache():
    """Test commit timestamp caching logic."""

    # Clear cache before test
    clear_commit_timestamp_cache()

    # Mock response for successful API call
    mock_response = Mock()
    mock_response.json.return_value = {
        "commit": {"committer": {"date": "2025-01-20T12:00:00Z"}}
    }
    mock_response.raise_for_status.return_value = None
    mock_response.status_code = 200
    mock_response.ok = True
    mock_response.headers = {"X-RateLimit-Remaining": "4999"}

    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ) as mock_get:
        # First call should make API request and cache result
        result1 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result1 is not None
        assert isinstance(result1, datetime)
        assert mock_get.call_count == 1

        # Second call should use cache
        result2 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result2 == result1
        assert mock_get.call_count == 1  # Still only one call

        # Check that cache contains the entry
        cache_key = "meshtastic/firmware/abcdef123"
        assert cache_key in _commit_timestamp_cache
        cached_timestamp, cached_at = _commit_timestamp_cache[cache_key]
        assert cached_timestamp == result1
        assert isinstance(cached_at, datetime)

    # Test force_refresh bypasses cache
    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ) as mock_get:
        result3 = get_commit_timestamp(
            "meshtastic", "firmware", "abcdef123", force_refresh=True
        )
        assert result3 == result1
        assert mock_get.call_count == 1  # One more call due to force_refresh

    # Test cache expiry (simulate old cache entry)
    cache_key = "meshtastic/firmware/abcdef123"
    old_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)  # Expired
    _commit_timestamp_cache[cache_key] = (result1, old_timestamp)

    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ) as mock_get:
        result4 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result4 == result1
        assert mock_get.call_count == 1  # Should refresh expired cache

    # Clear cache after test
    clear_commit_timestamp_cache()


@pytest.mark.core_downloads
def test_persistent_commit_cache_file_operations():
    """Test persistent commit cache file operations."""
    import tempfile
    from pathlib import Path

    # Clear cache before test
    clear_commit_timestamp_cache()

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock platformdirs to use our temp directory and reset global cache file variable
        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir"
        ) as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset the global variable to force re-calculation
            import fetchtastic.downloader as downloader_module

            downloader_module._commit_cache_file = None

            # Test _get_commit_cache_file creates correct path
            cache_file = _get_commit_cache_file()
            expected_path = Path(temp_dir) / "commit_timestamps.json"
            assert cache_file == str(expected_path)

            # Test cache file doesn't exist initially
            assert not os.path.exists(cache_file)

            # Add some data to in-memory cache
            test_timestamp = datetime(2025, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
            test_key = "owner/repo/abc123"
            _commit_timestamp_cache[test_key] = (
                test_timestamp,
                datetime.now(timezone.utc),
            )

            # Test _save_commit_cache creates file
            _save_commit_cache()
            assert os.path.exists(cache_file)

            # Verify file contents
            with open(cache_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            assert test_key in saved_data
            assert len(saved_data[test_key]) == 2  # timestamp and cached_at

            # Clear in-memory cache
            _commit_timestamp_cache.clear()
            assert len(_commit_timestamp_cache) == 0

            # Test _load_commit_cache restores data
            _load_commit_cache()
            assert len(_commit_timestamp_cache) == 1
            assert test_key in _commit_timestamp_cache
            loaded_timestamp, loaded_cached_at = _commit_timestamp_cache[test_key]
            assert loaded_timestamp == test_timestamp
            assert isinstance(loaded_cached_at, datetime)


@pytest.mark.core_downloads
def test_persistent_commit_cache_expiry():
    """Test that expired cache entries are not loaded."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir"
        ) as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Clear global cache first
            clear_commit_timestamp_cache()

            # Reset global variable to force re-calculation
            import fetchtastic.downloader as downloader_module

            downloader_module._commit_cache_file = None

            cache_file = _get_commit_cache_file()

            # Create cache data with expired entry
            old_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)  # Expired
            old_cached_at = datetime.now(timezone.utc) - timedelta(hours=24)  # Expired

            cache_data = {
                "owner/repo/expired": [
                    old_timestamp.isoformat(),
                    old_cached_at.isoformat(),
                ],
                "owner/repo/valid": [
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ],
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)

            # Load cache - should only load valid entries
            _load_commit_cache()

            # Should only have the valid entry
            assert len(_commit_timestamp_cache) == 1
            assert "owner/repo/valid" in _commit_timestamp_cache
            assert "owner/repo/expired" not in _commit_timestamp_cache


@pytest.mark.core_downloads
def test_persistent_commit_cache_error_handling():
    """Test error handling for corrupted cache files."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir"
        ) as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Clear global cache first
            clear_commit_timestamp_cache()

            # Reset global variable to force re-calculation
            import fetchtastic.downloader as downloader_module

            downloader_module._commit_cache_file = None

            cache_file = _get_commit_cache_file()

            # Test with invalid JSON
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write("invalid json content")

            # Should not raise exception, just log debug message
            _load_commit_cache()
            assert len(_commit_timestamp_cache) == 0

            # Test with invalid structure (not a dict)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(["not", "a", "dict"], f)

            _load_commit_cache()
            assert len(_commit_timestamp_cache) == 0

            # Test with invalid timestamp format
            invalid_data = {
                "owner/repo/invalid": ["not-a-timestamp", "2025-01-20T12:00:00Z"]
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(invalid_data, f)

            _load_commit_cache()
            assert len(_commit_timestamp_cache) == 0


@pytest.mark.core_downloads
def test_clear_commit_timestamp_cache_persistent():
    """Test that clear_commit_timestamp_cache also removes persistent cache file."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir"
        ) as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset global variable to force re-calculation
            import fetchtastic.downloader as downloader_module

            downloader_module._commit_cache_file = None

            cache_file = _get_commit_cache_file()

            # Create cache file
            _commit_timestamp_cache["test/key"] = (
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            )
            _save_commit_cache()
            assert os.path.exists(cache_file)

            # Clear cache - should remove file
            clear_commit_timestamp_cache()
            assert not os.path.exists(cache_file)
            assert len(_commit_timestamp_cache) == 0


@pytest.mark.core_downloads
def test_get_commit_timestamp_loads_persistent_cache():
    """Test that get_commit_timestamp loads persistent cache on first access."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir"
        ) as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset global variable to force re-calculation
            import fetchtastic.downloader as downloader_module

            downloader_module._commit_cache_file = None

            cache_file = _get_commit_cache_file()

            # Pre-populate cache file
            test_timestamp = datetime(2025, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
            cached_at = datetime.now(timezone.utc) - timedelta(minutes=30)  # Recent

            cache_data = {
                "owner/repo/preloaded": [
                    test_timestamp.isoformat(),
                    cached_at.isoformat(),
                ]
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)

            # Clear in-memory cache
            _commit_timestamp_cache.clear()

            # Call get_commit_timestamp - should load from persistent cache
            result = get_commit_timestamp("owner", "repo", "preloaded")

            assert result == test_timestamp
            assert "owner/repo/preloaded" in _commit_timestamp_cache


@pytest.mark.core_downloads
def test_prerelease_directory_cache_behaviour(tmp_path):
    """Ensure prerelease directory caching honours expiry and force refresh."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    original_cache = {
        key: (list(directories), cached_at)
        for key, (directories, cached_at) in downloader._prerelease_dir_cache.items()
    }
    original_file = downloader._prerelease_dir_cache_file
    original_loaded = downloader._prerelease_dir_cache_loaded

    try:
        downloader._prerelease_dir_cache.clear()
        downloader._prerelease_dir_cache_file = None
        downloader._prerelease_dir_cache_loaded = False

        with patch(
            "fetchtastic.downloader.platformdirs.user_cache_dir",
            return_value=str(cache_root),
        ):
            with patch(
                "fetchtastic.downloader.menu_repo.fetch_repo_directories",
                side_effect=[
                    ["firmware-1.0.0.aaaa"],
                    ["firmware-1.0.0.bbbb"],
                    ["firmware-1.0.0.cccc"],
                ],
            ) as mock_fetch_dirs:
                dirs_first = downloader._fetch_prerelease_directories()
                assert dirs_first == ["firmware-1.0.0.aaaa"]
                assert mock_fetch_dirs.call_count == 1

                dirs_cached = downloader._fetch_prerelease_directories()
                assert dirs_cached == ["firmware-1.0.0.aaaa"]
                assert mock_fetch_dirs.call_count == 1

                dirs_force = downloader._fetch_prerelease_directories(
                    force_refresh=True
                )
                assert dirs_force == ["firmware-1.0.0.bbbb"]
                assert mock_fetch_dirs.call_count == 2

                # Force cache expiry by clearing it completely and removing persisted cache file
                with downloader._cache_lock:
                    downloader._prerelease_dir_cache.clear()
                    downloader._prerelease_dir_cache_loaded = False
                    if downloader._prerelease_dir_cache_file:
                        Path(downloader._prerelease_dir_cache_file).unlink(
                            missing_ok=True
                        )
                        downloader._prerelease_dir_cache_file = None

                dirs_expired = downloader._fetch_prerelease_directories()
                assert dirs_expired == ["firmware-1.0.0.cccc"]
                assert mock_fetch_dirs.call_count == 3

    finally:
        downloader._prerelease_dir_cache.clear()
        downloader._prerelease_dir_cache.update(original_cache)
        downloader._prerelease_dir_cache_file = original_file
        downloader._prerelease_dir_cache_loaded = original_loaded


@pytest.mark.core_downloads
def test_prerelease_commit_cache_save_only_on_new_entries():
    """Ensure commit cache is only persisted when new timestamps are fetched."""
    cache_key = "meshtastic/firmware/abcdef12"
    cached_timestamp = datetime(2025, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
    cached_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    original_cache_loaded = downloader._commit_cache_loaded

    try:
        with downloader._cache_lock:
            downloader._commit_timestamp_cache[cache_key] = (
                cached_timestamp,
                cached_at,
            )
            downloader._commit_cache_loaded = True

        with patch(
            "fetchtastic.downloader._fetch_prerelease_directories",
            return_value=["firmware-2.7.13.abcdef12"],
        ):
            with (
                patch(
                    "fetchtastic.downloader.get_commit_timestamp",
                    return_value=cached_timestamp,
                ) as mock_get_timestamp,
                patch("fetchtastic.downloader._save_commit_cache") as mock_save_cache,
            ):
                result = downloader._find_latest_remote_prerelease_dir("2.7.13")
                assert result == "firmware-2.7.13.abcdef12"
                mock_get_timestamp.assert_called_once()
                mock_save_cache.assert_not_called()

        # Clear cache to simulate missing entry and ensure save is triggered
        with downloader._cache_lock:
            downloader._commit_timestamp_cache.pop(cache_key, None)

        def _populate_cache(*_args, **_kwargs):
            with downloader._cache_lock:
                downloader._commit_timestamp_cache[cache_key] = (
                    cached_timestamp,
                    datetime.now(timezone.utc),
                )
            return cached_timestamp

        with patch(
            "fetchtastic.downloader._fetch_prerelease_directories",
            return_value=["firmware-2.7.13.abcdef12"],
        ):
            with (
                patch(
                    "fetchtastic.downloader.get_commit_timestamp",
                    side_effect=_populate_cache,
                ) as mock_get_timestamp,
                patch("fetchtastic.downloader._save_commit_cache") as mock_save_cache,
            ):
                result = downloader._find_latest_remote_prerelease_dir("2.7.13")
                assert result == "firmware-2.7.13.abcdef12"
                mock_get_timestamp.assert_called_once()
                mock_save_cache.assert_called_once()
    finally:
        with downloader._cache_lock:
            downloader._commit_timestamp_cache.pop(cache_key, None)
        downloader._commit_cache_loaded = original_cache_loaded


@pytest.mark.core_downloads
def test_get_commit_timestamp_error_handling():
    """Test error handling in get_commit_timestamp."""

    clear_commit_timestamp_cache()

    # Test HTTP error
    http_err = requests.HTTPError("404 Not Found")
    with patch("fetchtastic.downloader.make_github_api_request", side_effect=http_err):
        result = get_commit_timestamp("meshtastic", "firmware", "badcommit")
        assert result is None

    # Test JSON decode error
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_response.status_code = 200
    mock_response.ok = True

    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ):
        result = get_commit_timestamp("meshtastic", "firmware", "badcommit")
        assert result is None

    # Test missing date in response
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"commit": {"committer": {}}}  # Missing date
    mock_response.status_code = 200
    mock_response.ok = True

    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ):
        result = get_commit_timestamp("meshtastic", "firmware", "badcommit")
        assert result is None

    clear_commit_timestamp_cache()

    # Mock response for successful API call
    mock_response = Mock()
    mock_response.json.return_value = {
        "commit": {"committer": {"date": "2025-01-20T12:00:00Z"}}
    }
    mock_response.raise_for_status.return_value = None
    mock_response.status_code = 200
    mock_response.ok = True

    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ):
        # First call should make API request
        result1 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result1 is not None
        assert isinstance(result1, datetime)

        # Second call should use cache
        result2 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result2 == result1

        # Verify only one API call was made
        # (This is hard to test directly with the current setup, but cache should work)

    # Test cache expiry with force_refresh
    with patch(
        "fetchtastic.downloader.make_github_api_request", return_value=mock_response
    ):
        result3 = get_commit_timestamp(
            "meshtastic", "firmware", "abcdef123", force_refresh=True
        )
        assert result3 == result1  # Should still work

    # Clear cache after test
    clear_commit_timestamp_cache()


@pytest.mark.core_downloads
def test_normalize_version():
    """Test version normalization function."""

    # Test None input
    assert _normalize_version(None) is None

    # Test empty string
    assert _normalize_version("") is None
    assert _normalize_version("   ") is None

    # Test valid versions
    result = _normalize_version("v1.2.3")
    assert result is not None
    assert str(result) == "1.2.3"

    result = _normalize_version("1.2.3")
    assert result is not None
    assert str(result) == "1.2.3"

    # Test prerelease versions
    result = _normalize_version("v1.2.3-rc1")
    assert result is not None
    assert str(result) == "1.2.3rc1"

    result = _normalize_version("1.2.3-alpha1")
    assert result is not None
    assert str(result) == "1.2.3a1"

    result = _normalize_version("1.2.3-beta2")
    assert result is not None
    assert str(result) == "1.2.3b2"

    # Test hash suffix
    result = _normalize_version("v1.2.3.abc123")
    assert result is not None
    assert str(result) == "1.2.3+abc123"

    # Test invalid versions
    assert _normalize_version("invalid") is None
    assert _normalize_version("v") is None
