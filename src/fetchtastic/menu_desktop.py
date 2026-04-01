# src/fetchtastic/menu_desktop.py

import json
import re
from typing import cast

import requests  # type: ignore[import-untyped]
from pick import pick

from fetchtastic.client_release_discovery import (
    extract_matching_asset_names,
    is_desktop_asset_name,
    is_desktop_prerelease_tag,
    is_release_prerelease,
    select_best_release_with_assets,
)
from fetchtastic.constants import (
    MESHTASTIC_DESKTOP_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    make_github_api_request,
)

PLATFORM_GROUPS = {
    "macOS": [".dmg"],
    "Windows": [".msi", ".exe"],
    "Linux": [".deb", ".rpm", ".appimage"],
}


def _get_platform_label(filename: str) -> str | None:
    """Return the platform group label for a filename, or None if unrecognized."""
    lower = filename.lower()
    for platform, extensions in PLATFORM_GROUPS.items():
        for ext in extensions:
            if lower.endswith(ext.lower()):
                return platform
    return None


def extract_wildcard_pattern(filename: str) -> str:
    """
    Extract a normalized pattern from a desktop asset filename.

    Strips the semantic version from the filename and normalizes to match the
    format expected by matches_selected_patterns(). The result is lowercased
    and contains no wildcards, suitable for direct substring matching.

    Examples:
        Meshtastic-2.7.14-linux-x86_64.AppImage -> meshtastic-linux-x86_64.appimage
        Meshtastic_x64_2.7.14.msi -> meshtastic_x64.msi
        Meshtastic-2.7.14.dmg -> meshtastic.dmg
    """
    # Strip semantic version (with optional prerelease) using the same regex as utils.py
    version_pattern = r"[-_]?\d+\.\d+\.\d+(?:[-.]?(?:rc|dev|b|beta|alpha)\d+)?"
    result = re.sub(version_pattern, "", filename)

    # Clean up double separators that might result from version removal
    result = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], result)

    # Normalize: lowercase for case-insensitive matching
    result = result.lower()

    return result


def fetch_desktop_assets() -> list[str]:
    """
    Retrieve desktop client filenames from the latest Meshtastic Desktop release on GitHub.

    Returns:
        list[str]: Alphabetically sorted desktop asset filenames from the latest release.
                   Empty list if no releases or matching assets are found.
    """
    try:
        response = make_github_api_request(MESHTASTIC_DESKTOP_RELEASES_URL)
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

    max_releases_to_scan = min(10, len(releases))
    selected_release = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_desktop_asset_name,
        tag_prerelease_matcher=is_desktop_prerelease_tag,
        max_releases_to_scan=max_releases_to_scan,
    )
    if selected_release is None:
        logger.warning("No releases with desktop assets found in recent scan.")
        return []

    selected_tag = selected_release.get("tag_name", "<unknown>")
    if is_release_prerelease(
        selected_release, tag_prerelease_matcher=is_desktop_prerelease_tag
    ):
        logger.debug("Using prerelease with desktop assets: %s", selected_tag)
    else:
        logger.debug("Using stable release with desktop assets: %s", selected_tag)

    asset_names = extract_matching_asset_names(
        selected_release,
        asset_name_matcher=is_desktop_asset_name,
    )

    asset_names.sort()

    return asset_names


def select_assets(assets: list[str]) -> dict[str, list[str]] | None:
    """
    Present an interactive multi-select prompt of desktop filenames grouped by platform.

    Displays the provided desktop filenames for multi-selection, grouped by platform
    (macOS, Windows, Linux). For each chosen filename this function computes a
    base-name pattern using `extract_wildcard_pattern` and returns a dictionary with
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
    selected_assets = []
    for _display_str, index in cast(list[tuple[str, int]], selected_options):
        if index < 0 or index >= len(option_map):
            continue
        asset_name = option_map[index]
        if asset_name:
            selected_assets.append(asset_name)

    if not selected_assets:
        print("No desktop files selected. Desktop clients will not be downloaded.")
        return None

    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_wildcard_pattern(asset_name)
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
        if not assets:
            print("No desktop files found. Desktop clients will not be downloaded.")
            return None
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
