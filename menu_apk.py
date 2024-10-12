#!/data/data/com.termux/files/usr/bin/python

import os
import sys
from pick import pick
import requests
from dotenv import load_dotenv

# Load existing .env or create a new one
env_file = ".env"
if not os.path.exists(env_file):
    open(env_file, 'a').close()

load_dotenv(env_file)

# Function to fetch the latest APK release assets
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

# Function to present a menu to the user to select assets
def select_assets(assets, existing_selection):
    title = 'Select the APK files you want to download (press SPACE to select, ENTER to confirm):'
    options = assets
    # Pre-select existing selections
    pre_selected_indices = [i for i, asset in enumerate(assets) if asset in existing_selection]
    selected_options = pick(options, title, multiselect=True, min_selection_count=1, indicator='*', default_index=pre_selected_indices)
    selected_assets = [option[0] for option in selected_options]
    return selected_assets

def main():
    try:
        assets = fetch_apk_assets()
        existing_selection = os.getenv("SELECTED_APK_ASSETS", "").split()
        selected_assets = select_assets(assets, existing_selection)
        # Save the selected assets to .env
        selected_assets_str = ' '.join(selected_assets)
        # Remove existing SELECTED_APK_ASSETS line from .env
        with open(env_file, 'r') as f:
            lines = f.readlines()
        with open(env_file, 'w') as f:
            for line in lines:
                if not line.startswith('SELECTED_APK_ASSETS='):
                    f.write(line)
            f.write(f'SELECTED_APK_ASSETS="{selected_assets_str}"\n')
        print("Selected APK assets saved to .env")
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
