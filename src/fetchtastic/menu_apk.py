# src/fetchtastic/menu_apk.py

import json
from typing import cast

import requests
from pick import pick

from fetchtastic.constants import (
    APK_EXTENSION,
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    extract_base_name,
    make_github_api_request,
)


def fetch_apk_assets() -> list[str]:
    """
    Retrieve APK filenames from the latest Meshtastic Android release on GitHub.

    Returns:
        list[str]: Alphabetically sorted APK asset filenames from the latest release. Empty list if no releases or matching assets are found.
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
    asset_names = sorted(
        [
            asset_name
            for asset in assets
            if isinstance(asset, dict)
            and (asset_name := asset.get("name"))
            and asset_name.lower().endswith(APK_EXTENSION)
        ]
    )

    return asset_names


def select_assets(assets: list[str]) -> dict[str, list[str]] | None:
    """
    Present an interactive multi-select prompt of APK filenames and return selected base-name patterns.

    Displays the provided APK filenames for multi-selection; for each chosen filename this function computes a base-name pattern using `extract_base_name` and returns a dictionary `{"selected_assets": [base_pattern, ...]`. If no assets are selected, the function prints a short message and returns `None`.

    Parameters:
        assets (list[str]): APK asset filenames to present for selection.

    Returns:
        dict[str, list[str]] | None: `{"selected_assets": [base_pattern, ...]}` when one or more assets are selected, `None` if no selection was made.
    """
    title = """Select the APK files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases."""
    options = assets
    selected_options = pick(
        options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_assets = [
        option[0] for option in cast(list[tuple[str, int]], selected_options)
    ]
    if not selected_assets:
        print("No APK files selected. APKs will not be downloaded.")
        return None

    # Extract base patterns from selected filenames
    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu() -> dict[str, list[str]] | None:
    """
    Show an interactive APK selection menu and return the chosen base-name patterns.

    Presents a multi-select prompt for available APK filenames and returns a dictionary
    with selected base-name patterns when one or more items are chosen.

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
        # Handle JSON parsing and data validation errors
        logger.exception("APK menu failed due to data error")
        return None
    except (requests.RequestException, OSError):
        # Handle network and I/O errors
        logger.exception("APK menu failed due to network/I/O error")
        return None
    except (TypeError, KeyError, AttributeError):
        # Handle unexpected data structure errors
        logger.exception("APK menu failed due to data structure error")
        return None
    except Exception:  # noqa: BLE001
        # Catch-all for unexpected errors (backward compatibility)
        logger.exception("APK menu failed due to unexpected error")
        return None
