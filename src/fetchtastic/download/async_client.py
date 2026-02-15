"""
Async HTTP Client for Fetchtastic

This module provides asynchronous HTTP operations using aiohttp,
with proper session management, connection pooling, and error handling.

Provides both:
- AsyncGitHubClient: For GitHub API operations
- General async download capabilities
"""

import asyncio
import hashlib
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import aiofiles  # type: ignore[import-untyped]
import aiohttp
from aiohttp import (
    ClientResponse,
    ClientSession,
    ClientTimeout,
    TCPConnector,
)

from fetchtastic.constants import (
    API_CALL_DELAY,
    BYTES_PER_MEGABYTE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    FILE_SIZE_MB_LOGGING_THRESHOLD,
    HTTP_STATUS_ERROR_THRESHOLD,
    HTTP_STATUS_RETRY_THRESHOLD,
    RATE_LIMIT_REMAINING_DEFAULT,
)
from fetchtastic.log_utils import logger

from .interfaces import Asset, Pathish, Release


class AsyncDownloadError(Exception):
    """Exception raised for async download failures."""

    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        retry_count: int = 0,
        is_retryable: bool = False,
    ) -> None:
        """
        Create an AsyncDownloadError carrying structured details about a failed asynchronous download.

        Parameters:
            message (str): Human-readable error message.
            url (Optional[str]): The request URL that failed, if known.
            status_code (Optional[int]): HTTP status code associated with the failure, if any.
            retry_count (int): Number of retry attempts already performed.
            is_retryable (bool): True if the error is considered retryable, False otherwise.
        """
        super().__init__(message)
        self.message = message
        self.url = url
        self.status_code = status_code
        self.retry_count = retry_count
        self.is_retryable = is_retryable


class AsyncGitHubClient:
    """
    Asynchronous GitHub API client using aiohttp.

    Provides async methods for:
    - Fetching releases from GitHub API
    - Downloading files with progress tracking
    - Session management with connection pooling

    Example:
        async with AsyncGitHubClient() as client:
            releases = await client.get_releases(
                "https://api.github.com/repos/owner/repo/releases"
            )
    """

    def __init__(
        self,
        github_token: Optional[str] = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_concurrent: int = 5,
        connector_limit: int = 10,
    ) -> None:
        """
        Initialize the async GitHub client.

        Parameters:
            github_token (Optional[str]): GitHub personal access token for authentication.
            timeout (float): Request timeout in seconds.
            max_concurrent (int): Maximum concurrent downloads (semaphore limit).
            connector_limit (int): Maximum total connections in the pool.
        """

        def _clamp_positive(name: str, value: Any, default: int) -> int:
            """
            Normalize a value to a positive integer (minimum 1), falling back to a default on parse errors.

            Parameters:
                name (str): Identifier used in warning messages when logging invalid or clamped values.
                value (Any): Value to coerce to an integer.
                default (int): Fallback integer returned when `value` cannot be parsed as an int.

            Returns:
                int: The parsed integer if it is greater than or equal to 1; `default` if parsing fails; 1 if the parsed value is less than 1.

            Notes:
                Logs a warning when parsing fails or when a value is clamped to 1.
            """
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid %s value %r; using default of %d",
                    name,
                    value,
                    default,
                )
                return default
            if parsed <= 0:
                logger.warning("%s must be >= 1; clamping %d to 1", name, parsed)
                return 1
            return parsed

        self.github_token = github_token
        self.timeout = ClientTimeout(total=timeout)
        self.max_concurrent = _clamp_positive("max_concurrent", max_concurrent, 5)
        self.connector_limit = _clamp_positive("connector_limit", connector_limit, 10)
        self._session: Optional[ClientSession] = None
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._closed: bool = False

        # Rate limit tracking
        self._rate_limit_remaining: Dict[str, int] = {}
        self._rate_limit_reset: Dict[str, datetime] = {}

    async def __aenter__(self) -> "AsyncGitHubClient":
        """
        Enter the async context, ensuring the client session is initialized.

        Returns:
            AsyncGitHubClient: The initialized client instance.
        """
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Close the client's aiohttp session when exiting the async context.

        Ensures the underlying session is closed and the client is cleaned up.
        """
        await self.close()

    async def _ensure_session(self) -> ClientSession:
        """
        Ensure a session exists, creating one if needed.

        Returns:
            ClientSession: The active aiohttp session.
        """
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=self.connector_limit,
                limit_per_host=self.max_concurrent,
                enable_cleanup_closed=True,
            )
            self._session = ClientSession(
                connector=connector,
                timeout=self.timeout,
                headers=self._get_default_headers(),
            )
            # Semaphore is already created in __init__
        return self._session

    def _get_default_headers(self) -> Dict[str, str]:
        """
        Builds default HTTP headers for GitHub API requests.

        Includes Accept, GitHub API version, and User-Agent headers. If the client was configured with a GitHub token, includes an Authorization header.

        Returns:
            dict: HTTP headers to use for requests.
        """
        from fetchtastic.utils import get_user_agent

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": get_user_agent(),
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        return headers

    async def close(self) -> None:
        """Close the client session and release resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._closed = True
        # Clean up expired rate limit entries on close
        self._cleanup_expired_rate_limits()

    def _cleanup_expired_rate_limits(self) -> None:
        """
        Remove rate limit entries that have reset time in the past.

        This prevents unbounded growth of the rate limit tracking dictionaries
        by removing entries for tokens whose rate limit windows have expired.
        """
        now = datetime.now(timezone.utc)
        expired_keys = [
            token_hash
            for token_hash, reset_time in self._rate_limit_reset.items()
            if reset_time < now
        ]

        for key in expired_keys:
            self._rate_limit_remaining.pop(key, None)
            self._rate_limit_reset.pop(key, None)

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired rate limit entries")

    @asynccontextmanager
    async def _rate_limit_guard(self, token_hash: str) -> AsyncIterator[None]:
        """
        Prevent API calls when the per-token GitHub rate limit is exhausted.

        Parameters:
            token_hash (str): Identifier for the token's rate-limit state.

        Raises:
            AsyncDownloadError: If the token's remaining requests are zero and the reset time is in the future.
        """
        # Opportunistic cleanup of expired rate limit entries
        self._cleanup_expired_rate_limits()

        remaining = self._rate_limit_remaining.get(
            token_hash, RATE_LIMIT_REMAINING_DEFAULT
        )
        reset_time = self._rate_limit_reset.get(token_hash)

        if remaining == 0 and reset_time and reset_time > datetime.now(timezone.utc):
            raise AsyncDownloadError(
                f"GitHub API rate limit exceeded. Resets at {reset_time}",
                is_retryable=True,
            )
        yield

    async def get_releases(
        self,
        url: str,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Release]:
        """
        Fetch releases from GitHub API asynchronously.

        Parameters:
            url (str): GitHub API releases URL.
            limit (Optional[int]): Maximum number of releases to return.
            params (Optional[Dict[str, Any]]): Additional query parameters.

        Returns:
            List[Release]: List of Release objects, newest first.

        Raises:
            AsyncDownloadError: If the API request fails.
            ValueError: If `limit` is provided but is not an integer >= 0.
        """
        session = await self._ensure_session()

        # Create token hash for rate limit tracking
        token_hash = hashlib.sha256(
            (self.github_token or "no-token").encode()
        ).hexdigest()[:16]

        limit_int: Optional[int] = None
        if limit is not None:
            try:
                limit_int = int(limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"limit must be an integer >= 0, got {limit!r}"
                ) from exc
            if limit_int < 0:
                logger.warning(
                    "Negative limit %r requested for %s; treating as 0",
                    limit,
                    url,
                )
                limit_int = 0

        # Handle limit=0 explicitly - return empty list instead of making API call
        if limit_int == 0:
            logger.debug(f"limit=0 requested, returning empty list for {url}")
            return []

        request_params = dict(params) if params else {}
        if limit_int is not None:
            request_params["per_page"] = min(limit_int, 100)

        try:
            async with self._rate_limit_guard(token_hash):
                # Add small delay to be respectful to GitHub API
                await asyncio.sleep(API_CALL_DELAY)

                async with session.get(url, params=request_params) as response:
                    self._update_rate_limits(token_hash, response)

                    if response.status == 403:
                        raw_remaining = response.headers.get("X-RateLimit-Remaining")
                        try:
                            remaining = (
                                int(raw_remaining) if raw_remaining is not None else 0
                            )
                        except (TypeError, ValueError):
                            remaining = None
                        if remaining == 0:
                            reset_time = self._rate_limit_reset.get(token_hash)
                            raise AsyncDownloadError(
                                f"GitHub API rate limit exceeded. Resets at {reset_time}",
                                url=url,
                                status_code=403,
                                is_retryable=True,
                            )
                        raise AsyncDownloadError(
                            "GitHub API access forbidden",
                            url=url,
                            status_code=403,
                            is_retryable=False,
                        )

                    response.raise_for_status()
                    data = await response.json()

            if not isinstance(data, list):
                logger.warning(
                    "Unexpected releases payload type from %s: expected list, got %s",
                    url,
                    type(data).__name__,
                )
                return []

            releases = []
            for item in data:
                if not isinstance(item, dict):
                    logger.warning(
                        "Skipping malformed release entry from %s: expected dict, got %s",
                        url,
                        type(item).__name__,
                    )
                    continue

                tag_name = item.get("tag_name", "")
                if not isinstance(tag_name, str):
                    logger.warning(
                        "Skipping release entry from %s with invalid tag_name type %s",
                        url,
                        type(tag_name).__name__,
                    )
                    continue
                tag_name = tag_name.strip()
                if not tag_name:
                    logger.warning(
                        "Skipping release entry from %s with invalid or empty tag_name",
                        url,
                    )
                    continue

                assets_data = item.get("assets", [])
                if not isinstance(assets_data, list):
                    logger.warning(
                        "Skipping assets for release %s due to invalid assets type %s",
                        tag_name or "<unknown>",
                        type(assets_data).__name__,
                    )
                    assets_data = []

                parsed_assets: List[Asset] = []
                for asset in assets_data:
                    if not isinstance(asset, dict):
                        logger.warning(
                            "Skipping malformed asset in release %s: expected dict, got %s",
                            tag_name or "<unknown>",
                            type(asset).__name__,
                        )
                        continue
                    asset_name = asset.get("name", "")
                    if not isinstance(asset_name, str):
                        logger.warning(
                            "Skipping asset in release %s with invalid name type %s",
                            tag_name or "<unknown>",
                            type(asset_name).__name__,
                        )
                        continue
                    raw_size = asset.get("size", 0)
                    try:
                        asset_size = int(raw_size)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Using size=0 for asset %s in release %s due to invalid size value",
                            asset_name or "<unknown>",
                            tag_name or "<unknown>",
                        )
                        asset_size = 0
                    browser_download_url = asset.get("browser_download_url")
                    if not isinstance(browser_download_url, str):
                        browser_download_url = ""
                    parsed_assets.append(
                        Asset(
                            name=asset_name,
                            download_url=browser_download_url,
                            size=asset_size,
                            browser_download_url=browser_download_url or None,
                            content_type=asset.get("content_type"),
                        )
                    )

                release = Release(
                    tag_name=tag_name,
                    prerelease=item.get("prerelease", False),
                    published_at=item.get("published_at"),
                    name=item.get("name"),
                    body=item.get("body"),
                    assets=parsed_assets,
                )
                releases.append(release)

            logger.debug(f"Fetched {len(releases)} releases from {url}")
            return releases

        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP error fetching releases from {url}: {e.status}")
            raise AsyncDownloadError(
                f"HTTP error {e.status}: {e.message}",
                url=url,
                status_code=e.status,
                is_retryable=e.status >= HTTP_STATUS_RETRY_THRESHOLD,
            ) from e
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching releases from {url}: {e}")
            raise AsyncDownloadError(
                f"Network error: {e}",
                url=url,
                is_retryable=True,
            ) from e

    def _update_rate_limits(self, token_hash: str, response: ClientResponse) -> None:
        """
        Parse rate-limit headers from an HTTP response and update the client's per-token rate-limit state.

        Parameters:
            token_hash (str): Key identifying the token whose rate-limit state will be updated.
            response (ClientResponse): HTTP response from which `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers are read.
        """
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")

        if remaining:
            try:
                self._rate_limit_remaining[token_hash] = int(remaining)
            except (ValueError, TypeError):
                pass

        if reset:
            try:
                self._rate_limit_reset[token_hash] = datetime.fromtimestamp(
                    int(reset), tz=timezone.utc
                )
            except (ValueError, TypeError, OSError):
                pass

    async def download_file(
        self,
        url: str,
        target_path: Pathish,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_callback: Optional[Any] = None,
    ) -> bool:
        """
        Download a file to the given path with progress tracking and atomic replacement.

        Parameters:
            url (str): Source URL to download.
            target_path (Pathish): Destination file path; parent directories will be created if missing.
            chunk_size (int): Number of bytes to read per chunk.
            progress_callback (Optional[callable]): Optional callback invoked with (downloaded: int, total: Optional[int], filename: str).
                The callback may be a coroutine function; exceptions raised by the callback are logged and ignored.

        Returns:
            bool: `True` if the file was successfully downloaded and moved to `target_path`.

        Raises:
            AsyncDownloadError: On HTTP, network, filesystem, or unexpected failures. The exception carries `url`, `status_code` (when available),
                `is_retryable`, and `retry_count` metadata. Temporary files are removed on error.
        """
        session = await self._ensure_session()
        target = Path(target_path)

        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        # Use semaphore to limit concurrent downloads
        async with self._semaphore:
            temp_path = target.with_suffix(
                f".tmp.{os.getpid()}.{int(time.time() * 1000)}"
            )

            try:
                start_time = time.time()

                async with session.get(url) as response:
                    if response.status >= HTTP_STATUS_ERROR_THRESHOLD:
                        raise AsyncDownloadError(
                            f"HTTP error {response.status}",
                            url=url,
                            status_code=response.status,
                            is_retryable=response.status >= HTTP_STATUS_RETRY_THRESHOLD,
                        )

                    raw_content_length = response.headers.get("Content-Length")
                    try:
                        total_size = (
                            int(raw_content_length) if raw_content_length else 0
                        )
                    except (TypeError, ValueError):
                        total_size = 0
                    downloaded = 0

                    async with aiofiles.open(temp_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            await f.write(chunk)
                            downloaded += len(chunk)

                            if progress_callback:
                                try:
                                    result = progress_callback(
                                        downloaded, total_size or None, target.name
                                    )
                                    if asyncio.iscoroutine(result):
                                        await result
                                except Exception as cb_err:
                                    logger.debug(f"Progress callback error: {cb_err}")

                elapsed = time.time() - start_time
                file_size_mb = downloaded / BYTES_PER_MEGABYTE
                logger.debug(
                    f"Downloaded {url} in {elapsed:.2f}s ({file_size_mb:.2f} MB)"
                )

                # Atomic replace to handle existing targets across platforms
                temp_path.replace(target)

                if file_size_mb >= FILE_SIZE_MB_LOGGING_THRESHOLD:
                    logger.info(f"Downloaded: {target.name} ({file_size_mb:.1f} MB)")
                else:
                    logger.info(f"Downloaded: {target.name} ({downloaded} bytes)")

                return True

            except aiohttp.ClientError as e:
                logger.error(f"Download failed for {url}: {e}")
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise AsyncDownloadError(
                    f"Download failed: {e}",
                    url=url,
                    is_retryable=True,
                ) from e
            except OSError as e:
                logger.error(f"Filesystem error saving {target_path}: {e}")
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise AsyncDownloadError(
                    f"Filesystem error: {e}",
                    url=url,
                    is_retryable=False,
                ) from e
            except AsyncDownloadError:
                # Re-raise AsyncDownloadError without wrapping it, but clean up temp file first
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise
            except Exception as e:
                # Catch-all for unexpected exceptions - clean up temp file
                logger.exception(f"Unexpected error downloading {url}: {e}")
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
                raise AsyncDownloadError(
                    f"Unexpected error: {e}",
                    url=url,
                    is_retryable=True,
                ) from e

    async def download_file_with_retry(
        self,
        url: str,
        target_path: Pathish,
        max_retries: int = DEFAULT_CONNECT_RETRIES,
        retry_delay: float = 1.0,
        backoff_factor: float = 2.0,
        progress_callback: Optional[Any] = None,
    ) -> bool:
        """
        Attempt to download a URL to a local path, retrying with exponential backoff on retryable failures.

        Parameters:
            url (str): Source URL to download.
            target_path (Pathish): Destination path where the file will be written.
            max_retries (int): Maximum number of retry attempts after the initial try.
            retry_delay (float): Initial delay in seconds before the first retry.
            backoff_factor (float): Multiplier applied to the delay after each failed attempt.
            progress_callback (Optional[Any]): Optional callable or coroutine called with progress updates.

        Returns:
            bool: `True` if the file was downloaded successfully.

        Raises:
            AsyncDownloadError: If a non-retryable error occurs or all retry attempts are exhausted.
        """
        last_error: Optional[Exception] = None
        delay = retry_delay

        for attempt in range(max_retries + 1):
            try:
                return await self.download_file(
                    url, target_path, progress_callback=progress_callback
                )
            except AsyncDownloadError as e:
                last_error = e

                if not e.is_retryable or attempt == max_retries:
                    logger.error(f"Download failed permanently for {url}: {e.message}")
                    raise

                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries + 1} failed for {url}, "
                    f"retrying in {delay:.1f}s: {e.message}"
                )
                await asyncio.sleep(delay)
                delay *= backoff_factor

        # Should not reach here, but satisfy type checker
        if last_error:
            raise last_error
        return False


@asynccontextmanager
async def create_async_client(
    github_token: Optional[str] = None,
    max_concurrent: int = 5,
) -> AsyncIterator[AsyncGitHubClient]:
    """
    Provide a configured AsyncGitHubClient and ensure it is closed after use.

    Parameters:
        github_token (Optional[str]): GitHub personal access token used for Authorization header; if `None`, requests are unauthenticated.
        max_concurrent (int): Maximum number of concurrent network operations the client will allow.

    Returns:
        AsyncGitHubClient: Configured client instance; it will be closed when the context manager exits.
    """
    client = AsyncGitHubClient(github_token=github_token, max_concurrent=max_concurrent)
    try:
        yield client
    finally:
        await client.close()


async def download_files_concurrently(
    downloads: List[Dict[str, Any]],
    max_concurrent: int = 5,
    progress_callback: Optional[Any] = None,
    github_token: Optional[str] = None,
) -> List[Any]:
    """
    Download multiple files concurrently with a semaphore limit.

    Parameters:
        downloads (List[Dict[str, Any]]): List of download specs with 'url' and 'target_path'.
        max_concurrent (int): Maximum concurrent downloads.
        progress_callback (Optional[Any]): Optional progress callback for each download.
        github_token (Optional[str]): Optional GitHub token for authenticated downloads.
            Useful for private release assets and higher API rate limits.

    Returns:
        List[Any]: Results for each download. Each element is:
            - True: Download succeeded
            - False: Download failed without a specific exception
            - Exception: The exception that caused the failure (typically AsyncDownloadError)
            - ValueError: Invalid download spec (missing/invalid 'url' or 'target_path')
            This allows callers to inspect the root cause of failures.

    Example:
        downloads = [
            {"url": "https://...", "target_path": "/path/file1.bin"},
            {"url": "https://...", "target_path": "/path/file2.bin"},
        ]
        results = await download_files_concurrently(
            downloads,
            max_concurrent=3,
            github_token="ghp_...",
        )
        for i, result in enumerate(results):
            if result is True:
                print(f"Download {i} succeeded")
            elif isinstance(result, Exception):
                print(f"Download {i} failed: {result}")
            else:
                print(f"Download {i} failed")
    """
    async with create_async_client(
        github_token=github_token,
        max_concurrent=max_concurrent,
    ) as client:
        results: List[Any] = [None] * len(downloads)
        task_indexes: List[int] = []
        tasks: List[Any] = []

        for index, spec in enumerate(downloads):
            if not isinstance(spec, dict):
                results[index] = ValueError(
                    f"Invalid download spec at index {index}: expected dict"
                )
                continue

            url = spec.get("url")
            target_path = spec.get("target_path")
            if not isinstance(url, str) or not url.strip():
                results[index] = ValueError(
                    f"Invalid download spec at index {index}: missing/invalid 'url'"
                )
                continue
            if not isinstance(target_path, (str, Path)):
                results[index] = ValueError(
                    f"Invalid download spec at index {index}: missing/invalid 'target_path'"
                )
                continue

            task_indexes.append(index)
            tasks.append(
                client.download_file(
                    url,
                    target_path,
                    progress_callback=progress_callback,
                )
            )

        if tasks:
            gathered_results = await asyncio.gather(*tasks, return_exceptions=True)
            for index, task_result in zip(task_indexes, gathered_results, strict=True):
                results[index] = task_result

        # Return raw results to preserve exception information for debugging
        return results
