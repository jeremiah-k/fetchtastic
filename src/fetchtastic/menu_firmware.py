# src/fetchtastic/menu_firmware.py

import re

import requests
from pick import pick


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
    Return a filename with version and optional commit-hash segments removed.
    
    Removes a version pattern (optionally prefixed with `v`) and any trailing short commit/hash portion from the input filename.
    The matched substring includes a preceding '-' or '_' so the separator is removed along with the version, preserving other parts
    such as architecture or classifier.
    
    Parameters:
        filename (str): Original asset filename.
    
    Returns:
        str: Filename with the version/hash segment removed.
    
    Examples:
        'meshtasticd_2.5.13.1a06f88_amd64.deb' -> 'meshtasticd_amd64.deb'
        'firmware-rak4631-2.5.13.1a06f88-ota.zip' -> 'firmware-rak4631-ota.zip'
    """
    # Regular expression to match version numbers and commit hashes
    # Matches patterns like '-2.5.13.1a06f88' or '_2.5.13.1a06f88'
    base_name = re.sub(r"[-_]v?\d+\.\d+\.\d+(?:\.[\da-f]+)?", "", filename)
    return base_name


def select_assets(assets):
    """
    Displays a menu for the user to select firmware assets to download.
    Returns a dictionary containing the selected base patterns.
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
