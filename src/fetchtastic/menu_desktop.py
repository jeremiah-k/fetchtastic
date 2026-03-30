# src/fetchtastic/menu_desktop.py

import json
from typing import cast

import requests  # type: ignore[import-untyped]
from pick import pick

from fetchtastic.constants import (
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    make_github_api_request,
)

DESKTOP_EXTENSIONS = (".dmg", ".msi", ".exe", ".deb", ".rpm", ".appimage", ".AppImage")

PLATFORM_GROUPS = {
    "macOS": [".dmg"],
    "Windows": [".msi", ".exe"],
    "Linux": [".deb", ".rpm", ".appimage", ".AppImage"],
}


def _get_platform_label(filename: str) -> str | None:
    """Return the platform group label for a filename, or None if unrecognized."""
    lower = filename.lower()
    for platform, extensions in PLATFORM_GROUPS.items():
        for ext in extensions:
            if lower.endswith(ext.lower()):
                return platform
    return None


def extract_base_name(filename: str) -> str:
    """
    Extract a version-flexible base name pattern from a desktop asset filename.

    Strips the semantic version from the filename and replaces separators with
    wildcards so the pattern can match across releases.

    Examples:
        Meshtastic-2.7.14-linux-x86_64.AppImage -> *Meshtastic*linux*x86_64*AppImage*
        Meshtastic_x64_2.7.14.msi -> *Meshtastic_x64*msi*
        Meshtastic-2.7.14.dmg -> *Meshtastic*dmg*
    """
    import re

    # Strip semantic version (with optional prerelease) and surrounding separators
    version_pattern = r"[-_]?\d+\.\d+\.\d+(?:[-.]?(?:rc|dev|b|beta|alpha)\d+)?"
    result = re.sub(version_pattern, "", filename)

    # Replace remaining hyphens and dots with wildcards
    result = re.sub(r"[-.]+", "*", result)

    # Collapse consecutive wildcards
    result = re.sub(r"\*{2,}", "*", result)

    # Strip leading/trailing wildcards for clean wrapping
    result = result.strip("*")

    # Wrap with wildcards for flexible matching
    return f"*{result}*"


def fetch_desktop_assets() -> list[str]:
    """
    Retrieve desktop client filenames from the latest Meshtastic Android release on GitHub.

    Returns:
        list[str]: Alphabetically sorted desktop asset filenames from the latest release.
                   Empty list if no releases or matching assets are found.
    """
    try:
        response = make_github_api_request(MESHTASTIC_ANDROID_RELEASES_URL)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch desktop assets from GitHub API: {e}")
        return []

    try:
        releases = response.json()
        if isinstance(releases, list):
            logger.debug(f"Fetched {len(releases)} releases from GitHub API")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from GitHub API: {e}")
        return []
    if not isinstance(releases, list) or not releases:
        logger.warning("No releases found from GitHub API.")
        return []
    latest_release = releases[0] or {}
    assets = latest_release.get("assets", []) or []
    if not isinstance(assets, list):
        logger.warning("Invalid assets data from GitHub API.")
        return []
    asset_names = sorted(
        [
            asset_name
            for asset in assets
            if isinstance(asset, dict)
            and (asset_name := asset.get("name"))
            and any(asset_name.endswith(ext) for ext in DESKTOP_EXTENSIONS)
        ]
    )

    return asset_names


def select_assets(assets: list[str]) -> dict[str, list[str]] | None:
    """
    Present an interactive multi-select prompt of desktop filenames grouped by platform.

    Displays the provided desktop filenames for multi-selection, grouped by platform
    (macOS, Windows, Linux). For each chosen filename this function computes a
    base-name pattern using `extract_base_name` and returns a dictionary with
    selected patterns.

    Parameters:
        assets (list[str]): Desktop asset filenames to present for selection.

    Returns:
        dict[str, list[str]] | None: `{"selected_assets": [base_pattern, ...]}` when
            one or more assets are selected, `None` if no selection was made.
    """
    title = """Select the desktop client files you want to download (press SPACE to select, ENTER to confirm):
Note: Options are grouped by platform (macOS, Windows, Linux)."""

    # Build display options with platform group labels
    grouped: dict[str, list[str]] = {}
    for asset in assets:
        label = _get_platform_label(asset) or "Other"
        grouped.setdefault(label, []).append(asset)

    display_options: list[str] = []
    option_map: list[str] = []  # Maps display indices to actual asset names

    for platform in PLATFORM_GROUPS:
        if platform not in grouped:
            continue
        display_options.append(f"--- {platform} ---")
        option_map.append("")  # Placeholder for group label
        for asset in grouped[platform]:
            display_options.append(f"  {asset}")
            option_map.append(asset)

    # Handle any unrecognized assets under "Other"
    if "Other" in grouped:
        display_options.append("--- Other ---")
        option_map.append("")
        for asset in grouped["Other"]:
            display_options.append(f"  {asset}")
            option_map.append(asset)

    selected_options = pick(
        display_options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_display = [
        option[0] for option in cast(list[tuple[str, int]], selected_options)
    ]

    # Map display strings back to asset names, skipping group labels
    selected_assets = []
    for display_str in selected_display:
        stripped = display_str.strip()
        if stripped.startswith("---") or not stripped:
            continue
        if stripped in [a for a in assets]:
            selected_assets.append(stripped)

    if not selected_assets:
        print("No desktop files selected. Desktop clients will not be downloaded.")
        return None

    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu() -> dict[str, list[str]] | None:
    """
    Show an interactive desktop selection menu and return the chosen base-name patterns.

    Presents a multi-select prompt for available desktop client filenames grouped by
    platform and returns a dictionary with selected base-name patterns.

    Returns:
        dict[str, list[str]]: A mapping with key "selected_assets" to the list of selected
            base-name patterns (e.g., {"selected_assets": [...]}).
        None: If no selection is made, the user aborts, or an error occurs.
    """
    try:
        assets = fetch_desktop_assets()
        selected_result = select_assets(assets)
        if selected_result is None:
            return None
        return selected_result
    except (json.JSONDecodeError, ValueError):
        logger.exception("Desktop menu failed due to data error")
        return None
    except (requests.RequestException, OSError):
        logger.exception("Desktop menu failed due to network/I/O error")
        return None
    except (TypeError, KeyError, AttributeError):
        logger.exception("Desktop menu failed due to data structure error")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Desktop menu failed due to unexpected error")
        return None
