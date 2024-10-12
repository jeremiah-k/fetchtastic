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

# Function to present a menu to the user to select asset patterns
def select_asset_patterns(assets):
    title = 'Select the types of APK files you want to download (press SPACE to select, ENTER to confirm):'
    # Extract unique patterns from asset names
    patterns = set()
    for name in assets:
        if 'fdroid' in name.lower():
            patterns.add('fdroid')
        if 'google' in name.lower():
            patterns.add('google')
    options = list(patterns)
    selected_options = pick(options, title, multiselect=True, min_selection_count=1, indicator='*')
    selected_patterns = [option[0] for option in selected_options]
    return selected_patterns

def main():
    try:
        assets = fetch_apk_assets()
        selected_patterns = select_asset_patterns(assets)
        # Save the selected patterns to .env
        selected_patterns_str = ' '.join(selected_patterns)
        # Remove existing SELECTED_APK_PATTERNS line from .env
        with open(env_file, 'r') as f:
            lines = f.readlines()
        with open(env_file, 'w') as f:
            for line in lines:
                if not line.startswith('SELECTED_APK_PATTERNS='):
                    f.write(line)
            f.write(f'SELECTED_APK_PATTERNS="{selected_patterns_str}"\n')
        print("Selected APK patterns saved to .env")
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
