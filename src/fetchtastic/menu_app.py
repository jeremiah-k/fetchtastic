# src/fetchtastic/menu_app.py

from collections.abc import Sequence
from typing import Any

import requests
from pick import pick

from fetchtastic.client_release_discovery import (
    extract_matching_asset_dicts,
    is_client_app_prerelease_tag,
    select_best_release_with_assets,
)
from fetchtastic.constants import (
    APK_EXTENSION,
    DESKTOP_EXTENSIONS,
    MESHTASTIC_CLIENT_APP_RELEASES_URL,
)
from fetchtastic.download.client_app import is_snapshot_tag
from fetchtastic.log_utils import logger
from fetchtastic.utils import extract_base_name, make_github_api_request


def _asset_name(asset: str | dict[str, Any]) -> str | None:
    if isinstance(asset, str):
        return asset or None
    if isinstance(asset, dict):
        name = asset.get("name")
        return name if isinstance(name, str) and name else None
    return None


def _normalize_assets(
    assets: Sequence[str | dict[str, Any]],
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for asset in assets:
        name = _asset_name(asset)
        if name:
            platform = get_asset_platform_label(name) or "Client app"
            entries.append((f"{platform}: {name}", name))
    return entries


def get_asset_platform_label(asset_name: str) -> str | None:
    """Return the target platform to help choose an installer."""
    lower = asset_name.lower()
    if lower.endswith(".apk"):
        return "Android"
    if lower.endswith(".dmg"):
        return "macOS"
    if lower.endswith((".exe", ".msi")):
        return "Windows"
    if lower.endswith((".deb", ".rpm", ".appimage")):
        return "Linux"
    return None


def select_assets(
    assets: Sequence[str | dict[str, Any]],
) -> dict[str, list[str]] | None:
    """Select client app patterns from one collection of installers."""
    entries = _normalize_assets(assets)
    if not entries:
        print("No client app assets found. Client app releases will not be downloaded.")
        return None

    display_options = [display for display, _name in entries]
    title = """Select the client app assets you want to download (press SPACE to select, ENTER to confirm):
Options include Android APKs and Desktop installers from the same upstream release feed."""
    selected_options = pick(
        display_options, title, multiselect=True, min_selection_count=0, indicator="*"
    )

    selected_names: list[str] = []
    for _display, index in selected_options:
        if 0 <= index < len(entries):
            selected_names.append(entries[index][1])

    if not selected_names:
        print(
            "No client app assets selected. Client app releases will not be downloaded."
        )
        return None

    patterns = []
    for name in selected_names:
        pattern = extract_base_name(name)
        if not name.lower().endswith(APK_EXTENSION):
            pattern = pattern.lower()
        patterns.append(pattern)
    return {"selected_assets": patterns}


def fetch_app_assets() -> list[dict[str, Any]]:
    """Discover installer formats from a single release feed request.

    Prefer a stable release for each format, falling back to a prerelease when
    necessary. A release missing one installer format does not hide that format
    from setup if it is available in another recent release.
    """
    response = make_github_api_request(MESHTASTIC_CLIENT_APP_RELEASES_URL)
    releases = response.json()
    if not isinstance(releases, list):
        raise ValueError("Expected a list of client app releases")
    # Rolling snapshots are an opt-in channel, not setup's source of selections.
    releases = [
        r
        for r in releases
        if isinstance(r, dict)
        and isinstance(r.get("tag_name"), str)
        and not is_snapshot_tag(r["tag_name"])
    ]
    assets: list[dict[str, Any]] = []
    for extension in (APK_EXTENSION, *DESKTOP_EXTENSIONS):

        def matches(name: str, extension: str = extension) -> bool:
            return name.lower().endswith(extension.lower())

        release = select_best_release_with_assets(
            releases,
            asset_name_matcher=matches,
            tag_prerelease_matcher=is_client_app_prerelease_tag,
        )
        if release:
            assets.extend(
                extract_matching_asset_dicts(release, asset_name_matcher=matches)
            )
    return sorted(assets, key=lambda asset: asset["name"].lower())


def run_menu() -> dict[str, list[str]] | None:
    """Show the shared installer selector."""
    try:
        return select_assets(fetch_app_assets())
    except (
        requests.RequestException,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logger.warning("Unable to fetch client app assets: %s", exc)
        print(f"Warning: unable to fetch client app assets: {exc}")
        return None
