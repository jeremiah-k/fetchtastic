"""
Simplified automation configuration tests focused on coverage.
"""

import pytest

import fetchtastic.setup_config as setup_config


@pytest.mark.configuration
@pytest.mark.unit
class TestAutomationConfiguration:
    """Test automation configuration functionality."""

    def test_prompt_for_cron_frequency_valid_choices(self, mocker):
        """Test _prompt_for_cron_frequency with valid choices."""
        test_cases = [
            ("h", "hourly"),
            ("hourly", "hourly"),
            ("d", "daily"),
            ("daily", "daily"),
            ("n", "none"),
            ("none", "none"),
            ("", "hourly"),  # default
        ]

        for input_value, expected in test_cases:
            mocker.patch("builtins.input", return_value=input_value)
            result = setup_config._prompt_for_cron_frequency()
            assert result == expected

    def test_prompt_for_cron_frequency_invalid_choice(self, mocker):
        """Test _prompt_for_cron_frequency with invalid choice."""
        mocker.patch("builtins.input", side_effect=["invalid", "h"])

        result = setup_config._prompt_for_cron_frequency()
        assert result == "hourly"

    def test_configure_cron_job_with_frequency(self, mocker):
        """Test _configure_cron_job with valid frequency."""
        mock_setup = mocker.patch("fetchtastic.setup_config.setup_cron_job")
        mock_install = mocker.patch("fetchtastic.setup_config.install_crond")
        mocker.patch("builtins.input", return_value="daily")

        setup_config._configure_cron_job(install_crond_needed=True)

        mock_install.assert_called_once()
        mock_setup.assert_called_once_with("daily")

    def test_configure_cron_job_no_frequency(self, mocker):
        """Test _configure_cron_job when user selects none."""
        mock_setup = mocker.patch("fetchtastic.setup_config.setup_cron_job")
        mock_install = mocker.patch("fetchtastic.setup_config.install_crond")
        mocker.patch("builtins.input", return_value="none")

        setup_config._configure_cron_job(install_crond_needed=True)

        mock_install.assert_not_called()
        mock_setup.assert_not_called()

    def test_configure_cron_job_stops_when_termux_service_setup_fails(self, mocker):
        """Do not install a schedule when the Termux scheduler could not start."""
        mock_setup = mocker.patch("fetchtastic.setup_config.setup_cron_job")
        mocker.patch("fetchtastic.setup_config.install_crond", return_value=False)
        mocker.patch("builtins.input", return_value="hourly")

        setup_config._configure_cron_job(install_crond_needed=True)

        mock_setup.assert_not_called()

    def test_setup_boot_script_starts_services_and_uses_absolute_executable(
        self, mocker, tmp_path
    ):
        """Termux boot restarts services and invokes the resolved pipx shim."""
        boot_dir = tmp_path / ".termux" / "boot"
        executable = "/data/data/com.termux/files/home/.local/bin/fetchtastic"
        mocker.patch(
            "fetchtastic.setup_config.os.path.expanduser",
            return_value=str(boot_dir),
        )
        mocker.patch(
            "fetchtastic.setup_config._resolve_fetchtastic_executable",
            return_value=executable,
        )
        mocker.patch.dict(
            "fetchtastic.setup_config.os.environ",
            {"PREFIX": "/data/data/com.termux/files/usr"},
            clear=False,
        )

        setup_config.setup_boot_script()

        content = (boot_dir / "fetchtastic.sh").read_text(encoding="utf-8")
        assert (
            ". /data/data/com.termux/files/usr/etc/profile.d/start-services.sh"
            in content
        )
        assert f"{executable} download" in content

    def test_resolve_fetchtastic_executable_falls_back_to_pipx_bin(
        self, mocker, tmp_path
    ):
        """Automation resolves the default pipx shim even outside shell PATH."""
        pipx_bin = tmp_path / "pipx-bin"
        pipx_bin.mkdir()
        executable = pipx_bin / "fetchtastic"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)

        mocker.patch("fetchtastic.setup_config.shutil.which", return_value=None)
        mocker.patch.dict(
            "fetchtastic.setup_config.os.environ",
            {"PIPX_BIN_DIR": str(pipx_bin)},
            clear=False,
        )

        assert setup_config._resolve_fetchtastic_executable() == str(executable)

    def test_crontab_available_false_when_missing(self, mocker, capsys):
        mocker.patch("fetchtastic.setup_config.shutil.which", return_value=None)
        assert setup_config._crontab_available() is False
        # Function is now pure and doesn't print
        assert capsys.readouterr().out == ""

    def test_setup_cron_job_reports_crontab_install_failure(self, mocker):
        """A rejected crontab update must not be reported as successfully added."""
        mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.shutil.which", return_value="/usr/bin/crontab"
        )
        mocker.patch(
            "fetchtastic.setup_config._resolve_fetchtastic_executable",
            return_value="/home/user/.local/bin/fetchtastic",
        )
        mocker.patch(
            "fetchtastic.setup_config.subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout=""),
        )
        process = mocker.MagicMock(returncode=1)
        mocker.patch("fetchtastic.setup_config.subprocess.Popen", return_value=process)
        mock_error = mocker.patch("fetchtastic.setup_config.logger.error")

        setup_config.setup_cron_job("hourly")

        mock_error.assert_called_once_with(
            "Failed to install cron job: crontab exited with status %s", 1
        )

    def test_setup_cron_job_returns_early_without_crontab(self, mocker):
        mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
        mock_run = mocker.patch("fetchtastic.setup_config.subprocess.run")
        setup_config.setup_cron_job("hourly")
        mock_run.assert_not_called()

    def test_check_cron_job_exists_returns_false_without_crontab(self, mocker):
        mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
        mock_run = mocker.patch("fetchtastic.setup_config.subprocess.run")
        assert setup_config.check_cron_job_exists() is False
        mock_run.assert_not_called()

    def test_check_any_cron_jobs_exist_returns_false_without_crontab(self, mocker):
        mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
        mock_run = mocker.patch("fetchtastic.setup_config.subprocess.run")
        assert setup_config.check_any_cron_jobs_exist() is False
        mock_run.assert_not_called()

    def test_setup_automation_skips_cron_when_crontab_missing(self, mocker):
        mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)
        mocker.patch(
            "fetchtastic.setup_config.check_any_cron_jobs_exist",
            side_effect=AssertionError("Should not query cron without crontab"),
        )

        config = {}

        def wants(_section: str) -> bool:
            """
            Indicates that every setup section should be executed.

            Parameters:
                _section (str): Name of the setup section (ignored).

            Returns:
                True if the section should run, False otherwise.
            """
            return True

        assert (
            setup_config._setup_automation(config, is_partial_run=False, wants=wants)
            == {}
        )

    def test_setup_notifications_enable_new(self, mocker):
        """Test _setup_notifications enabling notifications."""
        config = {}

        # Mock simple input sequence to avoid infinite loops
        mocker.patch(
            "builtins.input",
            side_effect=["y", "custom.server.com", "test-topic", "n", "y"],
        )
        mocker.patch(
            "fetchtastic.setup_config.copy_to_clipboard_func", return_value=True
        )

        result = setup_config._setup_notifications(config)

        assert result["NTFY_TOPIC"] == "test-topic"
        assert result["NTFY_SERVER"] == "https://custom.server.com"

    def test_setup_notifications_disable(self, mocker):
        """Test _setup_notifications disabling notifications."""
        config = {
            "NTFY_TOPIC": "existing-topic",
            "NTFY_SERVER": "https://existing.server.com",
            "NOTIFY_ON_DOWNLOAD_ONLY": True,
        }

        mocker.patch("builtins.input", side_effect=["n", "y"])

        result = setup_config._setup_notifications(config)

        assert result["NTFY_TOPIC"] == ""
        assert result["NTFY_SERVER"] == ""

    def test_setup_github_token_valid(self, mocker):
        """Test _setup_github with valid token."""
        config = {}

        mocker.patch("builtins.input", side_effect=["y", "ghp_test_token_1234567890"])
        mocker.patch("getpass.getpass", return_value="ghp_test_token_1234567890")

        result = setup_config._setup_github(config)

        assert result["GITHUB_TOKEN"] == "ghp_test_token_1234567890"

    def test_setup_github_token_invalid(self, mocker):
        """Test _setup_github with invalid token format."""
        config = {}

        mocker.patch("builtins.input", side_effect=["y", "invalid_token"])
        mocker.patch("getpass.getpass", return_value="invalid_token")

        result = setup_config._setup_github(config)

        assert "GITHUB_TOKEN" not in result

    def test_setup_github_keep_existing(self, mocker):
        """Test _setup_github keeping existing token."""
        config = {"GITHUB_TOKEN": "ghp_existing_token_1234567890"}

        mocker.patch("builtins.input", return_value="n")  # Don't change

        result = setup_config._setup_github(config)

        assert result["GITHUB_TOKEN"] == "ghp_existing_token_1234567890"

    def test_prompt_for_setup_sections_full_setup(self, mocker):
        """Test _prompt_for_setup_sections for full setup."""
        mocker.patch("builtins.input", return_value="")

        result = setup_config._prompt_for_setup_sections()
        assert result is None  # Indicates full setup

    def test_prompt_for_setup_sections_partial(self, mocker):
        """Test _prompt_for_setup_sections for partial selection."""
        mocker.patch("builtins.input", return_value="a,f")

        result = setup_config._prompt_for_setup_sections()
        assert result == {"app", "firmware"}
