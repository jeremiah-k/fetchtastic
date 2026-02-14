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
    Raise a RuntimeError indicating network access is blocked during tests.

    This function is intended to replace network request callables and always raises a RuntimeError with the message stored in `_NETWORK_BLOCK_MSG`.

    Raises:
        RuntimeError: with `_NETWORK_BLOCK_MSG` explaining that network access is blocked and suggesting mocking requests or Session.request.
    """
    raise RuntimeError(_NETWORK_BLOCK_MSG)


async def _async_block_network(*_args, **_kwargs):
    """
    Raise a RuntimeError indicating async network access is blocked during tests.

    This async function is intended to replace async network request callables.

    Raises:
        RuntimeError: with `_ASYNC_NETWORK_BLOCK_MSG` explaining that async network
            access is blocked and suggesting mocking aiohttp.ClientSession.
    """
    raise RuntimeError(_ASYNC_NETWORK_BLOCK_MSG)


# Configure pytest-asyncio mode
pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    """Configure pytest-asyncio with auto mode."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test (auto-detected)"
    )


@pytest.fixture(autouse=True)
def _isolate_test_environment(tmp_path_factory, monkeypatch):
    """
    Isolate all Fetchtastic test paths (cache/state/config/log/downloads) to a temp root.

    This prevents pytest runs from touching real user directories.
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
    Disable real network requests during pytest runs by patching requests' HTTP entry points.

    Patches requests.get, requests.post, requests.put, requests.delete, requests.head and requests.Session.request so that any call raises a RuntimeError with a message indicating network access is blocked during tests.
    """
    requests.get = _block_network
    requests.post = _block_network
    requests.put = _block_network
    requests.delete = _block_network
    requests.head = _block_network
    requests.patch = _block_network
    requests.options = _block_network
    requests.Session.request = _block_network


# =============================================================================
# Async Test Fixtures
# =============================================================================


@pytest.fixture
def mock_aiohttp_session(mocker):
    """
    Fixture providing a mock aiohttp ClientSession for testing async HTTP operations.

    Yields a mock session that can be configured for specific test scenarios.
    """
    import aiohttp

    mock_session = mocker.MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    yield mock_session


@pytest.fixture
async def async_client(mock_aiohttp_session, mocker):
    """
    Fixture providing an AsyncGitHubClient with a mocked session.

    The client's session is pre-mocked to prevent network access.
    """
    from fetchtastic.download.async_client import AsyncGitHubClient

    client = AsyncGitHubClient(github_token="test-token")
    client._session = mock_aiohttp_session
    client._semaphore = mocker.MagicMock()

    yield client

    client._closed = True


@pytest.fixture
def mock_async_response(mocker):
    """
    Fixture providing a mock aiohttp ClientResponse for testing.

    Returns a factory function to create configured mock responses.
    """

    def _create_response(
        status=200,
        headers=None,
        json_data=None,
        content_chunks=None,
        raise_for_status=None,
    ):
        import aiohttp

        response = AsyncMock(spec=aiohttp.ClientResponse)
        response.status = status
        response.headers = headers or {}
        response.json = AsyncMock(return_value=json_data or {})

        if content_chunks:
            mock_content = mocker.MagicMock()
            mock_content.iter_chunked = mocker.Mock(return_value=content_chunks)
            response.content = mock_content

        if raise_for_status:
            response.raise_for_status = Mock(side_effect=raise_for_status)
        else:
            response.raise_for_status = Mock()

        return response

    from unittest.mock import Mock

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
    """Fixture providing a sample Asset object for testing."""
    from fetchtastic.download.interfaces import Asset

    return Asset(
        name="firmware-rak4631.bin",
        download_url="https://example.com/firmware-rak4631.bin",
        size=1024000,
        browser_download_url="https://example.com/firmware-rak4631.bin",
        content_type="application/octet-stream",
    )
