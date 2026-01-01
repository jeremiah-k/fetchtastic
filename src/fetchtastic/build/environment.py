"""
Environment detection and setup helpers for build modules.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fetchtastic.env_utils import is_termux
from fetchtastic.log_utils import logger

SHELL_BLOCK_START = "# >>> fetchtastic dfu setup >>>"
SHELL_BLOCK_END = "# <<< fetchtastic dfu setup <<<"

TERMUX_PACKAGE_COMMANDS: Dict[str, Sequence[str]] = {
    "openjdk-17": ("javac", "java"),
    "git": ("git",),
    "curl": ("curl", "wget"),
    "unzip": ("unzip",),
    "zip": ("zip",),
}


@dataclass
class BuildEnvironment:
    java_home: Optional[str]
    sdk_root: Optional[str]
    sdkmanager_path: Optional[str]
    missing_packages: List[str]
    missing_sdk_packages: List[str]

    def is_ready(self) -> bool:
        """
        Return True if the environment has required values and packages.
        """
        return (
            self.java_home is not None
            and self.sdk_root is not None
            and not self.missing_packages
            and not self.missing_sdk_packages
        )


def detect_missing_termux_packages() -> List[str]:
    """
    Return Termux packages whose commands are missing from PATH.
    """
    missing: List[str] = []
    for package, commands in TERMUX_PACKAGE_COMMANDS.items():
        if not any(shutil.which(cmd) for cmd in commands):
            missing.append(package)
    return missing


def detect_java_home() -> Optional[str]:
    """
    Detect a JAVA_HOME path from environment or known locations.
    """
    env_java = os.environ.get("JAVA_HOME")
    if env_java and os.path.isdir(env_java):
        return env_java

    javac_path = shutil.which("javac")
    if javac_path:
        real_javac = os.path.realpath(javac_path)
        candidate = os.path.dirname(os.path.dirname(real_javac))
        if os.path.isdir(candidate):
            return candidate

    prefix = os.environ.get("PREFIX")
    if prefix:
        for candidate in (
            "java-17-openjdk",
            "java-21-openjdk",
            "java-11-openjdk",
        ):
            path = os.path.join(prefix, "lib", "jvm", candidate)
            if os.path.isdir(path):
                return path

    return None


def default_android_sdk_root() -> str:
    """
    Return a default Android SDK root based on platform.
    """
    if is_termux():
        return os.path.expanduser("~/Android/Sdk")
    if os.name == "nt":
        return os.path.expanduser("~/AppData/Local/Android/Sdk")
    if platform.system().lower() == "darwin":
        return os.path.expanduser("~/Library/Android/sdk")
    return os.path.expanduser("~/Android/Sdk")


def find_sdkmanager(sdk_root: Optional[str]) -> Optional[str]:
    """
    Locate sdkmanager in PATH or in the provided SDK root.
    """
    sdkmanager = shutil.which("sdkmanager")
    if sdkmanager:
        return sdkmanager
    if not sdk_root:
        return None
    candidates = [
        os.path.join(sdk_root, "cmdline-tools", "latest", "bin", "sdkmanager"),
        os.path.join(sdk_root, "cmdline-tools", "bin", "sdkmanager"),
        os.path.join(sdk_root, "tools", "bin", "sdkmanager"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def build_shell_exports(
    java_home: Optional[str], sdk_root: Optional[str]
) -> Tuple[Dict[str, str], List[str]]:
    """
    Return environment exports and PATH entries for shell setup.
    """
    exports: Dict[str, str] = {}
    path_entries: List[str] = []
    if java_home:
        exports["JAVA_HOME"] = java_home
        path_entries.append("$JAVA_HOME/bin")
    if sdk_root:
        exports["ANDROID_SDK_ROOT"] = sdk_root
        exports["ANDROID_HOME"] = sdk_root
        path_entries.extend(
            [
                "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin",
                "$ANDROID_SDK_ROOT/platform-tools",
            ]
        )
    return exports, path_entries


def update_process_env(exports: Dict[str, str], path_entries: Iterable[str]) -> None:
    """
    Apply exports and PATH updates to the current process environment.
    """
    for key, value in exports.items():
        os.environ[key] = value
    if path_entries:
        current_path = os.environ.get("PATH", "")
        current_entries = current_path.split(os.pathsep) if current_path else []
        entries: List[str] = []
        for entry in path_entries:
            if not entry:
                continue
            expanded = os.path.expandvars(os.path.expanduser(entry))
            if expanded and expanded not in current_entries:
                entries.append(expanded)
        if entries:
            os.environ["PATH"] = (
                os.pathsep.join(entries + current_entries)
                if current_entries
                else os.pathsep.join(entries)
            )


def collect_shell_rc_files() -> List[str]:
    """
    Return shell rc files to update, including bash and zsh configs.
    """
    home_dir = os.path.expanduser("~")
    shell = os.environ.get("SHELL", "")
    shell_name = os.path.basename(shell)
    candidates = [
        os.path.join(home_dir, ".bashrc"),
        os.path.join(home_dir, ".zshrc"),
    ]
    if shell_name == "bash":
        candidates.append(os.path.join(home_dir, ".bashrc"))
    if shell_name == "zsh":
        candidates.append(os.path.join(home_dir, ".zshrc"))
    for name in (".profile", ".bash_profile", ".zprofile"):
        path = os.path.join(home_dir, name)
        if os.path.exists(path):
            candidates.append(path)

    seen = set()
    result: List[str] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def render_shell_block(exports: Dict[str, str], path_entries: Sequence[str]) -> str:
    """
    Render a shell config block with exports and PATH updates.
    """
    lines = [SHELL_BLOCK_START]
    for key, value in exports.items():
        lines.append(f'export {key}="{value}"')
    if path_entries:
        path_value = ":".join(path_entries + ["$PATH"])
        lines.append(f'export PATH="{path_value}"')
    lines.append(SHELL_BLOCK_END)
    return "\n".join(lines) + "\n"


def update_shell_config(path: str, block: str) -> bool:
    """
    Update a shell rc file with a rendered block. Returns True if updated.
    """
    try:
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()
        pattern = re.compile(
            re.escape(SHELL_BLOCK_START) + r".*?" + re.escape(SHELL_BLOCK_END),
            re.DOTALL,
        )
        if pattern.search(existing):
            updated = pattern.sub(block.strip("\n"), existing)
        else:
            updated = existing.rstrip("\n") + "\n\n" + block
        if updated != existing:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(updated)
            return True
    except OSError as exc:
        logger.warning("Failed to update shell config %s: %s", path, exc)
    return False


def update_shell_configs(
    exports: Dict[str, str], path_entries: Sequence[str]
) -> List[str]:
    """
    Update shell configuration files and return updated paths.
    """
    updated_files: List[str] = []
    block = render_shell_block(exports, list(path_entries))
    for path in collect_shell_rc_files():
        if update_shell_config(path, block):
            updated_files.append(path)
    return updated_files


def missing_sdk_packages(sdk_root: Optional[str], required: Sequence[str]) -> List[str]:
    """
    Return required Android SDK packages missing from sdk_root.
    """
    if not sdk_root:
        return list(required)
    missing: List[str] = []
    for package in required:
        if package.startswith("platforms;"):
            name = package.split(";", 1)[1]
            if not os.path.isdir(os.path.join(sdk_root, "platforms", name)):
                missing.append(package)
        elif package.startswith("build-tools;"):
            name = package.split(";", 1)[1]
            if not os.path.isdir(os.path.join(sdk_root, "build-tools", name)):
                missing.append(package)
        elif package == "platform-tools":
            if not os.path.isdir(os.path.join(sdk_root, "platform-tools")):
                missing.append(package)
        else:
            missing.append(package)
    return missing


def install_termux_packages(packages: Sequence[str]) -> bool:
    """
    Install Termux packages via pkg.
    """
    if not packages:
        return True
    if not shutil.which("pkg"):
        print("Termux pkg command not found; install packages manually.")
        return False
    try:
        subprocess.run(["pkg", "install", "-y", *packages], check=True)
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("Failed to install Termux packages: %s", exc)
        return False


def install_android_sdk_packages(
    sdkmanager_path: str, sdk_root: str, packages: Sequence[str]
) -> bool:
    """
    Install Android SDK packages via sdkmanager.
    """
    if not packages:
        return True
    try:
        subprocess.run(
            [sdkmanager_path, f"--sdk_root={sdk_root}", *packages],
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("Failed to install Android SDK packages: %s", exc)
        return False


def accept_android_licenses(sdkmanager_path: str, sdk_root: str) -> bool:
    """
    Accept Android SDK licenses via sdkmanager.
    """
    try:
        subprocess.run(
            [sdkmanager_path, f"--sdk_root={sdk_root}", "--licenses"],
            input="y\n" * 64,
            text=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("Failed to accept Android SDK licenses: %s", exc)
        return False
