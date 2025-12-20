from unittest.mock import MagicMock

import pytest

from fetchtastic import setup_config


@pytest.mark.unit
@pytest.mark.configuration
def test_run_setup_triggers_first_run_download_on_non_windows(tmp_path, mocker):
    """run_setup should invoke DownloadCLIIntegration().main() when user accepts first run."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "fetchtastic.yaml"

    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(config_dir))
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(config_file))
    mocker.patch("fetchtastic.setup_config.BASE_DIR", str(tmp_path / "downloads"))
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")

    def passthrough(config, *args, **kwargs):
        return config

    mocker.patch("fetchtastic.setup_config._setup_base", side_effect=passthrough)
    mocker.patch(
        "fetchtastic.setup_config._setup_downloads",
        side_effect=lambda config, *_args, **_kwargs: (config, True, True),
    )
    mocker.patch("fetchtastic.setup_config._setup_android", side_effect=passthrough)
    mocker.patch("fetchtastic.setup_config._setup_firmware", side_effect=passthrough)
    mocker.patch("fetchtastic.setup_config._setup_automation", side_effect=passthrough)
    mocker.patch(
        "fetchtastic.setup_config._setup_notifications", side_effect=passthrough
    )
    mocker.patch("fetchtastic.setup_config._setup_github", side_effect=passthrough)

    integration_instance = MagicMock()
    mock_integration = mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=integration_instance,
    )

    mocker.patch("builtins.input", return_value="y")

    setup_config.run_setup()

    mock_integration.assert_called_once()
    integration_instance.main.assert_called_once()
    assert isinstance(integration_instance.main.call_args.kwargs.get("config"), dict)


@pytest.mark.unit
@pytest.mark.configuration
def test_run_setup_skips_first_run_when_user_declines(tmp_path, mocker):
    """run_setup should not invoke DownloadCLIIntegration when user declines first run."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "fetchtastic.yaml"

    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(config_dir))
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(config_file))
    mocker.patch("fetchtastic.setup_config.BASE_DIR", str(tmp_path / "downloads"))
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")

    def passthrough(config, *args, **kwargs):
        return config

    mocker.patch("fetchtastic.setup_config._setup_base", side_effect=passthrough)
    mocker.patch(
        "fetchtastic.setup_config._setup_downloads",
        side_effect=lambda config, *_args, **_kwargs: (config, True, True),
    )
    mocker.patch("fetchtastic.setup_config._setup_android", side_effect=passthrough)
    mocker.patch("fetchtastic.setup_config._setup_firmware", side_effect=passthrough)
    mocker.patch("fetchtastic.setup_config._setup_automation", side_effect=passthrough)
    mocker.patch(
        "fetchtastic.setup_config._setup_notifications", side_effect=passthrough
    )
    mocker.patch("fetchtastic.setup_config._setup_github", side_effect=passthrough)

    integration_instance = MagicMock()
    mock_integration = mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=integration_instance,
    )

    mocker.patch("builtins.input", return_value="n")

    setup_config.run_setup()

    mock_integration.assert_not_called()
    integration_instance.main.assert_not_called()
