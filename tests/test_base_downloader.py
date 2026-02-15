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
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from fetchtastic.download.base import BaseDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Asset
from tests.async_test_utils import make_async_iter

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


# Concrete implementation of BaseDownloader for testing
class ConcreteDownloader(BaseDownloader):
    """Concrete implementation of BaseDownloader for testing purposes."""

    def check_extraction_needed(self, file_path, patterns):
        """
        Determine whether extraction is required for a downloaded archive given its path and extraction patterns.

        Parameters:
            file_path (str | pathlib.Path): Path to the downloaded file or archive.
            patterns (Iterable[str] | None): Iterable of glob-style patterns that specify which files to extract; may be None to indicate no pattern filtering.

        Returns:
            bool: `True` if the file should be extracted according to the provided patterns, `False` otherwise.
        """
        return True

    def validate_extraction_patterns(self, patterns):
        """
        Validate extraction filename patterns for archive extraction.

        Parameters:
            patterns (Iterable[str] | None): Glob-style include/exclude patterns to validate, or None to indicate no patterns.

        Returns:
            bool: `True` if the provided patterns are valid and usable for extraction, `False` otherwise.
        """
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

    def test_get_max_concurrent_default(self):
        """Test default max concurrent downloads."""
        config = {}
        downloader = ConcreteDownloader(config)

        assert downloader._get_max_concurrent() == 5

    def test_get_max_concurrent_invalid_fallback(self):
        """Invalid max concurrent values should fall back to default."""
        config = {"MAX_CONCURRENT_DOWNLOADS": "not-a-number"}
        downloader = ConcreteDownloader(config)

        assert downloader._get_max_concurrent() == 5

    def test_get_max_concurrent_clamped_minimum(self):
        """Values <= 0 should be clamped to avoid invalid semaphores."""
        config = {"MAX_CONCURRENT_DOWNLOADS": 0}
        downloader = ConcreteDownloader(config)

        assert downloader._get_max_concurrent() == 1


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

        with (
            patch.object(downloader, "_get_selected_patterns") as mock_patterns,
            patch.object(downloader, "_get_exclude_patterns") as mock_exclude,
        ):
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
            with (
                patch.object(downloader, "get_target_path_for_release") as mock_target,
                patch.object(downloader.file_operations, "get_file_size") as mock_size,
                patch.object(downloader, "verify") as mock_verify,
            ):
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
            with (
                patch.object(downloader, "get_target_path_for_release") as mock_target,
                patch.object(downloader.file_operations, "get_file_size") as mock_size,
            ):
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

        with (
            patch.object(downloader, "get_existing_file_path") as mock_existing,
            patch.object(downloader.file_operations, "get_file_size") as mock_size,
        ):
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


# =============================================================================
# Async Download Tests
# =============================================================================


@pytest.mark.asyncio
class TestBaseDownloaderAsyncDownload:
    """Test async_download method."""

    async def test_async_download_success(self, tmp_path, mocker):
        """Test successful async download."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        # Mock content iteration
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=make_async_iter([b"test content"])
        )
        mock_response.content = mock_content

        # Mock aiohttp session
        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            # Mock aiofiles
            mock_file = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock()

                # Mock _async_verify_file to return False (no existing file)
                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )
                mocker.patch.object(downloader, "_async_save_hash", AsyncMock())

                # Mock Path.replace to avoid actual filesystem operation
                mocker.patch.object(Path, "replace", return_value=None)

                target = tmp_path / "test.bin"
                result = await downloader.async_download(
                    "https://example.com/file.bin", target
                )

        assert result is True

    async def test_async_download_reuses_shared_session(self, tmp_path, mocker):
        """Multiple async downloads should reuse one session per downloader instance."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "4"}
        mock_response.raise_for_status = Mock()
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(
            side_effect=lambda *_args, **_kwargs: make_async_iter([b"test"])
        )
        mock_response.content = mock_content
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch(
                "aiohttp.ClientSession", return_value=mock_session
            ) as mock_session_cls,
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_file = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )
                mocker.patch.object(downloader, "_async_save_hash", AsyncMock())
                mocker.patch.object(Path, "replace", return_value=None)

                result1 = await downloader.async_download(
                    "https://example.com/file1.bin", tmp_path / "file1.bin"
                )
                result2 = await downloader.async_download(
                    "https://example.com/file2.bin", tmp_path / "file2.bin"
                )

        assert result1 is True
        assert result2 is True
        assert mock_session_cls.call_count == 1

    async def test_async_download_skips_existing_valid_file(self, tmp_path, mocker):
        """Test that async download skips existing valid file."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        # Create existing file
        existing_file = tmp_path / "existing.bin"
        existing_file.write_bytes(b"existing content")

        # Mock verification to return True
        mocker.patch.object(
            downloader, "_async_verify_file", AsyncMock(return_value=True)
        )

        result = await downloader.async_download(
            "https://example.com/file.bin", existing_file
        )

        assert result is True

    async def test_async_download_creates_parent_directory(self, tmp_path, mocker):
        """Test that async download creates parent directories."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mock_file = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock()

                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )
                mocker.patch.object(downloader, "_async_save_hash", AsyncMock())

                # Mock Path.replace to avoid actual filesystem operation
                mocker.patch.object(Path, "replace", return_value=None)

                target = tmp_path / "subdir" / "nested" / "test.bin"
                result = await downloader.async_download(
                    "https://example.com/file.bin", target
                )

        assert result is True
        assert target.parent.exists()

    async def test_async_download_with_progress_callback(self, tmp_path, mocker):
        """Test async download with progress callback."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        # Create an async iterator for chunks
        async def chunk_iterator(*_args, **_kwargs):
            """
            Yield a sequence of byte chunks suitable for testing async stream consumers.

            Yields:
                bytes: Sequential data chunks (`b"chunk1"`, `b"chunk2"`) to simulate streamed payloads.
            """
            for chunk in [b"chunk1", b"chunk2"]:
                yield chunk

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=chunk_iterator())
        mock_response.content = mock_content

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        callback_calls = []

        async def progress(downloaded, total, filename):
            """
            Append reported download progress values to the enclosing test's `callback_calls` list.

            Parameters:
                downloaded (int): Number of bytes downloaded so far.
                total (int | None): Total number of bytes expected, or None if unknown.
                filename (str): Name of the file being downloaded.
            """
            callback_calls.append((downloaded, total, filename))

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mock_file = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock()

                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )
                mocker.patch.object(downloader, "_async_save_hash", AsyncMock())

                # Mock Path.replace to avoid actual filesystem operation
                mocker.patch.object(Path, "replace", return_value=None)

                target = tmp_path / "test.bin"
                result = await downloader.async_download(
                    "https://example.com/file.bin",
                    target,
                    progress_callback=progress,
                )

        assert result is True
        assert len(callback_calls) == 2  # One per chunk

    async def test_async_download_progress_callback_exception_handled(
        self, tmp_path, mocker
    ):
        """Test that progress callback exceptions are handled gracefully."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        def bad_callback(_downloaded, _total, _filename):
            """
            Progress callback that always raises a ValueError.

            Parameters:
                _downloaded (int): Number of bytes or units downloaded so far.
                _total (int | None): Total number of bytes or units expected, or None if unknown.
                _filename (str): Name of the file being downloaded.

            Raises:
                ValueError: Always raised with the message "Callback error".
            """
            raise ValueError("Callback error")

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mock_file = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock()

                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )
                mocker.patch.object(downloader, "_async_save_hash", AsyncMock())

                # Mock Path.replace to avoid actual filesystem operation
                mocker.patch.object(Path, "replace", return_value=None)

                target = tmp_path / "test.bin"
                # Should not raise
                result = await downloader.async_download(
                    "https://example.com/file.bin",
                    target,
                    progress_callback=bad_callback,
                )

        assert result is True

    async def test_async_download_fallback_when_async_libs_unavailable(self, tmp_path):
        """Test fallback to sync download when async libs not available."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        target = tmp_path / "test.bin"

        # Make aiohttp import fail by removing from sys.modules
        saved_aiohttp = sys.modules.pop("aiohttp", None)
        saved_aiofiles = sys.modules.pop("aiofiles", None)
        try:
            with patch.dict("sys.modules", {"aiohttp": None, "aiofiles": None}):
                with patch.object(
                    downloader, "download", return_value=True
                ) as mock_sync_download:
                    result = await downloader.async_download(
                        "https://example.com/file.bin", target
                    )
        finally:
            if saved_aiohttp is not None:
                sys.modules["aiohttp"] = saved_aiohttp
            if saved_aiofiles is not None:
                sys.modules["aiofiles"] = saved_aiofiles

        assert result is True
        mock_sync_download.assert_called_once()

    async def test_async_download_client_error(self, tmp_path, mocker):
        """Test async download handles client errors."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)
        from aiohttp import ClientError

        from fetchtastic.download.async_client import AsyncDownloadError

        # Create a mock response that raises ClientError when entering context
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=ClientError("Connection failed")
        )
        mock_response.__aexit__ = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mocker.patch.object(
                downloader, "_async_verify_file", AsyncMock(return_value=False)
            )

            target = tmp_path / "test.bin"
            # Errors during download should raise AsyncDownloadError
            with pytest.raises(AsyncDownloadError) as exc_info:
                await downloader.async_download("https://example.com/file.bin", target)
            assert exc_info.value.is_retryable is True

    async def test_async_download_os_error(self, tmp_path, mocker):
        """Test async download handles OS errors."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)
        from fetchtastic.download.async_client import AsyncDownloadError

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            # Make file write fail
            mock_file = AsyncMock()
            mock_file.write = AsyncMock(side_effect=OSError("Disk full"))

            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock()

                mocker.patch.object(
                    downloader, "_async_verify_file", AsyncMock(return_value=False)
                )

                target = tmp_path / "test.bin"
                # OSError should raise AsyncDownloadError with is_retryable=False
                with pytest.raises(AsyncDownloadError) as exc_info:
                    await downloader.async_download(
                        "https://example.com/file.bin", target
                    )
                assert exc_info.value.is_retryable is False

    async def test_async_download_unexpected_exception(self, tmp_path, mocker):
        """Test async download handles unexpected exceptions."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)
        from fetchtastic.download.async_client import AsyncDownloadError

        # Create a mock that raises RuntimeError when entering the response context
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )
        mock_response.__aexit__ = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mocker.patch.object(
                downloader, "_async_verify_file", AsyncMock(return_value=False)
            )

            target = tmp_path / "test.bin"
            # Unexpected exceptions should raise AsyncDownloadError
            with pytest.raises(AsyncDownloadError):
                await downloader.async_download("https://example.com/file.bin", target)


# =============================================================================
# Async Verify File Tests
# =============================================================================


@pytest.mark.asyncio
class TestBaseDownloaderAsyncVerifyFile:
    """Test _async_verify_file method."""

    async def test_verify_regular_file_success(self, tmp_path, mocker):
        """Test verification of regular file that passes."""
        config = {}
        downloader = ConcreteDownloader(config)

        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test content")

        mocker.patch("fetchtastic.download.base.load_file_hash", return_value="abc123")
        mocker.patch("fetchtastic.utils.verify_file_integrity", return_value=True)

        result = await downloader._async_verify_file(test_file)

        assert result is True

    async def test_verify_zip_file_valid(self, tmp_path, mocker):
        """Test verification of valid zip file."""
        config = {}
        downloader = ConcreteDownloader(config)

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file.txt", "content")

        mocker.patch("fetchtastic.download.base.load_file_hash", return_value="abc123")
        mocker.patch("fetchtastic.utils.verify_file_integrity", return_value=True)

        result = await downloader._async_verify_file(zip_path)

        assert result is True

    async def test_verify_zip_file_corrupted(self, tmp_path):
        """Test verification of corrupted zip file."""
        config = {}
        downloader = ConcreteDownloader(config)

        # Create corrupted zip
        zip_path = tmp_path / "corrupt.zip"
        zip_path.write_bytes(b"not a valid zip file")

        result = await downloader._async_verify_file(zip_path)

        assert result is False

    async def test_verify_file_os_error(self, tmp_path, mocker):
        """Test verification handles OS errors."""
        config = {}
        downloader = ConcreteDownloader(config)

        nonexistent = tmp_path / "nonexistent.bin"

        mocker.patch(
            "fetchtastic.utils.verify_file_integrity",
            side_effect=OSError("File not found"),
        )

        result = await downloader._async_verify_file(nonexistent)

        assert result is False


# =============================================================================
# Async Save Hash Tests
# =============================================================================


@pytest.mark.asyncio
class TestBaseDownloaderAsyncSaveHash:
    """Test _async_save_hash method."""

    async def test_save_hash_success(self, tmp_path, mocker):
        """Test saving file hash successfully."""
        config = {}
        downloader = ConcreteDownloader(config)

        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test content")

        mock_calculate = mocker.patch(
            "fetchtastic.utils.calculate_sha256", return_value="abc123hash"
        )
        mock_save = mocker.patch("fetchtastic.utils.save_file_hash")

        await downloader._async_save_hash(test_file)

        mock_calculate.assert_called_once_with(str(test_file))
        mock_save.assert_called_once_with(str(test_file), "abc123hash")

    async def test_save_hash_no_hash_returned(self, tmp_path, mocker):
        """Test saving hash when calculate_sha256 returns None."""
        config = {}
        downloader = ConcreteDownloader(config)

        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test content")

        mock_calculate = mocker.patch(
            "fetchtastic.utils.calculate_sha256", return_value=None
        )
        mock_save = mocker.patch("fetchtastic.utils.save_file_hash")

        await downloader._async_save_hash(test_file)

        mock_calculate.assert_called_once()
        mock_save.assert_not_called()


# =============================================================================
# Async Download With Retry Tests
# =============================================================================


@pytest.mark.asyncio
class TestBaseDownloaderAsyncDownloadWithRetry:
    """Test async_download_with_retry method."""

    async def test_retry_success_first_attempt(self, tmp_path, mocker):
        """Test successful download on first attempt."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_download = mocker.patch.object(
            downloader, "async_download", AsyncMock(return_value=True)
        )

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin", target
        )

        assert result is True
        mock_download.assert_called_once()

    async def test_retry_success_after_failure(self, tmp_path, mocker):
        """Test successful download after failure."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_download = mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=[False, True]),
        )

        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin",
            target,
            max_retries=3,
            retry_delay=0.1,
        )

        assert result is True
        assert mock_download.call_count == 2

    async def test_retry_exhausted(self, tmp_path, mocker):
        """Test raising AsyncDownloadError after exhausting all retries."""
        from fetchtastic.download.async_client import AsyncDownloadError

        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_download = mocker.patch.object(
            downloader, "async_download", AsyncMock(return_value=False)
        )

        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"
        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                target,
                max_retries=2,
                retry_delay=0.1,
            )

        assert "Download failed after 3/3 attempts" in exc_info.value.message
        assert mock_download.call_count == 3  # Initial + 2 retries

    async def test_retry_exception_handling(self, tmp_path, mocker):
        """Test handling exceptions during retry."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_download = mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=[Exception("Network error"), True]),
        )

        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin",
            target,
            max_retries=3,
            retry_delay=0.1,
        )

        assert result is True
        assert mock_download.call_count == 2

    async def test_retry_exponential_backoff(self, tmp_path, mocker):
        """Test exponential backoff timing."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=[False, False, True]),
        )

        sleep_calls = []

        async def track_sleep(duration):
            """
            Record a sleep duration for later inspection.

            Appends the given duration, in seconds, to the shared `sleep_calls` list.

            Parameters:
                duration (float): Sleep time in seconds to record.
            """
            sleep_calls.append(duration)

        mocker.patch("asyncio.sleep", track_sleep)

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin",
            target,
            max_retries=3,
            retry_delay=1.0,
            backoff_factor=2.0,
        )

        assert result is True
        # Verify exponential backoff: 1.0, 2.0
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == 2.0

    async def test_retry_with_progress_callback(self, tmp_path, mocker):
        """Test retry with progress callback passed through."""
        config = {"DOWNLOAD_DIR": str(tmp_path)}
        downloader = ConcreteDownloader(config)

        mock_download = mocker.patch.object(
            downloader, "async_download", AsyncMock(return_value=True)
        )

        async def progress(downloaded, total, filename):
            """
            Report progress of an ongoing file download.

            Parameters:
                downloaded (int): Number of bytes downloaded so far.
                total (int | None): Total number of bytes expected, or None if unknown.
                filename (str): Name or path of the file being downloaded.
            """
            pass

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin",
            target,
            progress_callback=progress,
        )

        assert result is True
        # Verify progress callback was passed
        assert mock_download.call_args[1]["progress_callback"] == progress
