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
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from fetchtastic.constants import (
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_UNKNOWN,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import calculate_sha256, save_file_hash

from .async_client import AsyncDownloadError
from .async_core import AsyncDownloadCoreMixin
from .interfaces import Asset, DownloadResult, Pathish, Release

if TYPE_CHECKING:
    from .files import FileOperations

# Type alias for progress callback - accepts downloaded bytes, total bytes (or None), and filename
# Returns Any to allow both sync and async callbacks
ProgressCallback = Callable[[int, Optional[int], str], Any]


class AsyncDownloaderMixin(AsyncDownloadCoreMixin):
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

    # Instance-level semaphore/session for proper concurrency control across downloads
    _semaphore: Optional[asyncio.Semaphore] = None
    _session: Optional[Any] = None

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
        except AsyncDownloadError as e:
            logger.error("Error downloading %s: %s", asset.name, e.message)
            return DownloadResult(
                success=False,
                release_tag=release.tag_name,
                file_path=Path(target_path),
                download_url=asset.download_url,
                error_message=e.message,
                error_type=ERROR_TYPE_NETWORK,
                http_status_code=e.status_code,
                is_retryable=e.is_retryable,
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
                spec: Any = downloads[i] if i < len(downloads) else None
                release_tag = "<unknown>"
                file_path: Pathish = Path("<unknown>")
                download_url: Optional[str] = None
                is_retryable = False
                error_message = str(r)

                if isinstance(spec, dict):
                    release = spec.get("release")
                    asset = spec.get("asset")

                    if (
                        release is not None
                        and asset is not None
                        and isinstance(getattr(release, "tag_name", None), str)
                        and isinstance(getattr(asset, "name", None), str)
                        and isinstance(getattr(asset, "download_url", None), str)
                    ):
                        release_tag = release.tag_name
                        download_url = asset.download_url
                        is_retryable = True
                        try:
                            file_path = Path(
                                self.get_target_path_for_release(
                                    release.tag_name, asset.name
                                )
                            )
                        except Exception:
                            file_path = Path("<unknown>")
                    else:
                        error_message = f"Invalid download spec: {r}"

                final_results.append(
                    DownloadResult(
                        success=False,
                        release_tag=release_tag,
                        file_path=file_path,
                        download_url=download_url,
                        error_message=error_message,
                        error_type=ERROR_TYPE_UNKNOWN,
                        is_retryable=is_retryable,
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
