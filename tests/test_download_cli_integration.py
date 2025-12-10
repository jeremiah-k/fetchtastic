import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration


def test_cli_integration_main_loads_config_and_runs(mocker):
    """main should load config and delegate to run_download."""
    integration = DownloadCLIIntegration()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(True, "cfg"))
    mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"DOWNLOAD_DIR": "/tmp"}
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
