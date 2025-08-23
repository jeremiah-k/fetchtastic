# src/fetchtastic/menu_apk.py

import re
import time

import requests
from pick import pick

from fetchtastic.constants import (
    API_CALL_DELAY,
    APK_EXTENSION,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger


def fetch_apk_assets():
    response = requests.get(MESHTASTIC_ANDROID_RELEASES_URL, timeout=GITHUB_API_TIMEOUT)
    response.raise_for_status()

    # Small delay to be respectful to GitHub API
    time.sleep(API_CALL_DELAY)

    releases = response.json()
    if not isinstance(releases, list) or not releases:
        logger.warning("No Android releases found from GitHub API.")
        return []
    latest_release = releases[0] or {}
    assets = latest_release.get("assets", []) or []
    asset_names = sorted(
        [
            (asset.get("name") or "")
            for asset in assets
            if str(asset.get("name") or "").lower().endswith(APK_EXTENSION.lower())
        ]
    )  # Sorted alphabetically
    return asset_names


def extract_base_name(filename):
    # Remove version numbers and extensions from filename to get base pattern
    # Example: 'fdroidRelease-2.5.9.apk' -> 'fdroidRelease-.apk'
    """
    Return a filename with a trailing semantic-version segment removed.

    Removes a single version segment matching the pattern `-X.Y.Z` or `_X.Y.Z` (digits separated by dots) from the input filename and returns the resulting string. The file extension and other parts of the name are preserved.

    Parameters:
        filename (str): The original filename (e.g., "fdroidRelease-2.5.9.apk").

    Returns:
        str: The filename with the `[-_]X.Y.Z` version segment removed (e.g., "fdroidRelease.apk").
    """
    # Remove '-/_' + optional 'v' + semver + optional suffix segments (e.g., '-beta.1', '.c1f4f79')
    # But preserve the file extension
    base_name = re.sub(r"[-_]v?\d+\.\d+\.\d+(?:[._-][0-9A-Za-z]+)*(?=\.)", "", filename)
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
    return {"selected_assets": base_patterns}


def run_menu():
    try:
        assets = fetch_apk_assets()
        selected_result = select_assets(assets)
        if selected_result is None:
            return None
        return selected_result
    except Exception:
        logger.exception("APK menu failed")
        return None
