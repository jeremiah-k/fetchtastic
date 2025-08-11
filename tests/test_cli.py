import sys
import pytest
from unittest.mock import patch

from fetchtastic import cli

def test_cli_download_command(mocker):
    """Test the 'download' command dispatch."""
    mocker.patch('sys.argv', ['fetchtastic', 'download'])
    mock_downloader_main = mocker.patch('fetchtastic.downloader.main')
    mock_setup_run = mocker.patch('fetchtastic.setup_config.run_setup')

    # 1. Test when config exists
    mocker.patch('fetchtastic.setup_config.config_exists', return_value=(True, '/fake/path'))
    # Mock the migration logic to avoid its side effects
    mocker.patch('fetchtastic.setup_config.prompt_for_migration')
    mocker.patch('fetchtastic.setup_config.migrate_config')
    cli.main()
    mock_downloader_main.assert_called_once()
    mock_setup_run.assert_not_called()

    # 2. Test when config does not exist
    mock_downloader_main.reset_mock()
    mocker.patch('fetchtastic.setup_config.config_exists', return_value=(False, None))
    cli.main()
    mock_setup_run.assert_called_once()
    mock_downloader_main.assert_not_called()


def test_cli_setup_command(mocker):
    """Test the 'setup' command dispatch."""
    mocker.patch('sys.argv', ['fetchtastic', 'setup'])
    mock_setup_run = mocker.patch('fetchtastic.setup_config.run_setup')
    # Patch the display_version_info where it's looked up: in the cli module
    mocker.patch('fetchtastic.cli.display_version_info', return_value=('1.0', '1.0', False))

    cli.main()
    mock_setup_run.assert_called_once()


def test_cli_repo_browse_command(mocker):
    """Test the 'repo browse' command dispatch."""
    mocker.patch('sys.argv', ['fetchtastic', 'repo', 'browse'])
    mock_repo_main = mocker.patch('fetchtastic.repo_downloader.main')
    mocker.patch('fetchtastic.setup_config.config_exists', return_value=(True, '/fake/path'))
    mocker.patch('fetchtastic.setup_config.load_config', return_value={'key': 'val'})
    mocker.patch('fetchtastic.cli.display_version_info', return_value=('1.0', '1.0', False))

    cli.main()
    mock_repo_main.assert_called_once()


def test_cli_clean_command(mocker):
    """Test the 'clean' command dispatch."""
    mocker.patch('sys.argv', ['fetchtastic', 'clean'])
    # Mock the function in the cli module itself
    mock_run_clean = mocker.patch('fetchtastic.cli.run_clean')
    cli.main()
    mock_run_clean.assert_called_once()


def test_cli_version_command(mocker):
    """Test the 'version' command dispatch."""
    mocker.patch('sys.argv', ['fetchtastic', 'version'])
    # Patch where the function is looked up (in the cli module)
    mock_version_info = mocker.patch('fetchtastic.cli.display_version_info', return_value=('1.2.3', '1.2.3', False))
    cli.main()
    mock_version_info.assert_called_once()


def test_cli_no_command(mocker):
    """Test running with no command."""
    mocker.patch('sys.argv', ['fetchtastic'])
    # The ArgumentParser instance is local to cli.main, so we patch the class
    mock_print_help = mocker.patch('argparse.ArgumentParser.print_help')

    cli.main()

    # Assert that print_help was called on an instance of the parser
    mock_print_help.assert_called_once()
