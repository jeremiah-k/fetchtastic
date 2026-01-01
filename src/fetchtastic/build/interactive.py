"""
Interactive helpers for build modules.
"""

from __future__ import annotations

import shutil
from typing import Optional

from fetchtastic.build.base import (
    BuildResult,
    GradleBuildModule,
    resolve_android_sdk_root,
)


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
