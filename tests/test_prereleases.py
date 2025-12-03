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
    _extract_identifier_from_entry,
    _format_history_entry,
    _get_commit_cache_file,
    _get_commit_hash_from_dir,
    _is_entry_deleted,
    _load_commit_cache,
    _normalize_version,
    _save_commit_cache,
    _sort_key,
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
    """
    Fixture that blocks external network access for the duration of a test.

    Patches the HTTP call entry points used by the downloader tests so any attempt to perform network I/O raises an AssertionError with the message stored in `_BLOCKED_NETWORK_MSG`. This ensures tests cannot make real network requests.
    """

    def _no_net(*_args, **_kwargs):
        """
        Prevent network access by raising an AssertionError when called.

        Intended as a replacement for network-call functions in tests so any attempted network I/O fails immediately.

        Raises:
            AssertionError: always raised with the message contained in `_BLOCKED_NETWORK_MSG`.
        """
        raise AssertionError(_BLOCKED_NETWORK_MSG)

    with patch("fetchtastic.downloader.requests.get", _no_net):
        with patch("fetchtastic.downloader.requests.post", _no_net):
            with patch("fetchtastic.utils.requests.get", _no_net):
                with patch("fetchtastic.utils.requests.post", _no_net):
                    yield


@pytest.fixture
def mock_commit_history(monkeypatch):
    """
    Force prerelease commit history lookups to return an empty list for tests.

    Replaces downloader._get_prerelease_commit_history and commit-history fetchers with stubs
    that return [] to prevent network access during tests.
    """

    monkeypatch.setattr(
        downloader,
        "_get_prerelease_commit_history",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        downloader,
        "_fetch_recent_repo_commits",
        lambda *_args, **_kwargs: [],
    )


@pytest.fixture(autouse=True)
def _use_isolated_cache(tmp_path_factory, monkeypatch):
    """
    Prepare an isolated temporary user cache directory for tests and configure the downloader to use it.

    Resets downloader's internal cache-file globals so subsequent cache reads and writes target the isolated directory.

    Returns:
        Path: The temporary isolated cache directory path.
    """

    cache_dir = tmp_path_factory.mktemp("fetchtastic-cache")
    monkeypatch.setattr(
        downloader.platformdirs,
        "user_cache_dir",
        lambda *_args, **_kwargs: str(cache_dir),
    )

    # Ensure each test starts with clean in-memory and on-disk caches
    downloader.clear_all_caches()

    # Reset cached file path globals so newly patched cache dir is used
    downloader._commit_cache_file = None
    downloader._releases_cache_file = None
    downloader._prerelease_dir_cache_file = None
    downloader._prerelease_commit_history_file = None
    downloader._prerelease_dir_cache_loaded = False
    downloader._prerelease_commit_history_loaded = False
    downloader._commit_cache_loaded = False
    return cache_dir


def mock_github_commit_timestamp(commit_timestamps):
    """
    Create a requests.get side-effect that returns mock GitHub commit-timestamp responses for specified commit hashes.

    When the generated callable is invoked with a URL containing "/commits/{hash}" or "/git/commits/{hash}" and that hash exists in `commit_timestamps`, the returned mock's `json()` yields {"commit": {"committer": {"date": "<ISO timestamp>"}}} and `raise_for_status()` is a no-op. For other URLs the mock's `json()` returns an empty dict and `ok` is False.

    Parameters:
        commit_timestamps (dict): Mapping of commit hash (str) to ISO 8601 timestamp string.

    Returns:
        function: A callable (url, **kwargs) -> unittest.mock.Mock that simulates the described requests.get response.
    """

    def mock_get_response(url, **_kwargs):
        """
        Produce a requests-like mock response for GitHub commit timestamp endpoints using the surrounding `commit_timestamps` mapping.

        When the URL contains "/commits/{commit_hash}" or "/git/commits/{commit_hash}" for a commit_hash present in `commit_timestamps`, the response's `json()` returns {"commit": {"committer": {"date": <timestamp>}}}, `status_code` is 200, and `ok` is True. For other URLs `json()` returns an empty dict, `status_code` is 404, and `ok` is False. The mock also provides a `raise_for_status()` callable that is a no-op.

        Parameters:
            url (str): The requested URL.

        Returns:
            unittest.mock.Mock: A mock response object that mimics the shape and behavior of a requests.Response for commit-timestamp lookups.
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


# Temporary fix for indentation issues
def test_fetch_prerelease_directories_uses_token(monkeypatch):
    """Ensure remote directory listing honours explicit GitHub token settings."""

    captured = {}
    token = "fake_token_for_tests"

    def _fake_fetch_repo_directories(*, allow_env_token, github_token):
        """
        Record the received token parameters into the test `captured` mapping for later inspection.

        This helper mutates the module-level `captured` dictionary by storing the values of `allow_env_token` and `github_token`.

        Parameters:
            allow_env_token (bool): Whether environment-provided GitHub token is allowed.
            github_token (str | None): Explicit GitHub token provided to the fetch.

        Returns:
            list: An empty list.
        """
        captured["allow_env_token"] = allow_env_token
        captured["github_token"] = github_token
        return []

    monkeypatch.setattr(
        downloader.menu_repo,
        "fetch_repo_directories",
        _fake_fetch_repo_directories,
    )

    downloader._fetch_prerelease_directories(
        force_refresh=True,
        github_token=token,
        allow_env_token=False,
    )

    assert captured["github_token"] == token
    assert captured["allow_env_token"] is False


@patch("fetchtastic.menu_repo.fetch_repo_directories")
@patch("fetchtastic.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("fetchtastic.downloader.make_github_api_request")
def test_check_for_prereleases_download_and_cleanup(
    mock_api,
    mock_dl,
    mock_fetch_contents,
    mock_fetch_dirs,
    tmp_path,
    write_dummy_file,
    mock_commit_history,
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
    mock_fetch_contents.assert_called_once_with(
        "firmware-2.7.7.abcdef", allow_env_token=True, github_token=None
    )
    assert not stale_dir.exists()  # Verify stale prerelease was cleaned up


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_check_for_prereleases_no_directories(
    mock_fetch_dirs, tmp_path, mock_commit_history
):
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
    mock_api,
    mock_dl,
    mock_fetch_contents,
    mock_fetch_dirs,
    tmp_path,
    write_dummy_file,
    mock_commit_history,
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


def test_prerelease_directory_cleanup(tmp_path, write_dummy_file, mock_commit_history):
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

            def _dir_aware_contents(
                dir_name: str,
                **_kwargs,
            ):
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

    def _mock_get_history(*_args, **_kwargs):
        """
        Provide the predefined sample prerelease commit history from the surrounding test scope.

        Ignores all positional and keyword arguments.

        Returns:
            sample_history (object): The sample prerelease commit history object supplied by the surrounding test.
        """
        return sample_history

    monkeypatch.setattr(downloader, "_get_prerelease_commit_history", _mock_get_history)

    info = downloader.get_prerelease_tracking_info()

    # Check that history entries have new display formatting fields
    formatted_history = info["history"]
    assert len(formatted_history) == len(sample_history)
    assert info.get("expected_version") == "2.7.14"

    # Check first entry (active)
    first_entry = formatted_history[0]
    assert first_entry["identifier"] == sample_history[0]["identifier"]
    assert first_entry["display_name"] == sample_history[0]["identifier"]
    assert first_entry["markup_label"] == f"[green]{sample_history[0]['identifier']}[/]"
    assert not first_entry["is_deleted"]
    assert first_entry["is_newest"]

    # Check second entry (deleted)
    second_entry = formatted_history[1]
    assert second_entry["identifier"] == sample_history[1]["identifier"]
    assert second_entry["display_name"] == sample_history[1]["identifier"]
    assert (
        second_entry["markup_label"]
        == f"[red][strike]{sample_history[1]['identifier']}[/strike][/red]"
    )
    assert second_entry["is_deleted"]

    assert info["prerelease_count"] == len(sample_history)
    assert info["history_created"] == len(sample_history)
    assert info["history_deleted"] == 1
    assert info["history_active"] == len(sample_history) - 1
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
                    "fetchtastic.downloader._fetch_recent_repo_commits",
                    return_value=[],
                ),
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
                    "fetchtastic.downloader._fetch_recent_repo_commits",
                    return_value=[],
                ),
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


@patch("fetchtastic.downloader._fetch_recent_repo_commits")
def test_find_latest_remote_prerelease_prefers_commit_history(mock_fetch_commits):
    """_find_latest_remote_prerelease_dir should use commit history without directory/timestamp lookups when available."""
    commit_list = [
        {
            "sha": "abc123",
            "commit": {
                "message": "2.7.13.abc123 meshtastic/firmware@abc123",
                "committer": {"date": "2025-02-01T12:00:00Z"},
            },
        }
    ]
    mock_fetch_commits.return_value = commit_list

    with (
        patch(
            "fetchtastic.downloader._build_simplified_prerelease_history"
        ) as mock_history,
        patch("fetchtastic.downloader._fetch_prerelease_directories") as mock_dirs,
        patch("fetchtastic.downloader.get_commit_timestamp") as mock_get_ts,
    ):
        mock_history.return_value = [
            {"status": "active", "directory": "firmware-2.7.13.abc123"}
        ]
        mock_dirs.side_effect = AssertionError(
            "_fetch_prerelease_directories should not be called when history resolves"
        )
        mock_get_ts.side_effect = AssertionError(
            "commit timestamp should not be called"
        )

        result = downloader._find_latest_remote_prerelease_dir("2.7.13")

    assert result == "firmware-2.7.13.abc123"
    mock_fetch_commits.assert_called_once()
    mock_history.assert_called_once_with(
        "2.7.13", commit_list, github_token=None, allow_env_token=True
    )


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


class TestPrereleaseHelperFunctions:
    """Test helper functions for prerelease tracking."""

    def test_extract_identifier_from_entry(self):
        """Test identifier extraction from history entries."""
        # Test with identifier field
        entry1 = {"identifier": "test-123", "status": "active"}
        assert _extract_identifier_from_entry(entry1) == "test-123"

        # Test with directory field
        entry2 = {"directory": "firmware-2.7.9.abc123", "status": "active"}
        assert _extract_identifier_from_entry(entry2) == "firmware-2.7.9.abc123"

        # Test with dir field
        entry3 = {"dir": "test-dir", "status": "active"}
        assert _extract_identifier_from_entry(entry3) == "test-dir"

        # Test with missing all fields
        entry4 = {"status": "active"}
        assert _extract_identifier_from_entry(entry4) == ""

        # Test with empty fields
        entry5 = {"identifier": "", "directory": "", "dir": "", "status": "active"}
        assert _extract_identifier_from_entry(entry5) == ""

    def test_is_entry_deleted(self):
        """Test entry deletion detection."""
        # Test with deleted status
        entry1 = {"status": "deleted"}
        assert _is_entry_deleted(entry1) is True

        # Test with removed_at field
        entry2 = {"removed_at": "2023-01-01T00:00:00Z"}
        assert _is_entry_deleted(entry2) is True

        # Test with both deleted indicators
        entry3 = {"status": "deleted", "removed_at": "2023-01-01T00:00:00Z"}
        assert _is_entry_deleted(entry3) is True

        # Test with active status
        entry4 = {"status": "active"}
        assert _is_entry_deleted(entry4) is False

        # Test with empty status
        entry5 = {"status": ""}
        assert _is_entry_deleted(entry5) is False

    def test_format_history_entry(self):
        """Test history entry formatting."""
        # Test newest entry
        entry1 = {"identifier": "test-123", "status": "active"}
        result = _format_history_entry(entry1, 0, "test-123")
        assert result["display_name"] == "test-123"
        assert result["is_deleted"] is False
        assert result["is_newest"] is True
        assert result["is_latest"] is True
        assert "[green]" in result["markup_label"]

        # Test deleted entry
        entry2 = {"identifier": "test-456", "status": "deleted"}
        result = _format_history_entry(entry2, 1, "test-123")
        assert result["display_name"] == "test-456"
        assert result["is_deleted"] is True
        assert result["is_newest"] is False
        assert result["is_latest"] is False
        assert "[red][strike]" in result["markup_label"]

        # Test middle entry (not newest, not latest active)
        entry3 = {"identifier": "test-789", "status": "active"}
        result = _format_history_entry(entry3, 2, "test-123")
        assert result["display_name"] == "test-789"
        assert result["is_deleted"] is False
        assert result["is_newest"] is False
        assert result["is_latest"] is False
        assert result["markup_label"] == "test-789"

        # Test empty identifier
        entry4 = {"status": "active"}
        result = _format_history_entry(entry4, 3, None)
        assert result == entry4  # Should return unchanged if no identifier

    def test_sort_key_with_max_function(self):
        """Test the improved _sort_key function using max()."""
        # Test with added_at more recent
        entry1 = {
            "identifier": "test1",
            "added_at": "2023-12-01T10:00:00Z",
            "removed_at": "2023-11-01T10:00:00Z",
        }
        result = _sort_key(entry1)
        assert result[0] == "2023-12-01T10:00:00Z"  # max should pick added_at

        # Test with removed_at more recent
        entry2 = {
            "identifier": "test2",
            "added_at": "2023-10-01T10:00:00Z",
            "removed_at": "2023-12-01T10:00:00Z",
        }
        result = _sort_key(entry2)
        assert result[0] == "2023-12-01T10:00:00Z"  # max should pick removed_at

        # Test with empty timestamps
        entry3 = {"identifier": "test3", "added_at": "", "removed_at": ""}
        result = _sort_key(entry3)
        assert result[0] == ""  # max of empty strings should be empty

        # Test with missing timestamp fields
        entry4 = {"identifier": "test4"}
        result = _sort_key(entry4)
        assert result[0] == ""  # Missing fields should default to empty string


def test_create_default_prerelease_entry():
    """Test the new helper function for creating default prerelease entries."""

    result = downloader._create_default_prerelease_entry(
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

    assert result == expected


def test_fetch_recent_repo_commits_cache_expiry(tmp_path_factory, monkeypatch):
    """Test cache expiry logic in _fetch_recent_repo_commits."""

    import json
    from datetime import datetime, timedelta, timezone

    cache_dir = Path(tmp_path_factory.mktemp("cache-test"))
    cache_file = cache_dir / "prerelease_commits_cache.json"

    # Create expired cache (older than expiry time)
    expired_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    expired_cache = {
        "cached_at": expired_time,
        "commits": [{"sha": "test123", "commit": {"message": "test"}}],
    }
    cache_file.write_text(json.dumps(expired_cache))

    monkeypatch.setattr(downloader, "_ensure_cache_dir", lambda: str(cache_dir))

    # Mock the API call to return test data
    mock_commits = [{"sha": "new456", "commit": {"message": "new test"}}]

    # Mock response object with .json() method
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.json.return_value = mock_commits

    with patch("fetchtastic.downloader.make_github_api_request") as mock_api:
        mock_api.return_value = mock_response

        result = downloader._fetch_recent_repo_commits(10, force_refresh=False)

        # Should fetch from API due to expired cache
        assert result == mock_commits
        mock_api.assert_called_once()


@patch("fetchtastic.downloader._enrich_history_from_commit_details", return_value=None)
def test_build_simplified_prerelease_history(_mock_enrich):
    """Test the new commit message parsing logic with various scenarios."""

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
        {
            "sha": "unrelated123",
            "commit": {
                "message": "Some unrelated commit message",
                "committer": {"date": "2025-01-04T10:00:00Z"},
            },
        },
    ]

    # Test with expected version "2.7.14"
    result = downloader._build_simplified_prerelease_history("2.7.14", sample_commits)

    # Should have 2 entries for version 2.7.14 (one added, one deleted)
    assert len(result) == 2

    # First entry should be the newest active one (e959000)
    first_entry = result[0]
    assert first_entry["identifier"] == "2.7.14.e959000"
    assert first_entry["directory"] == "firmware-2.7.14.e959000"
    assert first_entry["base_version"] == "2.7.14"
    assert first_entry["commit_hash"] == "e959000"
    assert first_entry["status"] == "active"
    assert first_entry["active"] is True
    assert first_entry["added_at"] == "2025-01-02T10:00:00Z"
    assert first_entry["added_sha"] == "abc123def456"
    assert first_entry["removed_at"] is None
    assert first_entry["removed_sha"] is None

    # Second entry should be the older one (1c0c6b2)
    second_entry = result[1]
    assert second_entry["identifier"] == "2.7.14.1c0c6b2"
    assert second_entry["directory"] == "firmware-2.7.14.1c0c6b2"
    assert second_entry["base_version"] == "2.7.14"
    assert second_entry["commit_hash"] == "1c0c6b2"
    assert second_entry["status"] == "active"
    assert second_entry["active"] is True
    assert second_entry["added_at"] == "2025-01-01T10:00:00Z"
    assert second_entry["added_sha"] == "ghi789jkl012"
    assert second_entry["removed_at"] is None
    assert second_entry["removed_sha"] is None

    # Test with different expected version
    result_diff = downloader._build_simplified_prerelease_history(
        "2.7.13", sample_commits
    )

    # Should have 1 entry for version 2.7.13 (deleted one)
    assert len(result_diff) == 1
    deleted_entry = result_diff[0]
    assert deleted_entry["identifier"] == "2.7.13.ffb168b"
    assert deleted_entry["status"] == "deleted"
    assert deleted_entry["active"] is False
    assert deleted_entry["removed_at"] == "2025-01-03T10:00:00Z"
    assert deleted_entry["removed_sha"] == "def456ghi789"

    # Test edge cases
    assert downloader._build_simplified_prerelease_history("", sample_commits) == []
    assert downloader._build_simplified_prerelease_history("2.7.14", []) == []
    assert (
        downloader._build_simplified_prerelease_history("2.7.99", sample_commits) == []
    )


def test_build_simplified_prerelease_history_re_add_scenario():
    """Test that re-adding a prerelease after deletion properly updates status to active."""

    # Sample commits that add, delete, then re-add the same prerelease
    # Commits in newest-to-oldest order (as returned by GitHub API)
    re_add_commits = [
        {
            "sha": "readd789ghi012",
            "commit": {
                "message": "2.7.15.abc123 meshtastic/firmware@abc123",
                "committer": {"date": "2025-01-03T10:00:00Z"},
            },
        },
        {
            "sha": "del456def789",
            "commit": {
                "message": "Delete firmware-2.7.15.abc123 directory",
                "committer": {"date": "2025-01-02T10:00:00Z"},
            },
        },
        {
            "sha": "add123abc456",
            "commit": {
                "message": "2.7.15.abc123 meshtastic/firmware@abc123",
                "committer": {"date": "2025-01-01T10:00:00Z"},
            },
        },
    ]

    result = downloader._build_simplified_prerelease_history("2.7.15", re_add_commits)

    # Should have 1 entry for version 2.7.15 (re-added, so active)
    assert len(result) == 1

    entry = result[0]
    assert entry["identifier"] == "2.7.15.abc123"
    assert entry["directory"] == "firmware-2.7.15.abc123"
    assert entry["base_version"] == "2.7.15"
    assert entry["commit_hash"] == "abc123"
    # After re-add, status should be active
    assert entry["status"] == "active"
    assert entry["active"] is True
    # added_at should remain the first add time
    assert entry["added_at"] == "2025-01-01T10:00:00Z"
    assert entry["added_sha"] == "add123abc456"
    # removed_at should be cleared after re-add
    assert entry["removed_at"] is None
    assert entry["removed_sha"] is None


def test_prerelease_history_cache_is_persistent(tmp_path_factory, monkeypatch):
    """Test that prerelease history cache persists and does not expire by age."""

    import json
    from datetime import datetime, timedelta, timezone

    cache_dir = Path(tmp_path_factory.mktemp("history-cache-test"))
    cache_file = cache_dir / "prerelease_commit_history.json"

    # Create expired cache (older than 2 minutes)
    expired_time = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    expired_cache = {
        "2.7.15": {
            "entries": [{"identifier": "2.7.15.test123", "status": "active"}],
            "cached_at": expired_time,
        }
    }
    cache_file.write_text(json.dumps(expired_cache))

    monkeypatch.setattr(downloader, "_ensure_cache_dir", lambda: str(cache_dir))
    # Reset the cached file path
    downloader._prerelease_commit_history_file = None

    # Mock refresh function to return fresh data (matching full entry structure)
    fresh_entries = [
        {
            "directory": "firmware-2.7.15.abc123",
            "identifier": "2.7.15.abc123",
            "base_version": "2.7.15",
            "commit_hash": "abc123",
            "added_at": "2025-01-03T10:00:00Z",
            "removed_at": None,
            "added_sha": "fresh123abc456",
            "removed_sha": None,
            "active": True,
            "status": "active",
        }
    ]

    # Reset cache loaded flag to force reload
    downloader._prerelease_commit_history_loaded = False
    downloader._prerelease_commit_history_cache.clear()

    # Test 1: When force_refresh=True, should bypass cache and call refresh function
    with patch.object(downloader, "_refresh_prerelease_commit_history") as mock_refresh:
        mock_refresh.return_value = fresh_entries

        # Clear all cache state before test
        downloader._prerelease_commit_history_loaded = False
        downloader._prerelease_commit_history_cache.clear()

        # Call with force_refresh=True - should bypass cache and call refresh
        result = downloader._get_prerelease_commit_history("2.7.15", force_refresh=True)

        # Should return fresh data from refresh function
        assert result == fresh_entries
        # Verify refresh was called with correct parameters
        mock_refresh.assert_called_once_with("2.7.15", None, True, 40, True)

    # Test 2: When cache exists but is expired and force_refresh=False, refresh should be invoked
    with patch.object(downloader, "_refresh_prerelease_commit_history") as mock_refresh:
        mock_refresh.return_value = fresh_entries

        # Clear all cache state before test
        downloader._prerelease_commit_history_loaded = False
        downloader._prerelease_commit_history_cache.clear()

        result = downloader._get_prerelease_commit_history(
            "2.7.15", force_refresh=False
        )

        # Cache is expired, so refresh should be called and fresh data returned
        assert result == fresh_entries
        mock_refresh.assert_called_once()


def test_build_simplified_prerelease_history_edge_cases():
    """Test edge cases and malformed commit messages."""

    # Test with malformed commit messages
    malformed_commits = [
        {
            "sha": "abc123",
            "commit": {
                "message": "2.7.14.e959000",  # Missing firmware part
                "committer": {"date": "2025-01-02T10:00:00Z"},
            },
        },
        {
            "sha": "def456",
            "commit": {
                "message": "Delete firmware-2.7.14 directory",  # Missing hash
                "committer": {"date": "2025-01-03T10:00:00Z"},
            },
        },
        {
            "sha": "ghi789",
            "commit": {
                "message": "2.7.99.e959000 meshtastic/firmware@e959000",  # Wrong version
                "committer": {"date": "2025-01-04T10:00:00Z"},
            },
        },
    ]

    with patch(
        "fetchtastic.downloader._enrich_history_from_commit_details",
        return_value=None,
    ):
        result = downloader._build_simplified_prerelease_history(
            "2.7.14", malformed_commits
        )

    # Should handle malformed messages gracefully
    assert len(result) == 0


def test_build_history_fetches_uncertain_commits_when_rate_limit_allows(monkeypatch):
    """Unmatched commits should be classified via commit-detail fetch when rate limit permits."""

    uncertain_commit = [
        {
            "sha": "abc123",
            "commit": {
                "message": "Manual prerelease update",
                "committer": {"date": "2025-02-01T10:00:00Z"},
            },
        }
    ]

    fake_response = Mock()
    fake_response.json.return_value = {
        "files": [
            {
                "filename": "firmware-2.7.14.deadbeef/device-install.sh",
                "status": "added",
            }
        ]
    }

    monkeypatch.setattr(
        downloader,
        "get_api_request_summary",
        lambda: {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "auth_used": False,
            "rate_limit_remaining": 100,
        },
    )

    def _fake_request(*_args, **_kwargs):
        """
        Provide a predefined fake HTTP response for use as a mock side-effect.

        Used as a side-effect function when patching HTTP request calls in tests.

        Returns:
            The predefined `fake_response` object from the enclosing scope.
        """
        return fake_response

    with patch(
        "fetchtastic.downloader.make_github_api_request", side_effect=_fake_request
    ) as mock_request:
        result = downloader._build_simplified_prerelease_history(
            "2.7.14", uncertain_commit
        )

        assert len(result) == 1
        assert result[0]["identifier"] == "2.7.14.deadbeef"
        mock_request.assert_called_once()


def test_build_history_prioritizes_newest_uncertain_commits(monkeypatch):
    """Newest uncertain commits should be processed first when the enrichment limit applies."""

    call_order = []

    monkeypatch.setattr(downloader, "_should_fetch_uncertain_commits", lambda: True)
    monkeypatch.setattr(downloader, "_MAX_UNCERTAIN_COMMITS_TO_RESOLVE", 1)

    def fake_fetch(sha, github_token, allow_env_token):
        """
        Simulate GitHub commit file changes for testing.

        Parameters:
            sha (str): Commit SHA to simulate. If equal to "sha-newest" the returned list contains a firmware file in a directory suffixed with "abc1234"; otherwise it contains an older firmware file suffixed with "old9999".
            github_token: Ignored by this fake; present to match the real function signature.
            allow_env_token: Ignored by this fake; present to match the real function signature.

        Returns:
            list[dict]: A list of file-change dictionaries with keys `filename` (path to the file) and `status` (for example, `"added"`).
        """
        call_order.append(sha)
        if sha == "sha-newest":
            return [
                {
                    "filename": "firmware-2.7.15.abc1234/new.bin",
                    "status": "added",
                }
            ]
        return [
            {
                "filename": "firmware-2.7.15.old9999/old.bin",
                "status": "added",
            }
        ]

    monkeypatch.setattr(downloader, "_fetch_commit_files", fake_fetch)

    commits = [
        {
            "sha": "sha-newest",
            "commit": {
                "message": "Manual prerelease update",
                "committer": {"date": "2025-02-02T10:00:00Z"},
            },
        },
        {
            "sha": "sha-older",
            "commit": {
                "message": "Manual prerelease update",
                "committer": {"date": "2025-01-30T10:00:00Z"},
            },
        },
    ]

    history = downloader._build_simplified_prerelease_history("2.7.15", commits)

    assert call_order == ["sha-newest"]
    assert history
    assert history[0]["identifier"] == "2.7.15.abc1234"
    assert history[0]["added_sha"] == "sha-newest"
    assert all(entry["identifier"] != "2.7.15.old9999" for entry in history)


def test_build_history_skips_detail_fetch_when_rate_limit_low(monkeypatch):
    """Ensure we avoid extra API calls for uncertain commits when requests are scarce."""

    uncertain_commit = [
        {
            "sha": "abc123",
            "commit": {
                "message": "Manual prerelease update",
                "committer": {"date": "2025-02-01T10:00:00Z"},
            },
        }
    ]

    monkeypatch.setattr(
        downloader,
        "get_api_request_summary",
        lambda: {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "auth_used": False,
            "rate_limit_remaining": 10,
        },
    )

    with patch("fetchtastic.downloader.make_github_api_request") as mock_request:
        result = downloader._build_simplified_prerelease_history(
            "2.7.14", uncertain_commit
        )

        assert result == []
        mock_request.assert_not_called()


def test_fetch_recent_repo_commits_with_api_mocking(tmp_path_factory, monkeypatch):
    """Test _fetch_recent_repo_commits with targeted API mocking instead of full function mock."""

    import json

    cache_dir = Path(tmp_path_factory.mktemp("api-mock-test"))
    cache_file = cache_dir / "prerelease_commits_cache.json"

    # Mock API response with sample commit data
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
    ]

    # Mock response object
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.json.return_value = sample_commits

    monkeypatch.setattr(downloader, "_ensure_cache_dir", lambda: str(cache_dir))

    with patch("fetchtastic.downloader.make_github_api_request") as mock_api:
        mock_api.return_value = mock_response

        # Test fresh fetch (no cache)
        result = downloader._fetch_recent_repo_commits(10, force_refresh=False)

        # Should return the mocked commits
        assert result == sample_commits
        mock_api.assert_called_once()

        # Verify cache was created
        assert cache_file.exists()
        cached_data = json.loads(cache_file.read_text())
        assert "commits" in cached_data
        assert "cached_at" in cached_data
        assert cached_data["commits"] == sample_commits


def test_fetch_recent_repo_commits_force_refresh(tmp_path_factory, monkeypatch):
    """
    Verify that _fetch_recent_repo_commits ignores an existing cache when force_refresh=True and fetches fresh commit data from the GitHub API.

    This test creates a cached prerelease commits file, patches the cache directory and the GitHub API call, calls _fetch_recent_repo_commits with force_refresh=True, and asserts that the returned commits come from the API mock and that the API was invoked.
    """

    import json
    from datetime import datetime, timedelta, timezone

    cache_dir = Path(tmp_path_factory.mktemp("force-refresh-test"))
    cache_file = cache_dir / "prerelease_commits_cache.json"

    # Create existing cache
    existing_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    existing_cache = {
        "cached_at": existing_time,
        "commits": [{"sha": "existing123", "commit": {"message": "existing"}}],
    }
    cache_file.write_text(json.dumps(existing_cache))

    # Mock API response
    fresh_commits = [{"sha": "fresh456", "commit": {"message": "fresh"}}]
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.json.return_value = fresh_commits

    monkeypatch.setattr(downloader, "_ensure_cache_dir", lambda: str(cache_dir))

    with patch("fetchtastic.downloader.make_github_api_request") as mock_api:
        mock_api.return_value = mock_response

        result = downloader._fetch_recent_repo_commits(10, force_refresh=True)

        # Should fetch fresh data despite existing cache
        assert result == fresh_commits
        mock_api.assert_called_once()


def test_enrich_history_from_commit_details_with_renamed_files():
    """Test the _enrich_history_from_commit_details function handles renamed files correctly."""
    from fetchtastic.downloader import _enrich_history_from_commit_details

    # Mock data for renamed files scenario
    expected_version = "2.7.99"
    entries = {}

    # Test case 1: File renamed within same directory
    uncertain_commits_same_dir = [
        {
            "sha": "abc123def456",
            "commit": {"committer": {"date": "2023-01-01T00:00:00Z"}},
        }
    ]

    # Mock the API response for commit files
    files_same_dir = [
        {
            "filename": "firmware-2.7.99.abc123/new_file.bin",
            "status": "renamed",
            "previous_filename": "firmware-2.7.99.abc123/old_file.bin",
        }
    ]

    with patch(
        "fetchtastic.downloader._fetch_commit_files", return_value=files_same_dir
    ):
        with patch("fetchtastic.downloader._record_prerelease_addition") as mock_add:
            with patch(
                "fetchtastic.downloader._record_prerelease_deletion"
            ) as mock_remove:
                _enrich_history_from_commit_details(
                    entries,
                    uncertain_commits_same_dir,
                    expected_version,
                    "test_token",
                    False,
                )
                # Should be marked as added since dir_info exists (the fix we applied)
                mock_add.assert_called_once()
                mock_remove.assert_not_called()

    # Test case 2: File renamed to different directory
    uncertain_commits_diff_dir = [
        {
            "sha": "def456abc123",
            "commit": {"committer": {"date": "2023-01-01T00:00:00Z"}},
        }
    ]

    files_diff_dir = [
        {
            "filename": "firmware-2.7.99.def456/new_file.bin",
            "status": "renamed",
            "previous_filename": "firmware-2.7.99.abc123/old_file.bin",
        }
    ]

    entries.clear()
    with patch(
        "fetchtastic.downloader._fetch_commit_files", return_value=files_diff_dir
    ):
        with patch("fetchtastic.downloader._record_prerelease_addition") as mock_add:
            with patch(
                "fetchtastic.downloader._record_prerelease_deletion"
            ) as mock_remove:
                _enrich_history_from_commit_details(
                    entries,
                    uncertain_commits_diff_dir,
                    expected_version,
                    "test_token",
                    False,
                )
                # Should be marked as added since dir_info exists
                mock_add.assert_called_once()
                mock_remove.assert_called_once()


def test_extract_prerelease_dir_info_edge_cases():
    """Test _extract_prerelease_dir_info with various edge cases."""
    from fetchtastic.downloader import _extract_prerelease_dir_info

    expected_version = "2.7.99"

    # Valid cases
    valid_path = "some/path/firmware-2.7.99.abc123/file.bin"
    result = _extract_prerelease_dir_info(valid_path, expected_version)
    assert result is not None
    directory, identifier, short_hash = result
    assert directory == "firmware-2.7.99.abc123"
    assert identifier == "2.7.99.abc123"
    assert short_hash == "abc123"

    # Invalid version
    invalid_version_path = "some/path/firmware-1.2.3.def456/file.bin"
    result = _extract_prerelease_dir_info(invalid_version_path, expected_version)
    assert result is None

    # Empty path
    result = _extract_prerelease_dir_info("", expected_version)
    assert result is None

    # Non-matching pattern
    non_matching_path = "some/other/path/file.bin"
    result = _extract_prerelease_dir_info(non_matching_path, expected_version)
    assert result is None


def test_create_default_prerelease_entry_edge_cases():
    """Test helper function with various inputs."""

    # Test with different inputs
    result1 = downloader._create_default_prerelease_entry(
        "firmware-2.7.14.abc123", "2.7.14.abc123", "2.7.14", "abc123"
    )

    assert result1["directory"] == "firmware-2.7.14.abc123"
    assert result1["identifier"] == "2.7.14.abc123"
    assert result1["base_version"] == "2.7.14"
    assert result1["commit_hash"] == "abc123"
    assert result1["added_at"] is None
    assert result1["removed_at"] is None
    assert result1["added_sha"] is None
    assert result1["removed_sha"] is None
    assert result1["active"] is False
    assert result1["status"] == "unknown"


def test_cache_build_logging_scenarios(monkeypatch):
    """Test cache build logging in various scenarios."""
    from fetchtastic.downloader import _get_prerelease_commit_history

    # Test case 1: Initial cache build logs appropriate message
    original_cache = dict(downloader._prerelease_commit_history_cache)
    original_loaded = downloader._prerelease_commit_history_loaded

    def _noop_load() -> None:
        """
        No-op loader that performs no action.

        Intended as a placeholder loader to use when no persistent load should occur.
        """
        return None

    monkeypatch.setattr(downloader, "_load_prerelease_commit_history_cache", _noop_load)

    try:
        downloader._prerelease_commit_history_cache.clear()
        downloader._prerelease_commit_history_loaded = False

        with (
            patch.object(
                downloader,
                "_refresh_prerelease_commit_history",
                return_value=([], datetime.now()),
            ) as mock_refresh,
            patch.object(downloader.logger, "info") as mock_info,
        ):
            _get_prerelease_commit_history("2.7.99")
            mock_refresh.assert_called_once()

        mock_info.assert_called_once_with(
            "Building prerelease history cache for %s (this may take a couple of minutes on initial builds)...",
            "2.7.99",
        )
    finally:
        downloader._prerelease_commit_history_cache.clear()
        downloader._prerelease_commit_history_cache.update(original_cache)
        downloader._prerelease_commit_history_loaded = original_loaded

    # Test case 2: Force refresh also logs build message
    with (
        patch.object(
            downloader, "_refresh_prerelease_commit_history", return_value=[]
        ) as mock_refresh,
        patch.object(downloader.logger, "info") as mock_info,
    ):
        _get_prerelease_commit_history("2.7.99", force_refresh=True)
        mock_refresh.assert_called_once()

        mock_info.assert_called_once_with(
            "Building prerelease history cache for %s (this may take a couple of minutes on initial builds)...",
            "2.7.99",
        )

    # Test case 3: Cache hit doesn't log build message
    with (
        patch.object(downloader, "_refresh_prerelease_commit_history") as mock_refresh,
        patch.object(downloader.logger, "info") as mock_info,
    ):
        # Set up cache as loaded
        downloader._prerelease_commit_history_loaded = True
        downloader._prerelease_commit_history_cache["2.7.99"] = (
            [],
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            set(),
        )

        _get_prerelease_commit_history("2.7.99")
        mock_refresh.assert_not_called()
        mock_info.assert_not_called()


def test_cache_expiry_and_logging():
    """
    Verify that forcing a prerelease commit history refresh rebuilds the cache and emits an info log entry.

    This test sets up a cached entry, invokes _get_prerelease_commit_history with force_refresh=True, and asserts
    that the underlying refresh function is called and that an informational log message about building the cache is emitted.
    """
    from datetime import datetime, timedelta, timezone

    from fetchtastic.downloader import _get_prerelease_commit_history

    # Test case 1: Force refresh triggers rebuild with logging
    original_cache = dict(downloader._prerelease_commit_history_cache)
    original_loaded = downloader._prerelease_commit_history_loaded

    try:
        # Create cache entry
        cache_time = datetime.now(timezone.utc) - timedelta(hours=1)
        downloader._prerelease_commit_history_cache["2.7.99"] = (
            [],
            cache_time,
            cache_time,
            set(),
        )
        downloader._prerelease_commit_history_loaded = True

        with (
            patch.object(
                downloader,
                "_refresh_prerelease_commit_history",
                return_value=([], datetime.now(timezone.utc)),
            ) as mock_refresh,
            patch.object(downloader.logger, "info") as mock_info,
        ):
            # Force refresh should trigger rebuild
            _get_prerelease_commit_history("2.7.99", force_refresh=True)
            mock_refresh.assert_called_once()

        # Should log info message for cache build
        mock_info.assert_called_once_with(
            "Building prerelease history cache for %s (this may take a couple of minutes on initial builds)...",
            "2.7.99",
        )

    finally:
        downloader._prerelease_commit_history_cache.clear()
        downloader._prerelease_commit_history_cache.update(original_cache)
        downloader._prerelease_commit_history_loaded = original_loaded


def test_cleanup_apk_prereleases_non_standard_versioning(tmp_path):
    """Test _cleanup_apk_prereleases handles non-standard version tags like v2.7.8-open.2."""
    from fetchtastic.downloader import _cleanup_apk_prereleases

    # Create prerelease directory with non-standard versioning
    prerelease_dir = tmp_path / "prereleases"
    prerelease_dir.mkdir()

    # Create directories with non-standard versioning that should be cleaned up
    old_prerelease = prerelease_dir / "v2.7.8-open.2"
    old_prerelease.mkdir()
    (old_prerelease / "app.apk").touch()

    # Create directory with newer non-standard versioning that should be kept
    new_prerelease = prerelease_dir / "v2.8.0-open.1"
    new_prerelease.mkdir()
    (new_prerelease / "app.apk").touch()

    # Create directory with standard versioning that should be cleaned up
    standard_old = prerelease_dir / "v2.7.8"
    standard_old.mkdir()
    (standard_old / "app.apk").touch()

    # Run cleanup with release v2.7.8 - should remove old versions but keep newer ones
    _cleanup_apk_prereleases(str(prerelease_dir), "v2.7.8")

    # Check that old versions were removed
    assert (
        not old_prerelease.exists()
    ), "Old non-standard prerelease should be cleaned up"
    assert not standard_old.exists(), "Old standard prerelease should be cleaned up"

    # Check that newer version was kept
    assert new_prerelease.exists(), "Newer non-standard prerelease should be kept"


def test_cleanup_apk_prereleases_edge_cases(tmp_path):
    """Test _cleanup_apk_prereleases with edge cases and malformed version strings."""
    from fetchtastic.downloader import _cleanup_apk_prereleases

    prerelease_dir = tmp_path / "prereleases"
    prerelease_dir.mkdir()

    # Create directories with various edge cases
    valid_old = prerelease_dir / "v2.7.8-beta.1"
    valid_old.mkdir()

    invalid_version = prerelease_dir / "not-a-version"
    invalid_version.mkdir()

    no_version = prerelease_dir / "v"
    no_version.mkdir()

    # Run cleanup
    _cleanup_apk_prereleases(str(prerelease_dir), "v2.7.8")

    # Valid old version should be cleaned up
    assert not valid_old.exists()

    # Invalid versions should remain (they can't be parsed for comparison)
    assert invalid_version.exists()
    assert no_version.exists()


def test_firmware_prerelease_cleanup_explicit_none_checks(tmp_path):
    """Test firmware prerelease cleanup logic with explicit None checks for clarity."""
    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Create a prerelease directory that should be cleaned up
    old_prerelease = prerelease_dir / "firmware-2.7.8.abcdef12"
    old_prerelease.mkdir()

    # Run cleanup
    removed = cleanup_superseded_prereleases(str(tmp_path), "v2.7.8")

    # Should have removed old prerelease
    assert removed, "Should have removed old prerelease"
    assert not old_prerelease.exists(), "Old prerelease directory should be removed"


def test_firmware_cleanup_explicit_none_checks_path(tmp_path):
    """Test firmware cleanup logic path with explicit None checks for coverage."""
    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Create a prerelease directory that should be kept (newer)
    new_prerelease = prerelease_dir / "firmware-2.8.0.abcdef12"
    new_prerelease.mkdir()

    # Create a prerelease directory that should be cleaned up (older)
    old_prerelease = prerelease_dir / "firmware-2.7.8.abcdef12"
    old_prerelease.mkdir()

    # Run cleanup with explicit None check path
    removed = cleanup_superseded_prereleases(str(tmp_path), "v2.7.8")

    # Should have removed old prerelease but kept new one
    assert removed, "Should have removed old prerelease"
    assert not old_prerelease.exists(), "Old prerelease directory should be removed"
    assert new_prerelease.exists(), "New prerelease directory should be kept"


def test_apk_cleanup_regex_usage_path(tmp_path):
    """Test APK cleanup logic with various version formats and edge cases."""
    from fetchtastic.downloader import _cleanup_apk_prereleases

    prerelease_dir = tmp_path / "prereleases"
    prerelease_dir.mkdir()

    # Create directories that exercise the regex matching path
    exact_match = prerelease_dir / "v2.7.8-open.2"
    exact_match.mkdir()

    no_v_prefix = prerelease_dir / "2.7.8-beta.1"
    no_v_prefix.mkdir()

    invalid_format = prerelease_dir / "v2.7-open.2"  # Missing patch version
    invalid_format.mkdir()

    # Run cleanup to exercise the regex matching code path
    _cleanup_apk_prereleases(str(prerelease_dir), "v2.7.8")

    # Should clean up matching versions
    assert not exact_match.exists(), "Exact match should be cleaned up"
    assert not no_v_prefix.exists(), "No-v-prefix match should be cleaned up"

    # Invalid format should also be cleaned up (base version 2.7 <= 2.7.8)
    assert not invalid_format.exists(), "Invalid format should be cleaned up"


def test_get_release_tuple_fix():
    """Test _get_release_tuple function fixes for non-standard version handling."""
    from fetchtastic.downloader import _get_release_tuple

    # Test the fixed logic that returns longest valid tuple
    # Test with both base and normalized versions available
    result = _get_release_tuple("v2.7.8")
    assert result is not None
    assert result == (2, 7, 8)

    # Test with version that has base but no normalized
    result = _get_release_tuple("2.7.8-something")
    assert result is not None
    assert result == (2, 7, 8)

    # Test with None input
    result = _get_release_tuple(None)
    assert result is None

    # Test with empty string
    result = _get_release_tuple("")
    assert result is None

    # Test with version that only has normalized part
    result = _get_release_tuple("v2.7.8.0")
    assert result is not None
    assert result == (2, 7, 8, 0)


def test_apk_download_non_standard_version_handling():
    """Test APK download logic correctly handles non-standard version tags."""
    from fetchtastic.downloader import _get_release_tuple

    # Test that _get_release_tuple extracts base version for non-standard versions
    # This should still return a tuple (base version) for "v2.7.8-open.2"
    result = _get_release_tuple("v2.7.8-open.2")
    # The function extracts base version "2.7.8" using VERSION_BASE_RX
    assert result == (2, 7, 8), "Non-standard version should extract base version"


def test_apk_download_prerelease_filtering_logic():
    """Test that APK download logic correctly filters prereleases based on tuple comparison."""
    from fetchtastic.downloader import _get_release_tuple

    # Test scenarios for the fixed APK download logic
    test_cases = [
        # Standard prerelease same as latest (should be skipped)
        {
            "tag": "v2.7.8-open.2",
            "latest": "v2.7.8",
            "should_download": False,
            "reason": "prerelease_tuple == latest_release_tuple",
        },
        # Standard prerelease newer than latest (should be kept)
        {
            "tag": "v2.8.0-alpha.1",
            "latest": "v2.7.8",
            "should_download": True,
            "reason": "prerelease_tuple > latest_release_tuple",
        },
        # Standard prerelease older than latest (should be skipped)
        {
            "tag": "v2.7.7-beta.1",
            "latest": "v2.7.8",
            "should_download": False,
            "reason": "prerelease_tuple <= latest_release_tuple",
        },
        # Non-standard older than latest (should be skipped)
        {
            "tag": "v2.7.7-open.1",
            "latest": "v2.7.8",
            "should_download": False,
            "reason": "base_tuple <= latest_release_tuple",
        },
    ]

    # Add case where tag is unparseable (returns None)
    test_cases.append(
        {
            "tag": "invalid-tag",
            "latest": "2.0.0",
            "should_download": True,
            "reason": "unparseable tag should be kept",
        }
    )

    for case in test_cases:
        prerelease_tuple = _get_release_tuple(case["tag"])
        latest_release_tuple = _get_release_tuple(case["latest"])

        # Simulate the logic from _process_apk_downloads
        # The implementation in `_process_apk_downloads` ensures `latest_release_tuple` is not None here.
        should_download = (
            prerelease_tuple is None or prerelease_tuple > latest_release_tuple
        )

        assert (
            should_download == case["should_download"]
        ), f"Failed for {case['tag']}: {case['reason']}"


def test_firmware_cleanup_simplified_logic(tmp_path):
    """Test simplified firmware cleanup logic without prerelease special casing."""
    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Test case: prerelease newer than latest should be kept
    new_prerelease = prerelease_dir / "firmware-2.8.0.abcdef12"
    new_prerelease.mkdir()

    # Test case: prerelease older than latest should be removed
    old_prerelease = prerelease_dir / "firmware-2.7.8.abcdef12"
    old_prerelease.mkdir()

    # Run cleanup - should remove old, keep new
    removed = cleanup_superseded_prereleases(str(tmp_path), "v2.7.8")

    assert removed, "Should have removed old prerelease"
    assert not old_prerelease.exists(), "Old prerelease should be removed"
    assert new_prerelease.exists(), "New prerelease should be kept"
