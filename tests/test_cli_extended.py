"""
Additional CLI functionality tests for fetchtastic CLI module.

This module contains tests for CLI functions that need better coverage.

Tests include:
- Help command functionality
- Clean command functionality
- Version command functionality
- Error handling and edge cases
- Platform-specific behavior
"""

import os
import platform
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic import cli


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIHelpFunctionality:
    """Test CLI help functionality."""

    def test_show_help_basic(self, mocker):
        """Test basic help display."""
        mock_parser = MagicMock()
        mock_parser.print_help.return_value = None

        with patch("fetchtastic.cli.ArgumentParser", return_value=mock_parser):
            cli.show_help()
            mock_parser.print_help.assert_called_once()

    def test_show_help_with_message(self, mocker):
        """Test help display with custom message."""
        mock_parser = MagicMock()
        mock_parser.print_help.return_value = None

        with patch("fetchtastic.cli.ArgumentParser", return_value=mock_parser):
            cli.show_help("Custom message")
            mock_parser.print_help.assert_called_once()

    def test_cli_help_command(self, mocker):
        """Test CLI help command."""
        mocker.patch("sys.argv", ["fetchtastic", "--help"])

        mock_show_help = mocker.patch("fetchtastic.cli.show_help")

        with pytest.raises(SystemExit):
            cli.main()

        mock_show_help.assert_called_once()

    def test_cli_no_arguments(self, mocker):
        """Test CLI with no arguments shows help."""
        mocker.patch("sys.argv", ["fetchtastic"])

        mock_show_help = mocker.patch("fetchtastic.cli.show_help")

        with pytest.raises(SystemExit):
            cli.main()

        mock_show_help.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIVersionFunctionality:
    """Test CLI version functionality."""

    def test_get_fetchtastic_version_success(self, mocker):
        """Test successful version retrieval."""
        mock_version = mocker.patch("importlib.metadata.version", return_value="1.2.3")

        result = cli.get_fetchtastic_version()

        assert result == "1.2.3"
        mock_version.assert_called_once_with("fetchtastic")

    def test_get_fetchtastic_version_not_found(self, mocker):
        """Test version retrieval when package not found."""
        mock_version = mocker.patch(
            "importlib.metadata.version", side_effect=Exception("Not found")
        )

        result = cli.get_fetchtastic_version()

        assert result == "unknown"

    def test_display_version_info(self, mocker):
        """Test version info display."""
        mock_get_version = mocker.patch(
            "fetchtastic.cli.get_fetchtastic_version", return_value="1.2.3"
        )
        mock_check_updates = mocker.patch(
            "fetchtastic.cli.check_for_updates", return_value=("1.2.4", False)
        )

        app_version, latest_version, update_available = cli.display_version_info()

        assert app_version == "1.2.3"
        assert latest_version == "1.2.4"
        assert update_available is True
        mock_get_version.assert_called_once()
        mock_check_updates.assert_called_once_with("1.2.3")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLICleanFunctionality:
    """Test CLI clean functionality."""

    def test_run_clean_success(self, mocker):
        """Test successful clean operation."""
        mock_config_exists = mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config",
            return_value={"BASE_DIR": "/fake/dir"},
        )
        mock_cleanup = mocker.patch("fetchtastic.downloader.cleanup_old_versions")
        mock_clear_cache = mocker.patch("fetchtastic.downloader.clear_all_caches")
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_clean()

        mock_cleanup.assert_called_once()
        mock_clear_cache.assert_called_once()
        mock_logger.info.assert_any_call("Cleanup completed successfully!")

    def test_run_clean_no_config(self, mocker):
        """Test clean operation with no config."""
        mock_config_exists = mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(False, None)
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_clean()

        mock_logger.error.assert_called_with(
            "No configuration found. Run 'fetchtastic setup' first."
        )

    def test_run_clean_cleanup_failure(self, mocker):
        """Test clean operation with cleanup failure."""
        mock_config_exists = mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config",
            return_value={"BASE_DIR": "/fake/dir"},
        )
        mock_cleanup = mocker.patch(
            "fetchtastic.downloader.cleanup_old_versions",
            side_effect=Exception("Cleanup failed"),
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_clean()

        mock_logger.error.assert_called_with("Cleanup failed: Cleanup failed")

    def test_cli_clean_command(self, mocker):
        """Test CLI clean command."""
        mocker.patch("sys.argv", ["fetchtastic", "clean"])
        mock_run_clean = mocker.patch("fetchtastic.cli.run_clean")

        cli.main()

        mock_run_clean.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIRepoCleanFunctionality:
    """Test CLI repo clean functionality."""

    def test_run_repo_clean_success(self, mocker):
        """Test successful repo clean operation."""
        config = {"BASE_DIR": "/fake/dir", "REPO_DIR": "/fake/repo"}

        mock_remove = mocker.patch("os.remove")
        mock_rmtree = mocker.patch("shutil.rmtree")
        mock_listdir = mocker.patch(
            "os.listdir", return_value=["file1.json", "file2.json", "subdir"]
        )
        mock_isfile = mocker.patch("os.path.isfile", side_effect=[True, True, False])
        mock_isdir = mocker.patch("os.path.isdir", side_effect=[False, False, True])
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_repo_clean(config)

        # Should remove files and directory
        assert mock_remove.call_count == 2
        mock_rmtree.assert_called_once()

    def test_run_repo_clean_no_repo_dir(self, mocker):
        """Test repo clean with no repo directory configured."""
        config = {"BASE_DIR": "/fake/dir"}
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_repo_clean(config)

        mock_logger.info.assert_called_with("No repository directory configured.")

    def test_run_repo_clean_nonexistent_dir(self, mocker):
        """Test repo clean with nonexistent directory."""
        config = {"BASE_DIR": "/fake/dir", "REPO_DIR": "/nonexistent/repo"}
        mock_listdir = mocker.patch(
            "os.listdir", side_effect=FileNotFoundError("No such directory")
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_repo_clean(config)

        mock_logger.info.assert_called_with("Repository directory does not exist.")

    def test_run_repo_clean_permission_error(self, mocker):
        """Test repo clean with permission error."""
        config = {"BASE_DIR": "/fake/dir", "REPO_DIR": "/fake/repo"}
        mock_listdir = mocker.patch(
            "os.listdir", side_effect=PermissionError("Permission denied")
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.run_repo_clean(config)

        mock_logger.error.assert_called_with(
            "Permission denied accessing repository directory."
        )


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIErrorHandling:
    """Test CLI error handling."""

    def test_cli_invalid_command(self, mocker):
        """Test CLI with invalid command."""
        mocker.patch("sys.argv", ["fetchtastic", "invalid-command"])
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        # Should log error about invalid command

    def test_cli_download_command_config_load_error(self, mocker):
        """Test download command with config load error."""
        mocker.patch("sys.argv", ["fetchtastic", "download"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mocker.patch(
            "fetchtastic.setup_config.load_config",
            side_effect=Exception("Config load failed"),
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_logger.error.assert_called_with(
            "Failed to load configuration: Config load failed"
        )

    def test_cli_setup_command_error(self, mocker):
        """Test setup command with error."""
        mocker.patch("sys.argv", ["fetchtastic", "setup"])
        mocker.patch(
            "fetchtastic.setup_config.run_setup", side_effect=Exception("Setup failed")
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_logger.error.assert_called_with("Setup failed: Setup failed")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIPlatformSpecific:
    """Test CLI platform-specific behavior."""

    def test_cli_windows_integration_flag_windows(self, mocker):
        """Test Windows integration flag on Windows platform."""
        mocker.patch("platform.system", return_value="Windows")
        mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
        mocker.patch(
            "fetchtastic.setup_config.load_config",
            return_value={"BASE_DIR": "/fake/dir"},
        )
        mocker.patch(
            "fetchtastic.setup_config.create_windows_menu_shortcuts", return_value=True
        )
        mocker.patch(
            "fetchtastic.cli.display_version_info",
            return_value=("1.0.0", "1.0.0", False),
        )
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/fake/config.yaml")
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        cli.main()

        mock_logger.info.assert_any_call("Windows integrations updated successfully!")

    def test_cli_windows_integration_flag_linux(self, mocker):
        """Test Windows integration flag on Linux platform should fail."""
        mocker.patch("platform.system", return_value="Linux")
        mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
        mocker.patch(
            "fetchtastic.cli.display_version_info",
            return_value=("1.0.0", "1.0.0", False),
        )

        with pytest.raises(SystemExit):
            cli.main()

    def test_cli_windows_integration_flag_macos(self, mocker):
        """Test Windows integration flag on macOS platform should fail."""
        mocker.patch("platform.system", return_value="Darwin")
        mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
        mocker.patch(
            "fetchtastic.cli.display_version_info",
            return_value=("1.0.0", "1.0.0", False),
        )

        with pytest.raises(SystemExit):
            cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIVersionChecking:
    """Test CLI version checking functionality."""

    def test_check_for_updates_available(self, mocker):
        """Test update check when update is available."""
        mock_session = mocker.patch("fetchtastic.cli.requests.Session")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"info": {"version": "1.3.0"}}
        mock_session.return_value.__enter__.return_value.get.return_value = (
            mock_response
        )

        latest_version, update_available = cli.check_for_updates("1.2.3")

        assert latest_version == "1.3.0"
        assert update_available is True

    def test_check_for_updates_not_available(self, mocker):
        """Test update check when no update is available."""
        mock_session = mocker.patch("fetchtastic.cli.requests.Session")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"info": {"version": "1.2.3"}}
        mock_session.return_value.__enter__.return_value.get.return_value = (
            mock_response
        )

        latest_version, update_available = cli.check_for_updates("1.2.3")

        assert latest_version == "1.2.3"
        assert update_available is False

    def test_check_for_updates_network_error(self, mocker):
        """Test update check with network error."""
        mock_session = mocker.patch("fetchtastic.cli.requests.Session")
        mock_session.return_value.__enter__.return_value.get.side_effect = Exception(
            "Network error"
        )

        latest_version, update_available = cli.check_for_updates("1.2.3")

        assert latest_version == "1.2.3"
        assert update_available is False

    def test_check_for_updates_invalid_response(self, mocker):
        """Test update check with invalid response."""
        mock_session = mocker.patch("fetchtastic.cli.requests.Session")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.return_value.__enter__.return_value.get.return_value = (
            mock_response
        )

        latest_version, update_available = cli.check_for_updates("1.2.3")

        assert latest_version == "1.2.3"
        assert update_available is False
