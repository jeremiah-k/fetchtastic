import importlib
import os
import platform
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fetchtastic import setup_config
from tests.test_constants import TEST_CONFIG

# Utility function tests


@pytest.mark.configuration
@pytest.mark.unit
def test_is_termux_true():
    """Test is_termux returns True when PREFIX contains com.termux."""
    with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"}):
        assert setup_config.is_termux() is True


@pytest.mark.configuration
@pytest.mark.unit
def test_is_termux_false():
    """Test is_termux returns False when PREFIX doesn't contain com.termux."""
    with patch.dict(os.environ, {"PREFIX": "/usr/local"}, clear=True):
        assert setup_config.is_termux() is False


@pytest.mark.configuration
@pytest.mark.unit
def test_is_termux_no_prefix():
    """Test is_termux returns False when PREFIX is not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert setup_config.is_termux() is False


@pytest.mark.configuration
@pytest.mark.unit
@pytest.mark.parametrize(
    "is_termux_val, platform_system, expected",
    [
        (True, "Linux", "termux"),
        (False, "Darwin", "mac"),
        (False, "Linux", "linux"),
        (False, "Windows", "unknown"),
    ],
)
def test_get_platform(mocker, is_termux_val, platform_system, expected):
    """Test the platform detection logic."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=is_termux_val)
    mocker.patch("platform.system", return_value=platform_system)
    assert setup_config.get_platform() == expected


@pytest.mark.configuration
@pytest.mark.unit
@patch("subprocess.run")
def test_is_fetchtastic_installed_via_pip_true(mock_run):
    """Test detection of pip installation."""
    mock_run.return_value = MagicMock(stdout="fetchtastic 1.0.0", returncode=0)
    assert setup_config.is_fetchtastic_installed_via_pip() is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("subprocess.run")
def test_is_fetchtastic_installed_via_pip_false(mock_run):
    """Test when fetchtastic is not installed via pip."""
    mock_run.return_value = MagicMock(stdout="other-package 1.0.0", returncode=0)
    assert setup_config.is_fetchtastic_installed_via_pip() is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("subprocess.run")
def test_is_fetchtastic_installed_via_pip_error(mock_run):
    """Test pip check handles errors gracefully."""
    mock_run.side_effect = subprocess.SubprocessError("pip not found")
    assert setup_config.is_fetchtastic_installed_via_pip() is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("subprocess.run")
def test_is_fetchtastic_installed_via_pipx_true(mock_run):
    """Test detection of pipx installation."""
    mock_run.return_value = MagicMock(stdout="fetchtastic 1.0.0", returncode=0)
    assert setup_config.is_fetchtastic_installed_via_pipx() is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("subprocess.run")
def test_is_fetchtastic_installed_via_pipx_false(mock_run):
    """Test when fetchtastic is not installed via pipx."""
    mock_run.return_value = MagicMock(stdout="other-package 1.0.0", returncode=0)
    assert setup_config.is_fetchtastic_installed_via_pipx() is False


@pytest.mark.configuration
@pytest.mark.unit
def test_get_fetchtastic_installation_method_pipx():
    """Test installation method detection for pipx."""
    with patch(
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx", return_value=True
    ):
        with patch(
            "fetchtastic.setup_config.is_fetchtastic_installed_via_pip",
            return_value=False,
        ):
            assert setup_config.get_fetchtastic_installation_method() == "pipx"


@pytest.mark.configuration
@pytest.mark.unit
def test_get_fetchtastic_installation_method_pip():
    """Test installation method detection for pip."""
    with patch(
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx", return_value=False
    ):
        with patch(
            "fetchtastic.setup_config.is_fetchtastic_installed_via_pip",
            return_value=True,
        ):
            assert setup_config.get_fetchtastic_installation_method() == "pip"


@pytest.mark.configuration
@pytest.mark.unit
def test_get_fetchtastic_installation_method_unknown():
    """Test installation method detection when unknown."""
    with patch(
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx", return_value=False
    ):
        with patch(
            "fetchtastic.setup_config.is_fetchtastic_installed_via_pip",
            return_value=False,
        ):
            assert setup_config.get_fetchtastic_installation_method() == "unknown"


# Configuration file tests


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_no_file(tmp_path, mocker):
    """Test that load_config returns None when no config file exists."""
    # Patch the config file paths to point to our temp directory
    mocker.patch(
        "fetchtastic.setup_config.CONFIG_FILE", str(tmp_path / "new_config.yaml")
    )
    mocker.patch(
        "fetchtastic.setup_config.OLD_CONFIG_FILE", str(tmp_path / "old_config.yaml")
    )

    assert setup_config.load_config() is None


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_new_location(tmp_path, mocker):
    """Test loading config from the new (platformdirs) location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    config_data = {"SAVE_APKS": True}
    with open(new_config_path, "w") as f:
        yaml.dump(config_data, f)

    config = setup_config.load_config()
    assert config["SAVE_APKS"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_old_location_with_migration(tmp_path, mocker):
    """Test loading config from old location and automatic migration."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    # Create config in old location
    config_data = TEST_CONFIG.copy()
    with open(old_config_path, "w") as f:
        yaml.dump(config_data, f)

    config = setup_config.load_config()
    assert config["BASE_DIR"] == TEST_CONFIG["BASE_DIR"]
    # Check that migration occurred
    assert new_config_path.exists()


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_invalid_yaml(tmp_path, mocker):
    """Test loading config with invalid YAML."""
    new_config_path = tmp_path / "new_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))

    # Create invalid YAML
    with open(new_config_path, "w") as f:
        f.write("invalid: yaml: content: [")

    config = setup_config.load_config()
    assert config is None


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_new_location(tmp_path, mocker):
    """Test config_exists detects config in new location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    # Create config in new location
    new_config_path.write_text("test: config")

    exists, path = setup_config.config_exists()
    assert exists is True
    assert path == str(new_config_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_old_location(tmp_path, mocker):
    """Test config_exists detects config in old location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    # Create config in old location only
    old_config_path.write_text("test: config")

    exists, path = setup_config.config_exists()
    assert exists is True
    assert path == str(old_config_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_none(tmp_path, mocker):
    """Test config_exists when no config exists."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    exists, path = setup_config.config_exists()
    assert exists is False
    assert path is None


# Version and upgrade tests


@pytest.mark.configuration
@pytest.mark.unit
def test_get_upgrade_command_termux_pip():
    """Test upgrade command for Termux with pip installation."""
    with patch("fetchtastic.setup_config.is_termux", return_value=True):
        with patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pip",
        ):
            assert (
                setup_config.get_upgrade_command()
                == "pip install --upgrade fetchtastic"
            )


@pytest.mark.configuration
@pytest.mark.unit
def test_get_upgrade_command_termux_pipx():
    """Test upgrade command for Termux with pipx installation."""
    with patch("fetchtastic.setup_config.is_termux", return_value=True):
        with patch(
            "fetchtastic.setup_config.get_fetchtastic_installation_method",
            return_value="pipx",
        ):
            assert setup_config.get_upgrade_command() == "pipx upgrade fetchtastic"


@pytest.mark.configuration
@pytest.mark.unit
def test_get_upgrade_command_non_termux():
    """Test upgrade command for non-Termux platforms."""
    with patch("fetchtastic.setup_config.is_termux", return_value=False):
        assert setup_config.get_upgrade_command() == "pipx upgrade fetchtastic"


@pytest.mark.configuration
@pytest.mark.integration
@patch("requests.get")
@patch("importlib.metadata.version")
def test_check_for_updates_available(mock_version, mock_get):
    """Test update check when newer version is available."""
    mock_version.return_value = "1.0.0"
    mock_response = MagicMock()
    mock_response.json.return_value = {"info": {"version": "1.1.0"}}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    current, latest, available = setup_config.check_for_updates()
    assert current == "1.0.0"
    assert latest == "1.1.0"
    assert available is True


@pytest.mark.configuration
@pytest.mark.integration
@patch("requests.get")
@patch("importlib.metadata.version")
def test_check_for_updates_current(mock_version, mock_get):
    """Test update check when current version is latest."""
    mock_version.return_value = "1.0.0"
    mock_response = MagicMock()
    mock_response.json.return_value = {"info": {"version": "1.0.0"}}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    current, latest, available = setup_config.check_for_updates()
    assert current == "1.0.0"
    assert latest == "1.0.0"
    assert available is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("requests.get")
def test_check_for_updates_network_error(mock_get):
    """Test update check handles network errors gracefully."""
    mock_get.side_effect = Exception("Network error")

    current, latest, available = setup_config.check_for_updates()
    assert latest is None
    assert available is False


# Platform-specific directory tests


@pytest.mark.configuration
@pytest.mark.unit
def test_get_downloads_dir_termux():
    """Test downloads directory for Termux."""
    with patch("fetchtastic.setup_config.is_termux", return_value=True):
        downloads_dir = setup_config.get_downloads_dir()
        assert "storage/downloads" in downloads_dir


@pytest.mark.configuration
@pytest.mark.unit
def test_get_downloads_dir_non_termux():
    """Test downloads directory for non-Termux platforms."""
    with patch("fetchtastic.setup_config.is_termux", return_value=False):
        with patch(
            "platformdirs.user_downloads_dir", return_value="/home/user/Downloads"
        ):
            downloads_dir = setup_config.get_downloads_dir()
            assert downloads_dir == "/home/user/Downloads"


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_old_location(tmp_path, mocker):
    """Test loading config from the old location when new one doesn't exist."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    config_data = {"SAVE_FIRMWARE": True}
    with open(old_config_path, "w") as f:
        yaml.dump(config_data, f)

    config = setup_config.load_config()
    assert config["SAVE_FIRMWARE"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_prefers_new_location(tmp_path, mocker):
    """Test that the new config location is preferred when both exist."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))

    new_config_data = {"key": "new"}
    old_config_data = {"key": "old"}
    with open(new_config_path, "w") as f:
        yaml.dump(new_config_data, f)
    with open(old_config_path, "w") as f:
        yaml.dump(old_config_data, f)

    config = setup_config.load_config()
    assert config["key"] == "new"


def test_migrate_config(tmp_path, mocker):
    """Test the configuration migration logic."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config_path))
    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(tmp_path))

    # Create an old config file
    old_config_data = {"key": "to_be_migrated"}
    with open(old_config_path, "w") as f:
        yaml.dump(old_config_data, f)

    # Run migration
    assert setup_config.migrate_config() is True

    # Check that new config exists and old one is gone
    assert new_config_path.exists()
    assert not old_config_path.exists()

    # Check content of new config
    with open(new_config_path, "r") as f:
        new_config_data = yaml.safe_load(f)
    assert new_config_data["key"] == "to_be_migrated"


@pytest.mark.parametrize(
    "is_termux_val, install_method, expected",
    [
        (True, "pip", "pip install --upgrade fetchtastic"),
        (True, "pipx", "pipx upgrade fetchtastic"),
        (False, "pipx", "pipx upgrade fetchtastic"),
    ],
)
def test_get_upgrade_command(mocker, is_termux_val, install_method, expected):
    """Test the upgrade command generation logic."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=is_termux_val)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value=install_method,
    )
    assert setup_config.get_upgrade_command() == expected


def test_cron_job_setup(mocker):
    """Test the cron job setup and removal logic."""
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")
    mock_communicate = mock_popen.return_value.communicate
    mocker.patch("shutil.which", return_value="/path/to/fetchtastic")

    # 1. Add a cron job
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    setup_config.setup_cron_job()
    mock_communicate.assert_called_once()
    new_cron_content = mock_communicate.call_args[1]["input"]

    # 2. Remove the cron job
    mock_run.return_value = mocker.MagicMock(stdout=new_cron_content, returncode=0)
    setup_config.remove_cron_job()

    # Check that communicate was called a second time
    assert mock_communicate.call_count == 2
    final_cron_content = mock_communicate.call_args[1]["input"]
    assert "fetchtastic download" not in final_cron_content


def test_windows_shortcut_creation(mocker):
    """Test the Windows shortcut creation logic."""
    # Mock platform and inject a mock winshell module into sys.modules
    mocker.patch("platform.system", return_value="Windows")
    mock_winshell = mocker.MagicMock()
    mocker.patch.dict("sys.modules", {"winshell": mock_winshell})

    # Reload the setup_config module to make it see the mocked environment
    importlib.reload(setup_config)

    # Now that the module is reloaded, we can patch its internal dependencies
    mocker.patch("shutil.which", return_value="C:\\path\\to\\fetchtastic.exe")
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.makedirs")
    mocker.patch("builtins.open", mocker.mock_open())

    # Test creating start menu shortcuts
    setup_config.create_windows_menu_shortcuts("C:\\config.yaml", "C:\\downloads")

    # Check that the reloaded module (which now has winshell) called CreateShortcut
    assert mock_winshell.CreateShortcut.call_count > 0

    # A simple check to see if one of the expected shortcuts was created
    found_download_shortcut = False
    for call in mock_winshell.CreateShortcut.call_args_list:
        path_arg = call.kwargs.get("Path")
        if path_arg and "Fetchtastic Download.lnk" in path_arg:
            found_download_shortcut = True
            break
    assert found_download_shortcut

    # It's good practice to restore the original module to avoid side effects
    importlib.reload(setup_config)
