# Test Download Orchestrator
#
# Comprehensive unit tests for the DownloadOrchestrator class.

from unittest.mock import Mock, patch

import pytest

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
        orch.firmware_downloader = Mock()
        orch.firmware_downloader.download_dir = "/tmp/test"
        orch.firmware_downloader.is_release_revoked = Mock(return_value=False)

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

        orchestrator.android_downloader.get_releases.assert_called_once()

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
        orchestrator.android_downloader.get_latest_prerelease_tag = Mock(
            return_value="v2.7.10-open.1"
        )
        orchestrator.firmware_downloader.get_latest_release_tag = Mock(
            return_value=None
        )

        versions = orchestrator.get_latest_versions()

        assert versions["android"] == "v2.7.9"
        assert versions["android_prerelease"] == "v2.7.10-open.1"

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
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()

        orchestrator.run_download_pipeline()

        orchestrator._process_firmware_downloads.assert_called_once()
        orchestrator._process_android_downloads.assert_called_once()
        orchestrator._enhance_download_results_with_metadata.assert_called_once()
        orchestrator._retry_failed_downloads.assert_called_once()
        orchestrator._log_download_summary.assert_called_once_with(1000.0)

    def test_run_download_pipeline_disabled_components(self, orchestrator):
        """Test pipeline execution skips disabled components."""
        # Mock the processing methods
        orchestrator._process_android_downloads = Mock()
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
        orchestrator.android_downloader.should_download_asset.return_value = True
        orchestrator._handle_download_result = Mock()

        orchestrator.config["FILTER_REVOKED_RELEASES"] = False

        orchestrator._download_android_release(release)

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
