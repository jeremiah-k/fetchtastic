import hashlib
import importlib.metadata
import json
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from fetchtastic import utils
from fetchtastic.downloader import _format_api_summary


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
    if actual_hash is not None:
        utils.save_file_hash(str(file_path), actual_hash)
    else:
        pytest.fail("calculate_sha256 returned None for valid file")
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
    if file_hash is not None:
        utils.save_file_hash(str(download_path), file_hash)
    else:
        pytest.fail("calculate_sha256 returned None for valid file")

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


def test_save_file_hash_write_error(tmp_path, mocker):
    """Test save_file_hash handles OSError during hash file write."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Test OSError during hash file write
    mock_open = mocker.mock_open()
    mock_open.side_effect = OSError("Permission denied")

    with patch("builtins.open", mock_open):
        # Should not raise exception, just log error
        utils.save_file_hash(str(file_path), "dummy_hash")


def test_save_file_hash_cleanup_error(tmp_path, mocker):
    """Test save_file_hash handles OSError during temp file cleanup."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Test OSError during temp file cleanup after a replace failure
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("fetchtastic.utils.os.replace", side_effect=OSError("Replace failed"))
    mocker.patch("fetchtastic.utils.os.path.exists", return_value=True)
    mocker.patch("fetchtastic.utils.os.remove", side_effect=OSError("Remove failed"))

    # Should handle cleanup error gracefully
    utils.save_file_hash(str(file_path), "dummy_hash")


def test_remove_file_and_hash_success(tmp_path):
    """Test successful file and hash removal."""
    file_path = tmp_path / "test_file.txt"
    hash_path = tmp_path / "test_file.txt.sha256"

    file_path.write_text("test content")
    hash_path.write_text("dummy_hash")

    result = utils._remove_file_and_hash(str(file_path))

    assert result is True
    assert not file_path.exists()
    assert not hash_path.exists()


def test_remove_file_and_hash_no_hash_file(tmp_path):
    """Test file removal when hash file doesn't exist."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    result = utils._remove_file_and_hash(str(file_path))

    assert result is True
    assert not file_path.exists()


def test_remove_file_and_hash_error_handling(tmp_path, mocker):
    """Test error handling in file removal."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Test OSError during file removal
    mocker.patch(
        "fetchtastic.utils.os.remove", side_effect=OSError("Permission denied")
    )
    result = utils._remove_file_and_hash(str(file_path))
    assert result is False


def test_load_file_hash_file_not_found(tmp_path):
    """Test load_file_hash when hash file doesn't exist."""
    file_path = tmp_path / "nonexistent.txt"
    result = utils.load_file_hash(str(file_path))
    assert result is None


def test_load_file_hash_error_handling(tmp_path, mocker):
    """Test load_file_hash error handling."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Test OSError during hash file read
    mocker.patch("builtins.open", side_effect=OSError("Permission denied"))
    result = utils.load_file_hash(str(file_path))
    assert result is None


def test_calculate_sha256_file_not_found():
    """Test calculate_sha256 with non-existent file."""
    result = utils.calculate_sha256("/nonexistent/file.txt")
    assert result is None


def test_calculate_sha256_error_handling(tmp_path, mocker):
    """Test calculate_sha256 error handling."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("test content")

    # Test OSError during file read
    mocker.patch("builtins.open", side_effect=OSError("Permission denied"))
    result = utils.calculate_sha256(str(file_path))
    assert result is None


def test_download_file_with_retry_network_error_handling(tmp_path, mocker):
    """Test download_file_with_retry network error handling."""
    download_path = tmp_path / "test_file.zip"

    # Test requests.RequestException
    mock_session_class = mocker.patch("fetchtastic.utils.requests.Session")
    mock_session = MagicMock()
    mock_session_class.return_value = mock_session
    mock_session.get.side_effect = requests.RequestException("Network error")

    result = utils.download_file_with_retry(
        "http://example.com/file.zip", str(download_path)
    )
    assert result is False


def test_get_user_agent_with_version(mocker):
    """Test get_user_agent with version."""
    # Clear cache first
    utils._USER_AGENT_CACHE = None
    mocker.patch("fetchtastic.utils.importlib.metadata.version", return_value="1.2.3")
    user_agent = utils.get_user_agent()
    assert user_agent == "fetchtastic/1.2.3"


def test_get_user_agent_without_version(mocker):
    """Test get_user_agent when version is not available."""
    # Clear cache first
    utils._USER_AGENT_CACHE = None
    mocker.patch(
        "fetchtastic.utils.importlib.metadata.version",
        side_effect=importlib.metadata.PackageNotFoundError("Package not found"),
    )
    user_agent = utils.get_user_agent()
    assert user_agent == "fetchtastic/unknown"


def test_get_user_agent_caching(mocker):
    """Test that get_user_agent caches the result."""
    # Clear cache first
    utils._USER_AGENT_CACHE = None

    mock_version = mocker.patch(
        "fetchtastic.utils.importlib.metadata.version", return_value="1.2.3"
    )
    # First call should hit the metadata
    user_agent1 = utils.get_user_agent()
    assert user_agent1 == "fetchtastic/1.2.3"
    assert mock_version.call_count == 1

    # Second call should use cache
    user_agent2 = utils.get_user_agent()
    assert user_agent2 == "fetchtastic/1.2.3"
    assert mock_version.call_count == 1  # Should not be called again


@pytest.mark.core_downloads
@pytest.mark.unit
def test_rate_limit_cache_file_operations():
    """Test rate limit cache file operations."""
    import tempfile
    from pathlib import Path

    # Clear cache before test
    utils.clear_rate_limit_cache()

    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock platformdirs to use our temp directory
        with patch("fetchtastic.utils.platformdirs.user_cache_dir") as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset global variables to force re-calculation
            import fetchtastic.utils as utils_module

            utils_module._rate_limit_cache_file = None
            utils_module._rate_limit_cache_loaded = False

            cache_file = utils._get_rate_limit_cache_file()
            expected_path = Path(temp_dir) / "rate_limits.json"
            assert cache_file == str(expected_path)

            # Test cache file doesn't exist initially
            assert not os.path.exists(cache_file)

            # Test _update_rate_limit creates file
            utils._update_rate_limit("test_token_hash", 100)
            assert os.path.exists(cache_file)

            # Verify file contents
            with open(cache_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            assert "test_token_hash" in saved_data
            assert saved_data["test_token_hash"][0] == 100  # remaining count

            # Clear in-memory cache
            utils._rate_limit_cache.clear()
            assert len(utils._rate_limit_cache) == 0

            # Test _load_rate_limit_cache restores data
            utils._load_rate_limit_cache()
            assert len(utils._rate_limit_cache) == 1
            assert "test_token_hash" in utils._rate_limit_cache


@pytest.mark.core_downloads
@pytest.mark.unit
def test_rate_limit_cache_expiry():
    """Test that expired rate limit entries are not loaded."""
    import tempfile
    from datetime import datetime, timedelta, timezone

    # Clear cache before test
    utils.clear_rate_limit_cache()

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("fetchtastic.utils.platformdirs.user_cache_dir") as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset global variables to force re-calculation
            import fetchtastic.utils as utils_module

            utils_module._rate_limit_cache_file = None
            utils_module._rate_limit_cache_loaded = False

            cache_file = utils._get_rate_limit_cache_file()

            # Create cache data with expired entry (reset time in past)
            expired_reset = datetime.now(timezone.utc) - timedelta(hours=2)  # Expired
            valid_reset = datetime.now(timezone.utc) + timedelta(hours=1)  # Valid

            cache_data = {
                "expired_token": [50, expired_reset.isoformat()],
                "valid_token": [75, valid_reset.isoformat()],
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)

            # Load cache - should only load valid entries
            utils._load_rate_limit_cache()

            # Should only have valid entry
            assert len(utils._rate_limit_cache) == 1
            assert "valid_token" in utils._rate_limit_cache
            assert "expired_token" not in utils._rate_limit_cache


@pytest.mark.core_downloads
@pytest.mark.unit
def test_rate_limit_cache_error_handling():
    """Test error handling for corrupted rate limit cache files."""
    import tempfile

    # Clear cache before test
    utils.clear_rate_limit_cache()

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("fetchtastic.utils.platformdirs.user_cache_dir") as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Reset global variable to force re-calculation
            import fetchtastic.utils as utils_module

            utils_module._rate_limit_cache_file = None

            cache_file = utils._get_rate_limit_cache_file()

            # Test with invalid JSON
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write("invalid json content")

            # Should not raise exception
            utils._load_rate_limit_cache()
            assert len(utils._rate_limit_cache) == 0

            # Test with invalid structure (not a dict)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(["not", "a", "dict"], f)

            utils._load_rate_limit_cache()
            assert len(utils._rate_limit_cache) == 0

            # Test with invalid data format
            invalid_data = {"invalid_token": ["not-a-number", "2025-01-20T12:00:00Z"]}
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(invalid_data, f)

            utils._load_rate_limit_cache()
            assert len(utils._rate_limit_cache) == 0


@pytest.mark.core_downloads
@pytest.mark.unit
def test_get_cached_rate_limit():
    """Test _get_cached_rate_limit with different scenarios."""
    from datetime import datetime, timedelta, timezone

    # Clear cache
    utils.clear_rate_limit_cache()

    # Test with empty cache
    result = utils._get_cached_rate_limit("nonexistent_token")
    assert result is None

    # Test with valid cache entry (reset in future)
    future_reset = datetime.now(timezone.utc) + timedelta(hours=1)
    utils._rate_limit_cache["valid_token"] = (42, future_reset)

    result = utils._get_cached_rate_limit("valid_token")
    assert result == 42

    # Test with expired cache entry (reset in past)
    past_reset = datetime.now(timezone.utc) - timedelta(hours=1)
    utils._rate_limit_cache["expired_token"] = (25, past_reset)

    result = utils._get_cached_rate_limit("expired_token")
    assert result is None


@pytest.mark.core_downloads
@pytest.mark.unit
def test_clear_rate_limit_cache():
    """Test clear_rate_limit_cache functionality."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("fetchtastic.utils.platformdirs.user_cache_dir") as mock_cache_dir:
            mock_cache_dir.return_value = temp_dir

            # Clear cache first
            utils.clear_rate_limit_cache()

            # Reset global variable to force re-calculation
            import fetchtastic.utils as utils_module

            utils_module._rate_limit_cache_file = None

            cache_file = utils._get_rate_limit_cache_file()

            # Create cache file and in-memory data
            utils._update_rate_limit("test_token", 100)
            assert os.path.exists(cache_file)
            assert len(utils._rate_limit_cache) == 1

            # Clear cache
            utils.clear_rate_limit_cache()

            # Should clear both in-memory and persistent cache
            assert len(utils._rate_limit_cache) == 0
            assert not os.path.exists(cache_file)


@pytest.mark.core_downloads
@pytest.mark.unit
def test_make_github_api_request_rate_limit_tracking():
    """Test that make_github_api_request tracks rate limits properly."""
    from datetime import datetime, timezone

    # Clear cache before test
    utils.clear_rate_limit_cache()

    # Mock response with rate limit headers
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {
        "X-RateLimit-Remaining": "4999",
        "X-RateLimit-Reset": str(int((datetime.now(timezone.utc).timestamp() + 3600))),
    }
    mock_response.raise_for_status.return_value = None

    # Mock requests.get directly instead of Session
    with patch("fetchtastic.utils.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Make API request - use a valid URL pattern that won't trigger 404
        with patch("fetchtastic.utils.logger"):  # Suppress logging during test
            result = utils.make_github_api_request(
                "https://api.github.com/repos/test/repo"
            )

        assert result == mock_response

        # Check that rate limit was cached
        # Note: We can't easily check the exact token hash without importing internal functions
        # But we can verify that the cache was populated
        assert len(utils._rate_limit_cache) > 0


@pytest.mark.core_downloads
@pytest.mark.unit
def test_make_github_api_request_rate_limit_warnings():
    """Test rate limit warning functionality."""
    # Mock response with low rate limit
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-RateLimit-Remaining": "5"}  # Low rate limit
    mock_response.raise_for_status.return_value = None

    # Mock requests.get directly instead of Session
    with patch("fetchtastic.utils.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Make API request - should generate warning
        with patch("fetchtastic.log_utils.logger") as mock_logger:
            utils.make_github_api_request("https://api.github.com/repos/test/repo")

            # Should have logged a warning about low rate limit
            mock_logger.warning.assert_called()
            warning_call = mock_logger.warning.call_args[0][0]
            assert "rate limit running low" in warning_call.lower()
            assert "5" in warning_call


@pytest.mark.core_downloads
@pytest.mark.unit
def test_make_github_api_request_debug_logging():
    """Test that API requests are logged at debug level."""
    # Mock response without rate limit headers to avoid parsing issues
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}  # No rate limit headers
    mock_response.raise_for_status.return_value = None

    with patch("fetchtastic.utils.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Make API request - should generate debug log
        with patch("fetchtastic.log_utils.logger") as mock_logger:
            utils.make_github_api_request("https://api.github.com/repos/test/repo")

            # Should have logged debug message with URL
            debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
            api_request_call = next(
                (
                    call
                    for call in debug_calls
                    if "making github api request" in call.lower()
                ),
                None,
            )
            assert (
                api_request_call is not None
            ), "No debug call with 'making github api request' found"
            assert "https://api.github.com/repos/test/repo" in api_request_call


@pytest.mark.core_downloads
@pytest.mark.unit
def test_make_github_api_request_rate_limit_20_warning():
    """Test that no rate limit warning is generated at exactly 20 requests remaining."""
    # Mock response with exactly 20 rate limit as integer
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-RateLimit-Remaining": 20}  # Integer instead of string
    mock_response.raise_for_status.return_value = None

    with patch("fetchtastic.utils.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Make API request - should NOT generate warning at 20 (only warns at <= 10)
        with patch("fetchtastic.log_utils.logger") as mock_logger:
            utils.make_github_api_request("https://api.github.com/repos/test/repo")

            # Should NOT have logged a warning about rate limit at 20
            mock_logger.warning.assert_not_called()


@pytest.mark.core_downloads
@pytest.mark.unit
def test_make_github_api_request_cached_rate_limit():
    """Test that cached rate limits are used when headers are missing."""
    from datetime import datetime, timedelta, timezone

    # Clear cache and pre-populate with cached data
    utils.clear_rate_limit_cache()

    future_reset = datetime.now(timezone.utc) + timedelta(hours=1)
    # Calculate the actual token hash that will be generated
    import hashlib

    fake_token = "ghp_" + "x" * 36
    token_hash = hashlib.sha256(fake_token.encode()).hexdigest()[:16]
    utils._rate_limit_cache[token_hash] = (250, future_reset)

    # Mock response without rate limit headers
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}  # No rate limit headers
    mock_response.raise_for_status.return_value = None

    # Mock requests.get directly instead of Session
    with patch("fetchtastic.utils.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Make API request with known token
        with patch("fetchtastic.log_utils.logger") as mock_logger:
            utils.make_github_api_request(
                "https://api.github.com/repos/test/repo",
                github_token="ghp_" + "x" * 36,  # noqa: S105 - fake GitHub token format
            )

            # Should log cached rate limit estimate
            mock_logger.debug.assert_called()
            debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
            cached_log = any("cached estimate" in call.lower() for call in debug_calls)
            assert cached_log


@pytest.mark.core_downloads
@pytest.mark.unit
def test_cache_thread_safety():
    """Test that cache operations are thread-safe."""
    import threading
    import time
    from datetime import datetime, timezone

    # Clear caches before test
    utils.clear_rate_limit_cache()
    from fetchtastic.downloader import clear_commit_timestamp_cache

    clear_commit_timestamp_cache()

    # Test rate limit cache thread safety
    def update_rate_limit_worker(token_hash, value):
        for _ in range(10):
            utils._update_rate_limit(token_hash, value)
            time.sleep(0.001)  # Small delay to increase chance of race conditions

    def read_rate_limit_worker(token_hash):
        results = []
        for _ in range(10):
            result = utils._get_cached_rate_limit(token_hash)
            results.append(result)
            time.sleep(0.001)
        return results

    # Start multiple threads updating and reading rate limit cache
    threads = []
    for i in range(5):
        t1 = threading.Thread(
            target=update_rate_limit_worker, args=(f"token_{i}", 100 + i)
        )
        t2 = threading.Thread(target=read_rate_limit_worker, args=(f"token_{i}",))
        threads.extend([t1, t2])

    # Start all threads
    for thread in threads:
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Verify cache is in a consistent state
    assert len(utils._rate_limit_cache) == 5  # Should have 5 unique tokens
    for i in range(5):
        token_hash = f"token_{i}"
        assert token_hash in utils._rate_limit_cache
        remaining, cached_at = utils._rate_limit_cache[token_hash]
        assert remaining == 100 + i
        assert isinstance(cached_at, datetime)

    # Test commit timestamp cache thread safety
    from fetchtastic.downloader import _cache_lock, _commit_timestamp_cache

    def commit_cache_worker(key_suffix):
        for i in range(5):
            key = f"owner/repo/commit_{key_suffix}_{i}"
            timestamp = datetime.now(timezone.utc)
            with _cache_lock:
                _commit_timestamp_cache[key] = (timestamp, datetime.now(timezone.utc))
            time.sleep(0.001)

    # Start multiple threads updating commit cache
    commit_threads = []
    for i in range(3):
        t = threading.Thread(target=commit_cache_worker, args=(i,))
        commit_threads.append(t)

    for thread in commit_threads:
        thread.start()

    for thread in commit_threads:
        thread.join()

    # Verify all entries were added without corruption
    expected_entries = 15  # 3 threads * 5 entries each
    assert len(_commit_timestamp_cache) == expected_entries

    # Verify cache structure is valid
    for key, (timestamp, cached_at) in _commit_timestamp_cache.items():
        assert isinstance(timestamp, datetime)
        assert isinstance(cached_at, datetime)
        assert "/" in key  # Valid cache key format


@pytest.mark.core_downloads
@pytest.mark.unit
def test_api_tracking_functions():
    """Test API tracking functions."""
    # Reset tracking first
    utils.reset_api_tracking()

    # Test initial state
    summary = utils.get_api_request_summary()
    assert summary["total_requests"] == 0
    assert summary["cache_hits"] == 0
    assert summary["cache_misses"] == 0

    # Test cache hit tracking
    utils.track_api_cache_hit()
    summary = utils.get_api_request_summary()
    assert summary["total_requests"] == 0  # total_requests incremented separately
    assert summary["cache_hits"] == 1
    assert summary["cache_misses"] == 0

    # Test cache miss tracking
    utils.track_api_cache_miss()
    summary = utils.get_api_request_summary()
    assert summary["total_requests"] == 0
    assert summary["cache_hits"] == 1
    assert summary["cache_misses"] == 1

    # Test reset
    utils.reset_api_tracking()
    summary = utils.get_api_request_summary()
    assert summary["total_requests"] == 0
    assert summary["cache_hits"] == 0
    assert summary["cache_misses"] == 0


@pytest.mark.core_downloads
@pytest.mark.unit
def test_format_api_summary():
    """Test _format_api_summary function."""
    from datetime import datetime, timedelta, timezone

    # Test basic authenticated request with no cache lookups
    summary = {
        "total_requests": 5,
        "auth_used": True,
        "cache_hits": 0,
        "cache_misses": 0,
    }
    result = _format_api_summary(summary)
    # Check key components rather than exact string match
    assert "📊 GitHub API Summary:" in result
    assert "5 API requests" in result
    assert "🔐 authenticated" in result

    # Test basic unauthenticated request with cache statistics
    summary = {
        "total_requests": 3,
        "auth_used": False,
        "cache_hits": 2,
        "cache_misses": 1,
    }
    result = _format_api_summary(summary)
    # Check key components rather than exact string match
    assert "📊 GitHub API Summary:" in result
    assert "3 API requests" in result
    assert "🌐 unauthenticated" in result
    assert "3 cache lookups" in result
    assert "2 hits (skipped), 1 miss (fetched)" in result
    assert "66.7% hit rate" in result

    # Test request with no cache hits (should still show cache stats)
    summary = {
        "total_requests": 4,
        "auth_used": False,
        "cache_hits": 0,
        "cache_misses": 4,
    }
    result = _format_api_summary(summary)
    # Check key components rather than exact string match
    assert "📊 GitHub API Summary:" in result
    assert "4 API requests" in result
    assert "🌐 unauthenticated" in result
    assert "4 cache lookups" in result
    assert "0 hits (skipped), 4 misses (fetched)" in result
    assert "0.0% hit rate" in result

    # Test with rate limit info (future reset time)
    future_time = datetime.now(timezone.utc).replace(
        second=0, microsecond=0
    ) + timedelta(minutes=5)
    summary = {
        "total_requests": 2,
        "auth_used": True,
        "cache_hits": 1,
        "cache_misses": 1,
        "rate_limit_remaining": 4500,
        "rate_limit_reset": future_time,
    }
    result = _format_api_summary(summary)
    # Should contain rate limit info with minutes
    assert "4500 requests remaining (resets in" in result
    assert "min)" in result
    assert "📊 GitHub API Summary: 2 API requests (🔐 authenticated)" in result
    assert (
        "2 cache lookups → 1 hit (skipped), 1 miss (fetched) [50.0% hit rate]" in result
    )

    # Test with rate limit info (past reset time)
    past_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(
        minutes=5
    )
    summary = {
        "total_requests": 1,
        "auth_used": False,
        "cache_hits": 0,
        "cache_misses": 0,
        "rate_limit_remaining": 4999,
        "rate_limit_reset": past_time,
    }
    result = _format_api_summary(summary)
    # Check key components rather than exact string match
    assert "📊 GitHub API Summary:" in result
    assert "1 API request" in result
    assert "🌐 unauthenticated" in result
    assert "4999 requests remaining" in result


@pytest.mark.core_downloads
@pytest.mark.unit
def test_parse_rate_limit_header():
    """Test _parse_rate_limit_header function."""
    # Test valid integer string
    assert utils._parse_rate_limit_header("5000") == 5000

    # Test valid integer
    assert utils._parse_rate_limit_header(5000) == 5000

    # Test invalid string
    assert utils._parse_rate_limit_header("invalid") is None

    # Test None
    assert utils._parse_rate_limit_header(None) is None

    # Test empty string
    assert utils._parse_rate_limit_header("") is None

    # Test negative number
    assert utils._parse_rate_limit_header("-1") is None

    # Test float
    assert utils._parse_rate_limit_header(5000.5) == 5000

    # Test zero
    assert utils._parse_rate_limit_header("0") == 0


@pytest.mark.core_downloads
@pytest.mark.unit
def test_get_effective_github_token():
    """Test get_effective_github_token function."""
    # Test with explicit token
    result = utils.get_effective_github_token("explicit_token")
    assert result == "explicit_token"

    # Test with None (should return None)
    with patch.dict(os.environ, {}, clear=True):
        result = utils.get_effective_github_token(None)
        assert result is None

    # Test with GITHUB_TOKEN environment variable
    with patch.dict(os.environ, {"GITHUB_TOKEN": "env_token"}):
        result = utils.get_effective_github_token(None)
        assert result == "env_token"

    # Test explicit token takes precedence over env
    with patch.dict(os.environ, {"GITHUB_TOKEN": "env_token"}):
        result = utils.get_effective_github_token("explicit_token")
        assert result == "explicit_token"


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_file_with_retry_additional_error_cases(tmp_path):
    """Test additional error cases in download_file_with_retry."""
    download_path = tmp_path / "test_file.txt"

    # Test with invalid URL
    result = utils.download_file_with_retry("not-a-url", str(download_path))
    assert result is False

    # Test with empty URL
    result = utils.download_file_with_retry("", str(download_path))
    assert result is False


@pytest.mark.core_downloads
@pytest.mark.unit
def test_verify_file_integrity_additional_cases(tmp_path):
    """Test additional cases for verify_file_integrity."""
    # Test with directory (should return False)
    dir_path = tmp_path / "test_dir"
    dir_path.mkdir()
    result = utils.verify_file_integrity(str(dir_path))
    assert result is False

    # Test with file that has no hash but exists (should create hash and return True)
    file_path = tmp_path / "new_file.txt"
    file_path.write_text("new content")
    result = utils.verify_file_integrity(str(file_path))
    assert result is True
    # Hash file should be created
    hash_path = utils.get_hash_file_path(str(file_path))
    assert os.path.exists(hash_path)


@pytest.mark.core_downloads
@pytest.mark.unit
def test_extract_base_name_additional_cases():
    """Test extract_base_name with additional edge cases."""
    # Test with multiple version patterns
    assert utils.extract_base_name("app-1.2.3-beta-rc1.apk") == "app-rc1.apk"

    # Test with no extension
    assert utils.extract_base_name("tool-1.0.0") == "tool"

    # Test with complex version
    assert (
        utils.extract_base_name("package-2.7.13.abcdef123_amd64.deb")
        == "package_amd64.deb"
    )

    # Test with no version separators
    assert utils.extract_base_name("simplefile.txt") == "simplefile.txt"


@pytest.mark.core_downloads
@pytest.mark.unit
def test_matches_selected_patterns_edge_cases():
    """Test matches_selected_patterns with edge cases."""
    # Test with empty patterns (should match all)
    assert utils.matches_selected_patterns("anyfile.bin", []) is True

    # Test with None patterns
    assert utils.matches_selected_patterns("anyfile.bin", None) is True

    # Test case sensitivity
    assert (
        utils.matches_selected_patterns("Firmware-Rak4631-1.0.0.uf2", ["rak4631-"])
        is True
    )
    assert (
        utils.matches_selected_patterns("firmware-rak4631-1.0.0.uf2", ["RAK4631-"])
        is True
    )

    # Test with special characters in patterns
    assert (
        utils.matches_selected_patterns("file-with-dashes.bin", ["file-with-dashes"])
        is True
    )


@pytest.mark.core_downloads
@pytest.mark.unit
def test_format_api_summary_debug_coverage():
    """Test _format_api_summary function to ensure debug logging path is covered."""
    from datetime import datetime, timezone

    from fetchtastic.downloader import _format_api_summary

    # Test the function directly to ensure it's covered
    summary = {
        "total_requests": 5,
        "auth_used": False,
        "cache_hits": 2,
        "cache_misses": 3,
        "rate_limit_remaining": 55,
        "rate_limit_reset": datetime.now(timezone.utc),
    }

    result = _format_api_summary(summary)

    # Verify the function returns expected format
    assert "📊 GitHub API Summary: 5 API requests (🌐 unauthenticated)" in result
    assert "5 cache lookups" in result
    assert "2 hits" in result
    assert "3 misses" in result
    assert "55 requests remaining" in result

    # Test with no requests
    summary_no_requests = {
        "total_requests": 0,
        "auth_used": True,
        "cache_hits": 0,
        "cache_misses": 0,
    }

    result_no_requests = _format_api_summary(summary_no_requests)
    assert (
        "📊 GitHub API Summary: 0 API requests (🔐 authenticated)" in result_no_requests
    )


def test_matches_selected_patterns_nrf52_zip_extraction():
    """
    Test that `matches_selected_patterns` correctly handles `rak4631-`
    patterns for files inside `nrf52` zip archives. This is a regression
    test to ensure the fix for trailing separator patterns is working correctly.
    """
    from fetchtastic.utils import matches_selected_patterns

    # This filename is from a real nrf52 zip archive
    filename = "firmware-rak4631-2.7.15.567b8ea.uf2"

    # The pattern 'rak4631-' should match the filename
    assert matches_selected_patterns(filename, ["rak4631-"]) is True

    # The pattern 'rak4631_' should NOT match the filename
    assert matches_selected_patterns(filename, ["rak4631_"]) is False
