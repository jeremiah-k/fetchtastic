from pathlib import Path

import pytest

from fetchtastic.constants import APP_DIR_NAME
from fetchtastic.download.android import (
    MeshtasticAndroidAppDownloader,
    _is_apk_prerelease,
    _is_apk_prerelease_by_name,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, DownloadResult, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def downloader(tmp_path):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["*.apk"],
        "CHECK_APP_PRERELEASES": True,
    }
    return MeshtasticAndroidAppDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )


def test_android_downloader_is_client_app_wrapper(downloader):
    assert isinstance(downloader, MeshtasticClientAppDownloader)


def test_android_wrapper_uses_unified_app_path(downloader):
    path = downloader.get_target_path_for_release("v2.7.14", "meshtastic.apk")

    assert Path(path).parts[-3:] == (APP_DIR_NAME, "v2.7.14", "meshtastic.apk")


def test_android_wrapper_uses_unified_prerelease_path(downloader):
    release = Release(tag_name="v2.7.14-open.1", prerelease=True)
    path = downloader.get_target_path_for_release(
        release.tag_name, "meshtastic.apk", release=release
    )

    assert Path(path).parts[-4:] == (
        APP_DIR_NAME,
        "prerelease",
        "v2.7.14-open.1",
        "meshtastic.apk",
    )


def test_download_apk_returns_client_app_file_type(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(
        name="meshtastic.apk",
        download_url="https://example.invalid/meshtastic.apk",
        size=1,
    )
    mock_download_app = mocker.patch.object(downloader, "download_app")
    mock_download_app.return_value = DownloadResult(
        success=True,
        release_tag="v2.7.14",
        file_type="client_app",
    )

    result = downloader.download_apk(release, asset)

    mock_download_app.assert_called_once_with(release, asset)
    assert result is mock_download_app.return_value
    assert result.file_type == "android"


def test_android_release_notes_use_single_client_app_file(downloader):
    release = Release(tag_name="v2.7.14", prerelease=False, body="notes")

    path = downloader.ensure_release_notes(release)

    assert path is not None
    assert Path(path).name == "release_notes-v2.7.14.md"


def test_android_prerelease_helpers_remain_available():
    assert _is_apk_prerelease_by_name("v2.7.14-open.1") is True
    assert _is_apk_prerelease_by_name("v2.6.0-open.1") is False
    assert _is_apk_prerelease({"tag_name": "v2.7.14-open.1", "prerelease": True})
    assert not _is_apk_prerelease({"tag_name": "v2.7.14", "prerelease": False})
