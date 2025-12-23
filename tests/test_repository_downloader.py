"""
Repository Downloader Tests

Tests for RepositoryDownloader class which handles downloading
files from the meshtastic.github.io repository.
"""

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fetchtastic.constants import REPO_DOWNLOADS_DIR
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import DownloadResult
from fetchtastic.download.repository import RepositoryDownloader

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def test_config():
    """Provide a test configuration dictionary."""
    return {
        "DOWNLOAD_DIR": "/tmp/test_repository",
        "VERSIONS_TO_KEEP": 2,
    }


@pytest.fixture
def repository_downloader(test_config):
    """Create a RepositoryDownloader instance for tests."""
    cache_manager = CacheManager(cache_dir="/tmp/test_cache")
    return RepositoryDownloader(test_config, cache_manager)


class TestRepositoryDownloader:
    """Test suite for RepositoryDownloader functionality."""

    def test_initialization(self, test_config):
        """Test RepositoryDownloader initialization."""
        cache_manager = CacheManager(cache_dir="/tmp/test_cache")
        downloader = RepositoryDownloader(test_config, cache_manager)

        assert downloader.config == test_config
        assert isinstance(downloader.cache_manager, CacheManager)
        assert downloader.download_dir == test_config["DOWNLOAD_DIR"]
        assert downloader.repo_downloads_dir == REPO_DOWNLOADS_DIR

    def test_get_cleanup_summary(self, repository_downloader):
        """Test getting cleanup summary."""
        summary = repository_downloader.get_cleanup_summary()
        assert isinstance(summary, dict)
        assert "download_dir" in summary

    def test_get_repository_files_with_subdirectory(self, repository_downloader):
        """Test getting repository files with subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache_manager = CacheManager(cache_dir=str(cache_dir))

            files = repository_downloader.get_repository_files(
                "subdir",
                cache_manager=cache_manager,
            )
            assert isinstance(files, list)

    def test_get_repository_files_from_root(self, repository_downloader):
        """Test getting repository files from root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache_manager = CacheManager(cache_dir=str(cache_dir))

            files = repository_downloader.get_repository_files(
                "",
                cache_manager=cache_manager,
            )
            assert isinstance(files, list)

    def test_get_repository_download_url(self, repository_downloader):
        """Test getting download URL for a file."""
        file_info = {
            "download_url": "https://example.com/file.txt",
            "name": "file.txt",
            "path": "subdir/file.txt",
        }
        url = repository_downloader.get_repository_download_url(file_info)
        assert url == "https://example.com/file.txt"

    def test_is_safe_subdirectory_valid(self, repository_downloader):
        """Test safe subdirectory validation."""
        result = repository_downloader._is_safe_subdirectory("safe_dir")
        assert result is True

    def test_is_safe_subdirectory_empty(self, repository_downloader):
        """Test safe subdirectory validation rejects empty."""
        result = repository_downloader._is_safe_subdirectory("")
        assert result is False

    def test_is_safe_subdirectory_traversal(self, repository_downloader):
        """Test safe subdirectory validation rejects path traversal."""
        result = repository_downloader._is_safe_subdirectory("../unsafe")
        assert result is False

    def test_set_executable_permissions_success(self, repository_downloader):
        """Test setting executable permissions on shell scripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.sh"
            test_file.write_bytes(b"#!/bin/bash\necho test")

            result = repository_downloader._set_executable_permissions(str(test_file))
            assert result is True

    def test_clean_repository_directory(self, repository_downloader):
        """Test cleaning repository directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            download_dir = Path(tmpdir) / "downloads"
            repo_dir = download_dir / REPO_DOWNLOADS_DIR
            repo_dir.mkdir()

            test_file = repo_dir / "test.txt"
            test_file.write_bytes(b"test")

            result = repository_downloader.clean_repository_directory(str(download_dir))
            assert result is True
            assert not test_file.exists()

    def test_clean_repository_directory_not_exists(self, repository_downloader):
        """Test cleaning nonexistent directory succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            download_dir = Path(tmpdir) / "downloads"

            result = repository_downloader.clean_repository_directory(str(download_dir))
            assert result is True

    def test_get_latest_release_tag(self, repository_downloader):
        """Test getting latest release tag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "latest_repo_release.json"
            cache_file.write_text('{"version": "v1.0.0"}')

            cache_manager = CacheManager(cache_dir=tmpdir)
            downloader = RepositoryDownloader(
                {"DOWNLOAD_DIR": str(tmpdir)}, cache_manager
            )

            tag = downloader.get_latest_release_tag()
            assert tag == "v1.0.0"

    def test_get_latest_release_tag_missing_file(self, repository_downloader):
        """Test getting latest release tag when file is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_manager = CacheManager(cache_dir=tmpdir)
            downloader = RepositoryDownloader(
                {"DOWNLOAD_DIR": str(tmpdir)}, cache_manager
            )

            tag = downloader.get_latest_release_tag()
            assert tag is None

    def test_update_latest_release_tag(self, repository_downloader):
        """Test updating latest release tag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "latest_repo_release.json"
            cache_file.write_text('{"version": "v1.0.0"}')

            cache_manager = CacheManager(cache_dir=tmpdir)
            downloader = RepositoryDownloader(
                {"DOWNLOAD_DIR": str(tmpdir)}, cache_manager
            )

            result = downloader.update_latest_release_tag("v1.0.0")
            assert result is True

    def test_validate_extraction_patterns(self, repository_downloader):
        """Test extraction pattern validation."""
        patterns = ["*.txt", "*.bin"]
        exclude_patterns = ["*debug*"]

        result = repository_downloader.validate_extraction_patterns(
            patterns, exclude_patterns
        )
        assert result is True

    def test_validate_extraction_patterns_empty_include(self, repository_downloader):
        """Test extraction pattern validation rejects empty include."""
        result = repository_downloader.validate_extraction_patterns([], ["*.bin"])
        assert result is False

    def test_validate_extraction_patterns_invalid_glob(self, repository_downloader):
        """Test extraction pattern validation rejects invalid patterns."""
        result = repository_downloader.validate_extraction_patterns(["*.txt"], ["../*"])
        assert result is False

    def test_should_download_release_exists(self, repository_downloader):
        """Test checking if release should be downloaded when file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            download_dir = Path(tmpdir) / "downloads"
            repo_dir = download_dir / REPO_DOWNLOADS_DIR
            repo_dir.mkdir()

            test_file = repo_dir / "test.txt"
            test_file.write_bytes(b"test")

            result = repository_downloader.should_download_release("v1.0.0", "test.txt")
            assert result is False

    def test_should_download_release_missing(self, repository_downloader):
        """Test checking if missing release should be downloaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            download_dir = Path(tmpdir) / "downloads"
            repo_dir = download_dir / REPO_DOWNLOADS_DIR
            repo_dir.mkdir()

            result = repository_downloader.should_download_release("v1.0.0", "test.txt")
            assert result is True
