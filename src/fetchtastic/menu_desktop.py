"""Legacy desktop selector backed by shared client app discovery."""

from collections.abc import Sequence
from typing import Any

import requests

from .client_release_discovery import is_desktop_asset_name
from .log_utils import logger
from .menu_app import fetch_app_assets
from .menu_app import select_assets as _select_client_app_assets
from .utils import extract_base_name

PLATFORM_GROUPS = {
    "macOS": [".dmg"],
    "Windows": [".msi", ".exe"],
    "Linux": [".deb", ".rpm", ".appimage"],
}


def _asset_name(asset: str | dict[str, Any]) -> str | None:
    if isinstance(asset, str):
        return asset or None
    if isinstance(asset, dict):
        name = asset.get("name")
        return name if isinstance(name, str) and name else None
    return None


def extract_wildcard_pattern(filename: str) -> str:
    """Retain the historical Desktop pattern-normalization helper."""
    return extract_base_name(filename).lower()


def fetch_desktop_assets() -> list[str] | None:
    """Return desktop names only, retaining the historical None-on-error contract."""
    try:
        assets = fetch_app_assets()
    except (
        requests.RequestException,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logger.warning("Unable to fetch desktop assets: %s", exc)
        return None
    return [
        asset["name"]
        for asset in assets
        if isinstance(asset.get("name"), str) and is_desktop_asset_name(asset["name"])
    ]


def select_assets(
    assets: Sequence[str | dict[str, Any]],
) -> dict[str, list[str]] | None:
    """Keep legacy Desktop callers scoped while using the shared selector."""
    desktop_assets = [
        asset
        for asset in assets
        if (name := _asset_name(asset)) is not None and is_desktop_asset_name(name)
    ]
    if not desktop_assets:
        print("No desktop files found. Desktop clients will not be downloaded.")
        return None
    return _select_client_app_assets(desktop_assets)


def run_menu() -> dict[str, list[str]] | None:
    """Run the legacy Desktop-only selector over the shared release feed."""
    try:
        assets = fetch_desktop_assets()
        if assets is None:
            print(
                "Failed to fetch desktop files. Desktop clients will not be downloaded."
            )
            return None
        if not assets:
            print("No desktop files found. Desktop clients will not be downloaded.")
            return None
        return select_assets(assets)
    except (
        ValueError,
        requests.RequestException,
        OSError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        logger.exception("Desktop menu failed")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Desktop menu failed due to unexpected error")
        return None


__all__ = [
    "PLATFORM_GROUPS",
    "extract_wildcard_pattern",
    "fetch_desktop_assets",
    "run_menu",
    "select_assets",
]
