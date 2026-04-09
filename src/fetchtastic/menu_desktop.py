# src/fetchtastic/menu_desktop.py

import json
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
    extract_base_name,
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

    Uses shared base-name normalization and lowercases the result for
    case-insensitive substring matching in matches_selected_patterns().

    Examples:
        Meshtastic-2.7.14-linux-x86_64.AppImage -> meshtastic-linux-x86_64.appimage
        Meshtastic_x64_2.7.14.msi -> meshtastic_x64.msi
        Meshtastic-2.7.14.dmg -> meshtastic.dmg
    """
    return extract_base_name(filename).lower()


def fetch_desktop_assets() -> list[str] | None:
    """
    Retrieve desktop client filenames from the latest Meshtastic Desktop release on GitHub.

    Returns:
        list[str] | None: Alphabetically sorted desktop asset filenames from the latest
            release. Returns an empty list when no releases or matching assets are found.
            Returns None when fetch or response parsing fails.
    """
    try:
        response = make_github_api_request(MESHTASTIC_DESKTOP_RELEASES_URL)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch desktop assets from GitHub API: {e}")
        return None

    try:
        releases = response.json()
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from GitHub API: {e}")
        return None

    if not isinstance(releases, list) or not releases:
        logger.warning("No releases found from GitHub API.")
        return []

    logger.debug(
        "Fetched %d releases from %s", len(releases), MESHTASTIC_DESKTOP_RELEASES_URL
    )
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
    Present an interactive multi-select prompt of desktop filenames ordered by platform.

    Displays the provided desktop filenames for multi-selection, ordered by platform
    (macOS, Windows, Linux) without placeholder heading rows. For each chosen filename this function computes a
    base-name pattern using `extract_wildcard_pattern` and returns a dictionary with
    selected patterns.

    Parameters:
        assets (list[str]): Desktop asset filenames to present for selection.

    Returns:
        dict[str, list[str]] | None: `{"selected_assets": [base_pattern, ...]}` when
            one or more assets are selected, `None` if no selection was made.
    """
    title = """Select the desktop client files you want to download (press SPACE to select, ENTER to confirm):
Note: Options are ordered by platform (macOS, Windows, Linux)."""

    # Build option list in platform order without non-selectable heading rows.
    grouped: dict[str, list[str]] = {}
    for asset in assets:
        label = _get_platform_label(asset) or "Other"
        grouped.setdefault(label, []).append(asset)

    display_options: list[str] = []

    for platform in PLATFORM_GROUPS:
        if platform not in grouped:
            continue
        for asset in grouped[platform]:
            display_options.append(asset)

    # Handle any unrecognized assets under "Other"
    if "Other" in grouped:
        for asset in grouped["Other"]:
            display_options.append(asset)

    selected_options = pick(
        display_options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_assets = []
    for _display_str, index in cast(list[tuple[str, int]], selected_options):
        if index < 0 or index >= len(display_options):
            continue
        asset_name = display_options[index]
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
        if assets is None:
            print(
                "Failed to fetch desktop files. Desktop clients will not be downloaded."
            )
            return None
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
