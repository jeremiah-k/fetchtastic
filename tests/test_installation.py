"""Select upgrades for the environment that is running Fetchtastic."""

import shlex
import subprocess
from unittest.mock import Mock

import pytest

from fetchtastic import installation, setup_config

pytestmark = [pytest.mark.unit, pytest.mark.configuration]


@pytest.mark.parametrize(
    "manager,arguments",
    [("uv", ["tool", "dir"]), ("pipx", ["environment", "--value", "PIPX_LOCAL_VENVS"])],
)
def test_manager_matches_active_environment(mocker, tmp_path, manager, arguments):
    tool_root = tmp_path / "tools with spaces"
    mocker.patch.object(installation.sys, "prefix", str(tool_root / "fetchtastic"))
    mocker.patch.object(installation.shutil, "which", return_value=f"/bin/{manager}")
    run = mocker.patch.object(
        installation.subprocess,
        "run",
        return_value=Mock(returncode=0, stdout=str(tool_root) + "\n"),
    )
    assert installation.is_tool_installation(manager)
    run.assert_called_once_with(
        [f"/bin/{manager}", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_manager_does_not_claim_another_installation(mocker, tmp_path):
    mocker.patch.object(
        installation.sys, "prefix", str(tmp_path / "pip-env" / "fetchtastic")
    )
    mocker.patch.object(installation.shutil, "which", return_value="/bin/uv")
    mocker.patch.object(
        installation.subprocess,
        "run",
        return_value=Mock(returncode=0, stdout=str(tmp_path / "uv-env")),
    )
    assert not installation.is_tool_installation("uv")


@pytest.mark.parametrize(
    "result",
    [
        Mock(returncode=1, stdout="root"),
        Mock(returncode=0, stdout=""),
        subprocess.TimeoutExpired("uv", 10),
        OSError("unavailable"),
    ],
)
def test_manager_query_failures(mocker, result):
    mocker.patch.object(installation.sys, "prefix", "/tools/fetchtastic")
    mocker.patch.object(installation.shutil, "which", return_value="/bin/uv")
    mocker.patch.object(
        installation.subprocess,
        "run",
        **(
            {"side_effect": result}
            if isinstance(result, Exception)
            else {"return_value": result}
        ),
    )
    assert not installation.is_tool_installation("uv")


def test_missing_manager(mocker):
    mocker.patch.object(installation.sys, "prefix", "/tools/fetchtastic")
    mocker.patch.object(installation.shutil, "which", return_value=None)
    assert not installation.is_tool_installation("uv")


def test_virtual_environment_does_not_probe_managers(mocker):
    mocker.patch.object(installation.sys, "prefix", "/checkout/.venv")
    run = mocker.patch.object(installation.subprocess, "run")
    assert not installation.is_tool_installation("uv")
    assert not installation.is_tool_installation("pipx")
    run.assert_not_called()


@pytest.mark.parametrize(
    "uv,pipx,pip,expected",
    [
        (True, True, True, "uv"),
        (False, True, True, "pipx"),
        (False, False, True, "pip"),
        (False, False, False, "unknown"),
    ],
)
def test_manager_precedence(mocker, uv, pipx, pip, expected):
    mocker.patch.object(
        setup_config, "is_fetchtastic_installed_via_uv", return_value=uv
    )
    mocker.patch.object(
        setup_config, "is_fetchtastic_installed_via_pipx", return_value=pipx
    )
    mocker.patch.object(
        setup_config, "is_fetchtastic_installed_via_pip", return_value=pip
    )
    assert setup_config.get_fetchtastic_installation_method() == expected


@pytest.mark.parametrize(
    "method,expected",
    [
        ("uv", ["uv", "tool", "upgrade", "fetchtastic"]),
        ("pipx", ["pipx", "upgrade", "fetchtastic"]),
        ("unknown", ["uv", "tool", "install", "--upgrade", "fetchtastic"]),
    ],
)
def test_posix_tool_upgrade(method, expected):
    assert shlex.split(installation.upgrade_command(method)) == expected


def test_pip_upgrade_uses_active_interpreter(mocker):
    mocker.patch.object(installation.sys, "executable", "/venv with spaces/bin/python")
    assert shlex.split(installation.upgrade_command("pip")) == [
        "/venv with spaces/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "fetchtastic",
    ]


def test_windows_pip_upgrade_uses_console_interpreter_and_extra(mocker):
    mocker.patch.object(
        installation.sys, "executable", "C:/venv with spaces/Scripts/pythonw.exe"
    )
    assert (
        installation.upgrade_command("pip", windows=True)
        == '"C:/venv with spaces/Scripts/python.exe" -m pip install --upgrade fetchtastic[win]'
    )


def test_windows_uv_upgrade_resolves_executable(mocker):
    mocker.patch.object(
        installation.shutil, "which", return_value="C:/tools with spaces/uv.exe"
    )
    assert (
        installation.upgrade_command("uv", windows=True)
        == '"C:/tools with spaces/uv.exe" tool upgrade fetchtastic'
    )


def test_pip_detection_matches_package_name(mocker):
    mocker.patch.object(
        setup_config.subprocess,
        "run",
        return_value=Mock(returncode=0, stdout="fetchtastic-helper 1.0\n"),
    )
    assert not setup_config.is_fetchtastic_installed_via_pip()


def test_automation_finds_configured_uv_tool_bin(mocker, tmp_path):
    executable = tmp_path / "uv bin" / "fetchtastic"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    mocker.patch.object(setup_config.shutil, "which", return_value=None)
    mocker.patch.dict(
        setup_config.os.environ, {"UV_TOOL_BIN_DIR": str(executable.parent)}
    )
    assert setup_config._resolve_fetchtastic_executable() == str(executable)


def test_windows_update_shortcut_uses_active_manager(mocker, tmp_path):
    mocker.patch.object(setup_config.platform, "system", return_value="Windows")
    mocker.patch.object(setup_config, "WINDOWS_MODULES_AVAILABLE", True)
    mocker.patch.object(setup_config, "winshell", mocker.Mock(), create=True)
    mocker.patch.object(setup_config, "CONFIG_DIR", str(tmp_path / "config"))
    mocker.patch.object(
        setup_config, "WINDOWS_START_MENU_FOLDER", str(tmp_path / "menu/Fetchtastic")
    )
    mocker.patch.object(setup_config, "BASE_DIR", str(tmp_path / "downloads"))
    mocker.patch.object(
        setup_config.platformdirs, "user_log_dir", return_value=str(tmp_path / "logs")
    )
    mocker.patch.object(
        setup_config, "get_fetchtastic_installation_method", return_value="uv"
    )
    mocker.patch.object(
        setup_config.shutil,
        "which",
        side_effect=lambda name: {
            "fetchtastic": "C:/bin/fetchtastic.exe",
            "uv": "C:/tools with spaces/uv.exe",
        }.get(name),
    )
    assert setup_config.create_windows_menu_shortcuts(
        str(tmp_path / "config.yaml"), str(tmp_path / "downloads")
    )
    batch = (tmp_path / "config/batch/fetchtastic_update.bat").read_text()
    assert '"C:/tools with spaces/uv.exe" tool upgrade fetchtastic' in batch
    assert "uninstall" not in batch
