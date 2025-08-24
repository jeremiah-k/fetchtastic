# src/fetchtastic/menu_firmware.py

import time

import requests
from pick import pick

from fetchtastic.constants import (
    API_CALL_DELAY,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
)
from fetchtastic.utils import extract_base_name


def fetch_firmware_assets():
    """
    Return a sorted list of firmware asset filenames from the latest Meshtastic GitHub release.

    Makes an HTTP GET request to MESHTASTIC_FIRMWARE_RELEASES_URL (with timeout GITHUB_API_TIMEOUT),
    pauses for API_CALL_DELAY after the request, then parses the JSON and returns the asset names
    from the first release entry, sorted alphabetically.

    Returns:
        list[str]: Sorted asset filenames present in the latest release.

    Raises:
        requests.HTTPError: If the HTTP request returns a non-2xx status (raised by response.raise_for_status()).
    """
    response = requests.get(
        MESHTASTIC_FIRMWARE_RELEASES_URL, timeout=GITHUB_API_TIMEOUT
    )
    response.raise_for_status()

    # Small delay to be respectful to GitHub API
    time.sleep(API_CALL_DELAY)

    releases = response.json()
    # Get the latest release
    latest_release = releases[0]
    assets = latest_release["assets"]
    # Sorted alphabetically
    asset_names = sorted([asset["name"] for asset in assets])
    return asset_names


def select_assets(assets):
    """
    Present an interactive multiselect menu of firmware asset filenames and return their base-name patterns.

    Displays a prompt (SPACE to select, ENTER to confirm) built from the provided list of asset filenames, lets the user choose zero or more entries, and converts each selected filename into a base pattern via extract_base_name.

    Parameters:
        assets (list[str]): List of firmware asset filenames (as returned by the releases API).

    Returns:
        dict[str, list[str]]: {"selected_assets": [base_pattern, ...]} for the chosen files.
        None: If the user makes no selection.
    """
    title = """Select the firmware files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases."""
    options = assets
    selected_options = pick(
        options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_assets = [option[0] for option in selected_options]
    if not selected_assets:
        print("No firmware files selected. Firmware will not be downloaded.")
        return None

    # Extract base patterns from selected filenames
    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu():
    """
    Runs the firmware selection menu and returns the selected patterns.
    """
    try:
        assets = fetch_firmware_assets()
        selection = select_assets(assets)
        if selection is None:
            return None
        return selection
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
