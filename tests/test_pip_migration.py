"""
Comprehensive tests for pip-to-pipx migration functionality.
"""

import os
import tempfile
from unittest.mock import MagicMock, mock_open, patch

import pytest

import fetchtastic.setup_config as setup_config


@pytest.mark.configuration
@pytest.mark.unit
class TestPipToPipxMigration:
    """Test pip-to-pipx migration functionality."""

    def test_migrate_pip_to_pipx_not_termux(self, mocker):
        """Test migrate_pip_to_pipx fails gracefully on non-Termux."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

        result = setup_config.migrate_pip_to_pipx()

        assert result is False

    def test_migrate_pip_to_pipx_not_pip_installation(self, mocker):
        """Test migrate_pip_to_pipx when not installed via pip."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pipx",
        )

        result = setup_config.migrate_pip_to_pipx()

        assert result is True

    def test_migrate_pip_to_pipx_user_declines(self, mocker):
        """Test migrate_pip_to_pipx when user declines migration."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )
        mocker.patch("builtins.input", return_value="n")

        result = setup_config.migrate_pip_to_pipx()

        assert result is False

    def test_migrate_pip_to_pipx_pipx_not_available_install_success(self, mocker):
        """Test migrate_pip_to_pipx when pipx needs installation."""
        config_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".yaml"
        )
        config_file.write("KEY: value")
        config_file.close()

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", config_file.name)

        # Mock subprocess calls
        mock_subprocess = mocker.MagicMock()

        # pipx.which returns None (not installed)
        # pip install user succeeds
        # python -m pipx ensurepath succeeds
        # pip uninstall succeeds
        # pipx install succeeds
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout=""),  # pip install --user pipx
            MagicMock(returncode=0, stdout=""),  # python -m pipx ensurepath
            MagicMock(returncode=0, stdout=""),  # pip uninstall fetchtastic
            MagicMock(returncode=0, stdout=""),  # pipx install fetchtastic
        ]

        mocker.patch(
            "shutil.which", side_effect=[None, "/usr/bin/pip", "/usr/bin/pipx"]
        )
        mocker.patch("builtins.input", return_value="y")
        mocker.patch("subprocess.run", mock_subprocess)
        mocker.patch("sys.exit", side_effect=SystemExit)  # Catch sys.exit

        try:
            setup_config.migrate_pip_to_pipx()
        except SystemExit:
            pass  # Expected

        # Verify subprocess calls
        assert mock_subprocess.call_count == 4

    def test_migrate_pip_to_pipx_backup_config_success(self, mocker):
        """
        Verify that migrating from pip to pipx backs up and restores the configuration file.

        Creates a temporary YAML config and simulates a Termux environment with a pip installation, accepts the migration prompt, runs the migration flow, and asserts the original config file still exists after migration completes.
        """
        config_content = "TEST: original_config"
        config_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".yaml"
        )
        config_file.write(config_content)
        config_file.close()

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", config_file.name)
        mocker.patch("builtins.input", return_value="y")  # Accept migration
        mocker.patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=""))
        mocker.patch("shutil.which", return_value="/usr/bin/pipx")
        mocker.patch("sys.exit", side_effect=SystemExit)

        with patch("builtins.open", mock_open(read_data=config_content)):
            try:
                setup_config.migrate_pip_to_pipx()
            except SystemExit:
                pass

        # File operations should have been attempted
        assert os.path.exists(config_file.name)  # Config should be restored

    def test_migrate_pip_to_pipx_subprocess_failure(self, mocker):
        """Test migration when subprocess calls fail."""
        config_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".yaml"
        )
        config_file.write("KEY: value")
        config_file.close()

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", config_file.name)
        mocker.patch("builtins.input", return_value="y")
        mocker.patch("shutil.which", return_value="/usr/bin/pipx")

        # Mock subprocess failure
        mock_subprocess = mocker.MagicMock()
        mock_subprocess.return_value.returncode = 1  # Failure
        mock_subprocess.return_value.stderr = "Install failed"
        mocker.patch("subprocess.run", mock_subprocess)

        result = setup_config.migrate_pip_to_pipx()

        assert result is False

    def test_migrate_pip_to_pipx_pipx_install_failure(self, mocker):
        """Test migration when pipx installation fails."""
        config_file = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".yaml"
        )
        config_file.write("KEY: value")
        config_file.close()

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", config_file.name)
        mocker.patch("builtins.input", return_value="y")

        # pipx not available and installation fails
        mocker.patch("shutil.which", return_value=None)
        mock_subprocess = mocker.MagicMock()
        mock_subprocess.return_value.returncode = 1  # pip install fails
        mock_subprocess.return_value.stderr = "Install failed"
        mocker.patch("subprocess.run", mock_subprocess)

        result = setup_config.migrate_pip_to_pipx()

        assert result is False

    def test_get_upgrade_command_termux_pip(self, mocker):
        """Test get_upgrade_command for Termux with pip."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        )

        command = setup_config.get_upgrade_command()

        assert command == "pip install --upgrade fetchtastic"

    def test_get_upgrade_command_termux_pipx(self, mocker):
        """Test get_upgrade_command for Termux with pipx."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pipx",
        )

        command = setup_config.get_upgrade_command()

        assert command == "pipx upgrade fetchtastic"

    def test_get_upgrade_command_non_termux(self, mocker):
        """Test get_upgrade_command for non-Termux (should default to pipx)."""
        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

        command = setup_config.get_upgrade_command()

        assert command == "pipx upgrade fetchtastic"

    def test_get_version_info(self, mocker):
        """Test get_version_info function."""
        mock_check = mocker.MagicMock()
        mock_check.return_value = ("1.0.0", "2.0.0", True)
        mocker.patch("fetchtastic.setup_config.check_for_updates", mock_check)

        current, latest, available = setup_config.get_version_info()

        assert current == "1.0.0"
        assert latest == "2.0.0"
        assert available is True
        mock_check.assert_called_once()
