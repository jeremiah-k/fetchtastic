# src/fetchtastic/menu_apk.py

import time

import requests
from pick import pick

from fetchtastic.constants import (
    API_CALL_DELAY,
    APK_EXTENSION,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import extract_base_name


def fetch_apk_assets():
    """
    Fetch APK asset filenames from the latest Meshtastic Android release on GitHub.

    Performs an HTTP GET to MESHTASTIC_ANDROID_RELEASES_URL with timeout GITHUB_API_TIMEOUT and raises an HTTPError on non-OK responses. After the request it waits API_CALL_DELAY seconds. Expects the API JSON to be a non-empty list of releases and treats the first element as the latest release; if the response is not a list or is empty, returns an empty list. Extracts asset names from the latest release whose names end with APK_EXTENSION (case-insensitive), defaulting missing names to the empty string, sorts them alphabetically, and returns the sorted list of names.

    Returns:
        list[str]: Alphabetically sorted APK asset filenames from the latest release. May be empty if no releases or matching assets are found.
    """
    response = requests.get(MESHTASTIC_ANDROID_RELEASES_URL, timeout=GITHUB_API_TIMEOUT)
    response.raise_for_status()

    # Small delay to be respectful to GitHub API
    time.sleep(API_CALL_DELAY)

    releases = response.json()
    if not isinstance(releases, list) or not releases:
        logger.warning("No Android releases found from GitHub API.")
        return []
    latest_release = releases[0] or {}
    assets = latest_release.get("assets", []) or []
    asset_names = sorted(
        [
            (asset.get("name") or "")
            for asset in assets
            if str(asset.get("name") or "").lower().endswith(APK_EXTENSION.lower())
        ]
    )  # Sorted alphabetically
    return asset_names


def select_assets(assets):
    """
    Present an interactive multi-select prompt for APK asset filenames and return selected base-name patterns.

    Displays an interactive list of the provided APK asset filenames and allows the user to select zero or more entries. For each selected filename this function computes a base-name pattern using extract_base_name and returns a dict {"selected_assets": [...base patterns...]}. If the user selects no assets, prints a short message and returns None.

    Parameters:
        assets (list[str]): List of APK asset filenames to present for selection.

    Returns:
        dict or None: A dictionary with key "selected_assets" mapping to a list of base-name patterns when one or more assets are chosen; otherwise None.
    """
    title = """Select the APK files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases."""
    options = assets
    selected_options = pick(
        options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_assets = [option[0] for option in selected_options]
    if not selected_assets:
        print("No APK files selected. APKs will not be downloaded.")
        return None

    # Extract base patterns from selected filenames
    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu():
    """
    Orchestrate fetching APK asset names and prompting the user to select one or more; return the selection.

    Calls fetch_apk_assets() to retrieve APK asset names from the latest Meshtastic Android release, then calls select_assets(assets) to present a multi-select prompt. Returns the dictionary produced by select_assets (e.g., {"selected_assets": [...base name patterns...]}) when the user makes a selection. Returns None if the user selects nothing, aborts, or if an error occurs (errors are caught and logged).
    """
    try:
        assets = fetch_apk_assets()
        selected_result = select_assets(assets)
        if selected_result is None:
            return None
        return selected_result
    except Exception:
        logger.exception("APK menu failed")
        return None
