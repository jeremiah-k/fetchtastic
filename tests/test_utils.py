import hashlib
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from fetchtastic import utils


@pytest.fixture
def temp_file(tmp_path):
    """
    Create a temporary file named "test_file.txt" under the provided pytest tmp_path and write a short byte string to it.

    The function writes the bytes b"This is a test file." to the file and returns the file path and the written content.

    Returns:
        tuple[pathlib.Path, bytes]: (file_path, content) where `file_path` is the path to the created file and `content` is the exact bytes written.
    """
    file_path = tmp_path / "test_file.txt"
    content = b"This is a test file."
    file_path.write_bytes(content)
    return file_path, content


@pytest.mark.core_downloads
@pytest.mark.unit
def test_get_hash_file_path(temp_file):
    """Test that get_hash_file_path returns the correct path."""
    file_path, _ = temp_file
    hash_path = utils.get_hash_file_path(str(file_path))
    assert hash_path == str(file_path) + ".sha256"


@pytest.mark.core_downloads
@pytest.mark.unit
def test_hash_functions(temp_file):
    """Test calculate_sha256, save_file_hash, and load_file_hash."""
    file_path, content = temp_file

    # Calculate hash
    expected_hash = hashlib.sha256(content).hexdigest()
    actual_hash = utils.calculate_sha256(str(file_path))
    assert actual_hash == expected_hash

    # Save and load hash
    utils.save_file_hash(str(file_path), actual_hash)
    loaded_hash = utils.load_file_hash(str(file_path))
    assert loaded_hash == actual_hash


@pytest.mark.core_downloads
@pytest.mark.unit
def test_verify_file_integrity(tmp_path):
    """Test verify_file_integrity function."""
    file_path = tmp_path / "test_integrity.txt"
    content = b"integrity test"
    file_path.write_bytes(content)

    # 1. New file: should return True and create a hash file
    assert utils.verify_file_integrity(str(file_path)) is True
    hash_file_path = utils.get_hash_file_path(str(file_path))
    assert os.path.exists(hash_file_path)

    # 2. File with matching hash: should return True
    assert utils.verify_file_integrity(str(file_path)) is True

    # 3. File with mismatched hash
    # Manually change the hash file
    with open(hash_file_path, "w") as f:
        f.write("mismatched_hash  test_integrity.txt\n")
    assert utils.verify_file_integrity(str(file_path)) is False

    # 4. Non-existent file: should return False
    assert utils.verify_file_integrity("non_existent_file.txt") is False


@pytest.mark.core_downloads
@pytest.mark.unit
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_success(mock_session, tmp_path):
    """Test successful download."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_content.return_value = [b"some ", b"data"]
    mock_session.return_value.get.return_value = mock_response

    download_path = tmp_path / "downloaded_file.txt"
    result = utils.download_file_with_retry(
        "http://example.com/file.txt", str(download_path)
    )

    assert result is True
    assert download_path.read_bytes() == b"some data"
    mock_session.return_value.get.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_existing_valid(mock_session, tmp_path):
    """Test download when file exists and is valid."""
    download_path = tmp_path / "existing_file.txt"
    download_path.write_bytes(b"existing data")

    # Create a valid hash file
    file_hash = utils.calculate_sha256(str(download_path))
    utils.save_file_hash(str(download_path), file_hash)

    result = utils.download_file_with_retry(
        "http://example.com/file.txt", str(download_path)
    )

    assert result is True
    mock_session.return_value.get.assert_not_called()


@pytest.mark.core_downloads
@pytest.mark.integration
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_existing_corrupted_zip(mock_session, tmp_path):
    """Test download when a zip file exists but is corrupted."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Provide a minimal valid zip file as the new content
    valid_zip_content = b"PK\x05\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    mock_response.iter_content.return_value = [valid_zip_content]
    mock_session.return_value.get.return_value = mock_response

    download_path = tmp_path / "corrupted.zip"
    # Create a corrupted zip file
    download_path.write_bytes(b"this is not a zip file")

    # Mock zipfile.ZipFile to raise BadZipFile only on the first check
    original_zipfile_init = zipfile.ZipFile.__init__

    def mock_zipfile_init(self, file, *args, **kwargs):
        """
        Mock replacement for zipfile.ZipFile.__init__ used in tests.

        Raises zipfile.BadZipFile when called with the path equal to the enclosing-scope `download_path`
        to simulate a corrupted ZIP on first validation; for any other path it delegates to
        `original_zipfile_init` from the enclosing scope.

        Depends on `original_zipfile_init` and `download_path` being defined in the surrounding scope.
        """
        if str(file) == str(download_path):
            raise zipfile.BadZipFile
        else:
            original_zipfile_init(self, file, *args, **kwargs)

    with patch(
        "zipfile.ZipFile.__init__", side_effect=mock_zipfile_init, autospec=True
    ):
        result = utils.download_file_with_retry(
            "http://example.com/file.zip", str(download_path)
        )

    assert result is True
    assert download_path.read_bytes() == valid_zip_content
    mock_session.return_value.get.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_network_error(mock_session, tmp_path):
    """Test download with a network error."""
    mock_session.return_value.get.side_effect = requests.exceptions.RequestException

    download_path = tmp_path / "downloaded_file.txt"
    result = utils.download_file_with_retry(
        "http://example.com/file.txt", str(download_path)
    )

    assert result is False
    assert not os.path.exists(download_path)


@pytest.mark.core_downloads
@pytest.mark.integration
@patch("fetchtastic.utils.platform.system", return_value="Windows")
@patch("fetchtastic.utils.os.replace")
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_windows_permission_error(
    mock_session, mock_os_replace, mock_platform, tmp_path
):
    """Test Windows-specific retry logic on PermissionError."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_content.return_value = [b"windows data"]
    mock_session.return_value.get.return_value = mock_response

    # Simulate PermissionError on the first two calls, then succeed on the third
    mock_os_replace.side_effect = [
        PermissionError,
        PermissionError,
        None,  # Successful call
    ]

    download_path = tmp_path / "windows_file.txt"

    with patch("fetchtastic.utils.time.sleep") as mock_sleep:
        result = utils.download_file_with_retry(
            "http://example.com/file.txt", str(download_path)
        )

    # Assert that the function reports success
    assert result is True

    # Assert that the retry logic was triggered
    assert mock_os_replace.call_count == 3
    assert mock_sleep.call_count == 2

    # Assert that the final successful call was made with the correct arguments
    # The implementation may add a uniqueness suffix to the temp file name; allow prefix match
    args, kwargs = mock_os_replace.call_args
    assert args[1] == str(download_path)
    assert args[0].startswith(str(download_path) + ".tmp")


# Additional comprehensive tests for better coverage


@pytest.mark.core_downloads
@pytest.mark.unit
def test_calculate_sha256_nonexistent_file():
    """Test calculate_sha256 with non-existent file."""
    result = utils.calculate_sha256("nonexistent_file.txt")
    assert result is None


@pytest.mark.core_downloads
@pytest.mark.unit
def test_load_file_hash_nonexistent_hash_file(tmp_path):
    """Test load_file_hash when hash file doesn't exist."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    result = utils.load_file_hash(str(file_path))
    assert result is None


@pytest.mark.core_downloads
@pytest.mark.unit
def test_save_file_hash_io_error(tmp_path):
    """Test save_file_hash handles IO errors gracefully."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Try to save hash to a directory that doesn't exist
    with patch("builtins.open", side_effect=IOError("Permission denied")):
        # Should not raise an exception
        utils.save_file_hash(str(file_path), "test_hash")


@pytest.mark.core_downloads
@pytest.mark.unit
def test_verify_file_integrity_io_error():
    """Test verify_file_integrity handles IO errors gracefully."""
    with patch("fetchtastic.utils.calculate_sha256", return_value=None):
        result = utils.verify_file_integrity("nonexistent_file.txt")
        assert result is False


@pytest.mark.core_downloads
@pytest.mark.integration
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_http_error(mock_session, tmp_path):
    """Test download with HTTP error response."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "404 Not Found"
    )
    mock_session.return_value.get.return_value = mock_response

    download_path = tmp_path / "not_found.txt"
    result = utils.download_file_with_retry(
        "http://example.com/notfound.txt", str(download_path)
    )

    assert result is False
    assert not download_path.exists()


@pytest.mark.core_downloads
@pytest.mark.integration
@patch("fetchtastic.utils.requests.Session")
def test_download_file_with_retry_partial_content(mock_session, tmp_path):
    """Test download with partial content and retry logic."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Simulate chunked download
    mock_response.iter_content.return_value = [b"chunk1", b"chunk2", b"chunk3"]
    mock_session.return_value.get.return_value = mock_response

    download_path = tmp_path / "chunked_file.txt"
    result = utils.download_file_with_retry(
        "http://example.com/chunked.txt", str(download_path)
    )

    assert result is True
    assert download_path.read_bytes() == b"chunk1chunk2chunk3"


@pytest.mark.core_downloads
@pytest.mark.unit
def test_extract_base_name():
    """Test extract_base_name function with various filename patterns."""
    test_cases = [
        ("fdroidRelease-2.5.9.apk", "fdroidRelease.apk"),
        ("firmware-rak4631-2.7.4.c1f4f79-ota.zip", "firmware-rak4631-ota.zip"),
        ("meshtasticd_2.5.13.1a06f88_amd64.deb", "meshtasticd_amd64.deb"),
        ("app_v1.2.3_release.apk", "app_release.apk"),
        ("tool-1.0.0.zip", "tool.zip"),
        ("simple.txt", "simple.txt"),  # No version to remove
        ("file-with-dashes.log", "file-with-dashes.log"),  # No version pattern
        # Test new prerelease patterns
        ("app-2.6.9-rc1.apk", "app.apk"),
        ("firmware-2.6.9.dev1-test.zip", "firmware-test.zip"),
        ("tool_2.5.13-beta2_linux.tar.gz", "tool_linux.tar.gz"),
        ("package-1.0.0-alpha3.deb", "package.deb"),
        ("app-2.6.9.rc1.apk", "app.apk"),  # dot-separated
        ("firmware_2.6.9.dev1_test.zip", "firmware_test.zip"),  # underscore-separated
    ]

    for input_filename, expected_output in test_cases:
        result = utils.extract_base_name(input_filename)
        assert (
            result == expected_output
        ), f"extract_base_name('{input_filename}') returned '{result}', expected '{expected_output}'"


def test_matches_selected_patterns_rak4631_variants():
    """Ensure backward-compatible matcher distinguishes dash vs underscore variants."""
    from fetchtastic.utils import matches_selected_patterns

    # Base device family (dash) should match only dash variant paths
    assert (
        matches_selected_patterns("firmware-rak4631-2.7.6.abc123.uf2", ["rak4631-"])
        is True
    )
    assert (
        matches_selected_patterns(
            "firmware-rak4631_eink-2.7.6.abc123.uf2", ["rak4631-"]
        )
        is False
    )

    # Underscore family should match only underscore variant paths
    assert (
        matches_selected_patterns(
            "firmware-rak4631_eink-2.7.6.abc123.uf2", ["rak4631_"]
        )
        is True
    )
    assert (
        matches_selected_patterns("firmware-rak4631-2.7.6.abc123.uf2", ["rak4631_"])
        is False
    )

    # No patterns provided defaults to permissive (handled upstream by checks)
    assert matches_selected_patterns("anything.bin", None) is True
    # Plain family token matches dashed and underscored variants (permissive intent)
    assert (
        matches_selected_patterns("firmware-rak4631-2.7.6.x.uf2", ["rak4631"]) is True
    )
    assert (
        matches_selected_patterns("firmware-rak4631_eink-2.7.6.x.uf2", ["rak4631"])
        is True
    )


def test_matches_selected_patterns_handles_renamed_android_assets():
    """Legacy config patterns should recognise new Android asset naming."""
    from fetchtastic.utils import matches_selected_patterns

    assert (
        matches_selected_patterns("app-fdroid-release.apk", ["fdroidRelease-"]) is True
    )
    assert (
        matches_selected_patterns("app-google-release.aab", ["googleRelease-"]) is True
    )
    # Sanitised comparison should also cope with dots, underscores, or casing
    assert (
        matches_selected_patterns("APP-GOOGLE-RELEASE.APK", ["googleRelease-"]) is True
    )


def test_legacy_strip_version_numbers():
    """Directly test legacy normalization which preserves the separator before versions."""
    from fetchtastic.utils import legacy_strip_version_numbers

    # Preserves '-' immediately before version
    assert (
        legacy_strip_version_numbers("firmware-rak4631-2.7.4.c1f4f79.zip")
        == "firmware-rak4631-.zip"
    )

    # Preserves '_' immediately before version and collapses repeated separators
    assert (
        legacy_strip_version_numbers("meshtasticd_2.5.13.1a06f88_amd64.deb")
        == "meshtasticd_amd64.deb"
    )

    # Does not alter filenames without version-like tokens
    assert legacy_strip_version_numbers("simple.txt") == "simple.txt"

    # Extra dashes are collapsed appropriately by legacy normalizer
    assert (
        legacy_strip_version_numbers("firmware--rak4631---2.7.4.c1f4f79.zip")
        == "firmware-rak4631-.zip"
    )


@pytest.mark.core_downloads
@pytest.mark.unit
@patch("fetchtastic.utils.requests.Session")
@patch("fetchtastic.utils.Retry")
def test_urllib3_v1_fallback_retry_creation(mock_retry, mock_session, tmp_path):
    """Test urllib3 v1 fallback when v2 parameters cause TypeError."""
    # Mock Retry to raise TypeError on first call (v2 params), succeed on second (v1 params)
    mock_retry.side_effect = [TypeError("unsupported parameter"), MagicMock()]

    # Just test the retry creation part by calling the function that creates the retry strategy
    # This will exercise the try/except block we added for urllib3 compatibility
    # Avoid real I/O
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = []
    mock_session.return_value.get.return_value = mock_resp
    utils.download_file_with_retry(
        "http://test.com/file.bin", str(tmp_path / "file.bin")
    )

    # Verify urllib3 v1 fallback was attempted
    assert mock_retry.call_count == 2
    # First call should have v2+ parameters
    first_call_kwargs = mock_retry.call_args_list[0][1]
    assert "respect_retry_after_header" in first_call_kwargs
    assert "allowed_methods" in first_call_kwargs

    # Second call should have v1 parameters
    second_call_kwargs = mock_retry.call_args_list[1][1]
    assert "respect_retry_after_header" not in second_call_kwargs
    assert "method_whitelist" in second_call_kwargs
    assert "allowed_methods" not in second_call_kwargs


def test_matches_selected_patterns_keyword_heuristic():
    """Test that keyword-based heuristic enables sanitized matching for known problematic patterns."""
    from fetchtastic.utils import matches_selected_patterns

    # Test that lowercase patterns with known keywords use sanitized matching
    assert (
        matches_selected_patterns("app-fdroid-release.apk", ["fdroid-release"]) is True
    )
    assert matches_selected_patterns("my-app-release.apk", ["my-app-release"]) is True
    assert matches_selected_patterns("some-app.aab", ["some-app-aab"]) is True

    # Test that patterns without keywords still preserve dash/underscore distinction
    assert (
        matches_selected_patterns("firmware-rak4631_eink-2.7.6.uf2", ["rak4631-"])
        is False
    )
    assert matches_selected_patterns("firmware-rak4631-2.7.6.uf2", ["rak4631-"]) is True
