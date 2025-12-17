from unittest.mock import MagicMock

import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def test_cli_integration_main_loads_config_and_runs(mocker):
    """main should load config and delegate to run_download."""
    integration = DownloadCLIIntegration()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(True, "cfg"))
    mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"DOWNLOAD_DIR": "/tmp"}
    )
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

    result = integration.main()

    run_download.assert_called_once_with({"DOWNLOAD_DIR": "/tmp"}, False)
    assert result[0] == ["fw"]
    assert result[5] == "fw_latest"


def test_cli_integration_main_handles_missing_config(mocker):
    """If no configuration exists, main should bail out cleanly."""
    integration = DownloadCLIIntegration()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    run_download = mocker.patch.object(integration, "run_download")

    result = integration.main()

    run_download.assert_not_called()
    assert result == ([], [], [], [], [], "", "")


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
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(True, "cfg"))
    mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"DOWNLOAD_DIR": "/tmp"}
    )
    mocker.patch(
        "fetchtastic.download.cli_integration.get_effective_github_token",
        return_value=None,
    )
    run_download = mocker.patch.object(
        integration, "run_download", return_value=([], [], [], [], [], "", "")
    )

    integration.main(force_refresh=True)

    run_download.assert_called_once_with({"DOWNLOAD_DIR": "/tmp"}, True)


def test_cli_integration_main_handles_config_load_failure(mocker):
    """main should handle config load failure gracefully."""
    integration = DownloadCLIIntegration()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(True, "cfg"))
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    run_download = mocker.patch.object(integration, "run_download")

    result = integration.main()

    run_download.assert_not_called()
    assert result == ([], [], [], [], [], "", "")


def test_run_download_successful(mocker):
    """run_download should orchestrate successful download pipeline."""
    integration = DownloadCLIIntegration()

    # Mock components
    mock_orchestrator = MagicMock()
    mock_android = MagicMock()
    mock_firmware = MagicMock()

    config = {"DOWNLOAD_DIR": "/tmp"}
    mock_results = [
        MagicMock(
            release_tag="v1.0",
            file_path="/tmp/firmware.zip",
            was_skipped=False,
            file_type="firmware",
        )
    ]
    mock_orchestrator.run_download_pipeline.return_value = (mock_results, [])

    # Mock version getters
    mock_android.get_latest_release_tag.return_value = "v0.9"
    mock_firmware.get_latest_release_tag.return_value = "v0.9"
    mock_orchestrator.get_latest_versions.return_value = {
        "firmware": "v1.0",
        "android": "v1.0",
    }

    # Mock cleanup and update methods
    mock_orchestrator.cleanup_old_versions = MagicMock()
    mock_orchestrator.update_version_tracking = MagicMock()

    # Mock version manager for comparisons
    mock_version_manager = MagicMock()
    mock_version_manager.compare_versions.return_value = 1  # v1.0 > v0.9
    mock_android.get_version_manager.return_value = mock_version_manager

    # Add a mock cache manager to the orchestrator
    mock_orchestrator.cache_manager = MagicMock()
    mock_orchestrator.android_downloader = mock_android
    mock_orchestrator.firmware_downloader = mock_firmware

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )
    mock_android_class = mocker.patch(
        "fetchtastic.download.cli_integration.MeshtasticAndroidAppDownloader",
        return_value=mock_android,
    )
    mock_firmware_class = mocker.patch(
        "fetchtastic.download.cli_integration.FirmwareReleaseDownloader",
        return_value=mock_firmware,
    )
    result = integration.run_download(config)

    # The downloader classes are no longer instantiated separately - we reuse orchestrator's downloaders
    # mock_android_class.assert_called_once_with(config, mock_orchestrator.cache_manager)
    # mock_firmware_class.assert_called_once_with(config, mock_orchestrator.cache_manager)

    assert len(result) == 7
    assert result[0] == ["v1.0"]  # downloaded_firmwares
    assert result[1] == ["v1.0"]  # new_firmware_versions
    assert result[2] == []  # downloaded_apks
    assert result[3] == []  # new_apk_versions
    assert result[4] == []  # failed_downloads
    assert result[5] == "v1.0"  # latest_firmware_version
    assert result[6] == "v1.0"  # latest_apk_version


def test_run_download_with_force_refresh(mocker):
    """run_download should clear caches when force_refresh is True."""
    integration = DownloadCLIIntegration()

    mock_orchestrator = MagicMock()
    mock_android = MagicMock()
    mock_firmware = MagicMock()

    config = {"DOWNLOAD_DIR": "/tmp"}
    mock_orchestrator.run_download_pipeline.return_value = ([], [])
    mock_android.get_latest_release_tag.return_value = None
    mock_firmware.get_latest_release_tag.return_value = None
    mock_orchestrator.get_latest_versions.return_value = {
        "firmware": None,
        "android": None,
    }

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )
    mocker.patch(
        "fetchtastic.download.cli_integration.MeshtasticAndroidAppDownloader",
        return_value=mock_android,
    )
    mocker.patch(
        "fetchtastic.download.cli_integration.FirmwareReleaseDownloader",
        return_value=mock_firmware,
    )
    integration.run_download(config, force_refresh=True)

    # Since the cache manager is shared, we can check if it was called on any downloader
    integration.android_downloader.cache_manager.clear_all_caches.assert_called_once()


def test_run_download_handles_exception(mocker):
    """run_download should return empty results on exception."""
    integration = DownloadCLIIntegration()

    # Force an exception during initialization
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        side_effect=ValueError("test error"),
    )

    result = integration.run_download({"DOWNLOAD_DIR": "/tmp"})

    assert result == ([], [], [], [], [], "", "")


def test_clear_caches_successful(mocker):
    """_clear_caches should clear shared caches."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.orchestrator.cache_manager = MagicMock()
    integration.android_downloader = MagicMock()
    integration.android_downloader.cache_manager = (
        integration.orchestrator.cache_manager
    )
    mock_clear_all = mocker.patch.object(
        integration.orchestrator.cache_manager, "clear_all_caches"
    )

    # Should not raise exception
    integration._clear_caches()
    mock_clear_all.assert_called_once()


def test_convert_results_to_legacy_format_firmware():
    """_convert_results_to_legacy_format should handle firmware results."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.orchestrator.get_latest_versions.return_value = {
        "android": "v0.9",
        "firmware": "v0.9",
    }

    # Mock version manager
    mock_version_manager = MagicMock()
    mock_version_manager.compare_versions.return_value = 1  # v1.0 > v0.9
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    mock_result = MagicMock()
    mock_result.release_tag = "v1.0"
    mock_result.file_path = "/tmp/firmware/firmware.zip"
    mock_result.was_skipped = False
    mock_result.file_type = "firmware"

    results = [mock_result]

    downloaded_firmwares, new_firmware_versions, downloaded_apks, new_apk_versions = (
        integration._convert_results_to_legacy_format(results)
    )

    assert downloaded_firmwares == ["v1.0"]
    assert new_firmware_versions == ["v1.0"]
    assert downloaded_apks == []
    assert new_apk_versions == []


def test_convert_results_to_legacy_format_android():
    """_convert_results_to_legacy_format should handle android results."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.orchestrator.get_latest_versions.return_value = {
        "android": "v0.9",
        "firmware": "v0.9",
    }

    # Mock version manager
    mock_version_manager = MagicMock()
    mock_version_manager.compare_versions.return_value = 1  # v1.0 > v0.9
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    mock_result = MagicMock()
    mock_result.release_tag = "v1.0"
    mock_result.file_path = "/tmp/android/app.apk"
    mock_result.was_skipped = False
    mock_result.file_type = "android"

    results = [mock_result]

    downloaded_firmwares, new_firmware_versions, downloaded_apks, new_apk_versions = (
        integration._convert_results_to_legacy_format(results)
    )

    assert downloaded_firmwares == []
    assert new_firmware_versions == []
    assert downloaded_apks == ["v1.0"]
    assert new_apk_versions == ["v1.0"]


def test_convert_results_to_legacy_format_skipped():
    """_convert_results_to_legacy_format should skip results marked as was_skipped."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.orchestrator.get_latest_versions.return_value = {
        "android": "v0.9",
        "firmware": "v0.9",
    }
    mock_result = MagicMock()
    mock_result.release_tag = "v1.0"
    mock_result.file_path = "/tmp/firmware/firmware.zip"
    mock_result.was_skipped = True  # This should be skipped

    results = [mock_result]

    downloaded_firmwares, new_firmware_versions, downloaded_apks, new_apk_versions = (
        integration._convert_results_to_legacy_format(results)
    )

    assert downloaded_firmwares == []
    assert new_firmware_versions == []
    assert downloaded_apks == []
    assert new_apk_versions == []


def test_is_newer_version():
    """_is_newer_version should compare versions correctly."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = MagicMock()
    mock_version_manager = MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    mock_version_manager.compare_versions.return_value = 1  # version1 > version2

    result = integration._is_newer_version("v1.1", "v1.0")

    assert result is True
    mock_version_manager.compare_versions.assert_called_once_with("v1.1", "v1.0")


def test_is_newer_version_older():
    """_is_newer_version should return False for older version."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = MagicMock()
    mock_version_manager = MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    mock_version_manager.compare_versions.return_value = -1  # version1 < version2

    result = integration._is_newer_version("v1.0", "v1.1")

    assert result is False
    mock_version_manager.compare_versions.assert_called_once_with("v1.0", "v1.1")


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
        "prerelease": None,
    }

    result = integration.get_latest_versions()

    assert result["firmware"] == "v1.0"
    assert result["android"] == "v2.0"
    assert result["prerelease"] == ""


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
    integration.android_downloader._get_download_dir.return_value = "/tmp/android"

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


def test_validate_configuration_valid():
    """_validate_configuration should return True when required keys exist."""
    integration = DownloadCLIIntegration()
    integration.config = {"DOWNLOAD_DIR": "/tmp"}

    result = integration._validate_configuration()

    assert result is True


def test_validate_configuration_invalid():
    """_validate_configuration should return False when required keys missing."""
    integration = DownloadCLIIntegration()
    integration.config = {}

    result = integration._validate_configuration()

    assert result is False


def test_check_download_directory_exists(mocker):
    """_check_download_directory should return True when directory exists."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = MagicMock()
    integration.android_downloader._get_download_dir.return_value = "/tmp/android"

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


def test_get_environment_info():
    """get_environment_info should return environment details."""
    integration = DownloadCLIIntegration()
    integration.config = {"DOWNLOAD_DIR": "/tmp"}

    result = integration.get_environment_info()

    assert "python_version" in result
    assert "working_directory" in result
    assert "download_directory" in result
    assert "configuration_loaded" in result
    assert result["configuration_loaded"] is True


def test_get_existing_prerelease_dirs(mocker):
    """_get_existing_prerelease_dirs should list firmware directories."""
    integration = DownloadCLIIntegration()

    mocker.patch("os.path.exists", return_value=True)
    mocker.patch(
        "os.listdir", return_value=["firmware-v1.0", "firmware-v1.1", "other-file"]
    )
    mocker.patch("os.path.isdir", return_value=True)
    mocker.patch("os.path.islink", return_value=False)
    result = integration._get_existing_prerelease_dirs("/tmp/prerelease")

    assert "firmware-v1.0" in result
    assert "firmware-v1.1" in result
    assert "other-file" not in result


def test_get_existing_prerelease_dirs_no_directory(mocker):
    """_get_existing_prerelease_dirs should return empty list when directory doesn't exist."""
    integration = DownloadCLIIntegration()

    mocker.patch("os.path.exists", return_value=False)
    result = integration._get_existing_prerelease_dirs("/tmp/prerelease")

    assert result == []
