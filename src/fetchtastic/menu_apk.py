# src/fetchtastic/menu_apk.py

import json
from typing import Any, Dict, Sequence, Union, cast

import requests  # type: ignore[import-untyped]
from pick import pick

from fetchtastic.client_release_discovery import (
    extract_matching_asset_dicts,
    is_android_asset_name,
    is_android_prerelease_tag,
    select_best_release_with_assets,
)
from fetchtastic.constants import (
    BYTES_PER_MEGABYTE,
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    extract_base_name,
    make_github_api_request,
)


def _get_apk_category(filename: str) -> tuple[str, str]:
    """
    Categorize an APK filename and return (group_label, display_name).

    Returns:
        tuple[str, str]: (group_label, display_name) where group_label is used for
            sorting and display_name is the human-readable label for the architecture.
    """
    lower = filename.lower()

    if "-google-" in lower:
        return ("0", "Google")
    if "-fdroid-universal-" in lower:
        return ("1", "F-Droid Universal")
    if "-fdroid-arm64-" in lower:
        return ("2", "F-Droid ARM64")
    if "-fdroid-armeabi-" in lower:
        return ("3", "F-Droid ARMv7")
    if "-fdroid-x86_64-" in lower:
        return ("4", "F-Droid x86_64")
    if "-fdroid-" in lower:
        return ("5", "F-Droid Other")

    return ("6", "Other")


def _format_file_size(size_bytes: int) -> str:
    """Format file size in MB, rounded to 1 decimal place."""
    size_mb = size_bytes / BYTES_PER_MEGABYTE
    return f"{size_mb:.1f} MB"


def fetch_apk_assets() -> list[Dict[str, Any]]:
    """
    Retrieve APK asset info from the latest Meshtastic Android release on GitHub.

    Returns:
        list[Dict[str, Any]]: List of APK assets as dicts with `name` and `size`,
            sorted alphabetically by name. Empty list if no releases or matching
            assets are found.
    """
    try:
        response = make_github_api_request(MESHTASTIC_ANDROID_RELEASES_URL)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch APK assets from GitHub API: {e}")
        return []

    try:
        releases = response.json()
        if isinstance(releases, list):
            logger.debug(f"Fetched {len(releases)} Android releases from GitHub API")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from GitHub API: {e}")
        return []
    if not isinstance(releases, list) or not releases:
        logger.warning("No Android releases found from GitHub API.")
        return []
    max_releases_to_scan = min(10, len(releases))
    selected_release = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
        max_releases_to_scan=max_releases_to_scan,
    )
    if selected_release is None:
        logger.warning("No Android releases with APK assets found in recent scan.")
        return []

    asset_list = extract_matching_asset_dicts(
        selected_release,
        asset_name_matcher=is_android_asset_name,
    )

    asset_list.sort(key=lambda item: item["name"])
    return asset_list


def _normalize_apk_assets(
    assets: Sequence[Union[str, Dict[str, Any]]],
) -> list[Dict[str, Any]]:
    """
    Normalize APK assets into dict items with `name` and `size` keys.

    Accepts either legacy string filenames or dict entries returned by GitHub API
    processing. Invalid entries are ignored.
    """
    normalized: list[Dict[str, Any]] = []
    for asset in assets:
        if isinstance(asset, str):
            if asset:
                normalized.append({"name": asset, "size": 0})
            continue
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str) or not name:
            continue
        size = asset.get("size", 0)
        normalized.append(
            {"name": name, "size": size if isinstance(size, int) and size >= 0 else 0}
        )
    return normalized


def select_assets(
    assets: Sequence[Union[str, Dict[str, Any]]],
) -> dict[str, list[str]] | None:
    """
    Present an interactive multi-select prompt of APK filenames grouped by architecture.

    Displays the provided APK filenames for multi-selection, grouped by:
    - Google flavor
    - F-Droid Universal
    - F-Droid ARM64
    - F-Droid ARMv7
    - F-Droid x86_64

    For each chosen filename this function computes a base-name pattern using
    `extract_base_name` and returns a dictionary `{"selected_assets": [base_pattern, ...]}`.
    If no assets are selected, the function prints a short message and returns `None`.

    Parameters:
        assets (list[Union[str, dict]]): APK assets as filenames or dict entries
            containing `name` and optional `size`.

    Returns:
        dict[str, list[str]] | None: `{"selected_assets": [base_pattern, ...]}` when
            one or more assets are selected, `None` if no selection was made.
    """
    title = """Select the APK files you want to download (press SPACE to select, ENTER to confirm):
Note: Options are grouped by flavor and architecture. File sizes shown in parentheses."""

    normalized_assets = _normalize_apk_assets(assets)
    if not normalized_assets:
        print("No valid APK files found. APKs will not be downloaded.")
        return None

    grouped: dict[str, list[Dict[str, Any]]] = {}
    for asset in normalized_assets:
        group_label, _ = _get_apk_category(asset["name"])
        grouped.setdefault(group_label, []).append(asset)

    display_options: list[str] = []
    option_map: dict[str, str] = {}

    ordered_groups = sorted(grouped.keys())
    for group_key in ordered_groups:
        group_assets = grouped[group_key]
        if not group_assets:
            continue
        _, display_name = _get_apk_category(group_assets[0]["name"])
        display_options.append(f"--- {display_name} ---")
        for asset in sorted(group_assets, key=lambda x: x["name"]):
            size_bytes = asset["size"] if isinstance(asset["size"], int) else 0
            size_str = _format_file_size(max(0, size_bytes))
            display = f"{asset['name']} ({size_str})"
            display_options.append(display)
            option_map[display] = asset["name"]

    selected_options = pick(
        display_options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_display = [
        option[0] for option in cast(list[tuple[str, int]], selected_options)
    ]

    asset_names = {asset["name"] for asset in normalized_assets}
    selected_assets = []
    for display_str in selected_display:
        if display_str in option_map:
            selected_assets.append(option_map[display_str])
        elif display_str in asset_names:
            # Backward-compatible path for tests/mocks that return raw filenames.
            selected_assets.append(display_str)

    if not selected_assets:
        print("No APK files selected. APKs will not be downloaded.")
        return None

    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu() -> dict[str, list[str]] | None:
    """
    Show an interactive APK selection menu and return the chosen base-name patterns.

    Presents a multi-select prompt for available APK filenames grouped by architecture
    and returns a dictionary with selected base-name patterns when one or more items
    are chosen.

    Returns:
        dict[str, list[str]]: A mapping with key "selected_assets" to the list of selected
            base-name patterns (e.g., {"selected_assets": [...]}).
        None: If no selection is made, the user aborts, or an error occurs.
    """
    try:
        assets = fetch_apk_assets()
        if not assets:
            print("No APK files found. APKs will not be downloaded.")
            return None
        selected_result = select_assets(assets)
        if selected_result is None:
            return None
        return selected_result
    except (json.JSONDecodeError, ValueError):
        logger.exception("APK menu failed due to data error")
        return None
    except (requests.RequestException, OSError):
        logger.exception("APK menu failed due to network/I/O error")
        return None
    except (TypeError, KeyError, AttributeError):
        logger.exception("APK menu failed due to data structure error")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("APK menu failed due to unexpected error")
        return None
