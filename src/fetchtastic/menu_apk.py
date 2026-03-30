# src/fetchtastic/menu_apk.py

import json
from typing import cast

import requests  # type: ignore[import-untyped]
from pick import pick

from fetchtastic.constants import (
    APK_EXTENSION,
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


def fetch_apk_assets() -> list[dict]:
    """
    Retrieve APK asset info from the latest Meshtastic Android release on GitHub.

    Returns:
        list[dict]: List of dicts with 'name' and 'size' keys, sorted alphabetically by name.
                    Empty list if no releases or matching assets are found.
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
    latest_release = releases[0] or {}
    assets = latest_release.get("assets", []) or []
    if not isinstance(assets, list):
        logger.warning("Invalid assets data from GitHub API.")
        return []

    asset_list = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_name = asset.get("name")
        if not asset_name or not asset_name.lower().endswith(APK_EXTENSION):
            continue
        asset_size = asset.get("size", 0)
        asset_list.append({"name": asset_name, "size": asset_size})

    asset_list.sort(key=lambda x: x["name"])
    return asset_list


def select_assets(assets: list[dict]) -> dict[str, list[str]] | None:
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
        assets (list[dict]): List of dicts with 'name' and 'size' keys for APK assets.

    Returns:
        dict[str, list[str]] | None: `{"selected_assets": [base_pattern, ...]}` when
            one or more assets are selected, `None` if no selection was made.
    """
    title = """Select the APK files you want to download (press SPACE to select, ENTER to confirm):
Note: Options are grouped by flavor and architecture. File sizes shown in parentheses."""

    grouped: dict[str, list[dict]] = {}
    for asset in assets:
        group_label, _ = _get_apk_category(asset["name"])
        grouped.setdefault(group_label, []).append(asset)

    display_options: list[str] = []
    option_map: list[str] = []

    ordered_groups = sorted(grouped.keys())
    for group_key in ordered_groups:
        group_assets = grouped[group_key]
        if not group_assets:
            continue
        _, display_name = _get_apk_category(group_assets[0]["name"])
        display_options.append(f"--- {display_name} ---")
        option_map.append("")
        for asset in sorted(group_assets, key=lambda x: x["name"]):
            size_str = _format_file_size(asset["size"])
            display_options.append(f"  {asset['name']} ({size_str})")
            option_map.append(asset["name"])

    selected_options = pick(
        display_options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_display = [
        option[0] for option in cast(list[tuple[str, int]], selected_options)
    ]

    selected_assets = []
    for display_str in selected_display:
        stripped = display_str.strip()
        if stripped.startswith("---") or not stripped:
            continue
        for asset_name in option_map:
            if asset_name and stripped.startswith(f"  {asset_name}"):
                selected_assets.append(asset_name)
                break

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
