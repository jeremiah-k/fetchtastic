import copy
import importlib
import os
import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml

# Import package module (matches real usage)
import fetchtastic.setup_config as setup_config
from tests.test_constants import TEST_CONFIG


@pytest.fixture
def reload_setup_config_module():
    """Fixture to reload the setup_config module after a test to restore original state."""
    yield
    # Restore the module to its original state after the test
    importlib.reload(setup_config)


# Utility function tests


@pytest.mark.configuration
@pytest.mark.unit
def test_is_termux_true():
    """Test is_termux returns True when PREFIX contains com.termux."""
    with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"}):
        assert setup_config.is_termux() is True

    # Test with different PREFIX values
    test_cases = [
        ({"PREFIX": "/data/data/com.termux/files/usr"}, True),
        ({"PREFIX": "/data/data/com.termux"}, True),
        ({"PREFIX": "/data/data/other/files/usr"}, False),
        ({"PREFIX": "/usr"}, False),
    ]

    for env_vars, expected in test_cases:
        with patch.dict(os.environ, env_vars):
            result = setup_config.is_termux()
            assert result == expected


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
    "value,default,expected",
    [
        (True, False, True),
        (False, True, False),
        (None, True, True),
        (None, False, False),
        (1, False, True),
        (0, True, False),
        (float("nan"), True, True),
        (float("nan"), False, False),
        ("yes", False, True),
        ("no", True, False),
        ("ON", False, True),
        ("off", True, False),
        ("2", False, True),
        ("maybe", True, True),
        ("", False, False),
        (object(), True, True),
    ],
)
def test_coerce_bool(value, default, expected):
    """Test _coerce_bool handles common value types."""
    from fetchtastic.setup_config import _coerce_bool

    assert _coerce_bool(value, default=default) is expected


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_skips_firmware_menu(mocker):
    """Partial runs should respect "keep existing" and skip the firmware menu."""
    from fetchtastic.setup_config import _setup_downloads

    # Preserve existing firmware selections and keep APKs disabled.
    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
        "CHECK_PRERELEASES": False,
    }

    # Only run the firmware section in this partial pass.
    def wants(section: str) -> bool:
        """
        Determine whether the requested setup section is 'firmware'.

        @returns `true` if `section` equals 'firmware', `false` otherwise.
        """
        return section == "firmware"

    # Answer prompts: keep firmware enabled, skip rerun, decline prereleases, decline suffixes.
    mocker.patch(
        "builtins.input",
        side_effect=["y", "n", "n", "n", "n"],
    )
    mock_menu = mocker.patch("fetchtastic.menu_firmware.run_menu")

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert save_apks is False
    assert save_firmware is True
    assert updated["SELECTED_FIRMWARE_ASSETS"] == ["rak4631"]
    mock_menu.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_skips_apk_menu(mocker):
    """Partial runs should respect "keep existing" and skip the APK menu."""
    from fetchtastic.setup_config import _setup_downloads

    # Preserve existing APK selections and skip firmware entirely.
    config = {
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
        "SELECTED_APK_ASSETS": ["meshtastic.apk"],
        "CHECK_APK_PRERELEASES": True,
    }

    # Only run the Android section in this partial pass.
    def wants(section: str) -> bool:
        """
        Check whether the requested setup section is the Android section.

        Parameters:
                section (str): Name of the setup section to test (e.g., "android").

        Returns:
                True if section is "android", False otherwise.
        """
        return section == "android"

    # Answer prompts: keep APKs enabled, skip rerun, decline prereleases, decline suffixes.
    mocker.patch(
        "builtins.input",
        side_effect=["y", "n", "n", "n"],
    )
    mock_menu = mocker.patch("fetchtastic.menu_apk.run_menu")

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert save_apks is True
    assert save_firmware is False
    assert updated["SELECTED_APK_ASSETS"] == ["meshtastic.apk"]
    mock_menu.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_reruns_firmware_menu(mocker):
    """Partial runs should re-run firmware menu when confirmed."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
        "CHECK_PRERELEASES": False,
    }

    def wants(section: str) -> bool:
        """
        Determine whether the requested setup section is 'firmware'.

        @returns `true` if `section` equals 'firmware', `false` otherwise.
        """
        return section == "firmware"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "y", "n", "n", "n"],
    )
    mock_menu = mocker.patch(
        "fetchtastic.menu_firmware.run_menu",
        return_value={"selected_assets": ["tbeam"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert save_apks is False
    assert save_firmware is True
    assert updated["SELECTED_FIRMWARE_ASSETS"] == ["tbeam"]
    mock_menu.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_reruns_apk_menu(mocker):
    """Partial runs should re-run APK menu when confirmed."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
        "SELECTED_APK_ASSETS": ["meshtastic.apk"],
        "CHECK_APK_PRERELEASES": True,
    }

    def wants(section: str) -> bool:
        """
        Check whether the requested setup section is the Android section.

        Parameters:
                section (str): Name of the setup section to test (e.g., "android").

        Returns:
                True if section is "android", False otherwise.
        """
        return section == "android"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "y", "n", "n"],
    )
    mock_menu = mocker.patch(
        "fetchtastic.menu_apk.run_menu",
        return_value={"selected_assets": ["meshtastic-debug.apk"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert save_apks is True
    assert save_firmware is False
    assert updated["SELECTED_APK_ASSETS"] == ["meshtastic-debug.apk"]
    mock_menu.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_skips_all_prompts(mocker):
    """No prompts should be shown when no sections are selected."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
    }

    def wants(_section: str) -> bool:
        """
        Determine whether the named setup section is requested for this run.

        Parameters:
            _section (str): Name of the setup section to check.

        Returns:
            True if the named section is requested, False otherwise.
        """
        return False

    mock_input = mocker.patch("builtins.input")
    _setup_downloads(config, is_partial_run=True, wants=wants)

    mock_input.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_full_run_prompts_channel_suffix(mocker):
    """Full runs should prompt for channel suffixes when downloads are enabled."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        """
        Indicates that any setup section should be included.

        Parameters:
            _section (str): Name of the setup section (ignored).

        Returns:
            bool: `True` for all sections.
        """
        return True

    mocker.patch(
        "builtins.input",
        side_effect=["", "n", "n", "n", "n"],
    )
    mocker.patch(
        "fetchtastic.menu_firmware.run_menu",
        return_value={"selected_assets": ["rak4631"]},
    )
    mocker.patch(
        "fetchtastic.menu_apk.run_menu",
        return_value={"selected_assets": ["meshtastic.apk"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    assert save_apks is True
    assert save_firmware is True
    assert updated["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] is False


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
    """Test platform detection logic."""
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
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx",
        return_value=True,
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
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx",
        return_value=False,
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
    """Test loading config from new (platformdirs) location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"

    # Patch the config file path directly in the module
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    config_data = {"SAVE_APKS": True}
    with open(new_config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    config = setup_config.load_config()
    assert config is not None
    assert config["SAVE_APKS"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_old_location_suggests_migration(tmp_path, mocker):
    """Test loading config from old location suggests migration."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    # Create config in old location
    config_data = TEST_CONFIG.copy()
    with open(old_config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    config = setup_config.load_config()
    assert config is not None
    assert config["BASE_DIR"] == TEST_CONFIG["BASE_DIR"]
    # Migration is suggested but not automatic - new file should not exist
    assert not new_config_path.exists()


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_invalid_yaml(tmp_path, mocker):
    """Test loading config with invalid YAML returns None and logs error."""
    new_config_path = tmp_path / "new_config.yaml"
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config_path))

    # Create invalid YAML that will cause a parsing error
    with open(new_config_path, "w") as f:
        f.write("invalid: [unclosed")

    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    assert setup_config.load_config() is None
    mock_logger.exception.assert_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_new_location(tmp_path, mocker):
    """Test config_exists detects config in new location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    # Create config in new location
    new_config_path.write_text("test: config", encoding="utf-8")

    exists, path = setup_config.config_exists()
    assert exists is True
    assert path == str(new_config_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_old_location(tmp_path, mocker):
    """Test config_exists detects config in old location."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    # Create config in old location only
    old_config_path.write_text("test: config", encoding="utf-8")

    exists, path = setup_config.config_exists()
    assert exists is True
    assert path == str(old_config_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists_none(tmp_path, mocker):
    """Test config_exists when no config exists."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

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
@patch("fetchtastic.setup_config.version")
def test_check_for_updates_available(mock_version, mock_get):
    """Test update check when newer version is available."""
    mock_version.return_value = "1.0.0"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "1.1.0"}}
    mock_get.return_value = mock_response

    current, latest, available = setup_config.check_for_updates()
    assert current == "1.0.0"
    assert latest == "1.1.0"
    assert available is True


@pytest.mark.configuration
@pytest.mark.integration
@patch("requests.get")
@patch("fetchtastic.setup_config.version")
def test_check_for_updates_current(mock_version, mock_get):
    """Test update check when current version is latest."""
    mock_version.return_value = "1.0.0"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "1.0.0"}}
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
    """Test downloads directory for Termux when storage exists."""
    with patch("fetchtastic.setup_config.is_termux", return_value=True):
        with patch("os.path.expanduser") as mock_expanduser:
            mock_expanduser.return_value = (
                "/data/data/com.termux/files/home/storage/downloads"
            )
            downloads_dir = setup_config.get_downloads_dir()
            assert "storage/downloads" in downloads_dir


@pytest.mark.configuration
@pytest.mark.unit
def test_get_downloads_dir_non_termux():
    """Test downloads directory for non-Termux platforms."""
    with patch("fetchtastic.setup_config.is_termux", return_value=False):
        with patch("os.path.exists") as mock_exists:
            with patch("os.path.expanduser") as mock_expanduser:
                mock_expanduser.side_effect = lambda path: path.replace(
                    "~", "/home/user"
                )
                mock_exists.side_effect = lambda path: path == "/home/user/Downloads"
                downloads_dir = setup_config.get_downloads_dir()
                assert downloads_dir == "/home/user/Downloads"


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_old_location(tmp_path, mocker):
    """Test loading config from the old location when new one doesn't exist."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    config_data = {"SAVE_FIRMWARE": True}
    with open(old_config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    config = setup_config.load_config()
    assert config is not None
    assert config["SAVE_FIRMWARE"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_prefers_new_location(tmp_path, mocker):
    """Test that the new config location is preferred when both exist."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))

    new_config_data = {"key": "new"}
    old_config_data = {"key": "old"}
    with open(new_config_path, "w") as f:
        yaml.safe_dump(new_config_data, f)
    with open(old_config_path, "w") as f:
        yaml.safe_dump(old_config_data, f)

    config = setup_config.load_config()
    assert config is not None
    assert config["key"] == "new"


def test_migrate_config(tmp_path, mocker):
    """Test the configuration migration logic."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))
    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(tmp_path))

    # Create an old config file
    old_config_data = {"key": "to_be_migrated"}
    with open(old_config_path, "w") as f:
        yaml.safe_dump(old_config_data, f)

    # Run migration
    assert setup_config.migrate_config() is True

    # Check that new config exists and old one is gone
    assert new_config_path.exists()
    assert not old_config_path.exists()

    # Check content of new config
    with open(new_config_path, "r") as f:
        new_config_data = yaml.safe_load(f)
    assert new_config_data["key"] == "to_be_migrated"


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_config_handles_load_error(tmp_path, mocker):
    """Migration should fail cleanly when loading the old config raises."""
    new_config_path = tmp_path / "new_config.yaml"
    old_config_path = tmp_path / "old_config.yaml"
    mocker.patch.object(setup_config, "CONFIG_FILE", str(new_config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(old_config_path))
    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(tmp_path))

    # Create a stub old config so the migrate routine attempts to read it.
    old_config_path.write_text("bad: yaml", encoding="utf-8")

    # Force YAML loading to raise so we cover the error path.
    mocker.patch(
        "fetchtastic.setup_config.yaml.safe_load", side_effect=yaml.YAMLError("boom")
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    assert setup_config.migrate_config() is False
    assert mock_logger.exception.called


@pytest.mark.parametrize(
    "is_termux_val, install_method, expected",
    [
        (True, "pip", "pip install --upgrade fetchtastic"),
        (True, "pipx", "pipx upgrade fetchtastic"),
        (False, "pipx", "pipx upgrade fetchtastic"),
    ],
)
def test_get_upgrade_command(mocker, is_termux_val, install_method, expected):
    """Test upgrade command generation logic."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=is_termux_val)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value=install_method,
    )
    assert setup_config.get_upgrade_command() == expected


def test_get_upgrade_command_fallback(mocker):
    """Test upgrade command when both install methods fail."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pipx",
    )
    mocker.patch(
        "fetchtastic.setup_config.is_fetchtastic_installed_via_pipx",
        return_value=False,
    )

    # Should fall back to pipx command when is_termux=True and installation_method="pipx"
    assert setup_config.get_upgrade_command() == "pipx upgrade fetchtastic"


@pytest.mark.configuration
@pytest.mark.unit
def test_config_exists(mocker):
    """Test config_exists function returns tuple."""
    # Test when config file exists
    mocker.patch("os.path.exists", return_value=True)
    result = setup_config.config_exists()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] is True  # exists flag
    assert isinstance(result[1], str)  # path

    # Test when config file doesn't exist
    mocker.patch("os.path.exists", return_value=False)
    result = setup_config.config_exists()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] is False  # exists flag


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config(mocker):
    """Test load_config function."""
    # Test when config doesn't exist
    mocker.patch("os.path.exists", return_value=False)
    config = setup_config.load_config()
    assert config is None


@pytest.mark.configuration
@pytest.mark.unit
def test_platform_functions():
    """Test platform detection functions."""
    # Test get_platform function
    platform = setup_config.get_platform()
    assert isinstance(platform, str)
    assert platform in ["linux", "darwin", "windows"]

    # Test get_downloads_dir function
    downloads_dir = setup_config.get_downloads_dir()
    assert isinstance(downloads_dir, str)
    # Just check it's a valid path, not necessarily ending in Downloads
    assert len(downloads_dir) > 0


@pytest.mark.configuration
@pytest.mark.unit
def test_installation_detection_functions(mocker):
    """Test installation detection functions."""
    # Test is_fetchtastic_installed_via_pip
    mocker.patch("shutil.which", return_value="/usr/bin/pip")
    mocker.patch("subprocess.run", return_value=mocker.MagicMock(stdout="fetchtastic"))

    result = setup_config.is_fetchtastic_installed_via_pip()
    assert isinstance(result, bool)

    # Test is_fetchtastic_installed_via_pipx
    mocker.patch("shutil.which", return_value="/usr/bin/pipx")
    result = setup_config.is_fetchtastic_installed_via_pipx()
    assert isinstance(result, bool)

    # Test get_fetchtastic_installation_method
    method = setup_config.get_fetchtastic_installation_method()
    assert isinstance(method, str)
    assert method in ["pip", "pipx", "source", "unknown"]


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_storage_function(mocker):
    """Test setup_storage function."""
    mock_subprocess = mocker.patch("subprocess.run")
    mocker.patch("fetchtastic.log_utils.logger")

    result = setup_config.setup_storage()

    # Function doesn't return anything, just runs
    assert result is None
    mock_subprocess.assert_called_once_with(["termux-setup-storage"], check=True)


@pytest.mark.configuration
@pytest.mark.unit
def test_migration_functions_simple(mocker):
    """Test migration-related functions with simpler mocking."""
    # Test install_crond function returns None by default
    result = setup_config.install_crond()
    assert result is None


@pytest.mark.configuration
@pytest.mark.unit
def test_get_upgrade_command_basic(mocker):
    """Test get_upgrade_command basic scenarios."""
    # Test with non-Termux environment (should default to pipx)
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

    result = setup_config.get_upgrade_command()

    # Should return pipx upgrade command for non-Termux
    assert result == "pipx upgrade fetchtastic"


@pytest.mark.configuration
@pytest.mark.unit
def test_windows_functions(mocker):
    """Test Windows-related functions."""
    # Test that Windows functions exist when not on Windows
    assert hasattr(setup_config, "create_windows_menu_shortcuts") is True
    assert hasattr(setup_config, "should_recommend_setup") is True


@pytest.mark.configuration
@pytest.mark.unit
def test_upgrade_command_termux_scenarios(mocker):
    """Test get_upgrade_command in Termux scenarios."""
    # Test with Termux and pip installation
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )

    result = setup_config.get_upgrade_command()
    assert result == "pip install --upgrade fetchtastic"

    # Test with Termux and pipx installation
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pipx",
    )

    result = setup_config.get_upgrade_command()
    assert result == "pipx upgrade fetchtastic"


@pytest.mark.configuration
@pytest.mark.unit
def test_config_file_operations(mocker):
    """Test config file operations."""
    import os
    import tempfile

    # Test with temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Use the correct config file name
        config_file = os.path.join(temp_dir, "fetchtastic.yaml")

        # Create a test config file
        with open(config_file, "w") as f:
            f.write("TEST_KEY: test_value\nBASE_DIR: /test/dir")

        # Test config_exists with explicit directory
        exists, path = setup_config.config_exists(temp_dir)
        assert exists is True
        assert path == config_file

        # Test load_config with explicit directory
        config = setup_config.load_config(temp_dir)
        assert config is not None
        assert config["TEST_KEY"] == "test_value"


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_missing_in_explicit_directory(tmp_path):
    """Explicit directory loads should return None when config is absent."""
    assert setup_config.load_config(str(tmp_path)) is None


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_rejects_non_mapping_yaml(tmp_path):
    """Explicit directory loads should reject non-mapping YAML content."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    config_path = config_dir / "fetchtastic.yaml"
    config_path.write_text("- not-a-mapping\n", encoding="utf-8")

    assert setup_config.load_config(str(config_dir)) is None


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_empty_file_returns_empty_mapping(tmp_path, mocker):
    """Empty config files should return an empty dict instead of crashing."""
    config_path = tmp_path / "fetchtastic.yaml"
    config_path.write_text("", encoding="utf-8")
    mocker.patch.object(setup_config, "CONFIG_FILE", str(config_path))
    mocker.patch.object(setup_config, "OLD_CONFIG_FILE", str(tmp_path / "old.yaml"))

    config = setup_config.load_config()
    assert config == {}


@pytest.mark.configuration
@pytest.mark.unit
def test_get_platform_comprehensive(mocker):
    """Test get_platform function comprehensively."""
    # Test each platform by mocking system()
    platforms = ["Linux", "Darwin", "Windows"]
    expected_results = ["linux", "mac", "unknown"]  # Note: Darwin returns "mac"

    for platform, expected in zip(platforms, expected_results, strict=False):
        mocker.patch("platform.system", return_value=platform)
        mocker.patch(
            "fetchtastic.setup_config.is_termux", return_value=False
        )  # Not Termux
        result = setup_config.get_platform()
        assert result == expected


@pytest.mark.configuration
@pytest.mark.unit
def test_get_downloads_dir_comprehensive(mocker):
    """Test get_downloads_dir function."""
    # Test with Termux environment
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)

    result = setup_config.get_downloads_dir()
    # With the mock above, should return the Termux downloads path
    assert isinstance(result, str)

    # Test with non-Termux environment - mock the actual path checks
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mock_home = "/test/home"
    mocker.patch("os.path.expanduser", return_value=mock_home)

    result = setup_config.get_downloads_dir()
    assert isinstance(result, str)


def test_cron_job_setup(mocker):
    """Test the cron job setup and removal logic."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
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


def test_cron_job_setup_hourly(mocker):
    """Test the cron job setup for hourly frequency."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")
    mock_communicate = mock_popen.return_value.communicate
    mocker.patch("shutil.which", return_value="/path/to/fetchtastic")

    # Add an hourly cron job
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    setup_config.setup_cron_job("hourly")
    mock_communicate.assert_called_once()
    new_cron_content = mock_communicate.call_args[1]["input"]

    # Check that the cron job contains hourly schedule (0 * * * *)
    assert "0 * * * * /path/to/fetchtastic download  # fetchtastic" in new_cron_content


def test_cron_job_setup_windows(mocker):
    """Test that cron job setup does nothing on Windows."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")

    # Should not raise any exceptions and should not attempt to run subprocess
    setup_config.setup_cron_job("daily")


def test_cron_job_setup_invalid_frequency(mocker):
    """Test cron job setup with invalid frequency parameter."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mock_run = mocker.patch("subprocess.run")
    mock_popen = mocker.patch("subprocess.Popen")
    mock_communicate = mock_popen.return_value.communicate
    mocker.patch("shutil.which", return_value="/path/to/fetchtastic")

    # Add a cron job with invalid frequency
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    setup_config.setup_cron_job("invalid")
    mock_communicate.assert_called_once()
    new_cron_content = mock_communicate.call_args[1]["input"]

    # Should default to hourly schedule (0 * * * *)
    assert "0 * * * * /path/to/fetchtastic download  # fetchtastic" in new_cron_content


def test_prompt_for_cron_frequency(mocker):
    """Test the _prompt_for_cron_frequency function."""
    mock_input = mocker.patch("builtins.input")

    # Test hourly choice
    mock_input.return_value = "h"
    result = setup_config._prompt_for_cron_frequency()
    assert result == "hourly"

    # Test daily choice
    mock_input.return_value = "d"
    result = setup_config._prompt_for_cron_frequency()
    assert result == "daily"

    # Test none choice
    mock_input.return_value = "n"
    result = setup_config._prompt_for_cron_frequency()
    assert result == "none"

    # Test default (empty input)
    mock_input.return_value = ""
    result = setup_config._prompt_for_cron_frequency()
    assert result == "hourly"

    # Test invalid input followed by valid input
    mock_input.side_effect = ["x", "h"]
    result = setup_config._prompt_for_cron_frequency()
    assert result == "hourly"


def test_setup_automation_windows_no_shortcut(mocker, reload_setup_config_module):
    """Test _setup_automation on Windows without existing startup shortcut."""
    # Mock platform and inject a mock winshell module into sys.modules
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mock_winshell = mocker.MagicMock()
    mocker.patch.dict("sys.modules", {"winshell": mock_winshell})

    mocker.patch("os.path.exists", return_value=False)
    mocker.patch("builtins.input", return_value="y")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)

    assert result == config


def test_setup_automation_termux_new_cron_hourly(mocker):
    """Test _setup_automation on Termux for new cron job setup with hourly choice."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
    mocker.patch(
        "fetchtastic.setup_config.check_boot_script_exists", return_value=False
    )

    mock_input = mocker.patch("builtins.input")
    mock_input.side_effect = ["h", "y"]  # hourly cron, boot script

    mock_install_crond = mocker.patch("fetchtastic.setup_config.install_crond")
    mock_setup_cron = mocker.patch("fetchtastic.setup_config.setup_cron_job")
    mock_setup_boot = mocker.patch("fetchtastic.setup_config.setup_boot_script")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)

    mock_install_crond.assert_called_once()
    mock_setup_cron.assert_called_once_with("hourly")
    mock_setup_boot.assert_called_once()
    assert result == config


def test_setup_automation_termux_reconfig_cron(mocker):
    """Test _setup_automation on Termux for existing cron job reconfiguration."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.check_boot_script_exists", return_value=False
    )

    mock_input = mocker.patch("builtins.input")
    mock_input.side_effect = ["y", "d", "n"]  # reconfigure, daily cron, no boot script

    mock_remove_cron = mocker.patch("fetchtastic.setup_config.remove_cron_job")
    mock_install_crond = mocker.patch("fetchtastic.setup_config.install_crond")
    mock_setup_cron = mocker.patch("fetchtastic.setup_config.setup_cron_job")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)

    mock_remove_cron.assert_called_once()
    mock_install_crond.assert_called_once()
    mock_setup_cron.assert_called_once_with("daily")
    assert result == config


def test_setup_automation_linux_new_setup(mocker):
    """Test _setup_automation on Linux for new cron job setup."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
    mocker.patch(
        "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False
    )

    mock_input = mocker.patch("builtins.input")
    mock_input.side_effect = ["h", "n"]  # hourly cron, no reboot

    mock_setup_cron = mocker.patch("fetchtastic.setup_config.setup_cron_job")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)

    mock_setup_cron.assert_called_once_with("hourly")
    assert result == config


def test_windows_shortcut_creation(mocker):
    """Test the Windows shortcut creation logic."""
    # Mock platform and inject a mock winshell module into sys.modules
    mocker.patch("platform.system", return_value="Windows")
    mock_winshell = mocker.MagicMock()
    mocker.patch.dict("sys.modules", {"winshell": mock_winshell})

    # Reload the setup_config module to make it see the mocked environment
    importlib.reload(setup_config)


@pytest.mark.configuration
@pytest.mark.unit
def test_windows_shortcut_creation_scandir_fallback(mocker):
    """Test fallback cleanup path when Start Menu folder removal fails."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    windows_folder = "C:\\StartMenu\\Fetchtastic"
    parent_dir = os.path.dirname(windows_folder)
    mocker.patch("fetchtastic.setup_config.WINDOWS_START_MENU_FOLDER", windows_folder)
    mocker.patch("fetchtastic.setup_config.BASE_DIR", "C:\\downloads")

    mocker.patch("shutil.which", return_value="C:\\path\\to\\fetchtastic.exe")
    mocker.patch("os.makedirs")
    mocker.patch("builtins.open", mock_open())

    def exists_side_effect(path):
        if path in {parent_dir, windows_folder}:
            return True
        return False

    mocker.patch("os.path.exists", side_effect=exists_side_effect)

    file_entry = MagicMock()
    file_entry.name = "shortcut.lnk"
    file_entry.path = os.path.join(windows_folder, "shortcut.lnk")
    file_entry.is_file = MagicMock(return_value=True)
    file_entry.is_dir = MagicMock(return_value=False)
    dir_entry = MagicMock()
    dir_entry.name = "Nested"
    dir_entry.path = os.path.join(windows_folder, "Nested")
    dir_entry.is_file = MagicMock(return_value=False)
    dir_entry.is_dir = MagicMock(return_value=True)

    mocker.patch(
        "os.scandir",
        return_value=MagicMock(
            __enter__=MagicMock(return_value=[file_entry, dir_entry]),
            __exit__=MagicMock(return_value=None),
        ),
    )
    mock_remove = mocker.patch("os.remove")

    def rmtree_side_effect(path):
        if path == windows_folder:
            raise OSError("remove failed")
        return None

    mock_rmtree = mocker.patch("shutil.rmtree", side_effect=rmtree_side_effect)

    result = setup_config.create_windows_menu_shortcuts(
        "C:\\config.yaml", "C:\\downloads"
    )

    assert result is True
    mock_rmtree.assert_any_call(windows_folder)
    mock_remove.assert_any_call(os.path.join(windows_folder, "shortcut.lnk"))
    mock_rmtree.assert_any_call(os.path.join(windows_folder, "Nested"))

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


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_apk.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False)
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
@patch(
    "fetchtastic.setup_config.platformdirs.user_config_dir",
    return_value="/tmp/config",  # nosec B108
)
def test_run_setup_first_run_linux_simple(
    mock_user_config_dir,
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_menu_firmware,
    mock_menu_apk,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
):
    """Test a simple first-run setup process on a Linux system."""
    user_inputs = [
        "",  # Use default base directory
        "b",  # Both APKs and firmware
        "n",  # Check for firmware prereleases
        "y",  # Check for APK prereleases
        "n",  # Add channel suffixes
        "2",  # Keep 2 versions of Android app
        "2",  # Keep 2 versions of firmware
        "n",  # No auto-extract
        "n",  # No cron job
        "n",  # No reboot cron job
        "n",  # No NTFY notifications
        "n",  # Would you like to set up a GitHub token now?
        "n",  # Don't perform first run now
    ]
    mock_input.side_effect = user_inputs

    mock_menu_apk.return_value = {"selected_assets": ["meshtastic-apk"]}
    mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

    with patch("builtins.open", mock_open()):
        with patch("sys.stdin.isatty", return_value=False):
            setup_config.run_setup()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]

        assert saved_config["SAVE_APKS"] is True
        assert saved_config["SAVE_FIRMWARE"] is True
        assert saved_config["ANDROID_VERSIONS_TO_KEEP"] == 2
        assert saved_config["FIRMWARE_VERSIONS_TO_KEEP"] == 2
        assert saved_config["CHECK_PRERELEASES"] is False
        assert saved_config["CHECK_APK_PRERELEASES"] is True
        assert saved_config["AUTO_EXTRACT"] is False
        assert saved_config["EXTRACT_PATTERNS"] == []
        assert saved_config["EXCLUDE_PATTERNS"] == []
        assert saved_config["NTFY_TOPIC"] == ""
        assert saved_config["NTFY_SERVER"] == ""

        mock_setup_cron_job.assert_not_called()
        mock_setup_reboot_cron_job.assert_not_called()
        mock_downloader_main.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Windows")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_apk.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.create_windows_menu_shortcuts")
@patch("fetchtastic.setup_config.create_config_shortcut")
@patch("fetchtastic.setup_config.create_startup_shortcut")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
@patch(
    "fetchtastic.setup_config.platformdirs.user_config_dir",
    return_value="/tmp/config",  # nosec B108
)
@patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
@patch("fetchtastic.setup_config.winshell", MagicMock(), create=True)
def test_run_setup_first_run_windows(
    mock_user_config_dir,
    mock_shutil_which,
    mock_downloader_main,
    mock_create_startup_shortcut,
    mock_create_config_shortcut,
    mock_create_windows_menu_shortcuts,
    mock_menu_firmware,
    mock_menu_apk,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
):
    """Test a simple first-run setup process on a Windows system."""
    user_inputs = [
        "",  # Use default base directory
        "y",  # create menu
        "b",  # Both APKs and firmware
        "n",  # Check for firmware prereleases
        "y",  # Check for APK prereleases
        "n",  # Add channel suffixes
        "2",  # Keep 2 versions of Android app
        "2",  # Keep 2 versions of firmware
        "n",  # No auto-extract
        "y",  # create startup shortcut
        "n",  # No NTFY notifications
        "n",  # Would you like to set up a GitHub token now?
        "",  # press enter to close
    ]
    mock_input.side_effect = user_inputs

    mock_menu_apk.return_value = {"selected_assets": ["meshtastic-apk"]}
    mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

    with patch("builtins.open", mock_open()):
        with patch("sys.stdin.isatty", return_value=False):
            setup_config.run_setup()

        mock_create_windows_menu_shortcuts.assert_called_once()
        mock_create_config_shortcut.assert_called_once()
        mock_create_startup_shortcut.assert_called_once()
        mock_downloader_main.assert_not_called()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]

        # Assert token is not set on Windows flow
        assert (
            "GITHUB_TOKEN" not in saved_config
            or saved_config.get("GITHUB_TOKEN") is None
        )
        assert saved_config["CHECK_APK_PRERELEASES"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("getpass.getpass", return_value="")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=True)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_apk.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.install_termux_packages")
@patch("fetchtastic.setup_config.check_storage_setup")
@patch(
    "fetchtastic.setup_config.get_fetchtastic_installation_method", return_value="pip"
)
@patch("fetchtastic.setup_config.migrate_pip_to_pipx")
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_boot_script")
@patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
@patch("fetchtastic.setup_config.check_boot_script_exists", return_value=False)
@patch("fetchtastic.cli.main")
@patch("shutil.which")
@patch("fetchtastic.setup_config.subprocess.run")
@patch(
    "fetchtastic.setup_config.platformdirs.user_config_dir",
    return_value="/tmp/config",  # nosec B108 - test-only path
)
def test_run_setup_first_run_termux(  # noqa: ARG001
    mock_user_config_dir,
    mock_subprocess_run,
    mock_shutil_which,
    mock_downloader_main,
    mock_check_boot_script_exists,
    mock_check_cron_job_exists,
    mock_setup_boot_script,
    mock_setup_cron_job,
    mock_migrate_pip_to_pipx,
    mock_get_install_method,
    mock_check_storage_setup,
    mock_install_termux_packages,
    mock_menu_firmware,
    mock_menu_apk,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_getpass,
    mock_input,
):
    """Test a simple first-run setup process on a Termux system."""
    user_inputs = [
        "n",  # don't migrate to pipx (so setup continues)
        "",  # Use default base directory
        "b",  # Both APKs and firmware
        "n",  # Check for firmware prereleases
        "y",  # Check for APK prereleases
        "n",  # Add channel suffixes
        "1",  # Keep 1 version of Android app
        "1",  # Keep 1 version of firmware
        "n",  # No auto-extract
        "y",  # wifi only
        "h",  # hourly cron job
        "y",  # boot script
        "n",  # No NTFY notifications
        "n",  # Would you like to set up a GitHub token now?
        "n",  # Don't perform first run now
    ]
    mock_input.side_effect = user_inputs

    mock_menu_apk.return_value = {"selected_assets": ["meshtastic-apk"]}
    mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

    with patch("builtins.open", mock_open()):
        setup_config.run_setup()

        mock_install_termux_packages.assert_called_once()
        mock_check_storage_setup.assert_called_once()
        mock_migrate_pip_to_pipx.assert_not_called()  # User chose not to migrate
        mock_setup_cron_job.assert_called_once()
        mock_setup_boot_script.assert_called_once()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]
        assert saved_config["WIFI_ONLY"] is True
        assert saved_config["CHECK_APK_PRERELEASES"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.os.path.exists")
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.yaml.safe_load")
@patch("fetchtastic.setup_config.menu_apk.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=True)
@patch("fetchtastic.setup_config.remove_cron_job")
@patch("fetchtastic.setup_config.remove_reboot_cron_job")
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
@patch(
    "fetchtastic.setup_config.platformdirs.user_config_dir",
    return_value="/tmp/config",  # nosec B108
)
def test_run_setup_existing_config(
    mock_user_config_dir,
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_remove_reboot_cron_job,
    mock_remove_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_menu_firmware,
    mock_menu_apk,
    mock_yaml_safe_load,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
):
    """Test the setup process when a configuration file already exists."""
    existing_config = {
        "BASE_DIR": "/tmp/meshtastic",  # nosec B108
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": True,
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": False,
        "EXTRACT_PATTERNS": [],
        "EXCLUDE_PATTERNS": [],
        "NTFY_TOPIC": "old-topic",
        "NTFY_SERVER": "https://ntfy.sh/old",
    }
    mock_os_path_exists.return_value = True
    mock_yaml_safe_load.return_value = existing_config

    user_inputs = [
        "",  # choose full setup at the new prompt
        "/new/base/dir",  # New base directory
        "f",  # Only firmware
        "y",  # Check for pre-releases
        "n",  # Add channel suffixes
        "5",  # Keep 5 versions of firmware
        "y",  # Auto-extract
        "rak4631- tbeam",  # Extraction patterns
        "y",  # Confirm extraction/exclude summary
        "y",  # reconfigure cron
        "n",  # no cron job
        "n",  # no reboot cron
        "y",  # reconfigure ntfy
        "https://ntfy.sh/new",  # new server
        "new-topic",  # new topic
        "n",  # no copy
        "n",  # no notify on download only
        "n",  # No GitHub token setup
        "n",  # Don't perform first run now
    ]
    mock_input.side_effect = user_inputs

    mock_menu_firmware.return_value = {"selected_assets": ["new-firmware"]}

    with patch("builtins.open", mock_open()):
        with patch("sys.stdin.isatty", return_value=False):
            setup_config.run_setup()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]

        assert saved_config["BASE_DIR"] == "/new/base/dir"
        assert saved_config["SAVE_APKS"] is False
        assert saved_config["SAVE_FIRMWARE"] is True
        assert saved_config["FIRMWARE_VERSIONS_TO_KEEP"] == 5
        assert saved_config["AUTO_EXTRACT"] is True
        assert saved_config["EXTRACT_PATTERNS"] == ["rak4631-", "tbeam"]
        assert saved_config["CHECK_PRERELEASES"] is True
        assert saved_config["SELECTED_FIRMWARE_ASSETS"] == ["new-firmware"]
        assert saved_config["SELECTED_PRERELEASE_ASSETS"] == ["rak4631-", "tbeam"]
        assert "SELECTED_APK_ASSETS" not in saved_config
        assert saved_config["NTFY_SERVER"] == "https://ntfy.sh/new"
        assert saved_config["NTFY_TOPIC"] == "new-topic"

        mock_remove_cron_job.assert_called_once()
        mock_remove_reboot_cron_job.assert_called_once()
        mock_setup_cron_job.assert_not_called()
        mock_setup_reboot_cron_job.assert_not_called()


# Covered by test_run_setup_invalid_sections; removed to reduce duplication.


@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.os.path.exists")
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.yaml.safe_load")
@patch("fetchtastic.setup_config.menu_apk.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=True)
@patch("fetchtastic.setup_config.remove_cron_job")
@patch("fetchtastic.setup_config.remove_reboot_cron_job")
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
@patch(
    "fetchtastic.setup_config.platformdirs.user_config_dir",
    return_value="/tmp/config",  # nosec B108
)
def test_run_setup_partial_firmware_section(
    mock_user_config_dir,
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_remove_reboot_cron_job,
    mock_remove_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_menu_firmware,
    mock_menu_apk,
    mock_yaml_safe_load,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
):
    """Partial firmware run should update firmware options without touching others."""

    existing_config = {
        "BASE_DIR": "/tmp/meshtastic",  # nosec B108 - test-only path
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": True,
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": False,
        "EXTRACT_PATTERNS": [],
        "EXCLUDE_PATTERNS": [],
    }
    mock_os_path_exists.return_value = True
    mock_yaml_safe_load.return_value = existing_config
    mock_menu_firmware.return_value = {"selected_assets": ["firmware-esp32-.zip"]}

    mock_yaml_dump.reset_mock()
    mock_remove_cron_job.reset_mock()
    mock_remove_reboot_cron_job.reset_mock()
    mock_setup_cron_job.reset_mock()
    mock_setup_reboot_cron_job.reset_mock()
    mock_menu_firmware.reset_mock()
    mock_menu_apk.reset_mock()

    mock_input.side_effect = [
        "y",  # Download firmware releases
        "y",  # Re-run firmware menu
        "y",  # Check for firmware prereleases
        "n",  # Add channel suffixes
        "3",  # Keep 3 versions of firmware
        "y",  # Auto-extract
        "esp32- rak4631-",  # Extraction patterns
    ]

    with patch("builtins.open", mock_open()):
        with patch("sys.stdin.isatty", return_value=False):
            setup_config.run_setup(sections=["firmware"])

    mock_menu_firmware.assert_called_once()
    mock_menu_apk.assert_not_called()

    saved_configs = [
        copy.deepcopy(args[0][0]) for args in mock_yaml_dump.call_args_list
    ]
    assert any(cfg.get("FIRMWARE_VERSIONS_TO_KEEP") == 3 for cfg in saved_configs)
    assert any(cfg.get("CHECK_PRERELEASES") is True for cfg in saved_configs)

    mock_setup_cron_job.assert_not_called()
    mock_setup_reboot_cron_job.assert_not_called()

    # Verify GitHub token is not persisted when user declines
    assert (
        "GITHUB_TOKEN" not in saved_configs[-1]
        or saved_configs[-1].get("GITHUB_TOKEN") is None
    )
    mock_remove_cron_job.assert_not_called()
    mock_remove_reboot_cron_job.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_section_shortcuts_mapping():
    """Test that SECTION_SHORTCUTS correctly maps shortcuts to full section names."""
    from fetchtastic.setup_config import SECTION_SHORTCUTS, SETUP_SECTION_CHOICES

    # Test that all shortcuts map to valid section choices
    for shortcut, section in SECTION_SHORTCUTS.items():
        assert (
            section in SETUP_SECTION_CHOICES
        ), f"Shortcut '{shortcut}' maps to invalid section '{section}'"

    # Test specific mappings
    assert SECTION_SHORTCUTS["b"] == "base"
    assert SECTION_SHORTCUTS["a"] == "android"
    assert SECTION_SHORTCUTS["f"] == "firmware"
    assert SECTION_SHORTCUTS["n"] == "notifications"
    assert SECTION_SHORTCUTS["m"] == "automation"
    assert SECTION_SHORTCUTS["g"] == "github"

    # Test that all expected shortcuts exist
    expected_shortcuts = {"b", "a", "f", "n", "m", "g"}
    assert set(SECTION_SHORTCUTS.keys()) == expected_shortcuts


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_section_choices():
    """Test that SETUP_SECTION_CHOICES contains expected sections."""
    from fetchtastic.setup_config import SETUP_SECTION_CHOICES

    expected_sections = {
        "base",
        "android",
        "firmware",
        "notifications",
        "automation",
        "github",
    }
    assert SETUP_SECTION_CHOICES == expected_sections


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_empty_input(mock_input):
    """Test _prompt_for_setup_sections returns None for empty input."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = ""
    result = _prompt_for_setup_sections()
    assert result is None


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_shortcuts(mock_input):
    """Test _prompt_for_setup_sections handles shortcuts correctly."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = "f, a"
    result = _prompt_for_setup_sections()
    assert result == {"firmware", "android"}


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_full_names(mock_input):
    """Test _prompt_for_setup_sections handles full section names."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = "firmware, notifications"
    result = _prompt_for_setup_sections()
    assert result == {"firmware", "notifications"}


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_mixed_input(mock_input):
    """Test _prompt_for_setup_sections handles mixed shortcuts and full names."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = "f, android, n"
    result = _prompt_for_setup_sections()
    assert result == {"firmware", "android", "notifications"}


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("builtins.print")
def test_prompt_for_setup_sections_invalid_input(mock_print, mock_input):
    """Test _prompt_for_setup_sections handles invalid input and retries."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    # First invalid input, then valid input
    mock_input.side_effect = ["invalid_section", "f"]
    result = _prompt_for_setup_sections()
    assert result == {"firmware"}

    # Check that error message was printed
    mock_print.assert_any_call(
        "Unrecognised section 'invalid_section'. Please choose from the listed options."
    )


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_all_keywords(mock_input):
    """Test _prompt_for_setup_sections returns None for 'all' keywords."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    test_cases = ["all", "full", "everything", "ALL", "Full"]
    for keyword in test_cases:
        mock_input.return_value = keyword
        result = _prompt_for_setup_sections()
        assert result is None, f"Keyword '{keyword}' should return None"


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_semicolon_separator(mock_input):
    """Test _prompt_for_setup_sections handles semicolon separators."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = "f; a; n"
    result = _prompt_for_setup_sections()
    assert result == {"firmware", "android", "notifications"}


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_prompt_for_setup_sections_quit(mock_input):
    """Test _prompt_for_setup_sections returns empty set for quit."""
    from fetchtastic.setup_config import _prompt_for_setup_sections

    mock_input.return_value = "q"
    result = _prompt_for_setup_sections()
    assert result == set()


@pytest.mark.configuration
@pytest.mark.unit
def test_run_setup_invalid_sections():
    """Test run_setup raises ValueError for invalid sections."""
    from fetchtastic.setup_config import run_setup

    with pytest.raises(
        ValueError, match="Unsupported setup section\\(s\\): invalid_section"
    ):
        run_setup(sections=["invalid_section"])

    with pytest.raises(
        ValueError, match="Unsupported setup section\\(s\\): bad1, bad2"
    ):
        run_setup(sections=["firmware", "bad1", "bad2"])


@pytest.mark.configuration
@pytest.mark.unit
def test_run_setup_valid_sections():
    """Test run_setup accepts valid sections without error."""
    from fetchtastic.setup_config import SETUP_SECTION_CHOICES, run_setup

    # Test each valid section individually
    for section in SETUP_SECTION_CHOICES:
        with patch(
            "fetchtastic.setup_config.config_exists", return_value=(False, None)
        ):
            with patch(
                "builtins.input", side_effect=["", "n", "n", "n", "n", "n", "n"]
            ):
                with patch("builtins.open", mock_open()):
                    with patch("fetchtastic.setup_config.yaml.safe_dump"):
                        with patch("os.makedirs"):
                            try:
                                run_setup(sections=[section])
                            except ValueError:
                                pytest.fail(f"Section '{section}' should be valid")


@pytest.mark.configuration
@pytest.mark.unit
@patch("fetchtastic.setup_config.config_exists")
@patch("fetchtastic.setup_config._prompt_for_setup_sections")
def test_run_setup_prompts_for_sections_when_config_exists(
    mock_prompt, mock_config_exists
):
    """Test run_setup prompts for sections when config exists and no sections specified."""
    from fetchtastic.setup_config import run_setup

    mock_config_exists.return_value = (True, "/path/to/config")
    mock_prompt.return_value = {"firmware"}

    with patch("builtins.input", side_effect=["", "n", "n", "n"]):
        with patch("builtins.open", mock_open()):
            with patch("fetchtastic.setup_config.yaml.safe_dump"):
                with patch("fetchtastic.setup_config.load_config", return_value={}):
                    with patch("os.makedirs"):
                        try:
                            run_setup()
                        except (ValueError, TypeError, AttributeError):
                            pass  # We expect exceptions due to incomplete mocking

    mock_prompt.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
@patch("fetchtastic.setup_config.config_exists")
@patch("fetchtastic.setup_config._prompt_for_setup_sections")
def test_run_setup_skips_prompt_when_sections_provided(mock_prompt, mock_config_exists):
    """Test run_setup skips prompting when sections are explicitly provided."""
    from fetchtastic.setup_config import run_setup

    mock_config_exists.return_value = (True, "/path/to/config")

    with patch("builtins.input", side_effect=["", "n", "n", "n"]):
        with patch("builtins.open", mock_open()):
            with patch("fetchtastic.setup_config.yaml.safe_dump"):
                with patch("fetchtastic.setup_config.load_config", return_value={}):
                    with patch("os.makedirs"):
                        try:
                            run_setup(sections=["firmware"])
                        except (ValueError, TypeError, AttributeError):
                            pass  # We expect exceptions due to incomplete mocking

    mock_prompt.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
@patch("fetchtastic.setup_config.config_exists")
@patch("fetchtastic.setup_config._prompt_for_setup_sections")
def test_run_setup_quits_on_prompt_cancel(mock_prompt, mock_config_exists):
    """Test run_setup exits when user quits the section prompt."""
    from fetchtastic.setup_config import run_setup

    mock_config_exists.return_value = (True, "/path/to/config")
    mock_prompt.return_value = set()

    with patch("builtins.print") as mock_print:
        run_setup()

    mock_print.assert_any_call("Setup cancelled.")


# SELECTED_PRERELEASE_ASSETS setup wizard tests


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_new_config(mock_input):
    """Test setup wizard prompts for SELECTED_PRERELEASE_ASSETS with new configuration."""
    config = {"CHECK_PRERELEASES": True}

    # Simulate user inputs: 3 versions, yes to auto-extract, device patterns
    mock_input.side_effect = ["3", "y", "rak4631- tbeam", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=True, default_versions=2
        )

    assert result["FIRMWARE_VERSIONS_TO_KEEP"] == 3
    assert result["AUTO_EXTRACT"] is True
    assert result["EXTRACT_PATTERNS"] == ["rak4631-", "tbeam"]
    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == ["rak4631-", "tbeam"]


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_keep_last_beta_non_interactive(mock_input):
    """Non-interactive runs should keep the existing KEEP_LAST_BETA setting."""
    config = {"KEEP_LAST_BETA": True}

    mock_input.side_effect = ["2", "n"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=True, default_versions=2
        )

    assert result["KEEP_LAST_BETA"] is True
    assert mock_input.call_count == 2


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_keep_last_beta_interactive(mock_input):
    """Interactive runs should prompt for KEEP_LAST_BETA."""
    config = {"KEEP_LAST_BETA": False}

    mock_input.side_effect = ["2", "y", "n"]

    with patch("sys.stdin.isatty", return_value=True), patch.dict(
        os.environ, {"CI": ""}
    ):
        result = setup_config._setup_firmware(
            config, is_first_run=True, default_versions=2
        )

    assert result["KEEP_LAST_BETA"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_extraction_tips_only_when_enabled(mock_input, capsys):
    """Extraction tips should only appear when auto-extract is enabled."""
    config = {"CHECK_PRERELEASES": False}

    mock_input.side_effect = ["2", "n"]

    with patch("sys.stdin.isatty", return_value=False):
        setup_config._setup_firmware(config, is_first_run=True, default_versions=2)

    captured = capsys.readouterr()
    assert "File Extraction Configuration" not in captured.out
    assert "Tips for precise selection" not in captured.out


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_extraction_tips_when_enabled(mock_input, capsys):
    """Extraction tips should appear after auto-extract is enabled."""
    config = {"CHECK_PRERELEASES": False}

    mock_input.side_effect = ["2", "y", "", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        setup_config._setup_firmware(config, is_first_run=True, default_versions=2)

    captured = capsys.readouterr()
    assert "File Extraction Configuration" in captured.out
    assert "Tips for precise selection" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_migration_accept(mock_input):
    """Test migration from EXTRACT_PATTERNS to SELECTED_PRERELEASE_ASSETS when user accepts."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": True,
        "EXTRACT_PATTERNS": ["station-", "heltec-", "rak4631-"],
        "AUTO_EXTRACT": True,
    }

    # Simulate user inputs: keep 2 versions, keep auto-extract, keep current extraction patterns
    mock_input.side_effect = ["2", "y", "y", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == [
        "station-",
        "heltec-",
        "rak4631-",
    ]
    assert result["EXTRACT_PATTERNS"] == ["station-", "heltec-", "rak4631-"]
    assert result["AUTO_EXTRACT"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_migration_decline(mock_input):
    """Test migration from EXTRACT_PATTERNS to SELECTED_PRERELEASE_ASSETS when user declines."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": True,
        "EXTRACT_PATTERNS": ["station-", "heltec-"],
        "AUTO_EXTRACT": True,
    }

    # Simulate user inputs: keep 2 versions, keep auto-extract, change extraction patterns, new patterns
    mock_input.side_effect = ["2", "y", "n", "esp32- rak4631-", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == ["esp32-", "rak4631-"]
    assert result["EXTRACT_PATTERNS"] == ["esp32-", "rak4631-"]
    assert result["AUTO_EXTRACT"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_existing_keep(mock_input):
    """Test keeping existing SELECTED_PRERELEASE_ASSETS configuration."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "CHECK_PRERELEASES": True,
        "SELECTED_PRERELEASE_ASSETS": ["tbeam", "t1000-e-"],
        "AUTO_EXTRACT": False,
    }

    # Simulate user inputs: keep 3 versions, no auto-extract
    mock_input.side_effect = ["3", "n"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == []
    assert result["AUTO_EXTRACT"] is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_existing_change(mock_input):
    """Test changing existing SELECTED_PRERELEASE_ASSETS configuration."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "CHECK_PRERELEASES": True,
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": ["old-pattern"],
    }

    # Simulate user inputs: keep 3 versions, keep auto-extract, don't keep patterns, new patterns
    mock_input.side_effect = ["3", "y", "n", "new-pattern device-", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["EXTRACT_PATTERNS"] == ["new-pattern", "device-"]
    assert result["SELECTED_PRERELEASE_ASSETS"] == ["new-pattern", "device-"]
    assert result["AUTO_EXTRACT"] is True


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_disabled_prereleases(mock_input):
    """Test that SELECTED_PRERELEASE_ASSETS is cleared when prereleases are disabled."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": False,
        "SELECTED_PRERELEASE_ASSETS": ["rak4631-", "tbeam"],
        "AUTO_EXTRACT": False,
    }

    # Simulate user inputs: keep 2 versions, no auto-extract
    mock_input.side_effect = ["2", "n"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is False
    assert result["SELECTED_PRERELEASE_ASSETS"] == []  # Should be cleared
    assert result["AUTO_EXTRACT"] is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_empty_patterns(mock_input):
    """Test handling of empty prerelease asset patterns."""
    config = {"CHECK_PRERELEASES": True}

    # Simulate user inputs: 2 versions, yes to auto-extract, empty patterns
    mock_input.side_effect = ["2", "y", "", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=True, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == []
    assert result["AUTO_EXTRACT"] is False  # Should be disabled with empty patterns


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_selected_prerelease_assets_migration_empty_input(mock_input):
    """Test migration scenario when user provides empty input after declining migration."""
    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "CHECK_PRERELEASES": True,
        "EXTRACT_PATTERNS": ["station-", "heltec-"],
        "AUTO_EXTRACT": False,
    }

    # Simulate user inputs: keep 2 versions, yes to auto-extract, decline to keep patterns, empty input
    mock_input.side_effect = ["2", "y", "n", "", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["CHECK_PRERELEASES"] is True
    assert result["SELECTED_PRERELEASE_ASSETS"] == []  # Empty when no input provided
    assert result["AUTO_EXTRACT"] is False  # Should be disabled with empty patterns


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
def test_setup_firmware_extract_patterns_string_config(mock_input):
    """EXTRACT_PATTERNS should be split when provided as a string."""
    config = {
        "CHECK_PRERELEASES": True,
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": "tbeam rak4631-",
    }

    mock_input.side_effect = ["2", "y", "y", "y"]

    with patch("sys.stdin.isatty", return_value=False):
        result = setup_config._setup_firmware(
            config, is_first_run=False, default_versions=2
        )

    assert result["EXTRACT_PATTERNS"] == ["tbeam", "rak4631-"]
    assert result["SELECTED_PRERELEASE_ASSETS"] == ["tbeam", "rak4631-"]


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_base_windows_existing_menu_shortcuts(mocker):
    """Windows setup should prompt to update existing Start Menu shortcuts."""
    config = {}
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mocker.patch("fetchtastic.setup_config.create_config_shortcut")
    mocker.patch("fetchtastic.setup_config.create_windows_menu_shortcuts")
    mocker.patch("fetchtastic.setup_config.os.makedirs")
    mocker.patch(
        "fetchtastic.setup_config.os.path.exists",
        side_effect=lambda path: path == setup_config.WINDOWS_START_MENU_FOLDER,
    )

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=["C:\\Fetchtastic", "y"],
    )

    setup_config._setup_base(
        config, is_partial_run=False, is_first_run=True, wants=lambda _: True
    )

    setup_config.create_windows_menu_shortcuts.assert_called_once_with(
        setup_config.CONFIG_FILE, setup_config.BASE_DIR
    )


# Test helper functions for configuration handling


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_selected_assets_key():
    """Test get_prerelease_patterns with SELECTED_PRERELEASE_ASSETS key."""
    from fetchtastic.download import get_prerelease_patterns

    config = {"SELECTED_PRERELEASE_ASSETS": ["rak4631-", "tbeam"]}
    result = get_prerelease_patterns(config)

    assert result == ["rak4631-", "tbeam"]


def test_windows_modules_import_success(mocker, reload_setup_config_module):
    """Test successful Windows modules import."""
    _ = reload_setup_config_module  # ensure fixture exercised (lint)
    # Mock platform.system to return Windows
    mocker.patch("platform.system", return_value="Windows")

    # Mock successful imports - only mock winshell since that's what's imported at module level
    mock_winshell = mocker.MagicMock()

    import sys

    with patch.dict(
        sys.modules,
        {
            "winshell": mock_winshell,
        },
    ):
        # Reload the module to test import logic
        import importlib

        importlib.reload(setup_config)

        # Should have Windows modules available
        assert setup_config.WINDOWS_MODULES_AVAILABLE is True


def test_non_windows_platform_no_modules(mocker, reload_setup_config_module):
    """Test non-Windows platform doesn't try to import Windows modules."""
    _ = reload_setup_config_module  # ensure fixture exercised (lint)
    # Mock platform.system to return Linux
    mocker.patch("platform.system", return_value="Linux")

    # Reload the module to test import logic
    import importlib

    importlib.reload(setup_config)

    # Should not have Windows modules available
    assert setup_config.WINDOWS_MODULES_AVAILABLE is False


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_github_no_token(capsys, monkeypatch):
    """Test _setup_github when no token is configured and user declines."""
    from fetchtastic.setup_config import _setup_github

    config = {}

    # Mock input to decline setting up token
    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = _setup_github(config)

    assert result is config  # Should return the same config dict
    captured = capsys.readouterr()
    assert "GitHub API requests have different rate limits:" in captured.out
    assert "60 requests per hour" in captured.out
    assert "5,000 requests per hour" in captured.out
    assert "83x more!" not in captured.out  # Ensure promotional text is removed


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_github_existing_token_keep(capsys, monkeypatch):
    """Test _setup_github when token exists and user keeps it."""
    from fetchtastic.setup_config import _setup_github

    config = {
        "GITHUB_TOKEN": "fake_existing_token_12345678901234567890"
    }  # nosec S105 (test-only)

    # Mock input to keep existing token
    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = _setup_github(config)

    assert result is config
    assert result["GITHUB_TOKEN"] == "fake_existing_token_12345678901234567890"
    captured = capsys.readouterr()
    assert "Current status: Token configured" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_github_set_new_token(capsys, monkeypatch):
    """Test _setup_github when user sets a new token."""
    from fetchtastic.setup_config import _setup_github

    config = {}

    # Mock inputs: yes to setup, enter valid token
    inputs = [
        "y",
        "ghp_fake_token_for_testing_123456789012345678901234",
    ]  # nosec S105 (test-only)
    input_iter = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _: next(input_iter))
    monkeypatch.setattr(
        "getpass.getpass",
        lambda _: "ghp_fake_token_for_testing_123456789012345678901234",  # nosec S105 (test-only)
    )

    result = _setup_github(config)

    assert result is config
    assert (
        result["GITHUB_TOKEN"] == "ghp_fake_token_for_testing_123456789012345678901234"
    )
    captured = capsys.readouterr()
    assert "GitHub API requests have different rate limits:" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_selected_assets_none():
    """Test get_prerelease_patterns with SELECTED_PRERELEASE_ASSETS set to None."""
    from fetchtastic.download import get_prerelease_patterns

    config = {"SELECTED_PRERELEASE_ASSETS": None}
    result = get_prerelease_patterns(config)

    assert result == []  # Should return empty list, not None


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_selected_assets_empty():
    """Test get_prerelease_patterns with empty SELECTED_PRERELEASE_ASSETS."""
    from fetchtastic.download import get_prerelease_patterns

    config = {"SELECTED_PRERELEASE_ASSETS": []}
    result = get_prerelease_patterns(config)

    assert result == []


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_fallback_to_extract_patterns():
    """Test get_prerelease_patterns fallback to EXTRACT_PATTERNS."""
    from fetchtastic.download import get_prerelease_patterns

    config = {"EXTRACT_PATTERNS": ["station-", "heltec-"]}

    with patch("fetchtastic.log_utils.logger") as mock_logger:
        result = get_prerelease_patterns(config)

        assert result == ["station-", "heltec-"]
        mock_logger.warning.assert_called_once()
        assert "deprecated" in mock_logger.warning.call_args[0][0].lower()


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_no_keys():
    """Test get_prerelease_patterns with no configuration keys."""
    from fetchtastic.download import get_prerelease_patterns

    config = {}
    result = get_prerelease_patterns(config)

    assert result == []


@pytest.mark.configuration
@pytest.mark.unit
def testget_prerelease_patterns_precedence():
    """Test that SELECTED_PRERELEASE_ASSETS takes precedence over EXTRACT_PATTERNS."""
    from fetchtastic.download import get_prerelease_patterns

    config = {
        "SELECTED_PRERELEASE_ASSETS": ["new-pattern"],
        "EXTRACT_PATTERNS": ["old-pattern"],
    }

    with patch("fetchtastic.log_utils.logger") as mock_logger:
        result = get_prerelease_patterns(config)

        assert result == ["new-pattern"]
        mock_logger.warning.assert_not_called()  # No deprecation warning when using new key
