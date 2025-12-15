import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path so we can import the module
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

import fetchtastic.cli as cli


@pytest.mark.user_interface
@pytest.mark.unit
def test_show_help_unknown_command(mocker, capsys):
    """Test help system for unknown command."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_subparsers = mocker.MagicMock()
    mock_subparsers.choices = {"setup": mocker.MagicMock()}

    # Call help with unknown command
    cli.show_help(
        mock_parser,
        mock_repo_parser,
        mock_repo_subparsers,
        "unknown_command",
        None,
        mock_subparsers,
    )

    captured = capsys.readouterr()
    assert "Unknown command: unknown_command" in captured.out
    assert "Available commands:" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_show_help_repo_unknown_subcommand(mocker, capsys):
    """Test help system for unknown repo subcommand."""
    mock_parser = mocker.MagicMock()
    mock_repo_parser = mocker.MagicMock()
    mock_repo_subparsers = mocker.MagicMock()
    mock_repo_subparsers.choices = {
        "browse": mocker.MagicMock(),
        "clean": mocker.MagicMock(),
    }
    mock_subparsers = mocker.MagicMock()

    # Call help with unknown repo subcommand
    cli.show_help(
        mock_parser,
        mock_repo_parser,
        mock_repo_subparsers,
        "repo",
        "unknown_subcommand",
        mock_subparsers,
    )

    captured = capsys.readouterr()
    assert "Unknown repo subcommand: unknown_subcommand" in captured.out
    assert "Available repo subcommands: browse, clean" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_topic_command_no_notifications(mocker, capsys):
    """Test topic command when notifications are not configured."""
    mocker.patch("fetchtastic.setup_config.load_config", return_value={})

    with patch("sys.argv", ["fetchtastic", "topic"]):
        with mocker.patch("builtins.input", side_effect=EOFError):
            cli.main()

    captured = capsys.readouterr()
    assert "Notifications are not set up" in captured.out
    assert "Run 'fetchtastic setup'" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_topic_command_clipboard_failure(mocker, capsys):
    """Test topic command when clipboard copy fails."""
    mock_config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test-topic"}

    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mocker.patch("builtins.input", return_value="y")  # Try to copy
    mocker.patch("fetchtastic.setup_config.copy_to_clipboard_func", return_value=False)
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

    with patch("sys.argv", ["fetchtastic", "topic"]):
        cli.main()

    captured = capsys.readouterr()
    assert "Failed to copy to clipboard" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_version_command_update_available(mocker, capsys):
    """Test version command when update is available."""
    mocker.patch(
        "fetchtastic.cli.display_version_info", return_value=("0.8.0", "0.9.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command", return_value="pipx upgrade fetchtastic"
    )
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

    with patch("sys.argv", ["fetchtastic", "version"]):
        cli.main()

    # Should log update information
    mock_logger.info.assert_any_call("\nUpdate Available")
    mock_logger.info.assert_any_call(
        "A newer version (v0.9.0) of Fetchtastic is available!"
    )
    mock_logger.info.assert_any_call("Run 'pipx upgrade fetchtastic' to upgrade.")


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_clean_permission_errors(mocker, capsys):
    """Test run_clean with file/directory permission errors."""
    from fetchtastic.constants import MANAGED_DIRECTORIES, MANAGED_FILES

    mock_config = {"BASE_DIR": "/tmp/test"}

    # Mock file operations to raise permission errors
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/path/to/config")
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/path/to/config")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/path/to/old_config")

    def mock_remove_with_error(path):
        if "config" in path:
            raise PermissionError("Permission denied")

    mocker.patch("os.remove", side_effect=mock_remove_with_error)
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.path.isdir", return_value=False)  # No batch dir
    mocker.patch("os.listdir", return_value=[])
    mocker.patch("builtins.input", return_value="y")
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

    cli.run_clean()

    captured = capsys.readouterr()
    assert "Failed to delete" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_clean_managed_file_filtering(mocker):
    """Test run_clean correctly filters managed vs unmanaged files."""
    from fetchtastic.constants import MANAGED_DIRECTORIES, MANAGED_FILES

    mock_config = {"BASE_DIR": "/tmp/test"}

    # Mock directory contents with mix of managed and unmanaged files
    mock_files = [
        "config.yaml",  # Unmanaged
        "firmware-rak4631.zip",  # Managed by extension pattern
        "personal_file.txt",  # Unmanaged
        "firmware",  # Managed directory
        "documents",  # Unmanaged directory
    ]

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/path/to/config")
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/path/to/config")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/path/to/old_config")
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.listdir", return_value=mock_files)
    mocker.patch(
        "os.path.isdir", side_effect=lambda path: path in ["firmware", "documents"]
    )
    mocker.patch(
        "os.path.isfile",
        side_effect=lambda path: path.endswith(".yaml") or ".zip" in path,
    )

    # Track what gets removed
    removed_files = []
    removed_dirs = []

    def mock_remove(path):
        removed_files.append(path)

    def mock_rmtree(path):
        removed_dirs.append(path)

    mocker.patch("os.remove", side_effect=mock_remove)
    mocker.patch("shutil.rmtree", side_effect=mock_rmtree)
    mocker.patch("fetchtastic.log_utils.logger")
    mocker.patch("builtins.input", return_value="y")

    cli.run_clean()

    # Should only remove managed files, not personal files
    assert "/tmp/test/config.yaml" in removed_files  # Config file is always removed
    assert "/tmp/test/firmware-rak4631.zip" in removed_files  # Managed file
    assert "/tmp/test/firmware" in removed_dirs  # Managed directory

    # Should NOT remove unmanaged files
    assert "/tmp/test/personal_file.txt" not in removed_files
    assert "/tmp/test/documents" not in removed_dirs


@pytest.mark.user_interface
@pytest.mark.unit
def test_cron_job_cleanup_logic(mocker):
    """Test cron job removal on non-Windows platforms."""
    mock_config = {"BASE_DIR": "/tmp/test"}

    # Mock Linux environment
    mocker.patch("platform.system", return_value="Linux")

    # Mock crontab operations
    mock_crontab_output = "# Existing cron\n# fetchtastic download\n0 3 * * * /usr/bin/fetchtastic download\n# Other cron\n"
    mock_subprocess = mocker.MagicMock()
    mock_subprocess.return_value.returncode = 0
    mock_subprocess.return_value.stdout = mock_crontab_output
    mock_popen = mocker.MagicMock()
    mock_popen_instance = mocker.MagicMock()
    mock_popen.return_value = mock_popen_instance

    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/config")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old/config")
    mocker.patch("os.path.exists", return_value=False)
    mocker.patch("builtins.input", return_value="y")
    mocker.patch("fetchtastic.log_utils.logger")
    mocker.patch("subprocess.run", mock_subprocess)
    mocker.patch("subprocess.Popen", mock_popen)

    cli.run_clean()

    # Should have called crontab -l to list jobs
    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.assert_any_call(
        ["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    # Should have called crontab to update
    mock_popen.assert_called_once()
    process_communicate_call = mock_popen_instance.communicate.call_args
    assert process_communicate_call is not None

    # Verify that input to crontab doesn't contain fetchtastic jobs
    new_cron_input = process_communicate_call[1]["input"]
    assert "fetchtastic download" not in new_cron_input


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_repo_command_no_subcommand(mocker, capsys):
    """Test repo command without subcommand."""
    mock_repo_parser = mocker.MagicMock()

    with patch("sys.argv", ["fetchtastic", "repo"]):
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = mocker.MagicMock(
                command="repo", repo_command=None
            )
            cli.main()

    # Should print help for repo command
    mock_repo_parser.print_help.assert_called_once()
