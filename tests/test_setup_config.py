import pytest
import yaml
import importlib
from unittest.mock import patch

from fetchtastic import setup_config

@pytest.mark.parametrize("is_termux_val, platform_system, expected", [
    (True, 'Linux', 'termux'),
    (False, 'Darwin', 'mac'),
    (False, 'Linux', 'linux'),
    (False, 'Windows', 'unknown'),
])
def test_get_platform(mocker, is_termux_val, platform_system, expected):
    """Test the platform detection logic."""
    mocker.patch('fetchtastic.setup_config.is_termux', return_value=is_termux_val)
    mocker.patch('platform.system', return_value=platform_system)
    assert setup_config.get_platform() == expected


def test_load_config_no_file(tmp_path, mocker):
    """Test that load_config returns None when no config file exists."""
    # Patch the config file paths to point to our temp directory
    mocker.patch('fetchtastic.setup_config.CONFIG_FILE', str(tmp_path / 'new_config.yaml'))
    mocker.patch('fetchtastic.setup_config.OLD_CONFIG_FILE', str(tmp_path / 'old_config.yaml'))

    assert setup_config.load_config() is None


def test_load_config_new_location(tmp_path, mocker):
    """Test loading config from the new (platformdirs) location."""
    new_config_path = tmp_path / 'new_config.yaml'
    old_config_path = tmp_path / 'old_config.yaml'
    mocker.patch('fetchtastic.setup_config.CONFIG_FILE', str(new_config_path))
    mocker.patch('fetchtastic.setup_config.OLD_CONFIG_FILE', str(old_config_path))

    config_data = {'SAVE_APKS': True}
    with open(new_config_path, 'w') as f:
        yaml.dump(config_data, f)

    config = setup_config.load_config()
    assert config['SAVE_APKS'] is True


def test_load_config_old_location(tmp_path, mocker):
    """Test loading config from the old location when new one doesn't exist."""
    new_config_path = tmp_path / 'new_config.yaml'
    old_config_path = tmp_path / 'old_config.yaml'
    mocker.patch('fetchtastic.setup_config.CONFIG_FILE', str(new_config_path))
    mocker.patch('fetchtastic.setup_config.OLD_CONFIG_FILE', str(old_config_path))

    config_data = {'SAVE_FIRMWARE': True}
    with open(old_config_path, 'w') as f:
        yaml.dump(config_data, f)

    config = setup_config.load_config()
    assert config['SAVE_FIRMWARE'] is True


def test_load_config_prefers_new_location(tmp_path, mocker):
    """Test that the new config location is preferred when both exist."""
    new_config_path = tmp_path / 'new_config.yaml'
    old_config_path = tmp_path / 'old_config.yaml'
    mocker.patch('fetchtastic.setup_config.CONFIG_FILE', str(new_config_path))
    mocker.patch('fetchtastic.setup_config.OLD_CONFIG_FILE', str(old_config_path))

    new_config_data = {'key': 'new'}
    old_config_data = {'key': 'old'}
    with open(new_config_path, 'w') as f:
        yaml.dump(new_config_data, f)
    with open(old_config_path, 'w') as f:
        yaml.dump(old_config_data, f)

    config = setup_config.load_config()
    assert config['key'] == 'new'


def test_migrate_config(tmp_path, mocker):
    """Test the configuration migration logic."""
    new_config_path = tmp_path / 'new_config.yaml'
    old_config_path = tmp_path / 'old_config.yaml'
    mocker.patch('fetchtastic.setup_config.CONFIG_FILE', str(new_config_path))
    mocker.patch('fetchtastic.setup_config.OLD_CONFIG_FILE', str(old_config_path))
    mocker.patch('fetchtastic.setup_config.CONFIG_DIR', str(tmp_path))

    # Create an old config file
    old_config_data = {'key': 'to_be_migrated'}
    with open(old_config_path, 'w') as f:
        yaml.dump(old_config_data, f)

    # Run migration
    assert setup_config.migrate_config() is True

    # Check that new config exists and old one is gone
    assert new_config_path.exists()
    assert not old_config_path.exists()

    # Check content of new config
    with open(new_config_path, 'r') as f:
        new_config_data = yaml.safe_load(f)
    assert new_config_data['key'] == 'to_be_migrated'


@pytest.mark.parametrize("is_termux_val, install_method, expected", [
    (True, 'pip', 'pip install --upgrade fetchtastic'),
    (True, 'pipx', 'pipx upgrade fetchtastic'),
    (False, 'pipx', 'pipx upgrade fetchtastic'),
])
def test_get_upgrade_command(mocker, is_termux_val, install_method, expected):
    """Test the upgrade command generation logic."""
    mocker.patch('fetchtastic.setup_config.is_termux', return_value=is_termux_val)
    mocker.patch('fetchtastic.setup_config.get_fetchtastic_installation_method', return_value=install_method)
    assert setup_config.get_upgrade_command() == expected


def test_cron_job_setup(mocker):
    """Test the cron job setup and removal logic."""
    mock_run = mocker.patch('subprocess.run')
    mock_popen = mocker.patch('subprocess.Popen')
    mock_communicate = mock_popen.return_value.communicate
    mocker.patch('shutil.which', return_value='/path/to/fetchtastic')

    # 1. Add a cron job
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    setup_config.setup_cron_job()
    mock_communicate.assert_called_once()
    new_cron_content = mock_communicate.call_args[1]['input']

    # 2. Remove the cron job
    mock_run.return_value = mocker.MagicMock(stdout=new_cron_content, returncode=0)
    setup_config.remove_cron_job()

    # Check that communicate was called a second time
    assert mock_communicate.call_count == 2
    final_cron_content = mock_communicate.call_args[1]['input']
    assert "fetchtastic download" not in final_cron_content


def test_windows_shortcut_creation(mocker):
    """Test the Windows shortcut creation logic."""
    # Mock platform and inject a mock winshell module into sys.modules
    mocker.patch('platform.system', return_value='Windows')
    mock_winshell = mocker.MagicMock()
    mocker.patch.dict('sys.modules', {'winshell': mock_winshell})

    # Reload the setup_config module to make it see the mocked environment
    importlib.reload(setup_config)

    # Now that the module is reloaded, we can patch its internal dependencies
    mocker.patch('shutil.which', return_value='C:\\path\\to\\fetchtastic.exe')
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('os.makedirs')
    mocker.patch('builtins.open', mocker.mock_open())

    # Test creating start menu shortcuts
    setup_config.create_windows_menu_shortcuts('C:\\config.yaml', 'C:\\downloads')

    # Check that the reloaded module (which now has winshell) called CreateShortcut
    assert mock_winshell.CreateShortcut.call_count > 0

    # A simple check to see if one of the expected shortcuts was created
    found_download_shortcut = False
    for call in mock_winshell.CreateShortcut.call_args_list:
        path_arg = call.kwargs.get('Path')
        if path_arg and "Fetchtastic Download.lnk" in path_arg:
            found_download_shortcut = True
            break
    assert found_download_shortcut

    # It's good practice to restore the original module to avoid side effects
    importlib.reload(setup_config)
