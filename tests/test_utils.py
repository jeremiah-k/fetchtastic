import hashlib
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from fetchtastic import utils


@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file with some content."""
    file_path = tmp_path / "test_file.txt"
    content = b"This is a test file."
    file_path.write_bytes(content)
    return file_path, content


def test_get_hash_file_path(temp_file):
    """Test that get_hash_file_path returns the correct path."""
    file_path, _ = temp_file
    hash_path = utils.get_hash_file_path(str(file_path))
    assert hash_path == str(file_path) + ".sha256"


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
    temp_path = str(download_path) + ".tmp"

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
    mock_os_replace.assert_called_with(temp_path, str(download_path))
