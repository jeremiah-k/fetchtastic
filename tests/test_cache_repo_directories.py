from datetime import datetime, timedelta, timezone

import pytest

from fetchtastic.download.cache import CacheManager


class _FakeResponse:
    def __init__(self, payload):
        """
        Initialize the fake response with a JSON payload to be returned by its json() method.
        
        Parameters:
            payload (Any): The JSON-serializable value that json() will return (commonly a dict or list).
        """
        self._payload = payload

    def json(self):
        """
        Retrieve the stored JSON payload.
        
        Returns:
            payload (Any): The payload provided to the fake response (a dict, list, or other JSON-compatible value).
        """
        return self._payload


@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
    """
    Provide an isolated cache directory for tests by patching platformdirs.user_cache_dir to return the given temporary path.
    
    Parameters:
        tmp_path (pathlib.Path): Temporary directory provided by pytest to use as the cache directory.
        monkeypatch (pytest.MonkeyPatch): pytest fixture used to patch `platformdirs.user_cache_dir`.
    
    Returns:
        pathlib.Path: The same `tmp_path` passed in, now acting as the process cache directory.
    """
    monkeypatch.setattr(
        "platformdirs.user_cache_dir", lambda *_args, **_kwargs: str(tmp_path)
    )
    return tmp_path


def test_get_repo_directories_caches_with_ttl(monkeypatch, isolated_cache_dir):
    manager = CacheManager()

    payload = [
        {"type": "dir", "name": "firmware-1.2.3.abc"},
        {"type": "dir", "name": "firmware-1.2.4.def"},
        {"type": "file", "name": "README.md"},
    ]

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Test helper that records an invocation and returns a fake HTTP response with the configured payload.
        
        Increments calls["count"] each time it's called to track invocation count.
        
        Returns:
            _FakeResponse: A fake response whose json() returns the preset `payload`.
        """
        calls["count"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(
        "fetchtastic.download.cache.make_github_api_request", fake_request
    )

    first = manager.get_repo_directories("")
    second = manager.get_repo_directories("")

    assert calls["count"] == 1
    assert first == ["firmware-1.2.3.abc", "firmware-1.2.4.def"]
    assert second == first


def test_get_repo_directories_refreshes_when_stale(monkeypatch, isolated_cache_dir):
    manager = CacheManager()

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Simulate an HTTP API call by incrementing a shared call counter and returning a fake response containing a single directory entry.
        
        The function increments `calls["count"]` and returns an `_FakeResponse` whose `json()` yields a list with one object: a directory `{"type": "dir", "name": "firmware-<n>"}` where `<n>` is the updated call count.
        
        @returns:
            _FakeResponse: Response whose `json()` returns the described list.
        """
        calls["count"] += 1
        return _FakeResponse([{"type": "dir", "name": f"firmware-{calls['count']}"}])

    monkeypatch.setattr(
        "fetchtastic.download.cache.make_github_api_request", fake_request
    )

    monkeypatch.setattr(
        "fetchtastic.download.cache.FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS",
        60,
    )
    # Seed cache with an entry older than the TTL.
    cache_file = isolated_cache_dir / "prerelease_dirs.json"
    cache_file.write_text(
        (
            "{"
            '"repo:/": {'
            '"directories": ["firmware-old"],'
            f'"cached_at": "{(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}"'
            "}"
            "}"
        ),
        encoding="utf-8",
    )

    first = manager.get_repo_directories("")
    second = manager.get_repo_directories("", force_refresh=True)

    assert calls["count"] == 2
    assert first == ["firmware-1"]
    assert second == ["firmware-2"]


def test_get_repo_contents_caches_with_ttl(monkeypatch, isolated_cache_dir):
    manager = CacheManager()

    payload = [
        {"type": "file", "name": "firmware.bin", "download_url": "https://example/x"},
        {"type": "dir", "name": "subdir"},
        "not-a-dict",
    ]

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Test helper that records an invocation and returns a fake HTTP response with the configured payload.
        
        Increments calls["count"] each time it's called to track invocation count.
        
        Returns:
            _FakeResponse: A fake response whose json() returns the preset `payload`.
        """
        calls["count"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(
        "fetchtastic.download.cache.make_github_api_request", fake_request
    )

    first = manager.get_repo_contents("firmware-1.2.3.abc")
    second = manager.get_repo_contents("firmware-1.2.3.abc")

    assert calls["count"] == 1
    assert first == [payload[0], payload[1]]
    assert second == first


def test_get_repo_contents_refreshes_when_stale(monkeypatch, isolated_cache_dir):
    manager = CacheManager()

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Simulates a GitHub API request that returns a single file entry and increments the shared call counter.
        
        Returns:
            _FakeResponse: A response whose JSON payload is a list containing one dict with keys `type: "file"` and `name: "fw-{N}.bin"`, where `{N}` is the updated value of `calls["count"]`. This function also increments `calls["count"]`.
        """
        calls["count"] += 1
        return _FakeResponse([{"type": "file", "name": f"fw-{calls['count']}.bin"}])

    monkeypatch.setattr(
        "fetchtastic.download.cache.make_github_api_request", fake_request
    )

    monkeypatch.setattr(
        "fetchtastic.download.cache.FIRMWARE_PRERELEASE_DIR_CACHE_EXPIRY_SECONDS",
        60,
    )
    cache_file = isolated_cache_dir / "repo_contents.json"
    cache_file.write_text(
        (
            "{"
            '"contents:firmware-older": {'
            '"contents": [{"type": "file", "name": "old.bin"}],'
            f'"cached_at": "{(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}"'
            "}"
            "}"
        ),
        encoding="utf-8",
    )

    first = manager.get_repo_contents("firmware-older")
    second = manager.get_repo_contents("firmware-older", force_refresh=True)

    assert calls["count"] == 2
    assert first == [{"type": "file", "name": "fw-1.bin"}]
    assert second == [{"type": "file", "name": "fw-2.bin"}]