import os
from unittest.mock import MagicMock

import pytest

import fetchtastic.setup_config as setup_config


@pytest.mark.configuration
@pytest.mark.unit
def test_crontab_available_true_when_present(mocker):
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")
    assert setup_config._crontab_available() is True


@pytest.mark.configuration
@pytest.mark.unit
def test_crontab_available_false_when_missing_prints_message(mocker, capsys):
    mocker.patch("fetchtastic.setup_config.shutil.which", return_value=None)
    # Function is now pure and doesn't print anything directly
    setup_config._crontab_available()
    captured = capsys.readouterr()
    assert captured.out == ""


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_cron_job_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")

    setup_config.setup_cron_job("hourly")

    mock_run.assert_not_called()
    mock_popen.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_remove_cron_job_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")

    setup_config.remove_cron_job()

    mock_run.assert_not_called()
    mock_popen.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_check_cron_job_exists_file_not_found_returns_false(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("subprocess.run", side_effect=FileNotFoundError())
    assert setup_config.check_cron_job_exists() is False


@pytest.mark.configuration
@pytest.mark.unit
def test_check_any_cron_jobs_exist_file_not_found_returns_false(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("subprocess.run", side_effect=FileNotFoundError())
    assert setup_config.check_any_cron_jobs_exist() is False


@pytest.mark.configuration
@pytest.mark.unit
def test_check_cron_job_exists_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    assert setup_config.check_cron_job_exists() is False
    mock_run.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_check_any_cron_jobs_exist_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    assert setup_config.check_any_cron_jobs_exist() is False
    mock_run.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_reboot_cron_job_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")

    setup_config.setup_reboot_cron_job()

    mock_run.assert_not_called()
    mock_popen.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_remove_reboot_cron_job_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")

    setup_config.remove_reboot_cron_job()

    mock_run.assert_not_called()
    mock_popen.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_linux_skips_when_crontab_unavailable(mocker):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
    mock_check_any = mocker.patch("fetchtastic.setup_config.check_any_cron_jobs_exist")

    config = {"EXISTING": "value"}
    assert setup_config._setup_automation(config, False, lambda _: True) == config
    mock_check_any.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_run_setup_handles_empty_partial_sections(mocker, capsys):
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(True, None))
    mocker.patch("fetchtastic.setup_config._setup_base", return_value={})
    mocker.patch(
        "fetchtastic.setup_config._setup_downloads", return_value=({}, False, False)
    )

    setup_config.run_setup(sections=["firmware"])

    captured = capsys.readouterr()
    assert "Updating Fetchtastic setup sections:" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_run_setup_first_run_calls_download_cli_integration(mocker, tmp_path):
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))

    mocker.patch.object(setup_config, "BASE_DIR", str(tmp_path / "downloads"))
    mocker.patch.object(setup_config, "CONFIG_DIR", str(tmp_path / "config"))
    mocker.patch.object(
        setup_config, "CONFIG_FILE", str(tmp_path / "config" / "config.yaml")
    )
    os.makedirs(setup_config.CONFIG_DIR, exist_ok=True)

    mocker.patch("fetchtastic.setup_config._setup_base", return_value={})
    mocker.patch(
        "fetchtastic.setup_config._setup_downloads", return_value=({}, True, False)
    )
    mocker.patch(
        "fetchtastic.setup_config._setup_android", side_effect=lambda config, *_: config
    )
    mocker.patch(
        "fetchtastic.setup_config._setup_automation",
        side_effect=lambda config, *_: config,
    )
    mocker.patch(
        "fetchtastic.setup_config._setup_notifications",
        side_effect=lambda config: config,
    )
    mocker.patch(
        "fetchtastic.setup_config._setup_github", side_effect=lambda config: config
    )

    mocker.patch("builtins.input", return_value="y")

    mock_download_cli_cls = mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration"
    )
    mock_instance = MagicMock()
    mock_download_cli_cls.return_value = mock_instance

    setup_config.run_setup()

    mock_download_cli_cls.assert_called_once()
    mock_instance.main.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_non_interactive_uses_recommended(mocker, capsys):
    config: dict = {}
    mocker.patch("fetchtastic.setup_config.sys.stdin.isatty", return_value=False)
    mocker.patch.dict(os.environ, {}, clear=False)

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == setup_config.RECOMMENDED_EXCLUDE_PATTERNS
    captured = capsys.readouterr()
    assert "Using recommended exclude patterns" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_ci_env_uses_recommended(mocker, capsys):
    config: dict = {}
    mocker.patch("fetchtastic.setup_config.sys.stdin.isatty", return_value=True)
    mocker.patch.dict(os.environ, {"CI": "true"}, clear=False)

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == setup_config.RECOMMENDED_EXCLUDE_PATTERNS
    captured = capsys.readouterr()
    assert "Using recommended exclude patterns" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
@pytest.mark.parametrize(
    ("input_value", "expected"),
    [("hourly", "hourly"), ("daily", "daily"), ("none", "none")],
)
def test_prompt_for_cron_frequency_accepts_full_words(mocker, input_value, expected):
    mocker.patch("builtins.input", return_value=input_value)
    assert setup_config._prompt_for_cron_frequency() == expected
