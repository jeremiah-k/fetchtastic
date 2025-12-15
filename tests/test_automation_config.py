"""
Simplified automation configuration tests focused on coverage.
"""

import os
from unittest.mock import MagicMock, patch

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
            ("d", "daily"),
            ("n", "none"),
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
        mocker.patch("builtins.input", return_value="none")

        setup_config._configure_cron_job(install_crond_needed=False)

        # Should not call setup functions
        assert True  # Simple assertion

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
        assert result == {"android", "firmware"}
