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


def test_cli_setup_command_windows_integration_update(mocker):
    """Test the 'setup' command with Windows integration update."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mock_load_config = mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"BASE_DIR": "/fake/dir"}
    )
    mock_create_shortcuts = mocker.patch(
        "fetchtastic.setup_config.create_windows_menu_shortcuts", return_value=True
    )
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/fake/config.yaml")

    mock_logger = mocker.patch("fetchtastic.cli.logger")
    cli.main()
    mock_load_config.assert_called_once()
    mock_create_shortcuts.assert_called_once_with("/fake/config.yaml", "/fake/dir")

    mock_logger.info.assert_any_call("Windows integrations updated successfully!")


def test_cli_setup_command_windows_integration_update_no_config(mocker):
    """Test the 'setup' command with Windows integration update but no config."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    mock_logger = mocker.patch("fetchtastic.cli.logger")
    cli.main()

    # Should log error message about no configuration
    mock_logger.error.assert_called_with(
        "No configuration found. Run 'fetchtastic setup' first."
    )


def test_cli_setup_command_windows_integration_update_failed(mocker):
    """Test the 'setup' command with Windows integration update that fails."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"BASE_DIR": "/fake/dir"}
    )
    mocker.patch(
        "fetchtastic.setup_config.create_windows_menu_shortcuts", return_value=False
    )
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/fake/config.yaml")

    mock_logger = mocker.patch("fetchtastic.cli.logger")
    cli.main()
    mock_logger.error.assert_any_call("Failed to update Windows integrations.")


def test_cli_setup_command_windows_integration_update_non_windows(mocker):
    """Test the 'setup' command with Windows integration update on non-Windows."""
    # Mock platform.system to ensure the flag is not added on non-Windows platforms
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch(
        "fetchtastic.cli.display_version_info",
        return_value=("1.0.0", "1.0.0", False),
    )

    # This should raise SystemExit because --update-integrations is not available on Linux
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.parametrize("command", ["browse", "clean"])
def test_cli_repo_command_success(mocker, command):
    """Test the 'repo' subcommands with successful execution."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", command])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mock_load_config = mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"key": "val"}
    )

    if command == "browse":
        mock_action = mocker.patch("fetchtastic.repo_downloader.main")
    else:  # clean
        mock_action = mocker.patch("fetchtastic.cli.run_repo_clean")

    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    cli.main()
    mock_load_config.assert_called_once()
    mock_action.assert_called_once_with({"key": "val"})


def test_cli_repo_browse_command_no_config(mocker):
    """Test the 'repo browse' command with no config."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "browse"])
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")
    mock_load_config = mocker.patch(
        "fetchtastic.setup_config.load_config", return_value={"key": "val"}
    )
    mock_repo_main = mocker.patch("fetchtastic.repo_downloader.main")
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    cli.main()
    mock_run_setup.assert_called_once()
    mock_load_config.assert_called_once()
    mock_repo_main.assert_called_once_with({"key": "val"})


def test_cli_repo_browse_command_config_load_failed(mocker):
    """Test the 'repo browse' command when config loading fails."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "browse"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    mock_logger = mocker.patch("fetchtastic.cli.logger")
    cli.main()
    mock_logger.error.assert_any_call(
        "Configuration not found. Please run 'fetchtastic setup' first."
    )


@pytest.mark.parametrize("command", ["browse", "clean"])
def test_cli_repo_command_with_update_available(mocker, command):
    """Test the 'repo' subcommands with an update available."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", command])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})

    if command == "browse":
        mocker.patch("fetchtastic.repo_downloader.main")
    else:  # clean
        mocker.patch("fetchtastic.cli.run_repo_clean")

    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.1.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command",
        return_value="pip install --upgrade fetchtastic",
    )

    mock_logger = mocker.patch("fetchtastic.cli.logger")
    cli.main()

    # Should log update available messages
    mock_logger.info.assert_any_call("\nUpdate Available")
    mock_logger.info.assert_any_call(
        "A newer version (v1.1.0) of Fetchtastic is available!"
    )
    mock_logger.info.assert_any_call(
        "Run 'pip install --upgrade fetchtastic' to upgrade."
    )


def test_cli_repo_command_no_subcommand(mocker, capfd):
    """Test the 'repo' command with no subcommand."""
    mocker.patch("sys.argv", ["fetchtastic", "repo"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    cli.main()
    captured = capfd.readouterr()

    # Should show help output when no subcommand is provided
    # Help output goes to stdout in this case
    assert (
        "usage:" in captured.out
        or "Interact with the meshtastic.github.io repository" in captured.out
    )


def test_cli_no_command_help(mocker, capfd):
    """Test CLI with no command shows help."""
    mocker.patch("sys.argv", ["fetchtastic"])

    # CLI shows help and exits normally, doesn't raise SystemExit
    cli.main()

    captured = capfd.readouterr()
    assert "usage:" in captured.out


def test_cli_setup_command(mocker):
    """Test the 'setup' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "setup"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    # Patch the display_version_info where it's looked up: in the cli module
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_setup_run.assert_called_once_with(sections=None)


def test_cli_setup_command_with_sections(mocker):
    """Ensure the setup command forwards section filters."""
    mocker.patch(
        "sys.argv",
        ["fetchtastic", "setup", "--section", "firmware", "--section", "android"],
    )
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_positional_sections(mocker):
    """Positional section arguments should be passed to setup."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "firmware", "android"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_invalid_positional_sections(mocker):
    """Invalid positional section arguments should cause an error."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "invalid_section", "firmware"])
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    # parser.error() causes SystemExit(2)
    assert exc_info.value.code == 2


def test_cli_setup_command_with_duplicate_sections(mocker):
    """Duplicate section arguments should be deduplicated."""
    mocker.patch(
        "sys.argv",
        [
            "fetchtastic",
            "setup",
            "--section",
            "firmware",
            "firmware",
            "android",
            "firmware",
        ],
    )
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0", "1.0", False)
    )

    cli.main()

    # Should deduplicate while preserving order: firmware, android
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_update_available(mocker):
    """Test the 'setup' command when an update is available."""
    mocker.patch("sys.argv", ["fetchtastic", "setup"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mock_logger = mocker.patch("fetchtastic.cli.logger")

    # Mock version info to indicate update is available
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.1.0", True)
    )
    # Mock upgrade command to return expected value
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command",
        return_value="pip install --upgrade fetchtastic",
    )

    cli.main()

    # Should run setup
    mock_setup_run.assert_called_once_with(sections=None)

    # Should log update information
    mock_logger.info.assert_any_call("\nUpdate Available")
    mock_logger.info.assert_any_call(
        "A newer version (v1.1.0) of Fetchtastic is available!"
    )
    mock_logger.info.assert_any_call(
        "Run 'pip install --upgrade fetchtastic' to upgrade."
    )


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
@patch("fetchtastic.setup_config.BASE_DIR", "/tmp/test_base_dir")  # nosec B108
@patch("fetchtastic.setup_config.CONFIG_DIR", "/tmp/config/fetchtastic")  # nosec B108
@patch("os.path.isfile")
@patch("os.path.isdir")
def test_run_clean(
    mock_isdir,
    mock_isfile,
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
    mock_listdir.return_value = [
        "some_dir",  # unmanaged dir
        "repo-dls",  # managed dir
        "firmware-2.7.4",  # managed dir (starts with FIRMWARE_DIR_PREFIX)
        "latest_android_release.txt",  # managed file
        "unmanaged.txt",  # unmanaged file
    ]

    def isdir_side_effect(path):
        """
        Indicates whether a filesystem path should be treated as a directory for test side effects.

        Parameters:
            path (str): The filesystem path to evaluate.

        Returns:
            bool: `True` if the path's basename is one of "some_dir", "repo-dls", or "firmware-2.7.4", `False` otherwise.
        """
        return os.path.basename(path) in ["some_dir", "repo-dls", "firmware-2.7.4"]

    def isfile_side_effect(path):
        """
        Determine whether the provided filesystem path should be treated as a file for test side effects.

        Parameters:
            path (str): The filesystem path to examine.

        Returns:
            True if the path's basename is "latest_android_release.txt" or "unmanaged.txt", False otherwise.
        """
        return os.path.basename(path) in ["latest_android_release.txt", "unmanaged.txt"]

    mock_isdir.side_effect = isdir_side_effect
    mock_isfile.side_effect = isfile_side_effect
    mock_subprocess_run.return_value.stdout = "# fetchtastic cron job"
    mock_subprocess_run.return_value.returncode = 0
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = mock_popen.return_value
        mock_proc.communicate.return_value = (None, None)
        cli.run_clean()
        mock_popen.assert_called_once()

    # Check that config files are removed
    mock_os_remove.assert_any_call("/tmp/config/fetchtastic.yaml")  # nosec B108
    mock_os_remove.assert_any_call("/tmp/old_config/fetchtastic.yaml")  # nosec B108

    # Check that only managed directories are cleaned
    # "repo-dls" is in MANAGED_DIRECTORIES, "firmware-2.7.4" starts with FIRMWARE_DIR_PREFIX
    # "some_dir" is not managed, so should not be removed
    # Also removes batch directory from config dir
    mock_rmtree.assert_any_call("/tmp/config/fetchtastic/batch")  # nosec B108
    mock_rmtree.assert_any_call("/tmp/test_base_dir/repo-dls")  # nosec B108
    mock_rmtree.assert_any_call("/tmp/test_base_dir/firmware-2.7.4")  # nosec B108
    # Should not remove "some_dir"
    assert mock_rmtree.call_count == 3

    # Check that managed files are removed but unmanaged files are not
    # "latest_android_release.txt" is in MANAGED_FILES, so should be removed
    mock_os_remove.assert_any_call(
        "/tmp/test_base_dir/latest_android_release.txt"
    )  # nosec B108
    # "unmanaged.txt" is not managed, so should not be removed
    # Total removes: 2 config files + 1 managed file + boot script + log file = 5
    assert mock_os_remove.call_count == 5

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


def test_cli_help_command_general(mocker):
    """Test the 'help' command with no arguments."""
    mocker.patch("sys.argv", ["fetchtastic", "help"])
    mock_print_help = mocker.patch("argparse.ArgumentParser.print_help")

    cli.main()

    mock_print_help.assert_called_once()


def test_cli_help_command_repo(mocker):
    """Test the 'help repo' command."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "repo"])
    mock_show_help = mocker.patch("fetchtastic.cli.show_help")

    cli.main()

    mock_show_help.assert_called_once()
    # Verify the arguments passed to show_help
    args = mock_show_help.call_args[0]
    help_command = args[3]  # 4th argument is help_command
    help_subcommand = args[4]  # 5th argument is help_subcommand
    assert help_command == "repo"
    assert help_subcommand is None


def test_cli_help_command_repo_browse(mocker):
    """Test the 'help repo browse' command."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "repo", "browse"])
    mock_show_help = mocker.patch("fetchtastic.cli.show_help")

    cli.main()

    mock_show_help.assert_called_once()
    # Verify the arguments passed to show_help
    args = mock_show_help.call_args[0]
    help_command = args[3]  # 4th argument is help_command
    help_subcommand = args[4]  # 5th argument is help_subcommand
    assert help_command == "repo"
    assert help_subcommand == "browse"


def test_cli_help_command_setup(mocker):
    """Test the 'help setup' command."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "setup"])
    mock_show_help = mocker.patch("fetchtastic.cli.show_help")

    cli.main()

    mock_show_help.assert_called_once()
    # Verify the arguments passed to show_help
    args = mock_show_help.call_args[0]
    help_command = args[3]  # 4th argument is help_command
    help_subcommand = args[4]  # 5th argument is help_subcommand
    assert help_command == "setup"
    assert help_subcommand is None


def test_cli_help_command_unknown(mocker):
    """Test the 'help unknown' command."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "unknown"])
    mock_show_help = mocker.patch("fetchtastic.cli.show_help")

    cli.main()

    mock_show_help.assert_called_once()
    # Verify the arguments passed to show_help
    args = mock_show_help.call_args[0]
    help_command = args[3]  # 4th argument is help_command
    help_subcommand = args[4]  # 5th argument is help_subcommand
    assert help_command == "unknown"
    assert help_subcommand is None


def test_show_help_general(mocker):
    """Test show_help function with no specific command."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()

    cli.show_help(mock_parser, mock_repo_parser, mock_repo_subparsers, None, None)

    mock_parser.print_help.assert_called_once()
    mock_repo_parser.print_help.assert_not_called()


def test_show_help_repo_command(mocker):
    """Test show_help function with repo command."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()

    cli.show_help(mock_parser, mock_repo_parser, mock_repo_subparsers, "repo", None)

    mock_parser.print_help.assert_not_called()
    mock_repo_parser.print_help.assert_called_once()


def test_show_help_repo_browse_subcommand(mocker, capsys):
    """Test show_help function with repo browse subcommand."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_browse_parser = mocker.MagicMock()

    # Mock the choices dictionary to return the browse parser
    mock_repo_subparsers.choices = {
        "browse": mock_browse_parser,
        "clean": mocker.MagicMock(),
    }

    cli.show_help(mock_parser, mock_repo_parser, mock_repo_subparsers, "repo", "browse")

    mock_repo_parser.print_help.assert_called_once()
    mock_browse_parser.print_help.assert_called_once()

    # Check that the correct message was printed
    captured = capsys.readouterr()
    assert "Repo 'browse' command help:" in captured.out


def test_show_help_repo_unknown_subcommand(mocker, capsys):
    """Test show_help function with unknown repo subcommand."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_repo_subparsers.choices = {
        "browse": mocker.MagicMock(),
        "clean": mocker.MagicMock(),
    }

    cli.show_help(
        mock_parser, mock_repo_parser, mock_repo_subparsers, "repo", "unknown"
    )

    mock_repo_parser.print_help.assert_called_once()

    # Check that the correct error message was printed
    captured = capsys.readouterr()
    assert "Unknown repo subcommand: unknown" in captured.out
    assert "Available repo subcommands: browse, clean" in captured.out


def test_show_help_other_commands(mocker, capsys):
    """Test show_help function with other main commands."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_main_subparsers = mocker.MagicMock()

    # Create mock subparsers for each command
    mock_subparsers = {}
    for command in ["setup", "download", "topic", "clean", "version"]:
        mock_subparsers[command] = mocker.MagicMock()
    mock_main_subparsers.choices = mock_subparsers

    for command in ["setup", "download", "topic", "clean", "version"]:
        cli.show_help(
            mock_parser,
            mock_repo_parser,
            mock_repo_subparsers,
            command,
            None,
            mock_main_subparsers,
        )

    # Should call print_help once for each command's subparser
    for command in ["setup", "download", "topic", "clean", "version"]:
        mock_subparsers[command].print_help.assert_called_once()
    mock_repo_parser.print_help.assert_not_called()

    # Check that the correct messages were printed
    captured = capsys.readouterr()
    for command in ["setup", "download", "topic", "clean", "version"]:
        assert f"Help for '{command}' command:" in captured.out


def test_show_help_unknown_command(mocker, capsys):
    """Test show_help function with unknown command."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_main_subparsers = mocker.MagicMock()
    mock_main_subparsers.choices = {
        "setup": mocker.MagicMock(),
        "download": mocker.MagicMock(),
        "topic": mocker.MagicMock(),
        "clean": mocker.MagicMock(),
        "version": mocker.MagicMock(),
        "repo": mocker.MagicMock(),
        "help": mocker.MagicMock(),
    }

    cli.show_help(
        mock_parser,
        mock_repo_parser,
        mock_repo_subparsers,
        "unknown",
        None,
        mock_main_subparsers,
    )

    mock_parser.print_help.assert_not_called()
    mock_repo_parser.print_help.assert_not_called()

    # Check that the correct error message was printed
    captured = capsys.readouterr()
    assert "Unknown command: unknown" in captured.out
    assert "Available commands:" in captured.out
    # Check that all expected commands are present (should be sorted)
    expected_commands = [
        "clean",
        "download",
        "help",
        "repo",
        "setup",
        "topic",
        "version",
    ]
    assert ", ".join(expected_commands) in captured.out
    assert "For general help, use: fetchtastic help" in captured.out


def test_clipboard_prompt_eoferror_handling(mocker, capsys):
    """Test clipboard prompt handles EOFError gracefully in non-interactive contexts."""
    # Mock the config and setup
    mock_config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test-topic-123"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("sys.argv", ["fetchtastic", "topic"])

    # Mock input to raise EOFError (simulating closed stdin in CI/pipes)
    mocker.patch("builtins.input", side_effect=EOFError())

    # Mock clipboard function where it's imported in CLI
    mock_copy = mocker.patch(
        "fetchtastic.cli.copy_to_clipboard_func", return_value=True
    )

    # Run the command
    cli.main()

    # Should default to "y" and copy to clipboard
    mock_copy.assert_called_once_with("https://ntfy.sh/test-topic-123")

    # Check output
    captured = capsys.readouterr()
    assert "Current NTFY topic URL: https://ntfy.sh/test-topic-123" in captured.out
    assert "Topic URL copied to clipboard." in captured.out


def test_clipboard_prompt_yes_variations(mocker, capsys):
    """Test clipboard prompt accepts both 'y' and 'yes' responses."""
    mock_config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test-topic-123"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mock_copy = mocker.patch(
        "fetchtastic.cli.copy_to_clipboard_func", return_value=True
    )

    # Test "yes" response
    mocker.patch("builtins.input", return_value="yes")
    cli.main()
    mock_copy.assert_called_with("https://ntfy.sh/test-topic-123")

    # Reset mock
    mock_copy.reset_mock()

    # Test "y" response
    mocker.patch("builtins.input", return_value="y")
    cli.main()
    mock_copy.assert_called_with("https://ntfy.sh/test-topic-123")


def test_show_help_early_return_behavior(mocker, capsys):
    """Test that show_help returns early after handling repo command."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_repo_subparsers.choices = {
        "browse": mocker.MagicMock(),
        "clean": mocker.MagicMock(),
    }
    mock_main_subparsers = mocker.MagicMock()
    mock_main_subparsers.choices = {"setup": mocker.MagicMock()}

    # Test repo command with subcommand
    cli.show_help(
        mock_parser,
        mock_repo_parser,
        mock_repo_subparsers,
        "repo",
        "browse",
        mock_main_subparsers,
    )

    # Should have called repo parser and subcommand parser
    mock_repo_parser.print_help.assert_called_once()
    mock_repo_subparsers.choices["browse"].print_help.assert_called_once()

    # Should NOT have called main parser (due to early return)
    mock_parser.print_help.assert_not_called()

    # Check output
    captured = capsys.readouterr()
    assert "Repo 'browse' command help:" in captured.out


def test_copy_to_clipboard_func_termux_success(mocker):
    """Test clipboard functionality on Termux (success)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mock_run = mocker.patch("subprocess.run")

    result = cli.copy_to_clipboard_func("test text")

    assert result is True
    mock_run.assert_called_once_with(
        ["termux-clipboard-set"], input=b"test text", check=True
    )


def test_copy_to_clipboard_func_termux_failure(mocker):
    """Test clipboard functionality on Termux (failure)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("subprocess.run", side_effect=Exception("Termux error"))
    mock_print = mocker.patch("builtins.print")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_print.assert_called_with(
        "An error occurred while copying to clipboard: Termux error"
    )


def test_copy_to_clipboard_func_macos_success(mocker):
    """Test clipboard functionality on macOS (success)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Darwin")
    mock_run = mocker.patch("subprocess.run")

    result = cli.copy_to_clipboard_func("test text")

    assert result is True
    mock_run.assert_called_once_with("pbcopy", text=True, input="test text", check=True)


def test_copy_to_clipboard_func_linux_xclip_success(mocker):
    """Test clipboard functionality on Linux with xclip (success)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "shutil.which", side_effect=lambda x: "/usr/bin/xclip" if x == "xclip" else None
    )
    mock_run = mocker.patch("subprocess.run")

    result = cli.copy_to_clipboard_func("test text")

    assert result is True
    mock_run.assert_called_once_with(
        ["xclip", "-selection", "clipboard"], input=b"test text", check=True
    )


def test_copy_to_clipboard_func_linux_xsel_success(mocker):
    """Test clipboard functionality on Linux with xsel (success)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "shutil.which", side_effect=lambda x: "/usr/bin/xsel" if x == "xsel" else None
    )
    mock_run = mocker.patch("subprocess.run")

    result = cli.copy_to_clipboard_func("test text")

    assert result is True
    mock_run.assert_called_once_with(
        ["xsel", "--clipboard", "--input"], input=b"test text", check=True
    )


def test_copy_to_clipboard_func_linux_no_tools(mocker):
    """Test clipboard functionality on Linux with no clipboard tools."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value=None)  # No clipboard tools available
    mock_print = mocker.patch("builtins.print")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_print.assert_called_with(
        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
    )


def test_copy_to_clipboard_func_unsupported_platform(mocker):
    """Test clipboard functionality on unsupported platform."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="FreeBSD")
    mock_print = mocker.patch("builtins.print")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_print.assert_called_with(
        "Clipboard functionality is not supported on this platform."
    )


def test_copy_to_clipboard_func_subprocess_error(mocker):
    """Test clipboard functionality with subprocess error."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("subprocess.run", side_effect=Exception("Subprocess error"))
    mock_print = mocker.patch("builtins.print")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_print.assert_called_with(
        "An error occurred while copying to clipboard: Subprocess error"
    )


def test_run_clean_cancelled(mocker):
    """Test run_clean when user cancels."""
    mocker.patch("builtins.input", return_value="n")
    mock_print = mocker.patch("builtins.print")

    cli.run_clean()

    mock_print.assert_any_call("Clean operation cancelled.")


def test_run_clean_user_says_no_explicitly(mocker):
    """Test run_clean when user explicitly says no."""
    mocker.patch("builtins.input", return_value="no")
    mock_print = mocker.patch("builtins.print")

    cli.run_clean()

    mock_print.assert_any_call("Clean operation cancelled.")


def test_cli_download_with_log_level_config(mocker):
    """
    Verify that running the CLI `download` command applies a configured LOG_LEVEL and dispatches to the downloader.

    Sets up a fake CLI invocation and a loaded configuration containing a "LOG_LEVEL" key. Asserts that `set_log_level` is called with the configured value, `downloader.main` is invoked, and `setup_config.run_setup` is not called when a valid config exists.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Test when config exists with LOG_LEVEL setting
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Mock load_config to return config with LOG_LEVEL
    mock_config = {"LOG_LEVEL": "DEBUG", "other_setting": "value"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Verify that set_log_level was called with the correct level
    mock_set_log_level.assert_called_once_with("DEBUG")
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_without_log_level_config(mocker):
    """Test the 'download' command without LOG_LEVEL configuration."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Test when config exists but without LOG_LEVEL setting
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Mock load_config to return config without LOG_LEVEL
    mock_config = {"other_setting": "value"}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Verify that set_log_level was NOT called
    mock_set_log_level.assert_not_called()
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_with_empty_config(mocker):
    """
    Verify that when a config path exists but load_config returns None, the CLI `download` command:
    - does not call `set_log_level`,
    - invokes the downloader (`downloader.main`),
    - and does not run setup (`run_setup`).
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Test when config exists but load_config returns None
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Mock load_config to return None
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)

    cli.main()

    # Verify that set_log_level was NOT called
    mock_set_log_level.assert_not_called()
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_with_various_log_levels(mocker):
    """Test the 'download' command with various LOG_LEVEL values."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mocker.patch("fetchtastic.setup_config.run_setup")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Test different log levels
    log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    for log_level in log_levels:
        mock_set_log_level.reset_mock()
        mock_downloader_main.reset_mock()

        mock_config = {"LOG_LEVEL": log_level}
        mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

        cli.main()

        # Verify that set_log_level was called with the correct level
        mock_set_log_level.assert_called_once_with(log_level)
        mock_downloader_main.assert_called_once()


@pytest.mark.parametrize("log_level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_cli_download_parametrized_log_levels(mocker, log_level):
    """Test the 'download' command with parametrized LOG_LEVEL values."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    mock_config = {"LOG_LEVEL": log_level}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Verify that set_log_level was called with the correct level
    mock_set_log_level.assert_called_once_with(log_level)
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


@pytest.mark.parametrize("invalid_log_level", ["INVALID", "TRACE", "VERBOSE", "123"])
def test_cli_download_with_invalid_log_levels(mocker, invalid_log_level):
    """
    Verify that running the "download" command with an invalid LOG_LEVEL value does not raise,
    that the CLI passes the raw value to set_log_level (letting that function handle validation),
    and that downloader.main is invoked while setup_config.run_setup is not.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    mock_config = {"LOG_LEVEL": invalid_log_level}
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    # Should not raise an exception, but set_log_level might handle invalid values
    cli.main()

    # Verify that set_log_level was called with the invalid level (let set_log_level handle validation)
    mock_set_log_level.assert_called_once_with(invalid_log_level)
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_with_empty_log_level(mocker):
    """
    Verify that when a configuration contains an empty `LOG_LEVEL` value, the CLI's `download` command does not attempt to set the log level but still invokes the downloader and does not run setup.

    This patches a present configuration with `"LOG_LEVEL": ""` and asserts:
    - set_log_level is not called for the empty (falsy) value,
    - downloader.main is invoked exactly once,
    - setup_config.run_setup is not invoked.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    mock_config = {"LOG_LEVEL": ""}  # Empty string
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Empty string should NOT call set_log_level (falsy value)
    mock_set_log_level.assert_not_called()
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_with_case_insensitive_log_levels(mocker):
    """Test the 'download' command with case variations of LOG_LEVEL values."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_downloader_main = mocker.patch("fetchtastic.downloader.main")
    mock_set_log_level = mocker.patch("fetchtastic.cli.set_log_level")
    mocker.patch("fetchtastic.setup_config.run_setup")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Test different case variations
    case_variations = ["debug", "Info", "warning", "Error", "critical"]

    for log_level in case_variations:
        mock_set_log_level.reset_mock()
        mock_downloader_main.reset_mock()

        mock_config = {"LOG_LEVEL": log_level}
        mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

        cli.main()

        # Verify that set_log_level was called with the case variation
        mock_set_log_level.assert_called_once_with(log_level)
        mock_downloader_main.assert_called_once()
