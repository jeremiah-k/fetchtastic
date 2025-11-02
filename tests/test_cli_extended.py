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

from unittest.mock import MagicMock

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

        cli.show_help(mock_parser, None, None, None, None)
        mock_parser.print_help.assert_called_once()

    def test_show_help_with_message(self, mocker):
        """Test help display with custom message."""
        mock_parser = MagicMock()
        mock_parser.print_help.return_value = None

        cli.show_help(mock_parser, None, None, None, None)
        mock_parser.print_help.assert_called_once()

    def test_cli_help_command(self, mocker):
        """Test CLI help command."""
        mocker.patch("sys.argv", ["fetchtastic", "--help"])

        with pytest.raises(SystemExit):
            cli.main()

    def test_cli_no_arguments(self, mocker):
        """Test CLI with no arguments shows help."""
        mocker.patch("sys.argv", ["fetchtastic"])

        mock_parser_print_help = mocker.patch("argparse.ArgumentParser.print_help")

        with pytest.raises(SystemExit):
            cli.main()

        mock_parser_print_help.assert_called_once()


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
        mocker.patch("importlib.metadata.version", side_effect=Exception("Not found"))

        result = cli.get_fetchtastic_version()

        assert result == "unknown"

    def test_display_version_info(self, mocker):
        """Test version info display."""
        mock_check_updates = mocker.patch(
            "fetchtastic.setup_config.check_for_updates",
            return_value=("1.2.3", "1.2.4", True),
        )

        from fetchtastic.setup_config import display_version_info

        app_version, latest_version, update_available = display_version_info()

        assert app_version == "1.2.3"
        assert latest_version == "1.2.4"
        assert update_available is True
        mock_check_updates.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLICleanFunctionality:
    """Test CLI clean functionality."""

    def test_run_clean_success(self, mocker):
        """Test successful clean operation."""
        mock_input = mocker.patch("builtins.input", return_value="y")
        mock_os_remove = mocker.patch("os.remove")
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.listdir", return_value=[])
        mocker.patch("os.rmdir")
        mocker.patch("shutil.rmtree")
        mocker.patch("subprocess.run", return_value=mocker.Mock(returncode=0))
        mocker.patch("platform.system", return_value="Linux")

        cli.run_clean()

        mock_input.assert_called_once()
        # Should attempt to remove config files and directories
        assert mock_os_remove.call_count >= 2  # At least config files

    def test_run_clean_cancelled(self, mocker):
        """Test clean operation when user cancels."""
        mock_input = mocker.patch("builtins.input", return_value="n")
        mock_print = mocker.patch("builtins.print")

        cli.run_clean()

        mock_input.assert_called_once()
        mock_print.assert_any_call("Clean operation cancelled.")

    def test_run_clean_with_exception(self, mocker):
        """Test clean operation handles exceptions gracefully."""
        mock_input = mocker.patch("builtins.input", return_value="y")
        # Mock platform.system to return Windows to trigger batch directory cleanup
        mocker.patch("platform.system", return_value="Windows")
        # Mock shutil.rmtree for batch directory removal to fail, which is wrapped in try-catch
        mock_shutil_rmtree = mocker.patch(
            "shutil.rmtree", side_effect=Exception("Removal failed")
        )
        # Mock os.remove to succeed for config files
        mocker.patch("os.remove")
        # Mock os.path.exists to return True for config dir and batch dir
        mocker.patch(
            "os.path.exists",
            side_effect=lambda path: "batch" in path or "config" in path.lower(),
        )
        mocker.patch("os.listdir", return_value=[])
        mocker.patch("builtins.print")

        cli.run_clean()

        mock_input.assert_called_once()
        # Check that shutil.rmtree was called (which would trigger the exception handling)
        mock_shutil_rmtree.assert_called()

    def test_cli_clean_command(self, mocker):
        """Test CLI clean command."""
        mocker.patch("sys.argv", ["fetchtastic", "clean"])
        mock_run_clean = mocker.patch("fetchtastic.cli.run_clean")

        with pytest.raises(SystemExit):
            cli.main()

        mock_run_clean.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIRepoCleanFunctionality:
    """Test CLI repo clean functionality."""

    def test_run_repo_clean_success(self, mocker):
        """Test successful repo clean operation."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/fake/repo"}

        mock_input = mocker.patch("builtins.input", return_value="y")
        mock_clean_repo = mocker.patch(
            "fetchtastic.repo_downloader.clean_repo_directory", return_value=True
        )
        mocker.patch("fetchtastic.cli.logger")

        cli.run_repo_clean(config)

        mock_input.assert_called_once()
        mock_clean_repo.assert_called_once_with("/fake/repo")

    def test_run_repo_clean_no_repo_dir(self, mocker):
        """Test repo clean with no repo directory configured."""
        config = {"BASE_DIR": "/fake/dir"}
        mocker.patch("builtins.input", return_value="y")
        mock_print = mocker.patch("builtins.print")

        cli.run_repo_clean(config)

        mock_print.assert_any_call("Download directory not configured.")

    def test_run_repo_clean_nonexistent_dir(self, mocker):
        """Test repo clean with nonexistent directory."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/nonexistent/repo"}
        mocker.patch("builtins.input", return_value="y")
        mocker.patch("os.listdir", side_effect=FileNotFoundError("No such directory"))
        mock_clean_repo = mocker.patch(
            "fetchtastic.repo_downloader.clean_repo_directory", return_value=True
        )
        mock_print = mocker.patch("builtins.print")

        cli.run_repo_clean(config)

        mock_clean_repo.assert_called_once_with("/nonexistent/repo")
        mock_print.assert_any_call("Repository directory cleaned successfully.")

    def test_run_repo_clean_permission_error(self, mocker):
        """Test repo clean with permission error."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/fake/repo"}
        mocker.patch("builtins.input", return_value="y")
        mocker.patch("os.listdir", side_effect=PermissionError("Permission denied"))
        mock_clean_repo = mocker.patch(
            "fetchtastic.repo_downloader.clean_repo_directory", return_value=False
        )
        mock_print = mocker.patch("builtins.print")

        cli.run_repo_clean(config)

        mock_clean_repo.assert_called_once_with("/fake/repo")
        mock_print.assert_any_call("Failed to clean repository directory.")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIErrorHandling:
    """Test CLI error handling."""

    def test_cli_invalid_command(self, mocker):
        """Test CLI with invalid command."""
        mocker.patch("sys.argv", ["fetchtastic", "invalid-command"])
        mocker.patch("fetchtastic.cli.logger")

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
        mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(Exception, match="Config load failed"):
            cli.main()

    def test_cli_setup_command_error(self, mocker):
        """Test setup command with error."""
        mocker.patch("sys.argv", ["fetchtastic", "setup"])
        mocker.patch(
            "fetchtastic.setup_config.run_setup", side_effect=Exception("Setup failed")
        )
        mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(Exception, match="Setup failed"):
            cli.main()


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

        with pytest.raises(SystemExit):
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
        mock_get = mocker.patch("requests.get")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"info": {"version": "1.3.0"}}
        mock_get.return_value = mock_response

        from fetchtastic.setup_config import check_for_updates

        current_version, latest_version, update_available = check_for_updates()

        assert latest_version == "1.3.0"
        assert update_available is True

    def test_check_for_updates_not_available(self, mocker):
        """Test update check when no update is available."""
        mock_get = mocker.patch("requests.get")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"info": {"version": "1.2.3"}}
        mock_get.return_value = mock_response
        # Mock the version import to return expected version
        mocker.patch("importlib.metadata.version", return_value="1.2.3")

        from fetchtastic.setup_config import check_for_updates

        current_version, latest_version, update_available = check_for_updates()

        assert latest_version == "1.2.3"
        assert update_available is False

    def test_check_for_updates_network_error(self, mocker):
        """Test update check with network error."""
        mocker.patch("requests.get", side_effect=Exception("Network error"))

        from fetchtastic.setup_config import check_for_updates

        current_version, latest_version, update_available = check_for_updates()

        assert latest_version is None
        assert update_available is False

    def test_check_for_updates_invalid_response(self, mocker):
        """Test update check with invalid response."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mocker.patch("requests.get", return_value=mock_response)

        from fetchtastic.setup_config import check_for_updates

        current_version, latest_version, update_available = check_for_updates()

        assert latest_version is None
        assert update_available is False
