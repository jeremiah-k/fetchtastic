"""Targeted tests for async_core.py branch coverage."""

from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

import fetchtastic.download.async_core as async_core_module
from fetchtastic.download.async_client import AsyncDownloadError
from fetchtastic.download.async_core import AsyncDownloadCoreMixin

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


async def _make_async_iter(items):
    """
    Create an asynchronous iterator that yields each value from the given iterable.
    
    Parameters:
        items (Iterable): iterable of values to yield asynchronously.
    
    Returns:
        AsyncIterator: an async generator that yields each item from `items` in order.
    """
    for item in items:
        yield item


class ConcreteCoreDownloader(AsyncDownloadCoreMixin):
    """Concrete test implementation for AsyncDownloadCoreMixin."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        """
        Initialize the ConcreteCoreDownloader instance and its internal state.
        
        Parameters:
            config (dict[str, Any], optional): Configuration mapping to customize downloader behavior. If omitted, an empty configuration is used.
        
        Attributes set:
            config: The provided or default configuration.
            _semaphore: Internal semaphore for concurrency control, initialized to None.
            _session: HTTP client session placeholder, initialized to None.
            verify_result (bool): Flag controlling verification behavior; defaults to False.
            saved_paths (list[Path]): Records file paths saved via hashing; starts empty.
        """
        self.config = config or {}
        self._semaphore = None
        self._session = None
        self.verify_result = False
        self.saved_paths: list[Path] = []

    async def _async_verify_existing_file(self, file_path: Path) -> bool:
        """
        Determine whether an existing file at the given path should be treated as verified.
        
        Parameters:
            file_path (Path): Path to the existing file to verify.
        
        Returns:
            bool: `true` if the file is considered verified, `false` otherwise.
        """
        del file_path
        return self.verify_result

    async def _async_save_file_hash(self, file_path: Path) -> None:
        """
        Record the given file path in the downloader's saved paths list for later verification.
        
        Parameters:
            file_path (Path): Filesystem path of the saved file to record.
        """
        self.saved_paths.append(file_path)


@pytest.mark.asyncio
class TestSessionAndFallback:
    """Tests for session lifecycle and fallback behavior."""

    async def test_ensure_session_import_path_when_module_not_provided(self, mocker):
        """_ensure_session should import aiohttp when module argument is None."""
        downloader = ConcreteCoreDownloader()
        mock_session = mocker.MagicMock()
        mock_session.closed = False

        with (
            patch("aiohttp.TCPConnector") as mock_connector,
            patch("aiohttp.ClientTimeout") as mock_timeout,
            patch(
                "aiohttp.ClientSession", return_value=mock_session
            ) as mock_session_cls,
        ):
            session = await downloader._ensure_session()

        assert session is mock_session
        mock_connector.assert_called_once()
        mock_timeout.assert_called_once()
        mock_session_cls.assert_called_once()

    async def test_close_awaits_coroutine_close_result(self, mocker):
        """close should await coroutine close result and clear the session."""
        downloader = ConcreteCoreDownloader()
        mock_session = mocker.MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        downloader._session = mock_session

        await downloader.close()

        mock_session.close.assert_called_once()
        assert downloader._session is None

    async def test_async_context_manager_calls_ensure_and_close(self, mocker):
        """Async context manager should call ensure_session on enter and close on exit."""
        downloader = ConcreteCoreDownloader()
        mock_ensure = mocker.patch.object(
            downloader,
            "_ensure_session",
            AsyncMock(return_value=MagicMock()),
        )
        mock_close = mocker.patch.object(downloader, "close", AsyncMock())

        async with downloader as entered:
            assert entered is downloader

        mock_ensure.assert_called_once()
        mock_close.assert_called_once()

    async def test_sync_download_fallback_default_returns_none(self):
        """Default fallback implementation should return None."""
        downloader = ConcreteCoreDownloader()
        result = downloader._sync_download_fallback(
            "https://example.com/file.bin",
            "/tmp/file.bin",
        )
        assert result is None


@pytest.mark.asyncio
class TestAsyncCoreDownloadPaths:
    """Tests for download and cleanup branches in async core."""

    async def test_async_cleanup_temp_file_handles_oserror(self, tmp_path):
        """Cleanup should swallow OSError when deleting temp file fails."""
        downloader = ConcreteCoreDownloader()
        temp_path = tmp_path / "tempfile.tmp"
        temp_path.write_bytes(b"tmp")

        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            await downloader._async_cleanup_temp_file(temp_path)

    async def test_async_download_uses_sync_fallback_on_import_error(
        self, mocker, tmp_path
    ):
        """ImportError should use sync fallback result when one is provided."""
        downloader = ConcreteCoreDownloader()
        fallback = mocker.patch.object(
            downloader,
            "_sync_download_fallback",
            return_value=True,
        )
        target = tmp_path / "file.bin"

        original_import = __import__

        def fake_import(name, *args, **kwargs):
            """
            Simulate a missing async library by raising ImportError for specific module names.
            
            Parameters:
                name (str): The module name to import.
                *args: Positional arguments forwarded to the underlying import function.
                **kwargs: Keyword arguments forwarded to the underlying import function.
            
            Returns:
                module: The imported module for names other than "aiofiles" and "aiohttp".
            
            Raises:
                ImportError: If `name` is "aiofiles" or "aiohttp".
            """
            if name in {"aiofiles", "aiohttp"}:
                raise ImportError("missing async library")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = await downloader.async_download(
                "https://example.com/file.bin",
                target,
            )

        assert result is True
        fallback.assert_called_once()

    async def test_async_download_import_error_without_fallback_raises(self, tmp_path):
        """ImportError should raise AsyncDownloadError when no fallback is provided."""
        downloader = ConcreteCoreDownloader()
        target = tmp_path / "file.bin"

        original_import = __import__

        def fake_import(name, *args, **kwargs):
            """
            Simulate a missing async library by raising ImportError for specific module names.
            
            Parameters:
                name (str): The module name to import.
                *args: Positional arguments forwarded to the underlying import function.
                **kwargs: Keyword arguments forwarded to the underlying import function.
            
            Returns:
                module: The imported module for names other than "aiofiles" and "aiohttp".
            
            Raises:
                ImportError: If `name` is "aiofiles" or "aiohttp".
            """
            if name in {"aiofiles", "aiohttp"}:
                raise ImportError("missing async library")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(AsyncDownloadError) as exc_info:
                await downloader.async_download(
                    "https://example.com/file.bin",
                    target,
                )

        assert "Async libraries are required" in exc_info.value.message

    async def test_async_download_skips_existing_verified_file(self, tmp_path, mocker):
        """Existing verified file should return True without HTTP download."""
        downloader = ConcreteCoreDownloader()
        target = tmp_path / "existing.bin"
        target.write_bytes(b"already-present")
        downloader.verify_result = True

        ensure_session = mocker.patch.object(downloader, "_ensure_session", AsyncMock())
        result = await downloader.async_download(
            "https://example.com/file.bin",
            target,
        )

        assert result is True
        ensure_session.assert_not_called()

    async def test_async_download_http_status_raises_async_download_error(
        self, mocker, tmp_path
    ):
        """HTTP status >= 400 should raise AsyncDownloadError."""
        downloader = ConcreteCoreDownloader()

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mocker.patch.object(
            downloader,
            "_ensure_session",
            AsyncMock(return_value=mock_session),
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download(
                "https://example.com/missing.bin",
                tmp_path / "missing.bin",
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.is_retryable is False

    async def test_async_download_invalid_content_length_defaults_to_unknown_total(
        self, mocker, tmp_path
    ):
        """Invalid Content-Length should be treated as unknown total size."""
        downloader = ConcreteCoreDownloader()
        callback_calls: list[tuple[int, Optional[int], str]] = []

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "chunked"}
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mocker.patch.object(
            downloader,
            "_ensure_session",
            AsyncMock(return_value=mock_session),
        )

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        with (
            patch("aiofiles.open") as mock_open,
            patch.object(Path, "replace"),
        ):
            mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
            mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

            async def callback(downloaded, total, filename):
                """
                Record download progress by appending a (downloaded, total, filename) tuple to the captured `callback_calls` list.
                
                Parameters:
                	downloaded (int): Number of bytes downloaded so far.
                	total (int | None): Total number of bytes expected, or None if unknown.
                	filename (str): Name of the file being downloaded.
                """
                callback_calls.append((downloaded, total, filename))

            result = await downloader.async_download(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                progress_callback=callback,
            )

        assert result is True
        assert callback_calls == [(4, None, "file.bin")]

    async def test_async_download_large_payload_hits_mb_logging_branch(
        self, mocker, tmp_path
    ):
        """Large downloads should execute MB-size logging branch."""
        downloader = ConcreteCoreDownloader()
        large_chunk = b"x" * (1024 * 1024)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": str(len(large_chunk))}
        mock_content = MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([large_chunk]))
        mock_response.content = mock_content
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mocker.patch.object(
            downloader,
            "_ensure_session",
            AsyncMock(return_value=mock_session),
        )

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        with (
            patch("aiofiles.open") as mock_open,
            patch.object(Path, "replace"),
        ):
            mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
            mock_open.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await downloader.async_download(
                "https://example.com/large.bin",
                tmp_path / "large.bin",
            )

        assert result is True

    async def test_async_download_wraps_client_response_error(self, mocker, tmp_path):
        """ClientResponseError should be wrapped as AsyncDownloadError."""
        downloader = ConcreteCoreDownloader()
        import aiohttp

        mock_session = AsyncMock()
        mock_session.get = Mock(
            side_effect=aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=503,
                message="server-error",
            )
        )
        mocker.patch.object(
            downloader,
            "_ensure_session",
            AsyncMock(return_value=mock_session),
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
            )

        assert exc_info.value.status_code == 503
        assert exc_info.value.is_retryable is True


@pytest.mark.asyncio
class TestAsyncCoreRetryPaths:
    """Tests for async_download_with_retry branch behavior."""

    async def test_retry_non_retryable_async_error_is_re_raised(self, tmp_path, mocker):
        """Non-retryable AsyncDownloadError should propagate immediately."""
        downloader = ConcreteCoreDownloader()
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(
                side_effect=AsyncDownloadError(
                    "not-retryable",
                    is_retryable=False,
                )
            ),
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                max_retries=2,
            )

        assert exc_info.value.message == "not-retryable"

    async def test_retry_retryable_async_error_raises_at_final_attempt(
        self, tmp_path, mocker
    ):
        """Retryable AsyncDownloadError should be re-raised at final attempt."""
        downloader = ConcreteCoreDownloader()
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=AsyncDownloadError("retryable", is_retryable=True)),
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                max_retries=0,
            )

        assert exc_info.value.message == "retryable"
        assert exc_info.value.is_retryable is True

    async def test_retry_unexpected_exception_raises_at_final_attempt(
        self, tmp_path, mocker
    ):
        """Unexpected exceptions should be wrapped and raised at final attempt."""
        downloader = ConcreteCoreDownloader()
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                max_retries=0,
            )

        assert exc_info.value.message == "Unexpected error: boom"
        assert exc_info.value.is_retryable is True

    async def test_retry_defensive_last_error_branch(self, tmp_path, mocker):
        """Exercise defensive last_error branch after loop exits."""
        downloader = ConcreteCoreDownloader()
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=AsyncDownloadError("retryable", is_retryable=True)),
        )
        mocker.patch("asyncio.sleep", AsyncMock())
        mocker.patch.object(async_core_module, "range", return_value=[1])

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                max_retries=0,
            )

        assert exc_info.value.message == "retryable"

    async def test_retry_unexpected_exception_warns_then_raises_on_final_attempt(
        self, tmp_path, mocker
    ):
        """Unexpected exceptions should hit warning branch then raise on final attempt."""
        downloader = ConcreteCoreDownloader()
        mocker.patch.object(
            downloader,
            "async_download",
            AsyncMock(side_effect=[RuntimeError("first"), RuntimeError("second")]),
        )
        mock_sleep = mocker.patch("asyncio.sleep", AsyncMock())

        with pytest.raises(AsyncDownloadError) as exc_info:
            await downloader.async_download_with_retry(
                "https://example.com/file.bin",
                tmp_path / "file.bin",
                max_retries=1,
            )

        assert exc_info.value.message == "Unexpected error: second"
        mock_sleep.assert_awaited_once()