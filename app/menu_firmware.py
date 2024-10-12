# app/menu_firmware.py

import requests
from pick import pick

def fetch_firmware_assets():
    firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"
    response = requests.get(firmware_releases_url)
    response.raise_for_status()
    releases = response.json()
    # Get the latest release
    latest_release = releases[0]
    assets = latest_release['assets']
    asset_names = [asset['name'] for asset in assets]
    return asset_names

def select_assets(assets):
    title = '''Select the firmware files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases.'''
    options = assets
    selected_options = pick(options, title, multiselect=True, min_selection_count=0, indicator='*')
    selected_assets = [option[0] for option in selected_options]
    if not selected_assets:
        print("No firmware files selected. Firmware will not be downloaded.")
        return None
    return selected_assets

def run_menu():
    try:
        assets = fetch_firmware_assets()
        selected_assets = select_assets(assets)
        if selected_assets is None:
            return None
        return {
            'selected_assets': selected_assets
        }
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
