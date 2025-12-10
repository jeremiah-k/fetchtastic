"""
Tests for the new RepositoryDownloader class.

This module tests the new modular RepositoryDownloader implementation
that replaces the old monolithic repo_downloader.py functionality.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.fetchtastic.download.interfaces import DownloadResult
from src.fetchtastic.download.repository import RepositoryDownloader


@pytest.fixture
def mock_config():
    """Provides a mock configuration for the repository downloader."""
    return {
        "DOWNLOAD_DIR": "/tmp/test_downloads",
        "VERSIONS_TO_KEEP": 5,
        "REPO_DOWNLOADS_DIR": "repo-dls",
        "SHELL_SCRIPT_EXTENSION": ".sh",
    }


@pytest.fixture
def repository_downloader(mock_config):
    """Provides a RepositoryDownloader instance with mock configuration."""
    return RepositoryDownloader(mock_config)


@pytest.fixture
def mock_file_info():
    """Provides mock file information for repository downloads."""
    return {
        "name": "test-firmware.bin",
        "download_url": "https://meshtastic.github.io/firmware/test-firmware.bin",
        "size": 1024,
    }


@pytest.fixture
def mock_script_file_info():
    """Provides mock file information for shell script downloads."""
    return {
        "name": "device-update.sh",
        "download_url": "https://meshtastic.github.io/scripts/device-update.sh",
        "size": 2048,
    }


def test_repository_downloader_initialization(repository_downloader, mock_config):
    """Test that RepositoryDownloader initializes correctly."""
    assert repository_downloader.config == mock_config
    assert repository_downloader.repo_url == "https://meshtastic.github.io"
    assert repository_downloader.repo_downloads_dir == "repo-dls"
    assert repository_downloader.shell_script_extension == ".sh"


def test_get_safe_target_directory_success(repository_downloader, tmp_path):
    """Test _get_safe_target_directory with valid subdirectory."""
    # Mock the download_dir to use tmp_path
    repository_downloader.download_dir = str(tmp_path)

    # Test with no subdirectory
    target_dir = repository_downloader._get_safe_target_directory("")
    expected_dir = tmp_path / "firmware" / "repo-dls"
    assert target_dir == str(expected_dir)
    assert os.path.exists(expected_dir)

    # Test with valid subdirectory
    target_dir = repository_downloader._get_safe_target_directory("test-subdir")
    expected_dir = tmp_path / "firmware" / "repo-dls" / "test-subdir"
    assert target_dir == str(expected_dir)
    assert os.path.exists(expected_dir)


def test_get_safe_target_directory_invalid(repository_downloader, tmp_path):
    """Test _get_safe_target_directory with invalid subdirectory."""
    repository_downloader.download_dir = str(tmp_path)

    # Test with path traversal attempt
    target_dir = repository_downloader._get_safe_target_directory("../../../etc")
    expected_dir = tmp_path / "firmware" / "repo-dls"
    assert target_dir == str(expected_dir)  # Should fall back to base directory


def test_is_safe_subdirectory_valid(repository_downloader):
    """Test _is_safe_subdirectory with valid subdirectories."""
    assert repository_downloader._is_safe_subdirectory("valid-dir") is True
    assert repository_downloader._is_safe_subdirectory("valid/dir/path") is True
    assert repository_downloader._is_safe_subdirectory("") is True


def test_is_safe_subdirectory_invalid(repository_downloader):
    """Test _is_safe_subdirectory with invalid subdirectories."""
    assert repository_downloader._is_safe_subdirectory("../../etc") is False
    assert repository_downloader._is_safe_subdirectory("/absolute/path") is False
    assert repository_downloader._is_safe_subdirectory("../parent") is False
    assert repository_downloader._is_safe_subdirectory("dir/../../etc") is False


def test_download_repository_file_success(
    repository_downloader, mock_file_info, tmp_path
):
    """Test download_repository_file with successful download."""
    # Mock the download_dir to use tmp_path
    repository_downloader.download_dir = str(tmp_path)

    # Mock the download method to return True
    with patch.object(repository_downloader, "download", return_value=True):
        with patch.object(repository_downloader, "verify", return_value=True):
            result = repository_downloader.download_repository_file(
                mock_file_info, "test-dir"
            )

    assert result.success is True
    assert result.release_tag == "repository"
    assert "test-firmware.bin" in result.file_path
    assert os.path.exists(result.file_path)


def test_download_repository_file_failure(
    repository_downloader, mock_file_info, tmp_path
):
    """Test download_repository_file with failed download."""
    repository_downloader.download_dir = str(tmp_path)

    # Mock the download method to return False
    with patch.object(repository_downloader, "download", return_value=False):
        result = repository_downloader.download_repository_file(
            mock_file_info, "test-dir"
        )

    assert result.success is False
    assert result.release_tag == "repository"
    assert (
        result.error_message == "Failed to download repository file: test-firmware.bin"
    )


def test_download_repository_file_invalid_info(repository_downloader, tmp_path):
    """Test download_repository_file with invalid file info."""
    repository_downloader.download_dir = str(tmp_path)

    # Test with missing name
    invalid_info = {"download_url": "http://example.com/file.bin"}
    result = repository_downloader.download_repository_file(invalid_info, "test-dir")
    assert result.success is False
    assert "Invalid file info" in result.error_message

    # Test with missing download_url
    invalid_info = {"name": "test.bin"}
    result = repository_downloader.download_repository_file(invalid_info, "test-dir")
    assert result.success is False
    assert "Invalid file info" in result.error_message


def test_download_repository_file_script_permissions(
    repository_downloader, mock_script_file_info, tmp_path
):
    """Test that shell scripts get executable permissions set."""
    repository_downloader.download_dir = str(tmp_path)

    with patch.object(repository_downloader, "download", return_value=True):
        with patch.object(repository_downloader, "verify", return_value=True):
            with patch.object(
                repository_downloader, "_set_executable_permissions"
            ) as mock_chmod:
                result = repository_downloader.download_repository_file(
                    mock_script_file_info, "test-dir"
                )

    assert result.success is True
    mock_chmod.assert_called_once_with(result.file_path)


def test_set_executable_permissions_success(repository_downloader, tmp_path):
    """Test _set_executable_permissions on Unix-like systems."""
    test_file = tmp_path / "test-script.sh"
    test_file.write_text("#!/bin/bash\necho 'test'")

    # Mock os.name to simulate Unix
    with patch("os.name", "posix"):
        result = repository_downloader._set_executable_permissions(str(test_file))

    assert result is True


def test_clean_repository_directory_success(repository_downloader, tmp_path):
    """Test clean_repository_directory successfully cleans the directory."""
    # Create test directory structure
    repo_dir = tmp_path / "firmware" / "repo-dls"
    repo_dir.mkdir(parents=True)

    # Add test files and directories
    (repo_dir / "test_file.txt").write_text("test content")
    (repo_dir / "test_dir").mkdir()
    (repo_dir / "test_dir" / "nested_file.txt").write_text("nested content")

    # Mock the download_dir to use tmp_path
    repository_downloader.download_dir = str(tmp_path)

    result = repository_downloader.clean_repository_directory()

    assert result is True
    assert repo_dir.exists()  # Directory should still exist
    assert len(list(repo_dir.iterdir())) == 0  # But should be empty


def test_clean_repository_directory_nonexistent(repository_downloader, tmp_path):
    """Test clean_repository_directory when directory doesn't exist."""
    repository_downloader.download_dir = str(tmp_path)

    result = repository_downloader.clean_repository_directory()

    assert result is True  # Should return True when nothing to clean


def test_clean_repository_directory_error(repository_downloader, tmp_path):
    """Test clean_repository_directory when cleanup fails."""
    repo_dir = tmp_path / "firmware" / "repo-dls"
    repo_dir.mkdir(parents=True)
    (repo_dir / "test_file.txt").write_text("test content")

    repository_downloader.download_dir = str(tmp_path)

    # Mock os.listdir to raise an error
    with patch("os.listdir", side_effect=OSError("Permission denied")):
        result = repository_downloader.clean_repository_directory()

    assert result is False


def test_download_repository_files_batch(
    repository_downloader, mock_file_info, mock_script_file_info, tmp_path
):
    """Test download_repository_files_batch with multiple files."""
    repository_downloader.download_dir = str(tmp_path)

    files_info = [mock_file_info, mock_script_file_info]

    with patch.object(repository_downloader, "download", return_value=True):
        with patch.object(repository_downloader, "verify", return_value=True):
            results = repository_downloader.download_repository_files_batch(
                files_info, "test-dir"
            )

    assert len(results) == 2
    assert all(result.success for result in results)


def test_get_repository_download_url(repository_downloader):
    """Test get_repository_download_url method."""
    url = repository_downloader.get_repository_download_url("firmware/test.bin")
    assert url == "https://meshtastic.github.io/firmware/test.bin"


def test_cleanup_old_versions(repository_downloader, tmp_path):
    """Test cleanup_old_versions method."""
    repository_downloader.download_dir = str(tmp_path)

    # Create test directory structure
    repo_dir = tmp_path / "firmware" / "repo-dls"
    repo_dir.mkdir(parents=True)
    (repo_dir / "test_file.txt").write_text("test content")

    # Mock clean_repository_directory to verify it's called
    with patch.object(
        repository_downloader, "clean_repository_directory", return_value=True
    ) as mock_clean:
        repository_downloader.cleanup_old_versions(5)

    mock_clean.assert_called_once()


def test_get_latest_release_tag(repository_downloader):
    """Test get_latest_release_tag method."""
    result = repository_downloader.get_latest_release_tag()
    assert result == "repository-latest"


def test_update_latest_release_tag(repository_downloader):
    """Test update_latest_release_tag method."""
    result = repository_downloader.update_latest_release_tag("v1.0.0")
    assert result is True


def test_should_download_release(repository_downloader):
    """Test should_download_release method."""
    # Repository downloads should always return True
    result = repository_downloader.should_download_release("v1.0.0", "test.bin")
    assert result is True


def test_get_repository_files_empty(repository_downloader):
    """Test get_repository_files method."""
    # This would normally make HTTP requests, but for testing we expect empty list
    # when no actual API calls are made
    files = repository_downloader.get_repository_files()
    assert files == []


# Integration tests


def test_repository_downloader_integration(repository_downloader, tmp_path):
    """Integration test for repository downloader workflow."""
    repository_downloader.download_dir = str(tmp_path)

    # Test the complete workflow
    files_info = [
        {
            "name": "firmware.bin",
            "download_url": "https://example.com/firmware.bin",
            "size": 1024,
        },
        {
            "name": "update.sh",
            "download_url": "https://example.com/update.sh",
            "size": 2048,
        },
    ]

    with patch.object(repository_downloader, "download", return_value=True):
        with patch.object(repository_downloader, "verify", return_value=True):
            results = repository_downloader.download_repository_files_batch(
                files_info, "test-firmware"
            )

    # Verify results
    assert len(results) == 2
    assert all(result.success for result in results)

    # Verify files were created
    repo_dir = tmp_path / "firmware" / "repo-dls" / "test-firmware"
    assert repo_dir.exists()
    assert (repo_dir / "firmware.bin").exists()
    assert (repo_dir / "update.sh").exists()


# Error handling tests


def test_download_repository_file_exception(
    repository_downloader, mock_file_info, tmp_path
):
    """Test download_repository_file handles exceptions gracefully."""
    repository_downloader.download_dir = str(tmp_path)

    # Mock download to raise an exception
    with patch.object(
        repository_downloader, "download", side_effect=RuntimeError("Network error")
    ):
        result = repository_downloader.download_repository_file(
            mock_file_info, "test-dir"
        )

    assert result.success is False
    assert "Network error" in result.error_message


def test_clean_repository_directory_partial_failure(repository_downloader, tmp_path):
    """Test clean_repository_directory handles partial failures."""
    repo_dir = tmp_path / "firmware" / "repo-dls"
    repo_dir.mkdir(parents=True)

    # Create files that will cause errors when removed
    (repo_dir / "test_file.txt").write_text("test content")
    (repo_dir / "protected_file.txt").write_text("protected")

    repository_downloader.download_dir = str(tmp_path)

    # Mock os.remove to fail on the second file
    def mock_remove(path):
        if "protected_file.txt" in path:
            raise OSError("Permission denied")
        # Normal removal for other files

    with patch("os.remove", side_effect=mock_remove):
        with patch("os.path.isfile", return_value=True):
            with patch("os.path.islink", return_value=False):
                result = repository_downloader.clean_repository_directory()

    # Should return False when cleanup fails
    assert result is False
