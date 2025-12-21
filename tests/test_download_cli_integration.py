import os
import tempfile
from unittest.mock import MagicMock

import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration
from fetchtastic.download.files import _get_existing_prerelease_dirs

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads, pytest.mark.user_interface]


def test_cli_integration_main_loads_config_and_runs(mocker):
    """main should use provided config and delegate to run_download."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}
    mocker.patch(
        "fetchtastic.download.cli_integration.get_effective_github_token",
        return_value=None,
    )
    run_download = mocker.patch.object(
        integration,
        "run_download",
        return_value=(
            ["fw"],
            ["new_fw"],
            ["apk"],
            ["new_apk"],
            [],
            "fw_latest",
            "apk_latest",
        ),
    )

    result = integration.main(config=config)

    run_download.assert_called_once_with({"DOWNLOAD_DIR": "/tmp"}, False)
    assert result[0] == ["fw"]
    assert result[5] == "fw_latest"


def test_cli_integration_main_requires_config_argument():
    """main should require a config argument."""
    integration = DownloadCLIIntegration()

    with pytest.raises(TypeError):
        integration.main()


def test_cli_integration_main_with_config_parameter(mocker):
    """main should use provided config parameter instead of loading."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/custom"}
    run_download = mocker.patch.object(
        integration,
        "run_download",
        return_value=(
            ["fw"],
            ["new_fw"],
            ["apk"],
            ["new_apk"],
            [],
            "fw_latest",
            "apk_latest",
        ),
    )

    result = integration.main(config=config)

    run_download.assert_called_once_with(config, False)
    assert result[0] == ["fw"]


def test_cli_integration_main_with_force_refresh(mocker):
    """main should pass force_refresh parameter to run_download."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}
    mocker.patch(
        "fetchtastic.download.cli_integration.get_effective_github_token",
        return_value=None,
    )
    run_download = mocker.patch.object(
        integration, "run_download", return_value=([], [], [], [], [], "", "")
    )

    integration.main(config=config, force_refresh=True)

    run_download.assert_called_once_with({"DOWNLOAD_DIR": "/tmp"}, True)


def test_cli_integration_main_rejects_none_config(mocker):
    """main should reject a None config."""
    integration = DownloadCLIIntegration()
    run_download = mocker.patch.object(integration, "run_download")

    with pytest.raises(TypeError):
        integration.main(config=None)

    run_download.assert_not_called()


def test_cli_integration_update_cache_loads_config(mocker):
    """update_cache should use provided config and clear caches."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}
    mock_orchestrator = mocker.MagicMock(
        android_downloader=mocker.MagicMock(),
        firmware_downloader=mocker.MagicMock(),
    )
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )
    mock_clear = mocker.patch.object(integration, "_clear_caches")

    result = integration.update_cache(config=config)

    mock_clear.assert_called_once()
    assert result is True


def test_cli_integration_update_cache_requires_config():
    """update_cache should require config parameter."""
    integration = DownloadCLIIntegration()

    # Test that calling without config parameter raises TypeError
    with pytest.raises(TypeError):
        integration.update_cache()


def test_run_download_successful(mocker):
    """run_download should execute pipeline successfully and return expected results."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    # Mock orchestrator and its methods with realistic download results
    mock_orchestrator = MagicMock()
    mock_results = [
        MagicMock(
            release_tag="v1.0.0",
            file_path="/tmp/firmware.zip",
            was_skipped=False,
            file_type="firmware",
        ),
        MagicMock(
            release_tag="v2.0.0",
            file_path="/tmp/android.apk",
            was_skipped=False,
            file_type="android",
        ),
        MagicMock(
            release_tag="v0.9.0",
            file_path="/tmp/old_firmware.zip",
            was_skipped=True,  # Should be ignored
            file_type="firmware",
        ),
    ]
    mock_orchestrator.run_download_pipeline.return_value = (mock_results, [])
    mock_orchestrator.cleanup_old_versions.return_value = None
    mock_orchestrator.update_version_tracking.return_value = None
    mock_orchestrator.get_latest_versions.return_value = {
        "firmware": "v0.9.0",
        "android": "v1.9.0",
    }

    # Mock version manager for comparisons
    mock_version_manager = MagicMock()

    # Configure side_effect to return specific comparison results
    def compare_side_effect(version1, version2):
        # v1.0.0 > v0.9.0 (firmware comparison)
        if version1 == "v1.0.0" and version2 == "v0.9.0":
            return 1
        # v2.0.0 > v1.9.0 (android comparison)
        elif version1 == "v2.0.0" and version2 == "v1.9.0":
            return 1
        # v0.9.0 is skipped, shouldn't be compared
        else:
            return 0

    mock_version_manager.compare_versions.side_effect = compare_side_effect
    mock_android_downloader = MagicMock()
    mock_android_downloader.get_version_manager.return_value = mock_version_manager
    mock_orchestrator.android_downloader = mock_android_downloader

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )

    # Test successful run
    result = integration.run_download(config=config, force_refresh=False)

    # Verify orchestrator was called correctly
    mock_orchestrator.run_download_pipeline.assert_called_once()
    mock_orchestrator.cleanup_old_versions.assert_called_once()
    mock_orchestrator.update_version_tracking.assert_called_once()
    mock_orchestrator.get_latest_versions.assert_called()

    # Verify conversion logic was exercised and result format
    assert len(result) == 7
    assert result[0] == ["v1.0.0"]  # downloaded_firmwares (skipped one excluded)
    assert result[1] == ["v1.0.0"]  # new_firmware_versions (newer than v0.9.0)
    assert result[2] == ["v2.0.0"]  # downloaded_apks
    assert result[3] == ["v2.0.0"]  # new_apk_versions (newer than v1.9.0)
    assert result[4] == []  # failed_downloads
    assert result[5] == "v0.9.0"  # latest_firmware_version (from orchestrator)
    assert result[6] == "v1.9.0"  # latest_apk_version (from orchestrator)

    # Verify version comparison was called for new version detection
    # Should be called for each item in results, including skipped ones (3 times in this test)
    assert mock_version_manager.compare_versions.call_count == 3
    # Verify it was called with the correct arguments
    calls = mock_version_manager.compare_versions.call_args_list
    assert any(call[0] == ("v1.0.0", "v0.9.0") for call in calls)
    assert any(call[0] == ("v2.0.0", "v1.9.0") for call in calls)


def test_run_download_with_force_refresh(mocker):
    """run_download should clear caches when force_refresh=True."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    # Mock orchestrator
    mock_orchestrator = MagicMock()
    mock_orchestrator.run_download_pipeline.return_value = ([], [])
    mock_orchestrator.cleanup_old_versions.return_value = None
    mock_orchestrator.update_version_tracking.return_value = None
    mock_orchestrator.get_latest_versions.return_value = {}

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )

    # Mock _clear_caches to verify it's called
    mock_clear = mocker.patch.object(integration, "_clear_caches")

    # Test with force refresh
    integration.run_download(config=config, force_refresh=True)

    # Verify caches were cleared
    mock_clear.assert_called_once()


def test_run_download_handles_exception(mocker):
    """run_download should handle exceptions and return empty results."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    # Mock orchestrator to raise exception
    mock_orchestrator = MagicMock()
    mock_orchestrator.run_download_pipeline.side_effect = ValueError("Test error")

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )

    # Test exception handling
    result = integration.run_download(config=config, force_refresh=False)

    # Verify empty results are returned on error
    assert result == ([], [], [], [], [], "", "")


def test_is_newer_version_equal():
    """_is_newer_version should return False for equal versions."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = MagicMock()
    mock_version_manager = MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    mock_version_manager.compare_versions.return_value = 0  # version1 == version2

    result = integration._is_newer_version("v1.0", "v1.0")

    assert result is False
    mock_version_manager.compare_versions.assert_called_once_with("v1.0", "v1.0")


def test_get_failed_downloads():
    """get_failed_downloads should format failed downloads correctly."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()

    mock_failure = MagicMock()
    mock_failure.file_type = "firmware"
    mock_failure.file_path = "/tmp/firmware.zip"
    mock_failure.release_tag = "v1.0"
    mock_failure.download_url = "https://example.com/firmware.zip"
    mock_failure.error_message = "Download failed"
    mock_failure.is_retryable = True
    mock_failure.http_status_code = 404

    integration.orchestrator.failed_downloads = [mock_failure]

    result = integration.get_failed_downloads()

    assert len(result) == 1
    failure = result[0]
    assert failure["file_name"] == "firmware.zip"
    assert failure["release_tag"] == "v1.0"
    assert failure["url"] == "https://example.com/firmware.zip"
    assert failure["type"] == "Firmware"
    assert failure["error"] == "Download failed"
    assert failure["retryable"] is True
    assert failure["http_status"] == 404


def test_get_failed_downloads_no_orchestrator():
    """get_failed_downloads should return empty list when no orchestrator."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None

    result = integration.get_failed_downloads()

    assert result == []


def test_get_download_statistics():
    """get_download_statistics should delegate to orchestrator."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.orchestrator.get_download_statistics.return_value = {
        "total_downloads": 10,
        "success_rate": 0.8,
    }

    result = integration.get_download_statistics()

    assert result["total_downloads"] == 10
    assert result["success_rate"] == 0.8


def test_get_download_statistics_no_orchestrator():
    """get_download_statistics should return defaults when no orchestrator."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None

    result = integration.get_download_statistics()

    assert result["total_downloads"] == 0
    assert result["failed_downloads"] == 0
    assert result["success_rate"] == 0.0


def test_get_latest_versions():
    """get_latest_versions should delegate to orchestrator and convert None to empty string."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": "v1.0",
        "android": "v2.0",
        "firmware_prerelease": None,
        "android_prerelease": None,
    }

    result = integration.get_latest_versions()

    assert result["firmware"] == "v1.0"
    assert result["android"] == "v2.0"
    assert result["firmware_prerelease"] == ""
    assert result["android_prerelease"] == ""


def test_get_latest_versions_no_orchestrator():
    """get_latest_versions should return empty strings when no orchestrator."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None

    result = integration.get_latest_versions()

    assert result["android"] == ""
    assert result["firmware"] == ""
    assert result["firmware_prerelease"] == ""


def test_validate_integration_success(mocker):
    """validate_integration should return True when all components work."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.android_downloader.get_releases.return_value = [MagicMock()]
    integration.firmware_downloader.get_releases.return_value = [MagicMock()]
    integration.android_downloader.get_download_dir.return_value = "/tmp/android"

    mocker.patch("os.path.exists", return_value=True)
    result = integration.validate_integration()

    assert result is True


def test_validate_integration_missing_components():
    """validate_integration should return False when components are missing."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None

    result = integration.validate_integration()

    assert result is False


def test_validate_integration_fetch_failure():
    """validate_integration should return False when releases cannot be fetched."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.android_downloader.get_releases.return_value = []
    integration.firmware_downloader.get_releases.return_value = [MagicMock()]

    result = integration.validate_integration()

    assert result is False


def test_get_migration_report_initialized(mocker):
    """get_migration_report should return status when components are initialized."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()
    integration.config = {"DOWNLOAD_DIR": "/tmp"}

    mocker.patch.object(integration, "_validate_configuration", return_value=True)
    mocker.patch.object(integration, "_check_download_directory", return_value=True)
    mocker.patch.object(
        integration, "get_download_statistics", return_value={"total": 5}
    )
    result = integration.get_migration_report()

    assert result["status"] == "completed"
    assert result["android_downloader_initialized"] is True
    assert result["firmware_downloader_initialized"] is True
    assert result["orchestrator_initialized"] is True


def test_get_migration_report_not_initialized():
    """get_migration_report should return not_initialized when components missing."""
    integration = DownloadCLIIntegration()

    result = integration.get_migration_report()

    assert result["status"] == "not_initialized"
    assert result["android_downloader_initialized"] is False


def test_fallback_to_legacy():
    """fallback_to_legacy should return False (no longer needed)."""
    integration = DownloadCLIIntegration()

    result = integration.fallback_to_legacy()

    assert result is False


def test_validate_configuration_valid(tmp_path):
    """_validate_configuration should return True when required keys exist."""
    integration = DownloadCLIIntegration()
    integration.config = {"DOWNLOAD_DIR": str(tmp_path)}

    result = integration._validate_configuration()

    assert result is True


def test_validate_configuration_invalid():
    """_validate_configuration should return False when required keys missing."""
    integration = DownloadCLIIntegration()
    integration.config = {}

    result = integration._validate_configuration()

    assert result is False


def test_check_download_directory_exists(mocker, tmp_path):
    """_check_download_directory should return True when directory exists."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = MagicMock()
    integration.android_downloader.get_download_dir.return_value = str(
        tmp_path / "android"
    )

    mocker.patch("os.path.exists", return_value=True)
    result = integration._check_download_directory()

    assert result is True


def test_check_download_directory_missing():
    """_check_download_directory should return False when no android downloader."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = None

    result = integration._check_download_directory()

    assert result is False


def test_get_legacy_compatibility_report(mocker):
    """get_legacy_compatibility_report should return compatibility info."""
    integration = DownloadCLIIntegration()

    mocker.patch.object(
        integration, "get_download_statistics", return_value={"total": 10}
    )
    result = integration.get_legacy_compatibility_report()

    assert result["cli_integration_ready"] is True
    assert result["expected_interface_compatibility"] is True
    assert result["statistics"]["total"] == 10


def test_log_integration_summary(mocker):
    """log_integration_summary should log comprehensive summary."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.orchestrator.failed_downloads = []

    mock_report = {
        "status": "completed",
        "android_downloader_initialized": True,
        "firmware_downloader_initialized": True,
        "orchestrator_initialized": True,
        "configuration_valid": True,
        "download_directory_exists": True,
    }

    mock_stats = {
        "total_downloads": 5,
        "failed_downloads": 0,
        "success_rate": 1.0,
        "android_downloads": 2,
        "firmware_downloads": 3,
        "repository_downloads": 0,
    }

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")

    mocker.patch.object(integration, "get_migration_report", return_value=mock_report)
    mocker.patch.object(integration, "get_download_statistics", return_value=mock_stats)
    integration.log_integration_summary()

    # Verify logging calls were made
    assert mock_logger.info.call_count > 5


def test_log_integration_summary_no_orchestrator(mocker):
    """log_integration_summary should handle missing orchestrator."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")
    integration.log_integration_summary()

    mock_logger.info.assert_called_with("CLI Integration: Not initialized")


def test_handle_cli_error(mocker):
    """handle_cli_error should log appropriate messages based on error type."""
    integration = DownloadCLIIntegration()

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")

    # Test ImportError
    integration.handle_cli_error(ImportError("module not found"))
    mock_logger.error.assert_any_call(
        "Import error - please check your Python environment and dependencies"
    )

    # Test FileNotFoundError
    integration.handle_cli_error(FileNotFoundError("file not found"))
    mock_logger.error.assert_any_call(
        "File not found - please check your configuration and paths"
    )

    # Test generic Exception
    integration.handle_cli_error(Exception("generic error"))
    mock_logger.error.assert_any_call(
        "An unexpected error occurred - please check logs for details"
    )


def test_get_cli_help_integration():
    """get_cli_help_integration should return help information."""
    integration = DownloadCLIIntegration()

    result = integration.get_cli_help_integration()

    assert "description" in result
    assert "usage" in result
    assert "features" in result
    assert "android_info" in result
    assert "firmware_info" in result


def test_update_cli_progress(mocker):
    """update_cli_progress should log progress messages."""
    integration = DownloadCLIIntegration()

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")
    integration.update_cli_progress("Downloading files", 0.5)
    mock_logger.info.assert_called_with("Progress: 50.0% - Downloading files")

    integration.update_cli_progress("Processing", 0.0)
    mock_logger.info.assert_called_with("Status: Processing")


def test_get_environment_info(tmp_path):
    """get_environment_info should return environment details."""
    integration = DownloadCLIIntegration()
    integration.config = {"DOWNLOAD_DIR": str(tmp_path)}

    result = integration.get_environment_info()

    assert "python_version" in result
    assert "working_directory" in result
    assert "download_directory" in result
    assert "configuration_loaded" in result
    assert result["configuration_loaded"] is True


def test_get_existing_prerelease_dirs():
    """_get_existing_prerelease_dirs should list firmware directories."""
    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        prerelease_dir = os.path.join(temp_dir, "prerelease")
        os.makedirs(prerelease_dir)

        # Create test directories
        os.makedirs(os.path.join(prerelease_dir, "firmware-v1.0"))
        os.makedirs(os.path.join(prerelease_dir, "firmware-v1.1"))

        # Create a file that should be ignored
        with open(os.path.join(prerelease_dir, "other-file"), "w") as f:
            f.write("test")

        result = _get_existing_prerelease_dirs(prerelease_dir)

        assert "firmware-v1.0" in result
        assert "firmware-v1.1" in result
        assert "other-file" not in result


def test_get_existing_prerelease_dirs_no_directory():
    """_get_existing_prerelease_dirs should return empty list when directory doesn't exist."""
    result = _get_existing_prerelease_dirs("/nonexistent/directory")
    assert result == []


def test_convert_results_to_legacy_format_with_file_type_categorization():
    """Test _convert_results_to_legacy_format properly categorizes file types."""
    integration = DownloadCLIIntegration()

    # Mock result objects
    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    # Create test results with different file types
    results = [
        MockResult("v1.0", "firmware"),
        MockResult("v2.0", "android"),
        MockResult("v1.1", "firmware_prerelease"),
        MockResult("v2.1", "android_prerelease"),
        MockResult("v1.2", "firmware_prerelease_repo"),
    ]

    # Test the function
    downloaded_firmwares, _new_firmware_versions, downloaded_apks, _new_apk_versions = (
        integration._convert_results_to_legacy_format(results)
    )

    # Verify file type categorization worked correctly
    assert "v1.0" in downloaded_firmwares  # firmware
    assert "v1.1" in downloaded_firmwares  # firmware_prerelease
    assert "v1.2" in downloaded_firmwares  # firmware_prerelease_repo
    assert "v2.0" in downloaded_apks  # android
    assert "v2.1" in downloaded_apks  # android_prerelease
