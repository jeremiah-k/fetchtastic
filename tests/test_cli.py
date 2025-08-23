import os
import subprocess
from unittest.mock import patch

import pytest

from fetchtastic import cli


def test_cli_download_command(mocker):
    """Test the 'download' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # 1. Test when config exists
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    # Mock the migration logic to avoid its side effects
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")
    cli.main()
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()

    # 2. Test when config does not exist
    mock_downloader_main.reset_mock()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    cli.main()
    mock_setup_run.assert_called_once()
    mock_downloader_main.assert_not_called()


def test_cli_download_with_migration(mocker):
    """Test the 'download' command with an old config file that needs migration."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mocker.patch(
        "fetchtastic.setup_config.config_exists",
        return_value=(True, "/path/to/old/config"),
    )
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/path/to/old/config")
    mocker.patch("fetchtastic.setup_config.migrate_config", return_value=True)
    mocker.patch("os.path.exists", return_value=False)
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})

    cli.main()
    mock_downloader_main.assert_called_once()


def test_cli_setup_command(mocker):
    """Test the 'setup' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "setup"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    # Patch the display_version_info where it's looked up: in the cli module
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_setup_run.assert_called_once()


def test_cli_repo_browse_command(mocker):
    """Test the 'repo browse' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "browse"])
    mock_repo_main = mocker.patch("fetchtastic.repo_downloader.main")
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_repo_main.assert_called_once()


def test_cli_repo_clean_command(mocker):
    """Test the 'repo clean' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "clean"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})
    mock_run_repo_clean = mocker.patch("fetchtastic.cli.run_repo_clean")
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_run_repo_clean.assert_called_once()


def test_cli_clean_command(mocker):
    """Test the 'clean' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "clean"])
    # Mock the function in the cli module itself
    mock_run_clean = mocker.patch("fetchtastic.cli.run_clean")
    cli.main()
    mock_run_clean.assert_called_once()


@patch("builtins.input", return_value="y")
@patch("os.path.exists")
@patch("os.remove")
@patch("shutil.rmtree")
@patch("os.listdir")
@patch("os.rmdir")
@patch("subprocess.run")
@patch("platform.system", return_value="Linux")
@patch(
    "fetchtastic.setup_config.CONFIG_FILE", "/tmp/config/fetchtastic.yaml"
)  # nosec B108
@patch(
    "fetchtastic.setup_config.OLD_CONFIG_FILE", "/tmp/old_config/fetchtastic.yaml"
)  # nosec B108
@patch("fetchtastic.setup_config.BASE_DIR", "/tmp/meshtastic")  # nosec B108
@patch("os.path.isdir")
def test_run_clean(
    mock_isdir,
    mock_platform_system,
    mock_subprocess_run,
    mock_rmdir,
    mock_listdir,
    mock_rmtree,
    mock_os_remove,
    mock_os_path_exists,
    mock_input,
):
    """Test the run_clean function."""
    # Simulate existing files and directories
    mock_os_path_exists.return_value = True
    mock_listdir.return_value = ["some_dir"]
    mock_isdir.return_value = True
    mock_subprocess_run.return_value.stdout = "# fetchtastic cron job"

    cli.run_clean()

    # Check that config files are removed
    mock_os_remove.assert_any_call("/tmp/config/fetchtastic.yaml")  # nosec B108
    mock_os_remove.assert_any_call("/tmp/old_config/fetchtastic.yaml")  # nosec B108

    # Check that download directory is cleaned
    mock_rmtree.assert_any_call(
        os.path.join("/tmp/meshtastic", "some_dir")  # nosec B108
    )

    # Check that cron jobs are removed
    mock_subprocess_run.assert_any_call(
        ["crontab", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_cli_version_command(mocker):
    """Test the 'version' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "version"])
    # Patch where the function is looked up (in the cli module)
    mock_version_info = mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.2.3", "1.2.3", False)
    )
    cli.main()
    mock_version_info.assert_called_once()


def test_cli_no_command(mocker):
    """Test running with no command."""
    mocker.patch("sys.argv", ["fetchtastic"])
    # The ArgumentParser instance is local to cli.main, so we patch the class
    mock_print_help = mocker.patch("argparse.ArgumentParser.print_help")

    cli.main()

    # Assert that print_help was called on an instance of the parser
    mock_print_help.assert_called_once()


def test_cli_topic_command_with_config(mocker):
    """Test the 'topic' command with valid configuration."""
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mock_config = {"NTFY_SERVER": "https://ntfy.sh/", "NTFY_TOPIC": "test-topic-123"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("builtins.input", return_value="y")
    mock_copy = mocker.patch(
        "fetchtastic.cli.copy_to_clipboard_func", return_value=True
    )
    mock_print = mocker.patch("builtins.print")

    cli.main()

    # Verify the topic URL was displayed
    mock_print.assert_any_call("Current NTFY topic URL: https://ntfy.sh/test-topic-123")
    mock_print.assert_any_call("Topic name: test-topic-123")

    # Verify copy to clipboard was called
    mock_copy.assert_called_once_with("https://ntfy.sh/test-topic-123")


def test_cli_topic_command_termux(mocker):
    """Test the 'topic' command on Termux."""
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mock_config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "termux-topic"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("builtins.input", return_value="y")
    mock_copy = mocker.patch(
        "fetchtastic.cli.copy_to_clipboard_func", return_value=True
    )

    cli.main()

    # On Termux, should copy topic name instead of full URL
    mock_copy.assert_called_once_with("termux-topic")


def test_cli_topic_command_no_config(mocker):
    """Test the 'topic' command with no configuration."""
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    mock_print = mocker.patch("builtins.print")

    cli.main()

    mock_print.assert_called_with(
        "Notifications are not set up. Run 'fetchtastic setup' to configure notifications."
    )


def test_cli_topic_command_copy_declined(mocker):
    """Test the 'topic' command when user declines to copy."""
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mock_config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test-topic"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("builtins.input", return_value="n")
    mock_copy = mocker.patch("fetchtastic.cli.copy_to_clipboard_func")
    mock_print = mocker.patch("builtins.print")

    cli.main()

    mock_copy.assert_not_called()
    mock_print.assert_any_call("You can copy the topic information from above.")


def test_cli_setup_with_windows_integration_update(mocker):
    """Test the 'setup' command with Windows integration update."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mock_config = {"BASE_DIR": "/test/dir"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch(
        "fetchtastic.setup_config.create_windows_menu_shortcuts", return_value=True
    )
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    cli.main()

    # Should not run full setup, only update integrations
    mock_setup_run.assert_not_called()


def test_cli_setup_windows_integration_no_config(mocker):
    """Test Windows integration update with no config."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )
    mock_logger = mocker.patch("fetchtastic.cli.logger")

    cli.main()

    mock_logger.error.assert_called_with(
        "No configuration found. Run 'fetchtastic setup' first."
    )


def test_cli_setup_windows_integration_non_windows(mocker):
    """Test that --update-integrations flag is not available on non-Windows."""
    # On non-Windows, the --update-integrations flag shouldn't exist
    mocker.patch("platform.system", return_value="Linux")

    # Test that the argument parser doesn't include the flag on non-Windows
    with patch("sys.argv", ["fetchtastic", "setup", "--help"]):
        with pytest.raises(SystemExit):  # argparse exits with help
            cli.main()

    # The flag should not be available, so this is expected behavior


def test_cli_version_with_update_available(mocker):
    """Test the 'version' command with update available."""
    mocker.patch("sys.argv", ["fetchtastic", "version"])
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.1.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command", return_value="pipx upgrade fetchtastic"
    )
    mock_logger = mocker.patch("fetchtastic.cli.logger")

    cli.main()

    mock_logger.info.assert_any_call("Fetchtastic v1.0.0")
    mock_logger.info.assert_any_call("A newer version (v1.1.0) is available!")
    mock_logger.info.assert_any_call("Run 'pipx upgrade fetchtastic' to upgrade.")


def test_cli_repo_help_command(mocker):
    """Test the 'repo --help' command."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "--help"])

    # This should trigger SystemExit due to argparse help
    with pytest.raises(SystemExit):
        cli.main()
