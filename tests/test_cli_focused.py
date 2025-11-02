"""
Focused CLI functionality tests for fetchtastic CLI module.

This module contains tests for CLI functions that need better coverage
and are actually testable based on the real CLI implementation.

Tests include:
- Version functionality
- Error handling
- Basic command structure
"""

from unittest.mock import MagicMock, patch

import pytest

from fetchtastic import cli


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

    def test_get_fetchtastic_version_import_error(self, mocker):
        """Test version retrieval when importlib.metadata.version fails."""
        mock_version = mocker.patch(
            "importlib.metadata.version", side_effect=Exception("Not found")
        )

        result = cli.get_fetchtastic_version()

        assert result == "unknown"

    def test_get_fetchtastic_version_fallback_importlib_metadata(self, mocker):
        """Test version retrieval fallback to importlib_metadata."""
        # Mock ImportError for the main import
        mocker.patch("importlib.metadata.version", side_effect=ImportError("No module"))
        mock_fallback_version = mocker.patch(
            "importlib_metadata.version", return_value="1.2.3"
        )

        result = cli.get_fetchtastic_version()

        assert result == "1.2.3"
        mock_fallback_version.assert_called_once_with("fetchtastic")

    def test_get_fetchtastic_version_both_fail(self, mocker):
        """Test version retrieval when both imports fail."""
        mocker.patch("importlib.metadata.version", side_effect=ImportError("No module"))
        mocker.patch("importlib_metadata.version", side_effect=Exception("Not found"))

        result = cli.get_fetchtastic_version()

        assert result == "unknown"


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIErrorHandling:
    """Test CLI error handling."""

    def test_cli_version_command(self, mocker):
        """Test CLI version command."""
        mocker.patch("sys.argv", ["fetchtastic", "version"])
        mock_get_version = mocker.patch(
            "fetchtastic.cli.get_fetchtastic_version", return_value="1.2.3"
        )
        mock_print = mocker.patch("builtins.print")

        with pytest.raises(SystemExit):
            cli.main()

        mock_get_version.assert_called_once()
        # Should print version information
        mock_print.assert_called()

    def test_cli_invalid_command_shows_help(self, mocker):
        """Test that invalid command shows help and exits."""
        mocker.patch("sys.argv", ["fetchtastic", "invalid-command"])
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        # Should log error about invalid command

    def test_cli_help_command(self, mocker):
        """Test CLI help command."""
        mocker.patch("sys.argv", ["fetchtastic", "help"])

        with pytest.raises(SystemExit):
            cli.main()
        # Help should be displayed (handled by argparse)

    def test_cli_hyphen_help_command(self, mocker):
        """Test CLI -h command."""
        mocker.patch("sys.argv", ["fetchtastic", "-h"])

        with pytest.raises(SystemExit):
            cli.main()
        # Help should be displayed (handled by argparse)


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLICleanFunctionality:
    """Test CLI clean functionality."""

    def test_run_clean_no_config(self, mocker):
        """Test clean operation with no config."""
        mock_config_exists = mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(False, None)
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with patch("builtins.input", return_value="n"):  # Mock user input to 'no'
            cli.run_clean()

        mock_logger.error.assert_called_with(
            "No configuration found. Run 'fetchtastic setup' first."
        )

    def test_run_clean_user_cancels(self, mocker):
        """Test clean operation when user cancels."""
        mock_config_exists = mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config",
            return_value={"BASE_DIR": "/fake/dir"},
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with patch("builtins.input", return_value="n"):  # User says no
            with patch("builtins.print") as mock_print:
                cli.run_clean()
                mock_print.assert_any_call("Clean operation cancelled.")

    def test_run_clean_with_confirmation(self, mocker):
        """Test clean operation with user confirmation."""
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

        with patch("builtins.input", return_value="y"):  # User says yes
            cli.run_clean()

        mock_cleanup.assert_called_once()
        mock_clear_cache.assert_called_once()
        mock_logger.info.assert_any_call("Cleanup completed successfully!")

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

    def test_run_repo_clean_no_download_dir(self, mocker):
        """Test repo clean with no download directory configured."""
        config = {"BASE_DIR": "/fake/dir"}  # No DOWNLOAD_DIR key

        with patch("builtins.input", return_value="n"):  # User says no
            with patch("builtins.print") as mock_print:
                cli.run_repo_clean(config)
                mock_print.assert_any_call("Download directory not configured.")

    def test_run_repo_clean_user_cancels(self, mocker):
        """Test repo clean when user cancels."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/fake/download"}

        with patch("builtins.input", return_value="n"):  # User says no
            with patch("builtins.print") as mock_print:
                cli.run_repo_clean(config)
                mock_print.assert_any_call("Clean operation cancelled.")

    def test_run_repo_clean_with_confirmation(self, mocker):
        """Test repo clean with user confirmation."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/fake/download"}
        mock_clean = mocker.patch(
            "fetchtastic.repo_downloader.clean_repo_directory", return_value=True
        )

        with patch("builtins.input", return_value="y"):  # User says yes
            with patch("builtins.print") as mock_print:
                cli.run_repo_clean(config)
                mock_clean.assert_called_once_with("/fake/download")
                mock_print.assert_any_call("Repository directory cleaned successfully.")

    def test_run_repo_clean_failure(self, mocker):
        """Test repo clean with operation failure."""
        config = {"BASE_DIR": "/fake/dir", "DOWNLOAD_DIR": "/fake/download"}
        mock_clean = mocker.patch(
            "fetchtastic.repo_downloader.clean_repo_directory", return_value=False
        )

        with patch("builtins.input", return_value="y"):  # User says yes
            with patch("builtins.print") as mock_print:
                cli.run_repo_clean(config)
                mock_print.assert_any_call("Failed to clean repository directory.")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLISetupFunctionality:
    """Test CLI setup functionality."""

    def test_cli_setup_command_basic(self, mocker):
        """Test basic CLI setup command."""
        mocker.patch("sys.argv", ["fetchtastic", "setup"])
        mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")

        with pytest.raises(SystemExit):
            cli.main()

        mock_run_setup.assert_called_once_with(sections=None)

    def test_cli_setup_command_with_sections(self, mocker):
        """Test CLI setup command with sections."""
        mocker.patch("sys.argv", ["fetchtastic", "setup", "section1", "section2"])
        mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")

        with pytest.raises(SystemExit):
            cli.main()

        mock_run_setup.assert_called_once_with(sections=["section1", "section2"])

    def test_cli_setup_command_error(self, mocker):
        """Test setup command with error."""
        mocker.patch("sys.argv", ["fetchtastic", "setup"])
        mock_run_setup = mocker.patch(
            "fetchtastic.setup_config.run_setup", side_effect=Exception("Setup failed")
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_logger.error.assert_called_with("Setup failed: Setup failed")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIDownloadFunctionality:
    """Test CLI download functionality."""

    def test_cli_download_command_no_config(self, mocker):
        """Test download command with no config."""
        mocker.patch("sys.argv", ["fetchtastic", "download"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(False, None)
        )
        mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_run_setup.assert_called_once()
        mock_logger.info.assert_called_with("No configuration found. Starting setup...")

    def test_cli_download_command_with_config(self, mocker):
        """Test download command with existing config."""
        mocker.patch("sys.argv", ["fetchtastic", "download"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config", return_value={"key": "val"}
        )
        mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_downloader_main.assert_called_once()
        mock_logger.info.assert_called_with("Starting download process...")


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLIRepoFunctionality:
    """Test CLI repo functionality."""

    def test_cli_repo_browse_command(self, mocker):
        """Test CLI repo browse command."""
        mocker.patch("sys.argv", ["fetchtastic", "repo", "browse"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config", return_value={"key": "val"}
        )
        mock_repo_main = mocker.patch("fetchtastic.repo_downloader.main")

        with pytest.raises(SystemExit):
            cli.main()

        mock_repo_main.assert_called_once_with({"key": "val"})

    def test_cli_repo_clean_command(self, mocker):
        """Test CLI repo clean command."""
        mocker.patch("sys.argv", ["fetchtastic", "repo", "clean"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config", return_value={"key": "val"}
        )
        mock_run_repo_clean = mocker.patch("fetchtastic.cli.run_repo_clean")

        with pytest.raises(SystemExit):
            cli.main()

        mock_run_repo_clean.assert_called_once_with({"key": "val"})


@pytest.mark.user_interface
@pytest.mark.unit
class TestCLITopicFunctionality:
    """Test CLI topic functionality."""

    def test_cli_topic_command(self, mocker):
        """Test CLI topic command."""
        mocker.patch("sys.argv", ["fetchtastic", "topic"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config",
            return_value={"NTFY_TOPIC": "test-topic"},
        )
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_logger.info.assert_called_with("Current NTFY topic: test-topic")

    def test_cli_topic_command_no_topic(self, mocker):
        """Test CLI topic command with no topic configured."""
        mocker.patch("sys.argv", ["fetchtastic", "topic"])
        mocker.patch(
            "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
        )
        mock_load_config = mocker.patch(
            "fetchtastic.setup_config.load_config", return_value={}
        )  # No NTFY_TOPIC
        mock_logger = mocker.patch("fetchtastic.cli.logger")

        with pytest.raises(SystemExit):
            cli.main()

        mock_logger.info.assert_called_with("No NTFY topic configured.")
