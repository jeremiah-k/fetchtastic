import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import fetchtastic.setup_config as setup_config
from fetchtastic.setup_config import (
    _setup_downloads,
    install_crond,
    load_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.configuration]


def test_setup_downloads_menu_runtime_error(mocker, tmp_path):
    config = {
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": [],
    }
    wants = MagicMock()
    wants.side_effect = lambda section: section == "app"

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        return_value="y",
    )
    mocker.patch(
        "fetchtastic.setup_config.menu_app.run_menu",
        side_effect=RuntimeError("menu failed"),
    )
    mocker.patch("fetchtastic.setup_config.logger")

    result_config, result_apps, result_fw = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert result_config["SAVE_CLIENT_APPS"] is False
    assert result_config["SAVE_APKS"] is False
    assert result_config["SAVE_DESKTOP_APP"] is False
    assert result_config["SELECTED_APP_ASSETS"] == []
    assert result_apps is False


def test_setup_downloads_run_menu_returns_no_selected_assets_no_existing(
    mocker, capsys
):
    config = {
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": [],
    }
    wants = MagicMock()
    wants.side_effect = lambda section: section == "app"

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        return_value="y",
    )
    mocker.patch(
        "fetchtastic.setup_config.menu_app.run_menu",
        return_value={},
    )

    result_config, result_apps, result_fw = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    captured = capsys.readouterr()
    assert (
        "No client app asset selection was returned" in captured.out
        or "No client app assets selected" in captured.out
    )
    assert result_config["SAVE_CLIENT_APPS"] is False
    assert result_apps is False


def test_setup_downloads_run_menu_returns_no_selected_assets_with_existing(mocker):
    config = {
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": ["app.apk"],
    }
    wants = MagicMock()
    wants.side_effect = lambda section: section == "app"

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        return_value="y",
    )
    mocker.patch(
        "fetchtastic.setup_config.menu_app.run_menu",
        return_value={},
    )

    result_config, result_apps, result_fw = _setup_downloads(
        config, is_partial_run=True, wants=wants
    )

    assert result_config["SELECTED_APP_ASSETS"] == ["app.apk"]


def test_setup_downloads_rerun_false_no_existing_assets(mocker, capsys):
    call_count = [0]

    def fake_safe_input(prompt, **kwargs):
        call_count[0] += 1
        if "Re-run the client app asset selection menu" in prompt:
            return "n"
        return "y"

    config = {
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": ["app.apk"],
    }
    wants = MagicMock()
    wants.side_effect = lambda section: section == "app"

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=fake_safe_input,
    )

    original_get = dict.get

    class SwitchConfig(dict):
        def get(self, key, default=None):
            val = original_get(self, key, default)
            if key == "SELECTED_APP_ASSETS" and call_count[0] >= 2:
                return []
            return val

    switch_config = SwitchConfig(config)

    result_config, result_apps, result_fw = _setup_downloads(
        switch_config, is_partial_run=True, wants=wants
    )

    captured = capsys.readouterr()
    assert "No existing client app asset selection found" in captured.out
    assert result_config["SAVE_CLIENT_APPS"] is False
    assert result_apps is False


def test_install_crond_installs_cronie(mocker, capsys):
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("shutil.which", return_value=None)
    mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))

    install_crond()

    captured = capsys.readouterr()
    assert "cronie installed." in captured.out


def test_load_config_old_location_returns_none(tmp_path, mocker):
    old_config = str(tmp_path / "old_config.yaml")
    mocker.patch("fetchtastic.setup_config.OLD_CONFIG_FILE", old_config)
    mocker.patch("fetchtastic.setup_config.CONFIG_FILE", str(tmp_path / "new.yaml"))
    mocker.patch(
        "fetchtastic.setup_config.os.path.exists",
        side_effect=lambda p: p == old_config,
    )
    mocker.patch(
        "fetchtastic.setup_config._load_yaml_mapping",
        return_value=None,
    )
    mocker.patch("fetchtastic.setup_config.logger")

    result = load_config()
    assert result is None


def test_migrate_pip_to_pipx_in_setup_base_success(mocker, capsys):
    mocker.patch("fetchtastic.setup_config.is_termux", return_value=True)
    mocker.patch("fetchtastic.setup_config._crontab_available", return_value=True)
    mocker.patch(
        "fetchtastic.setup_config.get_fetchtastic_installation_method",
        return_value="pip",
    )
    mocker.patch("fetchtastic.setup_config.config_exists", return_value=(False, ""))
    mocker.patch("fetchtastic.setup_config.install_termux_packages")
    mocker.patch("fetchtastic.setup_config.check_storage_setup", return_value=True)

    mocker.patch(
        "fetchtastic.setup_config._safe_input",
        side_effect=["y", "/tmp/test"],
    )
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))
    mocker.patch("sys.exit")

    config = {}
    from fetchtastic.setup_config import _setup_base

    _setup_base(
        config,
        is_partial_run=False,
        is_first_run=True,
        wants=lambda s: True,
    )

    captured = capsys.readouterr()
    assert "pipx installed" in captured.out
    assert "Removed pip installation" in captured.out
    assert "Installed with pipx" in captured.out
