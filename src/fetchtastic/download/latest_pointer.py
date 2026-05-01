"""Best-effort latest symlink management for downloaded artifacts."""

import os
from pathlib import Path

from fetchtastic.constants import LATEST_POINTER_NAME
from fetchtastic.log_utils import logger

from .files import _sanitize_path_component


def _is_valid_latest_target(parent_dir: Path, target_name: str) -> bool:
    safe_target = _sanitize_path_component(target_name)
    if safe_target is None or safe_target != target_name:
        return False

    target = parent_dir / target_name
    if target.is_symlink():
        return False
    try:
        target.lstat()
    except OSError:
        return False
    return target.is_file() or target.is_dir()


def update_latest_pointer(
    parent_dir: str | Path,
    target_name: str,
    link_name: str = LATEST_POINTER_NAME,
) -> bool:
    """Point ``link_name`` at ``target_name`` using a same-directory relative symlink."""
    parent = Path(parent_dir)
    safe_link = _sanitize_path_component(link_name)
    if safe_link is None or safe_link != link_name:
        logger.debug("Skipping latest pointer with unsafe link name: %s", link_name)
        return False
    if not _is_valid_latest_target(parent, target_name):
        logger.debug(
            "Skipping latest pointer because target is invalid: %s/%s",
            parent,
            target_name,
        )
        return False

    link_path = parent / link_name
    tmp_path = parent / f".{link_name}.tmp"
    if parent.is_symlink():
        logger.debug(
            "Skipping latest pointer because parent dir is symlinked: %s",
            parent,
        )
        return False
    for ancestor in parent.parents:
        if ancestor.exists() and ancestor.is_symlink():
            logger.debug(
                "Skipping latest pointer because ancestor is symlinked: %s",
                ancestor,
            )
            return False
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if link_path.exists() and not link_path.is_symlink():
            logger.debug(
                "Skipping latest pointer because path is not a symlink: %s", link_path
            )
            return False
        if tmp_path.exists() or tmp_path.is_symlink():
            tmp_path.unlink()
        os.symlink(
            target_name,
            tmp_path,
            target_is_directory=(parent / target_name).is_dir(),
        )
        os.replace(tmp_path, link_path)
        logger.debug("Updated latest pointer: %s -> %s", link_path, target_name)
        return True
    except (AttributeError, NotImplementedError, OSError) as exc:
        try:
            if tmp_path.exists() or tmp_path.is_symlink():
                tmp_path.unlink()
            if link_path.exists() and not link_path.is_symlink():
                return False
            if link_path.is_symlink():
                link_path.unlink()
            os.symlink(
                target_name,
                link_path,
                target_is_directory=(parent / target_name).is_dir(),
            )
            logger.debug("Updated latest pointer: %s -> %s", link_path, target_name)
            return True
        except (AttributeError, NotImplementedError, OSError) as fallback_exc:
            logger.debug(
                "Could not update latest pointer %s -> %s: %s; fallback failed: %s",
                link_path,
                target_name,
                exc,
                fallback_exc,
            )
            return False


def remove_latest_pointer(
    parent_dir: str | Path,
    link_name: str = LATEST_POINTER_NAME,
) -> bool:
    """Remove a managed latest symlink without following it."""
    safe_link = _sanitize_path_component(link_name)
    if safe_link is None or safe_link != link_name:
        return False
    link_path = Path(parent_dir) / link_name
    try:
        if link_path.is_symlink():
            link_path.unlink()
            return True
    except OSError as exc:
        logger.debug("Could not remove latest pointer %s: %s", link_path, exc)
    return False
