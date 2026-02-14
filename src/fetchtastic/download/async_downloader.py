"""
Async Downloader Mixin for Fetchtastic

This module provides an AsyncDownloader mixin that adds async download
capabilities to downloader classes while maintaining backward compatibility
with synchronous operations.

Design:
- Mixin pattern to be combined with BaseDownloader
- Semaphore-based concurrency control
- Progress tracking for async operations
- Retry logic with exponential backoff
"""

import asyncio
import os
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import aiofiles
import aiohttp

from fetchtastic.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_UNKNOWN,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import calculate_sha256, save_file_hash

from .async_client import AsyncDownloadError
from .interfaces import Asset, DownloadResult, Pathish, Release

if TYPE_CHECKING:
    from .files import FileOperations

# Type alias for progress callback - accepts downloaded bytes, total bytes (or None), and filename
# Returns Any to allow both sync and async callbacks
ProgressCallback = Callable[[int, Optional[int], str], Any]


class AsyncDownloaderMixin:
    """
    Mixin class that adds async download capabilities to downloader classes.

    This mixin is designed to be combined with BaseDownloader and provides:
    - Async file download with progress tracking
    - Concurrent download management via semaphore
    - Retry logic with exponential backoff
    - Integration with the existing file operations

    Usage:
        class MyDownloader(BaseDownloader, AsyncDownloaderMixin):
            async def async_download_release(self, release):
                return await self.async_download_file(
                    release.download_url, target_path
                )
    """

    # These attributes are expected from BaseDownloader
    # Declared for type checking - actual values come from BaseDownloader
    config: Dict[str, Any]
    file_operations: "FileOperations"
    download_dir: str

    # Instance-level semaphore for proper concurrency control across downloads
    _semaphore: Optional[asyncio.Semaphore] = None
    _session: Optional[aiohttp.ClientSession] = None

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Get the target path for a release file.

        This method is expected to be provided by BaseDownloader.
        Override in subclass if not using BaseDownloader.

        Parameters:
            release_tag (str): The release tag/version.
            file_name (str): The filename of the asset.

        Returns:
            str: Full path where the file should be saved.

        Raises:
            ValueError: If the release_tag or file_name contains unsafe path components.
        """
        from .files import _sanitize_path_component

        safe_release = _sanitize_path_component(release_tag)
        safe_name = _sanitize_path_component(file_name)

        if safe_release is None:
            raise ValueError(
                f"Unsafe release tag provided; aborting to avoid path traversal: {release_tag!r}"
            )
        if safe_name is None:
            raise ValueError(
                f"Unsafe file name provided; aborting to avoid path traversal: {file_name!r}"
            )

        version_dir = os.path.join(self.download_dir, safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

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
        return int(self.config.get("MAX_DOWNLOAD_RETRIES", DEFAULT_CONNECT_RETRIES))

    def _get_retry_delay(self) -> float:
        """
        Get the initial retry delay from config.

        Returns:
            float: Initial retry delay in seconds.
        """
        return float(self.config.get("DOWNLOAD_RETRY_DELAY", 1.0))

    def _get_semaphore(self) -> asyncio.Semaphore:
        """
        Get or create the semaphore for concurrency control.

        The semaphore is created once per instance and reused for all downloads,
        ensuring proper concurrency limiting across multiple async_download calls.

        Returns:
            asyncio.Semaphore: Semaphore for limiting concurrent downloads.
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._get_max_concurrent())
        return self._semaphore

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create or return a reusable aiohttp session for downloads."""
        if self._session is None or getattr(self._session, "closed", False) is True:
            connector = aiohttp.TCPConnector(limit=self._get_max_concurrent())
            timeout = aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
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

    async def __aenter__(self) -> "AsyncDownloaderMixin":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def async_download(
        self,
        url: str,
        target_path: Pathish,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> bool:
        """
        Download a file asynchronously.

        Parameters:
            url (str): URL to download from.
            target_path (Pathish): Local path to save the file.
            progress_callback (Optional[ProgressCallback]):
                Optional callback for progress updates.

        Returns:
            bool: True if download succeeded, False otherwise.

        Raises:
            AsyncDownloadError: If download fails.
        """
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Check if file already exists and is valid
        if target.exists():
            if await self._async_verify_existing_file(target):
                logger.info(f"Skipped: {target.name} (already present & verified)")
                return True

        temp_path = target.with_suffix(f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
        downloaded = 0

        try:
            start_time = time.time()
            session = await self._ensure_session()

            # Use instance-level semaphore for proper concurrency control
            async with self._get_semaphore():
                async with session.get(url) as response:
                    response.raise_for_status()

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

            # Atomic replace to handle existing targets across platforms
            temp_path.replace(target)

            # Generate and save hash
            await self._async_save_file_hash(target)

            if file_size_mb >= 1.0:
                logger.info(f"Downloaded: {target.name} ({file_size_mb:.1f} MB)")
            else:
                logger.info(f"Downloaded: {target.name} ({downloaded} bytes)")

            return True

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

    async def _call_progress_callback(
        self,
        callback: ProgressCallback,
        downloaded: int,
        total: Optional[int],
        filename: str,
    ) -> None:
        """
        Call a progress callback, handling both sync and async callbacks.

        Parameters:
            callback: The callback function (sync or async).
            downloaded (int): Bytes downloaded so far.
            total (Optional[int]): Total bytes or None if unknown.
            filename (str): Name of the file being downloaded.
        """
        try:
            result = callback(downloaded, total, filename)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug(f"Progress callback error: {e}")

    async def _async_verify_existing_file(self, file_path: Path) -> bool:
        """
        Verify an existing file asynchronously.

        Parameters:
            file_path (Path): Path to the file to verify.

        Returns:
            bool: True if file is valid, False otherwise.
        """
        try:
            if file_path.suffix.lower() == ".zip":
                # ZipFile is sync, run in executor for non-blocking
                loop = asyncio.get_running_loop()

                def check_zip() -> bool:
                    try:
                        with zipfile.ZipFile(file_path, "r") as zf:
                            return zf.testzip() is None
                    except zipfile.BadZipFile:
                        return False

                if not await loop.run_in_executor(None, check_zip):
                    return False

            # Verify hash
            from fetchtastic.utils import verify_file_integrity

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, verify_file_integrity, str(file_path)
            )

        except (OSError, zipfile.BadZipFile) as e:
            logger.debug(f"File verification failed for {file_path}: {e}")
            return False

    async def _async_save_file_hash(self, file_path: Path) -> None:
        """
        Calculate and save file hash asynchronously.

        Parameters:
            file_path (Path): Path to the file to hash.
        """
        loop = asyncio.get_running_loop()

        def _compute_and_save() -> None:
            hash_value = calculate_sha256(str(file_path))
            if hash_value:
                save_file_hash(str(file_path), hash_value)

        await loop.run_in_executor(None, _compute_and_save)

    async def _async_cleanup_temp_file(self, temp_path: Path) -> None:
        """
        Clean up a temporary file if it exists.

        Parameters:
            temp_path (Path): Path to the temporary file.
        """
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError as e:
            logger.debug(f"Error cleaning up temp file {temp_path}: {e}")

    async def async_download_with_retry(
        self,
        url: str,
        target_path: Pathish,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        backoff_factor: float = 2.0,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> bool:
        """
        Download a file with retry logic and exponential backoff.

        Parameters:
            url (str): URL to download from.
            target_path (Pathish): Local path to save the file.
            max_retries (Optional[int]): Maximum retry attempts.
            retry_delay (Optional[float]): Initial retry delay in seconds.
            backoff_factor (float): Multiplier for delay after each retry.
            progress_callback (Optional[ProgressCallback]):
                Optional progress callback.

        Returns:
            bool: True if download succeeded, False otherwise.
        """
        max_retries = (
            max_retries if max_retries is not None else self._get_max_retries()
        )
        delay = retry_delay if retry_delay is not None else self._get_retry_delay()
        last_error: Optional[AsyncDownloadError] = None

        for attempt in range(max_retries + 1):
            try:
                result = await self.async_download(
                    url, target_path, progress_callback=progress_callback
                )
                # Backward compatibility: treat explicit False as a retryable failure.
                if result:
                    return True

                if attempt == max_retries:
                    logger.error(
                        f"Download failed permanently after {max_retries + 1} attempts for {url}"
                    )
                    return False
                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s"
                )
            except AsyncDownloadError as e:
                last_error = e
                if not e.is_retryable or attempt == max_retries:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    return False
                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e.message}"
                )
            except Exception as e:
                last_error = AsyncDownloadError(
                    f"Unexpected error: {e}",
                    url=url,
                    is_retryable=True,
                )
                if attempt == max_retries:
                    logger.error(
                        "Download failed permanently for %s: %s",
                        url,
                        last_error.message,
                    )
                    return False
                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e}"
                )

            await asyncio.sleep(delay)
            delay *= backoff_factor

        if last_error:
            logger.error("Download failed for %s: %s", url, last_error.message)
        return False

    async def async_download_release(
        self,
        release: Release,
        asset: Asset,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        """
        Download a release asset asynchronously.

        Parameters:
            release (Release): The release containing the asset.
            asset (Asset): The asset to download.
            progress_callback (Optional[ProgressCallback]):
                Optional progress callback.

        Returns:
            DownloadResult: Result of the download operation.
        """
        # Get target path - this method is from BaseDownloader or defined above
        target_path = self.get_target_path_for_release(release.tag_name, asset.name)

        try:
            success = await self.async_download_with_retry(
                asset.download_url,
                target_path,
                progress_callback=progress_callback,
            )

            if success:
                return DownloadResult(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=Path(target_path),
                    download_url=asset.download_url,
                    file_size=asset.size,
                )
            else:
                return DownloadResult(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=Path(target_path),
                    download_url=asset.download_url,
                    error_message="Download failed",
                    error_type=ERROR_TYPE_NETWORK,
                    is_retryable=True,
                )

        except Exception as e:
            logger.exception(f"Error downloading {asset.name}: {e}")
            return DownloadResult(
                success=False,
                release_tag=release.tag_name,
                file_path=Path(target_path),
                download_url=asset.download_url,
                error_message=str(e),
                error_type=ERROR_TYPE_UNKNOWN,
                is_retryable=True,
            )

    async def async_download_multiple(
        self,
        downloads: List[Dict[str, Any]],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[DownloadResult]:
        """
        Download multiple files concurrently.

        Parameters:
            downloads (List[Dict[str, Any]]): List of download specs with 'release' and 'asset'.
            progress_callback (Optional[ProgressCallback]):
                Optional progress callback for each download.

        Returns:
            List[DownloadResult]: Results for each download.
        """
        # Note: Concurrency is controlled by the semaphore in async_download_release
        # which calls async_download. Do NOT wrap with semaphore here to avoid deadlock.

        async def download_one(spec: Dict[str, Any]) -> DownloadResult:
            return await self.async_download_release(
                spec["release"],
                spec["asset"],
                progress_callback=progress_callback,
            )

        tasks = [download_one(spec) for spec in downloads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Convert exceptions to DownloadResult with error
        final_results: List[DownloadResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                spec = downloads[i]
                final_results.append(
                    DownloadResult(
                        success=False,
                        release_tag=spec["release"].tag_name,
                        file_path=Path(
                            self.get_target_path_for_release(
                                spec["release"].tag_name, spec["asset"].name
                            )
                        ),
                        download_url=spec["asset"].download_url,
                        error_message=str(r),
                        error_type=ERROR_TYPE_UNKNOWN,
                        is_retryable=True,
                    )
                )
            else:
                final_results.append(r)  # type: ignore[arg-type]
        return final_results


class AsyncDownloaderBase(AsyncDownloaderMixin):
    """
    Standalone async downloader base class.

    This class combines AsyncDownloaderMixin with basic initialization
    for use as a standalone async downloader without inheriting from
    BaseDownloader.

    For full functionality, combine AsyncDownloaderMixin with BaseDownloader.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize the async downloader.

        Parameters:
            config (Optional[Dict[str, Any]]): Configuration dictionary.
        """
        self.config = config or {}
        self.download_dir = self.config.get(
            "DOWNLOAD_DIR", os.path.expanduser("~/meshtastic")
        )


async def download_with_progress(
    url: str,
    target_path: Pathish,
    config: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> bool:
    """
    Convenience function for async download with progress tracking.

    Parameters:
        url (str): URL to download from.
        target_path (Pathish): Local path to save the file.
        config (Optional[Dict[str, Any]]): Optional configuration.
        progress_callback (Optional[ProgressCallback]): Optional progress callback.

    Returns:
        bool: True if download succeeded, False otherwise.

    Example:
        async def progress(downloaded, total, filename):
            if total:
                percent = (downloaded / total) * 100
                print(f"{filename}: {percent:.1f}%")

        await download_with_progress(
            "https://example.com/file.bin",
            "/path/to/file.bin",
            progress_callback=progress
        )
    """
    downloader = AsyncDownloaderBase(config)
    try:
        return await downloader.async_download(url, target_path, progress_callback)
    finally:
        await downloader.close()
