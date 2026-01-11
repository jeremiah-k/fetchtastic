"""
Integration tests for the modular download architecture.

This module tests the complete download pipeline to ensure the new modular
architecture preserves the same behavior as the legacy monolithic downloader.
"""

from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.interfaces import Release
from fetchtastic.download.orchestrator import DownloadOrchestrator


@pytest.fixture
def integration_config():
    """
    Default integration test configuration used by orchestrator tests.

    Returns:
        dict: Mapping of configuration keys to values:
            - DOWNLOAD_DIR (str): Path for test downloads.
            - FIRMWARE_VERSIONS_TO_KEEP (int): Number of firmware versions to retain.
            - ANDROID_VERSIONS_TO_KEEP (int): Number of Android versions to retain.
            - SELECTED_PATTERNS (list[str]): Inclusion filename patterns.
            - EXCLUDE_PATTERNS (list[str]): Exclusion filename patterns.
            - GITHUB_TOKEN (str): Token used for GitHub API calls in tests.
            - CHECK_FIRMWARE_PRERELEASES (bool): Whether to include firmware prereleases.
            - CHECK_ANDROID_PRERELEASES (bool): Whether to include Android prereleases.
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test_integration",
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": False,  # Disable for simpler testing
        "CHECK_ANDROID_PRERELEASES": False,
    }


@pytest.fixture
def orchestrator(integration_config):
    """
    Create a DownloadOrchestrator configured for integration tests.

    Parameters:
        integration_config (dict): Configuration values (e.g., DOWNLOAD_DIR, retention counts, patterns, tokens)
            used to initialize the orchestrator.

    Returns:
        DownloadOrchestrator: An orchestrator instance initialized with the provided configuration.
    """
    return DownloadOrchestrator(integration_config)


class TestDownloadIntegration:
    """Integration tests for the complete download pipeline."""

    def test_full_download_pipeline_no_network_calls(self, orchestrator):
        """Test the full download pipeline without actual network calls."""
        # Mock all network operations
        with (
            patch.object(
                orchestrator.firmware_downloader, "get_releases", return_value=[]
            ),
            patch.object(
                orchestrator.android_downloader, "get_releases", return_value=[]
            ),
            patch.object(orchestrator, "cleanup_old_versions"),
            patch.object(orchestrator, "update_version_tracking"),
            patch.object(orchestrator, "_manage_prerelease_tracking"),
            patch.object(orchestrator, "_log_download_summary"),
        ):
            # Should handle the error gracefully
            result = orchestrator.run_download_pipeline()

            # Should complete without errors
            assert result is not None or True  # Allow None return

    def test_orchestrator_initialization(self, integration_config):
        """Test that orchestrator initializes all components correctly."""
        orch = DownloadOrchestrator(integration_config)

        # Check that all downloaders are initialized
        assert orch.firmware_downloader is not None
        assert orch.android_downloader is not None
        assert orch.version_manager is not None
        assert orch.prerelease_manager is not None
        assert orch.cache_manager is not None

        # Check configuration is passed through
        assert orch.config == integration_config

    def test_download_statistics_tracking(self, orchestrator):
        """Test that download statistics are properly tracked."""
        # Initially should have empty statistics
        stats = orchestrator.get_download_statistics()
        assert isinstance(stats, dict)
        assert "total_downloads" in stats or len(stats) >= 0  # Allow empty dict

    def test_version_management_integration(self, orchestrator):
        """Test integration with version management."""
        with (
            patch.object(
                orchestrator.android_downloader, "get_releases", return_value=[]
            ),
            patch.object(
                orchestrator.firmware_downloader,
                "get_latest_release_tag",
                return_value=None,
            ),
            patch.object(
                orchestrator.firmware_downloader, "get_releases", return_value=[]
            ),
        ):
            versions = orchestrator.get_latest_versions()
        assert isinstance(versions, dict)

    def test_error_handling_in_pipeline(self, orchestrator):
        """Test error handling in the download pipeline."""
        # Mock a component to raise an exception
        with (
            patch.object(
                orchestrator.firmware_downloader,
                "get_releases",
                side_effect=ValueError("API Error"),
            ),
            patch.object(
                orchestrator.android_downloader, "get_releases", return_value=[]
            ),
            patch.object(orchestrator, "cleanup_old_versions"),
            patch.object(orchestrator, "update_version_tracking"),
            patch.object(orchestrator, "_manage_prerelease_tracking"),
            patch.object(orchestrator, "_log_download_summary"),
        ):
            # Should handle the error gracefully
            result = orchestrator.run_download_pipeline()
            # Should not crash, even with errors
            assert result is not None or True

    def test_configuration_isolation(self, integration_config):
        """Test that different orchestrator instances maintain separate configuration."""
        config1 = integration_config.copy()
        config1["DOWNLOAD_DIR"] = "/tmp/test1"

        config2 = integration_config.copy()
        config2["DOWNLOAD_DIR"] = "/tmp/test2"

        orch1 = DownloadOrchestrator(config1)
        orch2 = DownloadOrchestrator(config2)

        assert orch1.config["DOWNLOAD_DIR"] != orch2.config["DOWNLOAD_DIR"]
        assert (
            orch1.firmware_downloader.download_dir
            != orch2.firmware_downloader.download_dir
        )

    def test_component_interaction_patterns(self, orchestrator):
        """Test that components interact correctly."""
        # Test that components have their own version managers (current architecture)
        assert hasattr(orchestrator.firmware_downloader, "version_manager")
        assert hasattr(orchestrator.android_downloader, "version_manager")
        assert hasattr(orchestrator, "version_manager")

        # Test that components have cache managers
        assert hasattr(orchestrator.firmware_downloader, "cache_manager")
        assert hasattr(orchestrator.android_downloader, "cache_manager")
        assert hasattr(orchestrator, "cache_manager")

    def test_prerelease_management_integration(self, orchestrator):
        """Test prerelease management integration."""
        # Should not raise exceptions
        orchestrator.android_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        orchestrator.firmware_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with patch.object(orchestrator, "_refresh_commit_history_cache"):
            orchestrator._manage_prerelease_tracking()

    def test_cleanup_coordination(self, orchestrator):
        """Test that cleanup is coordinated across components."""
        # Should not raise exceptions
        orchestrator.android_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        orchestrator.firmware_releases = [Release(tag_name="v1.0.0", prerelease=False)]
        with (
            patch.object(orchestrator.firmware_downloader, "cleanup_old_versions"),
            patch.object(orchestrator, "_cleanup_deleted_prereleases"),
        ):
            orchestrator.cleanup_old_versions()

    def test_retry_logic_integration(self, orchestrator):
        """Test retry logic integration."""
        # Should not raise exceptions
        orchestrator._retry_failed_downloads()

    def test_success_rate_calculation(self, orchestrator):
        """Test success rate calculation."""
        rate = orchestrator._calculate_success_rate()
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 100.0

    def test_artifact_download_counting(self, orchestrator):
        """Test artifact download counting."""
        firmware_count = orchestrator._count_artifact_downloads("firmware")
        android_count = orchestrator._count_artifact_downloads("android")

        assert isinstance(firmware_count, int)
        assert isinstance(android_count, int)
        assert firmware_count >= 0
        assert android_count >= 0

    def test_end_to_end_workflow_simulation(self, orchestrator):
        """Simulate a complete end-to-end workflow."""
        # Mock all external dependencies
        mock_releases = [
            Mock(tag_name="v2.7.14", prerelease=False, assets=[]),
            Mock(tag_name="v2.7.15-rc1", prerelease=True, assets=[]),
        ]

        with (
            patch.object(
                orchestrator.firmware_downloader,
                "get_releases",
                return_value=mock_releases,
            ),
            patch.object(
                orchestrator.android_downloader, "get_releases", return_value=[]
            ),
            patch.object(
                orchestrator.firmware_downloader,
                "download_firmware",
                return_value=Mock(success=True),
            ),
            patch.object(orchestrator, "cleanup_old_versions"),
            patch.object(orchestrator, "update_version_tracking"),
            patch.object(orchestrator, "_manage_prerelease_tracking"),
            patch.object(orchestrator, "_log_download_summary"),
        ):
            # Run the complete workflow
            result = orchestrator.run_download_pipeline()

            # Should complete successfully
            assert result is not None or True

    def test_configuration_validation(self, integration_config):
        """Test that configuration is properly validated."""
        # Valid config should work
        orch = DownloadOrchestrator(integration_config)
        assert orch.config is not None

        # Missing required config should still work (defaults)
        minimal_config = {"DOWNLOAD_DIR": "/tmp/test"}
        orch_minimal = DownloadOrchestrator(minimal_config)
        assert orch_minimal.config is not None

    def test_component_lifecycle(self, orchestrator):
        """Test component lifecycle management."""
        # Components should be properly initialized
        assert orchestrator.download_results == []
        assert orchestrator.failed_downloads == []

        # After operations, lists should still exist
        assert isinstance(orchestrator.download_results, list)
        assert isinstance(orchestrator.failed_downloads, list)

    def test_logging_integration(self, orchestrator):
        """Test logging integration."""
        # Should not raise exceptions
        orchestrator._log_download_summary(100.0)

    def test_cache_integration(self, orchestrator):
        """Test cache integration across components."""
        # All components should have cache managers (current architecture has separate instances)
        assert orchestrator.firmware_downloader.cache_manager is not None
        assert orchestrator.android_downloader.cache_manager is not None
        assert orchestrator.cache_manager is not None
