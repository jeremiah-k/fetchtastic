"""Identify the active tool environment and build its upgrade command."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def is_tool_installation(manager: str) -> bool:
    """Match this interpreter to the manager's Fetchtastic environment.

    Listing installed tools can find a different installation from the one
    running this process. Compare environment paths before choosing a manager.
    """
    prefix = Path(sys.prefix)
    if prefix.name.casefold() != "fetchtastic":
        return False
    executable = shutil.which(manager)
    if not executable:
        return False
    commands = {
        "uv": ["tool", "dir"],
        "pipx": ["environment", "--value", "PIPX_LOCAL_VENVS"],
    }
    try:
        result = subprocess.run(
            [executable, *commands[manager]],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        expected = Path(result.stdout.strip()) / "fetchtastic"
        return os.path.normcase(str(prefix.resolve())) == os.path.normcase(
            str(expected.resolve())
        )
    except (OSError, subprocess.SubprocessError):
        return False


def upgrade_command(method: str, *, windows: bool = False) -> str:
    """Upgrade through the active manager, preserving direct pip installations."""
    package = "fetchtastic[win]" if windows else "fetchtastic"
    if method in {"pip", "unknown"}:
        python = Path(sys.executable)
        if windows and python.name.casefold() == "pythonw.exe":
            python = python.with_name("python.exe")
        args = [str(python), "-m", "pip", "install", "--upgrade", package]
    else:
        manager = "pipx" if method == "pipx" else "uv"
        executable = (shutil.which(manager) or manager) if windows else manager
        if method == "uv":
            args = [executable, "tool", "upgrade", "fetchtastic"]
        else:
            args = [executable, "upgrade", "fetchtastic"]
    return subprocess.list2cmdline(args) if windows else shlex.join(args)
