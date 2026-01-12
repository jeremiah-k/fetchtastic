import os
import subprocess
from unittest.mock import mock_open

import pytest
import yaml

# Import package module (matches real usage)
import fetchtastic.setup_config as setup_config


@pytest.fixture
def reload_setup_config_module():
    """Fixture to reload setup_config module after a test to restore original state."""
    yield
    # Restore module to its original state after test
    import importlib

    importlib.reload(setup_config)


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_success(mocker, tmp_path):
    """Test successful migration from pip to pipx in Termux."""
    mock_config_file = tmp_path / "config.yaml"
    mock_config_content = {"BASE_DIR": "/tmp/test", "SAVE_APKS": True}

    # Mock environment and functions
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(mock_config_file))
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )

    # Mock subprocess calls
    mock_subprocess = mocker.MagicMock()
    mock_subprocess.return_value.returncode = 0
    mock_subprocess.return_value.stdout = ""
    mocker.patch("subprocess.run", mock_subprocess)

    # Mock shutil.which for pipx
    mocker.patch(
        "shutil.which",
        side_effect=lambda cmd: "/usr/bin/pipx" if cmd == "pipx" else None,
    )

    # Mock file operations
    mocker.patch(
        "builtins.open", mock_open(read_data=yaml.safe_dump(mock_config_content))
    )
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.makedirs")

    # Mock input to accept migration
    mocker.patch("builtins.input", side_effect=["y", "y"])

    result = setup_config.migrate_pip_to_pipx()

    assert result is True
    # Verify pip uninstall was called
    pip_uninstall_calls = [
        call
        for call in mock_subprocess.call_args_list
        if "uninstall" in str(call) and "pip" in str(call)
    ]
    assert len(pip_uninstall_calls) == 1
    # Verify pipx install was called
    pipx_install_calls = [
        call
        for call in mock_subprocess.call_args_list
        if "pipx" in str(call) and "install" in str(call)
    ]
    assert len(pipx_install_calls) == 1  # pipx install fetchtastic


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_non_termux(mocker):
    """Test migration is skipped outside Termux environment."""
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=False)

    result = setup_config.migrate_pip_to_pipx()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_not_installed_via_pip(mocker):
    """Test migration is skipped when not installed via pip."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="unknown",
    )

    result = setup_config.migrate_pip_to_pipx()

    assert result is True


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_user_declines(mocker, tmp_path):
    """Test migration when user declines."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("builtins.input", return_value="n")

    result = setup_config.migrate_pip_to_pipx()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_pipx_install_failure(mocker, tmp_path):
    """Test migration failure when pipx install fails."""
    mock_config_file = tmp_path / "config.yaml"

    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(mock_config_file))
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("shutil.which", return_value="/usr/bin/pipx")

    # Mock subprocess to fail pipx install
    def mock_subprocess_side_effect(cmd, **kwargs):
        if "pipx" in str(cmd) and "install" in str(cmd):
            mock = mocker.MagicMock()
            mock.returncode = 1
            mock.stderr = "Installation failed"
            return mock
        return mocker.MagicMock(returncode=0, stdout="")

    mocker.patch("subprocess.run", side_effect=mock_subprocess_side_effect)
    mocker.patch("builtins.open", mock_open(read_data="test: config"))
    mocker.patch("os.path.exists", return_value=False)  # No existing config
    mocker.patch("builtins.input", return_value="y")

    result = setup_config.migrate_pip_to_pipx()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_pip_to_pipx_backup_failure(mocker, tmp_path):
    """Test migration failure when config backup fails."""
    mock_config_file = tmp_path / "config.yaml"

    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(mock_config_file))
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("shutil.which", return_value="/usr/bin/pipx")
    mocker.patch("builtins.input", return_value="y")
    mocker.patch(
        "subprocess.run", return_value=mocker.MagicMock(returncode=0, stdout="")
    )
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: str(path) == str(mock_config_file),
    )

    # Mock file operations to fail on backup read
    def mock_open_failure(filename, mode="r", *_args, **_kwargs):
        """
        A replacement for builtins.open that simulates a permission error when attempting to read the test config file.

        Parameters:
            filename: Path or name of the file being opened.
            mode (str): File mode (e.g., "r", "w", "rb"); defaults to "r".

        Returns:
            A file-like object produced by unittest.mock.mock_open() for the given filename and mode when no simulated error occurs.

        Raises:
            PermissionError: If the mode includes "r" and the filename matches the test config file, a permission error is raised to simulate a read failure.
        """
        if "r" in mode and str(mock_config_file) in str(filename):
            raise PermissionError("Permission denied")
        return mock_open()(filename, mode, *_args, **_kwargs)

    mocker.patch("builtins.open", side_effect=mock_open_failure)

    result = setup_config.migrate_pip_to_pipx()

    # Migration should be aborted when backup fails
    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_apk_only(mocker, capsys):
    """Test _setup_downloads with APK-only selection."""
    config = {}

    # Mock input to select APK only
    mocker.patch(
        "builtins.input",
        side_effect=[
            "a",  # Choose APK only
            "y",  # Check APK prereleases
            "n",  # Add channel suffixes
        ],
    )

    # Mock menu to return some APK assets
    mock_menu_result = {"selected_assets": ["meshtastic"]}
    mocker.patch("fetchtastic.menu_apk.run_menu", return_value=mock_menu_result)

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is True
    assert save_firmware is False
    assert result_config["SAVE_APKS"] is True
    assert result_config["SAVE_FIRMWARE"] is False
    assert result_config["CHECK_APK_PRERELEASES"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_firmware_only(mocker, capsys):
    """Test _setup_downloads with firmware-only selection."""
    config = {}

    # Mock input to select firmware only
    mocker.patch(
        "builtins.input",
        side_effect=[
            "f",  # Choose firmware only
            "n",  # Check firmware prereleases
            "n",  # Add channel suffixes
        ],
    )

    # Mock menu to return some firmware assets
    mock_menu_result = {"selected_assets": ["rak4631-"]}
    mocker.patch("fetchtastic.menu_firmware.run_menu", return_value=mock_menu_result)

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is False
    assert save_firmware is True
    assert result_config["SAVE_APKS"] is False
    assert result_config["SAVE_FIRMWARE"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_both_selected(mocker, capsys):
    """Test _setup_downloads with both APK and firmware selected."""
    config = {}

    # Mock input to select both
    mocker.patch(
        "builtins.input",
        side_effect=[
            "b",  # Choose both
            "n",  # Check firmware prereleases
            "y",  # Check APK prereleases
            "n",  # Add channel suffixes
        ],
    )

    # Mock menus
    mock_apk_result = {"selected_assets": ["meshtastic"]}
    mock_firmware_result = {"selected_assets": ["rak4631-"]}
    mocker.patch("fetchtastic.menu_apk.run_menu", return_value=mock_apk_result)
    mocker.patch(
        "fetchtastic.menu_firmware.run_menu", return_value=mock_firmware_result
    )

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is True
    assert save_firmware is True
    assert result_config["SAVE_APKS"] is True
    assert result_config["SAVE_FIRMWARE"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_no_selection(mocker, capsys):
    """Test _setup_downloads when user selects nothing."""
    config = {}

    # Mock input to select APK but then menu returns None
    mocker.patch("builtins.input", side_effect=["a"])  # Choose APK only
    mocker.patch("fetchtastic.menu_apk.run_menu", return_value=None)

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is False
    assert save_firmware is False
    assert result_config["SAVE_APKS"] is False
    assert result_config["SAVE_FIRMWARE"] is False

    captured = capsys.readouterr()
    assert "No APK assets selected" in captured.out


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_firmware_empty_selection(mocker):
    """Empty firmware selections should disable firmware downloads."""
    config = {}

    mocker.patch(
        "builtins.input",
        side_effect=[
            "f",  # Choose firmware only
            "n",  # Check firmware prereleases
            "n",  # Add channel suffixes
        ],
    )
    mocker.patch(
        "fetchtastic.menu_firmware.run_menu",
        return_value={"selected_assets": []},
    )

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is False
    assert save_firmware is False
    assert result_config["SAVE_FIRMWARE"] is False
    assert result_config["CHECK_PRERELEASES"] is False
    assert result_config["SELECTED_FIRMWARE_ASSETS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_apk_empty_selection(mocker):
    """Empty APK selections should disable APK downloads."""
    config = {}

    mocker.patch(
        "builtins.input",
        side_effect=[
            "a",  # Choose APK only
            "y",  # Check APK prereleases
            "n",  # Add channel suffixes
        ],
    )
    mocker.patch(
        "fetchtastic.menu_apk.run_menu",
        return_value={"selected_assets": []},
    )

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=False, wants=lambda _: True
    )

    assert save_apks is False
    assert save_firmware is False
    assert result_config["SAVE_APKS"] is False
    assert result_config["CHECK_APK_PRERELEASES"] is False
    assert result_config["SELECTED_APK_ASSETS"] == []


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_run(mocker):
    """Test _setup_downloads in partial run mode."""
    config = {
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
        "SELECTED_APK_ASSETS": ["existing"],
        "CHECK_APK_PRERELEASES": False,
    }

    # Mock input for partial run - only update APK settings
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Download Android APKs
            "n",  # Don't rerun menu (keep existing selection)
            "y",  # Enable prereleases
            "n",  # Add channel suffixes
        ],
    )

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=True, wants=lambda section: section == "android"
    )

    assert save_apks is True
    assert save_firmware is False  # Unchanged in partial run
    assert result_config["SAVE_APKS"] is True
    assert result_config["SAVE_FIRMWARE"] is False
    assert result_config["CHECK_APK_PRERELEASES"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_run_apk_keep_existing_skips_menu(mocker):
    """Partial Android run should skip the menu when user keeps existing selection."""
    config = {
        "SAVE_APKS": True,
        "SAVE_FIRMWARE": False,
        "SELECTED_APK_ASSETS": ["existing"],
        "CHECK_APK_PRERELEASES": False,
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Download Android APKs
            "n",  # Don't rerun menu
            "n",  # Disable prereleases
            "n",  # Add channel suffixes
        ],
    )

    mock_menu = mocker.patch("fetchtastic.menu_apk.run_menu")

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=True, wants=lambda section: section == "android"
    )

    assert save_apks is True
    assert save_firmware is False
    assert result_config["SAVE_APKS"] is True
    assert result_config["CHECK_APK_PRERELEASES"] is False
    mock_menu.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_run_firmware_forces_menu_without_selection(mocker):
    """Partial firmware run should force menu when no existing selection exists."""
    config = {"SAVE_APKS": False, "SAVE_FIRMWARE": True}

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Download firmware releases
        ],
    )

    mock_menu = mocker.patch("fetchtastic.menu_firmware.run_menu", return_value=None)

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=True, wants=lambda section: section == "firmware"
    )

    assert save_apks is False
    assert save_firmware is False
    assert result_config["SAVE_FIRMWARE"] is False
    assert result_config["SELECTED_FIRMWARE_ASSETS"] == []
    assert result_config["CHECK_PRERELEASES"] is False
    mock_menu.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_run_firmware_keep_existing_skips_menu(mocker):
    """Partial firmware run should skip menu when user keeps existing selection."""
    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["existing-firmware"],
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Download firmware releases
            "n",  # Don't rerun menu
            "n",  # Disable firmware prereleases
            "n",  # Add channel suffixes
        ],
    )

    mock_menu = mocker.patch("fetchtastic.menu_firmware.run_menu")

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=True, wants=lambda section: section == "firmware"
    )

    assert save_apks is False
    assert save_firmware is True
    assert result_config["SAVE_FIRMWARE"] is True
    assert result_config["SELECTED_FIRMWARE_ASSETS"] == ["existing-firmware"]
    mock_menu.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_downloads_partial_run_firmware_channel_suffix_config(mocker):
    """Partial firmware run should persist channel suffix selection."""
    config = {
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["existing-firmware"],
        "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
    }

    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Download firmware releases
            "n",  # Don't rerun menu
            "n",  # Disable firmware prereleases
            "n",  # Disable channel suffixes
        ],
    )

    result_config, save_apks, save_firmware = setup_config._setup_downloads(
        config, is_partial_run=True, wants=lambda section: section == "firmware"
    )

    assert save_apks is False
    assert save_firmware is True
    assert result_config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] is False


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_use_defaults(mocker):
    """Test configure_exclude_patterns accepting recommended defaults."""
    config = {}

    # Mock input to accept defaults
    mocker.patch("builtins.input", side_effect=["y", "n"])

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == setup_config.RECOMMENDED_EXCLUDE_PATTERNS


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_custom_patterns(mocker):
    """Test configure_exclude_patterns with custom patterns."""
    config = {}
    custom_patterns = ["*.debug", "*test*", "*.tmp"]

    # Mock interactive environment
    mocker.patch("os.environ.get", return_value=None)  # Ensure CI is not set
    mocker.patch("sys.stdin.isatty", return_value=True)

    # Mock input to use custom patterns
    mocker.patch("builtins.input", side_effect=["n", " ".join(custom_patterns)])

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == custom_patterns


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_add_to_defaults(mocker):
    """Test configure_exclude_patterns adding to defaults."""
    config = {}
    additional = ["*.custom", "*.test"]

    # Mock interactive environment
    mocker.patch("os.environ.get", return_value=None)  # Ensure CI is not set
    mocker.patch("sys.stdin.isatty", return_value=True)

    # Mock input to use defaults and add more
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Use defaults
            "y",  # Add more
            " ".join(additional),  # Additional patterns
        ],
    )

    patterns = setup_config.configure_exclude_patterns(config)

    expected = setup_config.RECOMMENDED_EXCLUDE_PATTERNS + additional
    assert patterns == expected


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_no_patterns(mocker):
    """Test configure_exclude_patterns with no patterns."""
    config = {}

    # Mock interactive environment
    mocker.patch("os.environ.get", return_value=None)  # Ensure CI is not set
    mocker.patch("sys.stdin.isatty", return_value=True)

    # Mock input to use no patterns
    mocker.patch("builtins.input", side_effect=["n", ""])

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == []


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_deduplicates(mocker):
    """Test configure_exclude_patterns de-duplicates patterns."""
    config = {}

    # Mock interactive environment
    mocker.patch("os.environ.get", return_value=None)  # Ensure CI is not set
    mocker.patch("sys.stdin.isatty", return_value=True)

    # Mock input to use defaults and provide duplicates
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Use defaults
            "y",  # Add more
            "*.hex *.custom *.hex",  # Additional patterns (with duplicates)
        ],
    )

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == [*setup_config.RECOMMENDED_EXCLUDE_PATTERNS, "*.custom"]


@pytest.mark.configuration
@pytest.mark.unit
def test_configure_exclude_patterns_non_interactive(mocker):
    """Test configure_exclude_patterns in non-interactive mode."""
    config = {}

    # Mock CI environment
    mocker.patch.dict(os.environ, {"CI": "true"})
    mocker.patch("sys.stdin.isatty", return_value=False)

    patterns = setup_config.configure_exclude_patterns(config)

    # Should use recommended defaults in non-interactive mode
    assert patterns == setup_config.RECOMMENDED_EXCLUDE_PATTERNS


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_notifications_enable_custom(mocker):
    """Test _setup_notifications enabling with custom server/topic."""
    config = {}

    # Mock input to enable notifications
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Enable notifications
            "custom.ntfy.server",  # Custom server
            "my-custom-topic",  # Custom topic
            "n",  # Don't copy to clipboard
            "y",  # Notify on download only
        ],
    )

    result_config = setup_config._setup_notifications(config)

    assert result_config["NTFY_SERVER"] == "https://custom.ntfy.server"
    assert result_config["NTFY_TOPIC"] == "my-custom-topic"
    assert result_config["NOTIFY_ON_DOWNLOAD_ONLY"] is True


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_notifications_disable_existing(mocker):
    """Test _setup_notifications disabling existing notifications."""
    config = {
        "NTFY_SERVER": "https://ntfy.sh",
        "NTFY_TOPIC": "existing-topic",
        "NOTIFY_ON_DOWNLOAD_ONLY": False,
    }

    # Mock input to disable notifications
    mocker.patch(
        "builtins.input",
        side_effect=[
            "n",  # Disable notifications
            "y",  # Confirm disable
        ],
    )

    result_config = setup_config._setup_notifications(config)

    assert result_config["NTFY_SERVER"] == ""
    assert result_config["NTFY_TOPIC"] == ""
    assert result_config["NOTIFY_ON_DOWNLOAD_ONLY"] is False


@pytest.mark.configuration
@pytest.mark.unit
def test_setup_notifications_clipboard_termux(mocker):
    """Test _setup_notifications clipboard on Termux."""
    config = {}

    # Mock Termux environment and clipboard
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.copy_to_clipboard_func", return_value=True)

    # Mock input to enable notifications and copy topic
    mocker.patch(
        "builtins.input",
        side_effect=[
            "y",  # Enable notifications
            "ntfy.sh",  # Default server
            "test-topic",  # Custom topic
            "y",  # Copy to clipboard
            "n",  # Don't notify on download only
        ],
    )

    setup_config._setup_notifications(config)

    # Should have attempted to copy topic (not URL on Termux)
    setup_config.copy_to_clipboard_func.assert_called_once_with("test-topic")


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_termux_success(mocker):
    """Test copy_to_clipboard_func success on Termux."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)

    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is True
    mock_subprocess.assert_called_once_with(
        ["termux-clipboard-set"], input="test text".encode("utf-8"), check=True
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_termux_failure(mocker):
    """Test copy_to_clipboard_func failure on Termux."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)

    # Mock subprocess to raise exception
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "termux-clipboard-set"),
    )
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.error.assert_called_once_with(
        "Error copying to Termux clipboard: %s", mocker.ANY
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_windows_success(mocker):
    """Test copy_to_clipboard_func success on Windows."""
    mocker.patch.dict(os.environ, {}, clear=True)  # Remove Termux env
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("fetchtastic.setup_config.WINDOWS_MODULES_AVAILABLE", True)

    # Mock win32clipboard
    mock_win32 = mocker.MagicMock()
    mocker.patch.dict("sys.modules", {"win32clipboard": mock_win32})

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is True
    mock_win32.OpenClipboard.assert_called_once()
    mock_win32.EmptyClipboard.assert_called_once()
    mock_win32.SetClipboardText.assert_called_once_with("test text")
    mock_win32.CloseClipboard.assert_called_once()


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_macos_success(mocker):
    """Test copy_to_clipboard_func success on macOS."""
    mocker.patch.dict(os.environ, {}, clear=True)  # Remove Termux env
    mocker.patch("platform.system", return_value="Darwin")

    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is True
    mock_subprocess.assert_called_once_with(
        "pbcopy", text=True, input="test text", check=True
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_linux_xclip_success(mocker):
    """Test copy_to_clipboard_func success on Linux with xclip."""
    mocker.patch.dict(os.environ, {}, clear=True)  # Remove Termux env
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value="/usr/bin/xclip")

    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is True
    mock_subprocess.assert_called_once_with(
        ["xclip", "-selection", "clipboard"],
        input="test text".encode("utf-8"),
        check=True,
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_linux_xsel_success(mocker):
    """Test copy_to_clipboard_func success on Linux with xsel."""
    mocker.patch.dict(os.environ, {}, clear=True)  # Remove Termux env
    mocker.patch("platform.system", return_value="Linux")
    # Mock shutil.which to return None for xclip, /usr/bin/xsel for xsel
    mocker.patch(
        "shutil.which",
        side_effect=lambda cmd: "/usr/bin/xsel" if cmd == "xsel" else None,
    )

    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is True
    mock_subprocess.assert_called_once_with(
        ["xsel", "--clipboard", "--input"],
        input="test text".encode("utf-8"),
        check=True,
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_copy_to_clipboard_linux_no_tools(mocker):
    """Test copy_to_clipboard_func when no clipboard tools available."""
    mocker.patch.dict(os.environ, {}, clear=True)  # Remove Termux env
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value=None)  # No clipboard tools
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")

    result = setup_config.copy_to_clipboard_func("test text")

    assert result is False
    mock_logger.warning.assert_called_once_with(
        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
    )


@pytest.mark.configuration
@pytest.mark.unit
def test_check_storage_setup_already_setup(mocker):
    """Test check_storage_setup when storage is already configured."""
    mocker.patch.dict(
        os.environ,
        {"PREFIX": "/data/data/com.termux/files/usr", "CI": ""},
        clear=True,
    )
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.sys.stdin.isatty", return_value=True)
    mocker.patch(
        "os.path.exists",
        side_effect=lambda path: {
            os.path.expanduser("~/storage"): True,
            os.path.expanduser("~/storage/downloads"): True,
        }.get(path, False),
    )
    mocker.patch("os.access", return_value=True)
    mocker.patch("builtins.print")

    result = setup_config.check_storage_setup()

    assert result is True


@pytest.mark.configuration
@pytest.mark.unit
def test_check_storage_setup_permission_denied_retry(mocker):
    """Test check_storage_setup retry loop after permission denied."""
    call_count = 0

    def mock_exists_access(path):
        """
        Simulates a file-existence/access check that fails twice and then succeeds.

        Useful for tests that need an existence check to return `False` on the first two invocations and `True` thereafter.

        Returns:
            bool: `True` on the third and subsequent calls, `False` for the first two calls.
        """
        nonlocal call_count
        call_count += 1
        if call_count <= 2:  # First two calls fail
            return False
        return True  # Third call succeeds

    mocker.patch.dict(
        os.environ,
        {"PREFIX": "/data/data/com.termux/files/usr", "CI": ""},
        clear=True,
    )
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config.sys.stdin.isatty", return_value=True)
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("os.access", side_effect=lambda path, mode: mock_exists_access(path))
    mocker.patch("fetchtastic.setup_config.setup_storage")
    mocker.patch("builtins.input", return_value="")  # Press Enter
    mocker.patch("builtins.print")

    result = setup_config.check_storage_setup()

    assert result is True
    # Should have called setup_storage twice (for the failures)
    assert setup_config.setup_storage.call_count == 2


@pytest.mark.configuration
@pytest.mark.unit
def test_install_termux_packages_all_missing(mocker):
    """Test install_termux_packages when all packages are missing."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("shutil.which", return_value=None)  # No tools found
    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)
    mocker.patch("builtins.print")

    setup_config.install_termux_packages()

    # Should install all three packages
    install_call = mock_subprocess.call_args_list[0]
    assert "pkg" in install_call[0][0]
    assert "install" in install_call[0][0]
    assert "termux-api" in install_call[0][0]
    assert "termux-services" in install_call[0][0]
    assert "cronie" in install_call[0][0]
    assert "-y" in install_call[0][0]


@pytest.mark.configuration
@pytest.mark.unit
def test_install_termux_packages_already_installed(mocker):
    """Test install_termux_packages when packages are already installed."""
    mocker.patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr"})
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch(
        "shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd
    )  # All tools found
    mock_subprocess = mocker.MagicMock()
    mocker.patch("subprocess.run", mock_subprocess)
    mocker.patch("builtins.print")

    setup_config.install_termux_packages()

    # Should not install anything
    mock_subprocess.assert_not_called()


@pytest.mark.configuration
@pytest.mark.unit
def test_should_recommend_setup_no_config(mocker):
    """Test should_recommend_setup when no config exists."""
    mocker.patch("fetchtastic.setup_config.load_config", return_value={})

    should_recommend, reason, last_version, current_version = (
        setup_config.should_recommend_setup()
    )

    assert should_recommend is True
    assert reason == "No configuration found"
    assert last_version is None
    assert current_version is None


@pytest.mark.configuration
@pytest.mark.unit
def test_should_recommend_setup_version_mismatch(mocker):
    """Test should_recommend_setup when version changed."""
    mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value={"LAST_SETUP_VERSION": "0.8.0"},
    )
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")

    should_recommend, reason, last_version, current_version = (
        setup_config.should_recommend_setup()
    )

    assert should_recommend is True
    assert "Version changed from 0.8.0 to 0.8.1" in reason
    assert last_version == "0.8.0"
    assert current_version == "0.8.1"


@pytest.mark.configuration
@pytest.mark.unit
def test_should_recommend_setup_current(mocker):
    """Test should_recommend_setup when setup is current."""
    mocker.patch(
        "fetchtastic.setup_config.load_config",
        return_value={"LAST_SETUP_VERSION": "0.8.1"},
    )
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")

    should_recommend, reason, last_version, current_version = (
        setup_config.should_recommend_setup()
    )

    assert should_recommend is False
    assert reason == "Setup is current"
    assert last_version == "0.8.1"
    assert current_version == "0.8.1"


@pytest.mark.configuration
@pytest.mark.unit
def test_get_version_info_success(mocker):
    """Test get_version_info successful version check."""
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")

    # Mock requests response
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "0.9.0"}}
    mocker.patch("requests.get", return_value=mock_response)
    # Don't mock packaging.version.parse to avoid breaking version comparison

    current, latest, available = setup_config.get_version_info()

    assert current == "0.8.1"
    assert latest == "0.9.0"
    assert available is True


@pytest.mark.configuration
@pytest.mark.unit
def test_get_version_info_request_failure(mocker):
    """Test get_version_info when request fails."""
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")
    mocker.patch("requests.get", side_effect=Exception("Network error"))

    current, latest, available = setup_config.get_version_info()

    assert current == "0.8.1"
    assert latest is None
    assert available is False


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_config_success(mocker, tmp_path):
    """Test successful config migration."""
    old_config = tmp_path / "old_config.yaml"
    new_config = tmp_path / "new_config.yaml"
    test_config_data = {"BASE_DIR": "/test", "SAVE_APKS": True}

    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", str(old_config))
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(new_config))
    mocker.patch("fetchtastic.setup_config.CONFIG_DIR", str(tmp_path))

    # Mock file operations
    mocker.patch("os.path.exists", side_effect=lambda path: path == str(old_config))
    mocker.patch("builtins.open", mock_open(read_data=yaml.safe_dump(test_config_data)))
    mocker.patch("os.makedirs")
    mocker.patch("os.remove")
    mocker.patch("fetchtastic.log_utils.logger")

    result = setup_config.migrate_config()

    assert result is True


@pytest.mark.configuration
@pytest.mark.unit
def test_migrate_config_no_old_config(mocker):
    """Test migrate_config when no old config exists."""
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/nonexistent/config.yaml")
    mocker.patch("os.path.exists", return_value=False)

    result = setup_config.migrate_config()

    assert result is False


@pytest.mark.configuration
@pytest.mark.unit
def test_prompt_for_migration(mocker):
    """Test prompt_for_migration function."""
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old/config.yaml")
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/new/config.yaml")
    mocker.patch("fetchtastic.log_utils.logger")

    result = setup_config.prompt_for_migration()

    assert result is True
