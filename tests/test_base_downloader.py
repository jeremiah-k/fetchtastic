"""
Comprehensive tests for BaseDownloader functionality.

Tests the base downloader implementation including:
- Initialization and configuration
- Download operations with retry logic
- Verification and integrity checks
- Archive extraction with pattern matching
- Path sanitization and security
- Version cleanup and management
- Error handling and edge cases
"""

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.base import BaseDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Asset


# Concrete implementation of BaseDownloader for testing
class ConcreteDownloader(BaseDownloader):
    """Concrete implementation of BaseDownloader for testing purposes."""

    def check_extraction_needed(self, file_path, patterns):
        """Stub implementation."""
        return True

    def validate_extraction_patterns(self, patterns):
        """Stub implementation."""
        return True


class TestBaseDownloaderInitialization:
    """Test BaseDownloader initialization."""

    def test_init_default_parameters(self):
        """Test initialization with default parameters."""
        config = {"DOWNLOAD_DIR": "/tmp/meshtastic", "VERSIONS_TO_KEEP": 5}
        downloader = ConcreteDownloader(config)

        assert downloader.config == config
        assert downloader.download_dir == "/tmp/meshtastic"
        assert downloader.versions_to_keep == 5
        assert downloader.version_manager is not None
        assert downloader.cache_manager is not None
        assert downloader.file_operations is not None

    def test_init_with_custom_cache_manager(self):
        """Test initialization with custom cache manager."""
        config = {"DOWNLOAD_DIR": "/tmp/test"}
        cache_manager = CacheManager()

        downloader = ConcreteDownloader(config, cache_manager=cache_manager)

        assert downloader.cache_manager is cache_manager

    def test_get_download_dir_from_config(self):
        """Test getting download directory from config."""
        config = {"DOWNLOAD_DIR": "/custom/path"}
        downloader = ConcreteDownloader(config)

        assert downloader.get_download_dir() == "/custom/path"

    def test_get_download_dir_default(self):
        """Test default download directory."""
        config = {}
        downloader = ConcreteDownloader(config)

        download_dir = downloader.get_download_dir()
        assert "meshtastic" in download_dir

    def test_get_versions_to_keep_from_config(self):
        """Test getting versions to keep from config."""
        config = {"VERSIONS_TO_KEEP": 10}
        downloader = ConcreteDownloader(config)

        assert downloader._get_versions_to_keep() == 10

    def test_get_versions_to_keep_default(self):
        """Test default versions to keep."""
        config = {}
        downloader = ConcreteDownloader(config)

        assert downloader._get_versions_to_keep() == 5


class TestBaseDownloaderDownload:
    """Test download functionality."""

    def test_download_success(self):
        """Test successful file download."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "test_file.txt"

            with patch("fetchtastic.utils.download_file_with_retry") as mock_download:
                mock_download.return_value = True

                result = downloader.download(
                    "https://example.com/file.txt", target_path
                )

                assert result is True
                mock_download.assert_called_once()

    def test_download_failure(self):
        """Test download failure."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "test_file.txt"

            with patch("fetchtastic.utils.download_file_with_retry") as mock_download:
                mock_download.return_value = False

                result = downloader.download(
                    "https://example.com/file.txt", target_path
                )

                assert result is False

    def test_download_creates_parent_directory(self):
        """Test that download creates parent directory if needed."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "subdir" / "test_file.txt"

            with patch("fetchtastic.utils.download_file_with_retry") as mock_download:
                mock_download.return_value = True

                downloader.download("https://example.com/file.txt", target_path)

                assert target_path.parent.exists()

    def test_download_handles_exception(self):
        """Test download handles exceptions gracefully."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "test_file.txt"

            with patch("fetchtastic.utils.download_file_with_retry") as mock_download:
                mock_download.side_effect = OSError("Disk full")

                result = downloader.download(
                    "https://example.com/file.txt", target_path
                )

                assert result is False


class TestBaseDownloaderVerify:
    """Test file verification functionality."""

    def test_verify_with_hash(self):
        """Test verification with expected hash."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            tmp_file.write("test content")
            tmp_path = tmp_file.name

        try:
            with patch.object(
                downloader.file_operations, "verify_file_hash"
            ) as mock_verify:
                mock_verify.return_value = True

                result = downloader.verify(tmp_path, expected_hash="abc123")

                assert result is True
                mock_verify.assert_called_once_with(tmp_path, "abc123")
        finally:
            os.unlink(tmp_path)

    def test_verify_without_hash(self):
        """Test verification without expected hash."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            tmp_file.write("test content")
            tmp_path = tmp_file.name

        try:
            with patch("fetchtastic.utils.verify_file_integrity") as mock_verify:
                mock_verify.return_value = True

                result = downloader.verify(tmp_path)

                assert result is True
                mock_verify.assert_called_once_with(tmp_path)
        finally:
            os.unlink(tmp_path)


class TestBaseDownloaderExtract:
    """Test archive extraction functionality."""

    def test_extract_with_patterns(self):
        """Test extracting files with patterns."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "test.zip"

            # Create a test zip file
            with zipfile.ZipFile(archive_path, "w") as zf:
                zf.writestr("file1.bin", "content1")
                zf.writestr("file2.txt", "content2")

            patterns = ["*.bin"]

            with patch.object(
                downloader.file_operations, "extract_archive"
            ) as mock_extract:
                mock_extract.return_value = [os.path.join(temp_dir, "file1.bin")]

                result = downloader.extract(archive_path, patterns)

                assert len(result) == 1
                assert isinstance(result[0], Path)

    def test_extract_with_exclude_patterns(self):
        """Test extracting files with exclude patterns."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "test.zip"

            with zipfile.ZipFile(archive_path, "w") as zf:
                zf.writestr("file1.bin", "content1")
                zf.writestr("file2.hex", "content2")

            patterns = ["*"]
            exclude_patterns = ["*.hex"]

            with patch.object(
                downloader.file_operations, "extract_archive"
            ) as mock_extract:
                mock_extract.return_value = [os.path.join(temp_dir, "file1.bin")]

                downloader.extract(archive_path, patterns, exclude_patterns)

                mock_extract.assert_called_once_with(
                    str(archive_path),
                    temp_dir,
                    patterns,
                    exclude_patterns,
                )


class TestBaseDownloaderPathSanitization:
    """Test path sanitization and security."""

    def test_sanitize_required_valid_path(self):
        """Test sanitizing valid path component."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch("fetchtastic.download.files._sanitize_path_component") as mock_san:
            mock_san.return_value = "safe_name"

            result = downloader._sanitize_required("safe_name", "test label")

            assert result == "safe_name"

    def test_sanitize_required_invalid_path(self):
        """Test sanitizing invalid path component raises error."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch("fetchtastic.download.files._sanitize_path_component") as mock_san:
            mock_san.return_value = None

            with pytest.raises(ValueError) as exc_info:
                downloader._sanitize_required("../../../etc/passwd", "test label")

            assert "Unsafe" in str(exc_info.value)
            assert "path traversal" in str(exc_info.value).lower()


class TestBaseDownloaderTargetPath:
    """Test target path generation."""

    def test_get_target_path_for_release(self):
        """Test generating target path for release."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {"DOWNLOAD_DIR": temp_dir}
            downloader = ConcreteDownloader(config)

            with patch.object(downloader, "_sanitize_required") as mock_sanitize:
                mock_sanitize.side_effect = lambda x, label: x

                result = downloader.get_target_path_for_release(
                    "v2.5.0", "firmware.zip"
                )

                expected = f"{temp_dir}/v2.5.0/firmware.zip"
                assert result == expected

    def test_get_target_path_creates_directory(self):
        """Test that getting target path creates version directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {"DOWNLOAD_DIR": temp_dir}
            downloader = ConcreteDownloader(config)

            with patch.object(downloader, "_sanitize_required") as mock_sanitize:
                mock_sanitize.side_effect = lambda x, label: x

                target = downloader.get_target_path_for_release(
                    "v2.5.0", "firmware.zip"
                )

                assert Path(target).parent.exists()


class TestBaseDownloaderShouldDownload:
    """Test should_download_release logic."""

    def test_should_download_matches_pattern(self):
        """Test should download when asset matches pattern."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(downloader, "_get_selected_patterns") as mock_patterns:
            mock_patterns.return_value = ["firmware-"]

            result = downloader.should_download_release(
                "v2.5.0", "firmware-rak4631.bin"
            )

            assert result is True

    def test_should_download_no_match(self):
        """Test should not download when asset doesn't match."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(downloader, "_get_selected_patterns") as mock_patterns:
            mock_patterns.return_value = ["firmware-"]

            result = downloader.should_download_release("v2.5.0", "debug.hex")

            assert result is False

    def test_should_download_exclude_pattern(self):
        """Test should not download when asset matches exclude pattern."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(
            downloader, "_get_selected_patterns"
        ) as mock_patterns, patch.object(
            downloader, "_get_exclude_patterns"
        ) as mock_exclude:
            mock_patterns.return_value = ["*"]
            mock_exclude.return_value = ["*.hex"]

            result = downloader.should_download_release("v2.5.0", "firmware.hex")

            assert result is False

    def test_should_download_no_patterns(self):
        """Test should download when no patterns specified."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(downloader, "_get_selected_patterns") as mock_patterns:
            mock_patterns.return_value = []

            result = downloader.should_download_release("v2.5.0", "any_file.bin")

            assert result is True


class TestBaseDownloaderPatternRetrieval:
    """Test pattern retrieval from config."""

    def test_get_selected_patterns_from_config(self):
        """Test getting selected patterns from config."""
        config = {"SELECTED_PATTERNS": ["pattern1", "pattern2"]}
        downloader = ConcreteDownloader(config)

        result = downloader._get_selected_patterns()

        assert result == ["pattern1", "pattern2"]

    def test_get_selected_patterns_legacy_keys(self):
        """Test getting selected patterns from legacy config keys."""
        config = {"SELECTED_FIRMWARE_ASSETS": ["firmware-"]}
        downloader = ConcreteDownloader(config)

        result = downloader._get_selected_patterns()

        assert result == ["firmware-"]

    def test_get_selected_patterns_single_string(self):
        """Test converting single string pattern to list."""
        config = {"SELECTED_PATTERNS": "single_pattern"}
        downloader = ConcreteDownloader(config)

        result = downloader._get_selected_patterns()

        assert result == ["single_pattern"]

    def test_get_exclude_patterns_from_config(self):
        """Test getting exclude patterns from config."""
        config = {"EXCLUDE_PATTERNS": ["*.hex", "*.debug"]}
        downloader = ConcreteDownloader(config)

        result = downloader._get_exclude_patterns()

        assert result == ["*.hex", "*.debug"]

    def test_get_exclude_patterns_default(self):
        """Test default exclude patterns."""
        config = {}
        downloader = ConcreteDownloader(config)

        result = downloader._get_exclude_patterns()

        assert result == []


class TestBaseDownloaderDownloadResult:
    """Test download result creation."""

    def test_create_download_result_success(self):
        """Test creating successful download result."""
        config = {}
        downloader = ConcreteDownloader(config)

        result = downloader.create_download_result(
            success=True,
            release_tag="v2.5.0",
            file_path="/downloads/v2.5.0/firmware.bin",
            download_url="https://example.com/firmware.bin",
            file_size=1024,
            file_type="firmware",
        )

        assert result.success is True
        assert result.release_tag == "v2.5.0"
        assert result.file_path == Path("/downloads/v2.5.0/firmware.bin")
        assert result.download_url == "https://example.com/firmware.bin"
        assert result.file_size == 1024
        assert result.file_type == "firmware"

    def test_create_download_result_failure(self):
        """Test creating failed download result."""
        config = {}
        downloader = ConcreteDownloader(config)

        result = downloader.create_download_result(
            success=False,
            release_tag="v2.5.0",
            file_path="/downloads/v2.5.0/firmware.bin",
            error_message="Download failed",
            error_type="NetworkError",
            is_retryable=True,
            http_status_code=503,
        )

        assert result.success is False
        assert result.error_message == "Download failed"
        assert result.error_type == "NetworkError"
        assert result.is_retryable is True
        assert result.http_status_code == 503

    def test_create_download_result_with_extracted_files(self):
        """Test creating result with extracted files."""
        config = {}
        downloader = ConcreteDownloader(config)

        extracted = [Path("/downloads/v2.5.0/file1.bin")]
        result = downloader.create_download_result(
            success=True,
            release_tag="v2.5.0",
            file_path="/downloads/v2.5.0/firmware.zip",
            extracted_files=extracted,
        )

        assert result.extracted_files == extracted


class TestBaseDownloaderExistingFile:
    """Test existing file operations."""

    def test_get_existing_file_path_exists(self):
        """Test getting path of existing file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {"DOWNLOAD_DIR": temp_dir}
            downloader = ConcreteDownloader(config)

            # Create test file
            release_dir = Path(temp_dir) / "v2.5.0"
            release_dir.mkdir()
            test_file = release_dir / "firmware.bin"
            test_file.write_text("test")

            with patch.object(downloader, "_sanitize_required") as mock_sanitize:
                mock_sanitize.side_effect = lambda x, label: x

                result = downloader.get_existing_file_path("v2.5.0", "firmware.bin")

                assert result == str(test_file)

    def test_get_existing_file_path_not_exists(self):
        """Test getting path when file doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {"DOWNLOAD_DIR": temp_dir}
            downloader = ConcreteDownloader(config)

            with patch.object(downloader, "_sanitize_required") as mock_sanitize:
                mock_sanitize.side_effect = lambda x, label: x

                result = downloader.get_existing_file_path("v2.5.0", "nonexistent.bin")

                assert result is None

    def test_cleanup_file_success(self):
        """Test successful file cleanup."""
        config = {}
        downloader = ConcreteDownloader(config)

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            with patch.object(
                downloader.file_operations, "cleanup_file"
            ) as mock_cleanup:
                mock_cleanup.return_value = True

                result = downloader.cleanup_file(tmp_path)

                assert result is True
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestBaseDownloaderAssetChecks:
    """Test asset integrity and completion checks."""

    def test_is_asset_complete_success(self):
        """Test checking if asset download is complete."""
        config = {}
        downloader = ConcreteDownloader(config)

        asset = Mock(spec=Asset)
        asset.name = "firmware.bin"
        asset.size = 1024

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(b"x" * 1024)
            tmp_path = tmp_file.name

        try:
            with patch.object(
                downloader, "get_target_path_for_release"
            ) as mock_target, patch.object(
                downloader.file_operations, "get_file_size"
            ) as mock_size, patch.object(
                downloader, "verify"
            ) as mock_verify:
                mock_target.return_value = tmp_path
                mock_size.return_value = 1024
                mock_verify.return_value = True

                result = downloader.is_asset_complete("v2.5.0", asset)

                assert result is True
        finally:
            os.unlink(tmp_path)

    def test_is_asset_complete_size_mismatch(self):
        """Test asset completion check with size mismatch."""
        config = {}
        downloader = ConcreteDownloader(config)

        asset = Mock(spec=Asset)
        asset.name = "firmware.bin"
        asset.size = 2048  # Different from actual

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(b"x" * 1024)
            tmp_path = tmp_file.name

        try:
            with patch.object(
                downloader, "get_target_path_for_release"
            ) as mock_target, patch.object(
                downloader.file_operations, "get_file_size"
            ) as mock_size:
                mock_target.return_value = tmp_path
                mock_size.return_value = 1024

                result = downloader.is_asset_complete("v2.5.0", asset)

                assert result is False
        finally:
            os.unlink(tmp_path)

    def test_needs_download_file_missing(self):
        """Test needs_download when file is missing."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(downloader, "get_existing_file_path") as mock_existing:
            mock_existing.return_value = None

            result = downloader.needs_download("v2.5.0", "firmware.bin", 1024)

            assert result is True

    def test_needs_download_size_mismatch(self):
        """Test needs_download when file size doesn't match."""
        config = {}
        downloader = ConcreteDownloader(config)

        with patch.object(
            downloader, "get_existing_file_path"
        ) as mock_existing, patch.object(
            downloader.file_operations, "get_file_size"
        ) as mock_size:
            mock_existing.return_value = "/path/to/file"
            mock_size.return_value = 512  # Different from expected

            result = downloader.needs_download("v2.5.0", "firmware.bin", 1024)

            assert result is True


class TestBaseDownloaderManagers:
    """Test manager getter methods."""

    def test_get_version_manager(self):
        """Test getting version manager."""
        config = {}
        downloader = ConcreteDownloader(config)

        manager = downloader.get_version_manager()

        assert manager is not None
        assert manager is downloader.version_manager

    def test_get_cache_manager(self):
        """Test getting cache manager."""
        config = {}
        downloader = ConcreteDownloader(config)

        manager = downloader.get_cache_manager()

        assert manager is not None
        assert manager is downloader.cache_manager
