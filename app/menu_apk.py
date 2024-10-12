# fetchtastic/menu_apk.py

import re
import requests
from pick import pick

def fetch_apk_assets():
    apk_releases_url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    response = requests.get(apk_releases_url)
    response.raise_for_status()
    releases = response.json()
    # Get the latest release
    latest_release = releases[0]
    assets = latest_release['assets']
    asset_names = [asset['name'] for asset in assets if asset['name'].endswith('.apk')]
    return asset_names

def select_assets(assets):
    title = '''Select the APK files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases.'''
    options = assets
    selected_options = pick(options, title, multiselect=True, min_selection_count=0, indicator='*')
    selected_assets = [option[0] for option in selected_options]
    if not selected_assets:
        print("No APK files selected. APKs will not be downloaded.")
        return None
    return selected_assets

def run_menu():
    try:
        assets = fetch_apk_assets()
        selected_assets = select_assets(assets)
        if selected_assets is None:
            return None
        return {
            'selected_assets': selected_assets
        }
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
