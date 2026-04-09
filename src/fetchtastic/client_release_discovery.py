"""
Shared release-discovery helpers for client app artifacts (Android + Desktop).
"""

from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence

from fetchtastic.constants import (
    APK_EXTENSION,
    DESKTOP_EXTENSIONS,
)

_DESKTOP_EXTENSIONS_LOWER = tuple(ext.lower() for ext in DESKTOP_EXTENSIONS)


class SupportsReleaseTuple(Protocol):
    """Protocol for version manager objects used in minimum-version checks."""

    def get_release_tuple(self, version: Optional[str]) -> tuple[int, ...] | None:
        """Return parsed version tuple for a tag, or None when unparsable."""


def is_android_asset_name(asset_name: str) -> bool:
    """Return True when the filename is an Android APK asset."""
    return asset_name.lower().endswith(APK_EXTENSION)


def is_desktop_asset_name(asset_name: str) -> bool:
    """Return True when the filename is a recognized Desktop installer asset."""
    return asset_name.lower().endswith(_DESKTOP_EXTENSIONS_LOWER)


def is_android_prerelease_tag(tag_name: str) -> bool:
    """Return True for Android legacy prerelease tag styles."""
    lowered = (tag_name or "").lower()
    return "-open" in lowered or "-closed" in lowered


def is_desktop_prerelease_tag(tag_name: str) -> bool:
    """Return True for Desktop legacy prerelease tag styles."""
    lowered = (tag_name or "").lower()
    return "-open" in lowered or "-closed" in lowered or "-internal" in lowered


def _iter_release_asset_dicts(release: Mapping[str, Any]) -> Sequence[Dict[str, Any]]:
    """Return release assets when present as a list of dict entries."""
    raw_assets = release.get("assets") or []
    if not isinstance(raw_assets, list):
        return []
    return [asset for asset in raw_assets if isinstance(asset, dict)]


def release_has_matching_assets(
    release: Mapping[str, Any],
    *,
    asset_name_matcher: Callable[[str], bool],
) -> bool:
    """Return True if a release contains at least one matching asset filename."""
    for asset in _iter_release_asset_dicts(release):
        name = asset.get("name")
        if isinstance(name, str) and name and asset_name_matcher(name):
            return True
    return False


def extract_matching_asset_names(
    release: Mapping[str, Any],
    *,
    asset_name_matcher: Callable[[str], bool],
) -> list[str]:
    """Extract matching asset names from a release."""
    matches: list[str] = []
    for asset in _iter_release_asset_dicts(release):
        name = asset.get("name")
        if isinstance(name, str) and name and asset_name_matcher(name):
            matches.append(name)
    return matches


def extract_matching_asset_dicts(
    release: Mapping[str, Any],
    *,
    asset_name_matcher: Callable[[str], bool],
) -> list[Dict[str, Any]]:
    """
    Extract matching assets as dicts with `name` and `size` keys.

    Invalid/missing size values are normalized to 0.
    """
    matches: list[Dict[str, Any]] = []
    for asset in _iter_release_asset_dicts(release):
        name = asset.get("name")
        if not isinstance(name, str) or not name or not asset_name_matcher(name):
            continue

        raw_size = asset.get("size", 0)
        try:
            parsed_size = int(raw_size)
        except (TypeError, ValueError):
            parsed_size = 0
        matches.append({"name": name, "size": max(0, parsed_size)})
    return matches


def is_release_prerelease(
    release: Mapping[str, Any],
    *,
    tag_prerelease_matcher: Callable[[str], bool],
) -> bool:
    """Return True when GitHub prerelease flag or legacy tag pattern marks prerelease."""
    tag_name = release.get("tag_name", "")
    tag_text = tag_name if isinstance(tag_name, str) else ""
    is_github_prerelease = bool(release.get("prerelease", False))
    return is_github_prerelease or tag_prerelease_matcher(tag_text)


def select_best_release_with_assets(
    releases: Sequence[Any],
    *,
    asset_name_matcher: Callable[[str], bool],
    tag_prerelease_matcher: Callable[[str], bool],
    max_releases_to_scan: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Select the best release containing matching assets.

    Selection policy:
    - Scan up to `max_releases_to_scan` releases in listed order.
    - Prefer the first stable release containing matching assets.
    - If no stable release qualifies, return the first prerelease candidate.
    - Return None when no scanned release has matching assets.
    """
    if max_releases_to_scan <= 0:
        return None

    prerelease_candidate: Optional[Dict[str, Any]] = None
    for raw_release in releases[:max_releases_to_scan]:
        if not isinstance(raw_release, dict):
            continue
        release = raw_release
        if not release_has_matching_assets(
            release, asset_name_matcher=asset_name_matcher
        ):
            continue

        if not is_release_prerelease(
            release, tag_prerelease_matcher=tag_prerelease_matcher
        ):
            return release
        if prerelease_candidate is None:
            prerelease_candidate = release

    return prerelease_candidate


def is_release_at_or_above_minimum(
    tag_name: str,
    *,
    minimum_version: tuple[int, ...],
    version_manager: SupportsReleaseTuple,
) -> bool:
    """
    Return True when parsed tag version is >= minimum; unparsable tags are allowed.
    """
    version_tuple = version_manager.get_release_tuple(tag_name)
    if not version_tuple:
        return True

    max_len = max(len(version_tuple), len(minimum_version))
    padded_version = version_tuple + (0,) * (max_len - len(version_tuple))
    padded_minimum = minimum_version + (0,) * (max_len - len(minimum_version))
    return padded_version >= padded_minimum
