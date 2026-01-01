"""
Interactive helpers for build modules.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional, Sequence

from fetchtastic.build.base import (
    BuildResult,
    GradleBuildModule,
    resolve_android_sdk_root,
)
from fetchtastic.build.environment import (
    BuildEnvironment,
    accept_android_licenses,
    build_shell_exports,
    cmdline_tools_host_os,
    default_android_sdk_root,
    detect_java_home,
    detect_missing_termux_optional_packages,
    detect_missing_termux_packages,
    find_sdkmanager,
    install_android_sdk_packages,
    install_cmdline_tools,
    install_termux_packages,
    missing_sdk_packages,
    update_process_env,
    update_shell_configs,
)
from fetchtastic.env_utils import is_termux


def print_build_requirements(module: GradleBuildModule) -> None:
    """
    Print requirement hints for a build module.
    """
    for line in module.describe_requirements():
        print(f"- {line}")


def prompt_yes_no(prompt: str, default: str = "no") -> bool:
    """
    Prompt for a yes/no answer and return True for yes.
    """
    default_value = default.strip().lower()
    if default_value not in {"y", "yes", "n", "no"}:
        default_value = "no"
    choice = input(prompt).strip().lower()
    if not choice:
        choice = default_value
    return choice.startswith("y")


def prompt_build_type(
    prompt: str = "Build type? [d]ebug/[r]elease (default: debug): ",
    default: str = "debug",
) -> str:
    """
    Prompt for a build type and return "debug" or "release".
    """
    choice = input(prompt).strip().lower()
    if not choice:
        choice = default
    return "release" if choice.startswith("r") else "debug"


def check_build_environment(module: GradleBuildModule) -> BuildEnvironment:
    """
    Inspect the environment needed for a build module.
    """
    java_home = detect_java_home()
    sdk_root = resolve_android_sdk_root()
    missing_packages: Sequence[str] = []
    if is_termux():
        missing_packages = detect_missing_termux_packages()
    sdkmanager_path = find_sdkmanager(sdk_root)
    missing_sdk = missing_sdk_packages(sdk_root, module.required_sdk_packages)
    return BuildEnvironment(
        java_home=java_home,
        sdk_root=sdk_root,
        sdkmanager_path=sdkmanager_path,
        missing_packages=list(missing_packages),
        missing_sdk_packages=list(missing_sdk),
    )


def prepare_build_environment(
    module: GradleBuildModule,
    *,
    install_missing_packages: bool = True,
    configure_shell: bool = True,
    install_sdk_packages: bool = True,
) -> Optional[BuildEnvironment]:
    """
    Ensure a build environment is ready, prompting to install missing dependencies.
    """
    env_status = check_build_environment(module)
    if env_status.missing_packages:
        print("Missing Termux packages:")
        for package in env_status.missing_packages:
            print(f"- {package}")
        if not install_missing_packages:
            print("Install missing packages and re-run setup.")
            return None
        if not prompt_yes_no(
            "Install missing Termux packages now? [y/n] (default: yes): ",
            default="yes",
        ):
            print("Skipping package installation.")
            return None
        if not install_termux_packages(env_status.missing_packages):
            return None
        env_status = check_build_environment(module)

    if is_termux():
        optional_missing = detect_missing_termux_optional_packages()
        if optional_missing:
            print("Recommended Termux packages for Android build tooling:")
            for package in optional_missing:
                print(f"- {package}")
            if not install_missing_packages:
                print("Install these packages and re-run setup if builds fail.")
            elif prompt_yes_no(
                "Install recommended Termux packages now? [y/n] (default: yes): ",
                default="yes",
            ):
                install_termux_packages(optional_missing)

    if not env_status.java_home:
        print("JAVA_HOME is not set and could not be detected.")
        if is_termux():
            print("Install openjdk-17 and re-run: pkg install openjdk-17")
        else:
            print("Install JDK 17 and ensure JAVA_HOME is set.")
        return None

    if not env_status.sdk_root:
        default_root = default_android_sdk_root()
        try:
            os.makedirs(default_root, exist_ok=True)
        except OSError as exc:
            print(f"Failed to create Android SDK root at {default_root}: {exc}")
            return None
        env_status.sdk_root = default_root
        print(f"Using ANDROID_SDK_ROOT: {default_root}")

    exports, path_entries = build_shell_exports(
        env_status.java_home, env_status.sdk_root
    )
    update_process_env(exports, path_entries)
    if configure_shell and exports:
        updated = update_shell_configs(exports, path_entries)
        if updated:
            print("Updated shell config files:")
            for path in updated:
                print(f"- {path}")
            print("Restart your shell or source the updated file(s) to apply changes.")
        else:
            print("Shell config already contains Fetchtastic DFU settings.")

    env_status.sdkmanager_path = find_sdkmanager(env_status.sdk_root)
    if not env_status.sdkmanager_path:
        if not install_sdk_packages:
            print("Android sdkmanager not found. Install cmdline-tools and retry.")
            return None
        if not install_cmdline_tools(
            env_status.sdk_root, host_os=cmdline_tools_host_os()
        ):
            return None
        env_status.sdkmanager_path = find_sdkmanager(env_status.sdk_root)
    env_status.missing_sdk_packages = missing_sdk_packages(
        env_status.sdk_root, module.required_sdk_packages
    )

    if env_status.missing_sdk_packages:
        if not env_status.sdkmanager_path:
            print("Android sdkmanager not found. Install cmdline-tools and retry.")
            return None
        print("Missing Android SDK packages:")
        for package in env_status.missing_sdk_packages:
            print(f"- {package}")
        if not install_sdk_packages:
            print("Install required SDK packages and re-run setup.")
            return None
        if prompt_yes_no(
            "Install missing Android SDK packages now? [y/n] (default: yes): ",
            default="yes",
        ):
            if not install_android_sdk_packages(
                env_status.sdkmanager_path,
                env_status.sdk_root,
                env_status.missing_sdk_packages,
            ):
                return None
            if prompt_yes_no(
                "Accept Android SDK licenses now? [y/n] (default: yes): ",
                default="yes",
            ):
                accept_android_licenses(env_status.sdkmanager_path, env_status.sdk_root)
            env_status.missing_sdk_packages = missing_sdk_packages(
                env_status.sdk_root, module.required_sdk_packages
            )
        if env_status.missing_sdk_packages:
            print("Android SDK packages are still missing.")
            return None

    return env_status


def run_module_build(
    module: GradleBuildModule,
    *,
    base_dir: str,
    build_type: Optional[str] = None,
    allow_update: Optional[bool] = None,
    sdk_root: Optional[str] = None,
    prompt_for_build_type: bool = True,
    prompt_for_update: bool = False,
    update_prompt: Optional[str] = None,
    start_message: Optional[str] = None,
) -> Optional[BuildResult]:
    """
    Run a build module, prompting for any missing options.
    """
    if not shutil.which("git"):
        print("Git is required to clone the repository. Please install git.")
        return None

    if sdk_root is None:
        sdk_root = resolve_android_sdk_root()
    if not sdk_root:
        print("Warning: Android SDK not detected (set ANDROID_SDK_ROOT).")

    if build_type is None:
        if prompt_for_build_type:
            build_type = prompt_build_type()
        else:
            build_type = "debug"

    build_type = build_type.lower()
    if build_type == "release":
        missing_env = module.missing_release_env()
        if missing_env:
            print("Release builds require signing env vars:")
            for name in missing_env:
                print(f"- {name}")
            print("Set these variables and re-run the build if needed.")
            return None

    if allow_update is None and prompt_for_update:
        if update_prompt is None:
            update_prompt = (
                f"Update the {module.name.upper()} repo before building? "
                "[y/n] (default: yes): "
            )
        allow_update = prompt_yes_no(update_prompt, default="yes")
    if allow_update is None:
        allow_update = True

    if start_message:
        print(start_message)

    return module.build(
        build_type,
        base_dir=base_dir,
        sdk_root=sdk_root,
        allow_update=allow_update,
    )
