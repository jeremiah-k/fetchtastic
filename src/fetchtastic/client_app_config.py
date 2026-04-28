"""
Configuration normalization for Meshtastic client app assets.

Client app assets include Android APKs and Desktop installers from the same
upstream Meshtastic-Android release feed. The primary config keys are:

- SAVE_CLIENT_APPS
- SELECTED_APP_ASSETS
- APP_VERSIONS_TO_KEEP
- CHECK_APP_PRERELEASES
"""

from __future__ import annotations

from typing import Any, Iterable

from fetchtastic.client_release_discovery import (
    is_android_asset_name,
    is_desktop_asset_name,
)
from fetchtastic.constants import (
    DEFAULT_APP_VERSIONS_TO_KEEP,
    DEFAULT_CHECK_APP_PRERELEASES,
)
from fetchtastic.utils import coerce_bool, expand_apk_selected_patterns


def _as_list(value: Any) -> list[str]:
    """Return string list values from a config value."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: Iterable[str]) -> list[str]:

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _coerce_keep_count(value: Any, default: int = DEFAULT_APP_VERSIONS_TO_KEEP) -> int:
    """Coerce a keep-count config value to a non-negative integer."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return int(default)


def _classify_selected_assets(
    selected_assets: list[str],
) -> tuple[list[str], list[str], bool]:
    """
    Return legacy APK/Desktop selections plus whether any primary asset is ambiguous.

    Primary selection remains SELECTED_APP_ASSETS. When present, it is
    authoritative and legacy lists are rebuilt from it. Ambiguous entries are
    kept only in SELECTED_APP_ASSETS and enable both legacy save flags so old
    callers do not accidentally disable client app downloads for broad patterns.
    """
    apk_assets = []
    desktop_assets = []
    ambiguous = False

    for item in selected_assets:
        if is_android_asset_name(item):
            apk_assets.append(item)
        elif is_desktop_asset_name(item):
            desktop_assets.append(item)
        else:
            ambiguous = True

    return expand_apk_selected_patterns(apk_assets), _dedupe(desktop_assets), ambiguous


def get_selected_app_assets(config: dict[str, Any]) -> list[str]:
    """
    Return normalized selected client app asset patterns.

    SELECTED_APP_ASSETS is authoritative when present. Otherwise, legacy APK and
    Desktop asset selections are unioned.
    """
    if "SELECTED_APP_ASSETS" in config:
        return _dedupe(_as_list(config.get("SELECTED_APP_ASSETS")))

    apk_assets = expand_apk_selected_patterns(
        _as_list(config.get("SELECTED_APK_ASSETS"))
    )
    desktop_assets = _as_list(
        config.get("SELECTED_DESKTOP_ASSETS", config.get("SELECTED_DESKTOP_PLATFORMS"))
    )
    return _dedupe([*apk_assets, *desktop_assets])


def normalize_client_app_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Populate primary client app config keys from legacy Android/Desktop keys.

    Existing primary keys remain authoritative. Legacy keys are left in place so
    older code paths and existing user config remain readable.
    """
    if "SAVE_CLIENT_APPS" not in config:
        config["SAVE_CLIENT_APPS"] = coerce_bool(
            config.get("SAVE_APKS", False), default=False
        ) or coerce_bool(config.get("SAVE_DESKTOP_APP", False), default=False)

    config["SELECTED_APP_ASSETS"] = get_selected_app_assets(config)

    if "APP_VERSIONS_TO_KEEP" not in config:
        android_keep = _coerce_keep_count(
            config.get("ANDROID_VERSIONS_TO_KEEP"), DEFAULT_APP_VERSIONS_TO_KEEP
        )
        desktop_keep = _coerce_keep_count(
            config.get("DESKTOP_VERSIONS_TO_KEEP"), DEFAULT_APP_VERSIONS_TO_KEEP
        )
        config["APP_VERSIONS_TO_KEEP"] = max(android_keep, desktop_keep)
    else:
        config["APP_VERSIONS_TO_KEEP"] = _coerce_keep_count(
            config.get("APP_VERSIONS_TO_KEEP"), DEFAULT_APP_VERSIONS_TO_KEEP
        )

    app_prereleases_explicit = "CHECK_APP_PRERELEASES" in config
    apk_key = (
        "CHECK_APK_PRERELEASES"
        if "CHECK_APK_PRERELEASES" in config
        else (
            "CHECK_ANDROID_PRERELEASES"
            if "CHECK_ANDROID_PRERELEASES" in config
            else None
        )
    )
    desktop_key = (
        "CHECK_DESKTOP_PRERELEASES" if "CHECK_DESKTOP_PRERELEASES" in config else None
    )
    if not app_prereleases_explicit:
        legacy_default = coerce_bool(
            config.get("CHECK_PRERELEASES"),
            default=DEFAULT_CHECK_APP_PRERELEASES,
        )
        if apk_key is None and desktop_key is None:
            apk_default = legacy_default
            desktop_default = legacy_default
        else:
            apk_default = False
            desktop_default = False
        apk_check = coerce_bool(
            config.get(apk_key) if apk_key is not None else apk_default,
            default=apk_default,
        )
        desktop_check = coerce_bool(
            config.get(desktop_key) if desktop_key is not None else desktop_default,
            default=desktop_default,
        )
        config["CHECK_APP_PRERELEASES"] = apk_check or desktop_check
    else:
        config["CHECK_APP_PRERELEASES"] = coerce_bool(
            config.get("CHECK_APP_PRERELEASES"),
            default=DEFAULT_CHECK_APP_PRERELEASES,
        )
        apk_check = coerce_bool(
            (
                config.get(apk_key)
                if apk_key is not None
                else config["CHECK_APP_PRERELEASES"]
            ),
            default=config["CHECK_APP_PRERELEASES"],
        )
        desktop_check = coerce_bool(
            (
                config.get(desktop_key)
                if desktop_key is not None
                else config["CHECK_APP_PRERELEASES"]
            ),
            default=config["CHECK_APP_PRERELEASES"],
        )

    # Keep legacy keys readable for compatibility without guessing from substrings.
    apk_assets, desktop_assets, has_ambiguous_assets = _classify_selected_assets(
        config["SELECTED_APP_ASSETS"]
    )
    config["SELECTED_APK_ASSETS"] = apk_assets
    config["SELECTED_DESKTOP_ASSETS"] = desktop_assets
    client_apps_enabled = coerce_bool(config.get("SAVE_CLIENT_APPS", False))
    if config["SELECTED_APP_ASSETS"]:
        config["SAVE_APKS"] = client_apps_enabled and (
            bool(config["SELECTED_APK_ASSETS"]) or has_ambiguous_assets
        )
        config["SAVE_DESKTOP_APP"] = client_apps_enabled and (
            bool(config["SELECTED_DESKTOP_ASSETS"]) or has_ambiguous_assets
        )
    else:
        config["SAVE_APKS"] = False
        config["SAVE_DESKTOP_APP"] = False
    config.pop("SELECTED_DESKTOP_PLATFORMS", None)
    config["ANDROID_VERSIONS_TO_KEEP"] = config["APP_VERSIONS_TO_KEEP"]
    config["DESKTOP_VERSIONS_TO_KEEP"] = config["APP_VERSIONS_TO_KEEP"]
    config["CHECK_APK_PRERELEASES"] = apk_check
    config["CHECK_DESKTOP_PRERELEASES"] = desktop_check
    return config


def client_app_downloads_enabled(config: dict[str, Any]) -> bool:
    """Return whether client app downloads are enabled without mutating config."""
    normalized = normalize_client_app_config(dict(config))
    return coerce_bool(normalized.get("SAVE_CLIENT_APPS"), default=False)
