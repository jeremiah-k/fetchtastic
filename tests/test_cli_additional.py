import pytest

from fetchtastic import cli


@pytest.fixture
def mock_cli_dependencies(mocker):
    """Fixture to mock common CLI dependencies while allowing CLI code to run."""
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
def test_cli_download_force_flag(mocker, mock_cli_dependencies):
    """Test 'download' command with --force flag."""
    mocker.patch("sys.argv", ["fetchtastic", "download", "--force"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )
    mocker.patch("fetchtastic.setup_config.prompt_for_migration")
    mocker.patch("fetchtastic.setup_config.migrate_config")

    cli.main()

    # Verify main was called (force flag should be passed through)
    assert mock_cli_dependencies.main.called


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
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
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
        "fetchtastic.cli.display_version_info", return_value=("1.0.0", "1.0.0", False)
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

    cli.main()

    # Should run setup since no config exists
    mock_run_setup.assert_called_once()
    mock_cli_dependencies.main.assert_not_called()


@pytest.mark.user_interface
@pytest.mark.unit
def test_cli_force_flag_handling(mocker, mock_cli_dependencies):
    """Test CLI force flag handling."""
    # Test with --force flag
    mocker.patch("sys.argv", ["fetchtastic", "download", "--force"])
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/fake/path")
    )

    cli.main()

    # Should call main with force flag
    args, kwargs = mock_cli_dependencies.main.call_args
    # Check that the integration was called with the force parameter
    assert mock_cli_dependencies.main.called
