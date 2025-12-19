"""
Comprehensive tests for DownloadOrchestrator functionality.

This module tests the core download orchestration behaviors that were
previously handled by the legacy downloader module, ensuring they work
correctly with the new modular architecture.
"""

import logging
from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def test_config(tmp_path):
    """Test configuration for download orchestrator."""
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
    Create a DownloadOrchestrator configured from the provided test configuration.

    Parameters:
        test_config (dict): Configuration dictionary used to construct the orchestrator. Expected keys include directories, retention counts, include/exclude patterns, GitHub token, and prerelease handling options.

    Returns:
        DownloadOrchestrator: An orchestrator instance initialized with the given configuration.
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
        mock_result = Mock()
        mock_result.success = True
        mock_result.file_path = str(tmp_path / "test" / "file.bin")

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
        mock_failed_result = Mock()
        mock_failed_result.success = False
        mock_failed_result.error_type = "network_error"
        mock_failed_result.is_retryable = True
        mock_failed_result.download_url = "https://example.com/file.apk"
        mock_failed_result.file_path = str(tmp_path / "test.apk")
        mock_failed_result.file_type = "android"
        mock_failed_result.retry_count = 0
        mock_failed_result.retry_timestamp = None
        mock_failed_result.release_tag = "test-tag"
        mock_failed_result.file_size = 1000

        # Patch downloader methods to avoid real I/O
        with (
            patch.object(
                orchestrator.android_downloader, "download", return_value=True
            ) as mock_download,
            patch.object(orchestrator.android_downloader, "verify", return_value=True),
        ):
            result = orchestrator._retry_single_failure(mock_failed_result)
            # Should return a DownloadResult with success=True
            from fetchtastic.download.interfaces import DownloadResult

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
        from fetchtastic.download.interfaces import DownloadResult

        mock_result = DownloadResult(
            success=True,
            file_type="firmware",
            download_url="https://example.com/firmware.bin",
            file_path=str(tmp_path / "firmware.bin"),
            file_size=1000,
            release_tag="v1.0.0",
        )
        orchestrator.download_results = [mock_result]

        # Method should enhance results with metadata
        orchestrator._enhance_download_results_with_metadata()

        # Verify that download_results still contains result and wasn't corrupted
        assert len(orchestrator.download_results) == 1
        assert orchestrator.download_results[0].success is True

    def test_is_download_retryable(self, orchestrator):
        """Test checking if download is retryable."""
        mock_result = Mock()
        mock_result.error_type = "network_error"

        result = orchestrator._is_download_retryable(mock_result)
        assert isinstance(result, bool)

    def test_log_download_summary(self, orchestrator, tmp_path):
        """Test logging download summary."""
        # Add some mock download results to test summary
        from fetchtastic.download.interfaces import DownloadResult

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

    def test_calculate_success_rate(self, orchestrator):
        """Test calculating success rate."""
        # Test with empty results
        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert rate == 0.0  # No downloads should give 0% success rate

        # Add some mock download results
        from fetchtastic.download.interfaces import DownloadResult

        orchestrator.download_results = [
            DownloadResult(success=True, file_type="firmware"),
            DownloadResult(success=True, file_type="android"),
            DownloadResult(success=False, file_type="firmware"),
            DownloadResult(success=True, file_type="firmware"),
        ]

        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 100.0  # Success rate should be percentage
        assert rate == 75.0  # 3 successful out of 4 = 75%

    def test_count_artifact_downloads(self, orchestrator):
        """Test counting artifact downloads."""
        # Add some mock download results to test counting
        from fetchtastic.download.interfaces import DownloadResult

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
        # Store original download results count
        original_count = len(orchestrator.download_results)

        # Method should exist and be callable
        orchestrator.cleanup_old_versions()

        # Verify that method completed without errors and didn't corrupt data
        assert len(orchestrator.download_results) >= original_count

    def test_get_latest_versions(self, orchestrator):
        """Test getting latest versions."""
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
        orchestrator.update_version_tracking()

    def test_manage_prerelease_tracking(self, orchestrator):
        """Test managing prerelease tracking."""
        # Method should exist and be callable
        orchestrator._manage_prerelease_tracking()

    def test_refresh_commit_history_cache(self, orchestrator):
        """Test refreshing commit history cache."""
        # Method should exist and be callable
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

    def test_download_android_release(self, orchestrator):
        """Test downloading Android release."""
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
