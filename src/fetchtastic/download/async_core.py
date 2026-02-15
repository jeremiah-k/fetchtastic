"""
Shared async download core for downloader implementations.

This module centralizes async session lifecycle, concurrency controls, core
download flow, and retry behavior used by both BaseDownloader and
AsyncDownloaderMixin.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fetchtastic.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
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
        Get the maximum concurrent downloads from config.

        Returns:
            int: Maximum concurrent downloads (default 5).
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
        Get the maximum retry count from config.

        Returns:
            int: Maximum retries (default 3).
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
        Get the initial retry delay from config.

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
        Get or create the semaphore for concurrency control.

        Returns:
            asyncio.Semaphore: Semaphore for limiting concurrent downloads.
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._get_max_concurrent())
        return self._semaphore

    async def _ensure_session(self, aiohttp_module: Optional[Any] = None) -> Any:
        """
        Create or return a reusable aiohttp session for downloads.

        Parameters:
            aiohttp_module (Optional[Any]): Optional imported aiohttp module to reuse.

        Returns:
            Any: Active aiohttp ClientSession instance.
        """
        if aiohttp_module is None:
            import aiohttp as aiohttp_module  # type: ignore[import-not-found]

        if self._session is None or getattr(self._session, "closed", False) is True:
            connector = aiohttp_module.TCPConnector(limit=self._get_max_concurrent())
            timeout = aiohttp_module.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT)
            self._session = aiohttp_module.ClientSession(
                connector=connector, timeout=timeout
            )
        return self._session

    async def close(self) -> None:
        """Close the shared aiohttp session, if active."""
        if (
            self._session is not None
            and getattr(self._session, "closed", False) is not True
        ):
            close_result = self._session.close()
            if asyncio.iscoroutine(close_result):
                await close_result
        self._session = None

    async def __aenter__(self) -> "AsyncDownloadCoreMixin":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
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
        Call a progress callback, handling both sync and async callbacks.
        """
        try:
            result = callback(downloaded, total, filename)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug(f"Progress callback error: {e}")

    async def _async_cleanup_temp_file(self, temp_path: Path) -> None:
        """
        Clean up a temporary file if it exists.
        """
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError as e:
            logger.debug(f"Error cleaning up temp file {temp_path}: {e}")

    async def _async_verify_existing_file(self, file_path: Path) -> bool:
        raise NotImplementedError

    async def _async_save_file_hash(self, file_path: Path) -> None:
        raise NotImplementedError

    async def async_download(
        self,
        url: str,
        target_path: Pathish,
        progress_callback: Optional[CoreProgressCallback] = None,
    ) -> bool:
        """
        Download a file asynchronously.

        Returns:
            bool: True if download succeeded.

        Raises:
            AsyncDownloadError: If download fails.
        """
        try:
            import aiofiles
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
                    if response.status >= 400:
                        raise AsyncDownloadError(
                            f"HTTP error {response.status}",
                            url=url,
                            status_code=response.status,
                            is_retryable=response.status >= 500,
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
            file_size_mb = downloaded / (1024 * 1024)
            logger.debug(f"Downloaded {url} in {elapsed:.2f}s")

            temp_path.replace(target)
            await self._async_save_file_hash(target)

            if file_size_mb >= 1.0:
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
                is_retryable=e.status >= 500,
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
        Download with retry logic and exponential backoff.

        Returns:
            bool: True if download succeeded, False otherwise.
        """
        attempts = max_retries if max_retries is not None else self._get_max_retries()
        delay = retry_delay if retry_delay is not None else self._get_retry_delay()
        last_error: Optional[AsyncDownloadError] = None

        for attempt in range(attempts + 1):
            try:
                result = await self.async_download(
                    url, target_path, progress_callback=progress_callback
                )
                if result:
                    return True

                if attempt == attempts:
                    logger.error(
                        f"Download failed permanently after {attempts + 1} attempts for {url}"
                    )
                    return False

                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s"
                )
            except AsyncDownloadError as e:
                last_error = e
                if not e.is_retryable:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    raise
                if attempt == attempts:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    return False
                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e.message}"
                )
            except Exception as e:
                last_error = AsyncDownloadError(
                    f"Unexpected error: {e}",
                    url=url,
                    is_retryable=True,
                )
                if attempt == attempts:
                    logger.error(
                        "Download failed permanently for %s: %s",
                        url,
                        last_error.message,
                    )
                    return False
                logger.warning(
                    f"Download attempt {attempt + 1}/{attempts + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e}"
                )

            await asyncio.sleep(delay)
            delay *= backoff_factor

        if last_error:
            logger.error("Download failed for %s: %s", url, last_error.message)
        return False
