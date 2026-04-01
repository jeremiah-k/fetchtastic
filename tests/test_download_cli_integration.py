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
            [],
            [],
            [],
            [],
            [],
            "fw_latest",
            "apk_latest",
            "desktop_latest",
        ),
    )

    result = integration.main(config=config)

    run_download.assert_called_once_with({"DOWNLOAD_DIR": "/tmp"}, False)

    # Unpack the 13-field tuple into named locals
    (
        downloaded_firmwares,
        new_firmware_versions,
        downloaded_apks,
        new_apk_versions,
        downloaded_desktop,
        new_desktop_versions,
        downloaded_firmware_prereleases,
        downloaded_apk_prereleases,
        downloaded_desktop_prereleases,
        failed_downloads,
        latest_firmware_version,
        latest_apk_version,
        latest_desktop_version,
    ) = result

    assert downloaded_firmwares == ["fw"]
    assert latest_firmware_version == "fw_latest"
    assert latest_apk_version == "apk_latest"
    assert latest_desktop_version == "desktop_latest"


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
            [],
            [],
            [],
            [],
            [],
            "fw_latest",
            "apk_latest",
            "",
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
        integration,
        "run_download",
        return_value=([], [], [], [], [], [], [], [], [], [], "", "", ""),
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


def test_cli_integration_clear_cache_loads_config(mocker):
    """clear_cache should use provided config and clear caches."""
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

    result = integration.clear_cache(config=config)

    mock_clear.assert_called_once()
    assert result is True


def test_cli_integration_clear_cache_requires_config():
    """clear_cache should require config parameter."""
    integration = DownloadCLIIntegration()

    # Test that calling without config parameter raises TypeError
    with pytest.raises(TypeError):
        integration.clear_cache()


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
        "desktop": "",
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
    assert len(result) == 13

    # Unpack the 13-field tuple into named locals
    (
        downloaded_firmwares,
        new_firmware_versions,
        downloaded_apks,
        new_apk_versions,
        downloaded_desktop,
        new_desktop_versions,
        downloaded_firmware_prereleases,
        downloaded_apk_prereleases,
        downloaded_desktop_prereleases,
        failed_downloads,
        latest_firmware_version,
        latest_apk_version,
        latest_desktop_version,
    ) = result

    assert downloaded_firmwares == ["v1.0.0"]  # skipped one excluded
    assert new_firmware_versions == ["v1.0.0"]  # newer than v0.9.0
    assert downloaded_apks == ["v2.0.0"]
    assert new_apk_versions == ["v2.0.0"]  # newer than v1.9.0
    assert downloaded_desktop == []
    assert new_desktop_versions == []
    assert downloaded_firmware_prereleases == []
    assert downloaded_apk_prereleases == []
    assert downloaded_desktop_prereleases == []
    assert failed_downloads == []
    assert latest_firmware_version == "v0.9.0"  # from orchestrator
    assert latest_apk_version == "v1.9.0"  # from orchestrator
    assert latest_desktop_version == ""  # from orchestrator

    # Verify version comparison was called for new version detection
    # Should be called for each downloaded item (2 times in this test)
    assert mock_version_manager.compare_versions.call_count == 2
    # Verify it was called with the correct arguments
    calls = mock_version_manager.compare_versions.call_args_list
    assert any(call[0] == ("v1.0.0", "v0.9.0") for call in calls)
    assert any(call[0] == ("v2.0.0", "v1.9.0") for call in calls)


def test_run_download_uses_tracked_desktop_version_for_new_detection(mocker):
    """Desktop new-version detection should compare against tracked local desktop version."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.run_download_pipeline.return_value = (
        [
            MagicMock(
                release_tag="v2.0.0",
                file_path="/tmp/Meshtastic-2.0.0.dmg",
                was_skipped=False,
                file_type="desktop",
            )
        ],
        [],
    )
    mock_orchestrator.cleanup_old_versions.return_value = None
    mock_orchestrator.update_version_tracking.return_value = None
    # Remote/latest value matches downloaded tag and should not be used for "new".
    mock_orchestrator.get_latest_versions.return_value = {
        "firmware": "",
        "android": "",
        "firmware_prerelease": "",
        "android_prerelease": "",
        "desktop": "v2.0.0",
        "desktop_prerelease": "",
    }

    mock_version_manager = MagicMock()
    mock_version_manager.compare_versions.side_effect = lambda version1, version2: (
        1 if (version1, version2) == ("v2.0.0", "v1.9.0") else 0
    )
    mock_android_downloader = MagicMock()
    mock_android_downloader.get_version_manager.return_value = mock_version_manager
    mock_orchestrator.android_downloader = mock_android_downloader
    mock_orchestrator.firmware_downloader = MagicMock()

    mock_desktop_downloader = MagicMock()
    mock_desktop_downloader.get_latest_release_tag.return_value = "v1.9.0"
    mock_desktop_downloader.get_latest_prerelease_tag.return_value = ""
    mock_orchestrator.desktop_downloader = mock_desktop_downloader

    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadOrchestrator",
        return_value=mock_orchestrator,
    )

    result = integration.run_download(config=config, force_refresh=False)

    assert result[4] == ["v2.0.0"]  # downloaded_desktop
    assert result[5] == ["v2.0.0"]  # new_desktop_versions


def test_log_download_results_summary_logs_history_summary(mocker):
    """Summary logging should emit firmware history summaries when available."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={"firmware_prerelease": "", "android_prerelease": ""}
    )

    integration.log_download_results_summary(
        elapsed_seconds=1.23,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[],
        latest_firmware_version="v1.0.0",
        latest_apk_version="v2.0.0",
        new_firmware_versions=[],
        new_apk_versions=[],
    )

    integration.orchestrator.log_firmware_release_history_summary.assert_called_once()


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
    assert result == ([], [], [], [], [], [], [], [], [], [], "", "", "")


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

    mock_manifest_failure = MagicMock()
    mock_manifest_failure.file_type = "firmware_manifest"
    mock_manifest_failure.file_path = "/tmp/firmware-2.7.20.json"
    mock_manifest_failure.release_tag = "v2.7.20"
    mock_manifest_failure.download_url = "https://example.com/firmware-2.7.20.json"
    mock_manifest_failure.error_message = "Manifest download failed"
    mock_manifest_failure.is_retryable = False
    mock_manifest_failure.http_status_code = 500

    integration.orchestrator.failed_downloads = [mock_failure, mock_manifest_failure]

    result = integration.get_failed_downloads()

    assert len(result) == 2
    firmware_failure = result[0]
    assert firmware_failure["file_name"] == "firmware.zip"
    assert firmware_failure["release_tag"] == "v1.0"
    assert firmware_failure["url"] == "https://example.com/firmware.zip"
    assert firmware_failure["type"] == "Firmware"
    assert firmware_failure["error"] == "Download failed"
    assert firmware_failure["retryable"] is True
    assert firmware_failure["http_status"] == 404

    manifest_failure = result[1]
    assert manifest_failure["file_name"] == "firmware-2.7.20.json"
    assert manifest_failure["release_tag"] == "v2.7.20"
    assert manifest_failure["type"] == "Firmware Manifest"


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
    (
        downloaded_firmwares,
        _new_firmware_versions,
        downloaded_apks,
        _new_apk_versions,
        _downloaded_desktop,
        _new_desktop_versions,
        _downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
        _downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    # Verify file type categorization worked correctly
    assert "v1.0" in downloaded_firmwares  # firmware
    assert "v2.0" in downloaded_apks  # android
    assert "v1.1" in _downloaded_firmware_prereleases  # firmware_prerelease
    assert "v1.2" in _downloaded_firmware_prereleases  # firmware_prerelease_repo
    assert "v2.1" in _downloaded_apk_prereleases  # android_prerelease


def test_convert_results_uses_android_prerelease_for_comparison(mocker):
    """Android prerelease comparisons should use prerelease current version."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": None,
        "android": "v2.7.9",
        "firmware_prerelease": None,
        "android_prerelease": "v2.7.10-open.1",
    }

    mock_version_manager = mocker.MagicMock()

    def compare_side_effect(version1, version2):
        """
        Test comparator that simulates specific version comparison outcomes.

        Returns 0 when the versions are considered equal, 1 when the first version
        is considered newer than the second, and defaults to 0 for all other inputs.

        Parameters:
            version1 (str): First version string to compare.
            version2 (str): Second version string to compare.

        Returns:
            int: `0` if versions are equal, `1` if `version1` is newer than `version2`, otherwise `0`.
        """
        if (version1, version2) == ("v2.7.10-open.1", "v2.7.10-open.1"):
            return 0
        if (version1, version2) == ("v2.7.10-open.1", "v2.7.9"):
            return 1
        return 0

    mock_version_manager.compare_versions.side_effect = compare_side_effect
    integration.android_downloader = mocker.MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            """
            Initialize a download result record with its release tag, file type, and skipped status.

            Parameters:
                release_tag (str): The release identifier associated with the file (e.g., version or tag).
                file_type (str): The category of the file (e.g., "firmware", "android", "firmware_prerelease", "android_prerelease").
                was_skipped (bool): Whether the file was skipped during processing; defaults to False.
            """
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    results = [MockResult("v2.7.10-open.1", "android_prerelease", False)]
    (
        _downloaded_fw,
        _new_fw,
        downloaded_apks,
        new_apks,
        _downloaded_desktop,
        _new_desktop_versions,
        _downloaded_firmware_prereleases,
        downloaded_apk_prereleases,
        _downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    assert new_apks == []
    assert downloaded_apks == []
    assert downloaded_apk_prereleases == ["v2.7.10-open.1"]
    calls = [call[0] for call in mock_version_manager.compare_versions.call_args_list]
    assert ("v2.7.10-open.1", "v2.7.10-open.1") in calls
    assert ("v2.7.10-open.1", "v2.7.9") not in calls


def test_convert_results_normalizes_firmware_prerelease_tags(mocker):
    """Firmware prerelease comparisons should normalize firmware- prefix."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": "v2.7.9",
        "android": None,
        "firmware_prerelease": "2.7.10-abcdef",
        "android_prerelease": None,
    }

    mock_version_manager = mocker.MagicMock()

    def compare_side_effect(version1, version2):
        """
        Simulates comparison outcomes for a small set of version string pairs used in tests.

        Parameters:
            version1 (str): The first version string to compare.
            version2 (str): The second version string to compare.

        Returns:
            int: `1` if `version1` is treated as newer than `version2` for a handled pair, `0` if they are considered equal or the pair is not explicitly handled.
        """
        if (version1, version2) == ("2.7.10-abcdef", "2.7.10-abcdef"):
            return 0
        if (version1, version2) == ("2.7.10-abcdef", "v2.7.9"):
            return 1
        return 0

    mock_version_manager.compare_versions.side_effect = compare_side_effect
    integration.android_downloader = mocker.MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            """
            Initialize a download result record with its release tag, file type, and skipped status.

            Parameters:
                release_tag (str): The release identifier associated with the file (e.g., version or tag).
                file_type (str): The category of the file (e.g., "firmware", "android", "firmware_prerelease", "android_prerelease").
                was_skipped (bool): Whether the file was skipped during processing; defaults to False.
            """
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    results = [MockResult("firmware-2.7.10-abcdef", "firmware_prerelease", False)]
    (
        downloaded_fw,
        new_fw,
        _downloaded_apks,
        _new_apks,
        _downloaded_desktop,
        _new_desktop_versions,
        downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
        _downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    assert new_fw == []
    assert downloaded_fw == []
    assert downloaded_firmware_prereleases == ["firmware-2.7.10-abcdef"]
    calls = [call[0] for call in mock_version_manager.compare_versions.call_args_list]
    assert ("2.7.10-abcdef", "2.7.10-abcdef") in calls
    assert ("firmware-2.7.10-abcdef", "2.7.10-abcdef") not in calls


def test_log_download_results_summary_logging_order(mocker):
    """log_download_results_summary should log versions in correct order: firmware, firmware prerelease, APK, APK prerelease."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()

    mock_orchestrator = integration.orchestrator
    mock_orchestrator.log_firmware_release_history_summary = MagicMock()
    mock_orchestrator.get_latest_versions.return_value = {
        "firmware": "v2.7.18.fb3bf78",
        "android": "v2.7.11",
        "firmware_prerelease": "v2.7.19-prerelease",
        "android_prerelease": None,
    }

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=19.5,
        downloaded_firmwares=["firmware.zip"],
        downloaded_apks=["android.apk"],
        downloaded_firmware_prereleases=["prerelease.zip"],
        downloaded_apk_prereleases=[],
        failed_downloads=[],
        latest_firmware_version="v2.7.18.fb3bf78",
        latest_apk_version="v2.7.11",
        new_firmware_versions=[],
        new_apk_versions=[],
    )

    latest_version_calls = []
    for call_args in mock_logger.info.call_args_list:
        call_str = str(call_args)
        if (
            "Latest firmware:" in call_str
            or "Latest APK:" in call_str
            or "firmware prerelease:" in call_str
            or "APK prerelease:" in call_str
        ):
            latest_version_calls.append(call_str)

    joined_calls = " ".join(latest_version_calls)

    assert "Latest firmware: v2.7.18.fb3bf78" in joined_calls
    assert "Latest firmware prerelease: v2.7.19-prerelease" in joined_calls
    assert "Latest APK: v2.7.11" in joined_calls
    assert "Latest APK prerelease: none" in joined_calls

    assert joined_calls.index("Latest firmware: v2.7.18.fb3bf78") < joined_calls.index(
        "Latest firmware prerelease: v2.7.19-prerelease"
    )
    assert joined_calls.index(
        "Latest firmware prerelease: v2.7.19-prerelease"
    ) < joined_calls.index("Latest APK: v2.7.11")
    assert joined_calls.index("Latest APK: v2.7.11") < joined_calls.index(
        "Latest APK prerelease: none"
    )


def test_run_download_orchestrator_none_after_init(mocker):
    """Test run_download raises RuntimeError when orchestrator is None after _initialize_components (lines 122-123)."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    def mock_init_components(cfg):
        integration.orchestrator = None

    mocker.patch.object(
        integration, "_initialize_components", side_effect=mock_init_components
    )

    with pytest.raises(
        RuntimeError, match="Failed to initialize download orchestrator"
    ):
        integration.run_download(config=config, force_refresh=False)


def test_get_tracked_desktop_versions_no_downloader(mocker):
    """_get_tracked_desktop_versions should return Nones when no desktop downloader (lines 221, 231)."""
    integration = DownloadCLIIntegration()
    integration.desktop_downloader = None

    result = integration._get_tracked_desktop_versions()

    assert result == {"desktop": None, "desktop_prerelease": None}


def test_get_tracked_desktop_versions_with_exceptions(mocker):
    """_get_tracked_desktop_versions should handle exceptions from downloader (lines 224-225, 231-232)."""
    integration = DownloadCLIIntegration()
    mock_desktop_downloader = mocker.MagicMock()
    mock_desktop_downloader.get_latest_release_tag.side_effect = OSError("io error")
    mock_desktop_downloader.get_latest_prerelease_tag.side_effect = ValueError(
        "value error"
    )
    integration.desktop_downloader = mock_desktop_downloader

    result = integration._get_tracked_desktop_versions()

    assert result == {"desktop": None, "desktop_prerelease": None}


def test_get_tracked_desktop_versions_non_string_return(mocker):
    """_get_tracked_desktop_versions should handle non-string returns (lines 234-237)."""
    integration = DownloadCLIIntegration()
    mock_desktop_downloader = mocker.MagicMock()
    mock_desktop_downloader.get_latest_release_tag.return_value = 123
    mock_desktop_downloader.get_latest_prerelease_tag.return_value = None
    integration.desktop_downloader = mock_desktop_downloader

    result = integration._get_tracked_desktop_versions()

    assert result == {"desktop": None, "desktop_prerelease": None}


def test_clear_caches_handles_error(mocker):
    """_clear_caches should handle and log errors (lines 250-258)."""
    integration = DownloadCLIIntegration()
    mock_android = mocker.MagicMock()
    mock_android.cache_manager.clear_all_caches.side_effect = OSError("cache error")
    integration.android_downloader = mock_android

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")
    integration._clear_caches()

    mock_logger.error.assert_called()


def test_clear_caches_no_android_downloader(mocker):
    """_clear_caches should handle missing android downloader (line 252)."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = None

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")
    integration._clear_caches()

    mock_logger.info.assert_called_with("All caches cleared")


def test_log_download_results_summary_no_orchestrator(mocker):
    """log_download_results_summary should handle missing orchestrator (line 300)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = None
    integration.get_latest_versions = mocker.MagicMock(
        return_value={"firmware_prerelease": "", "android_prerelease": ""}
    )
    mock_logger = mocker.MagicMock()

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[],
        latest_firmware_version="",
        latest_apk_version="",
    )

    # Verify logger was called (function works with no orchestrator)
    mock_logger.info.assert_called()


def test_log_download_results_summary_empty_versions(mocker):
    """log_download_results_summary should handle empty version strings (lines 325, 332, 340)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={
            "firmware_prerelease": "",
            "android_prerelease": "",
            "desktop_prerelease": "",
        }
    )
    mock_logger = mocker.MagicMock()

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[],
        latest_firmware_version="",
        latest_apk_version="",
        latest_desktop_version="",
    )

    logged_messages = [str(call) for call in mock_logger.info.call_args_list]
    assert not any("Latest firmware:" in msg and "v" in msg for msg in logged_messages)
    assert not any("Latest APK:" in msg and "v" in msg for msg in logged_messages)
    assert not any("Latest desktop:" in msg and "v" in msg for msg in logged_messages)


def test_log_download_results_summary_with_desktop_prerelease(mocker):
    """log_download_results_summary should log desktop prerelease (line 342)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={
            "firmware_prerelease": "",
            "android_prerelease": "",
            "desktop_prerelease": "v2.0.0-beta",
        }
    )
    mock_logger = mocker.MagicMock()

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[],
        latest_firmware_version="",
        latest_apk_version="",
        latest_desktop_version="v2.0.0",
    )

    logged_messages = [str(call) for call in mock_logger.info.call_args_list]
    assert any("v2.0.0-beta" in msg for msg in logged_messages)


def test_log_download_results_summary_with_failed_downloads(mocker):
    """log_download_results_summary should log failed downloads (lines 347-353)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={
            "firmware_prerelease": "",
            "android_prerelease": "",
            "desktop_prerelease": "",
        }
    )
    mock_logger = mocker.MagicMock()

    failed_downloads = [
        {
            "type": "Firmware",
            "release_tag": "v1.0.0",
            "file_name": "firmware.zip",
            "url": "https://example.com/firmware.zip",
            "retryable": True,
            "http_status": 404,
            "error": "Not found",
        }
    ]

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=failed_downloads,
        latest_firmware_version="",
        latest_apk_version="",
    )

    logged_messages = [str(call) for call in mock_logger.info.call_args_list]
    assert any("1 downloads failed" in msg for msg in logged_messages)


def test_log_download_results_summary_all_failed(mocker):
    """log_download_results_summary should log when all downloads failed (line 365)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={
            "firmware_prerelease": "",
            "android_prerelease": "",
            "desktop_prerelease": "",
        }
    )
    mock_logger = mocker.MagicMock()

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[
            {
                "type": "Firmware",
                "release_tag": "v1.0",
                "file_name": "f.zip",
                "url": "u",
                "retryable": True,
                "http_status": 500,
                "error": "err",
            }
        ],
        latest_firmware_version="",
        latest_apk_version="",
    )

    logged_messages = [str(call) for call in mock_logger.info.call_args_list]
    assert any("All attempted downloads failed" in msg for msg in logged_messages)


def test_log_download_results_summary_api_requests(mocker):
    """log_download_results_summary should log API summary when requests made (line 382)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.get_latest_versions = mocker.MagicMock(
        return_value={
            "firmware_prerelease": "",
            "android_prerelease": "",
            "desktop_prerelease": "",
        }
    )
    mock_logger = mocker.MagicMock()

    mocker.patch(
        "fetchtastic.download.cli_integration.get_api_request_summary",
        return_value={"total_requests": 5},
    )
    mocker.patch(
        "fetchtastic.download.cli_integration.format_api_summary",
        return_value="API Summary",
    )

    integration.log_download_results_summary(
        logger_override=mock_logger,
        elapsed_seconds=1.0,
        downloaded_firmwares=[],
        downloaded_apks=[],
        failed_downloads=[],
        latest_firmware_version="",
        latest_apk_version="",
    )

    mock_logger.debug.assert_called()


def test_convert_results_desktop_release(mocker):
    """_convert_results_to_legacy_format should handle desktop releases (line 507, 538-540)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": None,
        "android": None,
        "desktop": "v1.9.0",
        "desktop_prerelease": None,
    }

    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    mock_version_manager = mocker.MagicMock()
    mock_version_manager.compare_versions.return_value = 1
    integration.android_downloader = mocker.MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    results = [MockResult("v2.0.0", "desktop", False)]
    (
        _downloaded_fw,
        _new_fw,
        _downloaded_apks,
        _new_apks,
        downloaded_desktop,
        new_desktop,
        _downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
        _downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    assert downloaded_desktop == ["v2.0.0"]
    assert new_desktop == ["v2.0.0"]


def test_convert_results_desktop_prerelease(mocker):
    """_convert_results_to_legacy_format should handle desktop prereleases (lines 507, 538-540)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": None,
        "android": None,
        "desktop": "v1.9.0",
        "desktop_prerelease": "v1.9.5-beta",
    }

    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    mock_version_manager = mocker.MagicMock()
    mock_version_manager.compare_versions.return_value = 1
    integration.android_downloader = mocker.MagicMock()
    integration.android_downloader.get_version_manager.return_value = (
        mock_version_manager
    )

    results = [MockResult("v2.0.0-beta", "desktop_prerelease", False)]
    (
        _downloaded_fw,
        _new_fw,
        _downloaded_apks,
        _new_apks,
        _downloaded_desktop,
        _new_desktop,
        _downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
        downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    assert downloaded_desktop_prereleases == ["v2.0.0-beta"]


def test_convert_results_empty_release_tag(mocker):
    """_convert_results_to_legacy_format should skip empty release tags (line 462)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.get_latest_versions.return_value = {}

    class MockResult:
        def __init__(self, release_tag, file_type, was_skipped=False):
            self.release_tag = release_tag
            self.file_type = file_type
            self.was_skipped = was_skipped

    results = [MockResult("", "firmware", False), MockResult(None, "firmware", False)]
    (
        downloaded_fw,
        _new_fw,
        _downloaded_apks,
        _new_apks,
        _downloaded_desktop,
        _new_desktop,
        _downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
        _downloaded_desktop_prereleases,
    ) = integration._convert_results_to_legacy_format(results)

    assert downloaded_fw == []


def test_add_downloaded_asset_already_exists():
    """_add_downloaded_asset should not add duplicates (line 613)."""
    integration = DownloadCLIIntegration()
    downloaded_list = ["v1.0.0"]
    downloaded_set = {"v1.0.0"}

    integration._add_downloaded_asset("v1.0.0", downloaded_list, downloaded_set)

    assert downloaded_list == ["v1.0.0"]
    assert downloaded_set == {"v1.0.0"}


def test_get_version_manager_no_android_downloader():
    """_get_version_manager should return None when no android downloader (line 641)."""
    integration = DownloadCLIIntegration()
    integration.android_downloader = None

    result = integration._get_version_manager()

    assert result is None


def test_get_version_manager_via_attribute(mocker):
    """_get_version_manager should fall back to attribute access (lines 648-649)."""
    integration = DownloadCLIIntegration()
    mock_android = mocker.MagicMock()
    del mock_android.get_version_manager
    mock_android.version_manager = "version_manager_instance"
    integration.android_downloader = mock_android

    result = integration._get_version_manager()

    assert result == "version_manager_instance"


def test_main_handles_exception(mocker):
    """main should handle exceptions and return empty results (lines 772-780)."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    mocker.patch(
        "fetchtastic.download.cli_integration.get_effective_github_token",
        return_value=None,
    )
    mocker.patch.object(
        integration,
        "run_download",
        side_effect=OSError("network error"),
    )

    result = integration.main(config=config)

    assert result == ([], [], [], [], [], [], [], [], [], [], "", "", "")


def test_clear_cache_handles_exception(mocker):
    """clear_cache should handle exceptions and return False (lines 798-806)."""
    integration = DownloadCLIIntegration()
    config = {"DOWNLOAD_DIR": "/tmp"}

    mocker.patch.object(
        integration,
        "_initialize_components",
        side_effect=ValueError("config error"),
    )

    result = integration.clear_cache(config=config)

    assert result is False


def test_validate_integration_creates_directory(mocker):
    """validate_integration should create missing download directory (line 882)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.android_downloader.get_releases.return_value = [MagicMock()]
    integration.firmware_downloader.get_releases.return_value = [MagicMock()]
    integration.android_downloader.get_download_dir.return_value = "/tmp/new_dir"

    mocker.patch("os.path.exists", return_value=False)
    mock_makedirs = mocker.patch("os.makedirs")

    result = integration.validate_integration()

    assert result is True
    mock_makedirs.assert_called_once_with("/tmp/new_dir", exist_ok=True)


def test_validate_integration_handles_exception(mocker):
    """validate_integration should handle exceptions (lines 886-894)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    integration.android_downloader = MagicMock()
    integration.firmware_downloader = MagicMock()

    integration.android_downloader.get_releases.side_effect = OSError("network error")

    result = integration.validate_integration()

    assert result is False


def test_log_integration_summary_with_failed_downloads(mocker):
    """log_integration_summary should log failed downloads (lines 1037-1039)."""
    integration = DownloadCLIIntegration()
    integration.orchestrator = MagicMock()
    mock_failure = MagicMock()
    mock_failure.file_type = "firmware"
    mock_failure.release_tag = "v1.0.0"
    mock_failure.download_url = "https://example.com/firmware.zip"
    mock_failure.error_message = "Download failed"
    mock_failure.is_retryable = True
    integration.orchestrator.failed_downloads = [mock_failure]

    mock_report = {
        "status": "completed",
        "android_downloader_initialized": True,
        "firmware_downloader_initialized": True,
        "orchestrator_initialized": True,
        "configuration_valid": True,
        "download_directory_exists": True,
    }
    mock_stats = {
        "total_downloads": 1,
        "failed_downloads": 1,
        "success_rate": 0.0,
        "android_downloads": 0,
        "firmware_downloads": 0,
        "repository_downloads": 0,
    }

    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")
    mocker.patch.object(integration, "get_migration_report", return_value=mock_report)
    mocker.patch.object(integration, "get_download_statistics", return_value=mock_stats)

    integration.log_integration_summary()

    logged_messages = [str(call) for call in mock_logger.info.call_args_list]
    assert any("Failed downloads with URLs" in msg for msg in logged_messages)


def test_handle_cli_error_permission_error(mocker):
    """handle_cli_error should handle PermissionError (line 1064)."""
    integration = DownloadCLIIntegration()
    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")

    integration.handle_cli_error(PermissionError("access denied"))

    mock_logger.error.assert_any_call(
        "Permission error - please check file system permissions"
    )


def test_handle_cli_error_network_error(mocker):
    """handle_cli_error should handle network errors (line 1068)."""
    import requests

    integration = DownloadCLIIntegration()
    mock_logger = mocker.patch("fetchtastic.download.cli_integration.logger")

    integration.handle_cli_error(requests.ConnectionError("connection failed"))

    mock_logger.error.assert_any_call(
        "Network connection error - please check your internet connection"
    )
