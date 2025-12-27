"""
Tests for ALLOW_ENV_TOKEN configuration option.
"""

import os
from unittest.mock import patch

import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration

pytestmark = [pytest.mark.user_interface, pytest.mark.unit]


@pytest.fixture
def integration():
    """Create a DownloadCLIIntegration instance for testing."""
    return DownloadCLIIntegration()


@pytest.fixture
def mock_config():
    """
    Return a minimal mock configuration for tests.

    Returns:
        dict: Configuration containing "DOWNLOAD_DIR" set to "/tmp/test".
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test",
    }


def test_allow_env_token_true_uses_environment_token(integration, mock_config):
    """Test that ALLOW_ENV_TOKEN=True allows using environment GITHUB_TOKEN."""
    test_env_token = "test_env_token_value"

    with patch.dict(os.environ, {"GITHUB_TOKEN": test_env_token}):
        mock_config["ALLOW_ENV_TOKEN"] = True

        with patch.object(
            integration,
            "run_download",
            return_value=(["fw"], ["new_fw"], ["apk"], ["new_apk"], [], "fw", "apk"),
        ):
            integration.main(mock_config, force_refresh=False)

            # Environment token should be used
            assert mock_config["GITHUB_TOKEN"] == test_env_token


def test_allow_env_token_false_ignores_environment_token(integration, mock_config):
    """
    Verify that the environment GITHUB_TOKEN is ignored when the configuration disallows using environment tokens.

    The test sets both an environment token and a config-level token, runs the integration entry point, and asserts the config token remains in mock_config.
    """
    test_env_token = "test_env_token_value"
    test_config_token = "test_config_token_value"

    with patch.dict(os.environ, {"GITHUB_TOKEN": test_env_token}):
        mock_config["ALLOW_ENV_TOKEN"] = False
        mock_config["GITHUB_TOKEN"] = test_config_token

        with patch.object(
            integration,
            "run_download",
            return_value=(["fw"], ["new_fw"], ["apk"], ["new_apk"], [], "fw", "apk"),
        ):
            integration.main(mock_config, force_refresh=False)

            # Config token should be used, environment token ignored
            assert mock_config["GITHUB_TOKEN"] == test_config_token
            assert mock_config["GITHUB_TOKEN"] != test_env_token


def test_config_token_overrides_environment_token(integration, mock_config):
    """Test that config GITHUB_TOKEN always takes precedence over environment token."""
    test_env_token = "test_env_token_value"
    test_config_token = "test_config_token_value"

    with patch.dict(os.environ, {"GITHUB_TOKEN": test_env_token}):
        mock_config["ALLOW_ENV_TOKEN"] = True
        mock_config["GITHUB_TOKEN"] = test_config_token

        with patch.object(
            integration,
            "run_download",
            return_value=(["fw"], ["new_fw"], ["apk"], ["new_apk"], [], "fw", "apk"),
        ):
            integration.main(mock_config, force_refresh=False)

            # Config token should override environment token
            assert mock_config["GITHUB_TOKEN"] == test_config_token


def test_allow_env_token_missing_defaults_to_true(integration, mock_config):
    """Test that ALLOW_ENV_TOKEN defaults to True when not specified."""
    test_env_token = "test_env_token_value"

    with patch.dict(os.environ, {"GITHUB_TOKEN": test_env_token}):
        # Don't set ALLOW_ENV_TOKEN in config
        with patch.object(
            integration,
            "run_download",
            return_value=(["fw"], ["new_fw"], ["apk"], ["new_apk"], [], "fw", "apk"),
        ):
            integration.main(mock_config, force_refresh=False)

            # Environment token should be used by default
            assert mock_config["GITHUB_TOKEN"] == test_env_token


def test_no_token_when_env_disabled_and_no_config(integration, mock_config):
    """Test that no token is used when ALLOW_ENV_TOKEN=False and no config token."""
    test_env_token = "test_env_token_value"

    with patch.dict(os.environ, {"GITHUB_TOKEN": test_env_token}):
        mock_config["ALLOW_ENV_TOKEN"] = False
        # Don't set GITHUB_TOKEN in config
        with patch.object(
            integration,
            "run_download",
            return_value=(["fw"], ["new_fw"], ["apk"], ["new_apk"], [], "fw", "apk"),
        ):
            integration.main(mock_config, force_refresh=False)

            # No token should be set
            assert "GITHUB_TOKEN" not in mock_config
