# app/menu_apk.py

import re

import requests
from pick import pick


def fetch_apk_assets():
    apk_releases_url = (
        "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    )
    response = requests.get(apk_releases_url, timeout=10)
    response.raise_for_status()
    releases = response.json()
    # Get the latest release
    latest_release = releases[0]
    assets = latest_release["assets"]
    asset_names = sorted(
        [asset["name"] for asset in assets if asset["name"].endswith(".apk")]
    )  # Sorted alphabetically
    return asset_names


def extract_base_name(filename):
    # Remove version numbers and extensions from filename to get base pattern
    # Example: 'fdroidRelease-2.5.9.apk' -> 'fdroidRelease-.apk'
    base_name = re.sub(r"([-_])\d[\d\.\w]*", r"\1", filename)
    return base_name


def select_assets(assets):
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
    return base_patterns


def run_menu():
    try:
        assets = fetch_apk_assets()
        selected_patterns = select_assets(assets)
        if selected_patterns is None:
            return None
        return {"selected_assets": selected_patterns}
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
