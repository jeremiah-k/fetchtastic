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
