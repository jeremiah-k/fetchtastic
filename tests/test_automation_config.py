"""
Comprehensive tests for automation configuration (cron jobs, startup scripts).
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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
        mocker.patch("builtins.input", side_effect=["invalid", "x", "h"])

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

    def test_setup_automation_linux_crontab_available(self, mocker):
        """Test _setup_automation on Linux with crontab available."""
        config = {"BASE_DIR": "/tmp/test"}

        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
        mock_check_any = mocker.patch(
            "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False
        )
        mock_configure = mocker.patch("fetchtastic.setup_config._configure_cron_job")
        mock_check_reboot = mocker.patch(
            "fetchtastic.setup_config.check_reboot_cron_job", return_value=False
        )
        mock_setup_reboot = mocker.patch(
            "fetchtastic.setup_config.setup_reboot_cron_job"
        )
        mocker.patch("builtins.input", return_value="y")

        result = setup_config._setup_automation(config, False, lambda x: True)

        assert result == config
        mock_configure.assert_called_once_with(install_crond_needed=False)
        mock_setup_reboot.assert_called_once()

    def test_setup_automation_linux_no_crontab(self, mocker):
        """Test _setup_automation on Linux without crontab."""
        config = {"BASE_DIR": "/tmp/test"}

        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=False)

        result = setup_config._setup_automation(config, False, lambda x: True)

        assert result == config  # Should return unchanged

    def test_setup_automation_termux(self, mocker):
        """Test _setup_automation on Termux."""
        config = {"BASE_DIR": "/tmp/test"}

        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mock_check_cron = mocker.patch(
            "fetchtastic.setup_config.check_cron_job_exists", return_value=False
        )
        mock_check_boot = mocker.patch(
            "fetchtastic.setup_config.check_boot_script_exists", return_value=False
        )
        mock_configure = mocker.patch("fetchtastic.setup_config._configure_cron_job")
        mock_setup_boot = mocker.patch("fetchtastic.setup_config.setup_boot_script")
        mocker.patch("builtins.input", side_effect=["y", "y"])  # Both cron and boot

        result = setup_config._setup_automation(config, False, lambda x: True)

        assert result == config
        mock_configure.assert_called_once_with(install_crond_needed=True)
        mock_setup_boot.assert_called_once()

    def test_setup_automation_windows_with_modules(self, mocker):
        """Test _setup_automation on Windows with modules available."""
        config = {"BASE_DIR": "/tmp/test"}

        mocker.patch("platform.system", return_value="Windows")
        mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
        mock_startup = mocker.patch(
            "fetchtastic.setup_config.winshell.startup", return_value="/path/to/startup"
        )
        mock_check = mocker.patch(
            "os.path.exists", side_effect=[False]
        )  # No existing shortcut
        mock_create = mocker.patch(
            "fetchtastic.setup_config.create_startup_shortcut", return_value=True
        )
        mocker.patch("builtins.input", return_value="y")

        result = setup_config._setup_automation(config, False, lambda x: True)

        assert result == config
        mock_create.assert_called_once()

    def test_setup_automation_existing_cron_reconfigure(self, mocker):
        """Test _setup_automation with existing cron jobs."""
        config = {"BASE_DIR": "/tmp/test"}

        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
        mock_check_any = mocker.patch(
            "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=True
        )
        mock_remove = mocker.patch("fetchtastic.setup_config.remove_cron_job")
        mock_remove_reboot = mocker.patch(
            "fetchtastic.setup_config.remove_reboot_cron_job"
        )
        mock_configure = mocker.patch("fetchtastic.setup_config._configure_cron_job")
        mock_setup_reboot = mocker.patch(
            "fetchtastic.setup_config.setup_reboot_cron_job"
        )
        mocker.patch(
            "builtins.input", side_effect=["y", "n"]
        )  # Reconfigure, but no reboot

        result = setup_config._setup_automation(config, False, lambda x: True)

        assert result == config
        mock_remove.assert_called_once()
        mock_remove_reboot.assert_called_once()
        mock_configure.assert_called_once_with(install_crond_needed=False)
        mock_setup_reboot.assert_not_called()

    def test_setup_notifications_enable_flow(self, mocker):
        """Test _setup_notifications enabling notifications."""
        config = {}

        mocker.patch(
            "builtins.input",
            side_effect=[
                "y",  # Enable notifications
                "custom.server.com",  # Custom server
                "test-topic",  # Custom topic
                "y",  # Copy to clipboard (success)
                "y",  # Notify on download only
            ],
        )
        mocker.patch(
            "fetchtastic.setup_config.copy_to_clipboard_func", return_value=True
        )

        result = setup_config._setup_notifications(config)

        assert result["NTFY_TOPIC"] == "test-topic"
        assert result["NTFY_SERVER"] == "https://custom.server.com"
        assert result["NOTIFY_ON_DOWNLOAD_ONLY"] is True

    def test_setup_notifications_disable_existing(self, mocker):
        """Test _setup_notifications disabling existing notifications."""
        config = {
            "NTFY_TOPIC": "existing-topic",
            "NTFY_SERVER": "https://existing.server.com",
            "NOTIFY_ON_DOWNLOAD_ONLY": True,
        }

        mocker.patch("builtins.input", side_effect=["n", "y"])  # Disable, confirm

        result = setup_config._setup_notifications(config)

        assert result["NTFY_TOPIC"] == ""
        assert result["NTFY_SERVER"] == ""
        assert result["NOTIFY_ON_DOWNLOAD_ONLY"] is False

    def test_setup_notifications_keep_existing(self, mocker):
        """Test _setup_notifications keeping existing notifications."""
        config = {
            "NTFY_TOPIC": "existing-topic",
            "NTFY_SERVER": "https://existing.server.com",
            "NOTIFY_ON_DOWNLOAD_ONLY": False,
        }

        mocker.patch(
            "builtins.input", side_effect=["n", "n"]
        )  # Disable, but don't confirm

        result = setup_config._setup_notifications(config)

        assert result["NTFY_TOPIC"] == "existing-topic"
        assert result["NTFY_SERVER"] == "https://existing.server.com"
        assert result["NOTIFY_ON_DOWNLOAD_ONLY"] is False

    def test_setup_github_token_enable_new(self, mocker):
        """Test _setup_github enabling new token."""
        config = {}

        mocker.patch("builtins.input", side_effect=["y", "ghp_test_token_1234567890"])
        mocker.patch("getpass.getpass", return_value="ghp_test_token_1234567890")

        result = setup_config._setup_github(config)

        assert result["GITHUB_TOKEN"] == "ghp_test_token_1234567890"

    def test_setup_github_token_invalid_format(self, mocker):
        """Test _setup_github with invalid token format."""
        config = {}

        mocker.patch("builtins.input", side_effect=["y", "invalid_token"])
        mocker.patch("getpass.getpass", return_value="invalid_token")

        result = setup_config._setup_github(config)

        assert "GITHUB_TOKEN" not in result

    def test_setup_github_token_change_existing(self, mocker):
        """Test _setup_github changing existing token."""
        config = {"GITHUB_TOKEN": "ghp_old_token_1234567890"}

        mocker.patch("builtins.input", side_effect=["y", "ghp_new_token_1234567890"])
        mocker.patch("getpass.getpass", return_value="ghp_new_token_1234567890")

        result = setup_config._setup_github(config)

        assert result["GITHUB_TOKEN"] == "ghp_new_token_1234567890"

    def test_setup_github_token_keep_existing(self, mocker):
        """Test _setup_github keeping existing token."""
        config = {"GITHUB_TOKEN": "ghp_existing_token_1234567890"}

        mocker.patch("builtins.input", side_effect=["n"])  # Don't change

        result = setup_config._setup_github(config)

        assert result["GITHUB_TOKEN"] == "ghp_existing_token_1234567890"

    def test_prompt_for_setup_sections_full_setup(self, mocker):
        """Test _prompt_for_setup_sections for full setup."""
        mocker.patch("builtins.input", side_effect=["", "all", "everything", "full"])

        for input_value in ["", "all", "everything", "full"]:
            result = setup_config._prompt_for_setup_sections()
            assert result is None  # Indicates full setup

    def test_prompt_for_setup_sections_partial_selection(self, mocker):
        """Test _prompt_for_setup_sections for partial selection."""
        mocker.patch(
            "builtins.input",
            side_effect=[
                "a,f",  # Android and firmware
                "android, notifications",  # Full names
                "b a m",  # Shortcuts with spaces
                "g;n",  # Shortcuts with semicolon
            ],
        )

        # Test various input formats
        test_inputs = ["a,f", "android, notifications", "b a m", "g;n"]
        expected_sets = [
            {"android", "firmware"},
            {"android", "notifications"},
            {"base", "android", "automation"},
            {"github", "notifications"},
        ]

        for input_val, expected in zip(test_inputs, expected_sets):
            mocker.patch("builtins.input", return_value=input_val)
            result = setup_config._prompt_for_setup_sections()
            assert result == expected

    def test_prompt_for_setup_sections_invalid_then_retry(self, mocker):
        """Test _prompt_for_setup_sections with invalid input then retry."""
        mocker.patch("builtins.input", side_effect=["invalid", "x,y,z", "a"])

        result = setup_config._prompt_for_setup_sections()

        assert result == {"android"}
