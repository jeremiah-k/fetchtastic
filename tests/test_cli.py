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
    mock_subprocess_run.return_value.returncode = 0
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = mock_popen.return_value
        mock_proc.communicate.return_value = (None, None)
        cli.run_clean()
        mock_popen.assert_called_once()

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
