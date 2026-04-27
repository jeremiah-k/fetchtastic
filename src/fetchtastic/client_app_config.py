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

from typing import Any, Dict, Iterable, List

from fetchtastic.client_release_discovery import (
    is_android_asset_name,
    is_desktop_asset_name,
)
from fetchtastic.constants import (
    DEFAULT_APP_VERSIONS_TO_KEEP,
    DEFAULT_CHECK_APP_PRERELEASES,
)
from fetchtastic.utils import coerce_bool, expand_apk_selected_patterns


def _as_list(value: Any) -> List[str]:
    """Return string list values from a config value."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: Iterable[str]) -> List[str]:
    """Preserve order while removing case-insensitive duplicates."""
    result: List[str] = []
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
    config: Dict[str, Any],
    selected_assets: List[str],
) -> tuple[List[str], List[str], bool]:
    """
    Return legacy APK/Desktop selections plus whether any primary asset is ambiguous.

    Primary selection remains SELECTED_APP_ASSETS. Legacy lists are preserved when
    already present; otherwise only concrete asset filenames are classified.
    """
    legacy_apk_present = bool(_as_list(config.get("SELECTED_APK_ASSETS")))
    legacy_desktop_present = bool(
        _as_list(
            config.get(
                "SELECTED_DESKTOP_ASSETS", config.get("SELECTED_DESKTOP_PLATFORMS")
            )
        )
    )
    apk_assets = (
        expand_apk_selected_patterns(_as_list(config.get("SELECTED_APK_ASSETS")))
        if legacy_apk_present
        else []
    )
    desktop_assets = (
        _as_list(
            config.get(
                "SELECTED_DESKTOP_ASSETS", config.get("SELECTED_DESKTOP_PLATFORMS")
            )
        )
        if legacy_desktop_present
        else []
    )
    ambiguous = False

    if not legacy_apk_present or not legacy_desktop_present:
        for item in selected_assets:
            if is_android_asset_name(item):
                if not legacy_apk_present:
                    apk_assets.append(item)
            elif is_desktop_asset_name(item):
                if not legacy_desktop_present:
                    desktop_assets.append(item)
            else:
                ambiguous = True

    return _dedupe(apk_assets), _dedupe(desktop_assets), ambiguous


def get_selected_app_assets(config: Dict[str, Any]) -> List[str]:
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


def normalize_client_app_config(config: Dict[str, Any]) -> Dict[str, Any]:
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

    if "CHECK_APP_PRERELEASES" not in config:
        apk_default = config.get("CHECK_PRERELEASES", False)
        config["CHECK_APP_PRERELEASES"] = coerce_bool(
            config.get("CHECK_APK_PRERELEASES", apk_default),
            default=False,
        ) or coerce_bool(config.get("CHECK_DESKTOP_PRERELEASES", False), default=False)
    else:
        config["CHECK_APP_PRERELEASES"] = coerce_bool(
            config.get("CHECK_APP_PRERELEASES"),
            default=DEFAULT_CHECK_APP_PRERELEASES,
        )

    # Keep legacy keys readable for compatibility without guessing from substrings.
    apk_assets, desktop_assets, has_ambiguous_assets = _classify_selected_assets(
        config, config["SELECTED_APP_ASSETS"]
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
        config["SAVE_APKS"] = client_apps_enabled
        config["SAVE_DESKTOP_APP"] = client_apps_enabled
    config.pop("SELECTED_DESKTOP_PLATFORMS", None)
    config["ANDROID_VERSIONS_TO_KEEP"] = config["APP_VERSIONS_TO_KEEP"]
    config["DESKTOP_VERSIONS_TO_KEEP"] = config["APP_VERSIONS_TO_KEEP"]
    config["CHECK_APK_PRERELEASES"] = config["CHECK_APP_PRERELEASES"]
    config["CHECK_DESKTOP_PRERELEASES"] = config["CHECK_APP_PRERELEASES"]
    return config


def client_app_downloads_enabled(config: Dict[str, Any]) -> bool:
    """Return whether client app downloads are enabled without mutating config."""
    normalized = normalize_client_app_config(dict(config))
    return coerce_bool(normalized.get("SAVE_CLIENT_APPS"), default=False)
