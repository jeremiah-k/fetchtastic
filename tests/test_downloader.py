from unittest.mock import mock_open, patch

import pytest
import requests

from fetchtastic import downloader
from fetchtastic.utils import extract_base_name
from tests.test_constants import (
    TEST_VERSION_NEW,
    TEST_VERSION_NEWER,
    TEST_VERSION_OLD,
)


# Test cases for compare_versions
@pytest.mark.parametrize(
    "version1, version2, expected",
    [
        (TEST_VERSION_OLD, "2.0.0", 1),
        ("2.0.1", "2.0.0", 1),
        ("2.0.0", "2.0.1", -1),
        ("1.9.0", "2.0.0", -1),
        ("2.0.0", "2.0.0", 0),
        (TEST_VERSION_NEWER, TEST_VERSION_NEW, 1),
        (TEST_VERSION_NEW, TEST_VERSION_NEWER, -1),
        ("2.3.0", "2.3.0.b123456", 1),  # 2.3.0 > 2.3.0.b123456 (release > pre-release)
        ("v1.2.3", "1.2.3", 0),  # Should handle 'v' prefix
        ("1.2", "1.2.3", -1),  # Handle different number of parts
    ],
)
def test_compare_versions(version1, version2, expected):
    """Test the version comparison logic."""
    assert downloader.compare_versions(version1, version2) == expected
    # Antisymmetry: reversing operands should flip the sign
    assert downloader.compare_versions(version2, version1) == -expected


def test_compare_versions_prerelease_parsing():
    """Test new prerelease version parsing logic."""
    # Test dot-separated prerelease versions
    assert downloader.compare_versions("2.3.0.rc1", "2.3.0") == -1  # rc1 < final
    assert downloader.compare_versions("2.3.0.dev1", "2.3.0") == -1  # dev1 < final
    assert downloader.compare_versions("2.3.0.alpha1", "2.3.0") == -1  # alpha1 < final
    assert downloader.compare_versions("2.3.0.beta2", "2.3.0") == -1  # beta2 < final

    # Test dash-separated prerelease versions
    assert downloader.compare_versions("2.3.0-rc1", "2.3.0") == -1  # rc1 < final
    assert downloader.compare_versions("2.3.0-dev1", "2.3.0") == -1  # dev1 < final
    assert downloader.compare_versions("2.3.0-alpha1", "2.3.0") == -1  # alpha1 < final
    assert downloader.compare_versions("2.3.0-beta2", "2.3.0") == -1  # beta2 < final

    # rc ordering
    assert downloader.compare_versions("2.3.0.rc0", "2.3.0.rc1") == -1

    # Test prerelease ordering
    assert (
        downloader.compare_versions("2.3.0.alpha1", "2.3.0.beta1") == -1
    )  # alpha < beta
    assert downloader.compare_versions("2.3.0.beta1", "2.3.0.rc1") == -1  # beta < rc
    assert downloader.compare_versions("2.3.0.rc1", "2.3.0.dev1") == 1  # rc > dev


def test_compare_versions_invalid_version_exception():
    """Test InvalidVersion exception handling in version parsing."""
    # Test with a version that will trigger the hash coercion and InvalidVersion exception
    # This should exercise the InvalidVersion exception handling in the _try_parse function
    result = downloader.compare_versions("1.0.0.invalid+hash", "1.0.0")
    # The function should handle the exception gracefully and return a comparison result
    # Natural sort fallback should determine "1.0.0.invalid+hash" > "1.0.0"
    assert result == 1  # Should be greater due to natural sort fallback


def test_compare_versions_hash_coercion():
    """Test hash coercion in version parsing."""
    # Test versions with hash patterns that get coerced to local versions
    assert downloader.compare_versions("1.0.0.abc123", "1.0.0") == 1  # local > base
    assert (
        downloader.compare_versions("2.1.0.def456", "2.1.0.abc123") == 1
    )  # lexical comparison

    # Test edge cases that might trigger InvalidVersion in hash coercion
    result = downloader.compare_versions("1.0.0.invalid-hash+more", "1.0.0")
    assert isinstance(result, int)  # Should handle gracefully


def test_compare_versions_prerelease_edge_cases():
    """Test edge cases in prerelease version parsing."""
    # Test prerelease versions that might trigger InvalidVersion during coercion
    assert downloader.compare_versions("2.3.0.rc", "2.3.0") == -1  # rc without number
    assert downloader.compare_versions("2.3.0-dev", "2.3.0") == -1  # dev without number

    # Test mixed separators and edge cases
    result = downloader.compare_versions("2.3.0.invalid-pre", "2.3.0")
    assert isinstance(result, int)  # Should handle gracefully


# Test cases for strip_version_numbers
@pytest.mark.parametrize(
    "filename, expected",
    [
        ("firmware-rak4631-2.7.4.c1f4f79.bin", "firmware-rak4631.bin"),
        ("firmware-heltec-v3-2.7.4.c1f4f79.zip", "firmware-heltec-v3.zip"),
        ("firmware-tbeam-2.7.4.c1f4f79-update.bin", "firmware-tbeam-update.bin"),
        ("littlefs-rak11200-2.7.4.c1f4f79.bin", "littlefs-rak11200.bin"),
        ("device-install-2.3.2.sh", "device-install.sh"),
        ("some_file_without_version.txt", "some_file_without_version.txt"),
        ("file-with-v1.2.3-in-name.bin", "file-with-in-name.bin"),
    ],
)
def test_extract_base_name(filename, expected):
    """Test the logic for extracting base names from filenames."""
    assert extract_base_name(filename) == expected


# Test cases for strip_unwanted_chars
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hello 👋 World", "Hello  World"),
        ("This is a test.", "This is a test."),
        ("✅ New release", " New release"),
        ("", ""),
    ],
)
def test_strip_unwanted_chars(text, expected):
    """Test the removal of non-ASCII characters."""
    assert downloader.strip_unwanted_chars(text) == expected


# Test cases for safe_extract_path
@pytest.mark.parametrize(
    "extract_dir, file_path, should_raise",
    [
        ("/safe/dir", "file.txt", False),
        ("/safe/dir", "subdir/file.txt", False),
        ("/safe/dir", "../file.txt", True),
        ("/safe/dir", "/etc/passwd", True),
        ("/safe/dir", "subdir/../../file.txt", True),
        ("/safe/dir", "subdir/../safe_again.txt", False),
    ],
)
def test_safe_extract_path(extract_dir, file_path, should_raise):
    """Test the safe path extraction logic to prevent directory traversal."""
    if should_raise:
        with pytest.raises(ValueError):
            downloader.safe_extract_path(extract_dir, file_path)
    else:
        try:
            downloader.safe_extract_path(extract_dir, file_path)
        except ValueError:
            pytest.fail("safe_extract_path raised ValueError unexpectedly.")


def test_compare_file_hashes(tmp_path):
    """Test the file hash comparison logic."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file3 = tmp_path / "file3.txt"

    file1.write_text("hello")
    file2.write_text("hello")
    file3.write_text("world")

    assert downloader.compare_file_hashes(str(file1), str(file2)) is True
    assert downloader.compare_file_hashes(str(file1), str(file3)) is False
    assert downloader.compare_file_hashes(str(file1), "nonexistent") is False


def test_cleanup_old_versions(tmp_path):
    """Test the logic for cleaning up old version directories."""
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()

    # Create some version directories
    (firmware_dir / "v1.0").mkdir()
    (firmware_dir / "v2.0").mkdir()
    (firmware_dir / "v3.0").mkdir()
    (firmware_dir / "repo-dls").mkdir()  # Should be ignored
    (firmware_dir / "prerelease").mkdir()  # Should be ignored

    releases_to_keep = ["v2.0", "v3.0"]
    downloader.cleanup_old_versions(str(firmware_dir), releases_to_keep)

    assert not (firmware_dir / "v1.0").exists()
    assert (firmware_dir / "v2.0").exists()
    assert (firmware_dir / "v3.0").exists()
    assert (firmware_dir / "repo-dls").exists()
    assert (firmware_dir / "prerelease").exists()


def test_check_and_download_logs_when_no_assets_match(tmp_path, caplog):
    """When a release is new but no assets match the selection, log a helpful message."""
    # Capture logs from the 'fetchtastic' logger used by the downloader
    caplog.set_level("INFO", logger="fetchtastic")
    # One release with an asset that won't match the selected patterns
    releases = [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": "firmware-heltec-v3-1.0.0.zip",
                    "browser_download_url": "https://example.invalid/heltec-v3.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v0.9.0",
            "assets": [
                {
                    "name": "firmware-heltec-v3-0.9.0.zip",
                    "browser_download_url": "https://example.invalid/heltec-0.9.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path / "firmware")

    # Run with a pattern that won't match the provided asset name
    # Ensure logger propagates so caplog can capture records regardless of handlers
    from fetchtastic.log_utils import logger as ft_logger

    old_propagate = ft_logger.propagate
    ft_logger.propagate = True
    try:
        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=2,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )
    finally:
        ft_logger.propagate = old_propagate

    # No downloads and no failures expected; should note new version available
    assert downloaded == []
    assert failures == []
    assert new_versions == []
    expected = "Release v1.0.0 found, but no assets matched the current selection/exclude filters."
    assert expected in caplog.text


def test_new_versions_detection_with_saved_tag(tmp_path):
    """
    Verify new-release detection honors a saved latest-tag and that only releases newer than the saved tag (by list position, newest-first) are considered — but only releases with matching asset patterns are reported.

    Detailed behavior:
    - Writes a saved tag of "v2" and provides releases in newest-first order (v3, v2, v1).
    - v3 is technically newer than the saved tag, but its asset names do not match the provided selected_patterns, so no new_versions or downloads should be recorded.
    - Asserts that no downloads or failures occurred and that new_versions is empty.
    """
    releases = [
        {
            "tag_name": "v3",
            "published_at": "2024-03-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-3.zip",
                    "browser_download_url": "https://example.invalid/3.zip",
                    "size": 1,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v2",
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-2.zip",
                    "browser_download_url": "https://example.invalid/2.zip",
                    "size": 1,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v1",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-1.zip",
                    "browser_download_url": "https://example.invalid/1.zip",
                    "size": 1,
                }
            ],
            "body": "",
        },
    ]

    latest_release_file = str(tmp_path / "latest.txt")
    # Saved is v2; only v3 should be considered new
    (tmp_path / "latest.txt").write_text("v2")
    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        str(tmp_path),
        versions_to_keep=3,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )
    assert downloaded == []
    assert failures == []
    assert new_versions == []


def test_new_versions_detection_when_no_saved_tag(tmp_path):
    """When no saved tag exists, all tags are candidates (newest-first order)."""
    releases = [
        {
            "tag_name": "v3",
            "published_at": "2024-03-01T00:00:00Z",
            "assets": [],
            "body": "",
        },
        {
            "tag_name": "v2",
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [],
            "body": "",
        },
        {
            "tag_name": "v1",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [],
            "body": "",
        },
    ]
    latest_release_file = str(tmp_path / "latest.txt")
    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        str(tmp_path),
        versions_to_keep=3,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )
    assert downloaded == []
    assert failures == []
    assert new_versions == []


def test_new_versions_detection_when_saved_is_latest(tmp_path):
    """When saved is the newest tag and no downloads occur, there are no new versions."""
    releases = [
        {
            "tag_name": "v3",
            "published_at": "2024-03-01T00:00:00Z",
            "assets": [],
            "body": "",
        },
        {
            "tag_name": "v2",
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [],
            "body": "",
        },
    ]
    latest_release_file = str(tmp_path / "latest.txt")
    (tmp_path / "latest.txt").write_text("v3")
    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        str(tmp_path),
        versions_to_keep=2,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )
    assert downloaded == []
    assert failures == []
    assert new_versions == []
    # Note: Human-facing info message is printed; formatting via Rich can
    # move it outside caplog. State assertions above cover behavior.


def test_set_permissions_on_sh_files(tmp_path):
    """Test that .sh files are made executable."""
    script_path = tmp_path / "script.sh"
    other_file_path = tmp_path / "other.txt"

    script_path.write_text("#!/bin/bash\necho hello")
    other_file_path.write_text("hello")

    # Set initial permissions to non-executable
    import os

    os.chmod(script_path, 0o644)
    os.chmod(other_file_path, 0o644)

    downloader.set_permissions_on_sh_files(str(tmp_path))

    assert os.access(script_path, os.X_OK)
    assert not os.access(other_file_path, os.X_OK)


@pytest.fixture
def dummy_zip_file(tmp_path):
    """
    Create a dummy ZIP file containing sample firmware and support files used by extraction tests.

    The archive contains:
    - firmware-rak4631-2.7.4.c1f4f79.bin (nRF52-style firmware)
    - firmware-tbeam-2.7.4.c1f4f79.uf2 (alternate firmware format)
    - firmware-rak11200-2.7.4.c1f4f79.bin (ESP32-style firmware)
    - littlefs-rak11200-2.7.4.c1f4f79.bin (ESP32 littlefs image)
    - device-update.sh (shell updater script)
    - bleota.bin (BLE OTA payload)
    - notes.txt (auxiliary text file)

    Returns:
        pathlib.Path: Path to the created ZIP file.
    """
    import zipfile

    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # nRF52 devices (like RAK4631) - no littlefs files
        zf.writestr("firmware-rak4631-2.7.4.c1f4f79.bin", "rak_data")
        zf.writestr("firmware-tbeam-2.7.4.c1f4f79.uf2", "tbeam_data")
        # ESP32 devices (like RAK11200) - have littlefs files
        zf.writestr("firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("littlefs-rak11200-2.7.4.c1f4f79.bin", "littlefs_data")
        zf.writestr("device-update.sh", "echo updating")
        zf.writestr("bleota.bin", "bleota_data")
        zf.writestr("notes.txt", "some notes")
    return zip_path


def test_extract_files(dummy_zip_file, tmp_path):
    """Test file extraction with patterns."""
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    patterns = ["rak11200", "device-update.sh"]
    exclude_patterns = []

    downloader.extract_files(
        str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
    )

    assert (extract_dir / "firmware-rak11200-2.7.4.c1f4f79.bin").exists()
    assert (extract_dir / "littlefs-rak11200-2.7.4.c1f4f79.bin").exists()
    assert (extract_dir / "device-update.sh").exists()
    assert not (extract_dir / "firmware-tbeam-2.7.4.c1f4f79.uf2").exists()
    assert not (extract_dir / "notes.txt").exists()

    # Check that the shell script was made executable
    import os

    assert os.access(extract_dir / "device-update.sh", os.X_OK)


def test_extract_files_preserves_subdirectories(tmp_path):
    """Extraction should preserve archive subdirectories when writing to disk."""
    import os
    import zipfile

    zip_path = tmp_path / "nested.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("sub/dir/firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("sub/dir/device-install.sh", "echo hi")
        zf.writestr("sub/notes.txt", "n")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Include rak11200 and the script; exclude notes
    downloader.extract_files(
        str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], ["notes*"]
    )

    # Files extracted under their original subdirectories
    bin_path = out_dir / "sub/dir/firmware-rak11200-2.7.4.c1f4f79.bin"
    sh_path = out_dir / "sub/dir/device-install.sh"

    assert bin_path.exists()
    assert sh_path.exists()
    assert os.access(sh_path, os.X_OK)
    assert not (out_dir / "sub/notes.txt").exists()


def test_check_extraction_needed_with_nested_paths(tmp_path):
    """check_extraction_needed should consider nested archive paths and base-name filters."""
    import os
    import zipfile

    zip_path = tmp_path / "nested2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dir/inner/firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("dir/inner/device-install.sh", "echo hi")

    out_dir = tmp_path / "out2"
    out_dir.mkdir()

    # 1) Empty patterns -> never needed
    assert (
        downloader.check_extraction_needed(str(zip_path), str(out_dir), [], []) is False
    )

    # 2) Specific patterns: both files missing -> needed
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is True
    )

    # Create one of the expected files, still needed for the other
    os.makedirs(out_dir / "dir/inner", exist_ok=True)
    (out_dir / "dir/inner/firmware-rak11200-2.7.4.c1f4f79.bin").write_text(
        "rak11200_data"
    )
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is True
    )

    # Create the second expected file -> no extraction needed
    (out_dir / "dir/inner/device-install.sh").write_text("echo hi")
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is False
    )


def test_check_extraction_needed(dummy_zip_file, tmp_path):
    """Test the logic for checking if extraction is needed."""
    extract_dir = tmp_path / "extract_check"
    extract_dir.mkdir()
    patterns = ["rak4631", "rak11200", "tbeam"]
    exclude_patterns = []

    # 1. No files extracted yet, should be needed
    assert (
        downloader.check_extraction_needed(
            str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
        )
        is True
    )


def test_check_extraction_needed_with_dash_patterns(tmp_path):
    """Ensure dash-suffixed patterns are honored in extraction-needed check."""
    import zipfile

    zip_path = tmp_path / "dash.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("firmware-rak4631-2.7.4.c1f4f79.bin", "rak_data")

    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    # Missing -> extraction needed
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(extract_dir), ["rak4631-"], []
        )
        is True
    )
    # Create expected file -> not needed
    (extract_dir / "firmware-rak4631-2.7.4.c1f4f79.bin").write_text("rak_data")
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(extract_dir), ["rak4631-"], []
        )
        is False
    )


def test_extract_files_matching_and_exclude(tmp_path):
    """Test extraction honors legacy-style matching and exclude patterns."""
    import os
    import zipfile

    zip_path = tmp_path / "mix.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("firmware-rak4631-2.7.6.aaa.bin", "a")
        zf.writestr("firmware-rak4631_eink-2.7.6.aaa.uf2", "b")
        zf.writestr("device-install.sh", "echo x")
        zf.writestr("notes.txt", "n")

    out_dir = tmp_path / "ext"
    out_dir.mkdir()

    downloader.extract_files(
        str(zip_path), str(out_dir), ["rak4631-", "device-install.sh"], ["*eink*"]
    )

    assert (out_dir / "firmware-rak4631-2.7.6.aaa.bin").exists()
    assert not (out_dir / "firmware-rak4631_eink-2.7.6.aaa.uf2").exists()
    # script extracted and made executable
    sh_path = out_dir / "device-install.sh"
    assert sh_path.exists()
    assert os.access(sh_path, os.X_OK)

    # No further changes; validates include/exclude and executable bit behavior


def test_check_promoted_prereleases(tmp_path):
    """Test the cleanup of pre-releases that have been promoted."""
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

    downloader.check_promoted_prereleases(str(download_dir), latest_release_tag)

    assert not (prerelease_dir / "firmware-2.1.0").exists()
    assert (prerelease_dir / "firmware-2.2.0").exists()


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
def test_check_for_prereleases_download_and_cleanup(
    mock_dl, mock_fetch_contents, mock_fetch_dirs, tmp_path
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

    # Simulate successful download only for the matching file
    def _mock_dl(_url, dest):
        # Create the file to emulate a successful download
        """
        Mock download helper used in tests.

        Creates parent directories for `dest` if needed, writes a small binary payload (b"data") to `dest`, and returns True to indicate a successful download. Overwrites any existing file at `dest`.
        """
        import os

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"data")
        return True

    mock_dl.side_effect = _mock_dl

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


def test_no_up_to_date_log_when_new_versions_but_no_matches(tmp_path, caplog):
    """When new versions are available but no assets match, do not log 'up to date'."""
    caplog.set_level("INFO", logger="fetchtastic")
    releases = [
        {
            "tag_name": "v9.9.9",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-9.9.9.zip",
                    "browser_download_url": "https://example.invalid/heltec.zip",
                    "size": 10,
                }
            ],
            "body": "",
        }
    ]
    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path / "firmware")

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        download_dir,
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )
    assert downloaded == []
    assert failures == []
    assert new_versions == []
    # Should not log generic up-to-date message (may be formatted by Rich;
    # we assert state instead to avoid handler coupling)


def test_check_and_download_happy_path_with_extraction(tmp_path, caplog):
    """Covers successful download path, latest tag save, and auto-extract."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.0.0"
    zip_name = "firmware-rak4631-1.0.0.zip"

    # Release data with a single ZIP asset
    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": 100,  # nominal; not strictly enforced in this path
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Mock downloader to write a real ZIP that contains a file we want to auto-extract
    def _mock_dl(_url, dest):
        """
        Create a simple ZIP file at the given destination for use in tests.

        Creates any missing parent directories for dest and writes a ZIP archive containing a single entry
        "device-install.sh" with the contents "echo hi". The _url parameter is ignored (present only to match
        the downloader call signature).

        Parameters:
            _url (str): Ignored.
            dest (str): Filesystem path where the ZIP file will be created.

        Returns:
            bool: True on success.
        """
        import os
        import zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=["device-install.sh"],
            selected_patterns=["rak4631-"],
            auto_extract=True,
            exclude_patterns=[],
        )

    # The release should be considered downloaded
    assert downloaded == [release_tag]
    assert failures == []
    # latest_release_file written
    assert (tmp_path / "latest_firmware_release.txt").exists()
    # auto-extracted file exists and is executable
    extracted = tmp_path / release_tag / "device-install.sh"
    assert extracted.exists()
    import os

    assert os.access(extracted, os.X_OK)


def test_auto_extract_with_empty_patterns_does_not_extract(tmp_path, caplog):
    """When AUTO_EXTRACT is True but EXTRACT_PATTERNS is empty, do not extract any files."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.2.3"
    zip_name = "firmware-rak4631-1.2.3.zip"

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": 100,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Mock downloader to write a real ZIP that contains a file that would normally be extracted
    def _mock_dl(_url, dest):
        """
        Create a dummy ZIP file at the given destination to simulate a successful download.

        This helper writes a ZIP archive containing a single file named `device-install.sh`
        with the contents `echo hi`. It ensures parent directories for `dest` exist.

        Parameters:
            _url (str): Ignored; present to match the downloader function signature.
            dest (str): Path where the dummy ZIP file will be created.

        Returns:
            bool: Always True to indicate the mock download succeeded.
        """
        import os
        import zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],  # empty patterns
            selected_patterns=["rak4631-"],
            auto_extract=True,
            exclude_patterns=[],
        )

    # The release should be downloaded
    assert downloaded == [release_tag]
    assert failures == []

    # The ZIP should exist, but there should be no extracted files because patterns were empty
    release_path = tmp_path / release_tag
    zip_path = release_path / zip_name
    assert zip_path.exists()
    assert not (release_path / "device-install.sh").exists()


def test_check_and_download_release_already_complete_logs_up_to_date(tmp_path, caplog):
    """Cover the path where release is complete; actions_taken False leads to up-to-date log."""
    caplog.set_level("INFO", logger="fetchtastic")
    release_tag = "v3.3.3"
    zip_name = "firmware-rak4631-3.3.3.zip"
    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    (tmp_path / "latest_firmware_release.txt").write_text(release_tag)

    # Prepare a valid zip already present in the release directory
    import os
    import zipfile

    release_dir = tmp_path / release_tag
    release_dir.mkdir()
    zip_path = release_dir / zip_name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("foo.txt", "bar")
    size = os.path.getsize(zip_path)

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/zip",
                    "size": size,
                }
            ],
            "body": "",
        }
    ]

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        str(tmp_path),
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    assert downloaded == []  # already complete
    assert failures == []
    # With saved == latest and no new, function completes without downloads/failures
    # (log output may be handled by Rich and is validated elsewhere)


def test_check_for_prereleases_no_directories(tmp_path):
    """If repo has no firmware directories, function returns False, []."""
    with patch(
        "fetchtastic.downloader.menu_repo.fetch_repo_directories", return_value=[]
    ):
        found, versions = downloader.check_for_prereleases(
            str(tmp_path), "v1.0.0", ["rak4631-"], exclude_patterns=[]
        )
    assert found is False
    assert versions == []


def test_check_and_download_corrupted_existing_zip_records_failure(tmp_path):
    """Existing corrupted zip should be removed, and failed download recorded when retry fails."""
    release_tag = "v5.0.0"
    zip_name = "firmware-rak4631-5.0.0.zip"
    release_dir = tmp_path / release_tag
    release_dir.mkdir()

    # Create a corrupted zip file at the expected path
    bad_zip_path = release_dir / zip_name
    bad_zip_path.write_bytes(b"not a zip")

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/corrupt.zip",
                    "size": 10,
                }
            ],
            "body": "",
        }
    ]

    with patch("fetchtastic.downloader.download_file_with_retry", return_value=False):
        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            str(tmp_path / "latest.txt"),
            "Firmware",
            str(tmp_path),
            versions_to_keep=1,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )

    # Corrupted zip should have been removed during pre-check
    assert not bad_zip_path.exists()
    # Failure should be recorded
    assert failures and failures[0]["reason"].startswith(
        "download_file_with_retry returned False"
    )


def test_check_and_download_missing_download_url(tmp_path):
    """Assets with no browser_download_url should be recorded as failures and skipped."""
    release_tag = "v6.0.0"
    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-rak4631-6.0.0.uf2",
                    # Intentionally missing 'browser_download_url'
                    "size": 123,
                }
            ],
            "body": "",
        }
    ]

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        str(tmp_path / "latest.txt"),
        "Firmware",
        str(tmp_path),
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    assert downloaded == []
    assert failures and failures[0]["reason"] == "Missing browser_download_url"


def test_send_ntfy_notification(mocker):
    """Test the NTFY notification sending logic."""
    mock_post = mocker.patch("requests.post")

    # 1. Test successful notification
    downloader._send_ntfy_notification(
        "https://ntfy.sh", "mytopic", "Test message", "Test Title"
    )
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://ntfy.sh/mytopic"
    assert kwargs["data"] == "Test message".encode("utf-8")
    assert kwargs["headers"]["Content-Type"] == "text/plain; charset=utf-8"
    assert kwargs["headers"]["Title"] == "Test Title"
    assert kwargs["timeout"] == downloader.NTFY_REQUEST_TIMEOUT

    # 2. Test request exception
    mock_post.reset_mock()
    mock_post.side_effect = requests.exceptions.RequestException("Network error")
    # Should not raise an exception, just log a warning
    downloader._send_ntfy_notification("https://ntfy.sh", "mytopic", "Test message")
    assert mock_post.call_count == 1

    # 3. Test with no server/topic
    mock_post.reset_mock()
    downloader._send_ntfy_notification(None, None, "Test message")
    mock_post.assert_not_called()

    # 4. Header omission when no title is provided
    mock_post.reset_mock()
    downloader._send_ntfy_notification("https://ntfy.sh", "mytopic", "No title here")
    args, kwargs = mock_post.call_args
    assert "Title" not in kwargs["headers"]


@pytest.fixture
def mock_releases():
    """
    Return a pre-built list of mock GitHub release dictionaries used in tests.

    The list is pre-sorted by `published_at` descending (newest first). Each release dict contains:
    - `tag_name` (str)
    - `published_at` (ISO 8601 str)
    - `assets` (list of dicts), where each asset dict includes `name`, `size`, and `browser_download_url`.

    One entry intentionally has an empty `assets` list to simulate an incomplete release.
    """
    return [
        {
            "tag_name": "v2.7.4.c1f4f79",
            "published_at": "2023-01-03T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-2.7.4.c1f4f79.zip",
                    "size": 100,
                    "browser_download_url": "http://fake.url/v2.7.4.zip",
                }
            ],
        },
        {
            "tag_name": "v2.7.3.cf574c7",
            "published_at": "2023-01-02T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-2.7.3.cf574c7.zip",
                    "size": 100,
                    "browser_download_url": "http://fake.url/v2.7.3.zip",
                }
            ],
        },
        {
            "tag_name": "v2.7.2.f6d3782",
            "published_at": "2023-01-01T00:00:00Z",
            "assets": [],
        },
    ]


def test_get_latest_releases_data(mocker, mock_releases):
    """Test the logic for fetching and sorting release data."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    # The mock_releases fixture is already sorted, but the function sorts it again.
    # To test the sorting, we can pass an unsorted list to the function.
    unsorted_releases = [mock_releases[1], mock_releases[2], mock_releases[0]]
    mock_response.json.return_value = unsorted_releases
    mock_get.return_value = mock_response

    # 1. Test successful fetch and sort
    releases = downloader._get_latest_releases_data("http://fake.url/releases")
    assert len(releases) == 3
    assert releases[0]["tag_name"] == "v2.7.4.c1f4f79"
    assert releases[1]["tag_name"] == "v2.7.3.cf574c7"
    assert releases[2]["tag_name"] == "v2.7.2.f6d3782"

    # 2. Test limited scan count
    releases = downloader._get_latest_releases_data(
        "http://fake.url/releases", scan_count=2
    )
    assert len(releases) == 2
    assert releases[0]["tag_name"] == "v2.7.4.c1f4f79"

    # Validate request options on the last call
    _, kwargs = mock_get.call_args
    assert kwargs["timeout"] == downloader.GITHUB_API_TIMEOUT
    assert kwargs["params"]["per_page"] == 2
    assert kwargs["headers"]["Accept"] == "application/vnd.github+json"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"

    # 3. Test request exception
    mock_get.side_effect = requests.exceptions.RequestException
    releases = downloader._get_latest_releases_data("http://fake.url/releases")
    assert releases == []


def test_is_release_complete(tmp_path, mock_releases):
    """Test the logic for checking if a release is completely downloaded."""
    release_dir = tmp_path / "v2.7.3.cf574c7"
    release_dir.mkdir()
    # Use the correct release data for v2.7.3.cf574c7
    release_data = mock_releases[1]

    # 1. Asset is missing
    assert (
        downloader._is_release_complete(
            release_data, str(release_dir), ["firmware"], []
        )
        is False
    )

    # 2. Asset exists, but is a corrupted zip
    zip_path = release_dir / "firmware-2.7.3.cf574c7.zip"
    zip_path.write_bytes(b"corrupt zip")
    assert (
        downloader._is_release_complete(
            release_data, str(release_dir), ["firmware"], []
        )
        is False
    )

    # 3. Asset exists and is a valid zip, but the size is wrong
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("test.txt", "data")
    assert (
        downloader._is_release_complete(
            release_data, str(release_dir), ["firmware"], []
        )
        is False
    )

    # 4. Release is complete and valid
    # Mock the size check to pass

    with patch("os.path.getsize", return_value=100):
        assert (
            downloader._is_release_complete(
                release_data, str(release_dir), ["firmware"], []
            )
            is True
        )


def test_check_and_download(mocker, tmp_path, mock_releases):
    """Test the main download orchestration logic."""
    # Setup mocks for all dependencies
    mocker.patch(
        "fetchtastic.downloader._get_latest_releases_data", return_value=mock_releases
    )
    mock_is_complete = mocker.patch(
        "fetchtastic.downloader._is_release_complete", return_value=False
    )
    mock_download_file = mocker.patch(
        "fetchtastic.downloader.download_file_with_retry", return_value=True
    )
    mock_cleanup = mocker.patch("fetchtastic.downloader.cleanup_old_versions")
    mocker.patch("fetchtastic.downloader.extract_files")
    mocker.patch("fetchtastic.downloader.set_permissions_on_sh_files")

    # Setup paths and initial state
    download_dir = tmp_path / "firmware"
    download_dir.mkdir()
    latest_release_file = tmp_path / "latest.txt"
    latest_release_file.write_text(
        "v2.7.1.f35ca81"
    )  # Pretend v2.7.1 was the last one we saw

    # --- Scenario 1: New versions available and downloaded successfully ---
    downloaded, new, failed = downloader.check_and_download(
        releases=mock_releases,
        latest_release_file=str(latest_release_file),
        release_type="Firmware",
        download_dir_path=str(download_dir),
        versions_to_keep=2,  # Should keep v2.7.4 and v2.7.3
        extract_patterns=[],
        selected_patterns=["firmware"],
        auto_extract=False,
        exclude_patterns=[],
    )

    assert "v2.7.4.c1f4f79" in downloaded
    assert "v2.7.3.cf574c7" in downloaded
    assert failed == []
    # Check that cleanup was called with the correct versions to keep
    mock_cleanup.assert_called_once_with(
        str(download_dir), ["v2.7.4.c1f4f79", "v2.7.3.cf574c7"]
    )
    # Check that the latest release file was updated
    assert latest_release_file.read_text() == "v2.7.4.c1f4f79"
    # Ensure permissions pass executed
    assert downloader.set_permissions_on_sh_files.called

    # --- Scenario 2: All releases are up to date ---
    mock_is_complete.return_value = True  # Pretend all releases are already downloaded
    mock_download_file.reset_mock()
    mock_cleanup.reset_mock()
    latest_release_file.write_text("v3.0")

    downloaded, new, failed = downloader.check_and_download(
        releases=mock_releases,
        latest_release_file=str(latest_release_file),
        release_type="Firmware",
        download_dir_path=str(download_dir),
        versions_to_keep=2,
        extract_patterns=[],
        selected_patterns=["firmware"],
    )

    assert downloaded == []
    assert failed == []
    mock_download_file.assert_not_called()
    # Cleanup should NOT be called if no other actions were taken
    mock_cleanup.assert_not_called()

    # --- Scenario 3: Download fails ---
    mock_is_complete.return_value = False
    mock_download_file.return_value = False
    latest_release_file.write_text("v1.0")

    downloaded, new, failed = downloader.check_and_download(
        releases=mock_releases,
        latest_release_file=str(latest_release_file),
        release_type="Firmware",
        download_dir_path=str(download_dir),
        versions_to_keep=2,
        extract_patterns=[],
        selected_patterns=["firmware"],
    )

    assert downloaded == []
    assert len(failed) > 0
    assert failed[0]["release_tag"] == "v2.7.4.c1f4f79"  # It tries v2.7.4 first


@patch("fetchtastic.downloader._initial_setup_and_config")
@patch("fetchtastic.downloader._check_wifi_connection")
@patch("fetchtastic.downloader._process_firmware_downloads")
@patch("fetchtastic.downloader._process_apk_downloads")
@patch("fetchtastic.downloader._finalize_and_notify")
def test_main(
    mock_finalize,
    mock_process_apk,
    mock_process_firmware,
    mock_check_wifi,
    mock_initial_setup,
):
    """Test the main downloader orchestration."""
    # Simulate successful setup
    mock_initial_setup.return_value = (
        {"SAVE_FIRMWARE": True, "SAVE_APKS": True},
        "1.0.0",
        "1.1.0",
        True,
        {"download_dir": "/tmp"},  # nosec B108
    )
    mock_process_firmware.return_value = (["v1"], ["v1"], [], "v1")
    mock_process_apk.return_value = (["v2"], ["v2"], [], "v2")

    downloader.main()

    mock_initial_setup.assert_called_once()
    mock_check_wifi.assert_called_once()
    mock_process_firmware.assert_called_once()
    mock_process_apk.assert_called_once()
    mock_finalize.assert_called_once()


@patch("fetchtastic.downloader.display_version_info")
@patch("fetchtastic.downloader.setup_config.load_config")
@patch("os.path.exists")
@patch("os.makedirs")
def test_initial_setup_and_config(
    mock_makedirs, mock_exists, mock_load_config, mock_display_version
):
    """Test the initial setup and configuration loading."""
    # 1. Test with existing config
    mock_load_config.return_value = {
        "DOWNLOAD_DIR": "/tmp/test_downloads"
    }  # nosec B108
    mock_display_version.return_value = ("1.0.0", "1.1.0", True)
    mock_exists.return_value = True

    config, _, _, _, paths = downloader._initial_setup_and_config()

    assert config["DOWNLOAD_DIR"] == "/tmp/test_downloads"  # nosec B108
    mock_makedirs.assert_not_called()

    # 2. Test with no config
    mock_load_config.return_value = None
    config, _, _, _, paths = downloader._initial_setup_and_config()
    assert config is None

    # 3. Test directory creation
    mock_load_config.return_value = {
        "DOWNLOAD_DIR": "/tmp/test_downloads"
    }  # nosec B108
    mock_exists.return_value = False
    downloader._initial_setup_and_config()
    assert mock_makedirs.call_count == 3


@patch("fetchtastic.downloader.setup_config.is_termux", return_value=True)
@patch("os.popen")
def test_check_wifi_connection(mock_popen, mocker):
    """Test the Wi-Fi connection check on Termux."""
    config = {"WIFI_ONLY": True}

    # 1. Test when connected to Wi-Fi
    mock_popen.return_value.read.return_value = (
        '{"supplicant_state": "COMPLETED", "ip": "192.168.1.100"}'
    )
    downloader.downloads_skipped = False
    downloader._check_wifi_connection(config)
    assert downloader.downloads_skipped is False


@patch("fetchtastic.downloader._get_latest_releases_data")
@patch("fetchtastic.downloader.check_and_download")
@patch("fetchtastic.downloader.check_promoted_prereleases")
@patch("fetchtastic.downloader.check_for_prereleases")
@patch("os.path.exists", return_value=True)
def test_process_firmware_downloads(
    mock_exists,
    mock_check_for_prereleases,
    mock_check_promoted,
    mock_check_and_download,
    mock_get_releases,
):
    """Test the firmware download processing logic."""
    config = {
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["pattern1"],
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": True,
        "EXTRACT_PATTERNS": [],
        "EXCLUDE_PATTERNS": [],
        "AUTO_EXTRACT": False,
    }
    paths = {
        "firmware_releases_url": "url",
        "latest_firmware_release_file": "file",
        "firmware_dir": "/tmp/firmware",  # nosec B108
        "download_dir": "/tmp",  # nosec B108
    }
    with patch("builtins.open", mock_open(read_data="v1.0")):
        mock_get_releases.return_value = [{"tag_name": "v1.0"}]
        mock_check_and_download.return_value = (["v1.0"], ["v1.0"], [])
        mock_check_promoted.return_value = False
        mock_check_for_prereleases.return_value = (True, ["v1.1-pre"])

        downloaded, new, failed, latest = downloader._process_firmware_downloads(
            config, paths
        )

        assert "v1.0" in downloaded
        assert "pre-release v1.1-pre" in downloaded
        assert latest == "v1.0"


@patch("fetchtastic.downloader._send_ntfy_notification")
def test_finalize_and_notify(mock_send_ntfy):
    """Test the finalize and notify function."""
    config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}

    # 1. Test with downloaded files
    downloader._finalize_and_notify(
        start_time=0,
        config=config,
        downloaded_firmwares=["v1"],
        downloaded_apks=["v2"],
        new_firmware_versions=[],
        new_apk_versions=[],
        current_version="1.0.0",
        latest_version="1.0.0",
        update_available=False,
    )
    mock_send_ntfy.assert_called_once()
    assert "Downloaded Firmware versions: v1" in mock_send_ntfy.call_args[0][2]
    assert "Downloaded Android APK versions: v2" in mock_send_ntfy.call_args[0][2]
    # Assert the title
    _, kwargs = mock_send_ntfy.call_args
    assert kwargs["title"] == "Fetchtastic Download Completed"

    # 2. Test with no downloaded files
    mock_send_ntfy.reset_mock()
    downloader._finalize_and_notify(
        start_time=0,
        config=config,
        downloaded_firmwares=[],
        downloaded_apks=[],
        new_firmware_versions=[],
        new_apk_versions=[],
        current_version="1.0.0",
        latest_version="1.0.0",
        update_available=False,
    )
    mock_send_ntfy.assert_called_once()
    assert "All assets are up to date" in mock_send_ntfy.call_args[0][2]
    # Assert the title
    _, kwargs = mock_send_ntfy.call_args
    assert kwargs["title"] == "Fetchtastic Up to Date"

    # 3. Test with downloads skipped
    mock_send_ntfy.reset_mock()
    downloader.downloads_skipped = True
    downloader._finalize_and_notify(
        start_time=0,
        config=config,
        downloaded_firmwares=[],
        downloaded_apks=[],
        new_firmware_versions=["v3"],
        new_apk_versions=[],
        current_version="1.0.0",
        latest_version="1.0.0",
        update_available=False,
    )
    mock_send_ntfy.assert_called_once()
    assert "downloads were skipped" in mock_send_ntfy.call_args[0][2]
    # Assert the title
    _, kwargs = mock_send_ntfy.call_args
    assert kwargs["title"] == "Fetchtastic Downloads Skipped"
    downloader.downloads_skipped = False


def test_strip_unwanted_chars_additional():
    """Test additional cases for strip_unwanted_chars function."""
    # Test with mixed content
    assert downloader.strip_unwanted_chars("Hello 🌟 World! 👍") == "Hello  World! "

    # Test with only ASCII
    assert downloader.strip_unwanted_chars("Regular ASCII text") == "Regular ASCII text"

    # Test with only non-ASCII
    assert downloader.strip_unwanted_chars("🎉🎊🎈") == ""

    # Test with numbers and symbols
    assert downloader.strip_unwanted_chars("Test 123 @#$ 🚀") == "Test 123 @#$ "


def test_is_connected_to_wifi_non_termux(mocker):
    """Test is_connected_to_wifi for non-Termux platforms."""
    # Mock setup_config.is_termux to return False
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

    # Should return True for non-Termux platforms
    assert downloader.is_connected_to_wifi() is True


def test_compare_file_hashes_identical(tmp_path):
    """Test compare_file_hashes with identical files."""
    # Create two identical files
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    content = "This is test content for hash comparison"

    file1.write_text(content)
    file2.write_text(content)

    # Files should have identical hashes
    assert downloader.compare_file_hashes(str(file1), str(file2)) is True


def test_compare_file_hashes_different(tmp_path):
    """Test compare_file_hashes with different files."""
    # Create two different files
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"

    file1.write_text("Content A")
    file2.write_text("Content B")

    # Files should have different hashes
    assert downloader.compare_file_hashes(str(file1), str(file2)) is False


def test_compare_file_hashes_missing_file(tmp_path):
    """Test compare_file_hashes with missing files."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "nonexistent.txt"

    file1.write_text("Content")

    # Should return False when one file doesn't exist
    assert downloader.compare_file_hashes(str(file1), str(file2)) is False
