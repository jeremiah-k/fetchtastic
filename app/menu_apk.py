# app/menu_apk.py

import requests

def run_menu():
    # Fetch the latest release to get asset names
    releases_url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    response = requests.get(releases_url)
    response.raise_for_status()
    releases = response.json()
    if not releases:
        print("No releases found for Meshtastic Android APKs.")
        return None

    latest_release = releases[0]
    assets = latest_release.get('assets', [])
    if not assets:
        print("No assets found in the latest release.")
        return None

    # Extract unique asset names
    asset_names = set(asset['name'] for asset in assets)

    # Display menu
    print("Select the APK assets you want to download:")
    selected_assets = []
    for idx, asset_name in enumerate(asset_names, 1):
        print(f"{idx}. {asset_name}")
    print("Enter the numbers separated by commas (e.g., 1,3,4):")
    choices = input("Your choices: ").split(',')

    for choice in choices:
        choice = choice.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(asset_names):
                selected_assets.append(list(asset_names)[idx])

    if not selected_assets:
        print("No valid selections made.")
        return None

    return {'selected_assets': selected_assets}
