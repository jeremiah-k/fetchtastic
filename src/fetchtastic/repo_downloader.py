"""
Repository file downloader compatibility helpers.

The refactor moved repository downloads into `fetchtastic.download.repository`.
This module preserves the legacy functional entrypoint used by some tests and
older integrations.
"""

import os
import re
from typing import Any, Dict, List

from fetchtastic.constants import (
    EXECUTABLE_PERMISSIONS,
    REPO_DOWNLOADS_DIR,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import download_file_with_retry


def _safe_target_dir(download_dir: str, requested_subdir: str) -> str:
    base_repo_dir = os.path.join(download_dir, "firmware", REPO_DOWNLOADS_DIR)
    os.makedirs(base_repo_dir, exist_ok=True)

    if not requested_subdir:
        return base_repo_dir

    if re.search(r"(\.\./|\.\.\\|~|\\|\.\.)", requested_subdir):
        return base_repo_dir

    if os.path.isabs(requested_subdir):
        return base_repo_dir

    base_norm = os.path.normpath(base_repo_dir)
    candidate = os.path.normpath(os.path.join(base_norm, requested_subdir))
    # Ensure candidate stays within base_repo_dir
    try:
        if os.path.commonpath([base_norm, candidate]) != base_norm:
            return base_repo_dir
    except ValueError:
        return base_repo_dir

    os.makedirs(candidate, exist_ok=True)
    return candidate


def download_repo_files(selected_files: Dict[str, Any], download_dir: str) -> List[str]:
    directory = str(selected_files.get("directory") or "")
    files = selected_files.get("files") or []

    target_dir = _safe_target_dir(download_dir, directory)
    if target_dir.endswith(REPO_DOWNLOADS_DIR) and directory:
        logger.warning(
            "Sanitized unsafe repository subdirectory '%s'; using base repo directory",
            directory,
        )

    downloaded: List[str] = []
    for file_info in files:
        name = str((file_info or {}).get("name") or "")
        url = (file_info or {}).get("download_url")
        if not name or not url:
            continue

        safe_name = os.path.basename(name).strip()
        # Validate filename to prevent path traversal
        if not safe_name or safe_name in {".", ".."} or "\x00" in safe_name:
            logger.warning(f"Skipping unsafe filename: {safe_name}")
            continue

        dest_path = os.path.join(target_dir, safe_name)
        # Ensure dest_path is within target_dir
        real_dest_path = os.path.realpath(dest_path)
        real_target_dir = os.path.realpath(target_dir)
        if not real_dest_path.startswith(real_target_dir):
            logger.warning(f"Skipping file outside target directory: {safe_name}")
            continue

        if download_file_with_retry(str(url), dest_path):
            downloaded.append(dest_path)

            # Set executable permissions for shell scripts
            if safe_name.endswith(SHELL_SCRIPT_EXTENSION):
                try:
                    import stat

                    current_permissions = os.stat(dest_path).st_mode
                    os.chmod(dest_path, current_permissions | EXECUTABLE_PERMISSIONS)
                    logger.info(f"Set executable permissions for: {dest_path}")
                except OSError as e:
                    logger.warning(
                        f"Failed to set executable permissions for {dest_path}: {e}"
                    )

    return downloaded
