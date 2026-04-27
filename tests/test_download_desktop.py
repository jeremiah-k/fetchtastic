from pathlib import Path

import pytest

from fetchtastic.constants import APP_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.desktop import (
    MeshtasticDesktopDownloader,
    _is_desktop_prerelease,
    _is_desktop_prerelease_by_name,
)
from fetchtastic.download.interfaces import Asset, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def downloader(tmp_path):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["*.dmg"],
        "CHECK_APP_PRERELEASES": True,
    }
    return MeshtasticDesktopDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )


def test_desktop_downloader_is_client_app_wrapper(downloader):
    assert isinstance(downloader, MeshtasticClientAppDownloader)


def test_desktop_wrapper_uses_unified_app_path(downloader):
    path = downloader.get_target_path_for_release("v2.7.14", "Meshtastic-2.7.14.dmg")

    assert Path(path).parts[-3:] == (
        APP_DIR_NAME,
        "v2.7.14",
        "Meshtastic-2.7.14.dmg",
    )


def test_desktop_wrapper_uses_unified_prerelease_path(downloader):
    release = Release(tag_name="v2.7.14-closed.17", prerelease=True)
    path = downloader.get_target_path_for_release(
        release.tag_name, "Meshtastic-2.7.14.dmg", release=release
    )

    assert Path(path).parts[-4:] == (
        APP_DIR_NAME,
        "prerelease",
        "v2.7.14-closed.17",
        "Meshtastic-2.7.14.dmg",
    )


def test_download_desktop_returns_client_app_file_type(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(
        name="Meshtastic-2.7.14.dmg",
        download_url="https://example.invalid/Meshtastic-2.7.14.dmg",
        size=1,
    )
    mocker.patch.object(
        downloader, "_is_asset_complete_for_target", side_effect=[False, True]
    )
    mocker.patch.object(downloader, "download", return_value=True)

    result = downloader.download_app(release, asset)

    assert result.success is True
    assert result.file_type == "client_app"


def test_desktop_release_notes_use_single_client_app_file(downloader):
    release = Release(tag_name="v2.7.14", prerelease=False, body="notes")

    path = downloader.ensure_release_notes(release)

    assert path is not None
    assert Path(path).name == "release_notes-v2.7.14.md"


def test_desktop_mismatch_helpers_are_noop_compatibility(downloader):
    assert downloader.has_known_2714_prerelease_version_mismatch() is False
    assert downloader.get_known_2714_prerelease_mismatch_tags() == []


def test_desktop_prerelease_helpers_remain_available():
    assert _is_desktop_prerelease_by_name("v2.7.14-closed.17") is True
    assert _is_desktop_prerelease_by_name("v2.6.0-closed.1") is False
    assert _is_desktop_prerelease({"tag_name": "v2.7.14-closed.17", "prerelease": True})
    assert not _is_desktop_prerelease({"tag_name": "v2.7.14", "prerelease": False})
