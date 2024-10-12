#!/data/data/com.termux/files/usr/bin/python

import os
import sys
import re
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
def select_assets(assets):
    title = 'Select the APK files you want to download (press SPACE to select, ENTER to confirm):'
    options = assets
    selected_options = pick(options, title, multiselect=True, min_selection_count=1, indicator='*')
    selected_assets = [option[0] for option in selected_options]
    return selected_assets

def extract_patterns(selected_assets):
    patterns = []
    for asset in selected_assets:
        # Remove version numbers and extensions to create patterns
        pattern = re.sub(r'[-_.]?v?\d+.*', '', asset)
        patterns.append(pattern)
    return patterns

def main():
    try:
        assets = fetch_apk_assets()
        selected_assets = select_assets(assets)
        # Save the selected assets to .env
        selected_assets_str = ' '.join(selected_assets)
        # Remove existing SELECTED_APK_ASSETS and APK_PATTERNS lines from .env
        with open(env_file, 'r') as f:
            lines = f.readlines()
        with open(env_file, 'w') as f:
            for line in lines:
                if not line.startswith('SELECTED_APK_ASSETS=') and not line.startswith('APK_PATTERNS='):
                    f.write(line)
            f.write(f'SELECTED_APK_ASSETS="{selected_assets_str}"\n')
            # Generate patterns
            patterns = extract_patterns(selected_assets)
            patterns_str = ' '.join(patterns)
            f.write(f'APK_PATTERNS="{patterns_str}"\n')
        print("Selected APK assets saved to .env")
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
