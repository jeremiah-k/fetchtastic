"""
Environment detection and setup helpers for build modules.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fetchtastic.env_utils import is_termux
from fetchtastic.log_utils import logger

SHELL_BLOCK_START = "# >>> fetchtastic dfu setup >>>"
SHELL_BLOCK_END = "# <<< fetchtastic dfu setup <<<"

TERMUX_PACKAGE_COMMANDS: Dict[str, Sequence[str]] = {
    "git": ("git",),
    "curl": ("curl", "wget"),
    "unzip": ("unzip",),
    "zip": ("zip",),
    "aapt2": ("aapt2",),
}

TERMUX_OPTIONAL_PACKAGE_COMMANDS: Dict[str, Sequence[str]] = {
    "apksigner": ("apksigner",),
    "d8": ("d8",),
    "android-tools": ("adb",),
}

CMDLINE_TOOLS_MANIFEST_URL = (
    "https://dl.google.com/android/repository/repository2-1.xml"
)
CMDLINE_TOOLS_BASE_URL = "https://dl.google.com/android/repository/"


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


def detect_missing_termux_optional_packages() -> List[str]:
    """
    Return optional Termux packages whose commands are missing from PATH.
    """
    missing: List[str] = []
    for package, commands in TERMUX_OPTIONAL_PACKAGE_COMMANDS.items():
        if not any(shutil.which(cmd) for cmd in commands):
            missing.append(package)
    return missing


def cmdline_tools_host_os() -> str:
    """
    Return the host-os label used by the Android cmdline-tools repository.
    """
    if is_termux():
        return "linux"
    system = platform.system().lower()
    if system == "darwin":
        return "macosx"
    if system == "windows":
        return "windows"
    return "linux"


def resolve_cmdline_tools_url(host_os: Optional[str] = None) -> Optional[str]:
    """
    Resolve a cmdline-tools archive URL for the given host.
    """
    host = host_os or cmdline_tools_host_os()
    try:
        xml_data = urllib.request.urlopen(CMDLINE_TOOLS_MANIFEST_URL, timeout=20).read()
        root = ET.fromstring(xml_data)
    except (OSError, ET.ParseError) as exc:
        logger.error("Failed to load Android cmdline-tools manifest: %s", exc)
        return None

    best_version: Optional[Tuple[int, int, int]] = None
    best_url: Optional[str] = None
    for pkg in root.findall(".//{*}remotePackage"):
        path = pkg.get("path") or ""
        if not path.startswith("cmdline-tools;"):
            continue
        if any(tag in path for tag in ("alpha", "beta", "rc")):
            continue
        revision = pkg.find("{*}revision")
        if revision is None:
            continue
        version = (
            int(revision.findtext("{*}major", default="0")),
            int(revision.findtext("{*}minor", default="0")),
            int(revision.findtext("{*}micro", default="0")),
        )
        for archive in pkg.findall(".//{*}archive"):
            if archive.findtext("{*}host-os") != host:
                continue
            url = archive.findtext("{*}complete/{*}url")
            if not url:
                continue
            if best_version is None or version > best_version:
                best_version = version
                best_url = url

    if not best_url:
        return None
    if best_url.startswith(("http://", "https://")):
        return best_url
    return CMDLINE_TOOLS_BASE_URL + best_url


def install_cmdline_tools(sdk_root: str, host_os: Optional[str] = None) -> bool:
    """
    Download and install Android cmdline-tools into sdk_root/cmdline-tools/latest.
    """
    url = resolve_cmdline_tools_url(host_os)
    if not url:
        print("Unable to locate Android cmdline-tools download URL.")
        return False

    print(f"Downloading Android cmdline-tools from: {url}")
    tmp_dir = tempfile.mkdtemp(prefix="fetchtastic-cmdline-tools-")
    archive_path = os.path.join(tmp_dir, "cmdline-tools.zip")
    try:
        urllib.request.urlretrieve(url, archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(tmp_dir)
        extracted = os.path.join(tmp_dir, "cmdline-tools")
        if not os.path.isdir(extracted):
            for root, _dirs, files in os.walk(tmp_dir):
                if "sdkmanager" in files and os.path.basename(root) == "bin":
                    extracted = os.path.dirname(root)
                    break
        if not os.path.isdir(extracted):
            print("Failed to locate cmdline-tools in downloaded archive.")
            return False

        cmdline_root = os.path.join(sdk_root, "cmdline-tools")
        os.makedirs(cmdline_root, exist_ok=True)
        latest_dir = os.path.join(cmdline_root, "latest")
        shutil.rmtree(latest_dir, ignore_errors=True)
        shutil.move(extracted, latest_dir)
        ensure_cmdline_tools_executables(latest_dir)
        return True
    except (OSError, zipfile.BadZipFile) as exc:
        logger.error("Failed to install cmdline-tools: %s", exc)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _parse_java_version(text: str) -> Optional[int]:
    match = re.search(r'version "(\d+)', text)
    if match:
        return int(match.group(1))
    return None


def _java_version_from_home(java_home: str) -> Optional[int]:
    java_bin = os.path.join(java_home, "bin", "java")
    if os.path.isfile(java_bin):
        try:
            result = subprocess.run(
                [java_bin, "-version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            result = None
        if result:
            text = result.stderr or result.stdout
            version = _parse_java_version(text)
            if version:
                return version
    match = re.search(r"java[-_/](\d+)", java_home)
    if match:
        return int(match.group(1))
    return None


def _select_termux_java_home(min_version: Optional[int]) -> Optional[str]:
    prefix = os.environ.get("PREFIX")
    if not prefix:
        return None
    if min_version:
        candidates = [21, 17, 11]
        candidates = [version for version in candidates if version >= min_version]
    else:
        candidates = [17, 21, 11]
    for version in candidates:
        path = os.path.join(prefix, "lib", "jvm", f"java-{version}-openjdk")
        if os.path.isdir(path):
            return path
    return None


def detect_java_home(min_version: Optional[int] = None) -> Optional[str]:
    """
    Detect a JAVA_HOME path from environment or known locations.
    """
    prefix = os.environ.get("PREFIX")
    env_java = os.environ.get("JAVA_HOME")
    if env_java and os.path.isdir(env_java):
        if min_version is None:
            return env_java
        version = _java_version_from_home(env_java)
        if version and version >= min_version:
            return env_java

    if is_termux():
        termux_home = _select_termux_java_home(min_version)
        if termux_home:
            return termux_home

    javac_path = shutil.which("javac")
    if javac_path:
        real_javac = os.path.realpath(javac_path)
        candidate = os.path.dirname(os.path.dirname(real_javac))
        if os.path.isdir(candidate):
            if min_version is None:
                return candidate
            version = _java_version_from_home(candidate)
            if version and version >= min_version:
                return candidate

    if prefix:
        for candidate in (
            "java-17-openjdk",
            "java-21-openjdk",
            "java-11-openjdk",
        ):
            path = os.path.join(prefix, "lib", "jvm", candidate)
            if os.path.isdir(path):
                if min_version is None:
                    return path
                version = _java_version_from_home(path)
                if version and version >= min_version:
                    return path

    return None


def default_android_sdk_root() -> str:
    """
    Return a default Android SDK root based on platform.
    """
    if is_termux():
        return os.path.expanduser("~/Android/sdk")
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
            ensure_executable(candidate)
            if os.access(candidate, os.X_OK) or os.name == "nt":
                return candidate
    return None


def ensure_executable(path: str) -> bool:
    """
    Ensure a filesystem path is executable. Returns True if executable.
    """
    try:
        if not os.path.isfile(path):
            return False
        if os.access(path, os.X_OK):
            return True
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o111)
        return os.access(path, os.X_OK)
    except OSError as exc:
        logger.warning("Failed to mark executable %s: %s", path, exc)
        return False


def ensure_cmdline_tools_executables(cmdline_root: str) -> None:
    """
    Ensure cmdline-tools bin scripts are executable.
    """
    bin_dir = os.path.join(cmdline_root, "bin")
    if not os.path.isdir(bin_dir):
        return
    for name in os.listdir(bin_dir):
        if name.endswith(".bat"):
            continue
        path = os.path.join(bin_dir, name)
        if os.path.isfile(path):
            ensure_executable(path)


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

    def _has_prefixed_dir(root: str, prefix: str) -> bool:
        if os.path.isdir(os.path.join(root, prefix)):
            return True
        try:
            for entry in os.listdir(root):
                if entry.startswith(prefix):
                    return True
        except OSError:
            return False
        return False

    def _platform_installed(root: str, name: str) -> bool:
        platform_root = os.path.join(root, "platforms")
        return _has_prefixed_dir(platform_root, name)

    def _build_tools_installed(root: str, name: str) -> bool:
        tools_root = os.path.join(root, "build-tools")
        if os.path.isdir(os.path.join(tools_root, name)):
            return True
        parts = name.split(".")
        prefix = ".".join(parts[:2]) if len(parts) >= 2 else name
        if _has_prefixed_dir(tools_root, prefix):
            return True
        if is_termux():
            try:
                return any(
                    os.path.isdir(os.path.join(tools_root, entry))
                    for entry in os.listdir(tools_root)
                )
            except OSError:
                return False
        return False

    missing: List[str] = []
    for package in required:
        if package.startswith("platforms;"):
            name = package.split(";", 1)[1]
            if not _platform_installed(sdk_root, name):
                missing.append(package)
        elif package.startswith("build-tools;"):
            name = package.split(";", 1)[1]
            if not _build_tools_installed(sdk_root, name):
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
