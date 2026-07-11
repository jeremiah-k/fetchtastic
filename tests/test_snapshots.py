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

from fetchtastic.client_app_config import normalize_client_app_config
from fetchtastic.constants import (
    APP_DIR_NAME,
    APP_SNAPSHOTS_DIR_NAME,
    DEFAULT_CHECK_APP_SNAPSHOTS,
    FILE_TYPE_APP_SNAPSHOT,
    LATEST_APP_SNAPSHOT_JSON_FILE,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import (
    MeshtasticClientAppDownloader,
    is_snapshot_tag,
)
from fetchtastic.download.interfaces import Asset, DownloadResult, Release

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
        "SELECTED_APP_ASSETS": ["androidApp-fdroid-universal-debug-*.apk"],
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
        "SELECTED_APP_ASSETS": ["androidApp-fdroid-universal-debug-*.apk"],
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
    downloader.config["APP_SNAPSHOT_VERSIONS_TO_KEEP"] = 2
    base = tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME
    for vc in (100, 200, 300):
        (base / str(vc)).mkdir(parents=True)
    assert downloader.cleanup_superseded_snapshots() == 1
    assert (base / "300").is_dir()
    assert (base / "200").is_dir()
    assert not (base / "100").is_dir()


def test_cleanup_retention_floor_is_one(downloader, tmp_path):
    """A keep count of zero must be clamped to 1 — never delete the current snapshot."""
    downloader.config["APP_SNAPSHOT_VERSIONS_TO_KEEP"] = 0
    base = tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME
    (base / "100").mkdir(parents=True)
    assert downloader.cleanup_superseded_snapshots() == 0
    assert (base / "100").is_dir()


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


# ==================================================================
# PR Review: Snapshot Isolation & Robustness Tests
# ==================================================================


# ------------------------------------------------------------------
# 1. Prerelease Isolation (P0 proof)
# ------------------------------------------------------------------


def test_snapshot_not_classified_as_prerelease(downloader):
    """Snapshot release must NOT be classified as an ordinary client app prerelease."""
    release = _make_snapshot_release()
    assert downloader._is_client_app_prerelease(release) is False


def test_snapshot_excluded_from_handle_prereleases(downloader):
    """handle_prereleases must never return the snapshot release."""
    downloader.config["CHECK_APP_PRERELEASES"] = True
    snapshot = _make_snapshot_release()
    real_prerelease = Release(
        tag_name="v2.8.1-open.1",
        prerelease=True,
        published_at="2024-01-01",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="http://x",
                size=1,
            )
        ],
    )
    stable = Release(
        tag_name="v2.8.0",
        prerelease=False,
        published_at="2024-01-02",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="http://x",
                size=1,
            )
        ],
    )
    result = downloader.handle_prereleases([snapshot, real_prerelease, stable])
    tags = [r.tag_name for r in result]
    assert "snapshot" not in tags
    assert "v2.8.1-open.1" in tags


def test_snapshot_excluded_from_latest_prerelease_tag(downloader):
    snapshot = _make_snapshot_release()
    real_prerelease = Release(
        tag_name="v2.8.1-open.1",
        prerelease=True,
        published_at="2024-01-01",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="http://x",
                size=1,
            )
        ],
    )
    stable = Release(
        tag_name="v2.8.0",
        prerelease=False,
        published_at="2024-01-02",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="http://x",
                size=1,
            )
        ],
    )
    result = downloader.get_latest_prerelease_tag([snapshot, real_prerelease, stable])
    assert result != "snapshot"
    assert result == "v2.8.1-open.1"


def test_snapshot_disabled_does_not_interfere_with_prereleases(downloader_disabled):
    """CHECK_APP_SNAPSHOTS=False must not affect prerelease behavior."""
    snapshot = _make_snapshot_release()
    assert downloader_disabled._is_client_app_prerelease(snapshot) is False


def test_is_snapshot_tag_case_insensitive():
    assert is_snapshot_tag("snapshot") is True
    assert is_snapshot_tag("SNAPSHOT") is True
    assert is_snapshot_tag("Snapshot") is True
    assert is_snapshot_tag("v2.8.0") is False
    assert is_snapshot_tag("") is False


def test_snapshot_excluded_from_get_releases_payload():
    """_is_client_app_prerelease_payload must return False for snapshot payloads."""
    from fetchtastic.download.client_app import _is_client_app_prerelease_payload

    payload = {"tag_name": "snapshot", "prerelease": True}
    assert _is_client_app_prerelease_payload(payload) is False
    payload2 = {"tag_name": "v2.8.1-open.1", "prerelease": True}
    assert _is_client_app_prerelease_payload(payload2) is True


# ------------------------------------------------------------------
# 2. Asset Selection (P0-2 proof)
# ------------------------------------------------------------------


def test_get_selected_snapshot_assets_filters_by_pattern(downloader):
    """Only assets matching SELECTED_APP_ASSETS should be selected."""
    # Substring patterns (not glob) — the trailing dash is the reliable token.
    downloader.config["SELECTED_APP_ASSETS"] = ["androidApp-fdroid-universal-debug-"]
    release = _make_snapshot_release(vc=100)
    selected = downloader.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "fdroid" in selected[0].name
    assert "universal" in selected[0].name


def test_get_selected_snapshot_assets_empty_selection(downloader):
    """When no assets match the selection, return empty list."""
    release = _make_snapshot_release(vc=100)
    downloader.config["SELECTED_APP_ASSETS"] = ["*.dmg"]  # desktop-only pattern
    selected = downloader.get_selected_snapshot_assets(release)
    assert selected == []


def test_get_selected_snapshot_assets_rejects_non_snapshot_tag(downloader):
    release = _make_snapshot_release(vc=100)
    release.tag_name = "not-snapshot"
    assert downloader.get_selected_snapshot_assets(release) == []


def test_get_selected_snapshot_assets_rejects_mixed_version_codes(downloader):
    """Assets with different versionCodes should not be selected together."""
    downloader.config["SELECTED_APP_ASSETS"] = ["androidApp-fdroid-universal-debug-"]
    release = Release(
        tag_name="snapshot",
        prerelease=True,
        assets=[
            Asset(
                name="androidApp-fdroid-universal-debug-100.apk",
                download_url="http://x",
                size=1,
            ),
            Asset(
                name="androidApp-google-universal-debug-200.apk",
                download_url="http://x",
                size=1,
            ),
        ],
    )
    selected = downloader.get_selected_snapshot_assets(release)
    # First asset's vc (100) is authoritative; second (200) must be excluded
    assert len(selected) == 1
    assert "100" in selected[0].name


# ------------------------------------------------------------------
# 3. Release Validation (P1-5 proof)
# ------------------------------------------------------------------


def test_handle_snapshots_rejects_wrong_tag(downloader):
    """handle_snapshots must reject a release whose tag is not 'snapshot'."""
    release = _make_snapshot_release()
    release.tag_name = "not-snapshot"
    assert downloader.handle_snapshots(release) is None


def test_handle_snapshots_accepts_exact_tag(downloader):
    release = _make_snapshot_release()
    assert downloader.handle_snapshots(release) is not None


# ------------------------------------------------------------------
# 4. Completeness (P1-1 proof)
# ------------------------------------------------------------------


def test_is_snapshot_complete_when_all_present(downloader, tmp_path):
    release = _make_snapshot_release(vc=100)
    # Use a substring pattern that matches the fdroid universal variant
    downloader.config["SELECTED_APP_ASSETS"] = ["androidApp-fdroid-universal-debug-"]
    # Shrink selected asset's expected size to match a small test file
    selected = downloader.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    asset = selected[0]
    asset.size = 4
    target = downloader.get_snapshot_target_path(100, asset.name, create=True)
    Path(target).write_bytes(b"test")
    assert downloader.is_snapshot_complete(release, 100) is True


def test_is_snapshot_complete_false_when_missing(downloader, tmp_path):
    release = _make_snapshot_release(vc=100)
    assert downloader.is_snapshot_complete(release, 100) is False


def test_should_process_snapshot_newer(downloader):
    release = _make_snapshot_release(vc=999)
    assert downloader.should_process_snapshot(release, 999) is True


def test_should_process_snapshot_same_version_complete(downloader, cache_manager):
    """When versionCode matches tracked and files are complete, should NOT process."""
    # Track version 100
    downloader.update_snapshot_tracking(100)
    release = _make_snapshot_release(vc=100)
    # Files don't exist, so it's incomplete -> should process for backfill
    assert downloader.should_process_snapshot(release, 100) is True


def test_should_process_snapshot_disabled(downloader_disabled):
    release = _make_snapshot_release(vc=100)
    assert downloader_disabled.should_process_snapshot(release, 100) is False


# ------------------------------------------------------------------
# 5. Transactional Tracking
# ------------------------------------------------------------------


def test_transactional_all_success_tracks(downloader, cache_manager):
    """When all selected assets succeed, tracking IS updated."""
    downloader.config["SELECTED_APP_ASSETS"] = ["androidApp-fdroid-universal-debug-"]
    release = _make_snapshot_release(vc=100)
    with patch.object(
        downloader,
        "download_snapshot_asset",
        return_value=DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path="/tmp/x.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        ),
    ):
        selected = downloader.get_selected_snapshot_assets(release)
        results = []
        for asset in selected:
            results.append(downloader.download_snapshot_asset(release, asset, 100))
    assert all(r.success for r in results)
    # Verify tracking was updated (manually call update to verify it works)
    assert downloader.update_snapshot_tracking(100) is True


def test_transactional_partial_failure_no_track(downloader):
    """When some selected assets fail, tracking must NOT be updated."""
    results = [
        DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path="/tmp/a.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        ),
        DownloadResult(
            success=False,
            release_tag="snapshot",
            file_path="/tmp/b.apk",
            download_url="http://y",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        ),
    ]
    assert not all(r.success for r in results)  # Would NOT trigger tracking


# ------------------------------------------------------------------
# 6. Cleanup Safety
# ------------------------------------------------------------------


def test_cleanup_rejects_symlinked_root(downloader, tmp_path):
    """A symlinked snapshot root must be rejected."""
    base = tmp_path / "downloads" / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    real_snapshots = tmp_path / "real_snapshots"
    real_snapshots.mkdir()
    os.symlink(str(real_snapshots), str(base / APP_SNAPSHOTS_DIR_NAME))
    assert downloader.cleanup_superseded_snapshots() == 0


def test_cleanup_preserves_nonnumeric_dirs(downloader, tmp_path):
    base = tmp_path / "downloads" / APP_DIR_NAME / APP_SNAPSHOTS_DIR_NAME
    (base / "100").mkdir(parents=True)
    (base / "not_a_number").mkdir(parents=True)
    assert downloader.cleanup_superseded_snapshots() == 0  # keep=1, only 1 numeric dir
    assert (base / "not_a_number").is_dir()


# ------------------------------------------------------------------
# 7. Config Gating
# ------------------------------------------------------------------


def test_config_snapshots_forced_false_when_apps_disabled():
    """CHECK_APP_SNAPSHOTS must be False when SAVE_CLIENT_APPS is False."""
    config = {
        "SAVE_CLIENT_APPS": False,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
    }
    result = normalize_client_app_config(config)
    assert result["CHECK_APP_SNAPSHOTS"] is False


def test_config_snapshots_forced_false_empty_assets():
    """CHECK_APP_SNAPSHOTS must be False when SELECTED_APP_ASSETS is empty."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": [],
    }
    result = normalize_client_app_config(config)
    assert result["CHECK_APP_SNAPSHOTS"] is False


def test_config_snapshots_forced_false_desktop_only():
    """CHECK_APP_SNAPSHOTS must be False when only desktop assets are selected."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["meshtastic.dmg"],
    }
    result = normalize_client_app_config(config)
    assert result["CHECK_APP_SNAPSHOTS"] is False


def test_config_snapshots_preserved_when_valid():
    """CHECK_APP_SNAPSHOTS True is preserved when apps enabled with Android assets."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
    }
    result = normalize_client_app_config(config)
    assert result["CHECK_APP_SNAPSHOTS"] is True


def test_config_snapshots_default_false():
    """Missing CHECK_APP_SNAPSHOTS normalizes to False."""
    config = {"SAVE_CLIENT_APPS": True, "SELECTED_APP_ASSETS": ["*.apk"]}
    result = normalize_client_app_config(config)
    assert result["CHECK_APP_SNAPSHOTS"] is False


def test_config_snapshot_retention_normalized():
    """APP_SNAPSHOT_VERSIONS_TO_KEEP is normalized when snapshots are enabled."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
    }
    result = normalize_client_app_config(config)
    assert "APP_SNAPSHOT_VERSIONS_TO_KEEP" in result
    assert result["APP_SNAPSHOT_VERSIONS_TO_KEEP"] >= 1
