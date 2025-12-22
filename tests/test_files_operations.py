"""
Tests for File Operations Module

Comprehensive tests for the files.py module covering:
- File hash verification
- Archive extraction with safety checks
- Atomic file writes
- Path validation and security
- File cleanup operations
- Pattern validation
"""

import hashlib
import os
import zipfile
from pathlib import Path

import platformdirs
import pytest

from fetchtastic import utils
from fetchtastic.download.files import (
    FileOperations,
    _get_existing_prerelease_dirs,
    _is_release_complete,
    _matches_exclude,
    _prepare_for_redownload,
    _sanitize_path_component,
    safe_extract_path,
    strip_unwanted_chars,
)


class TestStripUnwantedChars:
    """Test strip_unwanted_chars function."""

    def test_strip_non_ascii_chars(self):
        """Test stripping non-ASCII characters."""
        result = strip_unwanted_chars("hello©world™test")
        assert result == "helloworldtest"

    def test_no_non_ascii_chars(self):
        """Test with no non-ASCII characters."""
        result = strip_unwanted_chars("hello world")
        assert result == "hello world"

    def test_empty_string(self):
        """Test with empty string."""
        result = strip_unwanted_chars("")
        assert result == ""

    def test_only_non_ascii(self):
        """Test with only non-ASCII characters."""
        result = strip_unwanted_chars("©™®")
        assert result == ""


class TestSanitizePathComponent:
    """Test _sanitize_path_component function."""

    def test_valid_component(self):
        """Test with valid path component."""
        result = _sanitize_path_component("valid_name")
        assert result == "valid_name"

    def test_strip_whitespace(self):
        """Test stripping whitespace."""
        result = _sanitize_path_component("  test  ")
        assert result == "test"

    def test_empty_component(self):
        """Test with empty component."""
        result = _sanitize_path_component("")
        assert result is None

    def test_dot_component(self):
        """Test with dot component."""
        result = _sanitize_path_component(".")
        assert result is None

    def test_double_dot_component(self):
        """Test with double dot component."""
        result = _sanitize_path_component("..")
        assert result is None

    def test_absolute_path(self):
        """Test with absolute path."""
        result = _sanitize_path_component("/absolute/path")
        assert result is None

    def test_path_separators(self):
        """Test with path separators."""
        result = _sanitize_path_component("path/with/separators")
        assert result is None

    def test_null_byte(self):
        """Test with null byte."""
        result = _sanitize_path_component("test\x00name")
        assert result is None


class TestMatchesExclude:
    """Test _matches_exclude function."""

    def test_case_insensitive_match(self):
        """Test case-insensitive matching."""
        assert _matches_exclude("test.exe", ["*.exe"]) is True
        assert _matches_exclude("TEST.EXE", ["*.exe"]) is True

    def test_no_match(self):
        """Test with no match."""
        assert _matches_exclude("test.txt", ["*.exe"]) is False

    def test_empty_patterns(self):
        """Test with empty patterns list."""
        assert _matches_exclude("test.exe", []) is False


class TestGetExistingPrereleaseDirs:
    """Test _get_existing_prerelease_dirs function."""

    def test_no_directory(self):
        """Test with non-existent directory."""
        result = _get_existing_prerelease_dirs("/non/existent/path")
        assert result == []

    def test_empty_directory(self, tmp_path):
        """Test with empty directory."""
        result = _get_existing_prerelease_dirs(str(tmp_path))
        assert result == []

    def test_with_prerelease_dirs(self, tmp_path):
        """Test with prerelease directories."""
        # Create some directories
        (tmp_path / "firmware-1.2.3.abc123").mkdir()
        (tmp_path / "firmware-1.2.4.def456").mkdir()
        (tmp_path / "regular_dir").mkdir()
        (tmp_path / "not_firmware-1.2.5").mkdir()

        result = _get_existing_prerelease_dirs(str(tmp_path))
        assert len(result) == 2
        assert "firmware-1.2.3.abc123" in result
        assert "firmware-1.2.4.def456" in result

    def test_with_symlinks(self, tmp_path):
        """Test handling of symlinks."""
        # Create a regular directory and a symlink
        real_dir = tmp_path / "firmware-1.2.3.abc123"
        real_dir.mkdir()

        # Create symlink (if supported)
        try:
            link_dir = tmp_path / "firmware-1.2.4.def456"
            link_dir.symlink_to(real_dir)
            result = _get_existing_prerelease_dirs(str(tmp_path))
            # Should not include symlinks
            assert len(result) == 1
            assert "firmware-1.2.3.abc123" in result
        except OSError:
            # Symlinks not supported on this platform
            result = _get_existing_prerelease_dirs(str(tmp_path))
            assert len(result) == 1


class TestIsReleaseComplete:
    """Test _is_release_complete function."""

    def test_no_directory(self, tmp_path):
        """Test with non-existent directory."""
        result = _is_release_complete({}, str(tmp_path / "nonexistent"), [], [])
        assert result is False

    def test_empty_directory(self, tmp_path):
        """Test with empty directory."""
        (tmp_path / "release").mkdir()
        result = _is_release_complete(
            {"assets": [{"name": "file1.zip", "size": 100}]},
            str(tmp_path / "release"),
            [],
            [],
        )
        assert result is False

    def test_complete_release(self, tmp_path):
        """Test with complete release."""
        release_dir = tmp_path / "release"
        release_dir.mkdir()

        # Create a valid zip file
        test_file = release_dir / "file1.zip"
        with zipfile.ZipFile(test_file, "w") as zf:
            zf.writestr("content.txt", "test content")

        # Get the actual size of the created zip file
        actual_size = os.path.getsize(test_file)

        result = _is_release_complete(
            {"assets": [{"name": "file1.zip", "size": actual_size}]},
            str(release_dir),
            [],
            [],
        )
        assert result is True

    def test_incomplete_release_wrong_size(self, tmp_path):
        """Test with wrong file size."""
        release_dir = tmp_path / "release"
        release_dir.mkdir()

        test_file = release_dir / "file1.zip"
        test_file.write_bytes(b"test")  # 4 bytes, not 120

        result = _is_release_complete(
            {"assets": [{"name": "file1.zip", "size": 120}]}, str(release_dir), [], []
        )
        assert result is False

    def test_corrupted_zip_file(self, tmp_path):
        """
        Verify _is_release_complete returns False when an asset's ZIP file is present but is corrupted or not a valid ZIP.

        Creates a non-ZIP file named as the expected asset and asserts the function reports the release as incomplete.
        """
        release_dir = tmp_path / "release"
        release_dir.mkdir()

        # Create a corrupted zip file
        test_file = release_dir / "file1.zip"
        test_file.write_bytes(b"not a zip file")

        result = _is_release_complete(
            {"assets": [{"name": "file1.zip", "size": 15}]}, str(release_dir), [], []
        )
        assert result is False


class TestFileOperations:
    """Test FileOperations class."""

    def test_atomic_write_success(self, tmp_path):
        """Test successful atomic write."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        content = "test content"

        result = file_ops.atomic_write(str(test_file), content)
        assert result is True
        assert test_file.exists()
        assert test_file.read_text() == content

    def test_atomic_write_failure(self):
        """Test atomic write failure."""
        file_ops = FileOperations()

        # Try to write to invalid path
        result = file_ops.atomic_write("/invalid/path/test.txt", "content")
        assert result is False

    def test_verify_file_hash_exists(self, tmp_path):
        """Test hash verification for existing file."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        content = "test content"
        test_file.write_text(content)

        # Calculate expected hash
        expected_hash = hashlib.sha256(content.encode()).hexdigest()

        result = file_ops.verify_file_hash(str(test_file), expected_hash)
        assert result is True

    def test_verify_file_hash_wrong_hash(self, tmp_path):
        """Test hash verification with wrong hash."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        result = file_ops.verify_file_hash(str(test_file), "wrong_hash")
        assert result is False

    def test_verify_file_hash_no_file(self):
        """Test hash verification for non-existent file."""
        file_ops = FileOperations()
        result = file_ops.verify_file_hash("/non/existent/file.txt", "hash")
        assert result is False

    def test_verify_file_hash_no_expected_hash(self, tmp_path):
        """Test hash verification without expected hash."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        result = file_ops.verify_file_hash(str(test_file))
        assert result is True

    def test_extract_archive_no_patterns(self, tmp_path):
        """Test archive extraction with no patterns."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"
        extract_dir = tmp_path / "extract"

        # Create a dummy zip file
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")

        result = file_ops.extract_archive(str(zip_path), str(extract_dir), [], [])
        assert result == []

    def test_extract_archive_with_patterns(self, tmp_path):
        """Test archive extraction with patterns."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        # Create a zip file with multiple files
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")
            zf.writestr("script.sh", "echo hello")
            zf.writestr("data.bin", b"binary data")

        result = file_ops.extract_archive(
            str(zip_path), str(extract_dir), ["*.txt", "*.sh"], []
        )

        assert len(result) == 2
        extracted_files = [p.name for p in result]
        assert "test.txt" in extracted_files
        assert "script.sh" in extracted_files

    def test_extract_archive_exclude_patterns(self, tmp_path):
        """Test archive extraction with exclude patterns."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")
            zf.writestr("secret.txt", "secret")

        result = file_ops.extract_archive(
            str(zip_path), str(extract_dir), ["*.txt"], ["secret*"]
        )

        assert len(result) == 1
        assert result[0].name == "test.txt"

    def test_validate_extraction_patterns_valid(self):
        """Test pattern validation with valid patterns."""
        file_ops = FileOperations()
        result = file_ops.validate_extraction_patterns(["*.txt", "*.sh"], ["*.tmp"])
        assert result is True

    def test_validate_extraction_patterns_empty(self):
        """Test pattern validation with empty patterns."""
        file_ops = FileOperations()
        result = file_ops.validate_extraction_patterns([""], [])
        assert result is False

    def test_validate_extraction_patterns_path_traversal(self):
        """Test pattern validation with path traversal."""
        file_ops = FileOperations()
        result = file_ops.validate_extraction_patterns(["../../../etc/passwd"], [])
        assert result is False

    def test_validate_extraction_patterns_overly_broad(self):
        """Test pattern validation with overly broad patterns."""
        file_ops = FileOperations()
        result = file_ops.validate_extraction_patterns(["*" * 10], [])
        assert result is False

    def test_check_extraction_needed_no_zip(self):
        """Test extraction check when zip doesn't exist."""
        file_ops = FileOperations()
        result = file_ops.check_extraction_needed(
            "/non/existent.zip", "/extract/dir", ["*.txt"], []
        )
        assert result is False

    def test_check_extraction_needed_no_patterns(self, tmp_path):
        """Test extraction check with no patterns."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")

        result = file_ops.check_extraction_needed(
            str(zip_path), str(tmp_path / "extract"), [], []
        )
        assert result is False

    def test_extract_with_validation_success(self, tmp_path):
        """Test successful extraction with validation."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"
        extract_dir = tmp_path / "extract"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")

        result = file_ops.extract_with_validation(
            str(zip_path), str(extract_dir), ["*.txt"], []
        )

        assert len(result) == 1
        assert result[0].name == "test.txt"

    def test_extract_with_validation_invalid_patterns(self, tmp_path):
        """Test extraction with invalid patterns."""
        file_ops = FileOperations()
        zip_path = tmp_path / "test.zip"
        extract_dir = tmp_path / "extract"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "content")

        result = file_ops.extract_with_validation(
            str(zip_path), str(extract_dir), [""], []
        )

        assert result == []

    def test_generate_hash_for_extracted_files(self, tmp_path, monkeypatch):
        """Test hash generation for extracted files."""
        monkeypatch.setattr(
            platformdirs, "user_cache_dir", lambda *args, **kwargs: str(tmp_path)
        )
        file_ops = FileOperations()

        # Create test files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content1")
        file2.write_text("content2")

        files = [Path(file1), Path(file2)]
        result = file_ops.generate_hash_for_extracted_files(files)

        assert len(result) == 2
        assert str(file1) in result
        assert str(file2) in result

        # Check that hash files were created in cache
        hash_file1 = utils.get_hash_file_path(str(file1))
        hash_file2 = utils.get_hash_file_path(str(file2))
        assert os.path.exists(hash_file1)
        assert os.path.exists(hash_file2)

    def test_cleanup_file_success(self, tmp_path):
        """Test successful file cleanup."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = file_ops.cleanup_file(str(test_file))
        assert result is True
        assert not test_file.exists()

    def test_cleanup_file_not_exists(self):
        """Test cleanup of non-existent file."""
        file_ops = FileOperations()
        result = file_ops.cleanup_file("/non/existent/file.txt")
        assert result is True

    def test_ensure_directory_exists_success(self, tmp_path):
        """Test successful directory creation."""
        file_ops = FileOperations()
        test_dir = tmp_path / "new_dir"

        result = file_ops.ensure_directory_exists(str(test_dir))
        assert result is True
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_directory_exists_already_exists(self, tmp_path):
        """Test directory creation when it already exists."""
        file_ops = FileOperations()
        result = file_ops.ensure_directory_exists(str(tmp_path))
        assert result is True

    def test_get_file_size_exists(self, tmp_path):
        """Test getting file size for existing file."""
        file_ops = FileOperations()
        test_file = tmp_path / "test.txt"
        content = "test content"
        test_file.write_text(content)

        result = file_ops.get_file_size(str(test_file))
        assert result == len(content)

    def test_get_file_size_not_exists(self):
        """Test getting file size for non-existent file."""
        file_ops = FileOperations()
        result = file_ops.get_file_size("/non/existent/file.txt")
        assert result is None

    def test_compare_file_hashes_identical(self, tmp_path):
        """Test comparing identical files."""
        file_ops = FileOperations()
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        content = "test content"
        file1.write_text(content)
        file2.write_text(content)

        result = file_ops.compare_file_hashes(str(file1), str(file2))
        assert result is True

    def test_compare_file_hashes_different(self, tmp_path):
        """Test comparing different files."""
        file_ops = FileOperations()
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content1")
        file2.write_text("content2")

        result = file_ops.compare_file_hashes(str(file1), str(file2))
        assert result is False

    def test_compare_file_hashes_missing_file(self, tmp_path):
        """Test comparing when one file is missing."""
        file_ops = FileOperations()
        file1 = tmp_path / "file1.txt"
        file1.write_text("content")

        result = file_ops.compare_file_hashes(str(file1), "/non/existent/file.txt")
        assert result is False


class TestPrepareForRedownload:
    """Test _prepare_for_redownload function."""

    def test_prepare_for_redownload_all_files_exist(self, tmp_path, monkeypatch):
        """Test cleanup when all related files exist."""
        # Set up cache directory for hash files
        monkeypatch.setattr(
            platformdirs,
            "user_cache_dir",
            lambda *args, **kwargs: str(tmp_path / "cache"),
        )

        # Create main file
        main_file = tmp_path / "test_file.txt"
        main_file.write_text("content")

        # Create hash file
        hash_path = utils.get_hash_file_path(str(main_file))
        os.makedirs(os.path.dirname(hash_path), exist_ok=True)
        with open(hash_path, "w") as f:
            f.write("dummy_hash  test_file.txt\n")

        # Create legacy hash file
        legacy_hash_path = utils.get_legacy_hash_file_path(str(main_file))
        with open(legacy_hash_path, "w") as f:
            f.write("legacy_hash  test_file.txt\n")

        # Create temp files
        temp_file1 = tmp_path / "test_file.txt.tmp.abc123"
        temp_file2 = tmp_path / "test_file.txt.tmp.def456"
        temp_file1.write_text("temp1")
        temp_file2.write_text("temp2")

        # Run cleanup
        result = _prepare_for_redownload(str(main_file))

        # Verify success
        assert result is True

        # Verify all files are removed
        assert not main_file.exists()
        assert not os.path.exists(hash_path)
        assert not os.path.exists(legacy_hash_path)
        assert not temp_file1.exists()
        assert not temp_file2.exists()

    def test_prepare_for_redownload_partial_files_exist(self, tmp_path, monkeypatch):
        """Test cleanup when only some related files exist."""
        # Set up cache directory
        monkeypatch.setattr(
            platformdirs,
            "user_cache_dir",
            lambda *args, **kwargs: str(tmp_path / "cache"),
        )

        # Create only main file and hash file
        main_file = tmp_path / "test_file.txt"
        main_file.write_text("content")

        hash_path = utils.get_hash_file_path(str(main_file))
        os.makedirs(os.path.dirname(hash_path), exist_ok=True)
        with open(hash_path, "w") as f:
            f.write("dummy_hash  test_file.txt\n")

        # Legacy hash and temp files don't exist

        # Run cleanup
        result = _prepare_for_redownload(str(main_file))

        # Verify success
        assert result is True

        # Verify files are removed
        assert not main_file.exists()
        assert not os.path.exists(hash_path)

    def test_prepare_for_redownload_no_files_exist(self, tmp_path, monkeypatch):
        """Test cleanup when no related files exist."""
        # Set up cache directory
        monkeypatch.setattr(
            platformdirs,
            "user_cache_dir",
            lambda *args, **kwargs: str(tmp_path / "cache"),
        )

        # No files exist
        main_file = tmp_path / "nonexistent.txt"

        # Run cleanup
        result = _prepare_for_redownload(str(main_file))

        # Verify success (no error when files don't exist)
        assert result is True

    def test_prepare_for_redownload_os_error(self, tmp_path, monkeypatch):
        """Test cleanup when OSError occurs."""
        # Set up cache directory
        monkeypatch.setattr(
            platformdirs,
            "user_cache_dir",
            lambda *args, **kwargs: str(tmp_path / "cache"),
        )

        # Create main file
        main_file = tmp_path / "test_file.txt"
        main_file.write_text("content")

        # Mock os.remove to raise OSError
        with monkeypatch.context() as m:
            m.setattr(
                "os.remove",
                lambda path: (_ for _ in ()).throw(OSError("Permission denied")),
            )
            result = _prepare_for_redownload(str(main_file))

        # Verify failure
        assert result is False

        # File should still exist since cleanup failed
        assert main_file.exists()


class TestSafeExtractPath:
    """Test safe_extract_path function."""

    def test_safe_path(self, tmp_path):
        """Test with safe extraction path."""
        result = safe_extract_path(str(tmp_path), "safe/file.txt")
        expected = os.path.join(str(tmp_path), "safe", "file.txt")
        assert result == os.path.realpath(expected)

    def test_path_traversal_attack(self, tmp_path):
        """Test protection against path traversal."""
        with pytest.raises(ValueError, match="Unsafe extraction path"):
            safe_extract_path(str(tmp_path), "../../../etc/passwd")

    def test_absolute_path_attack(self, tmp_path):
        """Test protection against absolute path."""
        with pytest.raises(ValueError, match="Unsafe extraction path"):
            safe_extract_path(str(tmp_path), "/etc/passwd")

    def test_null_byte_attack(self, tmp_path):
        """
        Ensure safe_extract_path propagates a ValueError when the requested extraction path contains a null byte.

        Asserts that calling safe_extract_path with a path containing a null byte raises ValueError.
        """
        # os.path.realpath raises ValueError for null bytes before our check
        with pytest.raises(ValueError):
            safe_extract_path(str(tmp_path), "safe/file.txt\x00evil.txt")
