import time
from pathlib import Path
from unittest.mock import AsyncMock

import platformdirs
import pytest
import requests

_NETWORK_BLOCK_MSG = (
    "Network access is blocked during tests. Mock requests.* or Session.request."
)

_ASYNC_NETWORK_BLOCK_MSG = (
    "Async network access is blocked during tests. Mock aiohttp.ClientSession."
)


def _block_network(*_args, **_kwargs):
    """
    Prevent network calls in tests by raising a RuntimeError.

    Raises:
        RuntimeError: with `_NETWORK_BLOCK_MSG` indicating that network access is blocked during tests.
    """
    raise RuntimeError(_NETWORK_BLOCK_MSG)


async def _async_block_network(*_args, **_kwargs):
    """
    Prevent async network calls during tests by raising a RuntimeError.

    Intended to replace async network request callables (for example, aiohttp.ClientSession methods)
    so tests do not perform real HTTP requests.

    Raises:
        RuntimeError: `_ASYNC_NETWORK_BLOCK_MSG` explaining that async network access is blocked and suggesting mocking `aiohttp.ClientSession`.
    """
    raise RuntimeError(_ASYNC_NETWORK_BLOCK_MSG)


# Configure pytest-asyncio mode - only register if available
try:
    import importlib

    importlib.import_module("pytest_asyncio")
    pytest_plugins = ("pytest_asyncio",)
except ImportError:
    pytest_plugins = ()


def pytest_configure(config):
    """
    Configure pytest-asyncio to enable automatic detection of asyncio-marked tests.

    Parameters:
        config: pytest.Config
            The pytest configuration object used to register the `asyncio` marker.
    """
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test (auto-detected)"
    )


@pytest.fixture(autouse=True)
def _isolate_test_environment(tmp_path_factory, monkeypatch):
    """
    Create an isolated temporary XDG and application directory layout and patch environment and configuration to use it for tests.

    This fixture creates temp directories for cache, state, config, data, downloads, and logs, sets XDG_* environment variables and FETCHTASTIC_DISABLE_FILE_LOGGING, patches platformdirs user_* functions to return the temp paths, and updates fetchtastic.setup_config constants (DOWNLOADS_DIR, DEFAULT_BASE_DIR, BASE_DIR, CONFIG_DIR, CONFIG_FILE, OLD_CONFIG_FILE) to point into the isolated structure.
    """
    base = tmp_path_factory.mktemp("fetchtastic")
    cache_dir = base / "cache"
    state_dir = base / "state"
    config_dir = base / "config"
    data_dir = base / "data"
    downloads_dir = base / "downloads"
    log_dir = state_dir / "log"

    for path in (cache_dir, state_dir, config_dir, data_dir, downloads_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    monkeypatch.setenv("FETCHTASTIC_DISABLE_FILE_LOGGING", "1")

    monkeypatch.setattr(
        platformdirs, "user_cache_dir", lambda *_args, **_kwargs: str(cache_dir)
    )
    monkeypatch.setattr(
        platformdirs, "user_state_dir", lambda *_args, **_kwargs: str(state_dir)
    )
    monkeypatch.setattr(
        platformdirs, "user_config_dir", lambda *_args, **_kwargs: str(config_dir)
    )
    monkeypatch.setattr(
        platformdirs, "user_data_dir", lambda *_args, **_kwargs: str(data_dir)
    )
    monkeypatch.setattr(
        platformdirs, "user_log_dir", lambda *_args, **_kwargs: str(log_dir)
    )

    import fetchtastic.setup_config as setup_config

    base_dir = Path(downloads_dir) / "Meshtastic"
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setup_config, "DOWNLOADS_DIR", str(downloads_dir))
    monkeypatch.setattr(setup_config, "DEFAULT_BASE_DIR", str(base_dir))
    monkeypatch.setattr(setup_config, "BASE_DIR", str(base_dir))
    monkeypatch.setattr(setup_config, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(
        setup_config,
        "CONFIG_FILE",
        str(Path(config_dir) / setup_config.CONFIG_FILE_NAME),
    )
    monkeypatch.setattr(
        setup_config,
        "OLD_CONFIG_FILE",
        str(Path(base_dir) / setup_config.CONFIG_FILE_NAME),
    )


def pytest_runtest_setup():
    """
    Prevent real network requests during tests by replacing HTTP entry points with blocking callables.

    Replaces common synchronous requests entry points and Session.request with a function that raises a RuntimeError indicating network access is blocked. If aiohttp is installed, replaces its top-level request and ClientSession HTTP methods with an async blocker; if aiohttp is not available, the function continues silently.
    """
    requests.get = _block_network
    requests.post = _block_network
    requests.put = _block_network
    requests.delete = _block_network
    requests.head = _block_network
    requests.patch = _block_network
    requests.options = _block_network
    requests.Session.request = _block_network

    try:
        import aiohttp  # type: ignore[import-not-found]

        aiohttp.request = _async_block_network
        aiohttp.ClientSession.request = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.get = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.post = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.put = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.delete = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.head = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.patch = _async_block_network  # type: ignore[assignment]
        aiohttp.ClientSession.options = _async_block_network  # type: ignore[assignment]
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _mock_time_sleep(monkeypatch):
    """
    Make time.sleep instant for all tests to prevent delays.

    This fixture is intentionally global because many retry/backoff paths use
    time.sleep(). Tests that require real timing behavior should explicitly
    monkeypatch sleep back to the real implementation within the test.
    """
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)


# =============================================================================
# Async Test Fixtures
# =============================================================================


@pytest.fixture
def mock_aiohttp_session(mocker):
    """
    Provide a mock aiohttp.ClientSession for testing async HTTP operations.

    Yields a MagicMock configured with the aiohttp.ClientSession spec and with `closed` set to False.
    """
    import aiohttp

    mock_session = mocker.MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    yield mock_session


@pytest.fixture
async def async_client(mock_aiohttp_session, mocker):
    """
    Provides an AsyncGitHubClient instance configured for tests.

    Returns:
        client (AsyncGitHubClient): A client whose `_session` is set to the provided mocked aiohttp session and whose `_semaphore` is a mock. The client is marked closed during fixture teardown.
    """
    from fetchtastic.download.async_client import AsyncGitHubClient

    client = AsyncGitHubClient(github_token="test-token")  # noqa: S106
    client._session = mock_aiohttp_session
    client._semaphore = mocker.MagicMock()

    yield client

    # Properly mark as closed (session is mocked, no need to close it)
    client._closed = True


@pytest.fixture
def mock_async_response(mocker):
    """
    Provide a factory that creates configured mock aiohttp.ClientResponse objects for tests.

    The returned factory can be called with parameters to set the response's status, headers,
    json() return value, an iterable of content chunks for content.iter_chunked, and a
    raise_for_status side effect.

    Returns:
        factory (callable): A function that returns a mocked `aiohttp.ClientResponse` configured
        with `status`, `headers`, `json()` behavior, optional `content.iter_chunked` chunks,
        and a `raise_for_status` mock that can raise an exception when called.
    """

    def _create_response(
        status=200,
        headers=None,
        json_data=None,
        content_chunks=None,
        raise_for_status=None,
    ):
        """
        Create a mocked aiohttp.ClientResponse configured for tests.

        Parameters:
            status (int): HTTP status code to expose on the response.
            headers (dict | None): Headers mapping for the response; defaults to empty dict.
            json_data (Any | None): Value that the response's asynchronous `json()` method will return.
            content_chunks (Iterable[bytes] | None): Iterable returned by `response.content.iter_chunked(...)` to simulate streamed body chunks.
            raise_for_status (Exception | callable | None): If provided, calling `response.raise_for_status()` will raise this exception (or call the callable). If `None`, `raise_for_status()` is a no-op.

        Returns:
            A mock object compatible with `aiohttp.ClientResponse`, with `status`, `headers`, an async `json()` method, optional `content.iter_chunked`, and a mocked `raise_for_status()` behavior.
        """
        import aiohttp

        response = AsyncMock(spec=aiohttp.ClientResponse)
        response.status = status
        response.headers = headers or {}
        response.json = AsyncMock(return_value=json_data or {})

        if content_chunks:

            async def _async_iter_chunks():
                for chunk in content_chunks:
                    yield chunk

            mock_content = mocker.MagicMock()
            mock_content.iter_chunked = mocker.Mock(return_value=_async_iter_chunks())
            response.content = mock_content

        if raise_for_status:
            response.raise_for_status = mocker.Mock(side_effect=raise_for_status)
        else:
            response.raise_for_status = mocker.Mock()

        return response

    return _create_response


@pytest.fixture
def sample_release_data():
    """Fixture providing sample GitHub release data for testing."""
    return [
        {
            "tag_name": "v2.7.15",
            "prerelease": False,
            "published_at": "2024-01-15T00:00:00Z",
            "name": "Release 2.7.15",
            "body": "## Release Notes\n\n- Feature 1\n- Feature 2",
            "assets": [
                {
                    "name": "firmware-rak4631.bin",
                    "browser_download_url": "https://example.com/firmware-rak4631.bin",
                    "size": 1024000,
                    "content_type": "application/octet-stream",
                },
                {
                    "name": "firmware-tbeam.bin",
                    "browser_download_url": "https://example.com/firmware-tbeam.bin",
                    "size": 1024000,
                    "content_type": "application/octet-stream",
                },
            ],
        },
        {
            "tag_name": "v2.7.14",
            "prerelease": False,
            "published_at": "2024-01-10T00:00:00Z",
            "name": "Release 2.7.14",
            "body": "Previous release",
            "assets": [
                {
                    "name": "firmware-rak4631.bin",
                    "browser_download_url": "https://example.com/old-firmware.bin",
                    "size": 1024000,
                    "content_type": "application/octet-stream",
                },
            ],
        },
    ]


@pytest.fixture
def sample_release(sample_release_data):
    """Fixture providing a sample Release object for testing."""
    from fetchtastic.download.interfaces import Asset, Release

    data = sample_release_data[0]
    return Release(
        tag_name=data["tag_name"],
        prerelease=data["prerelease"],
        published_at=data["published_at"],
        name=data["name"],
        body=data["body"],
        assets=[
            Asset(
                name=asset["name"],
                download_url=asset["browser_download_url"],
                size=asset["size"],
                browser_download_url=asset["browser_download_url"],
                content_type=asset.get("content_type"),
            )
            for asset in data["assets"]
        ],
    )


@pytest.fixture
def sample_asset():
    """
    Provide a sample Asset instance representing a firmware asset for tests.

    The returned Asset is populated with a realistic name, download URLs, size, and content type to be used by tests needing a firmware-like asset.

    Returns:
        Asset: An Asset object initialized with sample firmware metadata.
    """
    from fetchtastic.download.interfaces import Asset

    return Asset(
        name="firmware-rak4631.bin",
        download_url="https://example.com/firmware-rak4631.bin",
        size=1024000,
        browser_download_url="https://example.com/firmware-rak4631.bin",
        content_type="application/octet-stream",
    )
