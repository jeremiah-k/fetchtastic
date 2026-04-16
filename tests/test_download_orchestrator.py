# Test Download Orchestrator
#
# Comprehensive unit tests for the DownloadOrchestrator class.

import time
from unittest.mock import Mock, patch

import pytest
import requests

from fetchtastic.download.interfaces import DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


class TestDownloadOrchestrator:
    """Test suite for DownloadOrchestrator."""

    @pytest.fixture
    def mock_config(self):
        """
        Provide a mock configuration dictionary used by the tests.

        Returns:
            dict: Configuration mapping used in test fixtures with keys:
                DOWNLOAD_DIR: download directory path.
                SAVE_APKS: whether to save Android APKs.
                SAVE_FIRMWARE: whether to save firmware files.
                CHECK_APK_PRERELEASES: whether to consider Android prerelease APKs.
                CHECK_FIRMWARE_PRERELEASES: whether to consider firmware prereleases.
                SELECTED_FIRMWARE_ASSETS: list of firmware asset names to select.
                EXCLUDE_PATTERNS: list of glob patterns to exclude assets/releases.
                GITHUB_TOKEN: token for authenticated GitHub requests.
        """
        return {
            "DOWNLOAD_DIR": "/tmp/test",
            "SAVE_APKS": True,
            "SAVE_FIRMWARE": True,
            "SAVE_DESKTOP_APP": True,
            "CHECK_APK_PRERELEASES": True,
            "CHECK_FIRMWARE_PRERELEASES": True,
            "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
            "EXCLUDE_PATTERNS": ["*debug*"],
            "GITHUB_TOKEN": "test_token",
        }

    @pytest.fixture
    def orchestrator(self, mock_config):
        """
        Create a DownloadOrchestrator for tests with core managers and downloaders replaced by mocks.

        The returned orchestrator has its cache_manager, version_manager, and prerelease_manager replaced with Mock objects, and its android_downloader and firmware_downloader replaced with Mock objects whose download_dir is set to "/tmp/test". The firmware_downloader mock is configured so `is_release_revoked()` returns False and `collect_non_revoked_releases(...)` returns a tuple of (initial_releases, initial_releases, current_fetch_limit) to preserve initial inputs.

        Parameters:
            mock_config (dict): Configuration dictionary passed to the DownloadOrchestrator constructor.

        Returns:
            DownloadOrchestrator: Test instance with mocked managers and downloaders and deterministic firmware helper behavior.
        """
        orch = DownloadOrchestrator(mock_config)
        # Mock the dependencies that are set in __init__
        orch.cache_manager = Mock()
        orch.version_manager = Mock()
        orch.prerelease_manager = Mock()
        # Mock the downloaders that are created in __init__
        orch.android_downloader = Mock()
        orch.android_downloader.download_dir = "/tmp/test"
        orch.android_downloader.should_download_prerelease.return_value = True
        orch.android_downloader.update_prerelease_tracking.return_value = True
        orch.firmware_downloader = Mock()
        orch.firmware_downloader.download_dir = "/tmp/test"
        orch.firmware_downloader.is_release_revoked = Mock(return_value=False)
        orch.desktop_downloader = Mock()
        orch.desktop_downloader.download_dir = "/tmp/test"
        orch.desktop_downloader.get_releases.return_value = []
        orch.desktop_downloader.handle_prereleases.return_value = []
        orch.desktop_downloader.should_download_prerelease.return_value = True
        orch.desktop_downloader.update_prerelease_tracking.return_value = True
        orch.desktop_downloader.update_release_history.return_value = {}

        def _collect_non_revoked(*, initial_releases, current_fetch_limit, **_unused):
            return initial_releases, initial_releases, current_fetch_limit

        orch.firmware_downloader.collect_non_revoked_releases = Mock(
            side_effect=_collect_non_revoked
        )
        return orch

    def test_init(self, mock_config):
        """Test orchestrator initialization."""
        with (
            patch("fetchtastic.download.orchestrator.CacheManager") as mock_cache,
            patch("fetchtastic.download.orchestrator.VersionManager") as mock_version,
            patch(
                "fetchtastic.download.orchestrator.PrereleaseHistoryManager"
            ) as mock_prerelease,
        ):
            orch = DownloadOrchestrator(mock_config)

            assert orch.config == mock_config
            mock_cache.assert_called_once()
            mock_version.assert_called_once()
            mock_prerelease.assert_called_once()

    def test_process_android_downloads_no_releases(self, orchestrator):
        """Android processing should stop when no releases are found."""
        orchestrator.android_downloader.get_releases.return_value = []
        orchestrator.config["SAVE_APKS"] = True

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.migrate_legacy_layout.assert_called_once()
        orchestrator.android_downloader.get_releases.assert_called_once()

    def test_get_release_check_workers_invalid_value(self, orchestrator):
        """Invalid worker config should fall back to default."""
        orchestrator.config["MAX_PARALLEL_RELEASE_CHECKS"] = "invalid"

        worker_count = orchestrator._get_release_check_workers()

        assert worker_count == 4

    def test_check_releases_complete_uses_parallel_executor(self, orchestrator):
        """Multiple releases should use bounded parallel completeness checks."""
        releases = [
            Release(tag_name="v1.0.0", prerelease=False, assets=[]),
            Release(tag_name="v1.0.1", prerelease=False, assets=[]),
        ]
        orchestrator.config["MAX_PARALLEL_RELEASE_CHECKS"] = 8
        checker = Mock(return_value=True)

        with patch("fetchtastic.download.orchestrator.ThreadPoolExecutor") as mock_pool:
            pool_ctx = mock_pool.return_value.__enter__.return_value
            # Mock submit() to return futures with result() method
            mock_futures = [
                Mock(result=Mock(return_value=True)),
                Mock(result=Mock(return_value=False)),
            ]
            pool_ctx.submit.side_effect = mock_futures

            results = orchestrator._check_releases_complete(releases, checker)

        assert results == [True, False]
        mock_pool.assert_called_once_with(max_workers=2)
        assert pool_ctx.submit.call_count == 2

    def test_process_android_downloads_skips_complete_and_prerelease_assets(
        self, orchestrator
    ):
        """Completed releases and skipped prerelease assets should be handled cleanly."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"

        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_asset.return_value = False

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.is_release_complete.assert_called_once()
        orchestrator.android_downloader.download_apk.assert_not_called()

    def test_process_firmware_downloads_no_releases(self, orchestrator):
        """Firmware processing should stop when no releases are found."""
        orchestrator.firmware_downloader.get_releases.return_value = []
        orchestrator.config["SAVE_FIRMWARE"] = True

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.get_releases.assert_called_once()

    def test_select_latest_release_by_version_all_revoked(self, orchestrator):
        """When all releases are revoked, fallback selection should still work."""
        orchestrator.firmware_downloader.is_release_revoked.return_value = True
        orchestrator.version_manager.get_release_tuple.side_effect = lambda tag: (
            (1, 0, 0) if tag == "v1.0.0" else None
        )

        releases = [
            Release(tag_name="junk", prerelease=False, assets=[]),
            Release(tag_name="v1.0.0", prerelease=False, assets=[]),
        ]

        selected = orchestrator._select_latest_release_by_version(releases)

        assert selected is not None
        assert selected.tag_name == "v1.0.0"

    def test_log_firmware_release_history_summary_filters_to_keep_limit(
        self, orchestrator
    ):
        """Summary reporting should use the configured keep limit."""
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 1
        orchestrator.config["KEEP_LAST_BETA"] = False
        orchestrator.firmware_release_history = {
            "entries": {
                "v1.0.0": {"tag_name": "v1.0.0", "status": "revoked"},
                "v0.9.0": {"tag_name": "v0.9.0", "status": "revoked"},
            }
        }
        orchestrator.firmware_releases = [
            Release(tag_name="v1.0.0", prerelease=False),
            Release(tag_name="v0.9.0", prerelease=False),
        ]

        manager = Mock()
        manager.expand_keep_limit_to_include_beta.return_value = 1
        kept_release = Release(tag_name="v1.0.0", prerelease=False)
        manager.get_releases_for_summary.return_value = [kept_release]
        orchestrator.firmware_downloader.release_history_manager = manager

        orchestrator.log_firmware_release_history_summary()

        manager.log_release_channel_summary.assert_called_once()
        manager.log_release_status_summary.assert_called_once()
        manager.log_duplicate_base_versions.assert_called_once()
        status_history = manager.log_release_status_summary.call_args[0][0]
        assert status_history["entries"] == {
            "v1.0.0": {"tag_name": "v1.0.0", "status": "revoked"}
        }
        duplicate_arg = manager.log_duplicate_base_versions.call_args[0][0]
        assert len(duplicate_arg) == 1
        assert duplicate_arg[0].tag_name == "v1.0.0"

    def test_log_firmware_release_history_summary_logs_prerelease_summary(
        self, orchestrator
    ):
        """Prerelease summaries are emitted with the other release history reports."""
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 1
        orchestrator.config["KEEP_LAST_BETA"] = False
        orchestrator.firmware_release_history = {"entries": {}}
        orchestrator.firmware_releases = [Release(tag_name="v1.0.0", prerelease=False)]

        manager = Mock()
        manager.expand_keep_limit_to_include_beta.return_value = 1
        manager.get_releases_for_summary.return_value = orchestrator.firmware_releases
        orchestrator.firmware_downloader.release_history_manager = manager

        summary_payload = {
            "history_entries": [{"identifier": "abc", "status": "active"}],
            "clean_latest_release": "v1.0.0",
            "expected_version": "1.0.1",
        }
        orchestrator.firmware_prerelease_summary = summary_payload
        orchestrator.firmware_downloader.log_prerelease_summary = Mock()

        orchestrator.log_firmware_release_history_summary()

        orchestrator.firmware_downloader.log_prerelease_summary.assert_called_once_with(
            summary_payload["history_entries"],
            "v1.0.0",
            "1.0.1",
        )
        assert orchestrator.firmware_prerelease_summary is None

    def test_select_latest_release_by_version_ignores_prerelease_flag(
        self, mock_config
    ):
        """Latest firmware should be selected by version, not GitHub prerelease flag."""
        orch = DownloadOrchestrator(mock_config)
        releases = [
            Release(tag_name="v2.7.15.567b8ea", prerelease=False, assets=[]),
            Release(tag_name="v2.7.16.a597230", prerelease=True, assets=[]),
        ]
        selected = orch._select_latest_release_by_version(releases)
        assert selected is not None
        assert selected.tag_name == "v2.7.16.a597230"

    def test_get_latest_versions_reports_android_prerelease(self, orchestrator):
        """Latest versions should prefer stable Android releases and surface prereleases."""
        orchestrator.android_releases = [
            Release(tag_name="v2.7.10-open.1", prerelease=True, assets=[]),
            Release(tag_name="v2.7.9", prerelease=False, assets=[]),
        ]
        orchestrator.desktop_releases = []
        orchestrator.android_downloader.get_latest_prerelease_tag = Mock(
            return_value="v2.7.10-open.1"
        )
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["android"] == "v2.7.9"
        assert versions["android_prerelease"] == "v2.7.10-open.1"

    def test_get_latest_versions_reports_desktop_versions(self, orchestrator):
        """Latest versions should include Desktop stable and prerelease tags."""
        orchestrator.android_releases = []
        orchestrator.desktop_releases = [
            Release(tag_name="v2.7.12-open.1", prerelease=True, assets=[]),
            Release(tag_name="v2.7.11", prerelease=False, assets=[]),
        ]
        orchestrator.android_downloader.get_latest_prerelease_tag = Mock(
            return_value=None
        )
        orchestrator.desktop_downloader = Mock()
        orchestrator.desktop_downloader.get_latest_prerelease_tag.return_value = (
            "v2.7.12-open.1"
        )
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["desktop"] == "v2.7.11"
        assert versions["desktop_prerelease"] == "v2.7.12-open.1"

    def test_firmware_prerelease_cleanup_only_removes_managed_dirs(self, tmp_path):
        """
        Ensure prerelease cleanup doesn't delete user-created directories.

        The orchestrator should only remove directories that look like Fetchtastic-managed
        firmware prerelease directories (firmware prefix + parseable version) and that
        are not recognized as prerelease directories.
        """
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "CHECK_APK_PRERELEASES": False,
            "CHECK_FIRMWARE_PRERELEASES": True,
            "SELECTED_FIRMWARE_ASSETS": [],
            "EXCLUDE_PATTERNS": [],
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)

        stable_like = prerelease_dir / "firmware-2.0.0"
        stable_like.mkdir()
        user_dir = prerelease_dir / "notes"
        user_dir.mkdir()
        custom_prefixed = prerelease_dir / "firmware-custom"
        custom_prefixed.mkdir()
        valid_prerelease = prerelease_dir / "firmware-2.0.0.abcdef"
        valid_prerelease.mkdir()

        orch.firmware_downloader.get_releases = Mock(
            return_value=[Release(tag_name="v1.0.0", prerelease=False)]
        )
        orch.firmware_downloader.is_release_complete = Mock(return_value=True)
        orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
            return_value=([], [], None, None)
        )

        orch._process_firmware_downloads()

        assert not stable_like.exists()
        assert user_dir.exists()
        assert custom_prefixed.exists()
        assert valid_prerelease.exists()

    @patch("fetchtastic.download.orchestrator.time.time")
    def test_run_download_pipeline_success(self, mock_time, orchestrator):
        """Test successful download pipeline execution."""
        mock_time.return_value = 1000.0

        # Mock the processing methods
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        orchestrator.run_download_pipeline()

        orchestrator._process_firmware_downloads.assert_called_once()
        orchestrator._process_android_downloads.assert_called_once()
        orchestrator._process_desktop_downloads.assert_called_once()
        orchestrator._enhance_download_results_with_metadata.assert_called_once()
        orchestrator._retry_failed_downloads.assert_called_once()
        orchestrator._log_download_summary.assert_called_once_with(1000.0)

    def test_run_download_pipeline_disabled_components(self, orchestrator):
        """Test pipeline execution skips disabled components."""
        # Mock the processing methods
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        # Disable all components (download methods handle config internally)
        orchestrator.config = {
            "DOWNLOAD_ANDROID": False,
            "DOWNLOAD_FIRMWARE": False,
        }

        orchestrator.run_download_pipeline()

        # Should still call retry and metadata enhancement
        orchestrator._retry_failed_downloads.assert_called_once()
        orchestrator._enhance_download_results_with_metadata.assert_called_once()
        orchestrator._log_download_summary.assert_called_once()

        # Processing methods should still be called (they check internally)
        orchestrator._process_android_downloads.assert_called_once()
        orchestrator._process_desktop_downloads.assert_called_once()
        orchestrator._process_firmware_downloads.assert_called_once()

    def test_process_android_downloads(self, orchestrator):
        """Test Android download processing."""
        # Mock releases
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v1.0.0"
        mock_release.prerelease = False
        orchestrator.android_downloader.get_releases.return_value = [mock_release]
        orchestrator.android_downloader.is_release_complete.return_value = False
        orchestrator.android_downloader.handle_prereleases.return_value = []
        orchestrator._download_android_release = Mock()

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.get_releases.assert_called_once()
        orchestrator.android_downloader.is_release_complete.assert_called_once_with(
            mock_release
        )
        orchestrator._download_android_release.assert_called_once_with(mock_release)

    def test_process_firmware_downloads(self, orchestrator):
        """Test firmware download processing."""
        # Mock releases
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = False
        orchestrator._download_firmware_release = Mock()
        # Mock _select_latest_release_by_version to avoid None issue.
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.get_releases.assert_called_once()
        orchestrator.firmware_downloader.is_release_complete.assert_called_once_with(
            mock_release
        )
        orchestrator._download_firmware_release.assert_called_once_with(mock_release)

    def test_download_android_release_success(self, orchestrator):
        """Test successful Android release download."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.apk"
        release.assets = [asset]

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        orchestrator.android_downloader.download_apk.return_value = mock_result
        orchestrator.android_downloader.get_assets.return_value = [asset]
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator._handle_download_result = Mock()

        orchestrator.config["FILTER_REVOKED_RELEASES"] = False

        orchestrator._download_android_release(release)

        orchestrator.android_downloader.get_assets.assert_called_once_with(release)
        orchestrator.android_downloader.download_apk.assert_called_once()
        orchestrator._handle_download_result.assert_called_once_with(
            mock_result, "android"
        )

    def test_download_firmware_release_success(self, orchestrator):
        """Test successful firmware release download."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        asset = Mock()
        asset.name = "firmware.zip"
        release.assets = [asset]

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_extract_result = Mock(spec=DownloadResult)
        mock_extract_result.success = (
            False  # Don't call _handle_download_result for extract
        )
        orchestrator.firmware_downloader.download_firmware.return_value = mock_result
        orchestrator.firmware_downloader.extract_firmware.return_value = (
            mock_extract_result
        )
        orchestrator.firmware_downloader.should_download_release.return_value = True
        orchestrator._handle_download_result = Mock()

        orchestrator._download_firmware_release(release)

        orchestrator.firmware_downloader.download_firmware.assert_called_once()
        # _handle_download_result is called for download and potentially extract
        assert orchestrator._handle_download_result.call_count >= 1
        # Check that it was called with the download result
        calls = orchestrator._handle_download_result.call_args_list
        assert any(call[0][0] == mock_result for call in calls)

    def test_download_firmware_release_processes_manifest_results(self, orchestrator):
        """Firmware release downloads should include manifest result handling."""
        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Mock(name="firmware-rak4631-2.7.20.abcdef0.mt.json"),
                Mock(name="firmware-rak4631-2.7.20.abcdef0.zip"),
            ],
        )
        release.assets[0].name = "firmware-rak4631-2.7.20.abcdef0.mt.json"
        release.assets[1].name = "firmware-rak4631-2.7.20.abcdef0.zip"

        manifest_result = Mock(spec=DownloadResult)
        manifest_result.success = True
        manifest_result.was_skipped = False
        binary_result = Mock(spec=DownloadResult)
        binary_result.success = True
        binary_result.was_skipped = False

        orchestrator.firmware_downloader.download_manifests.return_value = [
            manifest_result
        ]
        orchestrator.firmware_downloader.should_download_release.return_value = True
        orchestrator.firmware_downloader.download_firmware.return_value = binary_result
        orchestrator._handle_download_result = Mock()

        any_downloaded = orchestrator._download_firmware_release(release)

        assert any_downloaded is True
        assert any(
            call[0][0] == manifest_result and call[0][1] == "firmware_manifest"
            for call in orchestrator._handle_download_result.call_args_list
        )
        assert any(
            call[0][0] == binary_result and call[0][1] == "firmware"
            for call in orchestrator._handle_download_result.call_args_list
        )

    def test_download_firmware_release_skips_extract_for_revoked(self, orchestrator):
        """Revoked firmware skips extraction even when auto-extract is enabled."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        asset = Mock()
        asset.name = "firmware.zip"
        release.assets = [asset]

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True
        mock_result.error_type = "revoked_release"

        orchestrator.config["AUTO_EXTRACT"] = True
        orchestrator.firmware_downloader.download_firmware.return_value = mock_result
        orchestrator.firmware_downloader.should_download_release.return_value = True
        orchestrator._handle_download_result = Mock()

        orchestrator._download_firmware_release(release)

        orchestrator.firmware_downloader.extract_firmware.assert_not_called()

    def test_handle_download_result_success(self, orchestrator):
        """Test handling successful download result."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/test.apk"

        orchestrator._handle_download_result(result, "android")

        # Should add to download_results
        assert result in orchestrator.download_results

    def test_handle_download_result_failure(self, orchestrator):
        """Test handling failed download result."""
        result = Mock(spec=DownloadResult)
        result.success = False
        result.error_message = "Download failed"

        orchestrator._handle_download_result(result, "android")

        # Should add to failed downloads
        assert result in orchestrator.failed_downloads

    def test_retry_failed_downloads(self, orchestrator):
        """Test retry logic for failed downloads."""
        # Mock failed results
        failed_result = Mock(spec=DownloadResult)
        failed_result.success = False
        failed_result.is_retryable = True
        failed_result.retry_count = 0

        orchestrator.failed_downloads = [failed_result]
        orchestrator._retry_single_failure = Mock(return_value=failed_result)

        orchestrator._retry_failed_downloads()

        orchestrator._retry_single_failure.assert_called_once_with(failed_result)

    def test_is_download_retryable(self, orchestrator):
        """Test retryable download determination."""
        # Network error should be retryable
        result = Mock(spec=DownloadResult)
        result.error_type = "network_error"
        assert orchestrator._is_download_retryable(result) is True

        # Validation error should not be retryable
        result.error_type = "validation_error"
        assert orchestrator._is_download_retryable(result) is False

    def test_get_download_statistics(self, orchestrator):
        """Test download statistics calculation."""
        # Mock some results
        orchestrator.download_results = [
            Mock(success=True),
            Mock(success=True),
        ]
        orchestrator.failed_downloads = [
            Mock(success=False),
        ]

        stats = orchestrator.get_download_statistics()

        assert stats["total_downloads"] == 3
        assert stats["successful_downloads"] == 2
        assert stats["failed_downloads"] == 1
        assert "success_rate" in stats

    def test_cleanup_old_versions(self, orchestrator):
        """Test cleanup of old versions."""
        # Mock downloaders
        mock_android = Mock()
        mock_firmware = Mock()
        orchestrator.android_downloader = mock_android
        orchestrator.firmware_downloader = mock_firmware

        orchestrator.cleanup_old_versions()

        mock_android.cleanup_old_versions.assert_called_once_with(
            5, cached_releases=orchestrator.android_releases
        )
        mock_firmware.cleanup_old_versions.assert_called_once()

    def test_get_latest_versions(self, orchestrator):
        """Test getting latest versions from downloaders."""
        # Mock the return values for the downloaders
        mock_android_release = Mock(spec=Release)
        mock_android_release.tag_name = "v1.0.0"
        mock_android_release.prerelease = False
        orchestrator.android_downloader.get_releases.return_value = [
            mock_android_release
        ]
        orchestrator.desktop_releases = []
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v2.0.0"
        orchestrator.version_manager.extract_clean_version.return_value = "2.0.0"
        orchestrator.version_manager.calculate_expected_prerelease_version.return_value = (
            "2.0.1"
        )
        orchestrator.prerelease_manager.get_latest_active_prerelease_from_history.return_value = (
            "firmware-2.0.1.abcdef",
            [],
        )

        versions = orchestrator.get_latest_versions()

        assert versions["android"] == "v1.0.0"
        assert versions["firmware"] == "v2.0.0"
        assert versions["firmware_prerelease"] == "2.0.1.abcdef"
        orchestrator.firmware_downloader.get_latest_release_tag.assert_called_once()

    def test_update_version_tracking_prefers_stable_android_and_desktop(
        self, orchestrator
    ):
        """Version tracking should record stable Android/Desktop tags and latest firmware tag."""
        orchestrator.android_releases = [
            Release(tag_name="v2.7.12-open.1", prerelease=True),
            Release(tag_name="v2.7.11", prerelease=False),
        ]
        orchestrator.firmware_releases = [Release(tag_name="v2.7.20", prerelease=False)]
        orchestrator.desktop_releases = [
            Release(tag_name="v2.7.12-open.1", prerelease=True),
            Release(tag_name="v2.7.11", prerelease=False),
        ]
        orchestrator.android_downloader.update_latest_release_tag = Mock()
        orchestrator.firmware_downloader.update_latest_release_tag = Mock()
        orchestrator.desktop_downloader = Mock()
        orchestrator._manage_prerelease_tracking = Mock()

        orchestrator.update_version_tracking()

        orchestrator.android_downloader.update_latest_release_tag.assert_called_once_with(
            "v2.7.11"
        )
        orchestrator.firmware_downloader.update_latest_release_tag.assert_called_once_with(
            "v2.7.20"
        )
        orchestrator.desktop_downloader.update_latest_release_tag.assert_called_once_with(
            "v2.7.11"
        )

    @patch("fetchtastic.download.orchestrator.logger")
    def test_log_download_summary(self, mock_logger, orchestrator):
        """Test download summary logging."""
        start_time = 1000.0

        # Mock statistics
        orchestrator.get_download_statistics = Mock(
            return_value={
                "total_downloads": 5,
                "successful_downloads": 4,
                "failed_downloads": 1,
                "success_rate": 80.0,
            }
        )

        orchestrator._log_download_summary(start_time)

        # Should log summary information
        mock_logger.info.assert_called()

    def test_enhance_download_results_with_metadata(self, orchestrator):
        """Test enhancing results with metadata."""
        # Setup test data
        result = Mock(spec=DownloadResult)
        result.success = False
        result.file_path = "/path/to/file.apk"
        result.file_type = None
        result.error_type = None
        result.retry_count = None
        orchestrator.download_results = []
        orchestrator.failed_downloads = [result]

        orchestrator._enhance_download_results_with_metadata()

        # Verify metadata was populated
        assert isinstance(result.file_type, str)
        assert result.file_type != ""
        assert result.retry_count == 0
        assert isinstance(result.is_retryable, bool)
        assert result.is_retryable is orchestrator._is_download_retryable(result)

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_non_termux(self):
        """is_connected_to_wifi returns True on non-Termux platforms."""
        with patch("fetchtastic.download.orchestrator.is_termux", return_value=False):
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is True

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_success(self):
        """is_connected_to_wifi returns True when Termux API reports connected."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout='{"supplicant_state": "COMPLETED", "ip": "192.168.1.100"}',
                stderr="",
            )
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is True

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_non_zero_exit(self):
        """is_connected_to_wifi returns False when Termux API exits non-zero."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("fetchtastic.download.orchestrator.logger") as mock_logger,
        ):
            mock_run.return_value = Mock(
                returncode=1, stdout="", stderr="error message"
            )
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False
            mock_logger.warning.assert_called()

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_empty_output(self):
        """is_connected_to_wifi returns False when Termux API returns empty output."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_non_dict_json(self):
        """is_connected_to_wifi returns False when JSON is not a dict."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = Mock(returncode=0, stdout='"not a dict"', stderr="")
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_json_decode_error(self):
        """is_connected_to_wifi returns False on JSON decode error."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("fetchtastic.download.orchestrator.logger") as mock_logger,
        ):
            mock_run.return_value = Mock(returncode=0, stdout="not json", stderr="")
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False
            mock_logger.warning.assert_called()

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_file_not_found(self):
        """is_connected_to_wifi returns False when termux-wifi-connectioninfo not found."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run", side_effect=FileNotFoundError()),
            patch("fetchtastic.download.orchestrator.logger") as mock_logger,
        ):
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False
            mock_logger.warning.assert_called()

    @pytest.mark.infrastructure
    def test_is_connected_to_wifi_termux_os_error(self):
        """is_connected_to_wifi returns False on OSError."""
        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch("subprocess.run", side_effect=OSError("test error")),
            patch("fetchtastic.download.orchestrator.logger") as mock_logger,
        ):
            from fetchtastic.download.orchestrator import is_connected_to_wifi

            assert is_connected_to_wifi() is False
            mock_logger.warning.assert_called()

    def test_process_desktop_downloads_disabled(self, orchestrator):
        """Desktop processing should skip when disabled in config."""
        orchestrator.config["SAVE_DESKTOP_APP"] = False

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.get_releases.assert_not_called()

    def test_process_desktop_downloads_no_releases(self, orchestrator):
        """Desktop processing should stop when no releases are found."""
        orchestrator.desktop_downloader.get_releases.return_value = []
        orchestrator.config["SAVE_DESKTOP_APP"] = True

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.get_releases.assert_called_once()

    def test_process_desktop_downloads_complete_release(self, orchestrator):
        """Desktop processing should skip complete releases."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = []
        orchestrator._download_desktop_release = Mock()

        orchestrator._process_desktop_downloads()

        orchestrator._download_desktop_release.assert_not_called()

    def test_process_desktop_downloads_with_prerelease(self, orchestrator):
        """Desktop processing should handle prerelease assets."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.config["CHECK_DESKTOP_PRERELEASES"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.dmg")]
        )
        prerelease.assets[0].name = "app.dmg"
        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.is_release_complete.return_value = False
        orchestrator.desktop_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        orchestrator.desktop_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.desktop_downloader.download_desktop.return_value = mock_result
        orchestrator._download_desktop_release = Mock(return_value=True)

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.download_desktop.assert_called_once()

    @patch("fetchtastic.download.orchestrator.logger")
    def test_process_desktop_downloads_error_handling(self, mock_logger, orchestrator):
        """Desktop processing should handle exceptions gracefully."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.desktop_downloader.get_releases.side_effect = OSError("test error")

        orchestrator._process_desktop_downloads()

        # Verify the mocked method was called
        orchestrator.desktop_downloader.get_releases.assert_called_once()
        # Verify error was logged
        mock_logger.error.assert_called()

    def test_download_desktop_release_success(self, orchestrator):
        """Test successful desktop release download."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.dmg"
        orchestrator.desktop_downloader.get_assets.return_value = [asset]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.desktop_downloader.download_desktop.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        result = orchestrator._download_desktop_release(release)

        assert result is True
        orchestrator.desktop_downloader.download_desktop.assert_called_once()

    def test_download_desktop_release_skipped(self, orchestrator):
        """Test desktop release download when skipped."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.dmg"
        orchestrator.desktop_downloader.get_assets.return_value = [asset]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True
        orchestrator.desktop_downloader.download_desktop.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        result = orchestrator._download_desktop_release(release)

        assert result is False

    def test_download_desktop_release_error(self, orchestrator):
        """Test desktop release download with error."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.dmg"
        orchestrator.desktop_downloader.get_assets.return_value = [asset]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        orchestrator.desktop_downloader.download_desktop.side_effect = OSError(
            "test error"
        )

        result = orchestrator._download_desktop_release(release)

        assert result is False

    def test_download_android_release_error(self, orchestrator):
        """Test Android release download with error."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.apk"
        release.assets = [asset]
        orchestrator.android_downloader.get_assets.return_value = [asset]
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.download_apk.side_effect = OSError("test error")

        result = orchestrator._download_android_release(release)

        assert result is False

    def test_ensure_releases_with_zero_limit(self, orchestrator):
        """_ensure_releases should return empty list when limit is 0."""
        result = orchestrator._ensure_android_releases(limit=0)
        assert result == []

    def test_ensure_releases_with_cached_and_limit(self, orchestrator):
        """_ensure_releases should slice cached releases when limit is set."""
        releases = [
            Release(tag_name="v1.0.0", prerelease=False, assets=[]),
            Release(tag_name="v2.0.0", prerelease=False, assets=[]),
        ]
        orchestrator.android_releases = releases
        orchestrator._android_releases_fetch_limit = 10

        result = orchestrator._ensure_android_releases(limit=1)

        assert len(result) == 1
        assert result[0].tag_name == "v1.0.0"

    def test_check_releases_complete_empty(self, orchestrator):
        """_check_releases_complete should return empty list for no releases."""
        result = orchestrator._check_releases_complete([], Mock())
        assert result == []

    def test_check_releases_complete_exception(self, orchestrator):
        """_check_releases_complete should handle exceptions in checker."""
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        checker = Mock(side_effect=ValueError("test error"))

        result = orchestrator._check_releases_complete([release], checker)

        assert result == [False]

    def test_handle_download_result_skipped_prerelease(self, orchestrator):
        """_handle_download_result should not log debug for skipped prereleases."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.was_skipped = True
        result.release_tag = "v1.0.0"

        orchestrator._handle_download_result(result, "android_prerelease")

        assert result in orchestrator.download_results

    def test_handle_download_result_skipped_non_prerelease(self, orchestrator):
        """_handle_download_result should log debug for skipped non-prerelease."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.was_skipped = True
        result.release_tag = "v1.0.0"

        orchestrator._handle_download_result(result, "android")

        assert result in orchestrator.download_results

    def test_process_firmware_downloads_symlink_cleanup(self, tmp_path):
        """Firmware processing should skip symlinks in prerelease cleanup."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "CHECK_FIRMWARE_PRERELEASES": False,
            "SELECTED_FIRMWARE_ASSETS": [],
            "EXCLUDE_PATTERNS": [],
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        symlink_target = tmp_path / "target"
        symlink_target.mkdir()
        symlink = prerelease_dir / "firmware-2.0.0.abcdef"
        try:
            symlink.symlink_to(symlink_target)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        orch.firmware_downloader.get_releases = Mock(
            return_value=[Release(tag_name="v1.0.0", prerelease=False)]
        )
        orch.firmware_downloader.is_release_complete = Mock(return_value=True)
        orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
            return_value=([], [], None, None)
        )

        orch._process_firmware_downloads()

        assert symlink.exists()

    def test_process_firmware_downloads_non_dir_cleanup(self, tmp_path):
        """Firmware processing should skip non-directory items in prerelease cleanup."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "CHECK_FIRMWARE_PRERELEASES": False,
            "SELECTED_FIRMWARE_ASSETS": [],
            "EXCLUDE_PATTERNS": [],
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        file_item = prerelease_dir / "firmware-2.0.0.abcdef.txt"
        file_item.write_text("test")

        orch.firmware_downloader.get_releases = Mock(
            return_value=[Release(tag_name="v1.0.0", prerelease=False)]
        )
        orch.firmware_downloader.is_release_complete = Mock(return_value=True)
        orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
            return_value=([], [], None, None)
        )

        orch._process_firmware_downloads()

        assert file_item.exists()

    def test_process_firmware_downloads_unparsable_version_cleanup(self, tmp_path):
        """Firmware processing should skip directories with unparsable versions."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "CHECK_FIRMWARE_PRERELEASES": False,
            "SELECTED_FIRMWARE_ASSETS": [],
            "EXCLUDE_PATTERNS": [],
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        unparsable_dir = prerelease_dir / "firmware-invalid-version"
        unparsable_dir.mkdir()

        orch.firmware_downloader.get_releases = Mock(
            return_value=[Release(tag_name="v1.0.0", prerelease=False)]
        )
        orch.firmware_downloader.is_release_complete = Mock(return_value=True)
        orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
            return_value=([], [], None, None)
        )

        orch._process_firmware_downloads()

        assert unparsable_dir.exists()

    def test_process_firmware_downloads_error(self, orchestrator):
        """Firmware processing should handle exceptions gracefully."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.firmware_downloader.get_releases.side_effect = OSError(
            "test error"
        )

        orchestrator._process_firmware_downloads()

    def test_download_firmware_release_no_assets_matched(self, orchestrator):
        """Firmware download should return False when no assets match."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        release.assets = []
        orchestrator.firmware_downloader.download_manifests.return_value = []
        orchestrator.firmware_downloader.should_download_release.return_value = False

        result = orchestrator._download_firmware_release(release)

        assert result is False

    def test_download_firmware_release_with_extraction(self, orchestrator):
        """Firmware download should extract when AUTO_EXTRACT is enabled."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        asset = Mock()
        asset.name = "firmware.zip"
        release.assets = [asset]
        orchestrator.config["AUTO_EXTRACT"] = True
        orchestrator.firmware_downloader.download_manifests.return_value = []
        orchestrator.firmware_downloader.should_download_release.return_value = True
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.firmware_downloader.download_firmware.return_value = mock_result
        mock_extract_result = Mock(spec=DownloadResult)
        orchestrator.firmware_downloader.extract_firmware.return_value = (
            mock_extract_result
        )
        orchestrator._handle_download_result = Mock()

        orchestrator._download_firmware_release(release)

        orchestrator.firmware_downloader.extract_firmware.assert_called_once()

    def test_download_firmware_release_error(self, orchestrator):
        """Firmware download should handle exceptions gracefully."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        release.assets = []
        orchestrator.firmware_downloader.download_manifests.side_effect = OSError(
            "test error"
        )

        result = orchestrator._download_firmware_release(release)

        assert result is False

    def test_retry_failed_downloads_with_sleep(self, orchestrator):
        """Test retry with sleep delay."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.success = False
        failed_result.is_retryable = True
        failed_result.retry_count = 0
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.apk"
        failed_result.file_path = "/tmp/file.apk"
        failed_result.error_type = "network_error"
        failed_result.file_type = "android"
        failed_result.error_message = "test error"
        failed_result.file_size = 1000

        orchestrator.config["RETRY_DELAY_SECONDS"] = 1
        orchestrator.config["MAX_RETRIES"] = 1
        orchestrator.failed_downloads = [failed_result]
        retry_result = Mock(spec=DownloadResult)
        retry_result.success = True
        retry_result.was_skipped = False
        retry_result.file_path = "/tmp/file.apk"
        retry_result.file_type = "android"
        orchestrator._retry_single_failure = Mock(return_value=retry_result)

        with patch("fetchtastic.download.orchestrator.time.sleep"):
            orchestrator._retry_failed_downloads()

        assert failed_result in orchestrator.download_results

    def test_retry_failed_downlogs_max_retries_exceeded(self, orchestrator):
        """Test retry when max retries is exceeded in retryable check."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.success = False
        failed_result.is_retryable = True
        failed_result.retry_count = 3
        failed_result.release_tag = "v1.0.0"

        orchestrator.config["MAX_RETRIES"] = 3
        orchestrator.failed_downloads = [failed_result]

        orchestrator._retry_failed_downloads()

        assert failed_result in orchestrator.failed_downloads

    def test_retry_failed_downloads_exception_during_retry(self, orchestrator):
        """Test retry when exception occurs during retry."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.success = False
        failed_result.is_retryable = True
        failed_result.retry_count = 0
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.apk"
        failed_result.file_path = "/tmp/file.apk"
        failed_result.error_type = "network_error"
        failed_result.file_type = "android"
        failed_result.error_message = "test error"
        failed_result.file_size = 1000

        orchestrator.config["RETRY_DELAY_SECONDS"] = 0
        orchestrator.config["MAX_RETRIES"] = 3
        orchestrator.failed_downloads = [failed_result]
        orchestrator._retry_single_failure = Mock(side_effect=OSError("retry failed"))

        orchestrator._retry_failed_downloads()

        assert failed_result.is_retryable is False

    def test_retry_single_failure_missing_url(self, orchestrator):
        """_retry_single_failure should handle missing URL."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = None
        failed_result.file_path = "/tmp/file.apk"
        failed_result.retry_count = 0
        failed_result.file_type = "android"
        failed_result.file_size = 1000

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False
        assert "missing URL" in result.error_message

    def test_retry_single_failure_missing_path(self, orchestrator):
        """_retry_single_failure should handle missing target path."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.apk"
        failed_result.file_path = None
        failed_result.retry_count = 0
        failed_result.file_type = "android"
        failed_result.file_size = 1000

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False

    def test_retry_single_failure_unsupported_file_type(self, orchestrator):
        """_retry_single_failure should handle unsupported file types."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.dat"
        failed_result.file_path = "/tmp/file.dat"
        failed_result.retry_count = 0
        failed_result.file_type = "unknown"
        failed_result.file_size = 1000

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False

    def test_retry_single_failure_firmware_type(self, orchestrator):
        """_retry_single_failure should use firmware downloader for firmware type."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/firmware.zip"
        failed_result.file_path = "/tmp/firmware.zip"
        failed_result.retry_count = 0
        failed_result.file_type = "firmware"
        failed_result.file_size = 1000

        orchestrator.firmware_downloader.download = Mock(return_value=True)
        orchestrator.firmware_downloader.verify = Mock(return_value=True)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is True
        orchestrator.firmware_downloader.download.assert_called_once()

    def test_retry_single_failure_desktop_type(self, orchestrator, tmp_path):
        """_retry_single_failure should use desktop downloader for desktop type."""
        target_file = tmp_path / "app.dmg"
        target_file.write_bytes(b"x" * 1000)

        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/app.dmg"
        failed_result.file_path = str(target_file)
        failed_result.retry_count = 0
        failed_result.file_type = "desktop"
        failed_result.file_size = 1000

        orchestrator.desktop_downloader.download = Mock(return_value=True)
        orchestrator.desktop_downloader.verify = Mock(return_value=True)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is True
        orchestrator.desktop_downloader.download.assert_called_once()

    def test_retry_single_failure_desktop_prerelease_type(self, orchestrator, tmp_path):
        """_retry_single_failure should use desktop downloader for desktop_prerelease type."""
        target_file = tmp_path / "app.dmg"
        target_file.write_bytes(b"x" * 1000)

        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/app.dmg"
        failed_result.file_path = str(target_file)
        failed_result.retry_count = 0
        failed_result.file_type = "desktop_prerelease"
        failed_result.file_size = 1000

        orchestrator.desktop_downloader.download = Mock(return_value=True)
        orchestrator.desktop_downloader.verify = Mock(return_value=True)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is True

    def test_retry_single_failure_android_prerelease_type(self, orchestrator):
        """_retry_single_failure should use Android downloader for android_prerelease type."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0-open.1"
        failed_result.download_url = "https://example.com/app.apk"
        failed_result.file_path = "/tmp/app.apk"
        failed_result.retry_count = 0
        failed_result.file_type = "android_prerelease"
        failed_result.file_size = 1000

        orchestrator.android_downloader.download = Mock(return_value=True)
        orchestrator.android_downloader.verify = Mock(return_value=True)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is True
        orchestrator.android_downloader.download.assert_called_once()

    def test_retry_single_failure_firmware_manifest_type(self, orchestrator, tmp_path):
        """_retry_single_failure should use firmware downloader for firmware_manifest type."""
        from fetchtastic.constants import FILE_TYPE_FIRMWARE_MANIFEST

        target_file = tmp_path / "firmware.json"
        target_file.write_text("{}", encoding="utf-8")

        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/firmware.json"
        failed_result.file_path = str(target_file)
        failed_result.retry_count = 0
        failed_result.file_type = FILE_TYPE_FIRMWARE_MANIFEST
        failed_result.file_size = 1000

        orchestrator.firmware_downloader.download = Mock(return_value=True)
        orchestrator.firmware_downloader.verify = Mock(return_value=True)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is True

    def test_retry_single_failure_firmware_manifest_invalid_json(
        self, orchestrator, tmp_path
    ):
        """Manifest retries should fail when downloaded JSON content is malformed."""
        from fetchtastic.constants import FILE_TYPE_FIRMWARE_MANIFEST

        target_file = tmp_path / "firmware.json"
        target_file.write_text("{", encoding="utf-8")

        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/firmware.json"
        failed_result.file_path = str(target_file)
        failed_result.retry_count = 0
        failed_result.file_type = FILE_TYPE_FIRMWARE_MANIFEST
        failed_result.file_size = 1000

        orchestrator.firmware_downloader.download = Mock(return_value=True)
        orchestrator.firmware_downloader.verify = Mock(return_value=True)
        orchestrator.firmware_downloader.cleanup_file = Mock()

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False
        orchestrator.firmware_downloader.cleanup_file.assert_called_once_with(
            str(target_file)
        )

    def test_retry_single_failure_download_succeeds_verify_fails(self, orchestrator):
        """_retry_single_failure should fail when download succeeds but verify fails."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.apk"
        failed_result.file_path = "/tmp/file.apk"
        failed_result.retry_count = 0
        failed_result.file_type = "android"
        failed_result.file_size = 1000

        orchestrator.android_downloader.download = Mock(return_value=True)
        orchestrator.android_downloader.verify = Mock(return_value=False)

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False

    def test_retry_single_failure_exception(self, orchestrator):
        """_retry_single_failure should handle exceptions."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.download_url = "https://example.com/file.apk"
        failed_result.file_path = "/tmp/file.apk"
        failed_result.retry_count = 0
        failed_result.file_type = "android"
        failed_result.file_size = 1000

        orchestrator.android_downloader.download = Mock(
            side_effect=OSError("download error")
        )

        result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False
        assert result.is_retryable is False

    def test_create_failure_result_with_override(self, orchestrator):
        """_create_failure_result should respect is_retryable_override."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.file_size = 1000
        failed_result.retry_count = 0
        failed_result.retry_timestamp = "2024-01-01 00:00:00"

        from pathlib import Path

        result = orchestrator._create_failure_result(
            failed_result,
            Path("/tmp/file.apk"),
            "https://example.com/file.apk",
            "android",
            "test error",
            is_retryable_override=True,
        )

        assert result.success is False
        assert result.is_retryable is True

    def test_create_failure_result_with_exception_message(self, orchestrator):
        """_create_failure_result should prefer exception_message."""
        failed_result = Mock(spec=DownloadResult)
        failed_result.release_tag = "v1.0.0"
        failed_result.file_size = 1000
        failed_result.retry_count = 0
        failed_result.retry_timestamp = "2024-01-01 00:00:00"

        from pathlib import Path

        result = orchestrator._create_failure_result(
            failed_result,
            Path("/tmp/file.apk"),
            "https://example.com/file.apk",
            "android",
            "test error",
            exception_message="exception message",
        )

        assert result.error_message == "exception message"

    def test_generate_retry_report_with_retryable(self, orchestrator):
        """_generate_retry_report should handle retryable failures."""
        retryable = Mock(spec=DownloadResult)
        retryable.file_type = "android"
        retryable.retry_count = 1
        non_retryable = Mock(spec=DownloadResult)
        non_retryable.error_type = "validation_error"

        orchestrator.failed_downloads = []
        orchestrator._generate_retry_report([retryable], [non_retryable])

    def test_generate_retry_report_empty(self, orchestrator):
        """_generate_retry_report should handle empty lists."""
        orchestrator._generate_retry_report([], [])

    def test_enhance_metadata_with_repo_path(self, orchestrator):
        """_enhance_download_results_with_metadata should detect repo paths."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/repo-dls/somefile.dat"
        result.file_type = None
        result.was_skipped = False
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        from fetchtastic.constants import FILE_TYPE_REPOSITORY

        assert result.file_type == FILE_TYPE_REPOSITORY

    def test_enhance_metadata_with_desktop_extension(self, orchestrator):
        """_enhance_download_results_with_metadata should detect desktop extensions."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/app.dmg"
        result.file_type = None
        result.was_skipped = False
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        from fetchtastic.constants import FILE_TYPE_DESKTOP

        assert result.file_type == FILE_TYPE_DESKTOP

    def test_enhance_metadata_with_unknown_path(self, orchestrator):
        """_enhance_download_results_with_metadata should handle unknown paths."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/unknown.dat"
        result.file_type = None
        result.was_skipped = False
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        from fetchtastic.constants import FILE_TYPE_UNKNOWN

        assert result.file_type == FILE_TYPE_UNKNOWN

    def test_is_download_retryable_unknown_error(self, orchestrator):
        """_is_download_retryable should return True for unknown error types."""
        result = Mock(spec=DownloadResult)
        result.error_type = "unknown_error_type"

        assert orchestrator._is_download_retryable(result) is True

    def test_log_download_summary_with_skipped(self, orchestrator):
        """_log_download_summary should log skipped count."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.was_skipped = True
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._log_download_summary(time.time())

    def test_log_download_summary_with_failures(self, orchestrator):
        """_log_download_summary should warn about failures."""
        result = Mock(spec=DownloadResult)
        result.success = False
        orchestrator.download_results = []
        orchestrator.failed_downloads = [result]

        orchestrator._log_download_summary(time.time())

    def test_log_prerelease_summary_missing_entries(self, orchestrator):
        """_log_prerelease_summary should skip when history_entries is missing."""
        orchestrator.firmware_prerelease_summary = {"history_entries": None}

        orchestrator._log_prerelease_summary()

    def test_log_prerelease_summary_invalid_clean_release(self, orchestrator):
        """_log_prerelease_summary should skip when clean_latest_release is not string."""
        orchestrator.firmware_prerelease_summary = {
            "history_entries": [{"id": "1"}],
            "clean_latest_release": 123,
            "expected_version": "1.0.1",
        }

        orchestrator._log_prerelease_summary()

    def test_log_prerelease_summary_invalid_expected_version(self, orchestrator):
        """_log_prerelease_summary should skip when expected_version is not string."""
        orchestrator.firmware_prerelease_summary = {
            "history_entries": [{"id": "1"}],
            "clean_latest_release": "v1.0.0",
            "expected_version": 123,
        }

        orchestrator._log_prerelease_summary()

    def test_log_prerelease_summary_success(self, orchestrator):
        """_log_prerelease_summary should call downloader log method."""
        orchestrator.firmware_prerelease_summary = {
            "history_entries": [{"id": "1"}],
            "clean_latest_release": "v1.0.0",
            "expected_version": "1.0.1",
        }
        orchestrator.firmware_downloader.log_prerelease_summary = Mock()

        orchestrator._log_prerelease_summary()

        orchestrator.firmware_downloader.log_prerelease_summary.assert_called_once()

    def test_log_firmware_history_with_filter_revoked(self, orchestrator):
        """log_firmware_release_history_summary should filter revoked releases."""
        orchestrator.config["FILTER_REVOKED_RELEASES"] = True
        orchestrator.config["KEEP_LAST_BETA"] = False
        orchestrator.firmware_release_history = {"entries": {}}
        orchestrator.firmware_releases = [
            Release(tag_name="v1.0.0", prerelease=False),
            Release(tag_name="v0.9.0", prerelease=False),
        ]
        orchestrator.firmware_downloader.is_release_revoked = Mock(
            side_effect=lambda r: r.tag_name == "v0.9.0"
        )
        manager = Mock()
        manager.get_releases_for_summary.return_value = [
            Release(tag_name="v1.0.0", prerelease=False)
        ]
        orchestrator.firmware_downloader.release_history_manager = manager

        orchestrator.log_firmware_release_history_summary()

        manager.log_release_channel_summary.assert_called_once()

    def test_cleanup_old_versions_with_desktop(self, orchestrator):
        """cleanup_old_versions should clean desktop when enabled."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.config["DESKTOP_VERSIONS_TO_KEEP"] = 2
        orchestrator._cleanup_deleted_prereleases = Mock()

        orchestrator.cleanup_old_versions()

        orchestrator.desktop_downloader.cleanup_old_versions.assert_called_once_with(
            2, cached_releases=orchestrator.desktop_releases
        )

    def test_cleanup_old_versions_error(self, orchestrator):
        """cleanup_old_versions should handle exceptions gracefully."""
        orchestrator.android_downloader.cleanup_old_versions.side_effect = OSError(
            "test error"
        )

        orchestrator.cleanup_old_versions()

    def test_cleanup_deleted_prereleases_no_latest_release(self, orchestrator):
        """_cleanup_deleted_prereleases should exit when no latest release."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value=None
        )

        orchestrator._cleanup_deleted_prereleases()

    def test_cleanup_deleted_prereleases_no_expected_version(self, orchestrator):
        """_cleanup_deleted_prereleases should exit when no expected version."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value=None
        )

        orchestrator._cleanup_deleted_prereleases()

    def test_cleanup_deleted_prereleases_no_deleted_entries(self, orchestrator):
        """_cleanup_deleted_prereleases should exit when no deleted entries."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orchestrator.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "active"}]
        )

        orchestrator._cleanup_deleted_prereleases()

    def test_cleanup_deleted_prereleases_with_deleted(self, tmp_path):
        """_cleanup_deleted_prereleases should remove deleted directories."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        deleted_dir = prerelease_dir / "firmware-1.0.1.abcdef"
        deleted_dir.mkdir()

        orch.firmware_downloader.get_latest_release_tag = Mock(return_value="v1.0.0")
        orch.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orch.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "deleted", "directory": "firmware-1.0.1.abcdef"}]
        )

        orch._cleanup_deleted_prereleases()

        assert not deleted_dir.exists()

    def test_cleanup_deleted_prereleases_unsafe_name(self, tmp_path):
        """_cleanup_deleted_prereleases should skip unsafe directory names."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        unsafe_dir = prerelease_dir / "firmware-1.0.1.abcdef"
        unsafe_dir.mkdir()

        orch.firmware_downloader.get_latest_release_tag = Mock(return_value="v1.0.0")
        orch.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orch.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "deleted", "directory": "../outside"}]
        )

        orch._cleanup_deleted_prereleases()

        assert unsafe_dir.exists()

    def test_cleanup_deleted_prereleases_error(self, orchestrator):
        """_cleanup_deleted_prereleases should handle exceptions gracefully."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            side_effect=OSError("test error")
        )

        orchestrator._cleanup_deleted_prereleases()

    def test_get_latest_versions_with_firmware_prerelease_prefix(self, orchestrator):
        """get_latest_versions should strip firmware- prefix from prerelease."""
        orchestrator.android_releases = []
        orchestrator.desktop_releases = []
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.extract_clean_version = Mock(return_value="1.0.0")
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orchestrator.prerelease_manager.get_latest_active_prerelease_from_history = (
            Mock(return_value=("firmware-1.0.1.abcdef", []))
        )
        orchestrator.android_downloader.get_latest_prerelease_tag = Mock(
            return_value=None
        )
        orchestrator.desktop_downloader.get_latest_prerelease_tag = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["firmware_prerelease"] == "1.0.1.abcdef"

    def test_get_latest_versions_with_firmware_prerelease_no_prefix(self, orchestrator):
        """get_latest_versions should keep prerelease without firmware- prefix."""
        orchestrator.android_releases = []
        orchestrator.desktop_releases = []
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.extract_clean_version = Mock(return_value="1.0.0")
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orchestrator.prerelease_manager.get_latest_active_prerelease_from_history = (
            Mock(return_value=("custom-1.0.1.abcdef", []))
        )
        orchestrator.android_downloader.get_latest_prerelease_tag = Mock(
            return_value=None
        )
        orchestrator.desktop_downloader.get_latest_prerelease_tag = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["firmware_prerelease"] == "custom-1.0.1.abcdef"

    def test_update_version_tracking_error(self, orchestrator):
        """update_version_tracking should handle exceptions gracefully."""
        orchestrator.android_downloader.get_releases = Mock(
            side_effect=OSError("test error")
        )

        orchestrator.update_version_tracking()

    def test_refresh_commit_history_cache_error(self, orchestrator):
        """_refresh_commit_history_cache should handle exceptions gracefully."""
        orchestrator.prerelease_manager.fetch_recent_repo_commits = Mock(
            side_effect=OSError("test error")
        )

        orchestrator._refresh_commit_history_cache()

    def test_manage_prerelease_tracking(self, orchestrator):
        """_manage_prerelease_tracking should call all downloader methods."""
        orchestrator._refresh_commit_history_cache = Mock()

        orchestrator._manage_prerelease_tracking()

        orchestrator.android_downloader.manage_prerelease_tracking_files.assert_called_once()
        orchestrator.firmware_downloader.manage_prerelease_tracking_files.assert_called_once()
        orchestrator.desktop_downloader.manage_prerelease_tracking_files.assert_called_once()

    def test_manage_prerelease_tracking_error(self, orchestrator):
        """_manage_prerelease_tracking should handle exceptions gracefully."""
        orchestrator.android_downloader.manage_prerelease_tracking_files = Mock(
            side_effect=OSError("test error")
        )

        orchestrator._manage_prerelease_tracking()

    def test_run_download_pipeline_resets_stale_wifi_skipped(self, orchestrator):
        """Stale wifi_skipped=True must be cleared at the start of a subsequent run."""
        orchestrator.wifi_skipped = True
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with patch("fetchtastic.download.orchestrator.is_termux", return_value=False):
            orchestrator.run_download_pipeline()

        assert orchestrator.wifi_skipped is False

    def test_run_download_pipeline_wifi_only_not_connected(self, orchestrator):
        """Pipeline should skip when WIFI_ONLY and not connected."""
        orchestrator.config["WIFI_ONLY"] = True
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            result = orchestrator.run_download_pipeline()

        assert result == ([], [])
        orchestrator._process_android_downloads.assert_not_called()

    def test_get_firmware_keep_limit_invalid(self, orchestrator):
        """_get_firmware_keep_limit should handle invalid values."""
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = "invalid"

        result = orchestrator._get_firmware_keep_limit()

        from fetchtastic.constants import DEFAULT_FIRMWARE_VERSIONS_TO_KEEP

        assert result == DEFAULT_FIRMWARE_VERSIONS_TO_KEEP

    # =========================================================================
    # Tests for uncovered desktop-related branches (coverage improvement)
    # =========================================================================

    def test_process_android_downloads_any_downloaded_true(self, orchestrator):
        """Test Android processing when _download_android_release returns True."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = False
        orchestrator.android_downloader.handle_prereleases.return_value = []
        orchestrator._download_android_release = Mock(return_value=True)

        orchestrator._process_android_downloads()

        orchestrator._download_android_release.assert_called_once_with(release)

    def test_process_android_downloads_prerelease_with_download(self, orchestrator):
        """Test Android prerelease handling when asset is downloaded."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"

        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.android_downloader.download_apk.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.download_apk.assert_called_once()
        orchestrator._handle_download_result.assert_called_with(
            mock_result, "android_prerelease"
        )
        orchestrator.android_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.1-beta"
        )

    def test_process_android_downloads_skips_prerelease_not_newer(self, orchestrator):
        """Android prerelease downloads should skip tags rejected by tracking policy."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"
        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_prerelease.return_value = False

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.download_apk.assert_not_called()
        orchestrator.android_downloader.update_prerelease_tracking.assert_not_called()

    def test_process_android_downloads_backfills_tracked_prerelease_when_incomplete(
        self, orchestrator
    ):
        """
        Tracked prerelease should be backfilled when selected assets changed.

        This protects naming transitions (for example legacy -> split APK assets)
        without redownloading older prerelease tags.
        """
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"

        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_prerelease.return_value = False
        orchestrator.android_downloader.get_current_tracked_prerelease_tag.return_value = (
            "v1.0.1-beta"
        )
        orchestrator.android_downloader.is_release_complete.side_effect = lambda rel: (
            not rel.prerelease
        )
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.get_assets.return_value = prerelease.assets

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.android_downloader.download_apk.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.download_apk.assert_called_once_with(
            prerelease, prerelease.assets[0]
        )
        orchestrator.android_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.1-beta"
        )

    def test_process_android_downloads_prerelease_skipped_updates_tracking(
        self, orchestrator
    ):
        """Skipped-but-complete Android prerelease should still update tracking."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"
        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_prerelease.return_value = True
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True
        orchestrator.android_downloader.download_apk.return_value = mock_result

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.1-beta"
        )

    def test_process_android_downloads_skips_older_after_newer_prerelease(
        self, orchestrator
    ):
        """When a newer prerelease is accepted, older prereleases should be skipped."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        newer = Release(
            tag_name="v1.0.2-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        older = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        newer.assets[0].name = "app.apk"
        older.assets[0].name = "app.apk"
        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [newer, older]
        orchestrator.android_downloader.should_download_prerelease.side_effect = [
            True,
            False,
        ]
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.get_assets.side_effect = lambda rel: rel.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.android_downloader.download_apk.return_value = mock_result

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.download_apk.assert_called_once_with(
            newer, newer.assets[0]
        )
        orchestrator.android_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.2-beta"
        )

    def test_process_android_downloads_no_prereleases_log(self, orchestrator):
        """Test Android processing logs 'No pre-release APKs available'."""
        orchestrator.config["SAVE_APKS"] = True
        orchestrator.config["CHECK_APK_PRERELEASES"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])

        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = []

        orchestrator._process_android_downloads()

        # Verify the method runs without errors and logs the message
        orchestrator.android_downloader.handle_prereleases.assert_called_once()

    def test_process_desktop_downloads_downloader_returns_none(self, orchestrator):
        """Test desktop processing when get_releases returns None."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.desktop_downloader.get_releases.return_value = None

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.get_releases.assert_called_once()

    def test_process_desktop_downloads_download_returns_true(self, orchestrator):
        """Test desktop processing when _download_desktop_release returns True."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = False
        orchestrator.desktop_downloader.handle_prereleases.return_value = []
        orchestrator._download_desktop_release = Mock(return_value=True)

        orchestrator._process_desktop_downloads()

        orchestrator._download_desktop_release.assert_called_once_with(release)

    def test_process_desktop_downloads_prerelease_skipped_asset(self, orchestrator):
        """Test desktop prerelease when should_download_asset returns False."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.dmg")]
        )
        prerelease.assets[0].name = "app.dmg"

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.desktop_downloader.should_download_asset.return_value = False
        orchestrator.desktop_downloader.get_assets.return_value = prerelease.assets

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.should_download_asset.assert_called_with(
            "app.dmg"
        )
        orchestrator.desktop_downloader.download_desktop.assert_not_called()

    def test_process_desktop_downloads_prerelease_not_downloaded(self, orchestrator):
        """Skipped-but-complete desktop prerelease should still update tracking."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.dmg")]
        )
        prerelease.assets[0].name = "app.dmg"

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        orchestrator.desktop_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True  # Not actually downloaded
        orchestrator.desktop_downloader.download_desktop.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        orchestrator._process_desktop_downloads()

        orchestrator._handle_download_result.assert_called_with(
            mock_result, "desktop_prerelease"
        )
        orchestrator.desktop_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.1-beta"
        )

    def test_process_desktop_downloads_prerelease_updates_tracking(self, orchestrator):
        """Desktop prerelease downloads should update tracking after successful asset download."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.dmg")]
        )
        prerelease.assets[0].name = "app.dmg"

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.desktop_downloader.should_download_prerelease.return_value = True
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        orchestrator.desktop_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        orchestrator.desktop_downloader.download_desktop.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.update_prerelease_tracking.assert_called_once_with(
            "v1.0.1-beta"
        )

    def test_process_desktop_downloads_skips_prerelease_not_newer(self, orchestrator):
        """Desktop prerelease downloads should skip tags rejected by tracking policy."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.dmg")]
        )
        prerelease.assets[0].name = "app.dmg"

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.desktop_downloader.should_download_prerelease.return_value = False

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.download_desktop.assert_not_called()
        orchestrator.desktop_downloader.update_prerelease_tracking.assert_not_called()

    def test_process_desktop_downloads_no_prereleases_log(self, orchestrator):
        """Test desktop processing logs 'No pre-release Desktop app builds available'."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.config["CHECK_DESKTOP_PRERELEASES"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = []

        orchestrator._process_desktop_downloads()

        orchestrator.desktop_downloader.handle_prereleases.assert_called_once()

    def test_process_desktop_downloads_up_to_date(self, orchestrator):
        """Test desktop processing when all assets are up to date."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])

        orchestrator.desktop_downloader.get_releases.return_value = [release]
        orchestrator.desktop_downloader.update_release_history.return_value = {}
        orchestrator.desktop_downloader.ensure_release_notes.return_value = None
        orchestrator.desktop_downloader.format_release_log_suffix.return_value = ""
        orchestrator.desktop_downloader.is_release_complete.return_value = True
        orchestrator.desktop_downloader.handle_prereleases.return_value = []
        orchestrator._download_desktop_release = Mock(return_value=False)

        orchestrator._process_desktop_downloads()

        # Should log "All Desktop app assets are up to date."
        orchestrator._download_desktop_release.assert_not_called()

    def test_process_firmware_downloads_disabled(self, orchestrator):
        """Test firmware processing when disabled."""
        orchestrator.config["SAVE_FIRMWARE"] = False

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.get_releases.assert_not_called()

    def test_process_firmware_downloads_download_returns_true(self, orchestrator):
        """Test firmware processing when _download_firmware_release returns True."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = False
        orchestrator._download_firmware_release = Mock(return_value=True)
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator._download_firmware_release.assert_called_once_with(mock_release)

    def test_process_firmware_downloads_with_prerelease_success(self, orchestrator):
        """Test firmware processing when prerelease download succeeds."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.version_manager.is_prerelease_version.return_value = False
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        mock_release.prerelease = False
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False
        prerelease_summary = {
            "history_entries": [{"id": "abc"}],
            "clean_latest_release": "v2.0.0",
            "expected_version": "2.0.1",
        }

        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [mock_result],
            [],
            None,
            prerelease_summary,
        )
        orchestrator._handle_download_result = Mock()

        orchestrator._process_firmware_downloads()

        assert orchestrator.firmware_prerelease_summary == prerelease_summary
        orchestrator._handle_download_result.assert_any_call(
            mock_result, "firmware_prerelease_repo"
        )

    def test_process_firmware_downloads_with_prerelease_failure(self, orchestrator):
        """Test firmware processing when prerelease download has failures."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.version_manager.is_prerelease_version.return_value = False
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        mock_release.prerelease = False
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = False

        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [mock_result],
            None,
            None,
        )
        orchestrator._handle_download_result = Mock()

        orchestrator._process_firmware_downloads()

        orchestrator._handle_download_result.assert_called_with(
            mock_result, "firmware_prerelease_repo"
        )

    def test_select_latest_release_by_version_no_parseable(self, orchestrator):
        """Test selecting latest when no releases have parseable versions."""
        orchestrator.version_manager.get_release_tuple.return_value = None

        releases = [
            Release(tag_name="junk1", prerelease=False, assets=[]),
            Release(tag_name="junk2", prerelease=False, assets=[]),
        ]

        selected = orchestrator._select_latest_release_by_version(releases)

        assert selected is not None
        assert selected.tag_name == "junk1"  # First release when none parse

    def test_select_latest_release_by_version_mixed_revoked(self, orchestrator):
        """Test selecting latest with mixed revoked and non-revoked releases."""

        def is_revoked(release):
            return release.tag_name == "v2.0.0"

        orchestrator.firmware_downloader.is_release_revoked.side_effect = is_revoked
        orchestrator.version_manager.get_release_tuple.side_effect = lambda tag: (
            (2, 0, 0) if tag == "v2.0.0" else ((1, 0, 0) if tag == "v1.0.0" else None)
        )

        releases = [
            Release(tag_name="v2.0.0", prerelease=False, assets=[]),  # revoked, higher
            Release(tag_name="v1.0.0", prerelease=False, assets=[]),  # not revoked
        ]

        selected = orchestrator._select_latest_release_by_version(releases)

        assert selected is not None
        assert selected.tag_name == "v1.0.0"  # Should pick non-revoked

    def test_download_firmware_release_manifest_skipped(self, orchestrator):
        """Test firmware manifest handling when result is skipped."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        release.assets = []

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True
        orchestrator.firmware_downloader.download_manifests.return_value = [mock_result]
        orchestrator.firmware_downloader.should_download_release.return_value = False
        orchestrator._handle_download_result = Mock()

        orchestrator._download_firmware_release(release)

        # Verify handle_download_result was called with manifest result
        calls = orchestrator._handle_download_result.call_args_list
        assert any(call[0][0] == mock_result for call in calls)

    def test_download_desktop_release_exception(self, orchestrator):
        """Test desktop release download when exception occurs."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"
        asset = Mock()
        asset.name = "app.dmg"
        orchestrator.desktop_downloader.get_assets.return_value = [asset]
        orchestrator.desktop_downloader.should_download_asset.return_value = True
        orchestrator.desktop_downloader.download_desktop.side_effect = (
            requests.RequestException("network error")
        )

        result = orchestrator._download_desktop_release(release)

        assert result is False

    def test_handle_download_result_with_url_logging(self, orchestrator):
        """Test handling failed download result with URL logging."""
        result = Mock(spec=DownloadResult)
        result.success = False
        result.error_message = "test error"
        result.release_tag = "v1.0.0"
        result.download_url = "https://example.com/file.apk"

        orchestrator._handle_download_result(result, "android")

        assert result in orchestrator.failed_downloads

    def test_retry_failed_downloads_empty_early_exit(self, orchestrator):
        """Test retry exits early when no failed downloads."""
        orchestrator.failed_downloads = []
        orchestrator._retry_single_failure = Mock()

        orchestrator._retry_failed_downloads()

        orchestrator._retry_single_failure.assert_not_called()

    def test_enhance_metadata_sets_retryable_for_failed(self, orchestrator):
        """Test enhancing metadata for failed downloads without retry data."""
        result = Mock(spec=DownloadResult)
        result.success = False
        result.file_path = "/path/to/file.apk"
        result.file_type = None
        result.error_type = "network_error"
        result.retry_count = None
        orchestrator.download_results = []
        orchestrator.failed_downloads = [result]

        orchestrator._enhance_download_results_with_metadata()

        assert result.retry_count == 0
        assert result.is_retryable is True

    def test_log_firmware_history_no_releases_or_history(self, orchestrator):
        """Test log_firmware_release_history_summary exits when no data."""
        orchestrator.firmware_release_history = None
        orchestrator.firmware_releases = None

        # Should not raise and should exit early
        orchestrator.log_firmware_release_history_summary()

    def test_log_firmware_history_with_keep_last_beta(self, orchestrator):
        """Test log_firmware_history with KEEP_LAST_BETA enabled."""
        orchestrator.config["FILTER_REVOKED_RELEASES"] = False
        orchestrator.config["KEEP_LAST_BETA"] = True
        orchestrator.firmware_release_history = {"entries": {}}
        orchestrator.firmware_releases = [
            Release(tag_name="v1.0.0", prerelease=False),
        ]

        manager = Mock()
        manager.expand_keep_limit_to_include_beta.return_value = 2
        manager.get_releases_for_summary.return_value = orchestrator.firmware_releases
        orchestrator.firmware_downloader.release_history_manager = manager

        orchestrator.log_firmware_release_history_summary()

        manager.expand_keep_limit_to_include_beta.assert_called_once()

    def test_cleanup_deleted_prereleases_no_prerelease_dir(self, orchestrator):
        """Test _cleanup_deleted_prereleases when prerelease dir doesn't exist."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orchestrator.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "deleted", "directory": "firmware-1.0.1.abcdef"}]
        )

        orchestrator._cleanup_deleted_prereleases()

        # Should exit early when prerelease_base_dir doesn't exist
        orchestrator.firmware_downloader.get_latest_release_tag.assert_called_once()

    def test_cleanup_deleted_prereleases_no_directory_in_entry(self, orchestrator):
        """Test _cleanup_deleted_prereleases when entry has no directory."""
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orchestrator.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "deleted"}]  # No 'directory' key
        )

        orchestrator._cleanup_deleted_prereleases()

        # Should skip entries without directory key
        orchestrator.prerelease_manager.get_prerelease_commit_history.assert_called_once()

    def test_cleanup_deleted_prereleases_rmtree_fails(self, tmp_path):
        """Test _cleanup_deleted_prereleases when rmtree fails."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)

        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)
        deleted_dir = prerelease_dir / "firmware-1.0.1.abcdef"
        deleted_dir.mkdir()

        orch.firmware_downloader.get_latest_release_tag = Mock(return_value="v1.0.0")
        orch.version_manager.calculate_expected_prerelease_version = Mock(
            return_value="1.0.1"
        )
        orch.prerelease_manager.get_prerelease_commit_history = Mock(
            return_value=[{"status": "deleted", "directory": "firmware-1.0.1.abcdef"}]
        )

        with patch(
            "fetchtastic.download.orchestrator._safe_rmtree", return_value=False
        ):
            orch._cleanup_deleted_prereleases()

        # Directory should still exist since rmtree returned False
        assert deleted_dir.exists()

    def test_get_latest_versions_no_expected_version(self, orchestrator):
        """Test get_latest_versions when expected_version is None."""
        orchestrator.android_releases = []
        orchestrator.desktop_releases = []
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value="v1.0.0"
        )
        orchestrator.version_manager.extract_clean_version = Mock(return_value="1.0.0")
        orchestrator.version_manager.calculate_expected_prerelease_version = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["firmware_prerelease"] is None

    def test_update_version_tracking_no_android_release(self, orchestrator):
        """Test update_version_tracking when no stable Android release found."""
        orchestrator.android_releases = [
            Release(tag_name="v2.7.12-open.1", prerelease=True),
        ]
        orchestrator.firmware_releases = []
        orchestrator.desktop_releases = []
        orchestrator.android_downloader.update_latest_release_tag = Mock()

        orchestrator.update_version_tracking()

        # Should not call update_latest_release_tag for Android since no stable release
        orchestrator.android_downloader.update_latest_release_tag.assert_not_called()

    def test_update_version_tracking_no_desktop_release(self, orchestrator):
        """Test update_version_tracking when no stable Desktop release found."""
        orchestrator.android_releases = []
        orchestrator.firmware_releases = []
        orchestrator.desktop_releases = [
            Release(tag_name="v2.7.12-open.1", prerelease=True),
        ]
        orchestrator.desktop_downloader = Mock()

        orchestrator.update_version_tracking()

        # Should not call update_latest_release_tag for Desktop since no stable release
        orchestrator.desktop_downloader.update_latest_release_tag.assert_not_called()

    def test_run_download_pipeline_non_termux_wifi_only(self, orchestrator):
        """Test pipeline runs normally on non-Termux even with WIFI_ONLY."""
        orchestrator.config["WIFI_ONLY"] = True
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with patch("fetchtastic.download.orchestrator.is_termux", return_value=False):
            orchestrator.run_download_pipeline()

        # Should proceed with downloads since is_termux returns False
        orchestrator._process_android_downloads.assert_called_once()

    def test_process_android_downloads_error_handling(self, orchestrator):
        """Test Android processing error handling."""
        orchestrator.config["SAVE_APKS"] = True
        orchestrator.android_downloader.get_releases.side_effect = (
            requests.RequestException("API error")
        )

        orchestrator._process_android_downloads()

        # Should handle exception gracefully
        orchestrator.android_downloader.get_releases.assert_called_once()

    def test_process_desktop_downloads_error_handling_valueerror(self, orchestrator):
        """Test Desktop processing error handling with ValueError."""
        orchestrator.config["SAVE_DESKTOP_APP"] = True
        orchestrator.desktop_downloader.get_releases.side_effect = ValueError(
            "test error"
        )

        orchestrator._process_desktop_downloads()

        # Should handle exception gracefully
        orchestrator.desktop_downloader.get_releases.assert_called_once()

    def test_enhance_metadata_with_msi_extension(self, orchestrator):
        """Test enhancing metadata detects .msi as desktop file type."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/app.msi"
        result.file_type = None
        result.was_skipped = False
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        from fetchtastic.constants import FILE_TYPE_DESKTOP

        assert result.file_type == FILE_TYPE_DESKTOP

    def test_enhance_metadata_with_deb_extension(self, orchestrator):
        """Test enhancing metadata detects .deb as desktop file type."""
        result = Mock(spec=DownloadResult)
        result.success = True
        result.file_path = "/tmp/app.deb"
        result.file_type = None
        result.was_skipped = False
        orchestrator.download_results = [result]
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        from fetchtastic.constants import FILE_TYPE_DESKTOP

        assert result.file_type == FILE_TYPE_DESKTOP

    def test_process_android_prerelease_asset_downloaded(self, orchestrator):
        """Test Android prerelease when asset is successfully downloaded (lines 728, 731)."""
        orchestrator.config["SAVE_APKS"] = True
        release = Release(tag_name="v1.0.0", prerelease=False, assets=[])
        prerelease = Release(
            tag_name="v1.0.1-beta", prerelease=True, assets=[Mock(name="app.apk")]
        )
        prerelease.assets[0].name = "app.apk"

        orchestrator.android_downloader.get_releases.return_value = [release]
        orchestrator.android_downloader.update_release_history.return_value = {}
        orchestrator.android_downloader.ensure_release_notes.return_value = None
        orchestrator.android_downloader.format_release_log_suffix.return_value = ""
        orchestrator.android_downloader.is_release_complete.return_value = True
        orchestrator.android_downloader.handle_prereleases.return_value = [prerelease]
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator.android_downloader.get_assets.return_value = prerelease.assets
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = False  # Actually downloaded, not skipped
        orchestrator.android_downloader.download_apk.return_value = mock_result
        orchestrator._handle_download_result = Mock()

        orchestrator._process_android_downloads()

        # The key assertion: any_android_downloaded should be True
        orchestrator.android_downloader.download_apk.assert_called_once()

    def test_select_latest_release_all_unparseable_with_revoked(self, orchestrator):
        """Test selecting latest when all versions unparsable but some revoked."""
        orchestrator.version_manager.get_release_tuple.return_value = None
        orchestrator.firmware_downloader.is_release_revoked.return_value = True

        releases = [
            Release(tag_name="unparsable1", prerelease=False, assets=[]),
        ]

        selected = orchestrator._select_latest_release_by_version(releases)

        # Should return first release when none parseable
        assert selected is not None
        assert selected.tag_name == "unparsable1"

    def test_handle_download_result_no_url(self, orchestrator):
        """Test handling failed download result without URL (early exit at 903)."""
        result = Mock(spec=DownloadResult)
        result.success = False
        result.error_message = "test error"
        result.release_tag = "v1.0.0"
        result.download_url = None  # No URL, should not log URL

        orchestrator._handle_download_result(result, "android")

        assert result in orchestrator.failed_downloads

    def test_process_android_downloads_disabled(self, orchestrator):
        """Test Android processing when disabled (line 227-228)."""
        orchestrator.config["SAVE_APKS"] = False

        orchestrator._process_android_downloads()

        orchestrator.android_downloader.migrate_legacy_layout.assert_not_called()
        orchestrator.android_downloader.get_releases.assert_not_called()

    def test_process_firmware_with_keep_last_beta(self, orchestrator):
        """Test firmware processing with KEEP_LAST_BETA enabled (lines 576-580)."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.config["KEEP_LAST_BETA"] = True
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        mock_beta = Mock(spec=Release)
        mock_beta.tag_name = "v2.0.1-beta"

        orchestrator.firmware_downloader.get_releases.return_value = [
            mock_release,
            mock_beta,
        ]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )
        orchestrator.firmware_downloader.release_history_manager.find_most_recent_beta = Mock(
            return_value=mock_beta
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.release_history_manager.find_most_recent_beta.assert_called_once()

    def test_process_firmware_with_filter_revoked(self, orchestrator):
        """Test firmware processing with FILTER_REVOKED_RELEASES (line 549->551)."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.config["FILTER_REVOKED_RELEASES"] = True
        orchestrator.config["KEEP_LAST_BETA"] = False
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"

        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.get_releases.assert_called_once()

    def test_download_firmware_no_latest_release(self, orchestrator):
        """Test firmware download when no latest release (line 609->631)."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"

        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=None)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        # Should skip prerelease firmware download when no latest release
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.assert_not_called()

    def test_process_firmware_prerelease_skipped_no_any_firmware(self, orchestrator):
        """Test firmware prerelease handling when skipped (line 621->623)."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.version_manager.is_prerelease_version.return_value = False
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        mock_release.prerelease = False
        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        mock_result.was_skipped = True  # Skipped, not actually downloaded

        orchestrator.firmware_downloader.get_releases.return_value = [mock_release]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator._select_latest_release_by_version = Mock(return_value=mock_release)
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [mock_result],
            [],
            None,
            None,
        )
        orchestrator._handle_download_result = Mock()

        orchestrator._process_firmware_downloads()

        orchestrator._handle_download_result.assert_called_with(
            mock_result, "firmware_prerelease_repo"
        )

    def test_process_firmware_repo_prerelease_uses_latest_by_version(
        self, orchestrator
    ):
        """Repo prerelease download and cleanup must use the latest release by version, even if hash-suffixed."""
        from fetchtastic.download.version import VersionManager

        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.version_manager = VersionManager()

        hash_latest = Release(tag_name="v2.7.22.96dd647", prerelease=True, assets=[])
        older_stable = Release(tag_name="v2.7.15", prerelease=False, assets=[])

        orchestrator.firmware_downloader.get_releases.return_value = [
            hash_latest,
            older_stable,
        ]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.download_repo_prerelease_firmware.assert_called_once_with(
            "v2.7.22.96dd647", force_refresh=False
        )
        orchestrator.firmware_downloader.cleanup_superseded_prereleases.assert_called_once_with(
            "v2.7.22.96dd647"
        )

    def test_discover_available_versions_populates_lists_when_wifi_skipped(
        self, orchestrator
    ):
        """Discovery should populate available-new lists with versions newer than tracked."""
        orchestrator.config["WIFI_ONLY"] = True
        firmware_releases = [
            Release(tag_name="v2.7.20", prerelease=False, assets=[]),
            Release(tag_name="v2.7.19", prerelease=False, assets=[]),
        ]
        apk_releases = [
            Release(tag_name="v2.7.10", prerelease=False, assets=[]),
            Release(tag_name="v2.7.9", prerelease=False, assets=[]),
        ]
        orchestrator.firmware_downloader.get_releases.return_value = firmware_releases
        orchestrator.android_downloader.get_releases.return_value = apk_releases
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v2.7.19"
        orchestrator.android_downloader.get_latest_release_tag.return_value = "v2.7.9"

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            result = orchestrator.run_download_pipeline()

        assert result == ([], [])
        assert orchestrator.wifi_skipped is True
        assert "v2.7.20" in orchestrator.available_new_firmware_versions
        assert "v2.7.19" not in orchestrator.available_new_firmware_versions
        assert "v2.7.10" in orchestrator.available_new_apk_versions
        assert "v2.7.9" not in orchestrator.available_new_apk_versions

    def test_discover_available_versions_no_side_effects(self, orchestrator):
        """Wi-Fi skip discovery must not call download, cleanup, or tracking-update methods."""
        orchestrator.config["WIFI_ONLY"] = True
        firmware_releases = [
            Release(tag_name="v2.7.20", prerelease=False, assets=[]),
        ]
        apk_releases = [
            Release(tag_name="v2.7.10", prerelease=False, assets=[]),
        ]
        orchestrator.firmware_downloader.get_releases.return_value = firmware_releases
        orchestrator.android_downloader.get_releases.return_value = apk_releases
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v2.7.19"
        orchestrator.android_downloader.get_latest_release_tag.return_value = "v2.7.9"

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            orchestrator.run_download_pipeline()

        orchestrator.firmware_downloader.download_firmware.assert_not_called()
        orchestrator.android_downloader.download_apk.assert_not_called()
        orchestrator.firmware_downloader.update_latest_release_tag.assert_not_called()
        orchestrator.android_downloader.update_latest_release_tag.assert_not_called()
        orchestrator.firmware_downloader.cleanup_old_versions.assert_not_called()
        orchestrator.android_downloader.cleanup_old_versions.assert_not_called()
        assert orchestrator.download_results == []
        assert orchestrator.failed_downloads == []

    def test_discover_available_versions_empty_when_no_tracked_version(
        self, orchestrator
    ):
        """When no tracked version exists, discovery should include all releases in window."""
        orchestrator.config["WIFI_ONLY"] = True
        firmware_releases = [
            Release(tag_name="v2.7.20", prerelease=False, assets=[]),
            Release(tag_name="v2.7.19", prerelease=False, assets=[]),
        ]
        apk_releases = [
            Release(tag_name="v2.7.10", prerelease=False, assets=[]),
            Release(tag_name="v2.7.9", prerelease=False, assets=[]),
        ]
        orchestrator.firmware_downloader.get_releases.return_value = firmware_releases
        orchestrator.android_downloader.get_releases.return_value = apk_releases
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = None
        orchestrator.android_downloader.get_latest_release_tag.return_value = None
        orchestrator.version_manager.compare_versions.side_effect = lambda v1, v2: (
            1 if v1 > v2 else (-1 if v1 < v2 else 0)
        )
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            result = orchestrator.run_download_pipeline()

        assert result == ([], [])
        assert orchestrator.available_new_firmware_versions == [
            "v2.7.20",
            "v2.7.19",
        ]
        assert orchestrator.available_new_apk_versions == ["v2.7.10", "v2.7.9"]

    def test_firmware_skip_discovery_ignores_revoked_releases(self, orchestrator):
        """Skip discovery should exclude revoked firmware releases just like normal processing."""
        orchestrator.config["WIFI_ONLY"] = True
        orchestrator.config["FILTER_REVOKED_RELEASES"] = True
        orchestrator.config["KEEP_LAST_BETA"] = False
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 3

        revoked = Release(tag_name="v2.7.21", prerelease=False, assets=[])
        valid = Release(tag_name="v2.7.20", prerelease=False, assets=[])
        older = Release(tag_name="v2.7.19", prerelease=False, assets=[])

        def _collect_non_revoked(
            *, initial_releases, target_count, current_fetch_limit, **_kw
        ):
            non_revoked = [r for r in initial_releases if r is not revoked]
            return non_revoked, initial_releases, current_fetch_limit

        orchestrator.firmware_downloader.collect_non_revoked_releases = Mock(
            side_effect=_collect_non_revoked
        )
        orchestrator.firmware_downloader.get_releases.return_value = [
            revoked,
            valid,
            older,
        ]
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v2.7.19"
        orchestrator.android_downloader.get_releases.return_value = []

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            orchestrator.run_download_pipeline()

        assert "v2.7.21" not in orchestrator.available_new_firmware_versions
        assert "v2.7.20" in orchestrator.available_new_firmware_versions

    def test_firmware_skip_discovery_fetches_enough_with_prereleases(
        self, orchestrator
    ):
        """Skip discovery should find stable versions even when prereleases fill top slots."""
        orchestrator.config["WIFI_ONLY"] = True
        orchestrator.config["KEEP_LAST_BETA"] = False
        orchestrator.config["FILTER_REVOKED_RELEASES"] = False
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 5

        pre1 = Release(tag_name="v2.7.22-beta.1", prerelease=True, assets=[])
        pre2 = Release(tag_name="v2.7.22-beta.2", prerelease=True, assets=[])
        stable1 = Release(tag_name="v2.7.21", prerelease=False, assets=[])
        stable2 = Release(tag_name="v2.7.20", prerelease=False, assets=[])
        stable3 = Release(tag_name="v2.7.19", prerelease=False, assets=[])

        orchestrator.firmware_downloader.get_releases.return_value = [
            pre1,
            pre2,
            stable1,
            stable2,
            stable3,
        ]
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v2.7.18"
        orchestrator.android_downloader.get_releases.return_value = []

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            orchestrator.run_download_pipeline()

        assert "v2.7.21" in orchestrator.available_new_firmware_versions
        assert "v2.7.20" in orchestrator.available_new_firmware_versions
        assert "v2.7.19" in orchestrator.available_new_firmware_versions
        assert "v2.7.22-beta.1" not in orchestrator.available_new_firmware_versions

    def test_apk_skip_discovery_no_underfetch_with_prereleases(self, orchestrator):
        """APK skip discovery should not under-fetch when prereleases are interleaved."""
        orchestrator.config["WIFI_ONLY"] = True
        orchestrator.config["ANDROID_VERSIONS_TO_KEEP"] = 2

        pre = Release(tag_name="v2.7.12-open.1", prerelease=True, assets=[])
        stable1 = Release(tag_name="v2.7.11", prerelease=False, assets=[])
        stable2 = Release(tag_name="v2.7.10", prerelease=False, assets=[])

        orchestrator.android_downloader.get_releases.return_value = [
            pre,
            stable1,
            stable2,
        ]
        orchestrator.android_downloader.get_latest_release_tag.return_value = "v2.7.9"
        orchestrator.firmware_downloader.get_releases.return_value = []

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            orchestrator.run_download_pipeline()

        assert "v2.7.11" in orchestrator.available_new_apk_versions
        assert "v2.7.10" in orchestrator.available_new_apk_versions
        assert "v2.7.12-open.1" not in orchestrator.available_new_apk_versions

    def test_skip_discovery_isolates_firmware_and_apk_failures(self, orchestrator):
        """Firmware discovery failure must not suppress APK discovery results."""
        orchestrator.config["WIFI_ONLY"] = True

        orchestrator.firmware_downloader.get_releases.side_effect = OSError(
            "firmware fetch failed"
        )

        apk_releases = [
            Release(tag_name="v2.7.10", prerelease=False, assets=[]),
        ]
        orchestrator.android_downloader.get_releases.return_value = apk_releases
        orchestrator.android_downloader.get_latest_release_tag.return_value = "v2.7.9"

        def _cmp_ver(v1, v2):
            t1 = tuple(int(x) for x in v1.lstrip("v").split("."))
            t2 = tuple(int(x) for x in v2.lstrip("v").split("."))
            return 1 if t1 > t2 else (-1 if t1 < t2 else 0)

        orchestrator.version_manager.compare_versions.side_effect = _cmp_ver
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_desktop_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        with (
            patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
            patch(
                "fetchtastic.download.orchestrator.is_connected_to_wifi",
                return_value=False,
            ),
        ):
            result = orchestrator.run_download_pipeline()

        assert result == ([], [])
        assert orchestrator.available_new_firmware_versions == []
        assert "v2.7.10" in orchestrator.available_new_apk_versions

    def test_process_firmware_hash_suffixed_is_valid_baseline_when_latest(
        self, orchestrator
    ):
        """A hash-suffixed tag that is latest by version must be used as baseline."""
        from fetchtastic.download.version import VersionManager

        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.version_manager = VersionManager()

        hash_newer = Release(tag_name="v2.7.22.96dd647", prerelease=True, assets=[])
        stable_older = Release(tag_name="v2.7.20", prerelease=False, assets=[])

        orchestrator.firmware_downloader.get_releases.return_value = [
            hash_newer,
            stable_older,
        ]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.download_repo_prerelease_firmware.assert_called_once_with(
            "v2.7.22.96dd647", force_refresh=False
        )
        orchestrator.firmware_downloader.cleanup_superseded_prereleases.assert_called_once_with(
            "v2.7.22.96dd647"
        )

    def test_hash_suffixed_only_releases_still_provide_baseline(self, orchestrator):
        """When all releases are hash-suffixed, the latest by version is still used as baseline."""
        from fetchtastic.download.version import VersionManager

        orchestrator.config["SAVE_FIRMWARE"] = True
        hash_a = Release(tag_name="v2.7.16.9058cce", prerelease=False, assets=[])
        hash_b = Release(tag_name="v2.7.14.abcdef12", prerelease=False, assets=[])

        real_vm = VersionManager()
        orchestrator.version_manager = real_vm

        orchestrator.firmware_downloader.get_releases.return_value = [hash_a, hash_b]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.download_repo_prerelease_firmware.assert_called_once_with(
            "v2.7.16.9058cce", force_refresh=False
        )
        orchestrator.firmware_downloader.cleanup_superseded_prereleases.assert_called_once_with(
            "v2.7.16.9058cce"
        )

    def test_process_firmware_uses_latest_by_version_with_mixed_releases(
        self, orchestrator
    ):
        """Latest release by version is used as baseline regardless of hash suffix or prerelease flag."""
        from fetchtastic.download.version import VersionManager

        orchestrator.config["SAVE_FIRMWARE"] = True
        official_stable = Release(tag_name="v2.7.22", prerelease=False, assets=[])
        hash_suffixed_stable = Release(
            tag_name="v2.7.21.abcdef12", prerelease=False, assets=[]
        )
        hash_suffixed_pre = Release(
            tag_name="v2.7.20.1234567", prerelease=True, assets=[]
        )

        real_vm = VersionManager()
        orchestrator.version_manager = real_vm

        orchestrator.firmware_downloader.get_releases.return_value = [
            hash_suffixed_pre,
            hash_suffixed_stable,
            official_stable,
        ]
        orchestrator.firmware_downloader.is_release_complete.return_value = True
        orchestrator.firmware_downloader.download_repo_prerelease_firmware.return_value = (
            [],
            [],
            None,
            None,
        )

        orchestrator._process_firmware_downloads()

        orchestrator.firmware_downloader.download_repo_prerelease_firmware.assert_called_once_with(
            "v2.7.22", force_refresh=False
        )
        orchestrator.firmware_downloader.cleanup_superseded_prereleases.assert_called_once_with(
            "v2.7.22"
        )


class TestFirmwarePrereleaseBaselineRegression:
    """Regression tests for the repo-prerelease baseline selection fix.

    Verifies that the orchestrator selects the latest release by version
    (not filtered by the GitHub prerelease flag) as the baseline for
    repo-prerelease download and superseded prerelease cleanup.
    """

    @staticmethod
    def _make_releases():
        return [
            Release(tag_name="v2.7.22.96dd647", prerelease=True, assets=[]),
            Release(tag_name="v2.7.21.1370b23", prerelease=True, assets=[]),
            Release(tag_name="v2.7.20.6658ec2", prerelease=True, assets=[]),
            Release(tag_name="v2.7.15.567b8ea", prerelease=False, assets=[]),
        ]

    def _setup_orchestrator(self, tmp_path):
        from fetchtastic.download.version import VersionManager

        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "SAVE_APKS": False,
            "SAVE_FIRMWARE": True,
            "CHECK_FIRMWARE_PRERELEASES": True,
            "SELECTED_FIRMWARE_ASSETS": [],
            "EXCLUDE_PATTERNS": [],
            "GITHUB_TOKEN": "test_token",
        }
        orch = DownloadOrchestrator(config)
        releases = self._make_releases()

        orch.version_manager = VersionManager()

        orch.firmware_downloader.download_dir = str(tmp_path)
        orch.firmware_downloader.is_release_revoked = Mock(return_value=False)
        orch.firmware_downloader.is_release_complete = Mock(return_value=True)
        orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
            return_value=([], [], None, None)
        )
        orch.firmware_downloader.cleanup_superseded_prereleases = Mock(
            return_value=False
        )
        orch.firmware_downloader.update_release_history = Mock(return_value={})
        orch.firmware_downloader.ensure_release_notes = Mock()
        orch.firmware_downloader.format_release_log_suffix = Mock(return_value="")

        def _collect_non_revoked(*, initial_releases, current_fetch_limit, **_kw):
            return initial_releases, initial_releases, current_fetch_limit

        orch.firmware_downloader.collect_non_revoked_releases = Mock(
            side_effect=_collect_non_revoked
        )

        orch._ensure_firmware_releases = Mock(return_value=releases)

        return orch

    def test_repo_prerelease_uses_latest_by_version_not_latest_non_prerelease(
        self, tmp_path
    ):
        """Test A: download_repo_prerelease_firmware must receive the latest
        version (v2.7.22.96dd647), NOT the latest non-prerelease
        (v2.7.15.567b8ea)."""
        orch = self._setup_orchestrator(tmp_path)

        orch._process_firmware_downloads()

        orch.firmware_downloader.download_repo_prerelease_firmware.assert_called_once_with(
            "v2.7.22.96dd647", force_refresh=False
        )
        orch.firmware_downloader.cleanup_superseded_prereleases.assert_called_once_with(
            "v2.7.22.96dd647"
        )

    def test_repo_prerelease_does_not_use_latest_non_prerelease(self, tmp_path):
        """Test A (negative): baseline must NOT be the latest non-prerelease tag."""
        orch = self._setup_orchestrator(tmp_path)

        orch._process_firmware_downloads()

        download_tag = (
            orch.firmware_downloader.download_repo_prerelease_firmware.call_args[0][0]
        )
        cleanup_tag = orch.firmware_downloader.cleanup_superseded_prereleases.call_args[
            0
        ][0]

        assert download_tag != "v2.7.15.567b8ea"
        assert cleanup_tag != "v2.7.15.567b8ea"

    def test_cross_path_consistency_download_and_cleanup_share_tag(self, tmp_path):
        """Test C: repo-prerelease download and cleanup must agree on the same tag."""
        orch = self._setup_orchestrator(tmp_path)

        orch._process_firmware_downloads()

        download_tag = (
            orch.firmware_downloader.download_repo_prerelease_firmware.call_args[0][0]
        )
        cleanup_tag = orch.firmware_downloader.cleanup_superseded_prereleases.call_args[
            0
        ][0]

        assert download_tag == cleanup_tag == "v2.7.22.96dd647"
