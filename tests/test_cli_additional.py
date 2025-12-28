import pytest

from fetchtastic import cli


@pytest.fixture
def mock_cli_dependencies(mocker):
    """
    Create a MagicMock that simulates the CLI integration and patch common external CLI dependencies.

    Patches:
    - fetchtastic.setup_config.load_config to return {"LOG_LEVEL": ""}
    - fetchtastic.log_utils.set_log_level
    - fetchtastic.cli.reset_api_tracking
    - time.time to return 1234567890
    - fetchtastic.cli.get_api_request_summary to return {"total_requests": 0}
    - fetchtastic.download.cli_integration.DownloadCLIIntegration to return the mock integration

    Parameters:
        mocker: The pytest-mock fixture used to apply patches.

    Returns:
        MagicMock: A mock integration where:
            - `main()` returns ([], [], [], [], [], "", "")
            - `update_cache()` returns True
            - `get_latest_versions()` returns a dict with empty strings for "firmware", "android", "firmware_prerelease", and "android_prerelease"
    """
    # Mock external dependencies to avoid side effects
    mocker.patch("fetchtastic.setup_config.load_config", return_value={"LOG_LEVEL": ""})
    mocker.patch("fetchtastic.log_utils.set_log_level")
    mocker.patch("fetchtastic.cli.reset_api_tracking")
    mocker.patch("time.time", return_value=1234567890)
    mocker.patch(
        "fetchtastic.cli.get_api_request_summary", return_value={"total_requests": 0}
    )

    # Mock integration instance
    mock_integration = mocker.MagicMock()
    mock_integration.main.return_value = ([], [], [], [], [], "", "")
    mock_integration.update_cache.return_value = True
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

    return mock_integration


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_setup_with_multiple_sections(mocker, mock_cli_dependencies):
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


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_help_with_specific_command(mocker, mock_cli_dependencies, capsys):
    """Test 'help' command with specific command."""
    mocker.patch("sys.argv", ["fetchtastic", "help", "repo", "browse"])

    cli.main()

    # Should capture help output
    captured = capsys.readouterr()
    assert "help" in captured.out.lower() or "usage" in captured.out.lower()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_setup_positional_sections(mocker, mock_cli_dependencies):
    """Test 'setup' command with positional sections."""
    mocker.patch("sys.argv", ["fetchtastic", "setup", "firmware", "android"])
    mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")
    mocker.patch(
        "fetchtastic.cli.get_version_info", return_value=("1.0.0", "1.0.0", False)
    )

    cli.main()

    mock_run_setup.assert_called_once()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_without_config(mocker, mock_cli_dependencies):
    """Test 'download' command when config doesn't exist."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")

    # Mock config_exists to return False
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    # Mock sys.stdin.isatty() to return True to simulate interactive terminal
    mocker.patch("sys.stdin.isatty", return_value=True)

    with pytest.raises(SystemExit):
        cli.main()

    # Should run setup since no config exists and we have an interactive terminal
    mock_run_setup.assert_called_once_with(perform_initial_download=False)
    mock_cli_dependencies.main.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_download_without_config_non_tty(mocker, mock_cli_dependencies):
    """Test 'download' command when config doesn't exist and no TTY is available."""
    mocker.patch("sys.argv", ["fetchtastic", "download"])
    mock_run_setup = mocker.patch("fetchtastic.setup_config.run_setup")

    # Mock config_exists to return False
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
    # Mock sys.stdin.isatty() to return False to simulate non-interactive terminal
    mocker.patch("sys.stdin.isatty", return_value=False)

    with pytest.raises(SystemExit):
        cli.main()

    # Should NOT run setup since no TTY is available
    mock_run_setup.assert_not_called()
    mock_cli_dependencies.main.assert_not_called()
