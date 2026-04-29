"""
Comprehensive tests for DownloadOrchestrator functionality.

This module tests the core download orchestration behaviors that were
previously handled by the legacy downloader module, ensuring they work
correctly with the new modular architecture.
"""

from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import (
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_DESKTOP,
    FILE_TYPE_DESKTOP_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_MANIFEST,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
    RELEASE_SCAN_COUNT,
)
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
        with (
            patch.object(
                orchestrator, "_process_client_app_downloads"
            ) as mock_client_app_process,
            patch.object(
                orchestrator, "_process_firmware_downloads"
            ) as mock_firmware_process,
            patch.object(orchestrator, "_log_download_summary") as mock_summary,
        ):
            result = orchestrator.run_download_pipeline()
            assert isinstance(result, tuple)
            assert len(result) == 2
            mock_client_app_process.assert_called_once()
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
            file_type=FILE_TYPE_ANDROID,
        )

        orchestrator._handle_download_result(mock_result, "test_operation")
        assert orchestrator.download_results[-1] is mock_result
        assert not orchestrator.failed_downloads

    def test_retry_failed_downloads(self, orchestrator, tmp_path):
        """Test retrying failed downloads."""
        retry_result = DownloadResult(
            success=False,
            error_type="network_error",
            file_type=FILE_TYPE_ANDROID,
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
                file_type=FILE_TYPE_ANDROID,
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
            file_type=FILE_TYPE_ANDROID,
            retry_count=0,
            release_tag="test-tag",
            file_size=1000,
        )
        (tmp_path / "test.apk").write_bytes(b"x" * 1000)

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

    def test_retry_single_failure_desktop_zip_validation_failure(
        self, orchestrator, tmp_path
    ):
        """Desktop retry should fail when post-download zip validation fails."""
        target_path = tmp_path / "Meshtastic.zip"
        target_path.write_bytes(b"data")

        failed_result = DownloadResult(
            success=False,
            error_type="network_error",
            is_retryable=True,
            download_url="https://example.com/Meshtastic.zip",
            file_path=str(target_path),
            file_type=FILE_TYPE_DESKTOP,
            retry_count=0,
            release_tag="v2.7.20",
            file_size=4,
        )

        with (
            patch.object(
                orchestrator.desktop_downloader, "download", return_value=True
            ),
            patch.object(orchestrator.desktop_downloader, "verify", return_value=True),
            patch.object(
                orchestrator.desktop_downloader, "_is_zip_intact", return_value=False
            ) as mock_zip_intact,
            patch.object(
                orchestrator.desktop_downloader, "cleanup_file"
            ) as mock_cleanup,
        ):
            result = orchestrator._retry_single_failure(failed_result)

        assert result.success is False
        assert result.error_type == "retry_failure"
        assert (
            result.error_message
            == "Downloaded client app ZIP failed post-download validation"
        )
        mock_zip_intact.assert_called_once_with(str(target_path))
        mock_cleanup.assert_called_once_with(str(target_path))

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
                file_type=FILE_TYPE_ANDROID,
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
                file_type=FILE_TYPE_ANDROID,
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
            DownloadResult(success=False, file_type=FILE_TYPE_ANDROID, file_size=500),
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

        assert stats["total_downloads"] == 3
        assert stats["successful_downloads"] == 2
        assert stats["failed_downloads"] == 1
        assert stats["success_rate"] == pytest.approx(66.6667)

    def test_calculate_success_rate(self, orchestrator):
        """Test calculating success rate."""
        # Test with empty results
        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert rate == 100.0  # No downloads gives 100% success rate by design

        orchestrator.download_results = [
            DownloadResult(success=True, file_type="firmware"),
            DownloadResult(success=True, file_type=FILE_TYPE_ANDROID),
            DownloadResult(success=False, file_type="firmware"),
            DownloadResult(success=True, file_type="firmware"),
        ]

        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 100.0  # Success rate should be percentage
        assert rate == 75.0  # 3 successful out of 4 attempted

    def test_count_artifact_downloads(self, orchestrator, tmp_path):
        """Test counting artifact downloads."""
        orchestrator.download_results = [
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_FIRMWARE,
                download_url="https://example.com/firmware1.bin",
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_FIRMWARE_PRERELEASE,
                file_path=str(
                    tmp_path
                    / "firmware"
                    / "prerelease"
                    / "v2.0.0-open.1"
                    / "firmware.bin"
                ),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                file_path=str(
                    tmp_path
                    / "firmware"
                    / "prerelease"
                    / "firmware-2.0.1.a1b2c3d"
                    / "repo.bin"
                ),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                file_path=str(tmp_path / "firmware" / "v2.0.0" / "device.mt.json"),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_ANDROID_PRERELEASE,
                download_url="https://example.com/android.apk",
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_DESKTOP,
                file_path=str(
                    tmp_path / "app" / "desktop" / "v2.0.0" / "Meshtastic.dmg"
                ),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_DESKTOP_PRERELEASE,
                file_path=str(
                    tmp_path
                    / "app"
                    / "desktop"
                    / "prerelease"
                    / "v2.0.1-open.1"
                    / "Meshtastic.dmg"
                ),
            ),
            DownloadResult(
                success=False,
                file_type=FILE_TYPE_FIRMWARE,
                download_url="https://example.com/firmware3.bin",
            ),
        ]

        # Firmware count should include stable/prerelease types and manifests.
        firmware_count = orchestrator._count_artifact_downloads(FILE_TYPE_FIRMWARE)
        assert isinstance(firmware_count, int)
        assert firmware_count == 4

        # Android count should include Android prerelease results.
        android_count = orchestrator._count_artifact_downloads(FILE_TYPE_ANDROID)
        assert android_count == 1

        desktop_count = orchestrator._count_artifact_downloads(FILE_TYPE_DESKTOP)
        desktop_prerelease_count = orchestrator._count_artifact_downloads(
            FILE_TYPE_DESKTOP_PRERELEASE
        )
        assert desktop_count == 2
        assert desktop_prerelease_count == 1

    def test_count_artifact_downloads_client_app_classification(
        self, orchestrator, tmp_path
    ):
        """Client-app assets are classified correctly for legacy stats."""
        orchestrator.download_results = [
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_CLIENT_APP,
                file_path=str(tmp_path / "app" / "v2.0.0" / "meshtastic.apk"),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_CLIENT_APP,
                file_path=str(tmp_path / "app" / "v2.0.0" / "Meshtastic.dmg"),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_CLIENT_APP,
                file_path=str(tmp_path / "app" / "v2.0.0" / "Meshtastic.msi"),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_CLIENT_APP,
                file_path=str(tmp_path / "app" / "v2.0.0" / "Meshtastic.exe"),
            ),
            DownloadResult(
                success=True,
                file_type=FILE_TYPE_CLIENT_APP,
                download_url="https://example.com/Meshtastic-unknown.bin",
            ),
        ]

        android_count = orchestrator._count_artifact_downloads(
            FILE_TYPE_CLIENT_APP, artifact_type=FILE_TYPE_ANDROID
        )
        assert android_count == 2

        desktop_count = orchestrator._count_artifact_downloads(
            FILE_TYPE_CLIENT_APP, artifact_type=FILE_TYPE_DESKTOP
        )
        assert desktop_count == 3

        client_app_count = orchestrator._count_artifact_downloads(FILE_TYPE_CLIENT_APP)
        assert client_app_count == 5

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
        orchestrator.desktop_releases = []
        with (
            patch.object(
                orchestrator.firmware_downloader,
                "get_latest_release_tag",
                return_value=None,
            ),
        ):
            versions = orchestrator.get_latest_versions()
        assert isinstance(versions, dict)
        assert "android" in versions
        assert "firmware" in versions
        assert "firmware_prerelease" in versions
        assert "android_prerelease" in versions
        assert "desktop" in versions
        assert "desktop_prerelease" in versions
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
            patch.object(
                orchestrator.desktop_downloader, "get_releases", return_value=[]
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
        assert keep_limit == int(DEFAULT_FIRMWARE_VERSIONS_TO_KEEP)

    def test_download_client_app_release(self, orchestrator):
        """
        Verify that a client app release containing an APK asset causes the orchestrator to attempt a download.
        """
        orchestrator.config["SELECTED_APP_ASSETS"] = ["*"]
        release = Release(tag_name="v2.7.14", prerelease=False)
        asset = Asset(
            name="meshtastic.apk", download_url="https://example.com/app.apk", size=1000
        )
        release.assets.append(asset)

        with patch.object(
            orchestrator.client_app_downloader, "download_app"
        ) as mock_download:
            mock_download.return_value = Mock(success=True)
            orchestrator._download_client_app_release(release)
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
