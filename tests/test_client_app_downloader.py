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


def test_move_legacy_path_rejects_symlinked_destination_ancestor(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "asset.txt").write_text("x", encoding="utf-8")
    real_target = tmp_path / "outside"
    real_target.mkdir()
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    symlink = app_dir / "linked"
    symlink.symlink_to(real_target, target_is_directory=True)

    moved = downloader._move_legacy_path(
        str(source), str(symlink / "v2.7.13" / "asset.txt")
    )

    assert moved is False
    assert (source / "asset.txt").exists()
    assert not (real_target / "v2.7.13" / "asset.txt").exists()


def test_ensure_prerelease_base_dir_rejects_symlinked_app_dir(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    outside = tmp_path / "outside-app"
    outside.mkdir()
    (downloads).mkdir(parents=True, exist_ok=True)
    (downloads / APP_DIR_NAME).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked client app dir"):
        downloader._ensure_prerelease_base_dir()


def test_ensure_prerelease_base_dir_rejects_symlinked_prerelease_dir(
    downloader, tmp_path
):
    downloads = tmp_path / "downloads"
    outside = tmp_path / "outside-prerelease"
    outside.mkdir()
    prerelease = downloads / APP_DIR_NAME / "prerelease"
    prerelease.parent.mkdir(parents=True, exist_ok=True)
    prerelease.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked client app prerelease dir"):
        downloader._ensure_prerelease_base_dir()


def test_ensure_prerelease_base_dir_rejects_realpath_outside_download_dir(
    downloader, tmp_path, mocker
):
    outside = tmp_path / "outside-realpath"
    outside.mkdir()
    import os

    original_realpath = os.path.realpath

    def _fake_realpath(path):
        path_obj = Path(path)
        if path_obj.name == "prerelease":
            return str(outside)
        return original_realpath(path)

    mocker.patch("os.path.realpath", side_effect=_fake_realpath)

    with pytest.raises(ValueError, match="unsafe client app prerelease dir"):
        downloader._ensure_prerelease_base_dir()


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
