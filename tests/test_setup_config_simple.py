# tests/test_setup_config_simple.py

"""
Simple, accurate tests for setup_config module functions.
Tests are based on actual function behavior, not assumptions.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic import setup_config


class TestTermuxDetection:
    """Test Termux detection functions."""

    def test_is_termux_true(self, mocker):
        """Test Termux detection when true."""
        mocker.patch("os.environ", {"PREFIX": "/data/data/com.termux/files/usr"})

        result = setup_config.is_termux()

        assert result is True

    def test_is_termux_false(self, mocker):
        """Test Termux detection when false."""
        mocker.patch("os.environ", {})

        result = setup_config.is_termux()

        assert result is False

    def test_is_termux_false_with_different_prefix(self, mocker):
        """Test Termux detection when PREFIX exists but doesn't contain com.termux."""
        mocker.patch("os.environ", {"PREFIX": "/usr/local"})

        result = setup_config.is_termux()

        assert result is False


class TestPlatformDetection:
    """Test platform detection functions."""

    def test_get_platform_linux(self, mocker):
        """Test platform detection for Linux."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("platform.system", return_value="Linux")

        result = setup_config.get_platform()

        assert result == "linux"

    def test_get_platform_mac(self, mocker):
        """Test platform detection for macOS (returns 'mac', not 'macos')."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("platform.system", return_value="Darwin")

        result = setup_config.get_platform()

        assert result == "mac"  # Note: function returns "mac", not "macos"

    def test_get_platform_termux(self, mocker):
        """Test platform detection for Termux."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)

        result = setup_config.get_platform()

        assert result == "termux"

    def test_get_platform_unknown(self, mocker):
        """Test platform detection for unknown platform."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("platform.system", return_value="Unknown")

        result = setup_config.get_platform()

        assert result == "unknown"


class TestInstallationDetection:
    """Test installation detection functions."""

    def test_is_fetchtastic_installed_via_pip_success(self, mocker):
        """Test pip installation detection when installed via pip."""
        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = "fetchtastic 1.0.0\nother-package 2.0.0"
        mocker.patch("subprocess.run", return_value=mock_run)

        result = setup_config.is_fetchtastic_installed_via_pip()

        assert result is True

    def test_is_fetchtastic_installed_via_pip_not_found(self, mocker):
        """Test pip installation detection when not installed."""
        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = "other-package 2.0.0\nanother-package 1.0.0"
        mocker.patch("subprocess.run", return_value=mock_run)

        result = setup_config.is_fetchtastic_installed_via_pip()

        assert result is False

    def test_is_fetchtastic_installed_via_pip_command_fails(self, mocker):
        """Test pip installation detection when pip command fails."""
        mock_run = MagicMock()
        mock_run.returncode = 1
        mocker.patch("subprocess.run", return_value=mock_run)

        result = setup_config.is_fetchtastic_installed_via_pip()

        assert result is False

    def test_is_fetchtastic_installed_via_pip_subprocess_error(self, mocker):
        """Test pip installation detection when subprocess error occurs."""
        mocker.patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "pip")
        )

        result = setup_config.is_fetchtastic_installed_via_pip()

        assert result is False

    def test_is_fetchtastic_installed_via_pipx_success(self, mocker):
        """Test pipx installation detection when installed via pipx."""
        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = "package fetchtastic 1.0.0\nother-package 2.0.0"
        mocker.patch("subprocess.run", return_value=mock_run)

        result = setup_config.is_fetchtastic_installed_via_pipx()

        assert result is True

    def test_is_fetchtastic_installed_via_pipx_not_found(self, mocker):
        """Test pipx installation detection when not installed."""
        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = "other-package 2.0.0\nanother-package 1.0.0"
        mocker.patch("subprocess.run", return_value=mock_run)

        result = setup_config.is_fetchtastic_installed_via_pipx()

        assert result is False


class TestConfigurationLoading:
    """Test configuration loading functions."""

    def test_config_exists_new_location(self, mocker):
        """Test config existence check when found in new location."""
        mocker.patch(
            "os.path.exists", side_effect=lambda path: path == setup_config.CONFIG_FILE
        )

        result = setup_config.config_exists()

        assert result == (True, setup_config.CONFIG_FILE)

    def test_config_exists_old_location(self, mocker):
        """Test config existence check when found in old location."""
        mocker.patch(
            "os.path.exists",
            side_effect=lambda path: path == setup_config.OLD_CONFIG_FILE,
        )

        result = setup_config.config_exists()

        assert result == (True, setup_config.OLD_CONFIG_FILE)

    def test_config_exists_not_found(self, mocker):
        """Test config existence check when not found."""
        mocker.patch("os.path.exists", return_value=False)

        result = setup_config.config_exists()

        assert result == (False, None)

    def test_config_exists_custom_directory_found(self, mocker):
        """Test config existence check with custom directory when found."""
        custom_dir = "/custom/dir"
        expected_path = os.path.join(custom_dir, setup_config.CONFIG_FILE_NAME)
        mocker.patch("os.path.exists", side_effect=lambda path: path == expected_path)

        result = setup_config.config_exists(custom_dir)

        assert result == (True, expected_path)

    def test_config_exists_custom_directory_not_found(self, mocker):
        """Test config existence check with custom directory when not found."""
        custom_dir = "/custom/dir"
        mocker.patch("os.path.exists", return_value=False)

        result = setup_config.config_exists(custom_dir)

        assert result == (False, None)

    def test_load_config_success_new_location(self, tmp_path, mocker):
        """Test successful configuration loading from new location."""
        config_data = {"BASE_DIR": "/test/dir", "NTFY_TOPIC": "test-topic"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text("BASE_DIR: /test/dir\nNTFY_TOPIC: test-topic")

        with patch("fetchtastic.setup_config.CONFIG_FILE", str(config_file)):
            with patch(
                "fetchtastic.setup_config.OLD_CONFIG_FILE", "/nonexistent/old.yaml"
            ):
                result = setup_config.load_config()

                assert result == config_data

    def test_load_config_success_old_location(self, tmp_path, mocker):
        """Test successful configuration loading from old location."""
        config_data = {"BASE_DIR": "/test/dir", "NTFY_TOPIC": "test-topic"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text("BASE_DIR: /test/dir\nNTFY_TOPIC: test-topic")

        with patch("fetchtastic.setup_config.CONFIG_FILE", "/nonexistent/new.yaml"):
            with patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(config_file)):
                result = setup_config.load_config()

                assert result == config_data

    def test_load_config_file_not_found(self, mocker):
        """Test configuration loading when file doesn't exist."""
        mocker.patch("os.path.exists", return_value=False)

        result = setup_config.load_config()

        assert result is None

    def test_load_config_invalid_yaml(self, tmp_path, mocker):
        """Test configuration loading with invalid YAML."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content: [")

        with patch("fetchtastic.setup_config.CONFIG_FILE", str(config_file)):
            with patch(
                "fetchtastic.setup_config.OLD_CONFIG_FILE", "/nonexistent/old.yaml"
            ):
                # Should raise an exception for invalid YAML
                with pytest.raises(Exception):
                    setup_config.load_config()


class TestDownloadsDirectory:
    """Test downloads directory functions."""

    def test_get_downloads_dir_termux_exists(self, mocker):
        """Test getting downloads directory on Termux when storage/downloads exists."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "os.path.exists", side_effect=lambda path: "storage/downloads" in path
        )
        mocker.patch(
            "os.path.expanduser",
            side_effect=lambda path: path.replace("~", "/home/user"),
        )

        result = setup_config.get_downloads_dir()

        assert result == "/home/user/storage/downloads"

    def test_get_downloads_dir_standard_exists(self, mocker):
        """Test getting downloads directory when standard Downloads exists."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

        def mock_exists(path):
            # Return True only for ~/Downloads, False for everything else
            return path == "/home/user/Downloads"

        mocker.patch("os.path.exists", side_effect=mock_exists)
        mocker.patch(
            "os.path.expanduser",
            side_effect=lambda path: path.replace("~", "/home/user"),
        )

        result = setup_config.get_downloads_dir()

        assert result == "/home/user/Downloads"

    def test_get_downloads_dir_fallback_to_home(self, mocker):
        """Test getting downloads directory fallback to home."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("os.path.exists", return_value=False)
        mocker.patch(
            "os.path.expanduser",
            side_effect=lambda path: path.replace("~", "/home/user"),
        )

        result = setup_config.get_downloads_dir()

        assert result == "/home/user"
