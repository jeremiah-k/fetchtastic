# Tests for unified app/<version>/ client-app storage cleanup safety.

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from fetchtastic.constants import APP_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def _make_downloader(tmp_path) -> MeshtasticClientAppDownloader:
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_PRERELEASES": True,
        "APP_VERSIONS_TO_KEEP": 1,
        "SELECTED_APP_ASSETS": ["*.apk", "*.dmg"],
    }
    dl = MeshtasticClientAppDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )
    real_vm = VersionManager()
    dl.version_manager.get_release_tuple = real_vm.get_release_tuple
    dl.version_manager.is_prerelease_version = real_vm.is_prerelease_version
    return dl


def test_stale_stable_version_dir_is_deleted(tmp_path):
    dl = _make_downloader(tmp_path)
    version_dir = tmp_path / APP_DIR_NAME / "v2.7.13"
    version_dir.mkdir(parents=True)
    (version_dir / "app-universal.apk").write_bytes(b"apk")
    (version_dir / "Meshtastic-2.7.13.dmg").write_bytes(b"dmg")
    (version_dir / "release_notes-v2.7.13.md").write_text("notes")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert not version_dir.exists()


def test_stale_prerelease_version_dir_is_deleted(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_dir = tmp_path / APP_DIR_NAME / "prerelease" / "v2.7.13-open.1"
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "app-universal.apk").write_bytes(b"apk")
    dl.handle_prereleases = Mock(return_value=[])

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not prerelease_dir.exists()


def test_all_fetched_expected_prerelease_dirs_are_retained(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    tags = ["v2.7.14-closed.1", "v2.7.14-closed.17", "v2.7.14-open.1"]
    for tag in tags:
        version_dir = prerelease_base / tag
        version_dir.mkdir(parents=True)
        (version_dir / "app-universal.apk").write_bytes(b"apk")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.13", prerelease=False),
            *[Release(tag_name=tag, prerelease=True) for tag in tags],
        ]
    )

    for tag in tags:
        assert (prerelease_base / tag).exists()


def test_stable_release_supersedes_matching_prerelease_dirs(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    superseded = prerelease_base / "v2.7.14-closed.17"
    next_expected = prerelease_base / "v2.7.15-open.1"
    superseded.mkdir(parents=True)
    next_expected.mkdir(parents=True)

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.14-closed.17", prerelease=True),
            Release(tag_name="v2.7.15-open.1", prerelease=True),
        ]
    )

    assert not superseded.exists()
    assert next_expected.exists()


def test_unknown_non_version_entries_under_app_are_preserved(tmp_path):
    dl = _make_downloader(tmp_path)
    unknown_dir = tmp_path / APP_DIR_NAME / "manual-files"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "keep.txt").write_text("mine")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert unknown_dir.exists()
    assert (unknown_dir / "keep.txt").exists()


def test_cleanup_skips_symlinks(tmp_path):
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    target = tmp_path / "outside"
    app_dir.mkdir(parents=True)
    target.mkdir()
    link = app_dir / "v2.7.13"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert link.is_symlink()
    assert target.exists()


def test_prerelease_cleanup_skips_symlinks(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    target = tmp_path / "outside-prerelease"
    prerelease_base.mkdir(parents=True)
    target.mkdir()
    link = prerelease_base / "v2.7.14-closed.1"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.13", prerelease=False)]
    )

    assert link.is_symlink()
    assert target.exists()


def test_prerelease_cleanup_skips_unknown_non_version_entries(tmp_path):
    dl = _make_downloader(tmp_path)
    unknown_dir = tmp_path / APP_DIR_NAME / "prerelease" / "manual-files"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "keep.txt").write_text("mine")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.13", prerelease=False)]
    )

    assert unknown_dir.exists()
    assert (unknown_dir / "keep.txt").exists()


def test_release_notes_use_single_upstream_release_filename(tmp_path):
    dl = _make_downloader(tmp_path)
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        body="release notes",
        assets=[],
    )

    notes_path = dl.ensure_release_notes(release)

    assert notes_path is not None
    assert notes_path.endswith("release_notes-v2.7.14.md")
    assert Path(notes_path).name == "release_notes-v2.7.14.md"


def test_mixed_apk_and_desktop_assets_live_together(tmp_path):
    dl = _make_downloader(tmp_path)
    release = Release(tag_name="v2.7.14", prerelease=False)
    apk = Asset(
        name="app-universal.apk",
        download_url="https://example.invalid/app-universal.apk",
        size=None,
    )
    dmg = Asset(
        name="Meshtastic-2.7.14.dmg",
        download_url="https://example.invalid/Meshtastic-2.7.14.dmg",
        size=None,
    )

    apk_path = dl.get_target_path_for_release(
        release.tag_name, apk.name, release=release
    )
    dmg_path = dl.get_target_path_for_release(
        release.tag_name, dmg.name, release=release
    )

    expected_dir = tmp_path / APP_DIR_NAME / "v2.7.14"
    assert Path(apk_path).parent == expected_dir
    assert Path(dmg_path).parent == expected_dir


def test_legacy_platform_classes_use_client_app_lifecycle(tmp_path):
    from fetchtastic.download.android import MeshtasticAndroidAppDownloader
    from fetchtastic.download.desktop import MeshtasticDesktopDownloader

    config = {"DOWNLOAD_DIR": str(tmp_path), "SAVE_CLIENT_APPS": True}
    cache = CacheManager(cache_dir=str(tmp_path / "cache"))

    assert isinstance(
        MeshtasticAndroidAppDownloader(config, cache), MeshtasticClientAppDownloader
    )
    assert isinstance(
        MeshtasticDesktopDownloader(config, cache), MeshtasticClientAppDownloader
    )
