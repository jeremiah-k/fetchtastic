"""
Comprehensive tests for async_downloader.py module.

Tests the async downloader mixin functionality including:
- AsyncDownloaderMixin configuration getters (lines 37-39, 85-116)
- Async download operations (lines 118-206)
- Progress callback handling (lines 208-229)
- File verification (lines 231-294)
- Retry logic (lines 296-343)
- Release download operations (lines 345-431)
- AsyncDownloaderBase class (lines 434-458)
- download_with_progress utility function (lines 461-492)
"""

import os
import zipfile
from pathlib import Path
from typing import Any, Dict, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from fetchtastic.constants import (
    DEFAULT_CONNECT_RETRIES,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_UNKNOWN,
)
from fetchtastic.download.async_client import AsyncDownloadError
from fetchtastic.download.async_downloader import (
    AsyncDownloaderBase,
    AsyncDownloaderMixin,
    download_with_progress,
)
from fetchtastic.download.interfaces import Asset, DownloadResult

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads, pytest.mark.asyncio]


# Helper to create async iterator from list
async def _make_async_iter(items):
    for item in items:
        yield item


# =============================================================================
# Concrete Implementation for Testing
# =============================================================================


class ConcreteAsyncDownloader(AsyncDownloaderMixin):
    """Concrete implementation for testing the mixin."""

    def __init__(self, config=None):
        self.config = config or {}
        self.download_dir = self.config.get(
            "DOWNLOAD_DIR", os.path.expanduser("~/meshtastic")
        )


# =============================================================================
# Config Getter Tests (lines 91-116)
# =============================================================================


class TestConfigGetters:
    """Test configuration getter methods."""

    def test_get_max_concurrent_default(self):
        """Test default max concurrent downloads."""
        downloader = ConcreteAsyncDownloader(config={})
        assert downloader._get_max_concurrent() == 5

    def test_get_max_concurrent_from_config(self):
        """Test max concurrent from config."""
        downloader = ConcreteAsyncDownloader(config={"MAX_CONCURRENT_DOWNLOADS": 10})
        assert downloader._get_max_concurrent() == 10

    def test_get_max_concurrent_invalid_fallback(self):
        """Invalid max concurrent value should fall back to default."""
        downloader = ConcreteAsyncDownloader(config={"MAX_CONCURRENT_DOWNLOADS": "bad"})
        assert downloader._get_max_concurrent() == 5

    def test_get_max_concurrent_clamped_minimum(self):
        """Max concurrent values <= 0 should be clamped to 1."""
        downloader = ConcreteAsyncDownloader(config={"MAX_CONCURRENT_DOWNLOADS": 0})
        assert downloader._get_max_concurrent() == 1

    def test_get_max_retries_default(self):
        """Test default max retries."""
        downloader = ConcreteAsyncDownloader(config={})
        assert downloader._get_max_retries() == 5  # DEFAULT_CONNECT_RETRIES

    def test_get_max_retries_from_config(self):
        """Test max retries from config."""
        downloader = ConcreteAsyncDownloader(config={"MAX_DOWNLOAD_RETRIES": 3})
        assert downloader._get_max_retries() == 3

    def test_get_max_retries_invalid_fallback(self):
        """Invalid max retries should fall back to default."""
        downloader = ConcreteAsyncDownloader(config={"MAX_DOWNLOAD_RETRIES": "oops"})
        assert downloader._get_max_retries() == DEFAULT_CONNECT_RETRIES

    def test_get_max_retries_clamped_minimum(self):
        """Negative max retries should be clamped to zero."""
        downloader = ConcreteAsyncDownloader(config={"MAX_DOWNLOAD_RETRIES": -2})
        assert downloader._get_max_retries() == 0

    def test_get_retry_delay_default(self):
        """Test default retry delay."""
        downloader = ConcreteAsyncDownloader(config={})
        assert downloader._get_retry_delay() == 1.0

    def test_get_retry_delay_from_config(self):
        """Test retry delay from config."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_RETRY_DELAY": 2.5})
        assert downloader._get_retry_delay() == 2.5

    def test_get_retry_delay_invalid_fallback(self):
        """Invalid retry delay should fall back to default."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_RETRY_DELAY": "oops"})
        assert downloader._get_retry_delay() == 1.0

    def test_get_retry_delay_clamped_minimum(self):
        """Negative retry delay should be clamped to zero."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_RETRY_DELAY": -1.5})
        assert downloader._get_retry_delay() == 0.0


# =============================================================================
# Get Target Path Tests (lines 70-89)
# =============================================================================


class TestGetTargetPathForRelease:
    """Test get_target_path_for_release method."""

    def test_get_target_path_basic(self, tmp_path):
        """Test basic target path generation."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        result = downloader.get_target_path_for_release("v2.5.0", "firmware.bin")

        expected = os.path.join(str(tmp_path), "v2.5.0", "firmware.bin")
        assert result == expected

    def test_get_target_path_creates_directory(self, tmp_path):
        """Test that target path creates version directory."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        result = downloader.get_target_path_for_release("v2.5.0", "firmware.bin")

        assert os.path.exists(os.path.dirname(result))

    def test_get_target_path_sanitizes_slashes(self, tmp_path):
        """Test that unsafe path components with slashes raise ValueError."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        # Slashes in release tag or file name should raise ValueError
        # to prevent path traversal attacks
        with pytest.raises(ValueError, match="Unsafe release tag"):
            downloader.get_target_path_for_release("v2.5.0/beta", "firmware.bin")

        with pytest.raises(ValueError, match="Unsafe file name"):
            downloader.get_target_path_for_release("v2.5.0", "sub/firmware.bin")


# =============================================================================
# Progress Callback Tests (lines 208-229)
# =============================================================================


class TestCallProgressCallback:
    """Test _call_progress_callback method."""

    async def test_sync_callback(self, mocker):
        """Test calling synchronous progress callback."""
        downloader = ConcreteAsyncDownloader()
        callback_calls = []

        def sync_callback(downloaded, total, filename):
            callback_calls.append((downloaded, total, filename))

        await downloader._call_progress_callback(sync_callback, 1024, 2048, "test.bin")

        assert len(callback_calls) == 1
        assert callback_calls[0] == (1024, 2048, "test.bin")

    async def test_async_callback(self, mocker):
        """Test calling async progress callback."""
        downloader = ConcreteAsyncDownloader()
        callback_calls = []

        async def async_callback(downloaded, total, filename):
            callback_calls.append((downloaded, total, filename))

        await downloader._call_progress_callback(async_callback, 1024, 2048, "test.bin")

        assert len(callback_calls) == 1
        assert callback_calls[0] == (1024, 2048, "test.bin")

    async def test_callback_exception_handled(self, mocker):
        """Test that callback exceptions are handled gracefully."""
        downloader = ConcreteAsyncDownloader()

        def bad_callback(downloaded, total, filename):
            raise ValueError("Callback error")

        # Should not raise
        await downloader._call_progress_callback(bad_callback, 1024, 2048, "test.bin")


# =============================================================================
# Async File Verification Tests (lines 231-266)
# =============================================================================


class TestAsyncVerifyExistingFile:
    """Test _async_verify_existing_file method."""

    async def test_verify_regular_file_missing_hash(self, tmp_path, mocker):
        """Test verification of regular file when no hash exists."""
        downloader = ConcreteAsyncDownloader()

        # Create a test file
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test content")

        # Mock verify_file_integrity to return True
        mocker.patch(
            "fetchtastic.utils.verify_file_integrity",
            return_value=True,
        )

        result = await downloader._async_verify_existing_file(test_file)

        assert result is True

    async def test_verify_zip_file_valid(self, tmp_path, mocker):
        """Test verification of valid zip file."""
        downloader = ConcreteAsyncDownloader()

        # Create a valid zip file
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file.txt", "content")

        mocker.patch(
            "fetchtastic.utils.verify_file_integrity",
            return_value=True,
        )

        result = await downloader._async_verify_existing_file(zip_path)

        assert result is True

    async def test_verify_zip_file_corrupted(self, tmp_path, mocker):
        """Test verification of corrupted zip file."""
        downloader = ConcreteAsyncDownloader()

        # Create a corrupted zip file
        zip_path = tmp_path / "corrupt.zip"
        zip_path.write_bytes(b"not a valid zip file")

        result = await downloader._async_verify_existing_file(zip_path)

        assert result is False

    async def test_verify_file_not_exists(self, tmp_path, mocker):
        """Test verification of non-existent file."""
        downloader = ConcreteAsyncDownloader()

        nonexistent = tmp_path / "nonexistent.bin"

        result = await downloader._async_verify_existing_file(nonexistent)

        assert result is False


# =============================================================================
# Async Save File Hash Tests (lines 268-281)
# =============================================================================


class TestAsyncSaveFileHash:
    """Test _async_save_file_hash method."""

    async def test_save_file_hash(self, tmp_path, mocker):
        """Test saving file hash."""
        downloader = ConcreteAsyncDownloader()

        # Create a test file
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test content")

        # Mock the hash functions
        mock_calculate = mocker.patch(
            "fetchtastic.download.async_downloader.calculate_sha256",
            return_value="abc123hash",
        )
        mock_save = mocker.patch("fetchtastic.download.async_downloader.save_file_hash")

        await downloader._async_save_file_hash(test_file)

        mock_calculate.assert_called_once_with(str(test_file))
        mock_save.assert_called_once_with(str(test_file), "abc123hash")


# =============================================================================
# Async Cleanup Temp File Tests (lines 283-294)
# =============================================================================


class TestAsyncCleanupTempFile:
    """Test _async_cleanup_temp_file method."""

    async def test_cleanup_existing_file(self, tmp_path):
        """Test cleaning up an existing temp file."""
        downloader = ConcreteAsyncDownloader()

        temp_file = tmp_path / "test.tmp"
        temp_file.write_bytes(b"temp content")

        await downloader._async_cleanup_temp_file(temp_file)

        assert not temp_file.exists()

    async def test_cleanup_nonexistent_file(self, tmp_path):
        """Test cleaning up a non-existent temp file."""
        downloader = ConcreteAsyncDownloader()

        nonexistent = tmp_path / "nonexistent.tmp"

        # Should not raise
        await downloader._async_cleanup_temp_file(nonexistent)


# =============================================================================
# Async Download Tests (lines 118-206)
# =============================================================================


class TestAsyncDownload:
    """Test async_download method."""

    async def test_async_download_success(self, mocker, tmp_path):
        """Test successful async download."""
        downloader = ConcreteAsyncDownloader(config={"MAX_CONCURRENT_DOWNLOADS": 3})

        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        # Mock content iteration
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=_make_async_iter([b"test content"])
        )
        mock_response.content = mock_content

        # Set up async context manager for response
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        # Mock aiohttp session
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        # Patch aiohttp
        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            # Patch aiofiles
            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                # Mock hash saving
                mocker.patch.object(
                    downloader,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                )
                mocker.patch.object(downloader, "_async_save_file_hash", AsyncMock())

                target = tmp_path / "test.bin"
                with patch.object(Path, "replace"):
                    result = await downloader.async_download(
                        "https://example.com/file.bin", target
                    )

        assert result is True

    async def test_async_download_reuses_shared_session(self, mocker, tmp_path):
        """Multiple downloads should reuse one session per downloader instance."""
        downloader = ConcreteAsyncDownloader(config={"MAX_CONCURRENT_DOWNLOADS": 3})

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "4"}
        mock_response.raise_for_status = Mock()
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch(
                "aiohttp.ClientSession", return_value=mock_session
            ) as mock_session_cls,
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                mocker.patch.object(
                    downloader,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                )
                mocker.patch.object(downloader, "_async_save_file_hash", AsyncMock())

                with patch.object(Path, "replace"):
                    result1 = await downloader.async_download(
                        "https://example.com/file1.bin", tmp_path / "file1.bin"
                    )
                    result2 = await downloader.async_download(
                        "https://example.com/file2.bin", tmp_path / "file2.bin"
                    )

        assert result1 is True
        assert result2 is True
        assert mock_session_cls.call_count == 1

    async def test_async_download_skips_existing_valid_file(self, mocker, tmp_path):
        """Test that download is skipped for existing valid file."""
        downloader = ConcreteAsyncDownloader()

        # Create an existing file
        existing_file = tmp_path / "existing.bin"
        existing_file.write_bytes(b"existing content")

        # Mock verification to return True
        mocker.patch.object(
            downloader,
            "_async_verify_existing_file",
            AsyncMock(return_value=True),
        )

        result = await downloader.async_download(
            "https://example.com/file.bin", existing_file
        )

        assert result is True

    async def test_async_download_creates_parent_directory(self, mocker, tmp_path):
        """Test that download creates parent directories."""
        downloader = ConcreteAsyncDownloader()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                mocker.patch.object(
                    downloader,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                )
                mocker.patch.object(downloader, "_async_save_file_hash", AsyncMock())

                target = tmp_path / "subdir" / "nested" / "test.bin"
                with patch.object(Path, "replace"):
                    result = await downloader.async_download(
                        "https://example.com/file.bin", target
                    )

        assert result is True
        assert target.parent.exists()

    async def test_async_download_with_progress_callback(self, mocker, tmp_path):
        """Test async download with progress callback."""
        downloader = ConcreteAsyncDownloader()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=_make_async_iter([b"chunk1", b"chunk2"])
        )
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        callback_calls = []

        async def progress(downloaded, total, filename):
            callback_calls.append((downloaded, total, filename))

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                mocker.patch.object(
                    downloader,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                )
                mocker.patch.object(downloader, "_async_save_file_hash", AsyncMock())

                target = tmp_path / "test.bin"
                with patch.object(Path, "replace"):
                    result = await downloader.async_download(
                        "https://example.com/file.bin",
                        target,
                        progress_callback=progress,
                    )

        assert result is True
        assert len(callback_calls) == 2  # One per chunk

    async def test_async_download_client_error(self, mocker, tmp_path):
        """Test async download handles client errors."""
        downloader = ConcreteAsyncDownloader()
        import aiohttp

        mock_session = AsyncMock()
        mock_session.get = Mock(side_effect=aiohttp.ClientError("Connection failed"))

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()

            mocker.patch.object(
                downloader, "_async_verify_existing_file", return_value=False
            )
            mocker.patch.object(downloader, "_async_cleanup_temp_file", AsyncMock())

            target = tmp_path / "test.bin"
            with pytest.raises(AsyncDownloadError) as exc_info:
                await downloader.async_download("https://example.com/file.bin", target)

        assert exc_info.value.is_retryable is True

    async def test_async_download_os_error(self, mocker, tmp_path):
        """Test async download handles OS errors."""
        downloader = ConcreteAsyncDownloader()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
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
                    downloader, "_async_verify_existing_file", return_value=False
                )
                mocker.patch.object(downloader, "_async_cleanup_temp_file", AsyncMock())

                target = tmp_path / "test.bin"
                with pytest.raises(AsyncDownloadError) as exc_info:
                    await downloader.async_download(
                        "https://example.com/file.bin", target
                    )

        assert exc_info.value.is_retryable is False


# =============================================================================
# Async Download With Retry Tests (lines 296-343)
# =============================================================================


class TestAsyncDownloadWithRetry:
    """Test async_download_with_retry method."""

    async def test_retry_success_first_attempt(self, mocker, tmp_path):
        """Test successful download on first attempt."""
        downloader = ConcreteAsyncDownloader()

        mock_download = mocker.patch.object(
            downloader, "async_download", AsyncMock(return_value=True)
        )

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin", target
        )

        assert result is True
        mock_download.assert_called_once()

    async def test_retry_success_after_failure(self, mocker, tmp_path):
        """Test successful download after failure."""
        downloader = ConcreteAsyncDownloader()

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

    async def test_retry_exhausted(self, mocker, tmp_path):
        """Test failure after exhausting all retries."""
        downloader = ConcreteAsyncDownloader()

        mock_download = mocker.patch.object(
            downloader, "async_download", AsyncMock(return_value=False)
        )

        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin",
            target,
            max_retries=2,
            retry_delay=0.1,
        )

        assert result is False
        assert mock_download.call_count == 3  # Initial + 2 retries

    async def test_retry_exception_handling(self, mocker, tmp_path):
        """Test handling exceptions during retry."""
        downloader = ConcreteAsyncDownloader()

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

    async def test_retry_uses_config_defaults(self, mocker, tmp_path):
        """Test that retry uses config defaults when not specified."""
        downloader = ConcreteAsyncDownloader(
            config={
                "MAX_DOWNLOAD_RETRIES": 5,
                "DOWNLOAD_RETRY_DELAY": 2.0,
            }
        )

        mocker.patch.object(downloader, "async_download", AsyncMock(return_value=True))

        target = tmp_path / "test.bin"
        result = await downloader.async_download_with_retry(
            "https://example.com/file.bin", target
        )

        assert result is True

    async def test_retry_exponential_backoff(self, mocker, tmp_path):
        """Test exponential backoff timing."""
        downloader = ConcreteAsyncDownloader()

        # Use exceptions to trigger retry delays (not False returns)
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(
                side_effect=[
                    Exception("Error 1"),  # First attempt - will retry
                    Exception("Error 2"),  # Second attempt - will retry
                    True,  # Third attempt - success
                ]
            ),
        )

        sleep_calls = []

        async def track_sleep(duration):
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


# =============================================================================
# Async Download Release Tests (lines 345-402)
# =============================================================================


class TestAsyncDownloadRelease:
    """Test async_download_release method."""

    async def test_download_release_success(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test successful release download."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        mocker.patch.object(
            downloader, "async_download_with_retry", AsyncMock(return_value=True)
        )

        result = await downloader.async_download_release(sample_release, sample_asset)

        assert result.success is True
        assert result.release_tag == sample_release.tag_name
        assert result.download_url == sample_asset.download_url
        assert result.file_size == sample_asset.size

    async def test_download_release_failure(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test failed release download."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        mocker.patch.object(
            downloader, "async_download_with_retry", AsyncMock(return_value=False)
        )

        result = await downloader.async_download_release(sample_release, sample_asset)

        assert result.success is False
        assert result.error_message == "Download failed"
        assert result.error_type == ERROR_TYPE_NETWORK
        assert result.is_retryable is True

    async def test_download_release_exception(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test release download with exception."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        mocker.patch.object(
            downloader,
            "async_download_with_retry",
            AsyncMock(side_effect=Exception("Unexpected error")),
        )

        result = await downloader.async_download_release(sample_release, sample_asset)

        assert result.success is False
        assert result.error_message is not None
        assert "Unexpected error" in result.error_message
        assert result.error_type == ERROR_TYPE_UNKNOWN
        assert result.is_retryable is True

    async def test_download_release_with_progress_callback(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test release download with progress callback."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        mock_download = mocker.patch.object(
            downloader, "async_download_with_retry", AsyncMock(return_value=True)
        )

        callback_calls = []

        async def progress(downloaded, total, filename):
            callback_calls.append((downloaded, total, filename))

        result = await downloader.async_download_release(
            sample_release, sample_asset, progress_callback=progress
        )

        assert result.success is True
        # Verify callback was passed
        assert mock_download.call_args[1]["progress_callback"] == progress


# =============================================================================
# Async Download Multiple Tests (lines 404-431)
# =============================================================================


class TestAsyncDownloadMultiple:
    """Test async_download_multiple method."""

    async def test_download_multiple_success(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test downloading multiple files."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        # Create second asset
        asset2 = Asset(
            name="firmware-tbeam.bin",
            download_url="https://example.com/firmware-tbeam.bin",
            size=1024000,
        )

        downloads = [
            {"release": sample_release, "asset": sample_asset},
            {"release": sample_release, "asset": asset2},
        ]

        mock_download = mocker.patch.object(
            downloader,
            "async_download_release",
            AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    release_tag=sample_release.tag_name,
                    file_path=tmp_path / "firmware.bin",
                )
            ),
        )

        results = await downloader.async_download_multiple(downloads)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert mock_download.call_count == 2

    async def test_download_multiple_mixed_results(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test downloading multiple files with mixed results."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        asset2 = Asset(
            name="firmware-tbeam.bin",
            download_url="https://example.com/firmware-tbeam.bin",
            size=1024000,
        )

        downloads = [
            {"release": sample_release, "asset": sample_asset},
            {"release": sample_release, "asset": asset2},
        ]

        mocker.patch.object(
            downloader,
            "async_download_release",
            AsyncMock(
                side_effect=[
                    DownloadResult(
                        success=True,
                        release_tag=sample_release.tag_name,
                        file_path=tmp_path / "file1.bin",
                    ),
                    DownloadResult(
                        success=False,
                        release_tag=sample_release.tag_name,
                        error_message="Failed",
                    ),
                ]
            ),
        )

        results = await downloader.async_download_multiple(downloads)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False

    async def test_download_multiple_handles_malformed_specs(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Malformed specs in gather exception path should produce fallback results."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        downloads = [
            {"release": sample_release, "asset": sample_asset},
            {"release": sample_release},  # missing asset
            cast(Dict[str, Any], "not-a-dict"),
        ]

        mocker.patch.object(
            downloader,
            "async_download_release",
            AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    release_tag=sample_release.tag_name,
                    file_path=tmp_path / "firmware.bin",
                )
            ),
        )

        results = await downloader.async_download_multiple(downloads)

        assert len(results) == 3
        assert results[0].success is True
        assert results[1].success is False
        assert results[2].success is False
        assert results[1].release_tag == "<unknown>"
        assert results[2].release_tag == "<unknown>"

    async def test_download_multiple_with_progress_callback(
        self, mocker, tmp_path, sample_release, sample_asset
    ):
        """Test downloading multiple files with progress callback."""
        downloader = ConcreteAsyncDownloader(config={"DOWNLOAD_DIR": str(tmp_path)})

        downloads = [{"release": sample_release, "asset": sample_asset}]

        mocker.patch.object(
            downloader,
            "async_download_release",
            AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    release_tag=sample_release.tag_name,
                    file_path=tmp_path / "firmware.bin",
                )
            ),
        )

        async def progress(downloaded, total, filename):
            pass

        results = await downloader.async_download_multiple(
            downloads, progress_callback=progress
        )

        assert len(results) == 1
        assert results[0].success is True


# =============================================================================
# AsyncDownloaderBase Tests (lines 434-458)
# =============================================================================


class TestAsyncDownloaderBase:
    """Test AsyncDownloaderBase class."""

    def test_init_default_config(self):
        """Test initialization with default config."""
        downloader = AsyncDownloaderBase()

        assert downloader.config == {}
        assert "meshtastic" in downloader.download_dir

    def test_init_with_config(self, tmp_path):
        """Test initialization with custom config."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "MAX_CONCURRENT_DOWNLOADS": 10,
        }
        downloader = AsyncDownloaderBase(config)

        assert downloader.config == config
        assert downloader.download_dir == str(tmp_path)

    async def test_base_is_usable_for_downloads(self, tmp_path, mocker):
        """Test that AsyncDownloaderBase can be used directly."""
        downloader = AsyncDownloaderBase(config={"DOWNLOAD_DIR": str(tmp_path)})

        # Mock the download process
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "4"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                mocker.patch.object(
                    downloader,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                )
                mocker.patch.object(downloader, "_async_save_file_hash", AsyncMock())

                target = tmp_path / "test.bin"
                with patch.object(Path, "replace"):
                    result = await downloader.async_download(
                        "https://example.com/file.bin", target
                    )

        assert result is True


# =============================================================================
# Download With Progress Utility Function Tests (lines 461-492)
# =============================================================================


class TestDownloadWithProgress:
    """Test download_with_progress utility function."""

    async def test_download_with_progress_success(self, tmp_path, mocker):
        """Test successful download with progress function."""
        callback_calls = []

        async def progress(downloaded, total, filename):
            callback_calls.append((downloaded, total, filename))

        # Mock the download
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=_make_async_iter([b"test content"])
        )
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                # Mock verification to skip existing
                with patch.object(
                    AsyncDownloaderBase,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                ):
                    with patch.object(
                        AsyncDownloaderBase,
                        "_async_save_file_hash",
                        AsyncMock(),
                    ):
                        target = tmp_path / "test.bin"
                        with patch.object(Path, "replace"):
                            result = await download_with_progress(
                                "https://example.com/file.bin",
                                target,
                                progress_callback=progress,
                            )

        assert result is True

    async def test_download_with_progress_custom_config(self, tmp_path, mocker):
        """Test download with custom config."""
        config = {"MAX_CONCURRENT_DOWNLOADS": 10}

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "4"}
        mock_response.raise_for_status = Mock()

        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with (
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch("aiohttp.ClientTimeout"),
            patch("aiohttp.TCPConnector"),
        ):
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            with patch("aiofiles.open") as mock_open:
                mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
                mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch.object(
                    AsyncDownloaderBase,
                    "_async_verify_existing_file",
                    AsyncMock(return_value=False),
                ):
                    with patch.object(
                        AsyncDownloaderBase,
                        "_async_save_file_hash",
                        AsyncMock(),
                    ):
                        target = tmp_path / "test.bin"
                        with patch.object(Path, "replace"):
                            result = await download_with_progress(
                                "https://example.com/file.bin",
                                target,
                                config=config,
                            )

        assert result is True

    async def test_download_with_progress_closes_downloader(self, tmp_path, mocker):
        """Helper should close downloader session in all cases."""
        mock_download = mocker.patch.object(
            AsyncDownloaderBase,
            "async_download",
            new_callable=AsyncMock,
            return_value=True,
        )
        mock_close = mocker.patch.object(
            AsyncDownloaderBase, "close", new_callable=AsyncMock
        )

        result = await download_with_progress(
            "https://example.com/file.bin",
            tmp_path / "file.bin",
        )

        assert result is True
        mock_download.assert_called_once()
        mock_close.assert_called_once()
