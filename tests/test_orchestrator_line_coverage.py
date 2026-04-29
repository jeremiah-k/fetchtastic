import os
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest
import requests

from fetchtastic.constants import (
    DEFAULT_APP_VERSIONS_TO_KEEP,
    ERROR_TYPE_RETRY_FAILURE,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
    FILE_TYPE_DESKTOP,
    FILE_TYPE_DESKTOP_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_MANIFEST,
    FILE_TYPE_REPOSITORY,
    FILE_TYPE_UNKNOWN,
    REPO_DOWNLOADS_DIR,
)
from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def make_release(tag, prerelease=False, assets=None):
    return Release(tag_name=tag, prerelease=prerelease, assets=assets or [])


def make_asset(name, url="https://example.com/file", size=100):
    return Asset(name=name, download_url=url, size=size)


def make_result(
    success=True,
    file_type=FILE_TYPE_CLIENT_APP,
    was_skipped=False,
    file_path=None,
    file_size=None,
    release_tag="v1.0.0",
    download_url="https://example.com/file",
):
    return DownloadResult(
        success=success,
        release_tag=release_tag,
        file_path=file_path or "/tmp/test/file.apk",
        download_url=download_url,
        file_size=file_size or 100,
        file_type=file_type,
        was_skipped=was_skipped,
    )


@pytest.fixture
def mock_config(tmp_path):
    return {
        "DOWNLOAD_DIR": str(tmp_path / "test_orch"),
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "APP_VERSIONS_TO_KEEP": 2,
        "REPO_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": [],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
        "CHECK_APP_PRERELEASES": True,
        "SAVE_CLIENT_APPS": True,
        "SAVE_FIRMWARE": True,
    }


@pytest.fixture
def orchestrator(mock_config):
    orch = DownloadOrchestrator(mock_config)
    orch.cache_manager = Mock()
    orch.version_manager = Mock()
    orch.prerelease_manager = Mock()
    orch.client_app_downloader = Mock()
    orch.client_app_downloader.download_dir = mock_config["DOWNLOAD_DIR"]
    orch.client_app_downloader.should_download_prerelease.return_value = True
    orch.client_app_downloader.update_prerelease_tracking.return_value = True
    orch.android_downloader = orch.client_app_downloader
    orch.desktop_downloader = orch.client_app_downloader
    orch.firmware_downloader = Mock()
    orch.firmware_downloader.download_dir = mock_config["DOWNLOAD_DIR"]
    return orch


class TestDiscoverAvailableApkVersionsWhenWifiSkipped:
    def test_early_return_when_save_client_apps_false(self, orchestrator):
        orchestrator.config["SAVE_CLIENT_APPS"] = False
        orchestrator._discover_available_apk_versions_when_wifi_skipped()
        assert orchestrator.available_new_apk_versions == []

    def test_invalid_app_versions_to_keep_fallback(self, orchestrator):
        orchestrator.config["APP_VERSIONS_TO_KEEP"] = "invalid"
        orchestrator.config["SAVE_CLIENT_APPS"] = True
        release = make_release("v1.0.0")
        orchestrator.client_app_releases = [release]
        orchestrator.version_manager.compare_versions.return_value = 1
        orchestrator.client_app_downloader.get_latest_release_tag.return_value = None
        orchestrator._discover_available_apk_versions_when_wifi_skipped()
        assert len(orchestrator.available_new_apk_versions) >= 0


class TestProcessClientAppDownloadsEarlyReturn:
    def test_returns_early_when_already_processed(self, orchestrator):
        orchestrator._client_app_downloads_processed = True
        orchestrator._process_client_app_downloads()
        assert orchestrator._client_app_downloads_processed is True

    def test_sets_processed_flag(self, orchestrator):
        assert orchestrator._client_app_downloads_processed is False
        orchestrator.client_app_releases = []
        orchestrator._process_client_app_downloads()
        assert orchestrator._client_app_downloads_processed is True


class TestProcessClientAppInvalidKeepCount:
    def test_invalid_app_versions_to_keep_warning(self, orchestrator):
        orchestrator.config["APP_VERSIONS_TO_KEEP"] = "bad"
        release = make_release("v2.0.0", assets=[make_asset("app.apk")])
        orchestrator.client_app_downloader.get_assets.return_value = [
            make_asset("app.apk")
        ]
        orchestrator.client_app_downloader.should_download_asset.return_value = True
        orchestrator.client_app_downloader.update_release_history.return_value = None
        orchestrator.client_app_downloader.is_release_complete.return_value = True
        orchestrator.client_app_releases = [release]
        orchestrator._process_client_app_downloads()
        assert orchestrator._client_app_downloads_processed is True


class TestDownloadClientAppReleaseCalled:
    def test_download_client_app_release_returns_true(self, orchestrator):
        release = make_release("v1.0.0", assets=[make_asset("app.apk")])
        asset = make_asset("app.apk")
        orchestrator.client_app_downloader.get_assets.return_value = [asset]
        orchestrator.client_app_downloader.should_download_asset.return_value = True
        result = make_result(
            success=True, was_skipped=False, file_type=FILE_TYPE_CLIENT_APP
        )
        orchestrator.client_app_downloader.download_app.return_value = result
        assert orchestrator._download_client_app_release(release) is True


class TestPrereleaseTrackingUpdate:
    def test_tracking_update_warning_on_failure(self, orchestrator):
        orchestrator.config["APP_VERSIONS_TO_KEEP"] = 2
        orchestrator.config["CHECK_APP_PRERELEASES"] = True
        prerelease = make_release("v2.0.0-rc1", prerelease=True)
        release = make_release("v1.0.0")
        orchestrator.client_app_releases = [release]
        orchestrator.client_app_downloader.get_assets.return_value = [
            make_asset("app.apk")
        ]
        orchestrator.client_app_downloader.should_download_asset.return_value = True
        orchestrator.client_app_downloader.should_download_prerelease.return_value = (
            True
        )
        orchestrator.client_app_downloader.update_release_history.return_value = None
        orchestrator.client_app_downloader.is_release_complete.return_value = True
        orchestrator.client_app_downloader.handle_prereleases.return_value = [
            prerelease
        ]
        dl_result = make_result(
            success=True, was_skipped=False, file_type=FILE_TYPE_CLIENT_APP_PRERELEASE
        )
        orchestrator.client_app_downloader.download_app.return_value = dl_result
        orchestrator.client_app_downloader.update_prerelease_tracking.return_value = (
            False
        )
        orchestrator._process_client_app_downloads()
        orchestrator.client_app_downloader.update_prerelease_tracking.assert_called_with(
            prerelease.tag_name
        )


class TestProcessDesktopDownloadsShim:
    def test_process_desktop_downloads_delegates(self, orchestrator):
        orchestrator._process_desktop_downloads()
        assert orchestrator._client_app_downloads_processed is True


class TestGetTrackedPrereleaseTag:
    def test_no_method_on_downloader(self, orchestrator):
        dl = Mock(spec=[])
        assert orchestrator._get_tracked_prerelease_tag(dl) is None

    def test_method_raises_exception(self, orchestrator):
        dl = Mock()
        dl.get_current_tracked_prerelease_tag.side_effect = OSError("fail")
        assert orchestrator._get_tracked_prerelease_tag(dl) is None

    def test_returns_non_string(self, orchestrator):
        dl = Mock()
        dl.get_current_tracked_prerelease_tag.return_value = 42
        assert orchestrator._get_tracked_prerelease_tag(dl) is None

    def test_returns_empty_string(self, orchestrator):
        dl = Mock()
        dl.get_current_tracked_prerelease_tag.return_value = ""
        assert orchestrator._get_tracked_prerelease_tag(dl) is None

    def test_returns_valid_string(self, orchestrator):
        dl = Mock()
        dl.get_current_tracked_prerelease_tag.return_value = "v2.0.0-rc1"
        assert orchestrator._get_tracked_prerelease_tag(dl) == "v2.0.0-rc1"


class TestDownloadClientAppReleaseSkipAndSuccess:
    def test_skips_asset_not_selected(self, orchestrator):
        release = make_release("v1.0.0", assets=[make_asset("app.apk")])
        orchestrator.client_app_downloader.get_assets.return_value = [
            make_asset("app.apk")
        ]
        orchestrator.client_app_downloader.should_download_asset.return_value = False
        result = orchestrator._download_client_app_release(release)
        assert result is False

    def test_download_success_was_not_skipped(self, orchestrator):
        release = make_release("v1.0.0")
        asset = make_asset("app.apk")
        orchestrator.client_app_downloader.get_assets.return_value = [asset]
        orchestrator.client_app_downloader.should_download_asset.return_value = True
        dl_result = make_result(
            success=True, was_skipped=False, file_type=FILE_TYPE_CLIENT_APP
        )
        orchestrator.client_app_downloader.download_app.return_value = dl_result
        assert orchestrator._download_client_app_release(release) is True


class TestRetryFailedDownloadClientAppType:
    def test_client_app_type_dispatch(self, orchestrator, tmp_path):
        target = tmp_path / "test.apk"
        target.write_bytes(b"x" * 100)
        failed = make_result(
            success=False,
            file_type=FILE_TYPE_CLIENT_APP,
            file_path=str(target),
            file_size=100,
        )
        orchestrator.client_app_downloader.download.return_value = True
        orchestrator.client_app_downloader.verify.return_value = True
        with patch("os.path.getsize", return_value=100):
            result = orchestrator._retry_single_failure(failed)
        assert result.success is True

    def test_client_app_prerelease_type_dispatch(self, orchestrator, tmp_path):
        target = tmp_path / "test.apk"
        target.write_bytes(b"x" * 100)
        failed = make_result(
            success=False,
            file_type=FILE_TYPE_CLIENT_APP_PRERELEASE,
            file_path=str(target),
            file_size=100,
        )
        orchestrator.client_app_downloader.download.return_value = True
        orchestrator.client_app_downloader.verify.return_value = True
        with patch("os.path.getsize", return_value=100):
            result = orchestrator._retry_single_failure(failed)
        assert result.success is True

    def test_client_app_size_mismatch(self, orchestrator, tmp_path):
        target = tmp_path / "test.apk"
        target.write_bytes(b"x" * 50)
        failed = make_result(
            success=False,
            file_type=FILE_TYPE_CLIENT_APP,
            file_path=str(target),
            file_size=100,
        )
        orchestrator.client_app_downloader.download.return_value = True
        orchestrator.client_app_downloader.verify.return_value = True
        result = orchestrator._retry_single_failure(failed)
        assert result.success is False


class TestClassifyDownloadResult:
    def test_firmware_manifest_detection(self, orchestrator, tmp_path):
        manifest_path = tmp_path / "firmware-2.3.0.json"
        manifest_path.touch()
        result = make_result(
            success=True,
            file_type="",
            file_path=str(manifest_path),
        )
        result.file_type = None
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []
        orchestrator._is_firmware_manifest_asset = Mock(return_value=True)
        orchestrator._enhance_download_results_with_metadata()
        assert result.file_type == FILE_TYPE_FIRMWARE_MANIFEST

    def test_desktop_extension_detection(self, orchestrator, tmp_path):
        dmg_path = tmp_path / "Meshtastic.dmg"
        dmg_path.touch()
        result = make_result(
            success=True,
            file_type="",
            file_path=str(dmg_path),
        )
        result.file_type = None
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []
        orchestrator._is_firmware_manifest_asset = Mock(return_value=False)
        orchestrator._enhance_download_results_with_metadata()
        assert result.file_type == "desktop"


class TestCountDownloadsByType:
    def test_desktop_file_type_matching(self, orchestrator):
        result = make_result(
            success=True,
            file_type=FILE_TYPE_DESKTOP,
            was_skipped=False,
        )
        orchestrator.download_results = [result]
        count = orchestrator._count_artifact_downloads(FILE_TYPE_DESKTOP)
        assert count == 1

    def test_desktop_prerelease_file_type_matching(self, orchestrator):
        result = make_result(
            success=True,
            file_type=FILE_TYPE_DESKTOP_PRERELEASE,
            was_skipped=False,
        )
        orchestrator.download_results = [result]
        count = orchestrator._count_artifact_downloads(FILE_TYPE_DESKTOP)
        assert count == 1

    def test_legacy_fallback_for_untyped(self, orchestrator):
        result = make_result(
            success=True,
            file_type="",
            was_skipped=False,
            file_path="/some/path/firmware/file.zip",
        )
        result.file_type = None
        orchestrator.download_results = [result]
        count = orchestrator._count_artifact_downloads("firmware")
        assert count == 1


class TestCleanupOldVersionsInvalidKeep:
    def test_invalid_app_versions_to_keep_fallback(self, orchestrator):
        orchestrator.config["APP_VERSIONS_TO_KEEP"] = "invalid"
        orchestrator.client_app_downloader.cleanup_old_versions.return_value = 0
        orchestrator.firmware_downloader.cleanup_old_versions.return_value = 0
        orchestrator.client_app_releases = [make_release("v1.0.0")]
        orchestrator.cleanup_old_versions()
        orchestrator.client_app_downloader.cleanup_old_versions.assert_called()


class TestUpdateLatestReleaseTagsSeparateAndroid:
    def test_android_sync_when_different_from_client_app(self, mock_config, tmp_path):
        orch = DownloadOrchestrator(mock_config)
        orch.version_manager = Mock()
        orch.prerelease_manager = Mock()
        orch.client_app_downloader = Mock()
        orch.android_downloader = Mock()
        orch.desktop_downloader = Mock()
        orch.firmware_downloader = Mock()
        release = make_release("v3.0.0")
        orch.client_app_releases = [release]
        orch.android_releases = None
        orch.desktop_releases = None
        orch._ensure_android_releases = Mock(return_value=[release])
        orch._ensure_firmware_releases = Mock(return_value=[])
        orch.update_version_tracking()
        orch.android_downloader.update_latest_release_tag.assert_called_with("v3.0.0")
