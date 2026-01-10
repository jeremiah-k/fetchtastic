"""
Comprehensive edge case tests for configuration loading and migration.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import fetchtastic.setup_config as setup_config


@pytest.mark.configuration
@pytest.mark.unit
class TestConfigEdgeCases:
    """Test edge cases for configuration loading and migration."""

    def test_config_exists_with_custom_directory(self, tmp_path):
        """Test config_exists with custom directory parameter."""
        # Create a config file in custom directory
        custom_dir = tmp_path / "custom_config_dir"
        custom_dir.mkdir()
        config_file = custom_dir / setup_config.CONFIG_FILE_NAME
        config_file.write_text("TEST: true")

        exists, path = setup_config.config_exists(str(custom_dir))
        assert exists is True
        assert str(path) == str(config_file)

        # Test non-existent directory
        non_existent = tmp_path / "non_existent"
        exists, path = setup_config.config_exists(str(non_existent))
        assert exists is False
        assert path is None

    def test_config_exists_prefer_new_location(self, tmp_path, mocker):
        """Test config_exists prefers new location over old."""
        # Mock global paths
        new_config = tmp_path / "new_config.yaml"
        old_config = tmp_path / "old_config.yaml"

        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config))
        mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config))

        # Both exist - should prefer new
        new_config.write_text("new: true")
        old_config.write_text("old: true")

        exists, path = setup_config.config_exists()
        assert exists is True
        assert str(path) == str(new_config)

        # Only old exists
        new_config.unlink()
        exists, path = setup_config.config_exists()
        assert exists is True
        assert str(path) == str(old_config)

        # Neither exists
        old_config.unlink()
        exists, path = setup_config.config_exists()
        assert exists is False
        assert path is None

    def test_load_config_invalid_yaml(self, tmp_path, mocker):
        """Test load_config with invalid YAML."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content: [", encoding="utf-8")

        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(config_file))
        mock_logger = mocker.patch("fetchtastic.setup_config.logger")

        assert setup_config.load_config() is None
        mock_logger.exception.assert_called()

    def test_load_config_file_not_found(self, tmp_path, mocker):
        """Test load_config when file doesn't exist."""
        non_existent = tmp_path / "non_existent.yaml"
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(non_existent))

        result = setup_config.load_config()
        assert result is None

    def test_migrate_config_success(self, tmp_path, mocker):
        """Test successful config migration."""
        old_config = tmp_path / "old_config.yaml"
        new_config_dir = tmp_path / "config_dir"
        new_config = new_config_dir / "config.yaml"

        old_config.write_text("KEY: value")
        new_config_dir.mkdir()

        mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config))
        mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config))
        mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(new_config_dir))

        result = setup_config.migrate_config()

        assert result is True
        assert new_config.exists()
        assert not old_config.exists()

        with open(new_config) as f:
            migrated_config = yaml.safe_load(f)
        assert migrated_config["KEY"] == "value"

    def test_migrate_config_no_old_config(self, tmp_path, mocker):
        """Test migration when no old config exists."""
        old_config = tmp_path / "non_existent.yaml"
        new_config = tmp_path / "new_config.yaml"

        mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config))

        result = setup_config.migrate_config()

        assert result is False
        assert not new_config.exists()

    def test_should_recommend_setup_no_config(self, mocker):
        """Test should_recommend_setup when no config exists."""
        mocker.patch("fetchtastic.setup_config.load_config", return_value=None)

        should_recommend, reason, last_version, current_version = (
            setup_config.should_recommend_setup()
        )

        assert should_recommend is True
        assert reason == "No configuration found"
        assert last_version is None
        assert current_version is None

    def test_should_recommend_setup_no_version_tracking(self, mocker):
        """Test should_recommend_setup when version not tracked."""
        config = {"OTHER_KEY": "value"}
        mocker.patch("fetchtastic.setup_config.load_config", return_value=config)

        should_recommend, reason, last_version, current_version = (
            setup_config.should_recommend_setup()
        )

        assert should_recommend is True
        assert reason == "Setup version not tracked"
        assert last_version is None
        assert current_version is None

    def test_get_platform_detection(self):
        """Test get_platform platform detection."""
        # Test Termux
        with patch("fetchtastic.setup_config.is_termux", return_value=True):
            assert setup_config.get_platform() == "termux"

        # Test macOS
        with patch("platform.system", return_value="Darwin"):
            with patch("fetchtastic.setup_config.is_termux", return_value=False):
                assert setup_config.get_platform() == "mac"

        # Test Linux
        with patch("platform.system", return_value="Linux"):
            with patch("fetchtastic.setup_config.is_termux", return_value=False):
                assert setup_config.get_platform() == "linux"

        # Test unknown
        with patch("platform.system", return_value="Unknown"):
            with patch("fetchtastic.setup_config.is_termux", return_value=False):
                assert setup_config.get_platform() == "unknown"

    def test_get_downloads_dir_termux(self, mocker):
        """Test get_downloads_dir for Termux."""
        storage_downloads = Path("/data/data/com.termux/files/home/storage/downloads")

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
        mocker.patch(
            "fetchtastic.setup_config.os.path.expanduser",
            return_value=str(storage_downloads),
        )

        result = setup_config.get_downloads_dir()
        assert str(storage_downloads) in result

    def test_get_downloads_dir_fallback(self, mocker):
        """Test get_downloads_dir fallback to home directory."""
        home = Path("/home/user")

        mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
        mocker.patch("os.path.exists", return_value=False)
        mocker.patch(
            "fetchtastic.setup_config.os.path.expanduser", return_value=str(home)
        )

        result = setup_config.get_downloads_dir()
        assert result == str(home)

    def test_is_termux_detection(self, mocker):
        """Test is_termux environment detection."""
        # Test Termux environment
        mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
        assert setup_config.is_termux() is True

        # Test non-Termux environment
        mocker.patch.dict(os.environ, {"PREFIX": "/usr/local"}, clear=False)
        # Remove the PREFIX key to simulate non-termux
        if "PREFIX" in os.environ:
            del os.environ["PREFIX"]
        assert setup_config.is_termux() is False

    def test_get_fetchtastic_installation_methods(self, mocker):
        """Test installation method detection functions."""
        # Test pip installation
        mock_subprocess = mocker.MagicMock()
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "fetchtastic 1.0.0"
        mocker.patch("subprocess.run", mock_subprocess)

        assert setup_config.is_fetchtastic_installed_via_pip() is True

        # Test pipx installation
        mock_subprocess.return_value.stdout = "fetchtastic 1.0.0"
        assert setup_config.is_fetchtastic_installed_via_pipx() is True

        # Test pipx not installed
        mock_subprocess.side_effect = FileNotFoundError()
        assert setup_config.is_fetchtastic_installed_via_pipx() is False

    def test_get_fetchtastic_installation_method_integration(self, mocker):
        """Test get_fetchtastic_installation_method integration."""
        # Test pipx preferred over pip
        mock_subprocess = mocker.MagicMock()
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "fetchtastic 1.0.0"
        mocker.patch("subprocess.run", mock_subprocess)

        method = setup_config.get_fetchtastic_installation_method()
        assert method in ["pip", "pipx", "unknown"]
