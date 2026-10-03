"""Legacy APK selector backed by shared client app discovery."""

from collections.abc import Sequence
from typing import Any

import requests

from .client_release_discovery import is_android_asset_name
from .log_utils import logger
from .menu_app import fetch_app_assets
from .menu_app import select_assets as _select_client_app_assets


def _asset_name(asset: str | dict[str, Any]) -> str | None:
    if isinstance(asset, str):
        return asset or None
    if isinstance(asset, dict):
        name = asset.get("name")
        return name if isinstance(name, str) and name else None
    return None


def fetch_apk_assets() -> list[dict[str, Any]]:
    """Return APK assets only, retaining the historical empty-list failure contract."""
    try:
        assets = fetch_app_assets()
    except (
        requests.RequestException,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logger.warning("Unable to fetch APK assets: %s", exc)
        return []
    return [
        asset
        for asset in assets
        if isinstance(asset.get("name"), str) and is_android_asset_name(asset["name"])
    ]


def select_assets(
    assets: Sequence[str | dict[str, Any]],
) -> dict[str, list[str]] | None:
    """Keep legacy APK callers scoped while using the shared selector."""
    apk_assets = [
        asset
        for asset in assets
        if (name := _asset_name(asset)) is not None and is_android_asset_name(name)
    ]
    if not apk_assets:
        print("No valid APK files found. APKs will not be downloaded.")
        return None
    return _select_client_app_assets(apk_assets)


def run_menu() -> dict[str, list[str]] | None:
    """Run the legacy APK-only selector over the shared release feed."""
    try:
        assets = fetch_apk_assets()
        if not assets:
            print("No APK files found. APKs will not be downloaded.")
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
        logger.exception("APK menu failed")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("APK menu failed due to unexpected error")
        return None


__all__ = ["fetch_apk_assets", "run_menu", "select_assets"]
