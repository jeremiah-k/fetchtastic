"""Tests for the nightly.meshtastic.org manifest fetch helpers on CacheManager.

These exercise the new CacheManager.get_nightly_index /
get_nightly_release_manifest / get_nightly_target_manifest surface area
added in support of meshtastic/firmware#11719. Real HTTP semantics are
covered by spinning up a stdlib http.server on an ephemeral port so that
404 short-circuit, 503-then-200 retry, and bad-target-id rejection all
hit actual request handling.
"""

import http.server
import json
import socketserver
import threading
from typing import Any

import pytest

from fetchtastic.download.cache import CacheManager

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


class _Handler(http.server.BaseHTTPRequestHandler):
    """Routes nightly-manifest GETs to canned JSON responses.

    Subclass and override ``index_status`` / ``release_status`` /
    ``target_status`` to drive error-path tests. The first matching path
    wins, so the test can mix 200 and non-200 responses on the same
    server (used by the 503-then-200 retry test).
    """

    server_state: dict[str, Any] = {}

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        # Silence stderr noise during tests; failures surface via assertions.
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.lstrip("/")
        state = self.server_state

        # Optional 503-then-200 counter; consumed once per request.
        if state.get("next_status"):
            code = int(state["next_status"].pop(0))
            if code != 200:
                self.send_response(code)
                self.end_headers()
                return

        if path == "index.json":
            self._send_json(state.get("index_status", 200), INDEX_BODY)
            return
        if path.startswith("firmware-") and path.endswith(".json") and ".mt." not in path:
            self._send_json(state.get("release_status", 200), RELEASE_BODY)
            return
        if path.endswith(".mt.json"):
            self._send_json(state.get("target_status", 200), TARGET_BODY)
            return
        self.send_response(404)
        self.end_headers()

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def nightly_server(monkeypatch):
    """Spin up a stdlib http.server bound to a random localhost port.

    Yields the base URL. The test can mutate ``server_state`` (a class
    attribute on the handler) to drive error paths.
    """
    _Handler.server_state = {}
    server = _ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _cache_pointing_at(monkeypatch, base_url: str, tmp_path) -> CacheManager:
    """Build a CacheManager whose nightly fetcher talks to the test server."""
    monkeypatch.setattr(
        "fetchtastic.download.cache.FIRMWARE_NIGHTLY_BASE_URL", base_url
    )
    return CacheManager(cache_dir=str(tmp_path))


# ---------- happy path ----------


def test_get_nightly_index_returns_parsed_payload(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    out = cm.get_nightly_index(force_refresh=True)
    assert out == INDEX_BODY
    assert out["version"] == "2.8.1.0becda3"
    assert out["id"] == "v2.8.1.0becda3"


def test_get_nightly_release_manifest_returns_targets(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    out = cm.get_nightly_release_manifest("2.8.1.0becda3", force_refresh=True)
    assert out == RELEASE_BODY
    assert [t["board"] for t in out["targets"]] == ["tbeam", "rak4631"]


def test_get_nightly_target_manifest_returns_files(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    out = cm.get_nightly_target_manifest("tbeam-2.8.1.0becda3", force_refresh=True)
    assert out == TARGET_BODY
    assert out["files"][0]["name"].endswith(".bin")


# ---------- fail-closed: bad id / 404 / non-retriable client error ----------


def test_get_nightly_target_manifest_rejects_path_traversal(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_target_manifest("../../../etc/passwd", force_refresh=True)


def test_get_nightly_target_manifest_rejects_empty_string(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_target_manifest("", force_refresh=True)


def test_get_nightly_release_manifest_rejects_bad_version(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    with pytest.raises(ValueError):
        cm.get_nightly_release_manifest("not-a-version", force_refresh=True)


def test_get_nightly_index_returns_empty_on_404(
    nightly_server, monkeypatch, tmp_path
) -> None:
    _Handler.server_state["index_status"] = 404
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    assert cm.get_nightly_index(force_refresh=True) == {}


# ---------- retry: 503 then 200 ----------


def test_get_nightly_index_retries_on_503_then_succeeds(
    nightly_server, monkeypatch, tmp_path
) -> None:
    # First request -> 503; second -> default 200 with INDEX_BODY.
    _Handler.server_state["next_status"] = [503]
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    out = cm.get_nightly_index(force_refresh=True)
    assert out == INDEX_BODY


def test_get_nightly_index_surfaces_503_after_retry_budget(
    nightly_server, monkeypatch, tmp_path
) -> None:
    # Two 503s exhausts the 1-retry budget.
    _Handler.server_state["next_status"] = [503, 503]
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    with pytest.raises(Exception):
        cm.get_nightly_index(force_refresh=True)


# ---------- caching ----------


def test_get_nightly_index_uses_cache_within_ttl(
    nightly_server, monkeypatch, tmp_path
) -> None:
    cm = _cache_pointing_at(monkeypatch, nightly_server, tmp_path)
    # First call populates cache; on second call the cache must be hit
    # before the server (the server would 500 if hit, since we don't reset state).
    _Handler.server_state["index_status"] = 500  # would raise if reached
    first = cm.get_nightly_index(force_refresh=True)
    assert first == INDEX_BODY
    # Now flip to a server-side failure that should never be reached:
    _Handler.server_state["index_status"] = 500
    second = cm.get_nightly_index(force_refresh=False)
    assert second == INDEX_BODY
