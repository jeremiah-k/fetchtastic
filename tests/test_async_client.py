"""
Comprehensive tests for async_client.py module.

Tests the async HTTP client functionality including:
- AsyncDownloadError exception class
- AsyncGitHubClient initialization and configuration
- Context manager protocol
- Session management
- Rate limit handling
- GitHub API operations (get_releases)
- File download operations with progress tracking
- Retry logic with exponential backoff
- Factory functions and utility functions
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from fetchtastic.download.async_client import (
    AsyncDownloadError,
    AsyncGitHubClient,
    create_async_client,
    download_files_concurrently,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


# =============================================================================
# AsyncDownloadError Tests (lines 43-75)
# =============================================================================


class TestAsyncDownloadError:
    """Test AsyncDownloadError exception class."""

    def test_init_with_message_only(self):
        """Test initialization with just a message."""
        error = AsyncDownloadError("Download failed")

        assert str(error) == "Download failed"
        assert error.message == "Download failed"
        assert error.url is None
        assert error.status_code is None
        assert error.retry_count == 0
        assert error.is_retryable is False

    def test_init_with_all_parameters(self):
        """Test initialization with all parameters."""
        error = AsyncDownloadError(
            message="Connection timeout",
            url="https://example.com/file.bin",
            status_code=504,
            retry_count=3,
            is_retryable=True,
        )

        assert error.message == "Connection timeout"
        assert error.url == "https://example.com/file.bin"
        assert error.status_code == 504
        assert error.retry_count == 3
        assert error.is_retryable is True

    def test_init_with_url_and_status(self):
        """Test initialization with URL and status code."""
        error = AsyncDownloadError(
            message="Not found",
            url="https://example.com/missing.bin",
            status_code=404,
        )

        assert error.url == "https://example.com/missing.bin"
        assert error.status_code == 404
        assert error.is_retryable is False  # 4xx errors not retryable by default


# =============================================================================
# AsyncGitHubClient Initialization Tests (lines 94-121)
# =============================================================================


class TestAsyncGitHubClientInitialization:
    """Test AsyncGitHubClient initialization."""

    def test_init_default_parameters(self):
        """Test initialization with default parameters."""
        client = AsyncGitHubClient()

        assert client.github_token is None
        assert client.max_concurrent == 5
        assert client.connector_limit == 10
        assert client._session is None
        assert client._semaphore is not None  # Semaphore is now created in __init__
        assert client._closed is False
        assert client._rate_limit_remaining == {}
        assert client._rate_limit_reset == {}

    def test_init_with_token(self):
        """Test initialization with GitHub token."""
        client = AsyncGitHubClient(github_token="ghp_test_token")

        assert client.github_token == "ghp_test_token"

    def test_init_with_custom_parameters(self):
        """Test initialization with custom parameters."""
        client = AsyncGitHubClient(
            github_token="test_token",
            timeout=60.0,
            max_concurrent=10,
            connector_limit=20,
        )

        assert client.github_token == "test_token"
        assert client.max_concurrent == 10
        assert client.connector_limit == 20
        # timeout is stored as ClientTimeout object
        assert client.timeout.total == 60.0


# =============================================================================
# Async Context Manager Tests (lines 122-134)
# =============================================================================


@pytest.mark.asyncio
class TestAsyncGitHubClientContextManager:
    """Test AsyncGitHubClient async context manager protocol."""

    async def test_aenter_returns_client(self, mocker):
        """Test __aenter__ returns the client instance."""
        client = AsyncGitHubClient()
        mock_ensure = mocker.patch.object(
            client, "_ensure_session", new_callable=AsyncMock
        )
        mock_ensure.return_value = mocker.MagicMock()

        result = await client.__aenter__()

        assert result is client
        mock_ensure.assert_called_once()

    async def test_aexit_closes_session(self, mocker):
        """Test __aexit__ calls close method."""
        client = AsyncGitHubClient()
        mock_close = mocker.patch.object(client, "close", new_callable=AsyncMock)

        await client.__aexit__(None, None, None)

        mock_close.assert_called_once()

    async def test_close_with_session(self, mocker):
        """Test close method with active session."""
        client = AsyncGitHubClient()
        mock_session = mocker.MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.close()

        mock_session.close.assert_called_once()
        assert client._session is None
        assert client._closed is True

    async def test_close_without_session(self, mocker):
        """Test close method when no session exists."""
        client = AsyncGitHubClient()
        client._session = None

        await client.close()

        assert client._closed is True


# =============================================================================
# Session Management Tests (lines 136-155)
# =============================================================================


@pytest.mark.asyncio
class TestEnsureSession:
    """Test _ensure_session method."""

    async def test_ensure_session_creates_new_session(self, mocker):
        """Test that _ensure_session creates a new session when none exists."""
        client = AsyncGitHubClient()
        mocker.patch.object(
            client, "_get_default_headers", return_value={"User-Agent": "test"}
        )

        # Mock the TCPConnector and ClientSession constructors
        with (
            patch("fetchtastic.download.async_client.TCPConnector") as mock_connector,
            patch(
                "fetchtastic.download.async_client.ClientSession"
            ) as mock_session_cls,
        ):
            mock_session = mocker.MagicMock()
            mock_session.closed = False
            mock_session_cls.return_value = mock_session

            session = await client._ensure_session()

            assert session is mock_session
            assert client._session is mock_session
            assert client._semaphore is not None
            mock_connector.assert_called_once()

    async def test_ensure_session_recreates_when_closed(self, mocker):
        """Test that _ensure_session recreates session when existing one is closed."""
        client = AsyncGitHubClient()
        mock_closed_session = mocker.MagicMock()
        mock_closed_session.closed = True
        client._session = mock_closed_session

        mocker.patch.object(
            client, "_get_default_headers", return_value={"User-Agent": "test"}
        )

        with (
            patch("fetchtastic.download.async_client.TCPConnector"),
            patch(
                "fetchtastic.download.async_client.ClientSession"
            ) as mock_session_cls,
        ):
            mock_new_session = mocker.MagicMock()
            mock_new_session.closed = False
            mock_session_cls.return_value = mock_new_session

            session = await client._ensure_session()

            assert session is mock_new_session
            assert client._session is mock_new_session


# =============================================================================
# Headers Tests (lines 157-173)
# =============================================================================


class TestGetDefaultHeaders:
    """Test _get_default_headers method."""

    def test_headers_without_token(self, mocker):
        """Test default headers without GitHub token."""
        client = AsyncGitHubClient(github_token=None)
        mocker.patch("fetchtastic.utils.get_user_agent", return_value="Fetchtastic/1.0")

        headers = client._get_default_headers()

        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert headers["User-Agent"] == "Fetchtastic/1.0"
        assert "Authorization" not in headers

    def test_headers_with_token(self, mocker):
        """Test default headers include authorization when token is provided."""
        client = AsyncGitHubClient(github_token="ghp_test_token")
        mocker.patch("fetchtastic.utils.get_user_agent", return_value="Fetchtastic/1.0")

        headers = client._get_default_headers()

        assert headers["Authorization"] == "token ghp_test_token"


# =============================================================================
# Rate Limit Guard Tests (lines 182-204)
# =============================================================================


@pytest.mark.asyncio
class TestRateLimitGuard:
    """Test _rate_limit_guard context manager."""

    async def test_rate_limit_guard_allows_when_remaining(self, mocker):
        """Test rate limit guard allows request when remaining > 0."""
        client = AsyncGitHubClient()
        token_hash = "abc123"
        client._rate_limit_remaining[token_hash] = 10
        client._rate_limit_reset[token_hash] = datetime.now(timezone.utc)

        async with client._rate_limit_guard(token_hash):
            pass  # Should not raise

    async def test_rate_limit_guard_raises_when_exceeded(self, mocker):
        """Test rate limit guard raises error when limit exceeded."""
        client = AsyncGitHubClient()
        token_hash = "abc123"
        client._rate_limit_remaining[token_hash] = 0
        # Set reset time in the future
        client._rate_limit_reset[token_hash] = datetime.now(timezone.utc) + timedelta(
            hours=1
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            async with client._rate_limit_guard(token_hash):
                pass

        assert "rate limit exceeded" in str(exc_info.value).lower()
        assert exc_info.value.is_retryable is True


# =============================================================================
# Get Releases Tests (lines 206-305)
# =============================================================================


@pytest.mark.asyncio
class TestGetReleases:
    """Test get_releases method."""

    async def test_get_releases_success(self, mocker, sample_release_data):
        """Test successful release fetch."""
        client = AsyncGitHubClient(github_token="test_token")

        # Create mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "60",
            "X-RateLimit-Reset": "1234567890",
        }
        mock_response.json = AsyncMock(return_value=sample_release_data)
        mock_response.raise_for_status = Mock()

        # Mock session
        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        # Set up context manager for session.get
        mock_session.get.return_value = mock_response

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )

        # Mock sleep to avoid delays
        mocker.patch("asyncio.sleep", AsyncMock())

        releases = await client.get_releases(
            "https://api.github.com/repos/test/test/releases"
        )

        assert len(releases) == 2
        assert releases[0].tag_name == "v2.7.15"
        assert releases[0].prerelease is False
        assert len(releases[0].assets) == 2
        assert releases[0].assets[0].name == "firmware-rak4631.bin"

    async def test_get_releases_with_limit(self, mocker, sample_release_data):
        """Test release fetch with limit parameter."""
        client = AsyncGitHubClient()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value=sample_release_data)
        mock_response.raise_for_status = Mock()

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        # Track what params were passed
        captured_params = {}

        def capture_params(url, params=None):
            captured_params["params"] = params
            return mock_response

        mock_session.get = Mock(side_effect=capture_params)

        await client.get_releases(
            "https://api.github.com/repos/test/test/releases", limit=50
        )

        assert captured_params["params"]["per_page"] == 50

    async def test_get_releases_non_list_payload_returns_empty(self, mocker):
        """Non-list JSON payloads should be handled defensively."""
        client = AsyncGitHubClient()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value={"unexpected": "shape"})
        mock_response.raise_for_status = Mock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        releases = await client.get_releases(
            "https://api.github.com/repos/test/test/releases"
        )

        assert releases == []

    async def test_get_releases_skips_malformed_entries(self, mocker):
        """Malformed release and asset entries should be skipped safely."""
        client = AsyncGitHubClient()

        payload = [
            "not-a-dict",
            {"tag_name": "v1.0.0", "assets": "not-a-list"},
            {
                "tag_name": "v1.1.0",
                "prerelease": False,
                "assets": [
                    "bad-asset",
                    {
                        "name": "firmware-good.bin",
                        "browser_download_url": "https://example.com/fw.bin",
                        "size": 1024,
                    },
                    {
                        "name": "firmware-bad-size.bin",
                        "browser_download_url": "https://example.com/fw2.bin",
                        "size": "not-int",
                    },
                ],
            },
        ]

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.json = AsyncMock(return_value=payload)
        mock_response.raise_for_status = Mock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = Mock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        releases = await client.get_releases(
            "https://api.github.com/repos/test/test/releases"
        )

        assert len(releases) == 2
        assert releases[0].tag_name == "v1.0.0"
        assert releases[0].assets == []
        assert releases[1].tag_name == "v1.1.0"
        assert len(releases[1].assets) == 2
        assert releases[1].assets[0].name == "firmware-good.bin"
        assert releases[1].assets[1].size == 0

    async def test_get_releases_rate_limit_exceeded(self, mocker):
        """Test get_releases raises error on rate limit exceeded."""
        client = AsyncGitHubClient()

        # Mock the entire get_releases to raise the expected error
        # This tests the logic without needing complex aiohttp mocking

        from fetchtastic.download.async_client import AsyncDownloadError

        # Mock _ensure_session to return a session that will raise on get
        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock()

        # Create a mock response that triggers rate limit
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "9999999999",
        }
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        # Make session.get return the mock response as async context manager
        mock_session.get.return_value = mock_response

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.get_releases("https://api.github.com/repos/test/test/releases")

        assert exc_info.value.status_code == 403
        assert exc_info.value.is_retryable is True

    async def test_get_releases_forbidden_not_rate_limit(self, mocker):
        """Test get_releases raises error for 403 not due to rate limit."""
        client = AsyncGitHubClient()

        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock()

        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.headers = {"X-RateLimit-Remaining": "10"}  # Still have remaining
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session.get.return_value = mock_response

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.get_releases("https://api.github.com/repos/test/test/releases")

        assert exc_info.value.status_code == 403
        assert exc_info.value.is_retryable is False

    async def test_get_releases_http_error(self, mocker):
        """Test get_releases handles HTTP errors."""
        client = AsyncGitHubClient()
        import aiohttp

        mock_session = AsyncMock()

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        # Simulate HTTP error
        mock_session.get = Mock(
            side_effect=aiohttp.ClientResponseError(
                request_info=Mock(), history=(), status=500, message="Server Error"
            )
        )

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.get_releases("https://api.github.com/repos/test/test/releases")

        assert exc_info.value.status_code == 500
        assert exc_info.value.is_retryable is True

    async def test_get_releases_network_error(self, mocker):
        """Test get_releases handles network errors."""
        client = AsyncGitHubClient()
        import aiohttp

        mock_session = AsyncMock()

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        # Simulate network error
        mock_session.get = Mock(side_effect=aiohttp.ClientError("Connection failed"))

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.get_releases("https://api.github.com/repos/test/test/releases")

        assert exc_info.value.is_retryable is True


# =============================================================================
# Update Rate Limits Tests (lines 307-330)
# =============================================================================


class TestUpdateRateLimits:
    """Test _update_rate_limits method."""

    def test_update_rate_limits_success(self, mocker):
        """Test updating rate limit tracking from response headers."""
        client = AsyncGitHubClient()
        mock_response = mocker.MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "45",
            "X-RateLimit-Reset": "1234567890",
        }

        client._update_rate_limits("test_hash", mock_response)

        assert client._rate_limit_remaining["test_hash"] == 45

    def test_update_rate_limits_missing_headers(self, mocker):
        """Test handling missing rate limit headers."""
        client = AsyncGitHubClient()
        mock_response = mocker.MagicMock()
        mock_response.headers = {}

        # Should not raise
        client._update_rate_limits("test_hash", mock_response)

        assert "test_hash" not in client._rate_limit_remaining

    def test_update_rate_limits_invalid_values(self, mocker):
        """Test handling invalid rate limit header values."""
        client = AsyncGitHubClient()
        mock_response = mocker.MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "invalid",
            "X-RateLimit-Reset": "not_a_number",
        }

        # Should not raise
        client._update_rate_limits("test_hash", mock_response)


# =============================================================================
# Download File Tests (lines 332-439)
# =============================================================================


# Helper to create async iterator from list
async def _make_async_iter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
class TestDownloadFile:
    """Test download_file method."""

    async def test_download_file_success(self, mocker, tmp_path):
        """Test successful file download."""
        client = AsyncGitHubClient()

        # Create mock response with content
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}

        # Mock content iteration - iter_chunked returns async iterator
        mock_content = mocker.MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=_make_async_iter([b"test content"])
        )
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        # Mock session
        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )

        # Mock aiofiles
        mock_file = AsyncMock()
        mock_file.write = AsyncMock()

        target = tmp_path / "test.bin"

        with patch("fetchtastic.download.async_client.aiofiles.open") as mock_open:
            mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
            mock_open.return_value.__aexit__ = AsyncMock(return_value=None)

            # Mock Path.replace to avoid file system operations
            with patch.object(Path, "replace"):
                result = await client.download_file(
                    "https://example.com/file.bin", target
                )

        assert result is True
        mock_file.write.assert_called()

    async def test_download_file_creates_parent_directory(self, mocker, tmp_path):
        """Test that download creates parent directories."""
        client = AsyncGitHubClient()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}

        mock_content = mocker.MagicMock()
        mock_content.iter_chunked = Mock(return_value=_make_async_iter([b"test"]))
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()

        target = tmp_path / "subdir" / "nested" / "test.bin"

        with patch("fetchtastic.download.async_client.aiofiles.open") as mock_open:
            mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
            mock_open.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch.object(Path, "replace"):
                result = await client.download_file(
                    "https://example.com/file.bin", target
                )

        assert result is True
        assert target.parent.exists()

    async def test_download_file_with_progress_callback(self, mocker, tmp_path):
        """Test download with progress callback."""
        client = AsyncGitHubClient()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "12"}

        mock_content = mocker.MagicMock()
        mock_content.iter_chunked = Mock(
            return_value=_make_async_iter([b"chunk1", b"chunk2"])
        )
        mock_response.content = mock_content

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()

        # Track callback invocations
        callback_calls = []

        async def progress_callback(downloaded, total, filename):
            callback_calls.append((downloaded, total))

        target = tmp_path / "test.bin"

        with patch("fetchtastic.download.async_client.aiofiles.open") as mock_open:
            mock_open.return_value.__aenter__ = AsyncMock(return_value=mock_file)
            mock_open.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch.object(Path, "replace"):
                result = await client.download_file(
                    "https://example.com/file.bin",
                    target,
                    progress_callback=progress_callback,
                )

        assert result is True
        assert len(callback_calls) == 2  # Once per chunk

    async def test_download_file_http_error(self, mocker, tmp_path):
        """Test download handles HTTP errors."""
        client = AsyncGitHubClient()

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.headers = {}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = mocker.MagicMock()
        mock_session.get = mocker.MagicMock(return_value=mock_response)

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )
        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.download_file("https://example.com/missing.bin", target)

        assert exc_info.value.status_code == 404
        assert exc_info.value.is_retryable is False

    async def test_download_file_client_error(self, mocker, tmp_path):
        """Test download handles client errors."""
        client = AsyncGitHubClient()
        import aiohttp

        mock_session = AsyncMock()
        mock_session.get = Mock(side_effect=aiohttp.ClientError("Connection failed"))

        mocker.patch.object(
            client, "_ensure_session", AsyncMock(return_value=mock_session)
        )

        target = tmp_path / "test.bin"

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.download_file("https://example.com/file.bin", target)

        assert exc_info.value.is_retryable is True


# =============================================================================
# Download File With Retry Tests (lines 441-489)
# =============================================================================


@pytest.mark.asyncio
class TestDownloadFileWithRetry:
    """Test download_file_with_retry method."""

    async def test_download_with_retry_success_first_attempt(self, mocker, tmp_path):
        """Test successful download on first attempt."""
        client = AsyncGitHubClient()

        mock_download = mocker.patch.object(
            client, "download_file", new_callable=AsyncMock, return_value=True
        )

        target = tmp_path / "test.bin"
        result = await client.download_file_with_retry(
            "https://example.com/file.bin", target
        )

        assert result is True
        mock_download.assert_called_once()

    async def test_download_with_retry_retries_on_retryable_error(
        self, mocker, tmp_path
    ):
        """Test retry on retryable errors."""
        client = AsyncGitHubClient()

        # First call fails, second succeeds
        mock_download = mocker.patch.object(
            client,
            "download_file",
            new_callable=AsyncMock,
            side_effect=[
                AsyncDownloadError("Network error", is_retryable=True),
                True,
            ],
        )

        # Mock sleep to speed up test
        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"
        result = await client.download_file_with_retry(
            "https://example.com/file.bin",
            target,
            max_retries=3,
            retry_delay=0.1,
            backoff_factor=2.0,
        )

        assert result is True
        assert mock_download.call_count == 2

    async def test_download_with_retry_non_retryable_raises_immediately(
        self, mocker, tmp_path
    ):
        """Test non-retryable errors raise immediately."""
        client = AsyncGitHubClient()

        mock_download = mocker.patch.object(
            client,
            "download_file",
            new_callable=AsyncMock,
            side_effect=AsyncDownloadError(
                "Not found", status_code=404, is_retryable=False
            ),
        )

        target = tmp_path / "test.bin"

        with pytest.raises(AsyncDownloadError) as exc_info:
            await client.download_file_with_retry(
                "https://example.com/missing.bin", target
            )

        assert exc_info.value.status_code == 404
        mock_download.assert_called_once()

    async def test_download_with_retry_exhausted_retries(self, mocker, tmp_path):
        """Test error raised after exhausting all retries."""
        client = AsyncGitHubClient()

        mock_download = mocker.patch.object(
            client,
            "download_file",
            new_callable=AsyncMock,
            side_effect=AsyncDownloadError("Network error", is_retryable=True),
        )

        mocker.patch("asyncio.sleep", AsyncMock())

        target = tmp_path / "test.bin"

        with pytest.raises(AsyncDownloadError):
            await client.download_file_with_retry(
                "https://example.com/file.bin", target, max_retries=2, retry_delay=0.1
            )

        assert mock_download.call_count == 3  # Initial + 2 retries

    async def test_download_with_retry_exponential_backoff(self, mocker, tmp_path):
        """Test exponential backoff timing."""
        client = AsyncGitHubClient()

        mocker.patch.object(
            client,
            "download_file",
            new_callable=AsyncMock,
            side_effect=[
                AsyncDownloadError("Error 1", is_retryable=True),
                AsyncDownloadError("Error 2", is_retryable=True),
                True,
            ],
        )

        # Track sleep calls
        sleep_calls = []

        async def track_sleep(duration):
            sleep_calls.append(duration)

        mocker.patch("asyncio.sleep", track_sleep)

        target = tmp_path / "test.bin"
        result = await client.download_file_with_retry(
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
# Create Async Client Factory Tests (lines 492-515)
# =============================================================================


@pytest.mark.asyncio
class TestCreateAsyncClient:
    """Test create_async_client factory function."""

    async def test_create_async_client_basic(self, mocker):
        """Test basic factory usage."""
        mock_close = mocker.patch.object(
            AsyncGitHubClient, "close", new_callable=AsyncMock
        )

        async with create_async_client() as client:
            assert isinstance(client, AsyncGitHubClient)
            assert client.github_token is None

        mock_close.assert_called_once()

    async def test_create_async_client_with_token(self, mocker):
        """Test factory with GitHub token."""
        mocker.patch.object(AsyncGitHubClient, "close", new_callable=AsyncMock)

        async with create_async_client(github_token="test_token") as client:
            assert client.github_token == "test_token"

    async def test_create_async_client_with_max_concurrent(self, mocker):
        """Test factory with custom max_concurrent."""
        mocker.patch.object(AsyncGitHubClient, "close", new_callable=AsyncMock)

        async with create_async_client(max_concurrent=10) as client:
            assert client.max_concurrent == 10


# =============================================================================
# Download Files Concurrently Tests (lines 518-553)
# =============================================================================


@pytest.mark.asyncio
class TestDownloadFilesConcurrently:
    """Test download_files_concurrently utility function."""

    async def test_download_files_concurrently_multiple(self, mocker, tmp_path):
        """Test downloading multiple files concurrently."""
        downloads = [
            {
                "url": "https://example.com/file1.bin",
                "target_path": str(tmp_path / "file1.bin"),
            },
            {
                "url": "https://example.com/file2.bin",
                "target_path": str(tmp_path / "file2.bin"),
            },
        ]

        # Mock the download_file method
        with patch.object(
            AsyncGitHubClient,
            "download_file",
            new_callable=AsyncMock,
            return_value=True,
        ):
            results = await download_files_concurrently(downloads, max_concurrent=2)

        assert len(results) == 2
        assert all(results)

    async def test_download_files_concurrently_mixed_results(self, mocker, tmp_path):
        """Test concurrent download with mixed success/failure."""
        downloads = [
            {
                "url": "https://example.com/file1.bin",
                "target_path": str(tmp_path / "file1.bin"),
            },
            {
                "url": "https://example.com/file2.bin",
                "target_path": str(tmp_path / "file2.bin"),
            },
        ]

        # Mock first success, second failure
        with patch.object(
            AsyncGitHubClient,
            "download_file",
            new_callable=AsyncMock,
            side_effect=[True, False],
        ):
            results = await download_files_concurrently(downloads)

        assert len(results) == 2
        assert results[0] is True
        assert results[1] is False

    async def test_download_files_concurrently_with_github_token(
        self, mocker, tmp_path
    ):
        """Test concurrent download forwards GitHub token to created client."""
        downloads = [
            {
                "url": "https://example.com/private-file.bin",
                "target_path": str(tmp_path / "private-file.bin"),
            }
        ]
        mock_client = AsyncMock()
        mock_client.download_file = AsyncMock(return_value=True)

        class MockClientContextManager:
            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        mock_create_client = mocker.patch(
            "fetchtastic.download.async_client.create_async_client",
            return_value=MockClientContextManager(),
        )

        results = await download_files_concurrently(
            downloads,
            max_concurrent=2,
            github_token="ghp_test_token",
        )

        mock_create_client.assert_called_once_with(
            github_token="ghp_test_token",
            max_concurrent=2,
        )
        assert results == [True]

    async def test_download_files_concurrently_invalid_specs(self, mocker, tmp_path):
        """Invalid specs should produce clear ValueError results."""
        downloads = [
            {"target_path": str(tmp_path / "missing-url.bin")},
            {"url": "https://example.com/missing-target.bin"},
            "not-a-dict",
            {
                "url": "https://example.com/valid.bin",
                "target_path": str(tmp_path / "valid.bin"),
            },
        ]
        mock_client = AsyncMock()
        mock_client.download_file = AsyncMock(return_value=True)

        class MockClientContextManager:
            async def __aenter__(self):
                return mock_client

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        mocker.patch(
            "fetchtastic.download.async_client.create_async_client",
            return_value=MockClientContextManager(),
        )

        results = await download_files_concurrently(downloads)

        assert len(results) == 4
        assert isinstance(results[0], ValueError)
        assert isinstance(results[1], ValueError)
        assert isinstance(results[2], ValueError)
        assert results[3] is True
