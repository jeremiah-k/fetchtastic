# app/menu_firmware.py

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
    Removes version numbers and commit hashes from the filename to get a base pattern.
    Preserves architecture identifiers and other important parts of the filename.

    Example:
    - 'meshtasticd_2.5.13.1a06f88_amd64.deb' -> 'meshtasticd__amd64.deb'
    - 'firmware-rak4631-2.5.13.1a06f88-ota.zip' -> 'firmware-rak4631--ota.zip'
    """
    # Regular expression to match version numbers and commit hashes
    # Matches patterns like '-2.5.13.1a06f88' or '_2.5.13.1a06f88'
    base_name = re.sub(r'([_-])\d+\.\d+\.\d+(?:\.[\da-f]+)?', r'\1', filename)
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
