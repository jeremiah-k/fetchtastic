# src/fetchtastic/menu_app.py

from typing import Any, Dict, Sequence, Union

from pick import pick

from fetchtastic import menu_apk, menu_desktop
from fetchtastic.utils import extract_base_name


def _asset_name(asset: Union[str, Dict[str, Any]]) -> str | None:
    if isinstance(asset, str):
        return asset or None
    if isinstance(asset, dict):
        name = asset.get("name")
        return name if isinstance(name, str) and name else None
    return None


def _normalize_assets(
    apk_assets: Sequence[Union[str, Dict[str, Any]]],
    desktop_assets: Sequence[str],
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for asset in apk_assets:
        name = _asset_name(asset)
        if name:
            entries.append((f"Android APK: {name}", name))
    for name in desktop_assets:
        if name:
            platform = menu_desktop._get_platform_label(name) or "Desktop"
            entries.append((f"{platform}: {name}", name))
    return entries


def select_assets(
    apk_assets: Sequence[Union[str, Dict[str, Any]]],
    desktop_assets: Sequence[str],
) -> dict[str, list[str]] | None:
    """Select client app asset patterns from Android and Desktop artifacts."""
    entries = _normalize_assets(apk_assets, desktop_assets)
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
        if menu_desktop._get_platform_label(name):
            pattern = pattern.lower()
        patterns.append(pattern)
    return {"selected_assets": patterns}


def run_menu() -> dict[str, list[str]] | None:
    """Show one asset selector for Android APKs and Desktop installers."""
    apk_assets = menu_apk.fetch_apk_assets()
    desktop_assets = menu_desktop.fetch_desktop_assets()
    if desktop_assets is None:
        desktop_assets = []
    return select_assets(apk_assets or [], desktop_assets)
