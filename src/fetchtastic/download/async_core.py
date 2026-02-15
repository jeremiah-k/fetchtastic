"""
Shared async download core for downloader implementations.

This module centralizes async session lifecycle, concurrency controls, core
download flow, and retry behavior used by both BaseDownloader and
AsyncDownloaderMixin.
"""

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fetchtastic.constants import (
    BYTES_PER_MEGABYTE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    FILE_SIZE_MB_LOGGING_THRESHOLD,
    HTTP_STATUS_ERROR_THRESHOLD,
    HTTP_STATUS_RETRY_THRESHOLD,
)
from fetchtastic.log_utils import logger

from .async_client import AsyncDownloadError
from .interfaces import Pathish

CoreProgressCallback = Callable[[int, Optional[int], str], Any]


class AsyncDownloadCoreMixin:
    """
    Shared core logic for async file downloads.

    Implementers must provide:
    - `_async_verify_existing_file(Path) -> bool`
    - `_async_save_file_hash(Path) -> None`
    """

    config: Dict[str, Any]
    _semaphore: Optional[asyncio.Semaphore]
    _session: Optional[Any]

    def _get_max_concurrent(self) -> int:
        """
        Determine the configured maximum number of concurrent downloads.

        Reads the `MAX_CONCURRENT_DOWNLOADS` config value, validates it as an integer, uses 5 if invalid, and clamps values less than 1 to 1.

        Returns:
            int: Maximum concurrent downloads as an int; guaranteed to be >= 1.
        """
        raw_value = self.config.get("MAX_CONCURRENT_DOWNLOADS", 5)
        try:
            parsed_value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid MAX_CONCURRENT_DOWNLOADS value %r; using default of 5",
                raw_value,
            )
            return 5

        if parsed_value <= 0:
            logger.warning(
                "MAX_CONCURRENT_DOWNLOADS must be >= 1; clamping %d to 1",
                parsed_value,
            )
            return 1

        return parsed_value

    def _get_max_retries(self) -> int:
        """
        Determine the maximum number of download retry attempts from configuration.

        Reads the `MAX_DOWNLOAD_RETRIES` value from `self.config`, validates it as an integer, uses `DEFAULT_CONNECT_RETRIES` when the configured value is invalid, and clamps negative values to 0.

        Returns:
            int: Maximum number of retry attempts.
        """
        raw_value = self.config.get("MAX_DOWNLOAD_RETRIES", DEFAULT_CONNECT_RETRIES)
        try:
            parsed_value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid MAX_DOWNLOAD_RETRIES value %r; using default %d",
                raw_value,
                DEFAULT_CONNECT_RETRIES,
            )
            return DEFAULT_CONNECT_RETRIES

        if parsed_value < 0:
            logger.warning(
                "MAX_DOWNLOAD_RETRIES must be >= 0; clamping %d to 0",
                parsed_value,
            )
            return 0

        return parsed_value

    def _get_retry_delay(self) -> float:
        """
        Return the initial download retry delay read from the configuration.

        Reads DOWNLOAD_RETRY_DELAY from self.config, defaults to 1.0 on missing or invalid values,
        and clamps negative values to 0.0.

        Returns:
            float: Initial retry delay in seconds.
        """
        raw_value = self.config.get("DOWNLOAD_RETRY_DELAY", 1.0)
        try:
            parsed_value = float(raw_value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid DOWNLOAD_RETRY_DELAY value %r; using default 1.0",
                raw_value,
            )
            return 1.0

        if parsed_value < 0.0:
            logger.warning(
                "DOWNLOAD_RETRY_DELAY must be >= 0.0; clamping %.3f to 0.0",
                parsed_value,
            )
            return 0.0

        return parsed_value

    def _get_semaphore(self) -> asyncio.Semaphore:
        """
        Lazily create and return a semaphore used to limit concurrent downloads.

        Returns:
            asyncio.Semaphore: Semaphore initialized with the configured maximum concurrent downloads.
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._get_max_concurrent())
        return self._semaphore

    async def _ensure_session(self, aiohttp_module: Optional[Any] = None) -> Any:
        """
        Ensure and return a reusable aiohttp ClientSession for downloads.

        If no aiohttp module is provided, the function will import it dynamically. The returned session is created with a TCPConnector limited by the configured maximum concurrent downloads and a default request timeout.

        Parameters:
            aiohttp_module (Optional[Any]): Optional aiohttp module to use instead of importing one.

        Returns:
            Active aiohttp ClientSession instance.
        """
        resolved_aiohttp: Any
        if aiohttp_module is None:
            import aiohttp as imported_aiohttp

            resolved_aiohttp = imported_aiohttp
        else:
            resolved_aiohttp = aiohttp_module

        if self._session is None or getattr(self._session, "closed", False):
            connector = resolved_aiohttp.TCPConnector(limit=self._get_max_concurrent())
            timeout = resolved_aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT)
            self._session = resolved_aiohttp.ClientSession(
                connector=connector, timeout=timeout
            )
        return self._session

    async def close(self) -> None:
        """Close the shared aiohttp session, if active."""
        if self._session is not None and not getattr(self._session, "closed", False):
            close_result = self._session.close()
            if asyncio.iscoroutine(close_result):
                await close_result
        self._session = None

    async def __aenter__(self) -> "AsyncDownloadCoreMixin":
        """
        Ensure an aiohttp session is created and return self for use as an async context manager.

        Returns:
            AsyncDownloadCoreMixin: The mixin instance with a ready-to-use session.
        """
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Close the mixin's shared HTTP session when exiting an async context.

        This is called by the asynchronous context manager protocol to ensure the underlying session is closed and resources are released.
        """
        await self.close()

    def _sync_download_fallback(self, url: str, target_path: Pathish) -> Optional[bool]:
        """
        Optional fallback when async dependencies are unavailable.

        Return None to indicate no fallback behavior.
        """
        del url, target_path
        return None

    async def _call_progress_callback(
        self,
        callback: CoreProgressCallback,
        downloaded: int,
        total: Optional[int],
        filename: str,
    ) -> None:
        """
        Invoke a progress callback with the current download progress and suppress any callback errors.

        Parameters:
            callback (CoreProgressCallback): Callable receiving (downloaded, total, filename). May be synchronous or return a coroutine.
            downloaded (int): Number of bytes downloaded so far.
            total (Optional[int]): Total number of bytes if known, otherwise None.
            filename (str): Target filename being downloaded.
        """
        try:
            result = callback(downloaded, total, filename)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.debug(f"Progress callback error: {e}")

    async def _async_cleanup_temp_file(self, temp_path: Path) -> None:
        """
        Delete the temporary file at the given path if it exists.

        Parameters:
            temp_path (Path): Path to the temporary file to remove. If removal fails due to an OS error, the error is logged at debug level and suppressed.
        """
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError as e:
            logger.debug(f"Error cleaning up temp file {temp_path}: {e}")

    async def _async_verify_existing_file(self, file_path: Path) -> bool:
        """
        Determine whether the local file at `file_path` is valid and the download can be skipped.

        Parameters:
            file_path (Path): Path to the local file to verify.

        Returns:
            bool: `True` if the file is valid and the caller may skip downloading, `False` otherwise.
        """
        raise NotImplementedError

    async def _async_save_file_hash(self, file_path: Path) -> None:
        """
        Compute and persist a content hash for the given file.

        Implementations should calculate a stable checksum (e.g., SHA-256) of the file at `file_path` and persist it according to the subclass's storage policy (for example, writing a sidecar file or updating a database). This method is expected to be implemented by concrete classes to record the downloaded file's hash for future verification.

        Parameters:
            file_path (Path): Path to the file whose hash should be computed and stored.
        """
        raise NotImplementedError

    async def async_download(
        self,
        url: str,
        target_path: Pathish,
        progress_callback: Optional[CoreProgressCallback] = None,
    ) -> bool:
        """
        Download a file from `url` and save it to `target_path`, reporting progress if requested.

        Parameters:
            progress_callback (Optional[CoreProgressCallback]): Optional callable invoked with
                (downloaded_bytes, total_bytes_or_None, filename) to report progress. May be
                sync or async; errors raised by the callback are logged and do not stop the download.

        Returns:
            bool: `True` if the file is present at `target_path` after this call (downloaded or
            already present and verified).

        Raises:
            AsyncDownloadError: If the download or file save fails.
        """
        try:
            import aiofiles  # type: ignore[import-untyped]
            import aiohttp
        except ImportError as e:
            fallback_result = self._sync_download_fallback(url, target_path)
            if fallback_result is not None:
                return fallback_result
            raise AsyncDownloadError(
                "Async libraries are required for async downloads",
                url=url,
                is_retryable=False,
            ) from e

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            if await self._async_verify_existing_file(target):
                logger.info(f"Skipped: {target.name} (already present & verified)")
                return True

        temp_path = target.with_suffix(f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
        downloaded = 0

        try:
            start_time = time.time()

            async with self._get_semaphore():
                session = await self._ensure_session(aiohttp)
                async with session.get(url) as response:
                    if response.status >= HTTP_STATUS_ERROR_THRESHOLD:
                        raise AsyncDownloadError(
                            f"HTTP error {response.status}",
                            url=url,
                            status_code=response.status,
                            is_retryable=response.status >= HTTP_STATUS_RETRY_THRESHOLD,
                        )

                    content_length = response.headers.get("Content-Length")
                    try:
                        total_size = int(content_length) if content_length else 0
                    except (TypeError, ValueError):
                        total_size = 0

                    async with aiofiles.open(temp_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(
                            DEFAULT_CHUNK_SIZE
                        ):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                await self._call_progress_callback(
                                    progress_callback,
                                    downloaded,
                                    total_size or None,
                                    target.name,
                                )

            elapsed = time.time() - start_time
            file_size_mb = downloaded / BYTES_PER_MEGABYTE
            logger.debug(f"Downloaded {url} in {elapsed:.2f}s")

            temp_path.replace(target)
            # Save hash in background - failure should not fail the download
            # since the file is already in place
            try:
                await self._async_save_file_hash(target)
            except Exception as hash_err:
                logger.warning(
                    "Failed to save file hash for %s: %s", target.name, hash_err
                )

            if file_size_mb >= FILE_SIZE_MB_LOGGING_THRESHOLD:
                logger.info(f"Downloaded: {target.name} ({file_size_mb:.1f} MB)")
            else:
                logger.info(f"Downloaded: {target.name} ({downloaded} bytes)")

            return True

        except AsyncDownloadError:
            await self._async_cleanup_temp_file(temp_path)
            raise
        except aiohttp.ClientResponseError as e:
            await self._async_cleanup_temp_file(temp_path)
            raise AsyncDownloadError(
                f"HTTP error {e.status}: {e.message}",
                url=url,
                status_code=e.status,
                is_retryable=e.status >= HTTP_STATUS_RETRY_THRESHOLD,
            ) from e
        except aiohttp.ClientError as e:
            await self._async_cleanup_temp_file(temp_path)
            raise AsyncDownloadError(
                f"Network error: {e}",
                url=url,
                is_retryable=True,
            ) from e
        except OSError as e:
            await self._async_cleanup_temp_file(temp_path)
            raise AsyncDownloadError(
                f"Filesystem error: {e}",
                url=url,
                is_retryable=False,
            ) from e
        except Exception as e:
            await self._async_cleanup_temp_file(temp_path)
            raise AsyncDownloadError(
                f"Unexpected error: {e}",
                url=url,
                is_retryable=True,
            ) from e

    async def async_download_with_retry(
        self,
        url: str,
        target_path: Pathish,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        backoff_factor: float = 2.0,
        progress_callback: Optional[CoreProgressCallback] = None,
    ) -> bool:
        """
        Attempt to download a URL to the given target path using retry attempts with exponential backoff.

        Parameters:
            url (str): Source URL to download.
            target_path (Pathish): Destination path for the downloaded file.
            max_retries (Optional[int]): Maximum retry attempts; when None uses configured default.
            retry_delay (Optional[float]): Initial delay in seconds between retries; when None uses configured default.
            backoff_factor (float): Multiplier applied to the delay after each retry.
            progress_callback (Optional[CoreProgressCallback]): Optional callback to report download progress.

        Returns:
            bool: `True` if the download succeeded.

        Raises:
            AsyncDownloadError: If a non-retryable download error occurs or retries are exhausted.
        """
        attempts = max_retries if max_retries is not None else self._get_max_retries()
        attempts = max(0, attempts)
        delay = retry_delay if retry_delay is not None else self._get_retry_delay()
        delay = max(0.0, delay)
        last_error: Optional[AsyncDownloadError] = None
        last_cause: Optional[Exception] = None

        for attempt in range(attempts + 1):
            try:
                result = await self.async_download(
                    url, target_path, progress_callback=progress_callback
                )
                if result:
                    return True

                last_error = AsyncDownloadError(
                    f"Download failed after {attempt + 1}/{attempts + 1} attempts",
                    url=url,
                    retry_count=attempt,
                    is_retryable=attempt < attempts,
                )
                last_cause = None
                if attempt == attempts:
                    logger.error(
                        f"Download failed permanently after {attempts + 1} attempts for {url}"
                    )
                    break

                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s"
                )
            except AsyncDownloadError as e:
                last_error = e
                last_cause = None
                if not e.is_retryable:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    raise
                if attempt == attempts:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    break
                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e.message}"
                )
            except Exception as e:
                wrapped_error = AsyncDownloadError(
                    f"Unexpected error: {e}",
                    url=url,
                    is_retryable=True,
                )
                last_error = wrapped_error
                last_cause = e
                if attempt == attempts:
                    logger.error(
                        "Download failed permanently for %s: %s",
                        url,
                        wrapped_error.message,
                    )
                    break
                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e}"
                )

            await asyncio.sleep(delay)
            delay *= backoff_factor

        if last_error is not None:
            if last_cause is not None:
                raise last_error from last_cause
            raise last_error
        raise AsyncDownloadError(
            "Download failed unexpectedly without an error context",
            url=url,
            is_retryable=False,
        )
