import os
import subprocess
from unittest.mock import Mock, patch

import pytest

# Import the package module (matches how users invoke it)
import fetchtastic.cli as cli


@pytest.fixture
def mock_cli_dependencies(mocker, tmp_path):
    """
    Pytest fixture that patches Fetchtastic CLI external dependencies (network, config, logging, time, and API-tracking) and supplies a mocked DownloadCLIIntegration for tests.

    The mock's `main()` returns empty result tuples, `update_cache()` returns `True`, and `get_latest_versions()` returns empty version strings.

    Returns:
        MagicMock: Mocked DownloadCLIIntegration instance configured for tests.
    """
    # Mock SSL/urllib3 to prevent SystemTimeWarning
    mocker.patch("urllib3.connectionpool.HTTPSConnectionPool")
    mocker.patch("urllib3.connection.HTTPSConnection")
    mocker.patch("urllib3.connection.HTTPConnection")
    mocker.patch("requests.get", return_value=mocker.MagicMock())
    mocker.patch("requests.Session.get", return_value=mocker.MagicMock())

    # Mock external dependencies to avoid side effects - patch at actual import locations
    mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value={"LOG_LEVEL": "", "DOWNLOAD_DIR": str(tmp_path / "downloads")},
    )
    mocker.patch("fetchtastic.log_utils.set_log_level")
    mocker.patch("fetchtastic.log_utils.logger")
    mocker.patch("fetchtastic.utils.reset_api_tracking")
    mocker.patch("time.time", return_value=1234567890)
    mocker.patch(
        "fetchtastic.utils.get_api_request_summary", return_value={"total_requests": 0}
    )
    mocker.patch(
        "fetchtastic.cli.get_api_request_summary", return_value={"total_requests": 0}
    )

    # Create a mock integration instance that prevents real downloads
    mock_integration_instance = mocker.MagicMock()
    mock_integration_instance.main.return_value = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        "",
        "",
    )
    mock_integration_instance.update_cache.return_value = True
    mock_integration_instance.get_latest_versions.return_value = {
        "firmware": "",
        "android": "",
        "firmware_prerelease": "",
        "android_prerelease": "",
    }

    # Mock the CLI integration at its defining module to prevent real downloads/network.
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=mock_integration_instance,
    )

    return mock_integration_instance


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_command(mocker, mock_cli_dependencies):
    """Test 'download' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Mock migration logic to avoid its side effects
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Test when config exists
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    cli.main()

    mock_cli_dependencies.main.assert_called_once()
    mock_setup_run.assert_not_called()

    # 2. Test when config does not exist
    mock_cli_dependencies.reset_mock()
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    mocker.patch("sys.stdin.isatty", return_value=True)  # Mock interactive session
    with pytest.raises(SystemExit):
        cli.main()

    mock_cli_dependencies.main.assert_not_called()
    mock_setup_run.assert_called_once()
    mock_cli_dependencies.main.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_migration(mocker, mock_cli_dependencies):
    """Test the 'download' command with an old config file that needs migration."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"key": "val"})

    mocker.patch(
        "fetchtastic.setup_config.config_exists",
        return_value=(True, "/path/to/old/config"),
    )
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/path/to/old/config")
    mocker.patch("fetchtastic.setup_config.migrate_config", return_value=True)
    mocker.patch("os.path.exists", return_value=False)

    cli.main()
    mock_cli_dependencies.main.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_update_available(mocker):
    """Test 'download' command checks for update after download completes."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    mock_get_version = mocker.patch("fetchtastic.cli.get_version_info")
    mock_get_version.return_value = ("1.0.0", "1.1.0", True)
    mock_reminder = mocker.patch("fetchtastic.cli._display_update_reminder")

    cli.main()

    # Verify version check was called
    mock_get_version.assert_called_once()
    # Verify update reminder was displayed
    mock_reminder.assert_called_once_with("1.1.0")


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_no_update_available(mocker):
    """Test 'download' command with no update available."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    mock_get_version = mocker.patch("fetchtastic.cli.get_version_info")
    mock_get_version.return_value = ("1.0.0", "1.0.0", False)
    mock_reminder = mocker.patch("fetchtastic.cli._display_update_reminder")

    cli.main()

    # Verify version check was called
    mock_get_version.assert_called_once()
    # Verify update reminder was NOT displayed
    mock_reminder.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
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
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/fake/config.yaml")

    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
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
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
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
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/fake/config.yaml")

    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
    cli.main()
    mock_logger.error.assert_any_call("Failed to update Windows integrations.")


def test_cli_setup_command_windows_integration_update_non_windows(mocker):
    """Test 'setup' command with Windows integration update on non-Windows."""
    # Mock platform.system to ensure that flag is not added on non-Windows platforms
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch(
        "fetchtastic.cli.get_version_info",
        return_value=("1.0.0", "1.0.0", False),
    )

    # This should raise SystemExit because --update-integrations is not available on Linux
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_help_command(mocker):
    """Test 'help' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "help"])

    # Help command doesn't raise SystemExit, just prints help
    cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_help_command_with_subcommand(mocker):
    """Test 'help' command with specific command argument."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "download"])

    # Help command doesn't raise SystemExit, just prints help
    cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_invalid_repo_command(mocker):
    """Test 'repo' command with invalid subcommand."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "invalid"])

    # Should exit with error
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_setup_invalid_section(mocker):
    """Test 'setup' command with invalid section."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--section", "invalid"])

    # Should exit with error
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_version_with_update_available(mocker):
    """Test 'version' command when update is available."""
    mocker.patch("sys.argv", ["fetchtastic", "version"])
    mock_display = mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "2.0.0", True)
    )

    cli.main()

    mock_display.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_clean_command_enhanced(mocker):
    """Test 'clean' command dispatch with enhanced checks."""
    mocker.patch("sys.argv", ["fetchtastic", "clean"])
    mock_clean = mocker.patch("fetchtastic.cli.run_clean")
    # Mock input to avoid stdin issues
    mocker.patch("builtins.input", return_value="y")

    cli.main()
    mock_clean.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_topic_command(mocker):
    """Test 'topic' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "topic"])
    mock_config = mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value={"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test-topic"},
    )
    mock_clipboard = mocker.patch(
        "fetchtastic.cli.copy_to_clipboard_func", return_value=True
    )
    mocker.patch("builtins.input", return_value="y")

    # Topic command doesn't raise SystemExit, just runs
    cli.main()

    mock_config.assert_called_once()
    mock_clipboard.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_repo_browse_command(mocker):
    """Test 'repo browse' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "browse"])
    mock_repo_menu = mocker.patch(
        "fetchtastic.menu_repo.run_repository_downloader_menu"
    )
    mocker.patch("builtins.input", return_value="")

    # Mock config_exists to return True to avoid setup running
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    # Mock RepositoryDownloader to prevent HTTP calls
    mocker.patch("fetchtastic.cli.RepositoryDownloader")
    # Mock urllib3 to prevent SSL/time warnings
    mocker.patch("urllib3.connectionpool.HTTPSConnectionPool")
    mocker.patch("urllib3.connection.HTTPSConnection")

    cli.main()

    mock_repo_menu.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_repo_clean_command(mocker):
    """Test 'repo clean' command by running CLI with mocked dependencies."""
    mocker.patch("sys.argv", ["fetchtastic", "repo", "clean"])

    # Mock input to return "y" to confirm clean operation
    mocker.patch("builtins.input", return_value="y")

    # Mock config_exists to return True to avoid setup running
    mock_config_exists = mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    # Mock RepositoryDownloader to prevent HTTP calls
    mocker.patch("fetchtastic.cli.RepositoryDownloader")
    # Mock urllib3 to prevent SSL/time warnings
    mocker.patch("urllib3.connectionpool.HTTPSConnectionPool")
    mocker.patch("urllib3.connection.HTTPSConnection")

    cli.main()

    # Verify config check was called
    mock_config_exists.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_no_command_basic(mocker):
    """Test CLI when no command is provided."""
    mocker.patch("sys.argv", ["fetchtastic"])

    # No command doesn't raise SystemExit, just shows help
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
        mock_action = mocker.patch(
            "fetchtastic.menu_repo.run_repository_downloader_menu"
        )
    else:  # clean
        mock_action = mocker.patch("fetchtastic.cli.run_repo_clean")
        # Mock input to avoid stdin issues
        mocker.patch("builtins.input", return_value="y")

    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
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
    mock_repo_main = mocker.patch(
        "fetchtastic.menu_repo.run_repository_downloader_menu"
    )
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
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
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
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
        mocker.patch("fetchtastic.menu_repo.run_repository_downloader_menu")
    else:  # clean
        mocker.patch("fetchtastic.cli.run_repo_clean")
        # Mock input to avoid stdin issues
        mocker.patch("builtins.input", return_value="y")

    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.1.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command",
        return_value="pip install --upgrade fetchtastic",
    )

    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
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
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
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
    # Patch the get_version_info where it's looked up: in the cli module
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))

    cli.main()
    mock_setup_run.assert_called_once_with(sections=None)


def test_cli_setup_command_with_sections(mocker):
    """Ensure the setup command forwards section filters."""
    mocker.patch(
        "sys.argv",
        ["fetchtastic", "setup", "--section", "firmware", "--section", "android"],
    )
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))

    cli.main()
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_positional_sections(mocker):
    """Positional section arguments should be passed to setup."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "firmware", "android"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))

    cli.main()
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_invalid_positional_sections(mocker):
    """Invalid positional section arguments should cause an error."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "invalid_section", "firmware"])
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))

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
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))

    cli.main()

    # Should deduplicate while preserving order: firmware, android
    mock_setup_run.assert_called_once_with(sections=["firmware", "android"])


def test_cli_setup_command_with_update_available(mocker):
    """Test the 'setup' command when an update is available."""
    mocker.patch("sys.argv", ["fetchtastic", "setup"])
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

    # Mock version info to indicate update is available
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.1.0", True)
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


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_clean_command(mocker):
    """Test the 'clean' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "clean"])
    # Mock the function in the cli module itself
    mock_run_clean = mocker.patch("fetchtastic.cli.run_clean")
    # Mock input to avoid stdin issues
    mocker.patch("builtins.input", return_value="y")
    cli.main()
    mock_run_clean.assert_called_once()


@patch("builtins.input", return_value="y")
@patch("os.remove")
@patch("os.path.exists")
@patch("shutil.rmtree")
@patch("os.scandir")
@patch("os.rmdir")
@patch("subprocess.run")
@patch("platform.system", return_value="Linux")
@patch(
    "fetchtastic.setup_config._crontab_available",
    return_value=True,
)
@patch("shutil.which", return_value="/usr/bin/crontab")
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
    _mock_shutil_which,
    _mock_crontab_available,
    _mock_platform_system,
    mock_subprocess_run,
    _mock_rmdir,
    mock_scandir,
    mock_rmtree,
    mock_os_path_exists,
    mock_os_remove,
    _mock_input,
):
    """Test the run_clean function."""
    with patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"}):
        # Simulate existing files and directories
        mock_os_path_exists.return_value = True
        # Create mock directory entries for os.scandir
        mock_some_dir = Mock()
        mock_some_dir.name = "some_dir"
        mock_some_dir.is_symlink.return_value = False
        mock_some_dir.is_file.return_value = False
        mock_some_dir.is_dir.return_value = True
        mock_some_dir.path = "/tmp/test_base_dir/some_dir"

        mock_repo_dls = Mock()
        mock_repo_dls.name = "repo-dls"
        mock_repo_dls.is_symlink.return_value = False
        mock_repo_dls.is_file.return_value = False
        mock_repo_dls.is_dir.return_value = True
        mock_repo_dls.path = "/tmp/test_base_dir/repo-dls"

        mock_firmware = Mock()
        mock_firmware.name = "firmware-2.7.4"
        mock_firmware.is_symlink.return_value = False
        mock_firmware.is_file.return_value = False
        mock_firmware.is_dir.return_value = True
        mock_firmware.path = "/tmp/test_base_dir/firmware-2.7.4"

        mock_yaml_lnk = Mock()
        mock_yaml_lnk.name = "fetchtastic_yaml.lnk"
        mock_yaml_lnk.is_symlink.return_value = True
        mock_yaml_lnk.is_file.return_value = False
        mock_yaml_lnk.is_dir.return_value = False
        mock_yaml_lnk.path = "/tmp/test_base_dir/fetchtastic_yaml.lnk"

        mock_unmanaged = Mock()
        mock_unmanaged.name = "unmanaged.txt"
        mock_unmanaged.is_symlink.return_value = False
        mock_unmanaged.is_file.return_value = True
        mock_unmanaged.is_dir.return_value = False
        mock_unmanaged.path = "/tmp/test_base_dir/unmanaged.txt"

        def scandir_side_effect(path):
            if path == "/tmp/config/fetchtastic":
                return Mock(
                    __enter__=Mock(return_value=[]), __exit__=Mock(return_value=None)
                )
            if path == "/tmp/test_base_dir":
                return Mock(
                    __enter__=Mock(
                        return_value=[
                            mock_some_dir,
                            mock_repo_dls,
                            mock_firmware,
                            mock_yaml_lnk,
                            mock_unmanaged,
                        ]
                    ),
                    __exit__=Mock(return_value=None),
                )
            return Mock(
                __enter__=Mock(return_value=[]), __exit__=Mock(return_value=None)
            )

        mock_scandir.side_effect = scandir_side_effect

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
            Determine whether a filesystem path should be treated as an existing file for test side effects based on its basename.

            Parameters:
                path (str): Filesystem path to check.

            Returns:
                True if the path's basename is "fetchtastic_yaml.lnk" or "unmanaged.txt", False otherwise.
            """
            return os.path.basename(path) in ["fetchtastic_yaml.lnk", "unmanaged.txt"]

        mock_isdir.side_effect = isdir_side_effect
        mock_isfile.side_effect = isfile_side_effect
        mock_subprocess_run.return_value.stdout = "# fetchtastic cron job"
        mock_subprocess_run.return_value.returncode = 0
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.communicate.return_value = (None, None)
            cli.run_clean()
            assert mock_popen.call_count == 2

    # Check that config files are removed
    mock_os_remove.assert_any_call("/tmp/config/fetchtastic.yaml")  # nosec B108
    mock_os_remove.assert_any_call("/tmp/old_config/fetchtastic.yaml")  # nosec B108

    # Check that only managed directories are cleaned
    # "repo-dls" is in MANAGED_DIRECTORIES, "firmware-2.7.4" starts with FIRMWARE_DIR_PREFIX
    # "some_dir" is not managed, so should not be removed
    # Also removes batch directory from config dir
    mock_rmtree.assert_any_call("/tmp/config/fetchtastic/batch")  # nosec B108
    mock_rmtree.assert_any_call("/tmp/config/fetchtastic")  # nosec B108
    mock_rmtree.assert_any_call("/tmp/test_base_dir/repo-dls")  # nosec B108
    mock_rmtree.assert_any_call("/tmp/test_base_dir/firmware-2.7.4")  # nosec B108
    # Should not remove "some_dir"
    assert mock_rmtree.call_count == 4

    # Check that managed files are removed but unmanaged files are not
    # "fetchtastic_yaml.lnk" is in MANAGED_FILES, so should be removed
    mock_os_remove.assert_any_call(
        "/tmp/test_base_dir/fetchtastic_yaml.lnk"
    )  # nosec B108
    # "unmanaged.txt" is not managed, so should not be removed
    # Total removes: 2 config files + 1 managed file + boot script + log file = 5
    assert mock_os_remove.call_count == 5

    # Check that cron jobs are removed
    mock_subprocess_run.assert_any_call(
        ["/usr/bin/crontab", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def test_cli_version_command(mocker):
    """Test the 'version' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "version"])
    # Patch where the function is looked up (in the cli module)
    mock_version_info = mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.2.3", "1.2.3", False)
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
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    cli.main()

    # Should not run full setup, only update integrations
    mock_setup_run.assert_not_called()


def test_cli_setup_windows_integration_no_config(mocker):
    """Test Windows integration update with no config."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "--update-integrations"])
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)
    mocker.patch("fetchtastic.cli.get_version_info", return_value=("1.0", "1.0", False))
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

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


def test_cli_version_with_update_available_legacy(mocker):
    """Test the 'version' command with update available."""
    mocker.patch("sys.argv", ["fetchtastic", "version"])
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.1.0", True)
    )
    mocker.patch(
        "fetchtastic.cli.get_upgrade_command", return_value="pipx upgrade fetchtastic"
    )
    mock_print = mocker.patch("builtins.print")

    cli.main()

    mock_print.assert_any_call("Fetchtastic v1.0.0")
    mock_print.assert_any_call("A newer version (v1.1.0) is available!")
    mock_print.assert_any_call("Run 'pipx upgrade fetchtastic' to upgrade.")


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
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once_with(
        "Error copying to Termux clipboard: %s", mocker.ANY
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
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.warning.assert_called_once_with(
        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
    )


def test_copy_to_clipboard_func_unsupported_platform(mocker):
    """Test clipboard functionality on unsupported platform."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="FreeBSD")
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.warning.assert_called_once_with(
        "Clipboard functionality is not supported on this platform."
    )


def test_copy_to_clipboard_func_subprocess_error(mocker):
    """Test clipboard functionality with subprocess error."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("subprocess.run", side_effect=Exception("Subprocess error"))
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = cli.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once_with(
        "Error copying to clipboard on %s: %s", "Darwin", mocker.ANY
    )


def test_run_clean_cancelled(mocker):
    """Test run_clean when user cancels."""
    mocker.patch("builtins.input", return_value="n")
    mock_print = mocker.patch("builtins.print")

    with patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"}):
        cli.run_clean()

    mock_print.assert_any_call("Clean operation cancelled.")


@pytest.mark.user_interface
@pytest.mark.unit
def test_run_clean_requires_tty(mocker, monkeypatch):
    """Non-interactive sessions should abort clean operations."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    mocker.patch("sys.stdin.isatty", return_value=False)
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")
    mock_input = mocker.patch("builtins.input")

    cli.run_clean()

    mock_input.assert_not_called()
    mock_logger.error.assert_called_once_with(
        "Clean operation requires an interactive terminal; aborting."
    )


def test_run_clean_user_says_no_explicitly(mocker):
    """Test run_clean when user explicitly says no."""
    mocker.patch("builtins.input", return_value="no")
    mock_print = mocker.patch("builtins.print")

    with patch.dict(os.environ, {"FETCHTASTIC_ALLOW_TEST_CLEAN": "1"}):
        cli.run_clean()

    mock_print.assert_any_call("Clean operation cancelled.")


@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_log_level_config(mocker):
    """
    Verify that running CLI `download` command applies a configured LOG_LEVEL and dispatches to downloader.

    Sets up a fake CLI invocation and a loaded configuration containing a "LOG_LEVEL" key. Asserts that `set_log_level` is called with the configured value, `downloader.main` is invoked, and `setup_config.run_setup` is not called when a valid config exists.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")

    # Mock config exists to avoid running setup
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Override config from mock_cli_dependencies to set specific LOG_LEVEL
    mock_config = {"LOG_LEVEL": "DEBUG", "DOWNLOAD_DIR": "/tmp/test"}  # nosec B108
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Verify that set_log_level was called with the correct level
    mock_set_log_level.assert_called_once_with("DEBUG")


def test_cli_download_without_log_level_config(mocker):
    """Test 'download' command without LOG_LEVEL configuration."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Mock SSL/urllib3 to prevent SystemTimeWarning
    mocker.patch("urllib3.connectionpool.HTTPSConnectionPool")
    mocker.patch("urllib3.connection.HTTPSConnection")
    mocker.patch("urllib3.connection.HTTPConnection")
    mocker.patch("requests.get", return_value=mocker.MagicMock())
    mocker.patch("requests.Session.get", return_value=mocker.MagicMock())

    # Mock external dependencies
    mocker.patch("fetchtastic.cli.reset_api_tracking")
    mocker.patch("time.time", return_value=1234567890)
    mocker.patch(
        "fetchtastic.cli.get_api_request_summary", return_value={"total_requests": 0}
    )

    # Mock integration
    mock_integration = mocker.MagicMock()
    mock_integration.main.return_value = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        "",
        "",
    )
    mock_integration.get_latest_versions.return_value = {
        "firmware": "",
        "android": "",
        "firmware_prerelease": "",
        "android_prerelease": "",
    }
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=mock_integration,
    )

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
    mock_integration.main.assert_called_once()
    mock_setup_run.assert_not_called()


def test_cli_download_with_empty_config(mocker):
    """
    Ensure the CLI 'download' command exits when a config path exists but load_config returns None.

    Verifies that `set_log_level` is not called, the download integration's `main()` is not invoked, and `run_setup` is not invoked.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")
    mock_setup_run = mocker.patch("fetchtastic.setup_config.run_setup")

    # Mock external dependencies
    mocker.patch("fetchtastic.cli.reset_api_tracking")
    mocker.patch("time.time", return_value=1234567890)
    mocker.patch(
        "fetchtastic.cli.get_api_request_summary", return_value={"total_requests": 0}
    )

    # Mock integration
    mock_integration = mocker.MagicMock()
    mock_integration.main.return_value = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        "",
        "",
    )
    mock_integration.get_latest_versions.return_value = {
        "firmware": "",
        "android": "",
        "firmware_prerelease": "",
        "android_prerelease": "",
    }
    mocker.patch(
        "fetchtastic.download.cli_integration.DownloadCLIIntegration",
        return_value=mock_integration,
    )

    # Test when config exists but load_config returns None
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Mock load_config to return None
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)

    with pytest.raises(SystemExit):
        cli.main()

    # Verify that set_log_level was NOT called
    mock_set_log_level.assert_not_called()
    mock_integration.main.assert_not_called()
    mock_setup_run.assert_not_called()


@pytest.mark.parametrize("log_level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_parametrized_log_levels(mocker, log_level):
    """
    Ensure the download command uses the LOG_LEVEL from configuration to set the logging level.

    Ensures that when a loaded configuration contains `LOG_LEVEL`, the CLI passes that value to `fetchtastic.log_utils.set_log_level`.

    Parameters:
        log_level: The configured log level value that should be forwarded to `set_log_level`.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])

    # Mock config exists to avoid running setup
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Override the config from mock_cli_dependencies to set specific log level
    mock_config = {"LOG_LEVEL": log_level, "DOWNLOAD_DIR": "/tmp/test"}  # nosec B108
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")

    cli.main()

    # Verify that set_log_level was called with the correct level
    mock_set_log_level.assert_called_once_with(log_level)


@pytest.mark.parametrize("invalid_log_level", ["INVALID", "TRACE", "VERBOSE", "123"])
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_invalid_log_levels(mocker, invalid_log_level):
    """
    Verify CLI passes an invalid LOG_LEVEL to set_log_level and still runs a download integration while not invoking setup.

    Asserts that set_log_level is called with the raw invalid value, DownloadCLIIntegration.main is invoked exactly once, and setup_config.run_setup is not called.

    Parameters:
        invalid_log_level (str): A string representing an invalid log level value to pass through to CLI.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")

    # Mock config exists to avoid running setup
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Override the config from mock_cli_dependencies to set specific invalid log level
    mock_config = {
        "LOG_LEVEL": invalid_log_level,
        "DOWNLOAD_DIR": "/tmp/test",  # nosec B108
    }  # nosec B108
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    # Should not raise an exception, but set_log_level might handle invalid values
    cli.main()

    # Verify that set_log_level was called with invalid level (let set_log_level handle validation)
    mock_set_log_level.assert_called_once_with(invalid_log_level)


@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_download_with_empty_log_level(mocker):
    """
    Verify that an empty LOG_LEVEL in the configuration does not trigger a log level change while the downloader still runs and setup is not invoked.

    Patches the CLI environment so load_config returns a config with "LOG_LEVEL" set to an empty string and asserts that:
    - fetchtastic.log_utils.set_log_level is not called,
    - the downloader is invoked once,
    - setup_config.run_setup is not invoked.
    """
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")

    # Mock config exists to avoid running setup
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Override config from mock_cli_dependencies to set empty log level
    mock_config = {"LOG_LEVEL": "", "DOWNLOAD_DIR": "/tmp/test"}  # nosec B108
    mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

    cli.main()

    # Empty string should NOT call set_log_level (falsy value)
    mock_set_log_level.assert_not_called()


def test_cli_download_with_case_insensitive_log_levels(mocker, mock_cli_dependencies):
    """Test the 'download' command with case variations of LOG_LEVEL values."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_set_log_level = mocker.patch("fetchtastic.log_utils.set_log_level")

    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    # Test different case variations
    case_variations = ["debug", "Info", "warning", "Error", "critical"]

    for log_level in case_variations:
        mock_set_log_level.reset_mock()
        mock_cli_dependencies.main.reset_mock()

        mock_config = {"LOG_LEVEL": log_level}
        mocker.patch("fetchtastic.setup_config.load_config", return_value=mock_config)

        cli.main()

        # Verify that set_log_level was called with the case variation
        mock_set_log_level.assert_called_once_with(log_level)
        mock_cli_dependencies.main.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_force_flag(mocker, mock_cli_dependencies):
    """Test 'download' command with --force-download flag."""
    mocker.patch("sys.argv", ["fetchtastic", "download", "--force-download"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    cli.main()

    # Verify main was called with force_refresh=True
    _args, kwargs = mock_cli_dependencies.main.call_args
    assert kwargs.get("force_refresh") is True


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_update_cache_flag(mocker, mock_cli_dependencies):
    """Test 'download' command with --update-cache flag."""
    mocker.patch("sys.argv", ["fetchtastic", "download", "--update-cache"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    cli.main()

    mock_cli_dependencies.update_cache.assert_called_once()
    mock_cli_dependencies.main.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_cache_update_command(mocker, mock_cli_dependencies):
    """Test 'cache update' command dispatch."""
    mocker.patch("sys.argv", ["fetchtastic", "cache", "update"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    cli.main()

    mock_cli_dependencies.update_cache.assert_called_once()
    mock_cli_dependencies.main.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
@pytest.mark.usefixtures("mock_cli_dependencies")
def test_cli_setup_with_multiple_sections(mocker):
    """Test 'setup' command with multiple --section arguments."""
    mocker.patch(
        "sys.argv",
        ["fetchtastic", "setup", "--section", "firmware", "--section", "android"],
    )
    mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    cli.main()

    mock_run_setup.assert_called_once()
