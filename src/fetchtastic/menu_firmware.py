# src/fetchtastic/menu_firmware.py

import json
from typing import cast

import requests
from pick import pick

from fetchtastic.constants import MESHTASTIC_FIRMWARE_RELEASES_URL
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    extract_base_name,
    make_github_api_request,
)


def fetch_firmware_assets() -> list[str]:
    """
    Retrieve firmware asset filenames from the latest Meshtastic GitHub release.

    Parses the releases returned by the Meshtastic GitHub API and returns the asset filenames
    from the most recent release, sorted alphabetically. If the API response is not a non-empty
    list or the JSON cannot be decoded, an empty list is returned.

    Returns:
        list[str]: Sorted asset filenames from the latest release; empty list if no release data is available.
    """
    try:
        response = make_github_api_request(MESHTASTIC_FIRMWARE_RELEASES_URL)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch firmware assets from GitHub API: {e}")
        return []

    try:
        releases = response.json()
        if isinstance(releases, list):
            logger.debug(f"Fetched {len(releases)} firmware releases from GitHub API")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from GitHub API: {e}")
        return []
    if not isinstance(releases, list) or not releases:
        logger.warning("No firmware releases found from GitHub API.")
        return []
    latest_release = releases[0] or {}
    assets = latest_release.get("assets") or []
    if not isinstance(assets, list):
        logger.warning("Invalid assets data from GitHub API.")
        return []
    # Sorted alphabetically, tolerate missing names
    asset_names = sorted(
        [
            (asset.get("name") or "")
            for asset in assets
            if isinstance(asset, dict) and (asset.get("name") or "")
        ]
    )

    return asset_names


def select_assets(assets: list[str]) -> dict[str, list[str]] | None:
    """
    Show an interactive multiselect of firmware filenames and return the selected base-name patterns.

    Parameters:
        assets (list[str]): Firmware asset filenames (typically from the GitHub releases API).

    Returns:
        dict[str, list[str]]: Dictionary {"selected_assets": [base_pattern, ...]} containing base-name patterns for the chosen files.
        None: If the user selects no files.
    """
    title = """Select the firmware files you want to download (press SPACE to select, ENTER to confirm):
Note: These are files from the latest release. Version numbers may change in other releases."""
    options = assets
    selected_options = pick(
        options, title, multiselect=True, min_selection_count=0, indicator="*"
    )
    selected_assets = [
        option[0] for option in cast(list[tuple[str, int]], selected_options)
    ]
    if not selected_assets:
        print("No firmware files selected. Firmware will not be downloaded.")
        return None

    # Extract base patterns from selected filenames
    base_patterns = []
    for asset_name in selected_assets:
        pattern = extract_base_name(asset_name)
        base_patterns.append(pattern)
    return {"selected_assets": base_patterns}


def run_menu() -> dict[str, list[str]] | None:
    """
    Execute the firmware asset selection flow and produce base-name patterns for chosen assets.
    
    Returns:
        A dictionary with the key "selected_assets" whose value is a list of selected asset base-name patterns, or `None` if no assets were selected or an error occurred.
    """
    try:
        assets = fetch_firmware_assets()
        selection = select_assets(assets)
        if selection is None:
            return None
        return selection
    except (json.JSONDecodeError, ValueError):
        # Handle JSON parsing and data validation errors
        logger.exception("Firmware menu failed due to data error")
        return None
    except (requests.RequestException, OSError):
        # Handle network and I/O errors
        logger.exception("Firmware menu failed due to network/I/O error")
        return None
    except (TypeError, KeyError, AttributeError):
        # Handle unexpected data structure errors
        logger.exception("Firmware menu failed due to data structure error")
        return None
    except Exception:  # noqa: BLE001
        # Catch-all for unexpected errors (backward compatibility)
        logger.exception("Firmware menu failed due to unexpected error")
        return None