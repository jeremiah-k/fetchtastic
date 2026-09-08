"""Tests for the nightly.meshtastic.org manifest fetch helpers on CacheManager.

These exercise the new CacheManager.get_nightly_index /
get_nightly_release_manifest / get_nightly_target_manifest surface area
added in support of meshtastic/firmware#11719. The HTTP boundary is
mocked via ``mocker.patch`` on ``requests.Session.request`` so the
project-wide network blocker in tests/conftest.py stays in effect.

Each test stages a queue of response objects (status_code, json_data).
A queue with mixed 503/200 entries exercises the retry path; a queue
with a single 404 exercises the short-circuit; etc.
"""

from typing import Any

import pytest

from fetchtastic.download.cache import CacheManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]

INDEX_BODY: dict[str, Any] = {
    "version": "2.8.1.0becda3",
    "id": "v2.8.1.0becda3",
    "title": "Meshtastic Firmware 2.8.1.0becda3 Nightly",
    "commit": "0becda3017e4a1fba6201ebbd7aeccf9201aa7c5",
}

RELEASE_BODY: dict[str, Any] = {
    "version": "2.8.1.0becda3",
    "targets": [
        {"board": "tbeam", "platform": "esp32"},
        {"board": "rak4631", "platform": "nrf52"},
    ],
}

TARGET_BODY: dict[str, Any] = {
    "version": "2.8.1.0becda3",
    "build_epoch": 1788825600,
    "platformioTarget": "tbeam",
    "mcu": "esp32",
    "repo": "meshtastic/firmware",
    "files": [
        {
            "name": "firmware-tbeam-2.8.1.0becda3.bin",
            "md5": "1f8122577feae6a07da60eb4d30b6757",
            "bytes": 2081056,
            "part_name": "app0",
        }
    ],
}


class _FakeResponse:
    """Minimal requests.Response stand-in carrying only what the fetcher reads."""

    def __init__(self, status_code: int, json_data: Any = None) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self) -> Any:
        return self._json_data

    def raise_for_status(self) -> None:
        import requests

        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"{self.status_code} simulated error")


def _enqueue(mocker, *responses) -> None:
    """Stage a sequence of fake responses, consumed one per Session.request call."""
    queue = list(responses)
    mocker.patch(
        "requests.Session.request",
        side_effect=queue,
    )


def _cache(monkeypatch, tmp_path, base_url: str = "https://nightly.meshtastic.org") -> CacheManager:
    return CacheManager(cache_dir=str(tmp_path))


# ---------- happy path ----------


def test_get_nightly_index_returns_parsed_payload(mocker, monkeypatch, tmp_path) -> None:
    _enqueue(mocker, _FakeResponse(200, INDEX_BODY))
    cm = _cache(monkeypatch, tmp_path)
    out = cm.get_nightly_index(force_refresh=True)
    assert out == INDEX_BODY
    assert out["version"] == "2.8.1.0becda3"
    assert out["id"] == "v2.8.1.0becda3"


def test_get_nightly_release_manifest_returns_targets(
    mocker, monkeypatch, tmp_path
) -> None:
    _enqueue(mocker, _FakeResponse(200, RELEASE_BODY))
    cm = _cache(monkeypatch, tmp_path)
    out = cm.get_nightly_release_manifest("2.8.1.0becda3", force_refresh=True)
    assert out == RELEASE_BODY
    assert [t["board"] for t in out["targets"]] == ["tbeam", "rak4631"]


def test_get_nightly_target_manifest_returns_files(
    mocker, monkeypatch, tmp_path
) -> None:
    _enqueue(mocker, _FakeResponse(200, TARGET_BODY))
    cm = _cache(monkeypatch, tmp_path)
    out = cm.get_nightly_target_manifest("tbeam-2.8.1.0becda3", force_refresh=True)
    assert out == TARGET_BODY
    assert out["files"][0]["name"].endswith(".bin")


def test_get_nightly_target_manifest_accepts_uppercase_hash(
    mocker, monkeypatch, tmp_path
) -> None:
    _enqueue(mocker, _FakeResponse(200, TARGET_BODY))
    cm = _cache(monkeypatch, tmp_path)
    out = cm.get_nightly_target_manifest("tbeam-2.8.1.0BECDA3", force_refresh=True)
    assert out == TARGET_BODY


# ---------- fail-closed: bad id ----------


def test_get_nightly_target_manifest_rejects_path_traversal(
    mocker, monkeypatch, tmp_path
) -> None:
    cm = _cache(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_target_manifest("../../../etc/passwd", force_refresh=True)


def test_get_nightly_target_manifest_rejects_empty_string(
    mocker, monkeypatch, tmp_path
) -> None:
    cm = _cache(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_target_manifest("", force_refresh=True)


def test_get_nightly_release_manifest_rejects_bad_version(
    mocker, monkeypatch, tmp_path
) -> None:
    cm = _cache(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_release_manifest("not-a-version", force_refresh=True)


# ---------- 404 short-circuit ----------


def test_get_nightly_index_returns_empty_on_404(mocker, monkeypatch, tmp_path) -> None:
    _enqueue(mocker, _FakeResponse(404))
    cm = _cache(monkeypatch, tmp_path)
    assert cm.get_nightly_index(force_refresh=True) == {}


# ---------- retry: 503 then 200 ----------


def test_get_nightly_index_retries_on_503_then_succeeds(
    mocker, monkeypatch, tmp_path
) -> None:
    sleeper = mocker.patch("fetchtastic.download.cache.time.sleep")
    _enqueue(mocker, _FakeResponse(503), _FakeResponse(200, INDEX_BODY))
    cm = _cache(monkeypatch, tmp_path)
    out = cm.get_nightly_index(force_refresh=True)
    assert out == INDEX_BODY
    sleeper.assert_called_once()


def test_get_nightly_index_surfaces_503_after_retry_budget(
    mocker, monkeypatch, tmp_path
) -> None:
    sleeper = mocker.patch("fetchtastic.download.cache.time.sleep")
    # Two 503s exhausts the 1-retry budget. The fetch raises HTTPError;
    # _fetch_nightly_json must propagate it so the orchestrator
    # classifies the failure as a transport error, not as a
    # "no candidate published yet" empty listing.
    _enqueue(mocker, _FakeResponse(503), _FakeResponse(503))
    cm = _cache(monkeypatch, tmp_path)
    import requests as _requests

    with pytest.raises(_requests.HTTPError):
        cm.get_nightly_index(force_refresh=True)
    sleeper.assert_called_once()


def test_get_nightly_index_propagates_connection_error(
    mocker, monkeypatch, tmp_path
) -> None:
    # requests.RequestException raised by the underlying HTTP call
    # (e.g. ConnectionError) must propagate through _fetch_nightly_json.
    # _get_cached_github_data swallows RequestException internally for
    # its GitHub callers; the nightly path captures and re-raises.
    import requests as _requests

    sleeper = mocker.patch("fetchtastic.download.cache.time.sleep")
    mocker.patch(
        "requests.Session.request",
        side_effect=_requests.ConnectionError("dns down"),
    )
    cm = _cache(monkeypatch, tmp_path)
    with pytest.raises(_requests.RequestException):
        cm.get_nightly_index(force_refresh=True)
    sleeper.assert_called_once()


def test_get_nightly_index_propagates_malformed_json(
    mocker, monkeypatch, tmp_path
) -> None:
    # A 200 response whose body is not valid JSON must surface as a
    # decode error, not be collapsed into the {} "no candidate" path.
    class _BrokenJsonResponse:
        status_code = 200

        def json(self) -> None:
            import json as _json

            raise _json.JSONDecodeError("bad", "x", 0)

        def raise_for_status(self) -> None:
            return

    _enqueue(mocker, _BrokenJsonResponse())
    cm = _cache(monkeypatch, tmp_path)
    import json as _json

    with pytest.raises(_json.JSONDecodeError):
        cm.get_nightly_index(force_refresh=True)


# ---------- caching ----------


def test_get_nightly_index_uses_cache_within_ttl(mocker, monkeypatch, tmp_path) -> None:
    _enqueue(mocker, _FakeResponse(200, INDEX_BODY))
    cm = _cache(monkeypatch, tmp_path)
    first = cm.get_nightly_index(force_refresh=True)
    assert first == INDEX_BODY
    # A second non-force_refresh call must hit the cache, not the network.
    # If the second call attempted a request, the queue (now empty) would
    # raise StopIteration; the test therefore asserts the cached value.
    second = cm.get_nightly_index(force_refresh=False)
    assert second == INDEX_BODY


def test_get_nightly_index_force_refresh_re_reads(mocker, monkeypatch, tmp_path) -> None:
    _enqueue(
        mocker,
        _FakeResponse(200, INDEX_BODY),
        _FakeResponse(200, {**INDEX_BODY, "title": "updated"}),
    )
    cm = _cache(monkeypatch, tmp_path)
    assert cm.get_nightly_index(force_refresh=True)["title"].startswith("Meshtastic")
    assert cm.get_nightly_index(force_refresh=True)["title"] == "updated"


def test_get_nightly_index_failed_fetch_does_not_poison_cache(
    mocker, monkeypatch, tmp_path
) -> None:
    import requests as _requests

    sleeper = mocker.patch("fetchtastic.download.cache.time.sleep")
    request = mocker.patch(
        "requests.Session.request",
        side_effect=[
            _requests.ConnectionError("dns down"),
            _requests.ConnectionError("dns down"),
            _FakeResponse(200, INDEX_BODY),
        ],
    )
    cm = _cache(monkeypatch, tmp_path)

    with pytest.raises(_requests.ConnectionError):
        cm.get_nightly_index(force_refresh=True)

    assert cm.get_nightly_index(force_refresh=False) == INDEX_BODY
    assert request.call_count == 3
    sleeper.assert_called_once()
