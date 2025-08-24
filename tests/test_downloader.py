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

    # 2. Extract one file, should still be needed
    (extract_dir / "firmware-rak4631-2.7.4.c1f4f79.bin").write_text("rak_data")
    assert (
        downloader.check_extraction_needed(
            str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
        )
        is True
    )

    # 3. All files extracted, should not be needed
    (extract_dir / "firmware-tbeam-2.7.4.c1f4f79.uf2").write_text("tbeam_data")
    (extract_dir / "firmware-rak11200-2.7.4.c1f4f79.bin").write_text("rak11200_data")
    (extract_dir / "littlefs-rak11200-2.7.4.c1f4f79.bin").write_text("littlefs_data")
    assert (
        downloader.check_extraction_needed(
            str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
        )
        is False
    )


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
