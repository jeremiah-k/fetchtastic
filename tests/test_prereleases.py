"""
Prerelease-related functionality tests for Fetchtastic downloader module.

This module contains tests for prerelease discovery, tracking, cleanup,
and related functionality.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fetchtastic import downloader
from fetchtastic.downloader import (
    matches_extract_patterns,
)


@pytest.fixture(autouse=True)
def _deny_network():
    def _no_net(*_args, **_kwargs):
        raise AssertionError("Network access is blocked in tests")

    with patch("fetchtastic.downloader.requests.get", _no_net):
        with patch("fetchtastic.downloader.requests.post", _no_net):
            yield


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
        from unittest.mock import Mock

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


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("requests.get")
def test_check_for_prereleases_download_and_cleanup(
    mock_get, mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path, write_dummy_file
):
    """Check that prerelease discovery downloads matching assets and cleans stale entries."""
    # Repo has a newer prerelease and some other dirs
    mock_fetch_dirs.return_value = [
        "firmware-2.7.7.abcdef",
        "random-not-firmware",
    ]
    # The prerelease contains a matching asset and a non-matching one
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-rak4631-2.7.7.abcdef.uf2",
            "download_url": "https://example.invalid/rak4631.uf2",
        },
        {
            "name": "firmware-heltec-v3-2.7.7.abcdef.zip",
            "download_url": "https://example.invalid/heltec.zip",
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
    mock_get.side_effect = mock_github_commit_timestamp(
        {"abcdef": "2025-01-20T12:00:00Z"}
    )

    latest_release_tag = "v2.7.6.111111"
    found, versions = downloader.check_for_prereleases(
        str(download_dir), latest_release_tag, ["rak4631-"], exclude_patterns=[]
    )

    assert found is True
    assert versions == ["firmware-2.7.7.abcdef"]

    # Matching file should exist; non-matching file should not be created by our stub
    target_file = (
        prerelease_dir / "firmware-2.7.7.abcdef" / "firmware-rak4631-2.7.7.abcdef.uf2"
    )
    assert target_file.exists()
    # Heltec non-matching file should not be downloaded
    assert not (
        prerelease_dir / "firmware-2.7.7.abcdef" / "firmware-heltec-v3-2.7.7.abcdef.zip"
    ).exists()

    # Only matching asset should have been downloaded once
    assert mock_dl.call_count == 1

    # Stale directory and stray file should be removed
    assert not stale_dir.exists()
    assert not stray.exists()


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("requests.get")
def test_check_for_prereleases_only_downloads_latest(
    mock_get, mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path, write_dummy_file
):
    """Ensure only the newest prerelease is downloaded and older ones are removed."""

    mock_fetch_dirs.return_value = [
        "firmware-2.7.4.abc123",
        "firmware-2.7.4.def456",
    ]

    def _fetch_contents(dir_name: str):
        """
        Constructs a single simulated firmware asset descriptor for a directory or tag name.

        If `dir_name` starts with the prefix "firmware-", that prefix is removed when forming the firmware file base name; otherwise the full `dir_name` is used. The returned list contains one dictionary with keys "name" (e.g., "firmware-rak4631-<suffix>.uf2") and "download_url" (a URL pointing to "<dir_name>.uf2").

        Parameters:
            dir_name (str): Directory or tag name used to construct the firmware asset entry.

        Returns:
            list[dict]: A single-element list with an asset descriptor suitable for tests.
        """
        prefix = "firmware-"
        suffix = dir_name[len(prefix) :] if dir_name.startswith(prefix) else dir_name
        return [
            {
                "name": f"firmware-rak4631-{suffix}.uf2",
                "download_url": f"https://example.invalid/{dir_name}.uf2",
            }
        ]

    mock_fetch_contents.side_effect = _fetch_contents

    mock_dl.side_effect = lambda _url, dest: write_dummy_file(dest)

    # Mock GitHub API responses for commit timestamps
    mock_get.side_effect = mock_github_commit_timestamp(
        {"abc123": "2025-01-15T10:30:00Z", "def456": "2025-01-10T08:45:00Z"}
    )

    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "firmware-2.7.4.def456").mkdir()

    found, versions = downloader.check_for_prereleases(
        str(download_dir),
        latest_release_tag="v2.7.3.000000",
        selected_patterns=["rak4631-"],
        exclude_patterns=[],
    )

    assert found is True
    assert versions == ["firmware-2.7.4.abc123"]
    assert mock_dl.call_count == 1
    mock_fetch_contents.assert_called_once_with("firmware-2.7.4.abc123")
    assert not (prerelease_dir / "firmware-2.7.4.def456").exists()


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_check_for_prereleases_no_directories(mock_fetch_dirs, tmp_path):
    """If repo has no firmware directories, function returns False, []."""
    mock_fetch_dirs.return_value = []
    found, versions = downloader.check_for_prereleases(
        str(tmp_path), "v1.0.0", ["rak4631-"], exclude_patterns=[]
    )
    assert found is False
    assert versions == []


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
@patch("requests.get")
def test_prerelease_tracking_functionality(
    mock_get, mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path, write_dummy_file
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
    mock_get.side_effect = mock_github_commit_timestamp(
        {"abcdef": "2025-01-20T12:00:00Z"}
    )

    # Run prerelease check
    found, versions = downloader.check_for_prereleases(
        str(download_dir), latest_release_tag, ["rak4631-"], exclude_patterns=[]
    )

    assert found is True
    assert len(versions) > 0

    # Check that tracking file was created (now JSON format)
    prerelease_dir = download_dir / "firmware" / "prerelease"
    tracking_file = prerelease_dir / "prerelease_tracking.json"
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
    import re

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
    info = downloader.get_prerelease_tracking_info(str(prerelease_dir))
    expected_clean_version = (
        downloader._extract_clean_version(latest_release_tag) or latest_release_tag
    )
    assert info["release"] == expected_clean_version
    assert info["prerelease_count"] > 0
    assert len(info["commits"]) > 0


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

    # Mock the repo to return a newer prerelease with same version but new hash
    with patch("fetchtastic.downloader.menu_repo.fetch_repo_directories") as mock_dirs:
        with patch(
            "fetchtastic.downloader.menu_repo.fetch_directory_contents"
        ) as mock_contents:
            mock_dirs.return_value = ["firmware-2.7.6.789abc"]

            def _dir_aware_contents(dir_name: str):
                base = dir_name.removeprefix("firmware-")
                return [
                    {
                        "name": f"firmware-rak4631-{base}.uf2",
                        "download_url": f"https://example.invalid/{dir_name}.uf2",
                    }
                ]

            mock_contents.side_effect = _dir_aware_contents

            with patch("fetchtastic.downloader.download_file_with_retry") as mock_dl:
                mock_dl.side_effect = lambda _url, dest: write_dummy_file(
                    dest, b"new data"
                )

                with patch("requests.get") as mock_get:
                    mock_get.side_effect = mock_github_commit_timestamp(
                        {"789abc": "2025-01-20T12:00:00Z"}
                    )

                    # Run prerelease check - this should clean up old directories
                    found, versions = downloader.check_for_prereleases(
                        str(download_dir),
                        "v2.7.5.baseline",
                        ["rak4631-"],
                        exclude_patterns=[],
                    )

                    # Verify the function succeeded
                    assert found is True
                    assert "firmware-2.7.6.789abc" in versions

                    # Verify old directories were removed
                    assert (
                        not old_dir1.exists()
                    ), "Old prerelease directory should be removed"
                    assert (
                        not old_dir2.exists()
                    ), "Old prerelease directory should be removed"

                # Verify new directory was created
                new_dir = prerelease_dir / "firmware-2.7.6.789abc"
                assert new_dir.exists(), "New prerelease directory should be created"


def test_prerelease_tracking_json_format(tmp_path):
    """Test the new JSON tracking file format and functions."""
    prerelease_dir = tmp_path / "prerelease"
    prerelease_dir.mkdir()

    # Test update_prerelease_tracking function
    latest_release = "v2.7.6.111111"
    prerelease1 = "firmware-2.7.7.abcdef"
    prerelease2 = "firmware-2.7.8.fedcba"  # Valid hex commit hash

    # Add first prerelease
    num1 = downloader.update_prerelease_tracking(
        str(prerelease_dir), latest_release, prerelease1
    )
    assert num1 == 1, "First prerelease should be #1"

    # Add second prerelease
    num2 = downloader.update_prerelease_tracking(
        str(prerelease_dir), latest_release, prerelease2
    )
    assert num2 == 2, "Second prerelease should be #2"

    # Test reading the tracking file
    info = downloader.get_prerelease_tracking_info(str(prerelease_dir))
    expected_clean_version = (
        downloader._extract_clean_version(latest_release) or latest_release
    )
    assert info["release"] == expected_clean_version
    assert info["prerelease_count"] == 2
    assert "2.7.7.abcdef" in info["commits"]
    assert "2.7.8.fedcba" in info["commits"]

    # Test that new release resets the tracking
    new_release = "v2.7.9.newrelease"
    num3 = downloader.update_prerelease_tracking(
        str(prerelease_dir),
        new_release,
        "firmware-2.7.10.abc123",  # Valid hex
    )
    assert num3 == 1, "First prerelease after new release should be #1"

    # Verify tracking was reset
    info = downloader.get_prerelease_tracking_info(str(prerelease_dir))
    expected_clean_new_release = (
        downloader._extract_clean_version(new_release) or new_release
    )
    assert info["release"] == expected_clean_new_release
    assert info["prerelease_count"] == 1
    assert "2.7.10.abc123" in info["commits"]
    assert "2.7.7.abcdef" not in info["commits"], "Old commits should be cleared"


def test_prerelease_tracking_edge_cases(tmp_path):
    """Test edge cases in prerelease tracking system."""
    prerelease_dir = tmp_path / "prerelease"
    prerelease_dir.mkdir()

    # Test with malformed prerelease directory name (should not be tracked)
    malformed_prerelease = "not-a-valid-format"
    num = downloader.update_prerelease_tracking(
        str(prerelease_dir), "v2.7.6", malformed_prerelease
    )
    assert (
        num == 0
    ), "Should not track malformed directory names (improved data consistency)"

    # Test reading empty tracking file (create a fresh directory)
    empty_test_dir = tmp_path / "empty_test"
    empty_test_dir.mkdir()

    # Create empty text file for backwards compatibility test
    empty_tracking_file = empty_test_dir / "prerelease_commits.txt"
    with open(empty_tracking_file, "w") as f:
        f.write("")  # Empty file

    info = downloader.get_prerelease_tracking_info(str(empty_test_dir))
    assert info == {}, "Should return empty dict for empty tracking file"

    # Test reading tracking file with old format (no "Release:" prefix)
    old_format_dir = tmp_path / "old_format_test"
    old_format_dir.mkdir()
    old_format_file = old_format_dir / "prerelease_commits.txt"
    with open(old_format_file, "w") as f:
        f.write("abcdef\nghijkl\n")  # Old format without Release: prefix

    info = downloader.get_prerelease_tracking_info(str(old_format_dir))
    assert info["release"] == "unknown"
    assert info["prerelease_count"] == 2
    assert "abcdef" in info["commits"]
    assert "ghijkl" in info["commits"]

    # Test reading non-existent tracking file
    no_file_dir = tmp_path / "no_file_test"
    no_file_dir.mkdir()
    info = downloader.get_prerelease_tracking_info(str(no_file_dir))
    assert info == {}, "Should return empty dict for non-existent file"


def test_prerelease_existing_files_tracking(tmp_path):
    """Test that existing prerelease files are properly tracked."""
    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    version_dir = prerelease_dir / "firmware-2.7.7.abcdef"
    version_dir.mkdir(parents=True)

    # Create an existing file
    existing_file = version_dir / "firmware-rak4631-2.7.7.abcdef.uf2"
    existing_file.write_bytes(b"existing data")

    with patch("fetchtastic.downloader.menu_repo.fetch_repo_directories") as mock_dirs:
        with patch(
            "fetchtastic.downloader.menu_repo.fetch_directory_contents"
        ) as mock_contents:
            mock_dirs.return_value = ["firmware-2.7.7.abcdef"]

            def _dir_aware_contents(dir_name: str):
                base = dir_name.removeprefix("firmware-")
                return [
                    {
                        "name": f"firmware-rak4631-{base}.uf2",
                        "download_url": f"https://example.invalid/{dir_name}.uf2",
                    }
                ]

            mock_contents.side_effect = _dir_aware_contents

            with patch("requests.get") as mock_get:
                mock_get.side_effect = mock_github_commit_timestamp(
                    {"abcdef": "2025-01-20T12:00:00Z"}
                )

                found, versions = downloader.check_for_prereleases(
                    str(download_dir),
                    "v2.7.6.111111",
                    ["rak4631-"],
                    exclude_patterns=[],
                )

                # Should track existing files but not report as "downloaded"
                assert found is False  # No new downloads occurred
                assert (
                    "firmware-2.7.7.abcdef" in versions
                )  # But directory is still tracked

            # And tracking JSON should reflect that commit
            info = downloader.get_prerelease_tracking_info(str(prerelease_dir))
            assert "2.7.7.abcdef" in info.get("commits", [])


def test_get_prerelease_tracking_info_error_handling():
    """Test error handling in get_prerelease_tracking_info."""
    import tempfile

    # uses top-level imports: Path
    from fetchtastic.downloader import get_prerelease_tracking_info

    with tempfile.TemporaryDirectory() as tmp_dir:
        prerelease_dir = Path(tmp_dir)

        # Test with non-existent directory
        result = get_prerelease_tracking_info(str(prerelease_dir / "nonexistent"))
        assert result == {}

        # Test with corrupted tracking file
        tracking_file = prerelease_dir / "prerelease_commits.txt"
        tracking_file.write_bytes(b"\xff\xfe\x00\x00")  # Invalid UTF-8

        result = get_prerelease_tracking_info(str(prerelease_dir))
        assert result == {}  # Should handle decode errors gracefully


def test_update_prerelease_tracking_error_handling():
    """Test error handling in update_prerelease_tracking."""
    import tempfile

    # uses top-level imports: Path
    from fetchtastic.downloader import update_prerelease_tracking

    if os.name == "nt":
        pytest.skip("Permission bits unreliable on Windows")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Test with read-only directory (should handle write errors)
        prerelease_dir = Path(tmp_dir) / "readonly"
        prerelease_dir.mkdir()
        prerelease_dir.chmod(0o444)  # Read-only

        try:
            # Should handle write errors gracefully and return count of existing commits
            result = update_prerelease_tracking(
                str(prerelease_dir), "v2.7.8", "firmware-2.7.9.abc123"
            )
            assert result == 0  # Should return actual persisted count (0 on failure)
        finally:
            # Restore permissions for cleanup
            prerelease_dir.chmod(0o755)


@pytest.mark.core_downloads
def test_get_commit_hash_from_dir():
    """Test extracting commit hash from prerelease directory names."""
    from fetchtastic.downloader import _get_commit_hash_from_dir

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
    from datetime import datetime, timedelta, timezone
    from unittest.mock import Mock, patch

    from fetchtastic.downloader import (
        _commit_timestamp_cache,
        clear_commit_timestamp_cache,
        get_commit_timestamp,
    )

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
        "fetchtastic.downloader.requests.get", return_value=mock_response
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
        "fetchtastic.downloader.requests.get", return_value=mock_response
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
        "fetchtastic.downloader.requests.get", return_value=mock_response
    ) as mock_get:
        result4 = get_commit_timestamp("meshtastic", "firmware", "abcdef123")
        assert result4 == result1
        assert mock_get.call_count == 1  # Should refresh expired cache

    # Clear cache after test
    clear_commit_timestamp_cache()


@pytest.mark.core_downloads
def test_get_commit_timestamp_error_handling():
    """Test error handling in get_commit_timestamp."""
    from datetime import datetime
    from unittest.mock import Mock, patch

    import requests

    from fetchtastic.downloader import (
        clear_commit_timestamp_cache,
        get_commit_timestamp,
    )

    clear_commit_timestamp_cache()

    # Test HTTP error
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    mock_response.status_code = 404

    with patch("fetchtastic.downloader.requests.get", return_value=mock_response):
        result = get_commit_timestamp("meshtastic", "firmware", "badcommit")
        assert result is None

    # Test JSON decode error
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_response.status_code = 200
    mock_response.ok = True

    with patch("fetchtastic.downloader.requests.get", return_value=mock_response):
        result = get_commit_timestamp("meshtastic", "firmware", "badcommit")
        assert result is None

    # Test missing date in response
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"commit": {"committer": {}}}  # Missing date
    mock_response.status_code = 200
    mock_response.ok = True

    with patch("fetchtastic.downloader.requests.get", return_value=mock_response):
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

    with patch("fetchtastic.downloader.requests.get", return_value=mock_response):
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
    with patch("fetchtastic.downloader.requests.get", return_value=mock_response):
        result3 = get_commit_timestamp(
            "meshtastic", "firmware", "abcdef123", force_refresh=True
        )
        assert result3 == result1  # Should still work

    # Clear cache after test
    clear_commit_timestamp_cache()


@pytest.mark.core_downloads
def test_normalize_version():
    """Test version normalization function."""
    from fetchtastic.downloader import _normalize_version

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
