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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from fetchtastic.constants import (
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_UNKNOWN,
    ERROR_TYPE_VALIDATION,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import calculate_sha256, save_file_hash

from .async_client import AsyncDownloadError
from .async_core import AsyncDownloadCoreMixin
from .files import is_zip_intact
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
        Compute the absolute filesystem path where a release asset should be saved, creating the release subdirectory if it does not exist.

        Parameters:
            release_tag (str): Release tag or version used to name the subdirectory; will be sanitized.
            file_name (str): Asset file name to save; will be sanitized.

        Returns:
            str: Absolute path to the target file within the downloader's download directory.

        Raises:
            ValueError: If `release_tag` or `file_name` contains unsafe path components and cannot be sanitized.
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
        Verify that a file is intact by performing applicable integrity checks.

        Performs a ZIP integrity check when the file has a ".zip" extension and verifies the file's persisted hash. On I/O errors or an invalid ZIP file, the function returns `false`.

        Returns:
            `true` if all applicable integrity checks pass, `false` otherwise.
        """
        import zipfile

        try:
            loop = asyncio.get_running_loop()
            if file_path.suffix.lower() == ".zip":
                if not await loop.run_in_executor(None, is_zip_intact, str(file_path)):
                    return False

            # Verify hash
            from fetchtastic.utils import verify_file_integrity

            return await loop.run_in_executor(
                None, verify_file_integrity, str(file_path)
            )

        except (OSError, zipfile.BadZipFile) as e:
            logger.debug("File verification failed for %s: %s", file_path, e)
            return False

    async def _async_save_file_hash(self, file_path: Path) -> None:
        """
        Compute the file's SHA-256 hash and persist it using save_file_hash.

        Parameters:
            file_path (Path): Path to the file whose SHA-256 hash will be computed and saved.
        """
        loop = asyncio.get_running_loop()

        def _compute_and_save() -> None:
            """
            Compute and persist the SHA-256 hash for the file referred to by `file_path` in the enclosing scope.

            If a hash is produced, saves it by calling `save_file_hash` with the file path string and the computed hash.
            """
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
        Download the specified asset from a release and produce a DownloadResult describing the outcome.

        Parameters:
            release (Release): Release object containing the asset; `tag_name` is used for target path and metadata.
            asset (Asset): Asset to download; uses `name`, `download_url`, and `size`.
            progress_callback (Optional[ProgressCallback]): Optional callback invoked with progress updates. Called as
                callback(downloaded_bytes: int, total_bytes: Optional[int], path: str).

        Returns:
            DownloadResult: On success, contains `success=True` and metadata (`release_tag`, `file_path`, `download_url`, `file_size`).
            On failure, contains `success=False` and error details (`error_message`, `error_type`, optional `http_status_code`, and `is_retryable`).
        """
        target_path: Optional[str] = None
        try:
            # Get target path - this method is from BaseDownloader or defined above
            target_path = self.get_target_path_for_release(release.tag_name, asset.name)

            await self.async_download_with_retry(
                asset.download_url,
                target_path,
                progress_callback=progress_callback,
            )
            return DownloadResult(
                success=True,
                release_tag=release.tag_name,
                file_path=Path(target_path),
                download_url=asset.download_url,
                file_size=asset.size,
            )
        except ValueError as e:
            logger.error("Error determining target path for %s: %s", asset.name, e)
            return DownloadResult(
                success=False,
                release_tag=release.tag_name,
                file_path=None,
                download_url=asset.download_url,
                error_message=str(e),
                error_type=ERROR_TYPE_VALIDATION,
                is_retryable=False,
            )
        except AsyncDownloadError as e:
            logger.error("Error downloading %s: %s", asset.name, e.message)
            return DownloadResult(
                success=False,
                release_tag=release.tag_name,
                file_path=Path(target_path) if target_path else None,
                download_url=asset.download_url,
                error_message=e.message,
                error_type=ERROR_TYPE_NETWORK,
                http_status_code=e.status_code,
                is_retryable=e.is_retryable,
            )
        except Exception as e:
            logger.exception("Error downloading %s: %s", asset.name, e)
            return DownloadResult(
                success=False,
                release_tag=release.tag_name,
                file_path=Path(target_path) if target_path else None,
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
        Concurrently download multiple release assets and return a per-spec DownloadResult.

        Parameters:
            downloads: Sequence of download specifications. Each spec should be a dict with keys "release" and "asset" where `release.tag_name` and `asset.name` identify the target and `asset.download_url` identifies the source when available.
            progress_callback: Optional per-download progress callback invoked with (bytes_received, total_bytes_or_None, status_message).

        Returns:
            A DownloadResult entry for each input spec in the same order. Successful entries have `success=True` and include metadata (release_tag, file_path, download_url, file_size); failed entries have `success=False` and include error details and whether the failure is considered retryable.
        """
        # Note: Concurrency is controlled by the semaphore in async_download_release
        # which calls async_download. Do NOT wrap with semaphore here to avoid deadlock.

        async def download_one(spec: Dict[str, Any]) -> DownloadResult:
            """
            Download a single release asset described by a download spec.

            Parameters:
                spec (Dict[str, Any]): Dictionary containing at least the keys `"release"` (a Release object or identifier) and `"asset"` (an Asset object or identifier); these are passed to the async_download_release method.

            Returns:
                DownloadResult: Result object describing success or failure and related metadata for the downloaded asset.
            """
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
            if isinstance(r, BaseException):
                spec: Any = downloads[i] if i < len(downloads) else None
                release_tag = "<unknown>"
                file_path: Optional[Pathish] = None
                download_url: Optional[str] = None
                is_retryable = False
                error_message = str(r)

                if isinstance(spec, dict):
                    release = spec.get("release")
                    asset = spec.get("asset")
                    tag_name = getattr(release, "tag_name", None)
                    asset_name = getattr(asset, "name", None)
                    asset_url = getattr(asset, "download_url", None)

                    if (
                        isinstance(tag_name, str)
                        and tag_name.strip()
                        and isinstance(asset_name, str)
                        and asset_name.strip()
                        and isinstance(asset_url, str)
                        and asset_url.strip()
                    ):
                        clean_tag_name = tag_name.strip()
                        clean_asset_name = asset_name.strip()
                        clean_asset_url = asset_url.strip()
                        release_tag = clean_tag_name
                        download_url = clean_asset_url
                        is_retryable = True
                        try:
                            file_path = Path(
                                self.get_target_path_for_release(
                                    clean_tag_name, clean_asset_name
                                )
                            )
                        except Exception:
                            file_path = None
                    else:
                        error_message = (
                            f"Invalid download spec: {spec!r}. Original error: {r}"
                        )

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
                final_results.append(r)
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
        Initialize the async downloader with an optional configuration and establish the download directory.

        Parameters:
            config (Optional[Dict[str, Any]]): Configuration dictionary; if provided, used as-is. The download directory is taken from `config["DOWNLOAD_DIR"]` when present, otherwise it defaults to `~/meshtastic`.
        """
        super().__init__()
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
    Download the file at the given URL to the specified local path while reporting progress.

    Parameters:
        url (str): The HTTP(S) URL to download.
        target_path (Pathish): Local path where the downloaded file will be saved.
        config (Optional[Dict[str, Any]]): Optional configuration for the downloader.
        progress_callback (Optional[ProgressCallback]): Callback invoked with
            (downloaded_bytes, total_bytes_or_None, filename) as progress updates occur.

    Returns:
        True if the download succeeded, False otherwise.

    Raises:
        AsyncDownloadError: If the download fails due to HTTP, network, or filesystem errors.
    """
    downloader = AsyncDownloaderBase(config)
    try:
        return await downloader.async_download(url, target_path, progress_callback)
    finally:
        await downloader.close()
