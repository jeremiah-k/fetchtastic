# Targeted tests for uncovered lines in setup_config.py
# Focus on DESKTOP-related configuration and other uncovered paths

import os
import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

import fetchtastic.setup_config as setup_config
from fetchtastic.client_app_config import DEFAULT_APP_VERSIONS_TO_KEEP

# Tests for uncovered lines 112, 153-156: cron command decorator edge cases


@pytest.mark.configuration
@pytest.mark.unit
def test_cron_command_required_edge_case_none_crontab_path(mocker):
    """Test cron_command_required when shutil.which returns None on second check."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value=None)
    mocker.patch("fetchtastic.setup_config.logger")

    @setup_config.cron_command_required
    def test_func(*, crontab_path: str = "crontab") -> str:
        return f"Called with {crontab_path}"

    result = test_func()
    assert result is None


@pytest.mark.configuration
@pytest.mark.unit
def test_cron_check_command_required_edge_case_none_path(mocker):
    """Test cron_check_command_required when shutil.which returns None on second check."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value=None)
    mocker.patch("fetchtastic.setup_config.logger")

    @setup_config.cron_check_command_required
    def test_func(*, crontab_path: str = "crontab") -> bool:
        return True

    result = test_func()
    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_cron_check_command_required_non_str_path(mocker):
    """Test cron_check_command_required when shutil.which returns non-string (rare edge case)."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value=123)  # Non-string type
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    @setup_config.cron_check_command_required
    def test_func(*, crontab_path: str = "crontab") -> bool:
        return crontab_path == "crontab"

    result = test_func()
    assert result is True
    mock_logger.debug.assert_called_once()


# Tests for desktop-related configuration in _setup_downloads


@pytest.mark.configuration
@pytest.mark.unit
def test_get_desktop_assets_prefers_new_key_even_when_empty():
    """Explicit empty new key should not fall back to legacy key."""
    config = {
        "SELECTED_DESKTOP_ASSETS": [],
        "SELECTED_DESKTOP_PLATFORMS": ["legacy-value"],
    }

    assert setup_config._get_desktop_assets(config) == []


@pytest.mark.configuration
@pytest.mark.unit
def test_parse_non_negative_int_rejects_negative():
    """Negative values should be rejected by non-negative parser."""
    assert setup_config._parse_non_negative_int("3") == 3
    assert setup_config._parse_non_negative_int("-1") is None
    assert setup_config._parse_non_negative_int("invalid") is None


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_full_run_desktop_only(mocker):
    """Test full run with desktop-only choice uses unified app menu."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=["d", "n"],  # desktop choice, no prerelease
    )
    mock_menu = mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value={"selected_assets": ["meshtastic.dmg"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    assert save_apks is True  # Desktop assets count as client apps
    assert save_firmware is False
    assert updated["SAVE_DESKTOP_APP"] is True
    assert "meshtastic.dmg" in updated["SELECTED_DESKTOP_ASSETS"]
    mock_menu.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_full_run_reprompts_invalid_choice(mocker, capsys):
    """Invalid full-run asset choices should reprompt until a valid token is entered."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=[
            "invalid-choice",
            "d",
            "n",
        ],  # invalid, desktop choice, no prerelease
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value={"selected_assets": ["meshtastic.dmg"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    captured = capsys.readouterr()
    assert "Invalid choice. Please enter a, f, d, m, b, or n." in captured.out
    assert save_apks is True  # Desktop assets count as client apps
    assert save_firmware is False
    assert updated["SAVE_DESKTOP_APP"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_full_run_multiple_selection(mocker):
    """Test full run with multiple selection (lines 779-789, 796-798)."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=[
            "m",
            "y",
            "y",
            "y",
            "n",
            "n",
            "n",
            "n",
        ],
    )
    mocker.patch(
        "fetchtastic.menu_firmware.run_menu",
        return_value={"selected_assets": ["rak4631"]},
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value={"selected_assets": ["meshtastic.apk", "meshtastic.dmg"]},
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    assert save_apks is True
    assert save_firmware is True
    assert updated["SAVE_DESKTOP_APP"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_full_run_none_selection(mocker):
    """Test full run with 'none' selection (lines 796-798)."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=["n"],  # none
    )

    updated, save_apks, save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    assert save_apks is False
    assert save_firmware is False
    assert updated["SAVE_DESKTOP_APP"] is False


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_desktop_section(mocker):
    """Test partial run with desktop section (lines 827-836)."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": False,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_DESKTOP_PLATFORMS": ["meshtastic.dmg"],
    }

    def wants(section: str) -> bool:
        return section == "app"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "y", "n"],  # Keep desktop enabled, re-run menu, no prerelease
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value={"selected_assets": ["meshtastic.appimage"]},
    )

    updated, _, _ = _setup_downloads(config, is_partial_run=True, wants=wants)

    assert updated["SAVE_DESKTOP_APP"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_desktop_keep_existing(mocker):
    """Test partial run with desktop section keeping existing selection (lines 929-936)."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": False,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_DESKTOP_PLATFORMS": ["meshtastic.dmg"],
    }

    def wants(section: str) -> bool:
        return section == "app"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "n", "n"],  # Keep desktop, don't re-run menu, no prerelease
    )
    mock_menu = mocker.patch("fetchtastic.menu_app.run_menu")

    updated, _, _ = _setup_downloads(config, is_partial_run=True, wants=wants)

    assert updated["SELECTED_DESKTOP_ASSETS"] == ["meshtastic.dmg"]
    mock_menu.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_desktop_no_existing_selection(mocker):
    """Test partial run with app section but no existing selection."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": False,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_DESKTOP_ASSETS": [],
    }

    def wants(section: str) -> bool:
        return section == "app"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "n"],  # Keep client apps enabled, no prerelease
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value=None,
    )

    updated, _, _ = _setup_downloads(config, is_partial_run=True, wants=wants)

    assert updated["SAVE_DESKTOP_APP"] is False  # Disabled because no selection
    assert updated["SELECTED_DESKTOP_ASSETS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_desktop_no_selection(mocker):
    """Test client app selection when user selects no assets."""
    from fetchtastic.setup_config import _setup_downloads

    config = {}

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=[
            "d",  # desktop choice (uses unified menu)
            "n",  # no prerelease
        ],
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value=None,  # No selection made
    )

    updated, _, _ = _setup_downloads(config, is_partial_run=False, wants=wants)

    assert updated["SAVE_DESKTOP_APP"] is False
    assert updated["SELECTED_DESKTOP_ASSETS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_save_desktop_false_clears_config(mocker):
    """Test that when only APK assets are selected, desktop config is cleared."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_DESKTOP_APP": True,
        "CHECK_DESKTOP_PRERELEASES": True,
        "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg"],
    }

    def wants(_section: str) -> bool:
        return True

    mocker.patch(
        "builtins.input",
        side_effect=[
            "a",  # APK/client app choice
            "n",  # no prerelease
            "n",  # no channel suffix
        ],
    )
    mocker.patch(
        "fetchtastic.menu_app.run_menu",
        return_value={"selected_assets": ["meshtastic.apk"]},
    )

    updated, _save_apks, _save_firmware = _setup_downloads(
        config, is_partial_run=False, wants=wants
    )

    assert updated["SAVE_DESKTOP_APP"] is False
    assert updated["CHECK_DESKTOP_PRERELEASES"] is False
    assert updated["SELECTED_DESKTOP_ASSETS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_non_desktop_preserves_desktop_state(mocker):
    """Partial runs outside desktop should not clear saved desktop selections."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": False,
        "SAVE_DESKTOP_APP": False,
        "CHECK_DESKTOP_PRERELEASES": True,
        "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg"],
    }

    def wants(section: str) -> bool:
        return section == "app"

    mocker.patch("builtins.input", side_effect=["n"])

    updated, _, _ = _setup_downloads(config, is_partial_run=True, wants=wants)

    assert updated["SAVE_DESKTOP_APP"] is False
    assert updated["CHECK_DESKTOP_PRERELEASES"] is True
    assert updated["SELECTED_DESKTOP_ASSETS"] == ["meshtastic.dmg"]


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_backward_compat_old_key(mocker):
    """Test backward compatibility: Old SELECTED_DESKTOP_PLATFORMS key should still work."""
    from fetchtastic.setup_config import _setup_downloads

    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": False,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_DESKTOP_PLATFORMS": ["meshtastic.dmg"],  # Using old key
    }

    def wants(section: str) -> bool:
        return section == "app"

    mocker.patch(
        "builtins.input",
        side_effect=["y", "n", "n"],  # Keep desktop, don't re-run menu, no prerelease
    )
    mock_menu = mocker.patch("fetchtastic.menu_app.run_menu")

    updated, _, _ = _setup_downloads(config, is_partial_run=True, wants=wants)

    # New key should be set, old key should be removed (migration complete)
    assert updated["SELECTED_DESKTOP_ASSETS"] == ["meshtastic.dmg"]
    assert (
        "SELECTED_DESKTOP_PLATFORMS" not in updated
    )  # Old key removed after migration
    mock_menu.assert_not_called()


# Tests for _disable_asset_downloads function


@pytest.mark.configuration
@pytest.mark.unit
def test_disable_asset_downloads_firmware_with_message():
    """Test _disable_asset_downloads with firmware asset type."""
    from fetchtastic.setup_config import _disable_asset_downloads

    config = {
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["test"],
        "CHECK_PRERELEASES": True,
    }

    updated, result = _disable_asset_downloads(
        config, "firmware", "Custom message about firmware."
    )

    assert updated["SAVE_FIRMWARE"] is False
    assert updated["SELECTED_FIRMWARE_ASSETS"] == []
    assert updated["CHECK_PRERELEASES"] is False
    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_disable_asset_downloads_apk_with_default_message(capsys):
    """Test _disable_asset_downloads with APK using default message (lines 719-722)."""
    from fetchtastic.setup_config import _disable_asset_downloads

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["test.apk"],
        "CHECK_APK_PRERELEASES": True,
    }

    updated, _ = _disable_asset_downloads(config, "APK")
    captured = capsys.readouterr()

    assert "No APK assets selected" in captured.out
    assert updated["SAVE_APKS"] is False
    assert updated["SELECTED_APK_ASSETS"] == []
    assert updated["CHECK_APK_PRERELEASES"] is False


# Tests for check_storage_setup (lines 614-617)


@pytest.mark.configuration
@pytest.mark.unit
def test_check_storage_setup_non_interactive(mocker):
    """Test check_storage_setup in non-interactive environment (lines 613-617)."""
    mocker.patch("sys.stdin.isatty", return_value=False)
    mocker.patch.dict(os.environ, {"CI": "true"})

    result = setup_config.check_storage_setup()

    assert result is False


# Tests for _setup_android uncovered lines


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_android_invalid_number_current_value(mocker, capsys):
    """Test _setup_android with invalid number in current value (lines 1043-1049)."""
    from fetchtastic.setup_config import _setup_android

    config = {
        "ANDROID_VERSIONS_TO_KEEP": "invalid",  # Invalid current value
    }

    mocker.patch(
        "builtins.input",
        side_effect=["also_invalid"],  # User also enters invalid
    )

    result = _setup_android(config, is_first_run=False, default_versions=3)
    captured = capsys.readouterr()

    assert result["ANDROID_VERSIONS_TO_KEEP"] == 3  # Falls back to default
    assert "Invalid current value — using default." in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_android_non_first_run_prompt(mocker):
    """Test _setup_android non-first run prompt text (line 1037)."""
    from fetchtastic.setup_config import _setup_android

    config = {"ANDROID_VERSIONS_TO_KEEP": 5}

    mock_input = mocker.patch("builtins.input", return_value="5")

    _setup_android(config, is_first_run=False, default_versions=2)

    call_args = mock_input.call_args[0][0]
    assert "current:" in call_args
    assert "5" in call_args


# Tests for _setup_firmware uncovered lines


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_invalid_number_current_value(mocker, capsys):
    """Test _setup_firmware with invalid number in current value (lines 1167-1169)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "FIRMWARE_VERSIONS_TO_KEEP": "invalid",
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": False,
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "also_invalid",
            "n",
        ],  # User enters invalid, then no to auto-extract
    )

    result = _setup_firmware(config, is_first_run=False, default_versions=3)
    captured = capsys.readouterr()

    assert result["FIRMWARE_VERSIONS_TO_KEEP"] == 3  # Falls back to default
    assert "Invalid number in current value" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_extract_patterns_unexpected_type(mocker, capsys):
    """Test _setup_firmware with unexpected EXTRACT_PATTERNS type (lines 1234-1238)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": 12345,  # Unexpected integer type
    }

    mock_logger = mocker.patch("fetchtastic.setup_config.logger")
    mocker.patch(
        "builtins.input",
        side_effect=[
            "2",
            "y",
            "",
            "y",
        ],  # versions, auto-extract yes, empty patterns, confirm
    )

    result = _setup_firmware(config, is_first_run=True, default_versions=2)

    mock_logger.warning.assert_called_once()
    assert "Unexpected type" in mock_logger.warning.call_args[0][0]
    assert result["EXTRACT_PATTERNS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_no_patterns_disables_auto_extract(mocker, capsys):
    """Test _setup_firmware when no patterns provided disables auto-extract (lines 1261-1264)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": False,
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "2",
            "y",
            "",
            "y",
        ],  # versions, auto-extract yes, empty patterns, confirm
    )

    result = _setup_firmware(config, is_first_run=True, default_versions=2)
    captured = capsys.readouterr()

    assert result["AUTO_EXTRACT"] is False
    assert "No extraction patterns provided; disabling auto-extract" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_reconfigure_patterns(mocker):
    """Test _setup_firmware reconfiguring patterns (lines 1289-1292)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": ["old-pattern"],
        "EXCLUDE_PATTERNS": ["old-exclude"],
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "2",  # versions
            "y",  # auto-extract yes
            "n",  # don't keep current patterns
            "new-pattern",  # new patterns
            "y",  # confirm correct
        ],
    )

    result = _setup_firmware(config, is_first_run=True, default_versions=2)

    assert "new-pattern" in result["EXTRACT_PATTERNS"]


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_auto_extract_off_clears_patterns(mocker):
    """Test _setup_firmware when auto-extract is turned off (lines 1293-1297)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": ["old-pattern"],
        "EXCLUDE_PATTERNS": ["old-exclude"],
    }

    mocker.patch(
        "builtins.input",
        side_effect=["2", "n"],  # versions, auto-extract no
    )

    result = _setup_firmware(config, is_first_run=True, default_versions=2)

    assert result["AUTO_EXTRACT"] is False
    assert result["EXTRACT_PATTERNS"] == []
    assert result["EXCLUDE_PATTERNS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_firmware_no_auto_extract_no_exclude_patterns(mocker):
    """Test _setup_firmware when auto-extract is False (lines 1269-1270)."""
    from fetchtastic.setup_config import _setup_firmware

    config = {
        "CHECK_PRERELEASES": False,
        "AUTO_EXTRACT": False,
    }

    mocker.patch(
        "builtins.input",
        side_effect=["2", "n"],  # versions, auto-extract no
    )

    result = _setup_firmware(config, is_first_run=True, default_versions=2)

    assert result["AUTO_EXTRACT"] is False
    assert result["EXTRACT_PATTERNS"] == []
    assert result["EXCLUDE_PATTERNS"] == []


# Tests for configure_exclude_patterns uncovered lines


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_no_defaults_custom_patterns(mocker):
    """Test configure_exclude_patterns with no defaults and custom patterns (lines 1112-1126)."""
    from fetchtastic.setup_config import configure_exclude_patterns

    mocker.patch("sys.stdin.isatty", return_value=True)
    mocker.patch.dict(os.environ, {"CI": ""})
    mocker.patch(
        "builtins.input",
        side_effect=[
            "n",  # don't use defaults
            "custom1 custom2",  # custom patterns
        ],
    )

    result = configure_exclude_patterns({})

    assert "custom1" in result
    assert "custom2" in result


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_no_defaults_empty_custom(mocker):
    """Test configure_exclude_patterns with no defaults and empty custom patterns (lines 1115-1123)."""
    from fetchtastic.setup_config import configure_exclude_patterns

    mocker.patch("sys.stdin.isatty", return_value=True)
    mocker.patch.dict(os.environ, {"CI": ""})
    mocker.patch(
        "builtins.input",
        side_effect=[
            "n",  # don't use defaults
            "",  # empty custom patterns
        ],
    )

    result = configure_exclude_patterns({})

    assert result == []


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_additional_patterns(mocker):
    """Test configure_exclude_patterns with additional patterns (lines 1108-1113)."""
    from fetchtastic.setup_config import configure_exclude_patterns

    mocker.patch("sys.stdin.isatty", return_value=True)
    mocker.patch.dict(os.environ, {"CI": ""})
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # use defaults
            "y",  # add more patterns
            "extra1 extra2",  # additional patterns
        ],
    )

    result = configure_exclude_patterns({})

    assert "extra1" in result
    assert "extra2" in result
    # Check defaults are still there
    assert "*.hex" in result


# Tests for get_downloads_dir uncovered line 545


@pytest.mark.configuration
@pytest.mark.unit
def test_get_downloads_dir_fallback_to_home(mocker):
    """Test get_downloads_dir fallback to home when Downloads doesn't exist (line 545)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("os.path.expanduser", return_value="/home/testuser")
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: False,  # Neither Downloads nor Download exist
    )

    result = setup_config.get_downloads_dir()

    assert result == "/home/testuser"


# Tests for Windows-related automation (lines 1406-1432, 1449-1457)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_windows_remove_shortcut(mocker):
    """Test _setup_automation on Windows removing existing startup shortcut (lines 1406-1432)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    startup_folder = "C:\\Startup"
    mock_winshell.startup.return_value = startup_folder

    mocker.patch("os.remove")
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: (
            path == os.path.join(startup_folder, "Fetchtastic.lnk")
            or path == startup_folder
        ),
    )
    mocker.patch(
        "builtins.input",
        side_effect=["y"],  # remove shortcut
    )

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)

    assert result == config


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_windows_keep_shortcut(mocker, capsys):
    """Test _setup_automation on Windows keeping existing startup shortcut (lines 1431-1457)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    startup_folder = "C:\\Startup"
    mock_winshell.startup.return_value = startup_folder

    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: (
            path == os.path.join(startup_folder, "Fetchtastic.lnk")
        ),
    )
    mocker.patch(
        "builtins.input",
        side_effect=["n"],  # don't remove shortcut
    )

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    assert "continue to run automatically" in captured.out


# Tests for Termux automation (lines 1481, 1489-1505)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_termux_unchanged_cron(mocker, capsys):
    """Test _setup_automation on Termux leaving cron unchanged (lines 1481)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.check_boot_script_exists", return_value=False
    )

    mocker.patch(
        "builtins.input",
        side_effect=["n", "y"],  # don't reconfigure cron, yes to boot script
    )
    mock_setup_boot = mocker.patch("fetchtastic.setup_config.setup_boot_script")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    assert "Cron job configuration left unchanged" in captured.out
    mock_setup_boot.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_termux_unchanged_boot(mocker, capsys):
    """Test _setup_automation on Termux leaving boot script unchanged (lines 1504-1505)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_boot_script_exists", return_value=True)

    mocker.patch(
        "builtins.input",
        side_effect=["n", "n"],  # don't reconfigure either
    )

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    assert "Boot script configuration left unchanged" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_termux_reconfigure_boot(mocker, capsys):
    """Test _setup_automation on Termux reconfiguring boot script (lines 1489-1503)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_boot_script_exists", return_value=True)

    mocker.patch(
        "builtins.input",
        side_effect=["n", "y"],  # don't reconfigure cron, reconfigure boot
    )
    mock_remove_boot = mocker.patch("fetchtastic.setup_config.remove_boot_script")
    mock_setup_boot = mocker.patch("fetchtastic.setup_config.setup_boot_script")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    capsys.readouterr()

    assert result == config
    mock_remove_boot.assert_called_once()
    mock_setup_boot.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_termux_no_boot_setup(mocker, capsys):
    """Test _setup_automation on Termux declining boot script setup (lines 1515-1519)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.check_boot_script_exists", return_value=False
    )

    mocker.patch(
        "builtins.input",
        side_effect=["n", "n"],  # don't reconfigure cron, no boot script
    )

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    assert "Boot script has not been set up" in captured.out


# Tests for Linux automation (lines 1557-1558, 1562)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_linux_no_reboot_setup(mocker, capsys):
    """Test _setup_automation on Linux declining reboot cron (lines 1578)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
    mocker.patch(
        "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False
    )

    mocker.patch(
        "builtins.input",
        side_effect=[
            "h",  # hourly cron
            "n",  # no reboot cron
        ],
    )
    mock_setup_cron = mocker.patch("fetchtastic.setup_config.setup_cron_job")

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    mock_setup_cron.assert_called_once_with("hourly")
    assert "Reboot cron job has not been set up" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_linux_unchanged_config(mocker, capsys):
    """Test _setup_automation on Linux leaving cron unchanged (lines 1561-1562)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=True
    )

    mocker.patch(
        "builtins.input",
        side_effect=["n"],  # don't reconfigure
    )

    config = {}
    result = setup_config._setup_automation(config, False, lambda _: True)
    captured = capsys.readouterr()

    assert result == config
    assert "Cron job configurations left unchanged" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_automation_linux_reconfigure_no_reboot(mocker):
    """Test _setup_automation on Linux reconfiguring but no reboot (lines 1557-1558)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("fetchtastic.setup_config.check_cron_job_exists", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=True
    )

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # reconfigure
            "h",  # hourly
            "n",  # no reboot
        ],
    )
    mock_remove_cron = mocker.patch("fetchtastic.setup_config.remove_cron_job")
    mock_remove_reboot = mocker.patch("fetchtastic.setup_config.remove_reboot_cron_job")
    mock_setup_cron = mocker.patch("fetchtastic.setup_config.setup_cron_job")

    config = {}
    setup_config._setup_automation(config, False, lambda _: True)

    mock_remove_cron.assert_called_once()
    mock_remove_reboot.assert_called_once()
    mock_setup_cron.assert_called_once_with("hourly")


# Tests for migrate_pip_to_pipx (lines 436-438, 440-441, 482->491, 488-489, 501-506)


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_local_pipx_fallback(mocker):
    """Test migrate_pip_to_pipx using local pipx fallback (lines 436-438)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("builtins.input", return_value="y")

    # First which call returns None (no pipx), second call also None
    which_side_effects = [None, None, "/usr/bin/pip"]  # pipx, local_pipx, pip
    mocker.patch("shutil.which", side_effect=which_side_effects)
    mocker.patch("builtins.open", mock_open(read_data="config: data"))

    # Use side_effect to return True for both config file and local pipx path
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: (
            path == setup_config.CONFIG_FILE or ".local/bin/pipx" in path
        ),
    )

    mock_subprocess = mocker.patch("fetchtastic.setup_config.subprocess.run")
    mock_subprocess.return_value = MagicMock(returncode=0)

    result = setup_config.migrate_pip_to_pipx()

    assert result is True


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_pipx_not_found_after_install(mocker, capsys):
    """Test migrate_pip_to_pipx when pipx not found after install (lines 440-441)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("builtins.input", return_value="y")

    # pipx never found
    mocker.patch("shutil.which", return_value=None)
    # Use side_effect to return True for config file check, False for local pipx check
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: path == setup_config.CONFIG_FILE,
    )
    mocker.patch("builtins.open", mock_open(read_data="config: data"))

    mock_subprocess = mocker.patch("fetchtastic.setup_config.subprocess.run")
    mock_subprocess.return_value = MagicMock(returncode=0)

    result = setup_config.migrate_pip_to_pipx()
    captured = capsys.readouterr()

    assert result is False
    assert "pipx executable not found after installation" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_restore_config_error(mocker, capsys):
    """Test migrate_pip_to_pipx with config restore error (lines 488-489)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.input", return_value="y")
    mocker.patch("shutil.which", return_value="/usr/bin/pipx")
    mocker.patch("builtins.open", mock_open(read_data="config: data"))

    mock_subprocess = mocker.patch("fetchtastic.setup_config.subprocess.run")
    mock_subprocess.return_value = MagicMock(returncode=0)

    # Make the config write fail
    mocker.patch("os.makedirs")
    mock_open_func = mock_open(read_data="config: data")
    mock_open_func.return_value.write.side_effect = OSError("Write error")
    mocker.patch("builtins.open", mock_open_func)

    result = setup_config.migrate_pip_to_pipx()
    captured = capsys.readouterr()

    assert result is True  # Migration still succeeds
    assert "Warning: Could not restore configuration" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_migration_failure(mocker, capsys):
    """Test migrate_pip_to_pipx with migration failure (lines 501-506)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.input", return_value="y")
    mocker.patch("shutil.which", return_value="/usr/bin/pipx")
    mocker.patch("builtins.open", mock_open(read_data="config: data"))

    mock_subprocess = mocker.patch("fetchtastic.setup_config.subprocess.run")

    # Let earlier steps succeed; fail only the pipx install call
    def run_side_effect(cmd, *args, **kwargs):
        if cmd[:2] == ["/usr/bin/pipx", "install"]:
            return MagicMock(returncode=1, stderr="Install failed")
        return MagicMock(returncode=0, stderr="")

    mock_subprocess.side_effect = run_side_effect

    result = setup_config.migrate_pip_to_pipx()
    captured = capsys.readouterr()

    assert result is False
    assert "Failed to install with pipx" in captured.out


# Tests for _safe_input EOF handling (line 72-73)


@pytest.mark.configuration
@pytest.mark.unit
def test_safe_input_eof_error(mocker):
    """Test _safe_input handles EOFError (lines 72-73)."""
    mocker.patch("builtins.input", side_effect=EOFError())

    result = setup_config._safe_input("Prompt: ", default="default_value")

    assert result == "default_value"


@pytest.mark.configuration
@pytest.mark.unit
def test_safe_input_keyboard_interrupt(mocker):
    """Test _safe_input handles KeyboardInterrupt (lines 72-73)."""
    mocker.patch("builtins.input", side_effect=KeyboardInterrupt())

    result = setup_config._safe_input("Prompt: ", default="default_value")

    assert result == "default_value"


# Tests for run_setup exit handling (lines 2206->2210, 2210->exit)


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Windows")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_app.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
@patch("fetchtastic.setup_config.winshell", MagicMock(), create=True)
@patch("fetchtastic.setup_config.create_windows_menu_shortcuts")
@patch("fetchtastic.setup_config.create_config_shortcut")
@patch("shutil.which")
def test_run_setup_windows_cmd_environment(
    mock_shutil_which,
    mock_create_config_shortcut,
    mock_create_windows_menu_shortcuts,
    mock_menu_firmware,
    mock_menu_app,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
    tmp_path,
):
    """Test Windows setup when running from cmd.exe (lines 2206-2214)."""
    # Save original values and patch CONFIG_DIR/CONFIG_FILE
    original_config_dir = setup_config.CONFIG_DIR
    original_config_file = setup_config.CONFIG_FILE
    setup_config.CONFIG_DIR = str(tmp_path / "test_config")
    setup_config.CONFIG_FILE = str(tmp_path / "test_config" / "fetchtastic.yml")
    try:
        with patch.dict(os.environ, {"COMSPEC": "cmd.exe"}):
            user_inputs = [
                "",  # Use default base directory
                "n",  # Don't create menu shortcuts
                "b",  # Both client apps and firmware
                "n",  # No firmware prereleases
                "n",  # No client app prereleases
                "n",  # No channel suffixes
                "2",  # Keep 2 versions
                "2",  # Keep 2 versions
                "n",  # No auto-extract
                "n",  # No Startup shortcut
                "n",  # No NTFY
                "n",  # No GitHub token
                "",  # Press Enter to close (simulating the pause at end)
            ]
            mock_input.side_effect = user_inputs

            mock_menu_app.return_value = {"selected_assets": ["meshtastic.apk"]}
            mock_menu_firmware.return_value = {
                "selected_assets": ["meshtastic-firmware"]
            }

            with patch("builtins.open", mock_open()):
                with patch("sys.stdin.isatty", return_value=False):
                    setup_config.run_setup()

        mock_yaml_dump.assert_called()
    finally:
        # Restore original values
        setup_config.CONFIG_DIR = original_config_dir
        setup_config.CONFIG_FILE = original_config_file


# Tests for _setup_notifications clipboard (lines 1656-1671)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_notifications_termux_clipboard(mocker):
    """Test _setup_notifications clipboard handling on Termux (lines 1656-1671)."""
    from fetchtastic.setup_config import _setup_notifications

    mocker.patch(
        "fetchtastic.setup_config.is_termux", return_value=True
    )  # Also covers line 2245
    mocker.patch("fetchtastic.setup_config.copy_to_clipboard_func", return_value=True)

    config = {}

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # enable notifications
            "ntfy.sh",  # server
            "test-topic",  # topic
            "y",  # copy to clipboard
            "n",  # notify on download only
        ],
    )

    result = _setup_notifications(config)

    assert result["NTFY_TOPIC"] == "test-topic"
    assert result["NTFY_SERVER"] == "https://ntfy.sh"


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_notifications_non_termux_clipboard_fail(mocker, capsys):
    """Test _setup_notifications clipboard failure on non-Termux (lines 1669-1671)."""
    from fetchtastic.setup_config import _setup_notifications

    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.copy_to_clipboard_func", return_value=False)

    config = {}

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # enable notifications
            "ntfy.sh",  # server
            "test-topic",  # topic
            "y",  # copy to clipboard
            "n",  # notify on download only
        ],
    )

    result = _setup_notifications(config)
    captured = capsys.readouterr()

    assert result["NTFY_TOPIC"] == "test-topic"
    assert "Failed to copy to clipboard" in captured.out


# Tests for check_for_updates error handling (lines 2277, 2280-2281, 2284-2285)


@pytest.mark.configuration
@pytest.mark.unit
@patch("requests.get")
@patch("fetchtastic.setup_config.version")
def test_check_for_updates_network_error_logging(mock_version, mock_get, mocker):
    """Test check_for_updates network error logging (lines 2277, 2280-2281)."""
    mock_version.return_value = "1.0.0"
    mock_get.side_effect = requests.RequestException("Network timeout")
    mocker.patch("fetchtastic.setup_config.logger")

    current, latest, available = setup_config.check_for_updates()

    assert current == "1.0.0"
    assert latest is None
    assert available is False


@pytest.mark.configuration
@pytest.mark.unit
@patch("requests.get")
@patch("fetchtastic.setup_config.version")
def test_check_for_updates_data_error_logging(mock_version, mock_get, mocker):
    """Test check_for_updates data/parsing error logging (lines 2284-2285)."""
    mock_version.return_value = "1.0.0"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_get.return_value = mock_response
    mocker.patch("fetchtastic.setup_config.logger")

    current, latest, available = setup_config.check_for_updates()

    assert current == "1.0.0"
    assert latest is None
    assert available is False


# Tests for _setup_base uncovered lines (1929-1931, 1974, 2021-2029)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_base_failed_config_load(mocker, capsys):
    """Test _setup_base when existing config fails to load (lines 1929-1931)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch(
        "fetchtastic.setup_config.config_exists", return_value=(True, "/path/to/config")
    )
    mocker.patch("fetchtastic.setup_config.load_config", return_value=None)

    config = {}

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=["", "n"],  # base dir, no windows menu
    )
    mocker.patch("fetchtastic.setup_config.os.makedirs")

    result = setup_config._setup_base(config, False, False, lambda _: True)
    captured = capsys.readouterr()

    assert "Failed to load existing configuration" in captured.out
    assert result["BASE_DIR"] == setup_config.BASE_DIR


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_base_non_first_run_default_dir(mocker):
    """Test _setup_base non-first run with default directory (line 1974)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))

    config = {}

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=[""],  # accept default
    )
    mocker.patch("fetchtastic.setup_config.os.makedirs")

    result = setup_config._setup_base(config, False, False, lambda _: True)

    assert "BASE_DIR" in result


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_base_windows_no_modules(mocker, capsys):
    """Test _setup_base on Windows without modules available (lines 2021-2029)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", False)
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, None))

    config = {}

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=[""],  # base dir
    )
    mocker.patch("fetchtastic.setup_config.os.makedirs")

    setup_config._setup_base(config, False, True, lambda _: True)
    captured = capsys.readouterr()

    assert "Windows shortcuts not available" in captured.out
    assert "pip install fetchtastic[windows]" in captured.out


# Tests for run_setup desktop configuration (lines 2119-2139, 2141->2145, 2146->2162)


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=True)  # Termux for 3 default
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_app.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False)
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.setup_config.install_termux_packages")
@patch("fetchtastic.setup_config.check_storage_setup")
@patch(
    "fetchtastic.setup_config.get_fetchtastic_installation_method",
    return_value="pipx",
)
@patch("fetchtastic.cli.main")
@patch("shutil.which")
def test_run_setup_desktop_invalid_version_input(
    mock_shutil_which,
    mock_downloader_main,
    mock_get_install_method,
    mock_check_storage_setup,
    mock_install_termux_packages,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_check_cron_job_exists,
    mock_menu_app,
    mock_menu_firmware,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
    tmp_path,
):
    """Test run_setup with client app choice and invalid version input."""
    # Save original values and patch CONFIG_DIR/CONFIG_FILE
    original_config_dir = setup_config.CONFIG_DIR
    original_config_file = setup_config.CONFIG_FILE
    setup_config.CONFIG_DIR = str(tmp_path / "test_config")
    setup_config.CONFIG_FILE = str(tmp_path / "test_config" / "fetchtastic.yml")
    try:
        user_inputs = [
            "",  # Use default base directory
            "d",  # Desktop/client app choice
            "n",  # No prereleases
            "invalid",  # Invalid version input
            "y",  # Wi-Fi only
            "n",  # No cron
            "n",  # No boot script
            "n",  # No NTFY
            "n",  # No GitHub token
            "n",  # Don't perform first run
        ]
        mock_input.side_effect = user_inputs

        mock_menu_app.return_value = {"selected_assets": ["meshtastic.dmg"]}
        mock_menu_firmware.return_value = {"selected_assets": ["meshtastic.dmg"]}

        with patch("builtins.open", mock_open()):
            with patch("sys.stdin.isatty", return_value=False):
                setup_config.run_setup()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]

        # Invalid input should fall back to the app retention default.
        assert saved_config["APP_VERSIONS_TO_KEEP"] == DEFAULT_APP_VERSIONS_TO_KEEP
    finally:
        # Restore original values
        setup_config.CONFIG_DIR = original_config_dir
        setup_config.CONFIG_FILE = original_config_file


# Tests for partial reconfiguration sections (lines 2169-2175, 2182-2183)


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_app.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False)
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
def test_run_setup_version_package_not_found(
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_check_cron_job_exists,
    mock_menu_firmware,
    mock_menu_app,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
    mocker,
    tmp_path,
):
    """Test run_setup when version() raises PackageNotFoundError."""
    from importlib.metadata import PackageNotFoundError

    # Save original values and patch CONFIG_DIR/CONFIG_FILE
    original_config_dir = setup_config.CONFIG_DIR
    original_config_file = setup_config.CONFIG_FILE
    setup_config.CONFIG_DIR = str(tmp_path / "test_config")
    setup_config.CONFIG_FILE = str(tmp_path / "test_config" / "fetchtastic.yml")
    try:
        user_inputs = [
            "",  # Use default base directory
            "b",  # Both client apps and firmware
            "n",  # No firmware prereleases
            "n",  # No client app prereleases
            "n",  # No channel suffixes
            "2",  # Keep 2 versions
            "2",  # Keep 2 versions firmware
            "n",  # No auto-extract
            "n",  # No cron
            "n",  # No reboot
            "n",  # No NTFY
            "n",  # No GitHub token
            "n",  # Don't perform first run
        ]
        mock_input.side_effect = user_inputs

        mock_menu_app.return_value = {"selected_assets": ["meshtastic.apk"]}
        mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

        mocker.patch(
            "fetchtastic.setup_config.version",
            side_effect=PackageNotFoundError("fetchtastic"),
        )

        with patch("builtins.open", mock_open()):
            with patch("sys.stdin.isatty", return_value=False):
                setup_config.run_setup()

        mock_yaml_dump.assert_called()
        saved_config = mock_yaml_dump.call_args[0][0]

        # LAST_SETUP_VERSION should not be set when package not found
        assert "LAST_SETUP_VERSION" not in saved_config
        assert "LAST_SETUP_DATE" in saved_config
    finally:
        # Restore original values
        setup_config.CONFIG_DIR = original_config_dir
        setup_config.CONFIG_FILE = original_config_file


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_app.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False)
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
def test_run_setup_version_other_error(
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_check_cron_job_exists,
    mock_menu_firmware,
    mock_menu_app,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
    mocker,
    tmp_path,
):
    """Test run_setup when version() raises other exception."""
    # Save original values and patch CONFIG_DIR/CONFIG_FILE
    original_config_dir = setup_config.CONFIG_DIR
    original_config_file = setup_config.CONFIG_FILE
    setup_config.CONFIG_DIR = str(tmp_path / "test_config")
    setup_config.CONFIG_FILE = str(tmp_path / "test_config" / "fetchtastic.yml")
    try:
        user_inputs = [
            "",  # Use default base directory
            "b",  # Both client apps and firmware
            "n",  # No firmware prereleases
            "n",  # No client app prereleases
            "n",  # No channel suffixes
            "2",  # Keep 2 versions
            "2",  # Keep 2 versions firmware
            "n",  # No auto-extract
            "n",  # No cron
            "n",  # No reboot
            "n",  # No NTFY
            "n",  # No GitHub token
            "n",  # Don't perform first run
        ]
        mock_input.side_effect = user_inputs

        mock_menu_app.return_value = {"selected_assets": ["meshtastic.apk"]}
        mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

        mocker.patch(
            "fetchtastic.setup_config.version",
            side_effect=RuntimeError("Unexpected error"),
        )
        mock_logger = mocker.patch("fetchtastic.setup_config.logger")

        with patch("builtins.open", mock_open()):
            with patch("sys.stdin.isatty", return_value=False):
                setup_config.run_setup()

        mock_yaml_dump.assert_called()
        mock_logger.debug.assert_called()
    finally:
        # Restore original values
        setup_config.CONFIG_DIR = original_config_dir
        setup_config.CONFIG_FILE = original_config_file


# Tests for config directory creation error


@pytest.mark.configuration
@pytest.mark.unit
@patch("builtins.input")
@patch("fetchtastic.setup_config.platform.system", return_value="Linux")
@patch("fetchtastic.setup_config.is_termux", return_value=False)
@patch("fetchtastic.setup_config.config_exists", return_value=(False, None))
@patch("fetchtastic.setup_config.os.path.exists", return_value=False)
@patch("fetchtastic.setup_config.os.makedirs")
@patch("fetchtastic.setup_config.yaml.safe_dump")
@patch("fetchtastic.setup_config.menu_app.run_menu")
@patch("fetchtastic.setup_config.menu_firmware.run_menu")
@patch("fetchtastic.setup_config.check_cron_job_exists", return_value=False)
@patch("fetchtastic.setup_config.check_any_cron_jobs_exist", return_value=False)
@patch("fetchtastic.setup_config.setup_cron_job")
@patch("fetchtastic.setup_config.setup_reboot_cron_job")
@patch("fetchtastic.cli.main")
@patch("shutil.which")
def test_run_setup_config_dir_creation_error(
    mock_shutil_which,
    mock_downloader_main,
    mock_setup_reboot_cron_job,
    mock_setup_cron_job,
    mock_check_any_cron_jobs_exist,
    mock_check_cron_job_exists,
    mock_menu_firmware,
    mock_menu_app,
    mock_yaml_dump,
    mock_makedirs,
    mock_os_path_exists,
    mock_config_exists,
    mock_is_termux,
    mock_platform_system,
    mock_input,
    mocker,
    capsys,
    tmp_path,
):
    """Test run_setup when config directory creation fails."""
    # Save original values and patch CONFIG_DIR/CONFIG_FILE
    original_config_dir = setup_config.CONFIG_DIR
    original_config_file = setup_config.CONFIG_FILE
    setup_config.CONFIG_DIR = str(tmp_path / "test_config")
    setup_config.CONFIG_FILE = str(tmp_path / "test_config" / "fetchtastic.yml")
    try:
        user_inputs = [
            "",  # Use default base directory
            "b",  # Both client apps and firmware
            "n",  # No firmware prereleases
            "n",  # No client app prereleases
            "n",  # No channel suffixes
            "2",  # Keep 2 versions
            "2",  # Keep 2 versions firmware
            "n",  # No auto-extract
            "n",  # No cron
            "n",  # No reboot
            "n",  # No NTFY
            "n",  # No GitHub token
            "n",  # Don't perform first run
        ]
        mock_input.side_effect = user_inputs

        mock_menu_app.return_value = {"selected_assets": ["meshtastic.apk"]}
        mock_menu_firmware.return_value = {"selected_assets": ["meshtastic-firmware"]}

        # Make makedirs raise an error when creating config dir
        def side_effect(path, exist_ok=False):
            if "config" in str(path):
                raise OSError("Permission denied")
            return None

        mock_makedirs.side_effect = side_effect

        with patch("builtins.open", mock_open()):
            with patch("sys.stdin.isatty", return_value=False):
                setup_config.run_setup()
                captured = capsys.readouterr()

        assert "Error creating config directory" in captured.out
    finally:
        # Restore original values
        setup_config.CONFIG_DIR = original_config_dir
        setup_config.CONFIG_FILE = original_config_file


# Tests for _setup_github uncovered lines (1755->1761, 1809-1810)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_github_keep_existing_token(mocker, capsys):
    """Test _setup_github keeping existing token (lines 1755-1761)."""
    from fetchtastic.setup_config import _setup_github

    existing_token = "token_" + "existing_placeholder"
    config = {"GITHUB_TOKEN": existing_token}

    mocker.patch(
        "builtins.input",
        side_effect=["n"],  # don't change token
    )

    result = _setup_github(config)
    captured = capsys.readouterr()

    assert result["GITHUB_TOKEN"] == existing_token
    assert "Keeping existing GitHub token configuration" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_github_empty_token_input(mocker, capsys):
    """Test _setup_github with empty token input (lines 1809-1810)."""
    from fetchtastic.setup_config import _setup_github

    config = {}

    mocker.patch(
        "builtins.input",
        side_effect=["y"],  # yes to setup
    )
    mocker.patch("getpass.getpass", return_value="")  # empty token

    result = _setup_github(config)
    captured = capsys.readouterr()

    assert "No token entered" in captured.out
    assert "GITHUB_TOKEN" not in result


# Tests for load_config directory handling (lines 3407->3411, 3429)


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_non_standard_location(mocker, capsys, tmp_path):
    """Test load_config from non-standard location (lines 3407-3411)."""
    from fetchtastic.setup_config import load_config

    tmp_dir = str(tmp_path / "custom_config")
    expected_path = os.path.join(tmp_dir, "fetchtastic.yaml")

    mocker.patch("os.path.exists", side_effect=lambda path: path == expected_path)
    mock_load_yaml = mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping",
        return_value={"BASE_DIR": "/custom/dir"},
    )

    result = load_config(tmp_dir)

    assert result is not None
    assert result["BASE_DIR"] == "/custom/dir"
    mock_load_yaml.assert_called_once_with(expected_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_directory_yaml_error(mocker, tmp_path):
    """Test load_config directory mode with YAML error (line 3429)."""
    from fetchtastic.setup_config import load_config

    tmp_dir = str(tmp_path / "custom_config")
    expected_path = os.path.join(tmp_dir, "fetchtastic.yaml")

    mocker.patch("os.path.exists", side_effect=lambda path: path == expected_path)
    mock_load_yaml = mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping", return_value=None
    )

    result = load_config(tmp_dir)

    assert result is None
    mock_load_yaml.assert_called_once_with(expected_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_migrates_legacy_desktop_asset_key(mocker, tmp_path):
    """load_config should migrate SELECTED_DESKTOP_PLATFORMS to SELECTED_DESKTOP_ASSETS."""
    from fetchtastic.setup_config import load_config

    tmp_dir = str(tmp_path / "custom_config")
    expected_path = os.path.join(tmp_dir, "fetchtastic.yaml")
    mocker.patch("os.path.exists", side_effect=lambda path: path == expected_path)
    mock_load_yaml = mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping",
        return_value={"SELECTED_DESKTOP_PLATFORMS": ["meshtastic.dmg"]},
    )

    result = load_config(tmp_dir)

    assert result is not None
    assert result["SELECTED_DESKTOP_ASSETS"] == ["meshtastic.dmg"]
    assert "SELECTED_DESKTOP_PLATFORMS" not in result
    mock_load_yaml.assert_called_once_with(expected_path)


@pytest.mark.configuration
@pytest.mark.unit
def test_load_config_new_desktop_asset_key_stays_authoritative(mocker, tmp_path):
    """load_config should keep new key value even when legacy key is present."""
    from fetchtastic.setup_config import load_config

    tmp_dir = str(tmp_path / "custom_config")
    expected_path = os.path.join(tmp_dir, "fetchtastic.yaml")
    mocker.patch("os.path.exists", side_effect=lambda path: path == expected_path)
    mock_load_yaml = mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping",
        return_value={
            "SELECTED_DESKTOP_ASSETS": [],
            "SELECTED_DESKTOP_PLATFORMS": ["legacy-value"],
        },
    )

    result = load_config(tmp_dir)

    assert result is not None
    assert result["SELECTED_DESKTOP_ASSETS"] == []
    assert "SELECTED_DESKTOP_PLATFORMS" not in result
    mock_load_yaml.assert_called_once_with(expected_path)


# Tests for _configure_cron_job (lines 1334-1340)


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_cron_job_none_frequency(mocker, capsys):
    """Test _configure_cron_job with 'none' frequency (lines 1334-1340)."""
    from fetchtastic.setup_config import _configure_cron_job

    mocker.patch(
        "builtins.input",
        side_effect=["n"],  # none
    )

    _configure_cron_job(install_crond_needed=False)
    captured = capsys.readouterr()

    assert "Cron job has not been set up" in captured.out


# Tests for install_crond exception handling (lines 2969-2970)


@pytest.mark.configuration
@pytest.mark.unit
def test_install_crond_exception(mocker, capsys):
    """Test install_crond with exception (lines 2969-2970)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("shutil.which", return_value=None)  # crond not installed
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "pkg", stderr=b"Install failed"),
    )

    setup_config.install_crond()
    captured = capsys.readouterr()

    assert "An error occurred while installing or enabling crond" in captured.out


# Tests for setup_cron_job uncovered lines (3011, 3028, 3033-3034)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_cron_job_termux_path(mocker):
    """Test setup_cron_job Termux path (line 3028)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Linux")
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.return_value = MagicMock(returncode=0, stdout="")

    mock_popen = mocker.patch("subprocess.Popen")
    mock_communicate = mock_popen.return_value.communicate

    setup_config.setup_cron_job("hourly")

    # Check that the cron line contains 'fetchtastic' without full path (Termux style)
    mock_communicate.assert_called()
    call_args = mock_communicate.call_args
    assert call_args is not None
    assert "input" in call_args.kwargs
    cron_content = call_args.kwargs["input"]
    assert "fetchtastic download  # fetchtastic" in cron_content


@pytest.mark.configuration
@pytest.mark.unit
def test_remove_cron_job_windows(mocker, capsys):
    """Test remove_cron_job on Windows (lines 3077-3078)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    setup_config.remove_cron_job()
    captured = capsys.readouterr()

    assert "Cron jobs are not supported on Windows" in captured.out


# Tests for remove_reboot_cron_job (lines 3248-3249)


@pytest.mark.configuration
@pytest.mark.unit
def test_remove_reboot_cron_job_windows(mocker, capsys):
    """Test remove_reboot_cron_job on Windows (lines 3248-3249)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    setup_config.remove_reboot_cron_job()
    captured = capsys.readouterr()

    assert "Cron jobs are not supported on Windows" in captured.out


# Tests for setup_reboot_cron_job (lines 3177-3179)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_reboot_cron_job_windows(mocker, capsys):
    """Test setup_reboot_cron_job on Windows (lines 3177-3179)."""
    mocker.patch("fetchtastic.setup_config.platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")

    setup_config.setup_reboot_cron_job()
    captured = capsys.readouterr()

    assert "Cron jobs are not supported on Windows" in captured.out


# Tests for check_cron_job_exists (lines 3361, 3368-3370)


@pytest.mark.configuration
@pytest.mark.unit
def test_check_cron_job_exists_returncode_nonzero(mocker):
    """Test check_cron_job_exists with non-zero return code (line 3361)."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)

    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.return_value = MagicMock(returncode=1, stdout="")

    result = setup_config.check_cron_job_exists()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_check_cron_job_exists_exception(mocker):
    """Test check_cron_job_exists with exception (lines 3368-3370)."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)

    mocker.patch("subprocess.run", side_effect=subprocess.SubprocessError("Error"))
    mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.check_cron_job_exists()

    assert result is False


# Tests for create_windows_menu_shortcuts and related functions


@pytest.mark.configuration
@pytest.mark.unit
def test_create_windows_menu_shortcuts_non_windows(mocker):
    """Test create_windows_menu_shortcuts on non-Windows (line 2464)."""
    mocker.patch("platform.system", return_value="Linux")

    result = setup_config.create_windows_menu_shortcuts("/config", "/downloads")

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_create_config_shortcut_non_windows(mocker):
    """Test create_config_shortcut on non-Windows (line 2732)."""
    mocker.patch("platform.system", return_value="Linux")

    result = setup_config.create_config_shortcut("/config", "/target")

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_create_startup_shortcut_non_windows(mocker):
    """Test create_startup_shortcut on non-Windows (line 2762)."""
    mocker.patch("platform.system", return_value="Linux")

    result = setup_config.create_startup_shortcut()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_create_startup_shortcut_no_fetchtastic(mocker, capsys):
    """Test create_startup_shortcut when fetchtastic not found (lines 2767-2769)."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    mocker.patch("shutil.which", return_value=None)  # fetchtastic not found

    result = setup_config.create_startup_shortcut()
    captured = capsys.readouterr()

    assert result is False
    assert "fetchtastic executable not found in PATH" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_create_startup_shortcut_exception(mocker):
    """Test create_startup_shortcut with exception (lines 2812-2814)."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mock_winshell.startup.side_effect = Exception("Startup folder error")
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    mocker.patch("shutil.which", return_value="C:\\fetchtastic.exe")

    result = setup_config.create_startup_shortcut()

    assert result is False


# Tests for copy_to_clipboard_func (lines 2829-2905)


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_none_text(mocker):
    """Test copy_to_clipboard_func with None text (line 2829-2830)."""
    result = setup_config.copy_to_clipboard_func(None)

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_termux_error(mocker):
    """Test copy_to_clipboard_func Termux error (lines 2839-2841)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("Clipboard error"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_darwin_error(mocker):
    """Test copy_to_clipboard_func macOS error (lines 2874-2875)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("pbcopy error"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_linux_no_tools(mocker, capsys):
    """Test copy_to_clipboard_func Linux with no clipboard tools (lines 2894-2897)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value=None)  # neither xclip nor xsel
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.warning.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_linux_error(mocker):
    """Test copy_to_clipboard_func Linux error (lines 2903-2905)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value="/usr/bin/xclip")
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("xclip error"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_unsupported_platform(mocker, capsys):
    """Test copy_to_clipboard_func unsupported platform (lines 2899-2902)."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)
    mocker.patch("platform.system", return_value="FreeBSD")
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.warning.assert_called_once()


# Tests for setup_storage (lines 2945-2947)


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_storage_exception(mocker, capsys):
    """Test setup_storage with exception (lines 2945-2947)."""
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "termux-setup-storage"),
    )

    setup_config.setup_storage()
    captured = capsys.readouterr()

    assert "An error occurred while setting up Termux storage" in captured.out
    assert "Please grant storage permissions when prompted" in captured.out


# Tests for migrate_config (lines 2397-2401, 2417-2418, 2423-2425)


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_config_dir_creation_error(mocker, capsys):
    """Test migrate_config when config dir creation fails (lines 2397-2401)."""
    mocker.patch(
        "os.path.exists", side_effect=lambda path: path == setup_config.OLD_CONFIG_FILE
    )
    mocker.patch(
        "os.makedirs",
        side_effect=OSError("Permission denied"),
    )
    mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping",
        return_value={"test": "value"},
    )
    mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.migrate_config()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_check_any_cron_jobs_exists_exception(mocker):
    """Test check_any_cron_jobs_exist with exception (lines 3323-3325)."""
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch("shutil.which", return_value="/usr/bin/crontab")
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("crontab error"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.check_any_cron_jobs_exist()

    assert result is False
    mock_logger.error.assert_called_once()


# Tests for create_windows_menu_shortcuts detailed error paths (2474-2480, 2483->2516)


@pytest.mark.configuration
@pytest.mark.unit
def test_create_windows_menu_shortcuts_parent_dir_error(mocker, capsys):
    """Test create_windows_menu_shortcuts when parent dir creation fails (lines 2474-2480)."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: False,
    )
    mocker.patch(
        "os.makedirs",
        side_effect=OSError("Permission denied"),
    )

    result = setup_config.create_windows_menu_shortcuts("/config", "/downloads")

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_create_windows_menu_shortcuts_folder_creation_error(mocker, capsys):
    """Test create_windows_menu_shortcuts when Start Menu folder creation fails (lines 2519-2521)."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: False,
    )
    mocker.patch("os.makedirs")
    mocker.patch("shutil.which", return_value="C:\\fetchtastic.exe")

    # Make the Start Menu folder creation fail on second call
    makedirs_calls = []

    def makedirs_side_effect(path, exist_ok=False):
        makedirs_calls.append(path)
        if setup_config.WINDOWS_START_MENU_FOLDER in str(path):
            raise OSError("Cannot create folder")
        return None

    mocker.patch("os.makedirs", side_effect=makedirs_side_effect)

    result = setup_config.create_windows_menu_shortcuts("/config", "/downloads")

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_create_windows_menu_shortcuts_no_fetchtastic(mocker, capsys):
    """Test create_windows_menu_shortcuts when fetchtastic not found (lines 2526-2527)."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)
    mock_winshell = mocker.MagicMock()
    mocker.patch.object(setup_config, "winshell", mock_winshell, create=True)

    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.makedirs")
    mocker.patch("shutil.which", return_value=None)  # fetchtastic not found

    result = setup_config.create_windows_menu_shortcuts("/config", "/downloads")
    captured = capsys.readouterr()

    assert result is False
    assert "fetchtastic executable not found in PATH" in captured.out


# Tests for prompt_for_migration (lines 2437-2441)


@pytest.mark.configuration
@pytest.mark.unit
def test_load_yaml_mapping_os_error(mocker):
    """Test _load_yaml_mapping with OSError (line 287)."""
    mocker.patch(
        "builtins.open",
        side_effect=OSError("Permission denied"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config._load_yaml_mapping("/nonexistent/config.yaml")

    assert result is None
    mock_logger.exception.assert_called_once()


# Test for is_fetchtastic_installed_via_pip/pipx exception handling


@pytest.mark.configuration
@pytest.mark.unit
def test_is_fetchtastic_installed_via_pip_os_error(mocker):
    """Test is_fetchtastic_installed_via_pip with OSError."""
    mocker.patch(
        "subprocess.run",
        side_effect=OSError("No such file"),
    )

    result = setup_config.is_fetchtastic_installed_via_pip()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_is_fetchtastic_installed_via_pipx_os_error(mocker):
    """Test is_fetchtastic_installed_via_pipx with OSError."""
    mocker.patch(
        "subprocess.run",
        side_effect=OSError("No such file"),
    )

    result = setup_config.is_fetchtastic_installed_via_pipx()

    assert result is False


# Test for _safe_current_version (lines 2245-2246)


@pytest.mark.configuration
@pytest.mark.unit
def test_safe_current_version_package_not_found(mocker):
    """Test _safe_current_version with PackageNotFoundError (lines 2245-2246)."""
    from importlib.metadata import PackageNotFoundError

    mocker.patch(
        "fetchtastic.setup_config.version",
        side_effect=PackageNotFoundError("fetchtastic"),
    )

    result = setup_config._safe_current_version()

    assert result == "unknown"


# Test for get_version_info (lines 2372-2376)


@pytest.mark.configuration
@pytest.mark.unit
def test_get_version_info(mocker):
    """Test get_version_info (lines 2372-2376)."""
    mocker.patch(
        "fetchtastic.setup_config.check_for_updates",
        return_value=("1.0.0", "1.1.0", True),
    )

    current, latest, available = setup_config.get_version_info()

    assert current == "1.0.0"
    assert latest == "1.1.0"
    assert available is True
