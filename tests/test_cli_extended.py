import argparse
import os
import subprocess
from unittest.mock import Mock, patch

import pytest
from pick import Option

# Import package module (matches how users invoke it)
import fetchtastic.cli as cli
import fetchtastic.menu_repo as menu_repo


@pytest.mark.user_interface
@pytest.mark.unit
def test_show_help_unknown_command(mocker, capsys):
    """Test help system for unknown command."""
    mock_parser = mocker.MagicMock()
    mock_subparsers = mocker.MagicMock()
    mock_subparsers.choices = {"setup": mocker.MagicMock()}

    # Call help with unknown command
    cli.show_help(
        mock_parser,
        mocker.MagicMock(),  # repo_parser
        mocker.MagicMock(),  # repo_subparsers
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
def test_select_item_pick_none(mocker):
    """Test select_item when user cancels with KeyboardInterrupt."""
    # Since select_item doesn't handle KeyboardInterrupt, we test that it propagates
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    mock_pick.side_effect = KeyboardInterrupt  # User cancelled with Ctrl+C

    # Test with proper item format (list of dicts)
    test_items = [
        {"name": "option1", "path": "option1", "type": "file"},
        {"name": "option2", "path": "option2", "type": "file"},
    ]

    # select_item doesn't handle KeyboardInterrupt, so it should raise
    with pytest.raises(KeyboardInterrupt):
        menu_repo.select_item(test_items, "current/path")


@pytest.mark.user_interface
@pytest.mark.unit
def test_select_item_empty_list(mocker):
    """Test select_item with empty options list."""
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")

    result = menu_repo.select_item([], "current/path")

    assert result is None
    mock_pick.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
def test_select_item_single_option(mocker):
    """Test select_item with single option."""
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    mock_pick.return_value = (
        Option(
            label="[Select files in this directory (1 file)]",
            value={"type": "current"},
        ),
        1,
    )

    test_items = [{"name": "only_option", "path": "only_option", "type": "file"}]
    result = menu_repo.select_item(test_items, "current/path")

    assert result is not None
    assert result["type"] == "current"


@pytest.mark.user_interface
@pytest.mark.unit
def test_select_files_user_quits(mocker):
    """Test select_files when user selects quit."""
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    # pick with multiselect returns a list of (option, index) tuples
    mock_pick.return_value = [
        (Option(label="[Quit]", value={"type": "quit"}), 2)
    ]  # Index 2 would be the [Quit] option

    test_files = [
        {"name": "file1.txt", "path": "file1.txt", "type": "file"},
        {"name": "file2.txt", "path": "file2.txt", "type": "file"},
    ]

    result = menu_repo.select_files(test_files)

    assert result == {"type": "quit"}


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_clean_permission_errors(mocker, capsys):
    """Test run_clean with file/directory permission errors."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})

    # Mock file operations to raise permission errors
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/path/to/config")
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/path/to/config")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/path/to/old_config")
    mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value={
            "DOWNLOAD_DIR": "/tmp/test_base_dir",
            "BASE_DIR": "/tmp/test_base_dir",
        },
    )
    mocker.patch("fetchtastic.setup_config.BASE_DIR", "/tmp/test_base_dir")
    mocker.patch("fetchtastic.setup_config.remove_cron_job")
    mocker.patch("fetchtastic.setup_config.remove_reboot_cron_job")

    def mock_remove_with_error(path):
        """Mock remove function that raises PermissionError for paths containing 'config'."""
        if "config" in path:
            raise PermissionError("Permission denied")

    mocker.patch("os.remove", side_effect=mock_remove_with_error)
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.path.isdir", return_value=False)  # No batch dir
    mocker.patch("os.listdir", return_value=[])
    mocker.patch(
        "os.scandir",
        return_value=Mock(
            __enter__=Mock(return_value=[]), __exit__=Mock(return_value=None)
        ),
    )
    mocker.patch("shutil.rmtree")
    mocker.patch("builtins.input", return_value="y")

    cli.run_clean()

    captured = capsys.readouterr()
    assert "Failed to delete" in captured.err


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_clean_managed_file_filtering(mocker):
    """
    Verify run_clean removes only managed artifacts and preserves personal files.

    Ensures the cleanup routine always removes the active and old config files, removes files and directories identified as managed (e.g., firmware archives and managed folders), and does not remove user personal files or directories.
    """
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})

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
    mocker.patch("fetchtastic.setup_config.BASE_DIR", "/test/base")
    mocker.patch("os.path.exists", return_value=True)
    scandir_entries = []
    for entry_name in mock_files:
        entry = Mock()
        entry.name = entry_name
        entry.path = f"/test/base/{entry_name}"
        entry.is_symlink.return_value = False
        entry.is_file.return_value = (
            entry_name.endswith(".yaml") or ".zip" in entry_name
        )
        entry.is_dir.return_value = entry_name in {"firmware", "documents"}
        scandir_entries.append(entry)

    mocker.patch(
        "os.scandir",
        return_value=Mock(
            __enter__=Mock(return_value=scandir_entries),
            __exit__=Mock(return_value=None),
        ),
    )
    mocker.patch(
        "os.path.isdir",
        side_effect=lambda path: os.path.basename(path) in {"firmware", "documents"},
    )
    mocker.patch(
        "os.path.isfile",
        side_effect=lambda path: path.endswith(".yaml") or ".zip" in path,
    )
    # Track what gets removed
    removed_files = []
    removed_dirs = []

    def mock_remove(path):
        """
        Record a filesystem path in the module-level removed_files list.

        Parameters:
            path (str): Filesystem path to record as removed.
        """
        removed_files.append(path)

    def mock_rmtree(path):
        """
        Record a directory path as removed by appending it to the test tracking list `removed_dirs`.

        Parameters:
            path (str): Filesystem path of the directory to record as removed.
        """
        removed_dirs.append(path)

    mocker.patch("os.remove", side_effect=mock_remove)
    mocker.patch("shutil.rmtree", side_effect=mock_rmtree)
    mocker.patch("fetchtastic.log_utils.logger")
    mocker.patch("builtins.input", return_value="y")

    cli.run_clean()

    # Should only remove managed files, not personal files
    assert "/path/to/config" in removed_files  # Config file is always removed
    assert "/path/to/old_config" in removed_files  # Old config is always removed

    # Should remove managed firmware directory
    assert "/test/base/firmware" in removed_dirs

    # Should NOT remove personal files and unmanaged directories
    assert not any("personal_file.txt" in f for f in removed_files)
    assert "/test/base/documents" not in removed_dirs
    assert not any(
        "config.yaml" in f for f in removed_files
    )  # Unmanaged config should not be removed


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_config_migration_failure(mocker, capsys):
    """Test CLI download command when config migration fails."""
    # Mock config in old location, migration fails
    mocker.patch(
        "fetchtastic.setup_config.config_exists",
        return_value=(True, "/old/config.yaml"),
    )
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old/config.yaml")
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/new/config.yaml")
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config", return_value=False)
    mocker.patch("builtins.input", side_effect=EOFError)  # Skip interactive

    with patch("sys.argv", ["fetchtastic", "download"]):
        with pytest.raises(SystemExit):
            cli.main()

    captured = capsys.readouterr()
    assert "Failed to migrate configuration" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_config_load_failure(mocker):
    """Test CLI download command when config loading fails after migration."""
    # Mock successful migration but config load fails (returns None)
    mocker.patch(
        "fetchtastic.setup_config.config_exists",
        side_effect=[
            (True, "/old/config.yaml"),  # First call - old config exists
            (False, None),  # Second call - new config doesn't exist yet
        ],
    )
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old/config.yaml")
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/new/config.yaml")
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value=None,  # Config load fails - now returns None instead of raising
    )
    mocker.patch("builtins.input", side_effect=EOFError)

    with patch("sys.argv", ["fetchtastic", "download"]):
        with pytest.raises(SystemExit):
            cli.main()

    # Should handle the config load failure gracefully


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_failed_downloads_reporting(mocker):
    """Test CLI download command error reporting for failed downloads."""

    # Mock SSL/urllib3 to prevent SystemTimeWarning
    mocker.patch("urllib3.connectionpool.HTTPSConnectionPool")
    mocker.patch("urllib3.connection.HTTPSConnection")
    mocker.patch("urllib3.connection.HTTPConnection")
    mocker.patch("requests.get", return_value=mocker.MagicMock())
    mocker.patch("requests.Session.get", return_value=mocker.MagicMock())

    mock_integration = mocker.MagicMock()
    failed_downloads = [
        {
            "type": "firmware",
            "release_tag": "v2.1.0",
            "file_name": "firmware.zip",
            "url": "https://example.com/firmware.zip",
            "retryable": True,
            "http_status": 500,
            "error": "Internal Server Error",
        },
        {
            "type": "apk",
            "release_tag": "v1.5.0",
            "file_name": "app.apk",
            "url": "https://example.com/app.apk",
            "retryable": False,
            "http_status": 404,
            "error": "Not Found",
        },
    ]

    mock_integration.main.return_value = (
        [],  # downloaded_firmwares
        ["v2.0.1"],  # new_firmware_versions
        [],  # downloaded_apks
        ["v1.1.0"],  # new_apk_versions
        [],  # downloaded_firmware_prereleases
        [],  # downloaded_apk_prereleases
        failed_downloads,
        "",  # latest_firmware_version
        "",  # latest_apk_version
    )

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/config.yaml")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"LOG_LEVEL": ""})
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=mock_integration,
    )
    mocker.patch("fetchtastic.log_utils.set_log_level")
    mocker.patch("fetchtastic.utils.reset_api_tracking")
    mocker.patch("time.time", return_value=1234567890)
    mocker.patch(
        "fetchtastic.utils.get_api_request_summary", return_value={"total_requests": 5}
    )
    mocker.patch("fetchtastic.log_utils.logger")

    with patch("sys.argv", ["fetchtastic", "download"]):
        cli.main()

    # Check that the integration method is called with correct parameters
    mock_integration.log_download_results_summary.assert_called_once()
    call_args = mock_integration.log_download_results_summary.call_args
    assert call_args.kwargs["failed_downloads"] == failed_downloads
    assert len(call_args.kwargs["failed_downloads"]) == 2
    assert call_args.kwargs["new_firmware_versions"] == ["v2.0.1"]
    assert call_args.kwargs["new_apk_versions"] == ["v1.1.0"]


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_repo_clean_config_missing(mocker):
    """Test run_repo_clean when config is missing."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})
    mocker.patch("builtins.input", return_value="y")
    mock_repo_downloader = mocker.patch("fetchtastic.cli.RepositoryDownloader")

    cli.run_repo_clean({})

    # Should create RepositoryDownloader and call clean
    mock_repo_downloader.assert_called_once_with({})
    mock_repo_downloader.return_value.clean_repository_directory.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_repo_clean_logs_summary(mocker):
    """Test run_repo_clean logs cleanup summary and errors."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})
    mocker.patch("builtins.input", return_value="y")
    mock_repo_downloader = mocker.patch("fetchtastic.cli.RepositoryDownloader")
    mock_repo_downloader.return_value.clean_repository_directory.return_value = True
    mock_repo_downloader.return_value.get_cleanup_summary.return_value = {
        "removed_files": 2,
        "removed_dirs": 1,
        "errors": ["disk full"],
        "success": True,
    }
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

    cli.run_repo_clean({"BASE_DIR": "/tmp/test"})

    mock_logger.info.assert_any_call(
        "Repository cleanup summary: %d file(s), %d dir(s) removed", 2, 1
    )
    mock_logger.warning.assert_any_call("Repository cleanup error: %s", "disk full")


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_repo_clean_confirmation_cancelled(mocker, capsys):
    """Test run_repo_clean when user cancels confirmation."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})
    mock_config = {"BASE_DIR": "/tmp/test"}
    mocker.patch("builtins.input", return_value="n")  # Cancel

    cli.run_repo_clean(mock_config)

    captured = capsys.readouterr()
    assert "Clean operation cancelled" in captured.out


@pytest.mark.user_interface
@pytest.mark.unit
def test_windows_specific_cleanup_logic(mocker):
    """Test Windows-specific cleanup with winshell available."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})

    # Mock Windows environment
    mocker.patch("platform.system", return_value="Windows")

    # Mock winshell availability and functions
    mock_winshell = mocker.MagicMock()
    mocker.patch.dict("sys.modules", {"winshell": mock_winshell})
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell.startup.return_value = (
        "/Users/test/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
    )

    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/config")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old_config")
    mocker.patch(
        "fetchtastic.setup_config.WINDOWS_START_MENU_FOLDER", "/start/menu/folder"
    )

    def exists_side_effect(path):
        if path == "/start/menu/folder":
            return True
        return False

    mocker.patch("os.path.exists", side_effect=exists_side_effect)

    mock_file_entry = Mock()
    mock_file_entry.name = "shortcut.lnk"
    mock_file_entry.path = "/start/menu/folder/shortcut.lnk"
    mock_file_entry.is_file.return_value = True
    mock_file_entry.is_dir.return_value = False
    mock_file_entry.is_symlink.return_value = False

    mock_dir_entry = Mock()
    mock_dir_entry.name = "folder"
    mock_dir_entry.path = "/start/menu/folder/folder"
    mock_dir_entry.is_file.return_value = False
    mock_dir_entry.is_dir.return_value = True
    mock_dir_entry.is_symlink.return_value = False

    def scandir_side_effect(path):
        if path == "/start/menu/folder":
            return Mock(
                __enter__=Mock(return_value=[mock_file_entry, mock_dir_entry]),
                __exit__=Mock(return_value=None),
            )
        return Mock(__enter__=Mock(return_value=[]), __exit__=Mock(return_value=None))

    mocker.patch("os.scandir", side_effect=scandir_side_effect)
    mock_remove = mocker.patch("os.remove")

    def rmtree_side_effect(path):
        if path == "/start/menu/folder":
            raise OSError("fail")
        return None

    mock_shutil_rmtree = mocker.patch("shutil.rmtree", side_effect=rmtree_side_effect)
    mocker.patch("builtins.input", return_value="y")
    mocker.patch("fetchtastic.log_utils.logger")

    cli.run_clean()

    # Should have attempted Windows-specific cleanup
    mock_winshell.startup.assert_called_once()
    mock_shutil_rmtree.assert_any_call("/start/menu/folder")
    mock_remove.assert_any_call("/start/menu/folder/shortcut.lnk")
    mock_shutil_rmtree.assert_any_call("/start/menu/folder/folder")


@pytest.mark.user_interface
@pytest.mark.unit
def test_cron_job_cleanup_logic(mocker):
    """Test cron job removal on non-Windows platforms."""
    mocker.patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"})

    # Mock Linux environment
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

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
    mock_subprocess.assert_any_call(
        ["/usr/bin/crontab", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    # Should have called crontab twice - once for daily jobs, once for reboot jobs
    assert mock_popen.call_count == 2
    mock_popen.assert_any_call(
        ["/usr/bin/crontab", "-"], stdin=subprocess.PIPE, text=True
    )

    # Verify that both remove_cron_job and remove_reboot_cron_job were called
    # (they each make a popen call to update the crontab)
    communicate_calls = mock_popen_instance.communicate.call_args_list
    assert len(communicate_calls) == 2


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_topic_command_no_notifications(mocker, capsys):
    """Test topic command when notifications are not configured."""
    mocker.patch("fetchtastic.setup_config.load_config", return_value={})

    with patch("sys.argv", ["fetchtastic", "topic"]):
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
    mocker.patch("fetchtastic.cli.copy_to_clipboard_func", return_value=False)

    with patch("sys.argv", ["fetchtastic", "topic"]):
        cli.main()

    captured = capsys.readouterr()
    assert "Failed to copy to clipboard" in captured.err


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_version_command_update_available(mocker):
    """
    Verify the "version" CLI command prints available update information and the upgrade instruction when a newer version is detected.
    """
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("0.8.0", "0.9.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command", return_value="pipx upgrade fetchtastic"
    )
    mock_print = mocker.patch("builtins.print")

    with patch("sys.argv", ["fetchtastic", "version"]):
        cli.main()

    # Should print update information
    mock_print.assert_any_call("A newer version (v0.9.0) is available!")
    mock_print.assert_any_call("Run 'pipx upgrade fetchtastic' to upgrade.")


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_repo_command_no_subcommand(mocker):
    """Test repo command without subcommand."""
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/config.yaml")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value={})

    with patch("sys.argv", ["fetchtastic", "repo"]):
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                command="repo", repo_command=None
            )
            # Should not raise an exception
            cli.main()
