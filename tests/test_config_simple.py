import os
import subprocess

import pytest

import fetchtastic.setup_config as setup_config


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
def test_display_version_info_request_failure(mocker):
    """Test display_version_info when request fails."""
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")
    mocker.patch("requests.get", side_effect=Exception("Network error"))

    current, latest, available = setup_config.display_version_info()

    assert current == "0.8.1"
    assert latest is None
    assert available is False


@pytest.mark.configuration
@pytest.mark.unit
def test_display_version_info_logs_update_notification(mocker):
    """Test display_version_info logs notification when update available and show_update_message=True."""
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "0.8.2"}}
    mocker.patch("requests.get", return_value=mock_response)

    current, latest, available = setup_config.display_version_info(
        show_update_message=True
    )

    assert current == "0.8.1"
    assert latest == "0.8.2"
    assert available is True

    mock_logger.info.assert_any_call("\nUpdate Available")
    mock_logger.info.assert_any_call(
        "A newer version (v0.8.2) of Fetchtastic is available!"
    )
    mock_logger.info.assert_any_call(mocker.ANY)


@pytest.mark.configuration
@pytest.mark.unit
def test_display_version_info_no_notification_when_show_message_false(mocker):
    """Test display_version_info does not log notification when show_update_message=False."""
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.1")

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "0.8.2"}}
    mocker.patch("requests.get", return_value=mock_response)

    current, latest, available = setup_config.display_version_info(
        show_update_message=False
    )

    assert current == "0.8.1"
    assert latest == "0.8.2"
    assert available is True

    update_notification_calls = [
        call
        for call in mock_logger.info.call_args_list
        if call and isinstance(call[0][0], str) and "Update Available" in call[0][0]
    ]
    assert len(update_notification_calls) == 0


@pytest.mark.configuration
@pytest.mark.unit
def test_display_version_info_no_notification_when_no_update(mocker):
    """Test display_version_info does not log notification when no update available."""
    mock_logger = mocker.patch("fetchtastic.setup_config.logger")
    mocker.patch("fetchtastic.setup_config.version", return_value="0.8.2")

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"info": {"version": "0.8.2"}}
    mocker.patch("requests.get", return_value=mock_response)

    current, latest, available = setup_config.display_version_info(
        show_update_message=True
    )

    assert current == "0.8.2"
    assert latest == "0.8.2"
    assert available is False

    update_notification_calls = [
        call
        for call in mock_logger.info.call_args_list
        if call and isinstance(call[0][0], str) and "Update Available" in call[0][0]
    ]
    assert len(update_notification_calls) == 0


@pytest.mark.configuration
@pytest.mark.unit
def test_prompt_for_migration(mocker):
    """Test prompt_for_migration function logs appropriate messages."""
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", "/old/config.yaml")
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", "/new/config.yaml")

    # Mock logger to capture calls
    mock_logger = mocker.patch("fetchtastic.log_utils.logger")

    result = setup_config.prompt_for_migration()

    # Verify function returns True
    assert result is True

    # Verify appropriate logging calls were made
    mock_logger.info.assert_any_call(
        "Found configuration file at old location: /old/config.yaml"
    )
    mock_logger.info.assert_any_call(
        "Automatically migrating to the new location: /new/config.yaml"
    )
