"""
Tests for Android snapshot debug build downloads.

Mirrors the prerelease test patterns. Snapshot builds are a rolling
GitHub prerelease tagged "snapshot" on meshtastic/Meshtastic-Android,
rebuilt on every push to main.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import (
    APP_DIR_NAME,
    APP_SNAPSHOTS_DIR_NAME,
    DEFAULT_CHECK_APP_SNAPSHOTS,
    LATEST_APP_SNAPSHOT_JSON_FILE,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]

# Flavor/ABI combinations that produce the 6 snapshot APKs
_SNAPSHOT_VARIANTS = [
    ("fdroid", "arm64-v8a"),
    ("fdroid", "armeabi-v7a"),
    ("fdroid", "universal"),
    ("google", "arm64-v8a"),
    ("google", "armeabi-v7a"),
    ("google", "universal"),
]


def _snapshot_asset_names(vc: int = 29321447) -> list[str]:
    return [
        f"androidApp-{flavor}-{abi}-debug-{vc}.apk"
        for flavor, abi in _SNAPSHOT_VARIANTS
    ]


def _make_snapshot_release(vc: int = 29321447, sha: str = "abc123def") -> Release:
    """Build a Release that mimics the rolling 'snapshot' tag."""
    asset_names = _snapshot_asset_names(vc)
    assets = [
        Asset(
            name=name,
            download_url=(
                f"https://github.com/meshtastic/Meshtastic-Android/"
                f"releases/download/snapshot/{name}"
            ),
            size=50_000_000,
        )
        for name in asset_names
    ]
    return Release(
        tag_name="snapshot",
        prerelease=True,
        name=f"Snapshot {vc} ({sha})",
        body=f"Automated debug build ({sha}), versionCode {vc}.",
        assets=assets,
    )


@pytest.fixture
def cache_manager(tmp_path):
    """Real CacheManager backed by tmp_path for file I/O."""
    return CacheManager(cache_dir=str(tmp_path))


@pytest.fixture
def downloader(tmp_path, cache_manager):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": [],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


@pytest.fixture
def downloader_disabled(tmp_path, cache_manager):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": [],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_SNAPSHOTS": False,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


# ------------------------------------------------------------------
# parse_snapshot_version_code
# ------------------------------------------------------------------


@pytest.mark.parametrize("flavor,abi", _SNAPSHOT_VARIANTS)
def test_parse_snapshot_version_code_all_variants(flavor, abi):
    """All 6 flavor/ABI variants should extract versionCode 29321447."""
    name = f"androidApp-{flavor}-{abi}-debug-29321447.apk"
    assert MeshtasticClientAppDownloader.parse_snapshot_version_code(name) == 29321447


def test_parse_snapshot_version_code_different_vc():
    assert (
        MeshtasticClientAppDownloader.parse_snapshot_version_code(
            "androidApp-google-universal-debug-29399999.apk"
        )
        == 29399999
    )


def test_parse_snapshot_version_code_non_snapshot():
    """Non-snapshot asset names return None."""
    assert (
        MeshtasticClientAppDownloader.parse_snapshot_version_code("firmware-1.2.3.bin")
        is None
    )
    assert (
        MeshtasticClientAppDownloader.parse_snapshot_version_code("app-1.0.apk") is None
    )
    assert (
        MeshtasticClientAppDownloader.parse_snapshot_version_code("random.txt") is None
    )


# ------------------------------------------------------------------
# extract_snapshot_commit_sha
# ------------------------------------------------------------------


def test_extract_commit_sha_from_name():
    release = _make_snapshot_release(sha="abc123def")
    assert (
        MeshtasticClientAppDownloader.extract_snapshot_commit_sha(release)
        == "abc123def"
    )


def test_extract_commit_sha_from_body():
    release = Release(
        tag_name="snapshot",
        name=None,
        body="Automated debug build (feedface), versionCode 100.",
    )
    assert (
        MeshtasticClientAppDownloader.extract_snapshot_commit_sha(release) == "feedface"
    )


def test_extract_commit_sha_missing():
    release = Release(tag_name="snapshot", name=None, body=None)
    assert MeshtasticClientAppDownloader.extract_snapshot_commit_sha(release) is None


# ------------------------------------------------------------------
# should_download_snapshot
# ------------------------------------------------------------------


def test_should_download_snapshot_no_tracking(downloader):
    """No tracking file → first run → should download."""
    assert downloader.should_download_snapshot(29321447) is True


def test_should_download_snapshot_newer(downloader, cache_manager):
    tracking = cache_manager.get_cache_file_path(LATEST_APP_SNAPSHOT_JSON_FILE)
    Path(tracking).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking).write_text(json.dumps({"version_code": 29321446}))
    assert downloader.should_download_snapshot(29321447) is True


def test_should_download_snapshot_older(downloader, cache_manager):
    tracking = cache_manager.get_cache_file_path(LATEST_APP_SNAPSHOT_JSON_FILE)
    Path(tracking).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking).write_text(json.dumps({"version_code": 29321447}))
    assert downloader.should_download_snapshot(29321446) is False


def test_should_download_snapshot_equal(downloader, cache_manager):
    tracking = cache_manager.get_cache_file_path(LATEST_APP_SNAPSHOT_JSON_FILE)
    Path(tracking).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking).write_text(json.dumps({"version_code": 29321447}))
    assert downloader.should_download_snapshot(29321447) is False


def test_should_download_snapshot_disabled(downloader_disabled):
    assert downloader_disabled.should_download_snapshot(29321447) is False


# ------------------------------------------------------------------
# update_snapshot_tracking
# ------------------------------------------------------------------


def test_update_snapshot_tracking_writes_json(downloader, cache_manager):
    tracking = cache_manager.get_cache_file_path(LATEST_APP_SNAPSHOT_JSON_FILE)
    assert downloader.update_snapshot_tracking(29321447, "abc123def") is True

    data = json.loads(Path(tracking).read_text())
    assert data["version_code"] == 29321447
    assert data["commit_sha"] == "abc123def"
    assert data["file_type"] == "app_snapshot"
    assert "last_updated" in data


def test_update_snapshot_tracking_no_sha(downloader, cache_manager):
    tracking = cache_manager.get_cache_file_path(LATEST_APP_SNAPSHOT_JSON_FILE)
    assert downloader.update_snapshot_tracking(100) is True

    data = json.loads(Path(tracking).read_text())
    assert data["version_code"] == 100
    assert data["commit_sha"] == ""


# ------------------------------------------------------------------
# _ensure_snapshot_base_dir / _resolve_snapshot_dir
# ------------------------------------------------------------------


def test_ensure_snapshot_base_dir(downloader, tmp_path):
    result = downloader._ensure_snapshot_base_dir()
    expected = str(tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME)
    assert result == expected
    assert os.path.isdir(result)


def test_resolve_snapshot_dir(downloader, tmp_path):
    result = downloader._resolve_snapshot_dir(29321447)
    expected = str(
        tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME / "29321447"
    )
    assert result == expected
    assert os.path.isdir(result)


# ------------------------------------------------------------------
# handle_snapshots
# ------------------------------------------------------------------


def test_handle_snapshots_disabled(downloader_disabled):
    release = _make_snapshot_release()
    assert downloader_disabled.handle_snapshots(release) is None


def test_handle_snapshots_valid(downloader):
    release = _make_snapshot_release()
    result = downloader.handle_snapshots(release)
    assert result is release


def test_handle_snapshots_none_release(downloader):
    assert downloader.handle_snapshots(None) is None


def test_handle_snapshots_no_apk_assets(downloader):
    release = Release(
        tag_name="snapshot",
        prerelease=True,
        assets=[Asset(name="notes.txt", download_url="http://x/notes.txt", size=10)],
    )
    assert downloader.handle_snapshots(release) is None


def test_handle_snapshots_get_snapshot_version_code(downloader):
    release = _make_snapshot_release(vc=42)
    assert downloader.get_snapshot_version_code(release) == 42


# ------------------------------------------------------------------
# cleanup_superseded_snapshots
# ------------------------------------------------------------------


def test_cleanup_superseded_snapshots(downloader, tmp_path):
    base = tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME
    for vc in (100, 200, 300):
        (base / str(vc)).mkdir(parents=True)
        (base / str(vc) / "test.apk").write_text("x")
    assert downloader.cleanup_superseded_snapshots() == 2
    assert (base / "300").is_dir()
    assert not (base / "200").is_dir()
    assert not (base / "100").is_dir()


def test_cleanup_superseded_snapshots_no_dir(downloader):
    assert downloader.cleanup_superseded_snapshots() == 0


def test_cleanup_keeps_multiple(downloader, tmp_path):
    downloader.config["APP_VERSIONS_TO_KEEP"] = 2
    base = tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME
    for vc in (100, 200, 300):
        (base / str(vc)).mkdir(parents=True)
    assert downloader.cleanup_superseded_snapshots() == 1
    assert (base / "300").is_dir()
    assert (base / "200").is_dir()
    assert not (base / "100").is_dir()


# ------------------------------------------------------------------
# fetch_snapshot_release (mocked)
# ------------------------------------------------------------------


def test_fetch_snapshot_release_success(downloader):
    release_data = {
        "tag_name": "snapshot",
        "prerelease": True,
        "name": "Snapshot 42 (deadbee)",
        "body": "Automated debug build",
        "assets": [
            {
                "name": "androidApp-google-universal-debug-42.apk",
                "browser_download_url": "https://example.com/apk",
                "size": 1000,
            }
        ],
    }
    mock_response = Mock()
    mock_response.json.return_value = release_data
    with patch(
        "fetchtastic.download.client_app.make_github_api_request",
        return_value=mock_response,
    ):
        result = downloader.fetch_snapshot_release()
    assert result is not None
    assert result.tag_name == "snapshot"
    assert len(result.assets) == 1


def test_fetch_snapshot_release_404(downloader):
    import requests

    exc = requests.HTTPError(response=Mock(status_code=404))
    with patch(
        "fetchtastic.download.client_app.make_github_api_request",
        side_effect=exc,
    ):
        result = downloader.fetch_snapshot_release()
    assert result is None


# ------------------------------------------------------------------
# Integration: end-to-end decision flow
# ------------------------------------------------------------------


def test_full_snapshot_decision_flow(downloader, cache_manager):
    """handle_snapshots → get_version_code → should_download → update_tracking."""
    release = _make_snapshot_release(vc=500)
    assert downloader.handle_snapshots(release) is release
    vc = downloader.get_snapshot_version_code(release)
    assert vc == 500
    assert downloader.should_download_snapshot(vc) is True  # first run
    assert downloader.update_snapshot_tracking(vc, "abc123") is True
    assert downloader.should_download_snapshot(vc) is False  # already tracked
    assert downloader.should_download_snapshot(501) is True  # newer


def test_default_check_app_snapshots_is_false():
    """The default for CHECK_APP_SNAPSHOTS must be False (opt-in)."""
    assert DEFAULT_CHECK_APP_SNAPSHOTS is False
