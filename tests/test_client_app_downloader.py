import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fetchtastic.constants import APP_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def cache_manager(tmp_path):
    cache = Mock(spec=CacheManager)
    cache.cache_dir = str(tmp_path / "cache")

    def _cache_path(file_name: str, suffix: str = ".json") -> str:
        path = Path(cache.cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        if suffix and not file_name.endswith(suffix):
            file_name = f"{file_name}{suffix}"
        return str(path / file_name)

    cache.get_cache_file_path.side_effect = _cache_path

    def _write_json(path, data):
        json_path = Path(path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data), encoding="utf-8")

    cache.atomic_write_json.side_effect = _write_json
    return cache


@pytest.fixture
def downloader(tmp_path, cache_manager):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release.apk", "meshtastic.dmg"],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_PRERELEASES": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


def test_migrates_legacy_apks_and_split_app_dirs(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    legacy_apk = downloads / "apks" / "v2.7.13"
    split_android = downloads / "app" / "android" / "v2.7.14"
    split_desktop = downloads / "app" / "desktop" / "v2.7.15"
    split_prerelease = downloads / "app" / "desktop" / "prerelease" / "v2.7.16-closed.1"
    for directory in (legacy_apk, split_android, split_desktop, split_prerelease):
        directory.mkdir(parents=True)
        (directory / "asset.txt").write_text("x", encoding="utf-8")

    downloader.migrate_legacy_layout()

    assert (downloads / "app" / "v2.7.13" / "asset.txt").exists()
    assert (downloads / "app" / "v2.7.14" / "asset.txt").exists()
    assert (downloads / "app" / "v2.7.15" / "asset.txt").exists()
    assert (
        downloads / "app" / "prerelease" / "v2.7.16-closed.1" / "asset.txt"
    ).exists()


def test_release_notes_use_single_client_app_file(downloader):
    release = Release(tag_name="v2.7.14", prerelease=False, body="notes")

    path = downloader.ensure_release_notes(release)

    assert path is not None
    assert Path(path).name == "release_notes-v2.7.14.md"
    assert Path(path).parent.name == "v2.7.14"
    assert Path(path).parent.parent.name == "app"


def test_cleanup_removes_stale_app_release_directories(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    for version in ("v2.7.14", "v2.7.13", "v2.7.12"):
        (downloads / APP_DIR_NAME / version).mkdir(parents=True)
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        published_at="2026-01-03T00:00:00Z",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="https://example.invalid/app.apk",
                size=None,
            )
        ],
    )

    downloader.cleanup_old_versions(1, cached_releases=[release])

    assert (downloads / APP_DIR_NAME / "v2.7.14").exists()
    assert not (downloads / APP_DIR_NAME / "v2.7.13").exists()
    assert not (downloads / APP_DIR_NAME / "v2.7.12").exists()
