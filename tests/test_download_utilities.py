"""
Additional core download functionality tests for fetchtastic downloader module.

This module contains tests for utility functions and core functionality that
were not covered in the original test_download_core.py module.

Tests include:
- Version normalization and comparison
- File operations and atomic writes
- Path sanitization and security
- Cache management
- Prerelease handling utilities
- Configuration parsing
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from fetchtastic import downloader


@pytest.mark.core_downloads
@pytest.mark.unit
class TestVersionUtilities:
    """Test version-related utility functions."""

    def test_normalize_version_with_v_prefix(self):
        """Test version normalization with v prefix."""
        result = downloader._normalize_version("v1.2.3")
        assert result == "v1.2.3"

    def test_normalize_version_without_v_prefix(self):
        """Test version normalization without v prefix."""
        result = downloader._normalize_version("1.2.3")
        assert result == "v1.2.3"

    def test_normalize_version_with_hash(self):
        """Test version normalization with commit hash."""
        result = downloader._normalize_version("v1.2.3-abc123")
        assert result == "v1.2.3"

    def test_normalize_version_none(self):
        """Test version normalization with None."""
        result = downloader._normalize_version(None)
        assert result is None

    def test_get_release_tuple_valid_version(self):
        """Test getting release tuple from valid version."""
        result = downloader._get_release_tuple("v1.2.3")
        assert result == (1, 2, 3)

    def test_get_release_tuple_invalid_version(self):
        """Test getting release tuple from invalid version."""
        result = downloader._get_release_tuple("invalid")
        assert result is None

    def test_get_release_tuple_none(self):
        """Test getting release tuple from None."""
        result = downloader._get_release_tuple(None)
        assert result is None

    def test_compare_versions_greater_than(self):
        """Test version comparison where first is greater."""
        result = downloader.compare_versions("v1.2.3", "v1.2.2")
        assert result == 1

    def test_compare_versions_less_than(self):
        """Test version comparison where first is less."""
        result = downloader.compare_versions("v1.2.2", "v1.2.3")
        assert result == -1

    def test_compare_versions_equal(self):
        """Test version comparison where versions are equal."""
        result = downloader.compare_versions("v1.2.3", "v1.2.3")
        assert result == 0

    def test_ensure_v_prefix_if_missing_with_v(self):
        """Test ensuring v prefix when already present."""
        result = downloader._ensure_v_prefix_if_missing("v1.2.3")
        assert result == "v1.2.3"

    def test_ensure_v_prefix_if_missing_without_v(self):
        """Test ensuring v prefix when missing."""
        result = downloader._ensure_v_prefix_if_missing("1.2.3")
        assert result == "v1.2.3"

    def test_ensure_v_prefix_if_missing_none(self):
        """Test ensuring v prefix with None."""
        result = downloader._ensure_v_prefix_if_missing(None)
        assert result is None

    def test_extract_clean_version_with_hash(self):
        """Test extracting clean version from version with hash."""
        result = downloader._extract_clean_version("v1.2.3-abc123def")
        assert result == "v1.2.3"

    def test_extract_clean_version_without_hash(self):
        """Test extracting clean version from version without hash."""
        result = downloader._extract_clean_version("v1.2.3")
        assert result == "v1.2.3"

    def test_extract_clean_version_none(self):
        """Test extracting clean version from None."""
        result = downloader._extract_clean_version(None)
        assert result is None

    def test_calculate_expected_prerelease_version(self):
        """Test calculating expected prerelease version."""
        result = downloader.calculate_expected_prerelease_version("v1.2.3")
        assert result == "v1.2.4-prerelease"


@pytest.mark.core_downloads
@pytest.mark.unit
class TestFileOperations:
    """Test file operation utility functions."""

    def test_atomic_write_success(self, tmp_path):
        """Test successful atomic write."""
        test_file = tmp_path / "test.txt"
        content = b"test content"

        result = downloader._atomic_write(str(test_file), content)

        assert result is True
        assert test_file.exists()
        assert test_file.read_bytes() == content

    def test_atomic_write_failure(self, tmp_path):
        """Test atomic write failure with invalid path."""
        invalid_path = "/invalid/path/test.txt"
        content = b"test content"

        result = downloader._atomic_write(invalid_path, content)

        assert result is False

    def test_atomic_write_text_success(self, tmp_path):
        """Test successful atomic text write."""
        test_file = tmp_path / "test.txt"
        content = "test content"

        result = downloader._atomic_write_text(str(test_file), content)

        assert result is True
        assert test_file.exists()
        assert test_file.read_text() == content

    def test_atomic_write_json_success(self, tmp_path):
        """Test successful atomic JSON write."""
        test_file = tmp_path / "test.json"
        data = {"key": "value", "number": 42}

        result = downloader._atomic_write_json(str(test_file), data)

        assert result is True
        assert test_file.exists()
        assert json.loads(test_file.read_text()) == data

    def test_sanitize_path_component_normal(self):
        """Test sanitizing normal path component."""
        result = downloader._sanitize_path_component("normal-path")
        assert result == "normal-path"

    def test_sanitize_path_component_with_slashes(self):
        """Test sanitizing path component with slashes."""
        result = downloader._sanitize_path_component("path/with/slashes")
        assert result == "path-with-slashes"

    def test_sanitize_path_component_with_dots(self):
        """Test sanitizing path component with dots."""
        result = downloader._sanitize_path_component("../path")
        assert result == "-path"

    def test_sanitize_path_component_none(self):
        """Test sanitizing None path component."""
        result = downloader._sanitize_path_component(None)
        assert result is None

    def test_safe_rmtree_success(self, tmp_path):
        """Test safe directory removal."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("content")

        result = downloader._safe_rmtree(str(test_dir), str(tmp_path), "test_dir")

        assert result is True
        assert not test_dir.exists()

    def test_safe_rmtree_nonexistent(self):
        """Test safe removal of non-existent directory."""
        result = downloader._safe_rmtree("/nonexistent/path", "/base", "test")
        assert result is False

    def test_strip_unwanted_chars(self):
        """Test stripping unwanted characters from text."""
        result = downloader.strip_unwanted_chars("test\x00\x01\x02text")
        assert result == "testtest"

    def test_set_permissions_on_sh_files(self, tmp_path):
        """Test setting permissions on shell files."""
        # Create test directory with .sh files
        test_dir = tmp_path / "test"
        test_dir.mkdir()

        sh_file1 = test_dir / "script1.sh"
        sh_file2 = test_dir / "script2.sh"
        txt_file = test_dir / "readme.txt"

        sh_file1.write_text("#!/bin/bash\necho 'test1'")
        sh_file2.write_text("#!/bin/bash\necho 'test2'")
        txt_file.write_text("readme")

        # Set permissions
        downloader.set_permissions_on_sh_files(str(test_dir))

        # Check that .sh files are executable
        assert os.access(sh_file1, os.X_OK)
        assert os.access(sh_file2, os.X_OK)
        # Check that .txt file is not executable
        assert not os.access(txt_file, os.X_OK)


@pytest.mark.core_downloads
@pytest.mark.unit
class TestPatternMatching:
    """Test pattern matching functions."""

    def test_matches_exclude_with_pattern(self):
        """Test matching exclude patterns."""
        result = downloader._matches_exclude("test-debug.zip", ["*-debug*"])
        assert result is True

    def test_matches_exclude_no_pattern(self):
        """Test no matching exclude patterns."""
        result = downloader._matches_exclude("test-release.zip", ["*-debug*"])
        assert result is False

    def test_matches_extract_patterns_with_device_manager(self, tmp_path):
        """Test extract patterns with device manager."""
        # Mock device manager
        mock_device_manager = MagicMock()
        mock_device_manager.is_device_match.return_value = True

        result = downloader.matches_extract_patterns(
            "firmware-heltec-v3.zip", ["heltec"], mock_device_manager
        )
        assert result is True

    def test_matches_extract_patterns_without_device_manager(self):
        """Test extract patterns without device manager."""
        result = downloader.matches_extract_patterns(
            "firmware-heltec-v3.zip", ["*heltec*"], None
        )
        assert result is True

    def test_matches_extract_patterns_no_match(self):
        """Test extract patterns with no match."""
        result = downloader.matches_extract_patterns(
            "firmware-rak4631.zip", ["*heltec*"], None
        )
        assert result is False


@pytest.mark.core_downloads
@pytest.mark.unit
class TestCacheManagement:
    """Test cache management functions."""

    def test_ensure_cache_dir(self):
        """Test ensuring cache directory exists."""
        with patch("fetchtastic.downloader._CACHE_DIR") as mock_cache_dir:
            mock_cache_dir.exists.return_value = False
            mock_cache_dir.mkdir.return_value = None

            result = downloader._ensure_cache_dir()

            mock_cache_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_get_commit_cache_file(self):
        """Test getting commit cache file path."""
        with patch("fetchtastic.downloader._ensure_cache_dir") as mock_ensure:
            mock_ensure.return_value = "/cache/dir"

            result = downloader._get_commit_cache_file()

            assert result == "/cache/dir/commit_timestamps.json"

    def test_get_releases_cache_file(self):
        """Test getting releases cache file path."""
        with patch("fetchtastic.downloader._ensure_cache_dir") as mock_ensure:
            mock_ensure.return_value = "/cache/dir"

            result = downloader._get_releases_cache_file()

            assert result == "/cache/dir/releases_cache.json"

    def test_clear_commit_cache(self):
        """Test clearing commit cache."""
        with patch("fetchtastic.downloader._get_commit_cache_file") as mock_get_file:
            mock_get_file.return_value = "/cache/commit.json"

            with patch("os.path.exists", return_value=True):
                with patch("os.remove") as mock_remove:
                    downloader._clear_commit_cache()
                    mock_remove.assert_called_once_with("/cache/commit.json")

    def test_clear_all_caches(self):
        """Test clearing all caches."""
        with patch("fetchtastic.downloader._clear_commit_cache") as mock_commit:
            with patch("fetchtastic.downloader._clear_releases_cache") as mock_releases:
                downloader.clear_all_caches()
                mock_commit.assert_called_once()
                mock_releases.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
class TestPrereleaseUtilities:
    """Test prerelease-related utility functions."""

    def test_parse_new_json_format(self):
        """Test parsing new JSON format."""
        data = {
            "latest_release_tag": "v1.2.3",
            "current_prerelease": "v1.2.4-prerelease-abc123",
        }

        result = downloader._parse_new_json_format(data)

        assert result["latest_release_tag"] == "v1.2.3"
        assert result["current_prerelease"] == "v1.2.4-prerelease-abc123"

    def test_parse_legacy_json_format(self):
        """Test parsing legacy JSON format."""
        data = {
            "latest_tag": "v1.2.3",
            "current_prerelease": "v1.2.4-prerelease-abc123",
        }

        result = downloader._parse_legacy_json_format(data)

        assert result["latest_release_tag"] == "v1.2.3"
        assert result["current_prerelease"] == "v1.2.4-prerelease-abc123"

    def test_get_commit_hash_from_dir(self):
        """Test extracting commit hash from directory name."""
        result = downloader._get_commit_hash_from_dir("v1.2.3-prerelease-abc123def")
        assert result == "abc123def"

    def test_get_commit_hash_from_dir_no_hash(self):
        """Test extracting commit hash from directory without hash."""
        result = downloader._get_commit_hash_from_dir("v1.2.3")
        assert result is None

    def test_get_prerelease_patterns(self):
        """Test getting prerelease patterns from config."""
        config = {"prerelease_patterns": ["*-prerelease*", "*-beta*"]}

        result = downloader._get_prerelease_patterns(config)

        assert "*-prerelease*" in result
        assert "*-beta*" in result

    def test_get_prerelease_patterns_default(self):
        """Test getting default prerelease patterns."""
        config = {}

        result = downloader._get_prerelease_patterns(config)

        assert "*-prerelease*" in result


@pytest.mark.core_downloads
@pytest.mark.unit
class TestReleaseUtilities:
    """Test release-related utility functions."""

    def test_summarise_release_scan(self):
        """Test release scan summarization."""
        result = downloader._summarise_release_scan("Firmware", 5, 3)

        assert "Firmware" in result
        assert "5" in result
        assert "3" in result

    def test_summarise_scan_window(self):
        """Test scan window summarization."""
        result = downloader._summarise_scan_window("Firmware", 10)

        assert "Firmware" in result
        assert "10" in result

    def test_is_release_complete_true(self):
        """Test checking if release is complete (true case)."""
        release = {"assets": [{"name": "firmware.zip"}, {"name": "bootloader.zip"}]}
        config = {"required_assets": ["firmware.zip", "bootloader.zip"]}

        result = downloader._is_release_complete(release, config)

        assert result is True

    def test_is_release_complete_false(self):
        """Test checking if release is complete (false case)."""
        release = {"assets": [{"name": "firmware.zip"}]}
        config = {"required_assets": ["firmware.zip", "bootloader.zip"]}

        result = downloader._is_release_complete(release, config)

        assert result is False

    def test_is_release_complete_no_required_assets(self):
        """Test checking if release is complete with no required assets specified."""
        release = {"assets": []}
        config = {}

        result = downloader._is_release_complete(release, config)

        assert result is True

    def test_normalize_commit_identifier(self):
        """Test normalizing commit identifier."""
        result = downloader._normalize_commit_identifier("abc123def", "v1.2.3")

        assert result == "abc123def"

    def test_normalize_commit_identifier_fallback(self):
        """Test normalizing commit identifier with fallback."""
        result = downloader._normalize_commit_identifier(None, "v1.2.3")

        assert result == "v1.2.3"
