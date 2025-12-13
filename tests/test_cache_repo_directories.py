from datetime import datetime, timedelta, timezone

import pytest

from fetchtastic.download.cache import CacheManager


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
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
        calls["count"] += 1
        return _FakeResponse([{"type": "dir", "name": f"firmware-{calls['count']}"}])

    monkeypatch.setattr(
        "fetchtastic.download.cache.make_github_api_request", fake_request
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
