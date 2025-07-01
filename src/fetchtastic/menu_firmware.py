# src/fetchtastic/menu_firmware.py

import re

import requests


def fetch_firmware_assets():
    """
    Fetches the list of firmware assets from the latest release on GitHub.
    """
    firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"
    response = requests.get(firmware_releases_url, timeout=10)
    response.raise_for_status()
    releases = response.json()
    # Get the latest release
    latest_release = releases[0]
    assets = latest_release["assets"]
    # Sorted alphabetically
    asset_names = sorted([asset["name"] for asset in assets])
    return asset_names


def extract_base_name(filename):
    """
    Removes version numbers and commit hashes from the filename to get a base pattern.
    Preserves architecture identifiers and other important parts of the filename.

    Example:
    - 'meshtasticd_2.5.13.1a06f88_amd64.deb' -> 'meshtasticd__amd64.deb'
    - 'firmware-rak4631-2.5.13.1a06f88-ota.zip' -> 'firmware-rak4631--ota.zip'
    - 'meshtasticd-2.7.0.16192.local705515a-src.zip' -> 'meshtasticd--src.zip'
    """
    # Regular expression to match version numbers and commit hashes
    # This handles complex patterns like '-2.7.0.16192.local705515a' and '-2.5.13.1a06f88'
    # Also handles meshtasticd format without dots: 'meshtasticd-2.7.0.local705515a-src.zip'
    base_name = re.sub(
        r"([_-])\d+\.\d+\.\d+(?:\.\d+)?(?:\.local[\da-f]+|\.[\da-f]+|local[\da-f]+)?",
        r"\1",
        filename,
    )

    # Clean up remaining version-like suffixes and commit hashes (like '705515a', 'a06f88', etc.)
    base_name = re.sub(r"([_-])[\da-f]{6,}(?=\.|$|[_-])", r"\1", base_name)
    return base_name


def select_assets(assets, preselected_patterns=None):
    """
    Displays a menu for the user to select firmware assets to download.
    Returns a dictionary containing the selected base patterns.

    Args:
        assets: List of available firmware assets
        preselected_patterns: List of previously selected base patterns for preselection
    """
    from fetchtastic.ui_utils import (
        multi_select_with_preselection,
        show_preselection_info,
    )

    # Handle preselection by matching patterns to current assets
    preselected_assets = []
    if preselected_patterns:
        for asset in assets:
            asset_pattern = extract_base_name(asset)
            if asset_pattern in preselected_patterns:
                preselected_assets.append(asset)

        if preselected_assets:
            show_preselection_info(preselected_assets)

    message = """Select the firmware files you want to download:
Note: These are files from the latest release. Version numbers may change in other releases."""

    selected_assets = multi_select_with_preselection(
        message=message, choices=assets, preselected=preselected_assets, min_selection=0
    )

    if not selected_assets:
        print("No firmware files selected. Firmware will not be downloaded.")
        return None

    # Extract base patterns from selected filenames
    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"SELECTED_FIRMWARE_ASSETS": base_patterns}


def run_menu(preselected_patterns=None):
    """
    Runs the firmware selection menu and returns the selected patterns.

    Args:
        preselected_patterns: List of previously selected base patterns for preselection
    """
    try:
        assets = fetch_firmware_assets()
        selection = select_assets(assets, preselected_patterns)
        if selection is None:
            return None
        return selection
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
