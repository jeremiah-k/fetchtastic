"""
Additional tests for downloader.py to improve core downloads coverage.

These tests focus on utility functions, error paths, and edge cases
that are not well covered by existing tests.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.fetchtastic.downloader import (
    _atomic_write_json,
    _create_default_prerelease_entry,
    _ensure_cache_dir,
    _extract_clean_version,
    _get_prerelease_commit_history_file,
    _get_prerelease_dir_cache_file,
    _normalize_version,
    _safe_rmtree,
)


class TestDownloaderUtilities:
    """Test suite for downloader utility functions."""

    def test_ensure_cache_dir_creates_directory(self, tmp_path):
        """Test _ensure_cache_dir creates directory when it doesn't exist."""
        with patch("src.fetchtastic.downloader._cache_dir", tmp_path / "cache"):
            cache_dir = _ensure_cache_dir()

        assert Path(cache_dir).exists()
        assert Path(cache_dir).is_dir()

    def test_ensure_cache_dir_returns_existing(self, tmp_path):
        """Test _ensure_cache_dir returns existing directory."""
        cache_path = tmp_path / "existing_cache"
        cache_path.mkdir(parents=True)

        with patch("src.fetchtastic.downloader._cache_dir", str(cache_path)):
            result = _ensure_cache_dir()

        assert result == str(cache_path)

    def test_atomic_write_json_success(self, tmp_path):
        """Test successful atomic write."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value", "items": [1, 2, 3]}

        result = _atomic_write_json(str(test_file), test_data)

        assert result is True
        assert test_file.exists()

        with test_file.open("r") as f:
            loaded_data = f.read()
            assert test_data in loaded_data  # Check if data is in the file

    def test_atomic_write_json_failure(self, tmp_path):
        """Test atomic write failure."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value"}

        # Mock json.dump to raise exception
        with patch("json.dump", side_effect=TypeError("Not serializable")):
            result = _atomic_write_json(str(test_file), test_data)

        assert result is False

    def test_normalize_version_valid_version(self):
        """Test version normalization with valid version."""
        result = _normalize_version("1.2.3")
        assert result is not None
        assert str(result) == "1.2.3"

    def test_normalize_version_invalid_version(self):
        """Test version normalization with invalid version."""
        result = _normalize_version("not.a.version")
        assert result is None

    def test_normalize_version_none_input(self):
        """Test version normalization with None input."""
        result = _normalize_version(None)
        assert result is None

    def test_safe_rmtree_success(self, tmp_path):
        """Test safe directory removal success."""
        test_dir = tmp_path / "test_remove"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("test")

        result = _safe_rmtree(str(test_dir), str(tmp_path), "test_remove")

        assert result is True
        assert not test_dir.exists()

    def test_safe_rmtree_security_violation(self, tmp_path):
        """Test safe directory removal with security violation."""
        # Try to remove directory outside base (should fail)
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        result = _safe_rmtree(str(outside_dir), str(tmp_path), "../outside")

        assert result is False
        assert outside_dir.exists()

    def test_safe_rmtree_nonexistent(self, tmp_path):
        """Test safe directory removal with non-existent directory."""
        nonexistent = tmp_path / "nonexistent"

        result = _safe_rmtree(str(nonexistent), str(tmp_path), "nonexistent")

        assert result is True  # Should return True for non-existent (already "removed")

    def test_extract_clean_version_success(self, tmp_path):
        """Test successful clean version extraction from ZIP."""
        zip_path = tmp_path / "test.zip"

        # Create a test ZIP with version file
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("version.txt", "1.2.3")

        result = _extract_clean_version(str(zip_path))
        assert result == "1.2.3"

    def test_extract_clean_version_no_version_file(self, tmp_path):
        """Test clean version extraction from ZIP without version file."""
        zip_path = tmp_path / "test.zip"

        # Create a test ZIP without version file
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("other.txt", "content")

        result = _extract_clean_version(str(zip_path))
        assert result is None

    def test_extract_clean_version_invalid_zip(self, tmp_path):
        """Test clean version extraction from invalid ZIP."""
        invalid_zip = tmp_path / "invalid.zip"
        invalid_zip.write_text("not a zip file")

        result = _extract_clean_version(str(invalid_zip))
        assert result is None

    def test_create_default_prerelease_entry(self):
        """Test creation of default prerelease entry."""
        result = _create_default_prerelease_entry(
            directory="firmware-1.2.3abcdef",
            identifier="1.2.3abcdef",
            base_version="1.2.3",
            commit_hash="abcdef123",
        )

        expected = {
            "directory": "firmware-1.2.3abcdef",
            "identifier": "1.2.3abcdef",
            "base_version": "1.2.3",
            "added_at": None,
            "added_sha": None,
            "active": True,
            "status": "active",
            "removed_at": None,
            "removed_sha": None,
        }

        assert result == expected

    def test_get_prerelease_dir_cache_file(self):
        """Test getting prerelease dir cache file path."""
        with patch(
            "src.fetchtastic.downloader._ensure_cache_dir", return_value="/cache/dir"
        ):
            result = _get_prerelease_dir_cache_file()

        assert result == "/cache/dir/prerelease_dir_cache.json"

    def test_get_prerelease_commit_history_file(self):
        """Test getting prerelease commit history file path."""
        with patch(
            "src.fetchtastic.downloader._ensure_cache_dir", return_value="/cache/dir"
        ):
            result = _get_prerelease_commit_history_file()

        assert result == "/cache/dir/prerelease_commit_history.json"


class TestDownloaderErrorPaths:
    """Test error paths and edge cases in downloader functions."""

    def test_version_handling_edge_cases(self):
        """Test version handling with various edge cases."""
        # Test with pre-release versions
        result = _normalize_version("1.2.3-alpha")
        assert result is not None
        assert "alpha" in str(result)

        # Test with build metadata
        result = _normalize_version("1.2.3+build123")
        assert result is not None
        assert "build123" in str(result)

    def test_atomic_write_permissions_error(self, tmp_path):
        """Test atomic write with permission errors."""
        test_file = tmp_path / "readonly.json"
        test_data = {"key": "value"}

        # Create file and make it readonly
        test_file.write_text("{}")
        test_file.chmod(0o444)

        result = _atomic_write_json(str(test_file), test_data)
        assert result is False

    def test_cache_dir_creation_error(self):
        """Test cache directory creation error."""
        # Mock os.makedirs to raise OSError
        with patch("os.makedirs", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError):
                _ensure_cache_dir()

    def test_safe_rmtree_with_symlinks(self, tmp_path):
        """Test safe directory removal with symlinks."""
        test_dir = tmp_path / "test_symlink"
        test_dir.mkdir()

        # Create a symlink inside
        target_file = tmp_path / "target.txt"
        target_file.write_text("target")
        symlink_file = test_dir / "symlink.txt"
        symlink_file.symlink_to(target_file)

        result = _safe_rmtree(str(test_dir), str(tmp_path), "test_symlink")

        assert result is True
        assert not test_dir.exists()
        assert target_file.exists()  # Target should not be removed

    def test_extract_clean_version_corrupted_zip(self, tmp_path):
        """Test clean version extraction from corrupted ZIP."""
        corrupted_zip = tmp_path / "corrupted.zip"
        corrupted_zip.write_bytes(b"corrupted zip content")

        result = _extract_clean_version(str(corrupted_zip))
        assert result is None
