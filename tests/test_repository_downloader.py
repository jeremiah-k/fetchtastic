"""
Repository Downloader Tests

Tests for RepositoryDownloader class which handles downloading
files from the meshtastic.github.io repository.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from fetchtastic.constants import (
    FIRMWARE_DIR_NAME,
    REPO_DOWNLOADS_DIR,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.download.repository import RepositoryDownloader

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def test_config():
    """
    Provide a test configuration used by RepositoryDownloader tests.

    Returns:
        dict: Test configuration with keys:
            DOWNLOAD_DIR (str): base download directory path.
            VERSIONS_TO_KEEP (int): number of versions to retain.
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test_repository",
        "VERSIONS_TO_KEEP": 2,
    }


@pytest.fixture
def repository_downloader(test_config):
    """
    Create a RepositoryDownloader configured for use in tests.

    Parameters:
        test_config (dict): Test configuration containing DOWNLOAD_DIR and VERSIONS_TO_KEEP.

    Returns:
        RepositoryDownloader: An instance of RepositoryDownloader configured with `test_config`.
    """
    return RepositoryDownloader(test_config)


class TestRepositoryDownloader:
    """Test suite for RepositoryDownloader functionality."""

    def test_initialization(self, test_config):
        """Test RepositoryDownloader initialization."""
        downloader = RepositoryDownloader(test_config)

        assert downloader.config == test_config
        assert downloader.download_dir == test_config["DOWNLOAD_DIR"]
        assert downloader.repo_downloads_dir == REPO_DOWNLOADS_DIR
        assert downloader.shell_script_extension == SHELL_SCRIPT_EXTENSION

    def test_get_cleanup_summary(self, repository_downloader):
        """Test getting cleanup summary."""
        summary = repository_downloader.get_cleanup_summary()
        assert isinstance(summary, dict)
        assert "removed_files" in summary
        assert "removed_dirs" in summary
        assert "errors" in summary
        assert "success" in summary

    @patch("fetchtastic.utils.make_github_api_request")
    def test_get_repository_files_with_subdirectory(
        self, mock_request, repository_downloader
    ):
        """Test getting repository files with subdirectory."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "file.txt",
                "path": "subdir/file.txt",
                "download_url": "https://example.com/file.txt",
                "size": 100,
                "type": "file",
            },
        ]
        mock_request.return_value = mock_response

        files = repository_downloader.get_repository_files("subdir")
        assert isinstance(files, list)
        assert len(files) == 1
        assert files[0]["name"] == "file.txt"

    @patch("fetchtastic.utils.make_github_api_request")
    def test_get_repository_files_from_root(self, mock_request, repository_downloader):
        """Test getting repository files from root."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "readme.md",
                "path": "readme.md",
                "download_url": "https://example.com/readme.md",
                "size": 50,
                "type": "file",
            },
        ]
        mock_request.return_value = mock_response

        files = repository_downloader.get_repository_files("")
        assert isinstance(files, list)
        assert len(files) == 1
        assert files[0]["name"] == "readme.md"

    def test_get_repository_download_url(self, repository_downloader):
        """Test getting download URL for a file."""
        url = repository_downloader.get_repository_download_url("subdir/file.txt")
        assert url.endswith("subdir/file.txt")

    def test_get_repository_download_url_raises_for_absolute_path(
        self, repository_downloader
    ):
        """Test get_repository_download_url raises ValueError for absolute path."""
        with pytest.raises(ValueError):
            repository_downloader.get_repository_download_url("/absolute/path.txt")

    def test_get_repository_download_url_raises_for_url(self, repository_downloader):
        """Test get_repository_download_url raises ValueError for URL."""
        with pytest.raises(ValueError):
            repository_downloader.get_repository_download_url(
                "https://example.com/file.txt"
            )

    def test_is_safe_subdirectory_valid(self, repository_downloader):
        """Test safe subdirectory validation."""
        result = repository_downloader._is_safe_subdirectory("safe_dir")
        assert result is True

    def test_is_safe_subdirectory_empty(self, repository_downloader):
        """Test safe subdirectory validation with empty string - requires check separately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)
            os.makedirs(os.path.join(tmpdir, FIRMWARE_DIR_NAME, REPO_DOWNLOADS_DIR))
            result = downloader._is_safe_subdirectory("")
            assert result is True

    def test_is_safe_subdirectory_traversal(self, repository_downloader):
        """Test safe subdirectory validation rejects path traversal."""
        result = repository_downloader._is_safe_subdirectory("../unsafe")
        assert result is False

    def test_is_safe_subdirectory_backslash(self, repository_downloader):
        """Test safe subdirectory validation rejects backslash."""
        result = repository_downloader._is_safe_subdirectory("unsafe\\path")
        assert result is False

    def test_set_executable_permissions_success(self, repository_downloader):
        """Test setting executable permissions on shell scripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.sh"
            test_file.write_bytes(b"#!/bin/bash\necho test")

            result = repository_downloader._set_executable_permissions(str(test_file))
            assert result is True

    def test_set_executable_permissions_windows(self, repository_downloader):
        """Test setting executable permissions on Windows is no-op."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.bat"
            test_file.write_bytes(b"echo test")

            with patch("os.name", "nt"):
                result = repository_downloader._set_executable_permissions(
                    str(test_file)
                )
                assert result is True

    def test_clean_repository_directory(self, repository_downloader):
        """Test cleaning repository directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)
            repo_dir = Path(tmpdir) / FIRMWARE_DIR_NAME / REPO_DOWNLOADS_DIR
            repo_dir.mkdir(parents=True)

            test_file = repo_dir / "test.txt"
            test_file.write_bytes(b"test")

            result = downloader.clean_repository_directory()
            assert result is True
            assert not test_file.exists()

    def test_clean_repository_directory_not_exists(self, repository_downloader):
        """Test cleaning nonexistent directory succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)

            result = downloader.clean_repository_directory()
            assert result is True

    def test_get_latest_release_tag(self, repository_downloader):
        """Test getting latest release tag."""
        tag = repository_downloader.get_latest_release_tag()
        assert tag == "repository-latest"

    def test_update_latest_release_tag(self, repository_downloader):
        """Test updating latest release tag (no-op)."""
        result = repository_downloader.update_latest_release_tag("v1.0.0")
        assert result is True

    def test_validate_extraction_patterns(self, repository_downloader):
        """Test extraction pattern validation."""
        patterns = ["*.txt", "*.bin"]
        exclude_patterns = ["*debug*"]

        result = repository_downloader.validate_extraction_patterns(
            patterns, exclude_patterns
        )
        assert result is True

    def test_should_download_release(self, repository_downloader):
        """Test checking if release should be downloaded."""
        result = repository_downloader.should_download_release("v1.0.0", "test.txt")
        assert result is True

    def test_check_extraction_needed(self, repository_downloader):
        """Test checking if extraction is needed."""
        result = repository_downloader.check_extraction_needed(
            "/path/to/file.txt", "/extract", ["*.txt"], []
        )
        assert result is False

    def test_cleanup_old_versions(self, repository_downloader):
        """Test cleaning up old versions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)
            repo_dir = Path(tmpdir) / FIRMWARE_DIR_NAME / REPO_DOWNLOADS_DIR
            repo_dir.mkdir(parents=True)

            test_file = repo_dir / "test.txt"
            test_file.write_bytes(b"test")

            downloader.cleanup_old_versions(5)
            assert not test_file.exists()

    def test_get_safe_target_directory_base(self, repository_downloader):
        """Test getting safe target directory (base)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)

            target_dir = downloader._get_safe_target_directory("")
            expected_dir = Path(tmpdir) / FIRMWARE_DIR_NAME / REPO_DOWNLOADS_DIR
            assert target_dir == str(expected_dir)
            assert Path(target_dir).exists()

    def test_get_safe_target_directory_with_subdir(self, repository_downloader):
        """Test getting safe target directory with subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)

            target_dir = downloader._get_safe_target_directory("subdir")
            expected_dir = (
                Path(tmpdir) / FIRMWARE_DIR_NAME / REPO_DOWNLOADS_DIR / "subdir"
            )
            assert target_dir == str(expected_dir)
            assert Path(target_dir).exists()

    def test_get_safe_target_directory_unsafe(self, repository_downloader):
        """Test getting safe target directory sanitizes unsafe path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_config = {"DOWNLOAD_DIR": tmpdir, "VERSIONS_TO_KEEP": 2}
            downloader = RepositoryDownloader(test_config)

            target_dir = downloader._get_safe_target_directory("../unsafe")
            expected_dir = Path(tmpdir) / FIRMWARE_DIR_NAME / REPO_DOWNLOADS_DIR
            assert target_dir == str(expected_dir)

    @patch("fetchtastic.utils.make_github_api_request")
    def test_get_repository_files_api_error(self, mock_request, repository_downloader):
        """Test get_repository_files handles API errors."""
        mock_request.side_effect = requests.RequestException("API Error")

        files = repository_downloader.get_repository_files("subdir")
        assert files == []

    @patch("fetchtastic.utils.make_github_api_request")
    def test_get_repository_files_empty_response(
        self, mock_request, repository_downloader
    ):
        """Test get_repository_files handles empty response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        files = repository_downloader.get_repository_files("subdir")
        assert files == []
