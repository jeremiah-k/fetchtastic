import os
from pathlib import Path

import platformdirs
import pytest
import requests

_NETWORK_BLOCK_MSG = (
    "Network access is blocked during tests. " "Mock requests.* or Session.request."
)


def _block_network(*_args, **_kwargs):
    """
    Raise a RuntimeError indicating network access is blocked during tests.

    This function is intended to replace network request callables and always raises a RuntimeError with the message stored in `_NETWORK_BLOCK_MSG`.

    Raises:
        RuntimeError: with `_NETWORK_BLOCK_MSG` explaining that network access is blocked and suggesting mocking requests or Session.request.
    """
    raise RuntimeError(_NETWORK_BLOCK_MSG)


@pytest.fixture(autouse=True, scope="session")
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
    requests.Session.request = _block_network
