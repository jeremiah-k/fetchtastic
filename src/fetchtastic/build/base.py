"""
Base helpers for build modules.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Sequence

import platformdirs

from fetchtastic.env_utils import is_termux as is_termux_env
from fetchtastic.log_utils import logger


def resolve_android_sdk_root() -> Optional[str]:
    """
    Resolve an Android SDK root from environment variables or common default locations.
    """
    env_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if env_root and os.path.isdir(env_root):
        return env_root

    candidates = [
        os.path.expanduser("~/Android/sdk"),
        os.path.expanduser("~/Android/Sdk"),
        os.path.expanduser("~/Library/Android/sdk"),
        os.path.expanduser("~/Library/Android/Sdk"),
        os.path.expanduser("~/Android"),
    ]
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "platforms")):
            return candidate

    return None


def ensure_local_properties(repo_dir: str, sdk_root: str) -> None:
    """
    Ensure local.properties exists with sdk.dir configured for Gradle builds.
    """
    local_properties = os.path.join(repo_dir, "local.properties")
    if os.path.exists(local_properties):
        return

    sdk_dir_value = sdk_root.replace("\\", "\\\\")
    try:
        with open(local_properties, "w") as handle:
            handle.write(f"sdk.dir={sdk_dir_value}\n")
    except OSError as exc:
        logger.warning("Could not write local.properties: %s", exc)


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or "unknown"


def git_identifier(repo_dir: str) -> str:
    """
    Return a git tag if HEAD is tagged, otherwise a short commit hash.
    """
    tag_result = subprocess.run(
        ["git", "-C", repo_dir, "describe", "--tags", "--exact-match"],
        capture_output=True,
        text=True,
        check=False,
    )
    tag_value = tag_result.stdout.strip()
    if tag_result.returncode == 0 and tag_value:
        return _safe_identifier(tag_value)

    sha_result = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha_value = sha_result.stdout.strip()
    if sha_result.returncode == 0 and sha_value:
        return _safe_identifier(sha_value)

    return "unknown"


def newest_match(paths: Iterable[str]) -> Optional[str]:
    """
    Return the newest file path from the iterable, or None if empty.
    """
    path_list = [p for p in paths if os.path.isfile(p)]
    if not path_list:
        return None
    return max(path_list, key=os.path.getmtime)


@dataclass
class BuildResult:
    success: bool
    message: str
    build_type: str
    repo_dir: Optional[str] = None
    artifact_path: Optional[str] = None
    dest_path: Optional[str] = None
    identifier: Optional[str] = None


@dataclass
class GradleBuildModule:
    name: str
    display_name: str
    repo_url: str
    repo_dirname: str
    output_prefix: str
    output_dirname: str
    gradle_tasks: Mapping[str, str]
    artifact_globs: Mapping[str, Sequence[str]]
    release_env_vars: Sequence[str] = ()
    requirements: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def describe_requirements(self) -> List[str]:
        """
        Return a list of requirement lines for the current platform.
        """
        if is_termux_env():
            platform_key = "termux"
        else:
            system_name = platform.system()
            platform_key = system_name.lower()

        lines = self.requirements.get(platform_key, [])
        if not lines:
            lines = self.requirements.get("default", [])
        return list(lines)

    def missing_release_env(self, env: Optional[Mapping[str, str]] = None) -> List[str]:
        """
        Return missing release signing environment variables.
        """
        if not self.release_env_vars:
            return []
        env_map = env or os.environ
        return [key for key in self.release_env_vars if not env_map.get(key)]

    def build(
        self,
        build_type: str,
        *,
        base_dir: str,
        cache_dir: Optional[str] = None,
        sdk_root: Optional[str] = None,
        allow_update: bool = True,
    ) -> BuildResult:
        """
        Build the module using Gradle and copy the artifact into base_dir/output_dirname.
        """
        build_type = build_type.lower()
        if build_type not in self.gradle_tasks:
            return BuildResult(
                success=False,
                message=f"Unknown build type: {build_type}",
                build_type=build_type,
            )

        cache_root = cache_dir or platformdirs.user_cache_dir("fetchtastic")
        repo_dir = os.path.join(cache_root, self.repo_dirname)
        os.makedirs(cache_root, exist_ok=True)

        repo_ready = _ensure_repo(self.repo_url, repo_dir, allow_update=allow_update)
        if not repo_ready:
            return BuildResult(
                success=False,
                message=f"Failed to prepare repository at {repo_dir}",
                build_type=build_type,
                repo_dir=repo_dir,
            )

        gradlew = _resolve_gradlew(repo_dir)
        if not gradlew:
            return BuildResult(
                success=False,
                message="Gradle wrapper not found in repository.",
                build_type=build_type,
                repo_dir=repo_dir,
            )

        build_env = os.environ.copy()
        if sdk_root:
            build_env.setdefault("ANDROID_SDK_ROOT", sdk_root)
            build_env.setdefault("ANDROID_HOME", sdk_root)
            ensure_local_properties(repo_dir, sdk_root)

        try:
            subprocess.run(
                [gradlew, self.gradle_tasks[build_type]],
                check=True,
                cwd=repo_dir,
                env=build_env,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            return BuildResult(
                success=False,
                message=f"Build failed: {exc}",
                build_type=build_type,
                repo_dir=repo_dir,
            )

        artifact = _find_artifact(repo_dir, self.artifact_globs.get(build_type, ()))
        if not artifact:
            return BuildResult(
                success=False,
                message="Build finished but no APK was found.",
                build_type=build_type,
                repo_dir=repo_dir,
            )

        identifier = git_identifier(repo_dir)
        dest_dir = os.path.join(os.path.expanduser(base_dir), self.output_dirname)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, f"{self.output_prefix}-{identifier}.apk")
        try:
            shutil.copy2(artifact, dest_path)
        except OSError as exc:
            return BuildResult(
                success=False,
                message=f"Failed to copy APK: {exc}",
                build_type=build_type,
                repo_dir=repo_dir,
                artifact_path=artifact,
                identifier=identifier,
            )

        return BuildResult(
            success=True,
            message=f"Saved APK to {dest_path}",
            build_type=build_type,
            repo_dir=repo_dir,
            artifact_path=artifact,
            dest_path=dest_path,
            identifier=identifier,
        )


def _ensure_repo(repo_url: str, repo_dir: str, *, allow_update: bool) -> bool:
    if os.path.exists(repo_dir):
        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            logger.error("Existing path is not a git repo: %s", repo_dir)
            return False
        if not allow_update:
            return True

        status = subprocess.run(
            ["git", "-C", repo_dir, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            logger.warning("Could not read repo status; skipping update.")
            return True
        if status.stdout.strip():
            logger.info("Repo has local changes; skipping update.")
            return True

        fetch_result = subprocess.run(
            ["git", "-C", repo_dir, "fetch", "--tags"], check=False
        )
        pull_result = subprocess.run(
            ["git", "-C", repo_dir, "pull", "--ff-only"], check=False
        )
        if fetch_result.returncode != 0 or pull_result.returncode != 0:
            logger.warning("Repo update failed; using existing checkout.")
        return True

    try:
        subprocess.run(["git", "clone", "--depth", "1", repo_url, repo_dir], check=True)
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("Failed to clone repo: %s", exc)
        return False


def _resolve_gradlew(repo_dir: str) -> Optional[str]:
    gradlew = "gradlew.bat" if os.name == "nt" else "gradlew"
    gradlew_path = os.path.join(repo_dir, gradlew)
    if not os.path.exists(gradlew_path):
        return None
    if os.name != "nt":
        try:
            os.chmod(gradlew_path, os.stat(gradlew_path).st_mode | 0o111)
        except OSError as exc:
            logger.debug("Could not chmod gradlew: %s", exc)
    return gradlew_path


def _find_artifact(repo_dir: str, patterns: Sequence[str]) -> Optional[str]:
    candidates: List[str] = []
    for pattern in patterns:
        candidates.extend(glob.glob(os.path.join(repo_dir, pattern)))
    artifact = newest_match(candidates)
    if artifact:
        return artifact
    return newest_match(
        glob.glob(os.path.join(repo_dir, "**", "*.apk"), recursive=True)
    )
