# Test Download Orchestrator
#
# Comprehensive unit tests for the DownloadOrchestrator class.

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator


class TestDownloadOrchestrator:
    """Test suite for DownloadOrchestrator."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration dictionary."""
        return {
            "DOWNLOAD_DIR": "/tmp/test",
            "CHECK_APK_PRERELEASES": True,
            "CHECK_FIRMWARE_PRERELEASES": True,
            "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
            "EXCLUDE_PATTERNS": ["*debug*"],
            "GITHUB_TOKEN": "test_token",
        }

    @pytest.fixture
    def orchestrator(self, mock_config):
        """Create a DownloadOrchestrator instance with mocked dependencies."""
        orch = DownloadOrchestrator(mock_config)
        # Mock the dependencies that are set in __init__
        orch.cache_manager = Mock()
        orch.version_manager = Mock()
        orch.prerelease_manager = Mock()
        # Mock the downloaders that are created in __init__
        orch.android_downloader = Mock()
        orch.firmware_downloader = Mock()
        orch.repository_downloader = Mock()
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

    @patch("fetchtastic.download.orchestrator.time.time")
    def test_run_download_pipeline_success(self, mock_time, orchestrator):
        """Test successful download pipeline execution."""
        mock_time.return_value = 1000.0

        # Mock the processing methods
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_repository_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()
        orchestrator._refresh_commit_history_cache = Mock()

        # Mock config checks
        orchestrator.config = {
            "DOWNLOAD_ANDROID": True,
            "DOWNLOAD_FIRMWARE": True,
            "DOWNLOAD_REPOSITORY": True,
        }

        orchestrator.run_download_pipeline()

        orchestrator._refresh_commit_history_cache.assert_called_once()
        orchestrator._process_firmware_downloads.assert_called_once()
        orchestrator._process_android_downloads.assert_called_once()
        orchestrator._process_repository_downloads.assert_called_once()
        orchestrator._enhance_download_results_with_metadata.assert_called_once()
        orchestrator._retry_failed_downloads.assert_called_once()
        orchestrator._log_download_summary.assert_called_once_with(1000.0)

    def test_run_download_pipeline_disabled_components(self, orchestrator):
        """Test pipeline execution skips disabled components."""
        # Mock the processing methods
        orchestrator._process_android_downloads = Mock()
        orchestrator._process_firmware_downloads = Mock()
        orchestrator._process_repository_downloads = Mock()
        orchestrator._retry_failed_downloads = Mock()
        orchestrator._enhance_download_results_with_metadata = Mock()
        orchestrator._log_download_summary = Mock()
        orchestrator._refresh_commit_history_cache = Mock()

        # Disable all components
        orchestrator.config = {
            "DOWNLOAD_ANDROID": False,
            "DOWNLOAD_FIRMWARE": False,
            "DOWNLOAD_REPOSITORY": False,
        }

        orchestrator.run_download_pipeline()

        # Should still call retry and metadata enhancement
        orchestrator._refresh_commit_history_cache.assert_called_once()
        orchestrator._retry_failed_downloads.assert_called_once()
        orchestrator._enhance_download_results_with_metadata.assert_called_once()
        orchestrator._log_download_summary.assert_called_once()

        # Processing methods should still be called (they check internally)
        orchestrator._process_android_downloads.assert_called_once()
        orchestrator._process_firmware_downloads.assert_called_once()
        orchestrator._process_repository_downloads.assert_called_once()

    def test_process_android_downloads(self, orchestrator):
        """Test Android download processing."""
        # Mock releases
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v1.0.0"
        orchestrator.version_manager.get_releases = Mock(return_value=[mock_release])

        # Mock filtering
        orchestrator._filter_releases = Mock(return_value=[mock_release])
        orchestrator._download_android_release = Mock()

        orchestrator._process_android_downloads()

        orchestrator.version_manager.get_releases.assert_called_once()
        orchestrator._filter_releases.assert_called_once_with([mock_release], "android")
        orchestrator._download_android_release.assert_called_once_with(mock_release)

    def test_process_firmware_downloads(self, orchestrator):
        """Test firmware download processing."""
        # Mock releases
        mock_release = Mock(spec=Release)
        mock_release.tag_name = "v2.0.0"
        orchestrator.version_manager.get_releases = Mock(return_value=[mock_release])

        # Mock filtering and downloading
        orchestrator._filter_releases = Mock(return_value=[mock_release])
        orchestrator._download_firmware_release = Mock()

        orchestrator._process_firmware_downloads()

        orchestrator.version_manager.get_releases.assert_called_once()
        orchestrator._filter_releases.assert_called_once_with(
            [mock_release], "firmware"
        )
        orchestrator._download_firmware_release.assert_called_once_with(mock_release)

    def test_process_repository_downloads(self, orchestrator):
        """Test repository download processing."""
        # Mock repository files
        mock_file = {"name": "test.sh", "download_url": "http://example.com/test.sh"}
        orchestrator.version_manager.get_repository_files = Mock(
            return_value=[mock_file]
        )

        # Mock filtering and downloading
        orchestrator._filter_repository_files = Mock(return_value=[mock_file])
        orchestrator._download_repository_file = Mock()

        orchestrator._process_repository_downloads()

        orchestrator.version_manager.get_repository_files.assert_called_once()
        orchestrator._filter_repository_files.assert_called_once_with([mock_file])
        orchestrator._download_repository_file.assert_called_once_with(mock_file)

    def test_filter_releases(self, orchestrator):
        """Test release filtering logic."""
        # Mock releases
        release1 = Mock(spec=Release)
        release1.tag_name = "v1.0.0"
        release1.prerelease = False

        release2 = Mock(spec=Release)
        release2.tag_name = "v2.0.0-beta"
        release2.prerelease = True

        releases = [release1, release2]

        # Mock existing releases and should_download checks
        orchestrator._get_existing_releases = Mock(return_value=["v1.0.0"])
        orchestrator._should_download_release = Mock(side_effect=[False, True])

        filtered = orchestrator._filter_releases(releases, "firmware")

        assert len(filtered) == 1
        assert filtered[0] == release2
        orchestrator._should_download_release.assert_any_call(release1, "firmware")
        orchestrator._should_download_release.assert_any_call(release2, "firmware")

    def test_should_download_release_prerelease_enabled(self, orchestrator):
        """Test should_download_release with prereleases enabled."""
        release = Mock(spec=Release)
        release.prerelease = True

        orchestrator.config = {"CHECK_FIRMWARE_PRERELEASES": True}

        result = orchestrator._should_download_release(release, "firmware")

        assert result is True

    def test_should_download_release_prerelease_disabled(self, orchestrator):
        """Test should_download_release with prereleases disabled."""
        release = Mock(spec=Release)
        release.prerelease = True
        release.tag_name = "v1.0.0-beta"

        orchestrator.config = {"CHECK_FIRMWARE_PRERELEASES": False}

        result = orchestrator._should_download_release(release, "firmware")

        assert result is False

    def test_get_existing_releases(self, orchestrator):
        """Test getting existing releases from filesystem."""
        # Mock downloader methods
        orchestrator.firmware_downloader.get_latest_release_tag.return_value = "v1.0.0"

        with (
            patch("os.path.exists") as mock_exists,
            patch("os.listdir") as mock_listdir,
            patch("os.path.isdir") as mock_isdir,
        ):
            mock_exists.return_value = True
            mock_listdir.return_value = ["v2.0.0", "not_a_version"]
            mock_isdir.return_value = True

            result = orchestrator._get_existing_releases("firmware")

            # Should return latest + directories
            assert "v1.0.0" in result
            assert "v2.0.0" in result

    def test_download_android_release_success(self, orchestrator):
        """Test successful Android release download."""
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        orchestrator.android_downloader.download_release.return_value = mock_result

        orchestrator._download_android_release(release)

        orchestrator.android_downloader.download_release.assert_called_once_with(
            release
        )
        orchestrator._handle_download_result.assert_called_once_with(
            mock_result, "android"
        )

    def test_download_firmware_release_success(self, orchestrator):
        """Test successful firmware release download."""
        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"

        mock_result = Mock(spec=DownloadResult)
        mock_result.success = True
        orchestrator.firmware_downloader.download_release.return_value = mock_result

        orchestrator._download_firmware_release(release)

        orchestrator.firmware_downloader.download_release.assert_called_once_with(
            release
        )
        orchestrator._handle_download_result.assert_called_once_with(
            mock_result, "firmware"
        )

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

        mock_android.cleanup_old_versions.assert_called_once()
        mock_firmware.cleanup_old_versions.assert_called_once()

    def test_get_latest_versions(self, orchestrator):
        """Test getting latest versions from downloaders."""
        # Mock cache_dir to avoid Path issues
        orchestrator.cache_manager.cache_dir = "/tmp/cache"

        versions = orchestrator.get_latest_versions()

        # Should call get_latest_release_tag on downloaders
        orchestrator.android_downloader.get_latest_release_tag.assert_called_once()
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
        # This method adds timing and other metadata to results
        # Test that it runs without error
        orchestrator.successful_downloads = []
        orchestrator.failed_downloads = []

        orchestrator._enhance_download_results_with_metadata()

        # Should not crash
        assert True
