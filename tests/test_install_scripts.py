"""Run shell installer paths against local command stubs without network access."""

import os
import shutil
import subprocess

import pytest

from fetchtastic.tools import get_install_script_path

pytestmark = [pytest.mark.integration, pytest.mark.configuration]


@pytest.fixture
def shell_installer(tmp_path):
    bash = shutil.which("bash")
    if bash is None or os.name == "nt":
        pytest.skip("POSIX bash installer")
    commands = tmp_path / "commands"
    commands.mkdir()
    tool_bin = tmp_path / "tool bin"
    tool_bin.mkdir()
    log = tmp_path / "calls"

    def write_executable(path, body):
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)

    write_executable(
        tool_bin / "fetchtastic",
        'printf "fetchtastic:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\n',
    )
    uv = commands / "uv"
    write_executable(
        uv,
        'printf "uv:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\nif [ "$1 $2" = "tool dir" ]; then printf "%s\\n" "$INSTALL_TEST_BIN"; fi\nif [ "$1 $2" = "tool list" ] && [ "${INSTALL_TEST_UV_OWNS:-0}" = 1 ]; then printf "fetchtastic v0.0.0\\n"; fi\nif [ "${INSTALL_TEST_FAIL:-0}" = 1 ] && [ "$1 $2" = "tool install" ]; then exit 7; fi\n',
    )
    write_executable(
        commands / "pkg", 'printf "pkg:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\n'
    )
    write_executable(
        commands / "pipx",
        'printf "pipx:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\nif [ "$1" = environment ]; then printf "%s\\n" "$INSTALL_TEST_BIN"; fi\nif [ "$1" = list ] && [ "${INSTALL_TEST_PIPX_OWNS:-0}" = 1 ]; then printf "fetchtastic 0.0.0\\n"; fi\n',
    )
    fake_pip = commands / "fake-pip"
    write_executable(fake_pip, 'printf "pip:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\n')
    write_executable(
        commands / "python3",
        'printf "python:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\nif [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; cp "$INSTALL_TEST_PIP" "$3/bin/python"; cp "$INSTALL_TEST_BIN/fetchtastic" "$3/bin/fetchtastic"; fi\n',
    )
    environment = {
        **os.environ,
        "PATH": f"{commands}:/usr/bin:/bin",
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_BIN_HOME": str(tmp_path / "bin"),
        "INSTALL_TEST_LOG": str(log),
        "INSTALL_TEST_BIN": str(tool_bin),
        "INSTALL_TEST_PIP": str(fake_pip),
        "TERMUX_VERSION": "",
        "PREFIX": "",
    }

    def run(*args, overrides=None):
        result = subprocess.run(
            [bash, get_install_script_path("linux"), *args],
            env={**environment, **(overrides or {})},
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return run, tmp_path, uv


def test_shell_defaults_to_uv(shell_installer):
    run, _, _ = shell_installer
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert "uv:tool install --python >=3.10 fetchtastic" in calls
    assert "uv:tool update-shell" in calls
    assert calls[-1] == "fetchtastic:version"


def test_shell_uses_native_termux_python(shell_installer):
    run, root, _ = shell_installer
    prefix = str(root / "termux")
    result, calls = run(overrides={"TERMUX_VERSION": "1", "PREFIX": prefix})
    assert result.returncode == 0, result.stderr
    assert f"uv:tool install --python {prefix}/bin/python fetchtastic" in calls


def test_shell_uv_failure_stops_installation(shell_installer):
    run, _, _ = shell_installer
    result, calls = run(overrides={"INSTALL_TEST_FAIL": "1"})
    assert result.returncode == 7
    assert calls[-1] == "uv:tool install --python >=3.10 fetchtastic"
    assert not any(call.startswith("uv:tool update-shell") for call in calls)


def test_shell_pip_uses_isolated_environment(shell_installer):
    run, root, _ = shell_installer
    result, calls = run("pip")
    assert result.returncode == 0, result.stderr
    assert "pip:-m pip install --upgrade fetchtastic" in calls
    assert (
        root / "bin/fetchtastic"
    ).resolve() == root / "data/fetchtastic/venv/bin/fetchtastic"


def test_shell_pip_preserves_foreign_command(shell_installer):
    run, root, _ = shell_installer
    command = root / "bin/fetchtastic"
    command.parent.mkdir()
    command.write_text("foreign")
    result, calls = run("pip")
    assert result.returncode == 1
    assert command.read_text() == "foreign"
    assert not any(call.startswith("pip:") for call in calls)


def test_shell_keeps_pipx_support(shell_installer):
    run, _, _ = shell_installer
    result, calls = run("pipx")
    assert result.returncode == 0, result.stderr
    assert "pipx:install --python python3 fetchtastic" in calls
    assert "pipx:upgrade fetchtastic" not in calls


def test_shell_pipx_rerun_upgrades_without_reinstalling(shell_installer):
    run, _, _ = shell_installer
    result, calls = run("pipx", overrides={"INSTALL_TEST_PIPX_OWNS": "1"})
    assert result.returncode == 0, result.stderr
    assert "pipx:upgrade fetchtastic" in calls
    assert "pipx:install --python python3 fetchtastic" not in calls


def test_shell_default_preserves_existing_pipx(shell_installer):
    run, root, _ = shell_installer
    path = f"{root / 'tool bin'}:{root / 'commands'}:/usr/bin:/bin"
    result, calls = run(overrides={"PATH": path, "INSTALL_TEST_PIPX_OWNS": "1"})
    assert result.returncode == 0, result.stderr
    assert "pipx:upgrade fetchtastic" in calls
    assert not any(call.startswith("uv:tool install") for call in calls)


def test_shell_default_preserves_existing_uv(shell_installer):
    run, root, _ = shell_installer
    path = f"{root / 'tool bin'}:{root / 'commands'}:/usr/bin:/bin"
    result, calls = run(overrides={"PATH": path, "INSTALL_TEST_UV_OWNS": "1"})
    assert result.returncode == 0, result.stderr
    assert "uv:tool upgrade fetchtastic" in calls
    assert not any(call.startswith("uv:tool install") for call in calls)
    assert not any(call.startswith("pipx:upgrade") for call in calls)


def test_shell_default_does_not_follow_stale_uv_registration(shell_installer):
    run, root, _ = shell_installer
    command = root / "commands" / "fetchtastic"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    result, calls = run(overrides={"INSTALL_TEST_UV_OWNS": "1"})
    assert result.returncode == 1
    assert "left untouched" in result.stderr
    assert "uv:tool upgrade fetchtastic" not in calls


def test_shell_default_does_not_follow_stale_pipx_registration(shell_installer):
    run, root, _ = shell_installer
    command = root / "commands" / "fetchtastic"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    result, calls = run(overrides={"INSTALL_TEST_PIPX_OWNS": "1"})
    assert result.returncode == 1
    assert "left untouched" in result.stderr
    assert "pipx:upgrade fetchtastic" not in calls


def test_shell_default_preserves_legacy_pip_interpreter(shell_installer):
    run, root, _ = shell_installer
    command = root / "commands" / "fetchtastic"
    command.write_text(f"#!{root / 'commands' / 'python3'}\n" "raise SystemExit(0)\n")
    command.chmod(0o755)
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert "python:-m pip install --upgrade fetchtastic" in calls
    assert not any(call.startswith("uv:tool install") for call in calls)


def test_shell_default_refuses_unknown_existing_installation(shell_installer):
    run, root, _ = shell_installer
    command = root / "commands" / "fetchtastic"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    result, calls = run()
    assert result.returncode == 1
    assert "left untouched" in result.stderr
    assert not any(call.startswith("uv:tool install") for call in calls)


def test_shell_default_refuses_foreign_command_in_managed_bin(shell_installer):
    run, root, _ = shell_installer
    command = root / "bin" / "fetchtastic"
    command.parent.mkdir()
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    path = f"{root / 'bin'}:{root / 'commands'}:/usr/bin:/bin"
    result, calls = run(overrides={"PATH": path})
    assert result.returncode == 1
    assert "left untouched" in result.stderr
    assert not any(call.startswith("uv:tool install") for call in calls)


def test_shell_rejects_unknown_installer(shell_installer):
    run, _, _ = shell_installer
    result, calls = run("unknown")
    assert result.returncode == 2
    assert calls == []


def test_shell_bootstraps_uv_into_configured_directory(shell_installer):
    run, root, uv = shell_installer
    saved = uv.with_name("uv-saved")
    uv.rename(saved)
    install_dir = root / "installed uv"
    curl = uv.with_name("curl")
    curl.write_text(
        '#!/bin/sh\nprintf "curl:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\nprintf \'mkdir -p "$UV_INSTALL_DIR"\\ncp "$INSTALL_TEST_UV" "$UV_INSTALL_DIR/uv"\\n\'\n'
    )
    curl.chmod(0o755)
    result, calls = run(
        overrides={"UV_INSTALL_DIR": str(install_dir), "INSTALL_TEST_UV": str(saved)}
    )
    assert result.returncode == 0, result.stderr
    assert calls[0] == "curl:-LsSf https://astral.sh/uv/install.sh"
    assert (install_dir / "uv").is_file()
    assert calls[-1] == "fetchtastic:version"


def test_shell_bootstraps_native_termux_packages(shell_installer):
    run, root, uv = shell_installer
    saved = uv.with_name("uv-saved")
    uv.rename(saved)
    pkg = uv.with_name("pkg")
    pkg.write_text(
        '#!/bin/sh\nprintf "pkg:%s\\n" "$*" >> "$INSTALL_TEST_LOG"\ncp "$INSTALL_TEST_UV" "$INSTALL_TEST_UV_TARGET"\n'
    )
    pkg.chmod(0o755)
    result, calls = run(
        overrides={
            "TERMUX_VERSION": "1",
            "PREFIX": str(root / "termux"),
            "INSTALL_TEST_UV": str(saved),
            "INSTALL_TEST_UV_TARGET": str(uv),
        }
    )
    assert result.returncode == 0, result.stderr
    assert calls[0] == "pkg:install -y python uv"
    assert not any(call.startswith("curl:") for call in calls)
