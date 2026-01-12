"""
Comprehensive tests for DownloadOrchestrator functionality.

This module tests the core download orchestration behaviors that were
previously handled by the legacy downloader module, ensuring they work
correctly with the new modular architecture.
"""

from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import RELEASE_SCAN_COUNT
from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def test_config(tmp_path):
    """
    Provide a test configuration dictionary for initializing a DownloadOrchestrator.

    Returns:
        dict: Configuration mapping with keys:
            - "DOWNLOAD_DIR" (str): base directory for downloads.
            - "FIRMWARE_VERSIONS_TO_KEEP" (int): number of firmware versions to retain.
            - "ANDROID_VERSIONS_TO_KEEP" (int): number of Android versions to retain.
            - "REPO_VERSIONS_TO_KEEP" (int): number of repository versions to retain.
            - "SELECTED_PATTERNS" (list[str]): filename patterns to include.
            - "EXCLUDE_PATTERNS" (list[str]): filename patterns to exclude.
            - "GITHUB_TOKEN" (str): token used for authenticated GitHub access.
            - "CHECK_FIRMWARE_PRERELEASES" (bool): whether to consider firmware prereleases.
            - "CHECK_ANDROID_PRERELEASES" (bool): whether to consider Android prereleases.
    """
    return {
        "DOWNLOAD_DIR": str(tmp_path / "test_orchestrator"),
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "REPO_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
        "CHECK_ANDROID_PRERELEASES": True,
    }


@pytest.fixture
def orchestrator(test_config):
    """
    Create a DownloadOrchestrator using the provided test configuration.

    Parameters:
        test_config (dict): Configuration dictionary containing directories, retention counts, include/exclude patterns, GitHub token, and prerelease handling options.

    Returns:
        DownloadOrchestrator: An orchestrator initialized with the provided configuration.
    """
    return DownloadOrchestrator(test_config)


class TestDownloadOrchestrator:
    """Test suite for DownloadOrchestrator functionality."""

    def test_initialization(self, test_config):
        """Test orchestrator initialization."""
        orch = DownloadOrchestrator(test_config)
        assert orch.config == test_config

    def test_run_download_pipeline(self, orchestrator):
        """Test running the complete download pipeline."""
        # Mock the actual download methods to avoid network calls
        with (
            patch.object(
                orchestrator, "_process_android_downloads"
            ) as mock_android_process,
            patch.object(
                orchestrator, "_process_firmware_downloads"
            ) as mock_firmware_process,
            patch.object(orchestrator, "_log_download_summary") as mock_summary,
        ):
            result = orchestrator.run_download_pipeline()
            assert isinstance(result, tuple)
            assert len(result) == 2
            mock_android_process.assert_called_once()
            mock_firmware_process.assert_called_once()
            mock_summary.assert_called_once()

    def test_get_extraction_patterns(self, orchestrator):
        """Test getting extraction patterns."""
        patterns = orchestrator._get_extraction_patterns()
        assert isinstance(patterns, list)

    def test_get_exclude_patterns(self, orchestrator):
        """Test getting exclude patterns."""
        patterns = orchestrator._get_exclude_patterns()
        assert isinstance(patterns, list)

    def test_handle_download_result(self, orchestrator, tmp_path):
        """Test handling download results."""
        # Create a mock download result
        mock_result = DownloadResult(
            success=True,
            file_path=str(tmp_path / "test" / "file.bin"),
            file_type="android",
        )

        orchestrator._handle_download_result(mock_result, "test_operation")
        assert orchestrator.download_results[-1] is mock_result
        assert not orchestrator.failed_downloads

    def test_retry_failed_downloads(self, orchestrator, tmp_path):
        """Test retrying failed downloads."""
        retry_result = DownloadResult(
            success=False,
            error_type="network_error",
            file_type="android",
            download_url="https://example.com/file",
            file_path=str(tmp_path / "test.apk"),
            is_retryable=True,
        )
        orchestrator.failed_downloads = [retry_result]

        with patch.object(
            orchestrator,
            "_retry_single_failure",
            return_value=DownloadResult(
                success=True,
                file_type="android",
                file_path=str(tmp_path / "test.apk"),
                download_url="https://example.com/file",
            ),
        ) as mock_retry_single:
            orchestrator._retry_failed_downloads()
            mock_retry_single.assert_called_once_with(retry_result)
            assert not orchestrator.failed_downloads
            assert any(r.success for r in orchestrator.download_results)

    def test_retry_single_failure(self, orchestrator, tmp_path):
        """Test retrying a single failed download."""
        mock_failed_result = DownloadResult(
            success=False,
            error_type="network_error",
            is_retryable=True,
            download_url="https://example.com/file.apk",
            file_path=str(tmp_path / "test.apk"),
            file_type="android",
            retry_count=0,
            release_tag="test-tag",
            file_size=1000,
        )

        with (
            patch.object(
                orchestrator.android_downloader, "download", return_value=True
            ) as mock_download,
            patch.object(orchestrator.android_downloader, "verify", return_value=True),
        ):
            result = orchestrator._retry_single_failure(mock_failed_result)
            assert isinstance(result, DownloadResult)
            assert result.success is True
            mock_download.assert_called_once()

    def test_generate_retry_report(self, orchestrator):
        """Test generating retry reports."""
        retryable_failures = [
            DownloadResult(
                success=False,
                file_type="firmware",
                error_type="network_error",
                retry_count=1,
                is_retryable=True,
            )
        ]
        non_retryable_failures = [
            DownloadResult(
                success=False,
                file_type="android",
                error_type="validation_error",
                retry_count=0,
                is_retryable=False,
            )
        ]
        orchestrator.failed_downloads = [retryable_failures[0]]
        with patch("fetchtastic.download.orchestrator.logger.info") as mock_info:
            orchestrator._generate_retry_report(
                retryable_failures, non_retryable_failures
            )
        assert any(
            "Retry success rate" in str(call.args[0])
            for call in mock_info.call_args_list
        )
        assert any(
            "Non-Retryable Failures Summary" in str(call.args[0])
            for call in mock_info.call_args_list
        )

    def test_enhance_download_results_with_metadata(self, orchestrator, tmp_path):
        """Test enhancing download results with metadata."""
        # Add some mock download results to test enhancement
        mock_result = DownloadResult(
            success=True,
            file_type="firmware",
            download_url="https://example.com/firmware.bin",
            file_path=str(tmp_path / "firmware.bin"),
            file_size=1000,
            release_tag="v1.0.0",
        )
        original_file_size = mock_result.file_size
        orchestrator.download_results = [mock_result]

        # Method should enhance results with metadata
        orchestrator._enhance_download_results_with_metadata()

        # Verify that download_results still contains result and wasn't corrupted
        assert len(orchestrator.download_results) == 1
        assert orchestrator.download_results[0].success is True
        assert orchestrator.download_results[0].file_size == original_file_size
        assert orchestrator.download_results[0].file_type == "firmware"

    def test_is_download_retryable(self, orchestrator):
        """Test checking if download is retryable."""
        mock_result = Mock()
        mock_result.error_type = "network_error"

        result = orchestrator._is_download_retryable(mock_result)
        assert isinstance(result, bool)
        assert result is True  # network_error should be retryable

        # Test non-retryable error
        mock_result.error_type = "validation_error"
        result = orchestrator._is_download_retryable(mock_result)
        assert isinstance(result, bool)
        assert result is False  # validation_error should not be retryable

    def test_log_download_summary(self, orchestrator, tmp_path):
        """Test logging download summary."""
        # Add some mock download results to test summary
        orchestrator.download_results = [
            DownloadResult(
                success=True,
                file_type="firmware",
                download_url="https://example.com/firmware.bin",
                file_path=str(tmp_path / "firmware.bin"),
                file_size=1000,
                release_tag="v1.0.0",
            ),
            DownloadResult(
                success=False,
                file_type="android",
                error_type="network_error",
                download_url="https://example.com/android.apk",
            ),
        ]

        # Method should log summary without errors
        orchestrator._log_download_summary(100.0)

        # Verify that download_results still contains expected results
        assert len(orchestrator.download_results) == 2
        assert orchestrator.download_results[0].success is True
        assert orchestrator.download_results[1].success is False

    def test_get_download_statistics(self, orchestrator):
        """Test getting download statistics."""
        orchestrator.download_results = [
            DownloadResult(success=True, file_type="firmware", file_size=1000),
            DownloadResult(success=False, file_type="android", file_size=500),
            DownloadResult(success=True, file_type="firmware", file_size=1500),
        ]

        stats = orchestrator.get_download_statistics()
        assert isinstance(stats, dict)
        # Verify expected keys are present
        expected_keys = {
            "total_downloads",
            "successful_downloads",
            "failed_downloads",
            "success_rate",
        }
        assert set(stats.keys()) >= expected_keys
        assert all(isinstance(v, (int, float)) for v in stats.values())

        # Verify specific values with our test data
        # total_downloads sums successful_downloads plus the number of currently tracked failures; since failed_downloads is empty it equals the number of successful entries.
        assert stats["total_downloads"] == 2
        assert stats["successful_downloads"] == 2
        assert (
            stats["failed_downloads"] == 0
        )  # failed_downloads comes from orchestrator.failed_downloads, not download_results
        assert stats["success_rate"] == 100.0  # 2 successful out of 2 attempted = 100%

    def test_calculate_success_rate(self, orchestrator):
        """Test calculating success rate."""
        # Test with empty results
        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert rate == 100.0  # No downloads gives 100% success rate by design

        orchestrator.download_results = [
            DownloadResult(success=True, file_type="firmware"),
            DownloadResult(success=True, file_type="android"),
            DownloadResult(success=False, file_type="firmware"),
            DownloadResult(success=True, file_type="firmware"),
        ]

        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 100.0  # Success rate should be percentage
        # With our test data: 3 successful out of (3 successful + 0 failed) = 100%
        # Note: failed DownloadResult in download_results doesn't count toward success rate calculation
        # Only entries in failed_downloads count as failed
        assert rate == 100.0  # 3 successful out of 3 attempted = 100%

    def test_count_artifact_downloads(self, orchestrator):
        """Test counting artifact downloads."""
        orchestrator.download_results = [
            DownloadResult(
                success=True,
                file_type="firmware",
                download_url="https://example.com/firmware1.bin",
            ),
            DownloadResult(
                success=True,
                file_type="firmware",
                download_url="https://example.com/firmware2.bin",
            ),
            DownloadResult(
                success=True,
                file_type="android",
                download_url="https://example.com/android.apk",
            ),
            DownloadResult(
                success=False,
                file_type="firmware",
                download_url="https://example.com/firmware3.bin",
            ),
        ]

        # Test counting firmware downloads (should count both successful and failed)
        firmware_count = orchestrator._count_artifact_downloads("firmware")
        assert isinstance(firmware_count, int)
        assert firmware_count == 3  # 3 firmware entries total

        # Test counting android downloads
        android_count = orchestrator._count_artifact_downloads("android")
        assert android_count == 1  # 1 android entry

    def test_cleanup_old_versions(self, orchestrator):
        """Test cleanup of old versions."""
        # Method should exist and be callable without raising; exact cleanup depends on filesystem contents
        orchestrator.android_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        orchestrator.firmware_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with (
            patch.object(orchestrator.android_downloader, "cleanup_old_versions"),
            patch.object(orchestrator.firmware_downloader, "cleanup_old_versions"),
            patch.object(orchestrator, "_cleanup_deleted_prereleases"),
        ):
            orchestrator.cleanup_old_versions()

    def test_get_latest_versions(self, orchestrator):
        """Test getting latest versions."""
        orchestrator.android_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with (
            patch.object(
                orchestrator.firmware_downloader,
                "get_latest_release_tag",
                return_value=None,
            ),
        ):
            versions = orchestrator.get_latest_versions()
        assert isinstance(versions, dict)
        # Should contain version information for different components
        assert len(versions) >= 0  # May be empty initially
        for key, value in versions.items():
            assert isinstance(key, str)
            assert isinstance(value, (str, type(None)))

    def test_update_version_tracking(self, orchestrator):
        """Test updating version tracking."""
        # Method should exist and be callable
        with (
            patch.object(
                orchestrator.android_downloader, "get_releases", return_value=[]
            ),
            patch.object(
                orchestrator.firmware_downloader, "get_releases", return_value=[]
            ),
            patch.object(orchestrator, "_manage_prerelease_tracking"),
        ):
            orchestrator.update_version_tracking()

    def test_manage_prerelease_tracking(self, orchestrator):
        """Test managing prerelease tracking."""
        # Method should exist and be callable
        orchestrator.android_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        orchestrator.firmware_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with (
            patch.object(orchestrator, "_refresh_commit_history_cache"),
            patch.object(
                orchestrator.android_downloader, "manage_prerelease_tracking_files"
            ),
            patch.object(
                orchestrator.firmware_downloader, "manage_prerelease_tracking_files"
            ),
        ):
            orchestrator._manage_prerelease_tracking()

    def test_refresh_commit_history_cache(self, orchestrator):
        """Test refreshing commit history cache."""
        # Method should exist and be callable
        with patch.object(orchestrator.prerelease_manager, "fetch_recent_repo_commits"):
            orchestrator._refresh_commit_history_cache()

    def test_process_android_downloads(self, orchestrator):
        """Test processing Android downloads."""
        # Mock network operations
        with patch.object(
            orchestrator.android_downloader, "get_releases", return_value=[]
        ):
            orchestrator._process_android_downloads()

    def test_process_firmware_downloads(self, orchestrator):
        """Test processing firmware downloads."""
        # Mock network operations
        with patch.object(
            orchestrator.firmware_downloader, "get_releases", return_value=[]
        ):
            orchestrator._process_firmware_downloads()

    @pytest.mark.parametrize("filter_revoked", [True, False])
    def test_process_firmware_downloads_uses_beta_fetch_limit(
        self, orchestrator, filter_revoked
    ):
        """
        Verify the firmware release fetch limit is beta-aware: it requests RELEASE_SCAN_COUNT releases by default and requests an additional RELEASE_SCAN_COUNT when FILTER_REVOKED_RELEASES is enabled.
        """
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 1
        orchestrator.config["KEEP_LAST_BETA"] = True
        orchestrator.config["FILTER_REVOKED_RELEASES"] = filter_revoked
        orchestrator.firmware_releases = None

        releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with (
            patch.object(
                orchestrator.firmware_downloader, "get_releases", return_value=releases
            ) as mock_get_releases,
            patch.object(
                orchestrator.firmware_downloader, "update_release_history"
            ) as mock_update_history,
            patch.object(
                orchestrator, "_select_latest_release_by_version", return_value=None
            ),
            patch.object(
                orchestrator.firmware_downloader,
                "format_release_log_suffix",
                return_value="",
            ),
            patch.object(
                orchestrator.firmware_downloader,
                "is_release_complete",
                return_value=True,
            ),
            patch.object(orchestrator.firmware_downloader, "ensure_release_notes"),
        ):
            mock_update_history.return_value = {"entries": {}}
            orchestrator._process_firmware_downloads()

        mock_get_releases.assert_called_once()
        expected_limit = RELEASE_SCAN_COUNT + (
            RELEASE_SCAN_COUNT if filter_revoked else 0
        )
        assert mock_get_releases.call_args.kwargs["limit"] == expected_limit

    def test_process_firmware_downloads_includes_latest_beta(self, orchestrator):
        """Latest beta should be included in the processed release list."""
        orchestrator.config["SAVE_FIRMWARE"] = True
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 1
        orchestrator.config["KEEP_LAST_BETA"] = True
        orchestrator.firmware_releases = None

        stable = Release(tag_name="v1.0.1", prerelease=False)
        beta = Release(tag_name="v1.0.0-beta", prerelease=False)
        releases = [stable, beta]

        orchestrator.firmware_downloader.release_history_manager.get_release_channel = (
            Mock(side_effect=lambda release: "beta" if release == beta else "")
        )

        with (
            patch.object(
                orchestrator.firmware_downloader, "get_releases", return_value=releases
            ),
            patch.object(
                orchestrator.firmware_downloader, "update_release_history"
            ) as mock_update_history,
            patch.object(
                orchestrator, "_select_latest_release_by_version", return_value=None
            ),
            patch.object(
                orchestrator.firmware_downloader,
                "format_release_log_suffix",
                return_value="",
            ) as mock_format_suffix,
            patch.object(
                orchestrator.firmware_downloader,
                "is_release_complete",
                return_value=True,
            ),
            patch.object(orchestrator.firmware_downloader, "ensure_release_notes"),
        ):
            mock_update_history.return_value = {"entries": {}}
            orchestrator._process_firmware_downloads()

        assert mock_format_suffix.call_count == 2

    def test_log_firmware_release_history_summary_with_beta(self, orchestrator):
        """Latest beta outside keep window should expand summary keep limit."""
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = 1
        orchestrator.config["KEEP_LAST_BETA"] = True
        orchestrator.firmware_releases = [
            Release(tag_name="v2.0.0", prerelease=False),
            Release(tag_name="v1.9.0", prerelease=False),
        ]
        orchestrator.firmware_release_history = {"entries": {}}
        manager = orchestrator.firmware_downloader.release_history_manager

        def _fake_channel(release):
            """
            Map a release to its release channel identifier.

            Parameters:
                release: An object with a `tag_name` attribute representing the release tag.

            Returns:
                The channel name `"beta"` if `release.tag_name` equals `"v1.9.0"`, otherwise `"alpha"`.
            """
            return "beta" if release.tag_name == "v1.9.0" else "alpha"

        with (
            patch.object(manager, "get_release_channel", side_effect=_fake_channel),
            patch.object(
                manager, "log_release_channel_summary"
            ) as mock_channel_summary,
            patch.object(manager, "log_release_status_summary"),
            patch.object(manager, "log_duplicate_base_versions"),
        ):
            orchestrator.log_firmware_release_history_summary()

        assert mock_channel_summary.called
        assert mock_channel_summary.call_args.kwargs["keep_limit"] == 2

    def test_get_firmware_keep_limit_invalid_config(self, orchestrator):
        """Invalid keep limit config should fall back to default."""
        orchestrator.config["FIRMWARE_VERSIONS_TO_KEEP"] = "nope"
        keep_limit = orchestrator._get_firmware_keep_limit()
        assert isinstance(keep_limit, int)
        assert keep_limit >= 0

    def test_download_android_release(self, orchestrator):
        """
        Verify that an Android release containing an APK asset causes the orchestrator to attempt a download.

        Creates a Release with one APK Asset, patches the orchestrator's Android downloader to simulate a successful download, calls _download_android_release, and asserts a download was invoked.
        """
        release = Release(tag_name="v2.7.14", prerelease=False)
        # Add an asset to the release
        asset = Asset(
            name="meshtastic.apk", download_url="https://example.com/app.apk", size=1000
        )
        release.assets.append(asset)

        # Mock the actual download
        with patch.object(
            orchestrator.android_downloader, "download_apk"
        ) as mock_download:
            mock_download.return_value = Mock(success=True)
            orchestrator._download_android_release(release)
            mock_download.assert_called()

    def test_download_firmware_release(self, orchestrator):
        """Test downloading firmware release."""
        release = Release(tag_name="v2.7.14", prerelease=False)
        # Add an asset to the release that matches the selection pattern
        asset = Asset(
            name="firmware-rak4631-2.7.14.bin",
            download_url="https://example.com/firmware.bin",
            size=1000,
        )
        release.assets.append(asset)

        # Mock the actual download
        with patch.object(
            orchestrator.firmware_downloader, "download_firmware"
        ) as mock_download:
            mock_download.return_value = Mock(success=True)
            orchestrator._download_firmware_release(release)
            mock_download.assert_called()
