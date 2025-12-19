from datetime import datetime, timedelta, timezone

import pytest

from fetchtastic.download.cache import CacheManager


class _FakeResponse:
    def __init__(self, payload):
        """
        Create a fake HTTP response that returns a preset JSON payload via its json() method.

        Parameters:
            payload (Any): JSON-serializable value that will be returned by json() (commonly a dict or list).
        """
        self._payload = payload

    def json(self):
        """
        Return the stored JSON-serializable payload.

        Returns:
            The JSON-serializable payload that was provided to the fake response.
        """
        return self._payload


@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
    """
    Provide an isolated cache directory for tests by patching platformdirs.user_cache_dir to return the given temporary path.

    Parameters:
        tmp_path (pathlib.Path): pytest temporary directory to use as the process cache directory.
        monkeypatch (pytest.MonkeyPatch): monkeypatch fixture used to override platformdirs.user_cache_dir.

    Returns:
        pathlib.Path: The same tmp_path now acting as the process cache directory.
    """
    monkeypatch.setattr(
        "platformdirs.user_cache_dir", lambda *_args, **_kwargs: str(tmp_path)
    )
    return tmp_path


def test_get_repo_directories_caches_with_ttl(monkeypatch, isolated_cache_dir):
    """
    Verify that get_repo_directories caches repository directory listings and avoids repeated API calls within the cache TTL.

    Creates a CacheManager, stubs the GitHub API to return a payload containing directories and a file, then calls get_repo_directories twice. Asserts the API was invoked only once and that the returned list contains only the directory names (in the original order) and is identical on the second call.
    """
    manager = CacheManager()

    payload = [
        {"type": "dir", "name": "firmware-1.2.3.abc"},
        {"type": "dir", "name": "firmware-1.2.4.def"},
        {"type": "file", "name": "README.md"},
    ]

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Record an invocation and return a fake HTTP response carrying the configured payload.

        Increments calls["count"] on each call to track invocation count.

        Returns:
            _FakeResponse: A fake response whose json() returns the preset payload.
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
    """
    Verifies that get_repo_directories refreshes stale cached entries and respects force_refresh.

    Seeds the cache with an entry older than the configured TTL, patches the GitHub request and cache-expiry value, then calls get_repo_directories twice (once normally and once with force_refresh=True). Asserts that the remote request is made for the stale entry and again for the forced refresh, and that each call returns the updated directory list corresponding to each fetch (`["firmware-1"]` then `["firmware-2"]`).
    """
    manager = CacheManager()

    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        """
        Simulate an HTTP API call that increments a shared call counter and returns a fake directory entry response.

        Returns:
            _FakeResponse: Response whose `json()` returns a single-item list containing a directory object `{"type": "dir", "name": "firmware-<n>"}`, where `<n>` is the incremented shared call count.
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
        Record an invocation and return a fake HTTP response carrying the configured payload.

        Increments calls["count"] on each call to track invocation count.

        Returns:
            _FakeResponse: A fake response whose json() returns the preset payload.
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
        Simulate a GitHub API response returning a single firmware file entry and increment a shared call counter.

        Increments calls["count"] and produces a response whose JSON payload reflects the updated count.

        Returns:
            _FakeResponse: JSON payload is a list with one dict: {"type": "file", "name": "fw-{N}.bin"}, where {N} is the new value of calls["count"].
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
