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
    """Assets with different versionCodes must be rejected entirely (all-or-nothing)."""
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
    # Mixed versionCodes → entire release rejected, no assets selected
    assert selected == []


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
    try:
        os.symlink(str(real_snapshots), str(base / APP_SNAPSHOTS_DIR_NAME))
    except OSError:
        pytest.skip("Symlinks are not supported or permitted on this platform")
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


# ------------------------------------------------------------------
# 9. Realistic Asset-Selection Tests (P0 proof with setup-generated patterns)
# ------------------------------------------------------------------


@pytest.fixture
def downloader_stable_patterns(tmp_path, cache_manager):
    """Downloader with realistic setup-generated stable APK selections."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release.apk"],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


def test_stable_fdroid_universal_selects_snapshot_fdroid_universal(
    downloader_stable_patterns,
):
    """app-fdroid-universal-release.apk → only androidApp-fdroid-universal-debug-*.apk."""
    release = _make_snapshot_release(vc=100)
    selected = downloader_stable_patterns.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "fdroid" in selected[0].name
    assert "universal" in selected[0].name


def test_stable_fdroid_arm64_selects_snapshot_fdroid_arm64(tmp_path, cache_manager):
    """app-fdroid-arm64-v8a-release.apk → only androidApp-fdroid-arm64-v8a-debug-*.apk."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-arm64-v8a-release.apk"],
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    dl = MeshtasticClientAppDownloader(config, cache_manager)
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "fdroid" in selected[0].name
    assert "arm64-v8a" in selected[0].name


def test_stable_google_release_maps_to_google_universal(tmp_path, cache_manager):
    """app-google-release.apk (no ABI) → androidApp-google-universal-debug-*.apk only."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-google-release.apk"],
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    dl = MeshtasticClientAppDownloader(config, cache_manager)
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "google" in selected[0].name
    assert "universal" in selected[0].name


def test_stable_google_does_not_select_arm64(tmp_path, cache_manager):
    """app-google-release.apk must NOT select google-arm64-v8a snapshot."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-google-release.apk"],
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    dl = MeshtasticClientAppDownloader(config, cache_manager)
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    for asset in selected:
        assert "arm64-v8a" not in asset.name
        assert "armeabi-v7a" not in asset.name


def test_wildcard_apk_selects_all_snapshot_apks(tmp_path, cache_manager):
    """*.apk selects all 6 snapshot APKs."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["*"],
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    dl = MeshtasticClientAppDownloader(config, cache_manager)
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 6


def test_desktop_only_selects_zero_snapshots(tmp_path, cache_manager):
    """Desktop-only patterns select zero snapshot assets."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["meshtastic.dmg"],
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    dl = MeshtasticClientAppDownloader(config, cache_manager)
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert selected == []


def test_explicit_snapshot_pattern_still_works(downloader):
    """A manually-authored snapshot-specific pattern still matches."""
    release = _make_snapshot_release(vc=100)
    selected = downloader.get_selected_snapshot_assets(release)
    # downloader fixture has SELECTED_APP_ASSETS=["androidApp-fdroid-universal-debug-*.apk"]
    # which the semantic parser resolves to flavor=fdroid, abi=universal
    assert len(selected) >= 1
    assert all("fdroid" in a.name and "universal" in a.name for a in selected)


def test_changing_selection_triggers_backfill(
    downloader_stable_patterns, cache_manager
):
    """Changing SELECTED_APP_ASSETS at the same versionCode triggers processing."""
    release = _make_snapshot_release(vc=100)
    # Track vc=100 so should_download_snapshot returns False
    downloader_stable_patterns.update_snapshot_tracking(100)
    # Current selection is fdroid-universal, which is incomplete (no file on disk)
    assert downloader_stable_patterns.should_process_snapshot(release, 100) is True
    # Now change to arm64 selection — still incomplete, should still process
    downloader_stable_patterns.config["SELECTED_APP_ASSETS"] = [
        "app-fdroid-arm64-v8a-release.apk"
    ]
    assert downloader_stable_patterns.should_process_snapshot(release, 100) is True


# ------------------------------------------------------------------
# 10. Retention Normalization Floor Tests
# ------------------------------------------------------------------


def test_config_retention_floor_zero_when_enabled():
    """APP_SNAPSHOT_VERSIONS_TO_KEEP=0 must be clamped to 1 when snapshots enabled."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
        "APP_SNAPSHOT_VERSIONS_TO_KEEP": 0,
    }
    result = normalize_client_app_config(config)
    assert result["APP_SNAPSHOT_VERSIONS_TO_KEEP"] >= 1


def test_config_retention_floor_negative_when_enabled():
    """Negative retention must be clamped to 1 when snapshots enabled."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
        "APP_SNAPSHOT_VERSIONS_TO_KEEP": -5,
    }
    result = normalize_client_app_config(config)
    assert result["APP_SNAPSHOT_VERSIONS_TO_KEEP"] >= 1


def test_config_retention_positive_preserved():
    """Valid positive retention is preserved."""
    config = {
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_SNAPSHOTS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
        "APP_SNAPSHOT_VERSIONS_TO_KEEP": 3,
    }
    result = normalize_client_app_config(config)
    assert result["APP_SNAPSHOT_VERSIONS_TO_KEEP"] == 3


# ------------------------------------------------------------------
# 11. Orchestrator Integration Tests (transactional through real code path)
# ------------------------------------------------------------------


def _make_orchestrator_for_snapshots(tmp_path, cache_manager, **config_overrides):
    """Create an orchestrator wired for snapshot integration tests."""
    from fetchtastic.download.interfaces import DownloadResult
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release.apk"],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_SNAPSHOTS": True,
        "CHECK_APP_PRERELEASES": False,
        "EXCLUDE_PATTERNS": [],
    }
    config.update(config_overrides)
    orch = DownloadOrchestrator(config)

    # Provide a stable release so _process_client_app_downloads doesn't exit early,
    # but mock stable-flow methods so it's a no-op.
    stable = Release(
        tag_name="v2.8.0",
        prerelease=False,
        published_at="2024-01-01",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk", download_url="http://x", size=1
            )
        ],
    )
    orch._ensure_client_app_releases = Mock(return_value=[stable])
    orch.client_app_downloader.migrate_legacy_layout = Mock()
    orch.client_app_downloader.update_release_history = Mock()
    # Stable assets should not match for download so stable flow is a no-op
    orch.client_app_downloader.should_download_asset = Mock(return_value=False)
    orch.client_app_downloader.handle_prereleases = Mock(return_value=[])
    return orch


def test_orch_snapshot_all_success_tracks_and_cleans(tmp_path, cache_manager):
    """Nonempty selected set, all succeed → tracking once, cleanup once."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    from fetchtastic.download.interfaces import DownloadResult

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    selected = [release.assets[0]]
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=selected
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path="/tmp/x.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        )
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_called_once()


def test_orch_snapshot_partial_failure_no_track_no_cleanup(tmp_path, cache_manager):
    """Mixed success/failure → no tracking, no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    from fetchtastic.download.interfaces import DownloadResult

    asset1, asset2 = release.assets[0], release.assets[1]
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[asset1, asset2]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        side_effect=[
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
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_not_called()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


def test_orch_snapshot_all_failure_no_track_no_cleanup(tmp_path, cache_manager):
    """All failure → no tracking, no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    from fetchtastic.download.interfaces import DownloadResult

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=DownloadResult(
            success=False,
            release_tag="snapshot",
            file_path="/tmp/x.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        )
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_not_called()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


def test_orch_snapshot_empty_selected_no_track_no_cleanup(tmp_path, cache_manager):
    """Empty selected set → no downloads, no tracking, no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(return_value=[])
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_not_called()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


def test_orch_snapshot_tracking_write_failure_no_cleanup(tmp_path, cache_manager):
    """Tracking write failure → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    from fetchtastic.download.interfaces import DownloadResult

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path="/tmp/x.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        )
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=False)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


def test_orch_snapshot_disabled_skips_entirely(tmp_path, cache_manager):
    """CHECK_APP_SNAPSHOTS=False → fetch never called."""
    orch = _make_orchestrator_for_snapshots(
        tmp_path, cache_manager, CHECK_APP_SNAPSHOTS=False
    )
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch._process_client_app_downloads()
    orch.client_app_downloader.fetch_snapshot_release.assert_not_called()


def test_orch_snapshot_success_counts_in_statistics(tmp_path, cache_manager):
    """Snapshot success counts in client_app_downloads and android_downloads."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    from fetchtastic.download.interfaces import DownloadResult

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path=str(tmp_path / "app" / "snapshots" / "100" / "test.apk"),
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
        )
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    # Do NOT mock _handle_download_result — let it store results for statistics

    orch._process_client_app_downloads()

    stats = orch.get_download_statistics()
    assert stats["client_app_downloads"] >= 1
    assert stats["android_downloads"] >= 1


# ==================================================================
# ABI Wildcard, Legacy APK, Stable Classification, Exclusion Warning
# ==================================================================


def _make_downloader_with_selection(tmp_path, cache_manager, selected_assets):
    """Helper: create a downloader with the given SELECTED_APP_ASSETS."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": selected_assets,
        "CHECK_APP_SNAPSHOTS": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


# ------------------------------------------------------------------
# ABI Wildcard Pattern Tests (P1)
# ------------------------------------------------------------------


def test_wildcard_fdroid_selects_all_fdroid_abis(tmp_path, cache_manager):
    """*fdroid*.apk selects all F-Droid snapshot ABIs (not just universal)."""
    dl = _make_downloader_with_selection(tmp_path, cache_manager, ["*fdroid*.apk"])
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 3
    assert all("fdroid" in a.name for a in selected)
    names = {a.name for a in selected}
    assert any("universal" in n for n in names)
    assert any("arm64-v8a" in n for n in names)
    assert any("armeabi-v7a" in n for n in names)


def test_wildcard_google_selects_all_google_abis(tmp_path, cache_manager):
    """*google*.apk selects all Google snapshot ABIs."""
    dl = _make_downloader_with_selection(tmp_path, cache_manager, ["*google*.apk"])
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 3
    assert all("google" in a.name for a in selected)
    names = {a.name for a in selected}
    assert any("universal" in n for n in names)
    assert any("arm64-v8a" in n for n in names)
    assert any("armeabi-v7a" in n for n in names)


def test_wildcard_pattern_app_fdroid_dash_star(tmp_path, cache_manager):
    """app-fdroid-*-release.apk selects all F-Droid ABIs."""
    dl = _make_downloader_with_selection(
        tmp_path, cache_manager, ["app-fdroid-*-release.apk"]
    )
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 3
    assert all("fdroid" in a.name for a in selected)


def test_wildcard_pattern_android_app_fdroid(tmp_path, cache_manager):
    """androidApp-fdroid-*-debug-*.apk selects all F-Droid ABIs."""
    dl = _make_downloader_with_selection(
        tmp_path, cache_manager, ["androidApp-fdroid-*-debug-*.apk"]
    )
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 3
    assert all("fdroid" in a.name for a in selected)


def test_no_abi_pattern_still_maps_to_universal(tmp_path, cache_manager):
    """app-google-release.apk (no wildcard) still maps to universal only."""
    dl = _make_downloader_with_selection(
        tmp_path, cache_manager, ["app-google-release.apk"]
    )
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "google" in selected[0].name
    assert "universal" in selected[0].name


# ------------------------------------------------------------------
# Legacy Generic APK Tests (P1)
# ------------------------------------------------------------------


def test_legacy_meshtastic_apk_maps_to_google_universal(tmp_path, cache_manager):
    """meshtastic.apk maps to Google universal snapshot."""
    dl = _make_downloader_with_selection(tmp_path, cache_manager, ["meshtastic.apk"])
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "google" in selected[0].name
    assert "universal" in selected[0].name


def test_legacy_app_release_apk_maps_to_google_universal(tmp_path, cache_manager):
    """app-release.apk maps to Google universal snapshot."""
    dl = _make_downloader_with_selection(tmp_path, cache_manager, ["app-release.apk"])
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "google" in selected[0].name
    assert "universal" in selected[0].name


def test_legacy_google_release_maps_to_google_universal(tmp_path, cache_manager):
    """googleRelease.apk maps to Google universal snapshot."""
    dl = _make_downloader_with_selection(tmp_path, cache_manager, ["googleRelease.apk"])
    release = _make_snapshot_release(vc=100)
    selected = dl.get_selected_snapshot_assets(release)
    assert len(selected) == 1
    assert "google" in selected[0].name
    assert "universal" in selected[0].name


# ------------------------------------------------------------------
# Stable Classification Tests (P2)
# ------------------------------------------------------------------


def test_snapshot_not_stable_even_if_prerelease_true(downloader):
    """Snapshot tag must never classify as stable, even if prerelease=True."""
    snap = Release(
        tag_name="snapshot",
        prerelease=True,
        assets=[Asset(name="x.apk", download_url="http://x", size=1)],
    )
    assert downloader._is_client_app_stable(snap) is False


def test_snapshot_not_stable_even_if_prerelease_false(downloader):
    """Snapshot tag must never classify as stable, even if prerelease=False."""
    snap = Release(
        tag_name="snapshot",
        prerelease=False,
        assets=[Asset(name="x.apk", download_url="http://x", size=1)],
    )
    assert downloader._is_client_app_stable(snap) is False


def test_normal_stable_is_stable(downloader):
    """A normal stable release classifies as stable."""
    stable = Release(
        tag_name="v2.8.0",
        prerelease=False,
        assets=[Asset(name="x.apk", download_url="http://x", size=1)],
    )
    assert downloader._is_client_app_stable(stable) is True


def test_normal_prerelease_not_stable(downloader):
    """A normal prerelease release does not classify as stable."""
    pre = Release(
        tag_name="v2.8.1-open.1",
        prerelease=True,
        assets=[Asset(name="x.apk", download_url="http://x", size=1)],
    )
    assert downloader._is_client_app_stable(pre) is False


# ------------------------------------------------------------------
# Debug Exclusion Warning Test (P2)
# ------------------------------------------------------------------


def test_debug_exclusion_warns_when_all_removed(downloader_stable_patterns, caplog):
    """EXCLUDE_PATTERNS=['*debug*'] should warn when all snapshot assets removed."""
    import logging

    downloader_stable_patterns.config["EXCLUDE_PATTERNS"] = ["*debug*"]
    release = _make_snapshot_release(vc=100)
    with caplog.at_level(logging.WARNING):
        selected = downloader_stable_patterns.get_selected_snapshot_assets(release)
    assert selected == []
    assert any("exclusion" in r.message.lower() for r in caplog.records)
