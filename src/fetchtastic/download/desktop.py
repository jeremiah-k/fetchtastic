"""Compatibility wrapper for the unified Meshtastic client app downloader."""

from __future__ import annotations

from typing import Any, Optional

from fetchtastic.client_release_discovery import (
    is_desktop_asset_name,
    is_desktop_prerelease_tag,
    is_release_at_or_above_minimum,
)
from fetchtastic.constants import (
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
    FILE_TYPE_DESKTOP,
    FILE_TYPE_DESKTOP_PRERELEASE,
)

from .client_app import MeshtasticClientAppDownloader
from .interfaces import Asset, DownloadResult, Release
from .version import VersionManager

MIN_DESKTOP_TRACKED_VERSION = (2, 7, 14)


class MeshtasticDesktopDownloader(MeshtasticClientAppDownloader):
    """Keep legacy Desktop callers scoped to desktop installer assets."""

    def get_assets(self, release: Release) -> list[Asset]:
        return [
            asset
            for asset in super().get_assets(release)
            if is_desktop_asset_name(asset.name)
        ]

    def should_download_asset(self, asset_name: str) -> bool:
        return is_desktop_asset_name(asset_name) and super().should_download_asset(
            asset_name
        )

    def download_desktop(self, release: Release, asset: Asset) -> DownloadResult:
        result = self.download_app(release, asset)
        if result.file_type == FILE_TYPE_CLIENT_APP:
            result.file_type = FILE_TYPE_DESKTOP
        elif result.file_type == FILE_TYPE_CLIENT_APP_PRERELEASE:
            result.file_type = FILE_TYPE_DESKTOP_PRERELEASE
        return result


def _is_desktop_prerelease_by_name(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    if not is_desktop_prerelease_tag(tag_name):
        return False
    manager = version_manager or VersionManager()
    return is_release_at_or_above_minimum(
        tag_name,
        minimum_version=MIN_DESKTOP_TRACKED_VERSION,
        version_manager=manager,
    )


def _is_desktop_prerelease(release: dict[str, Any]) -> bool:
    tag_name = (release or {}).get("tag_name", "")
    return isinstance(tag_name, str) and _is_desktop_prerelease_by_name(tag_name)


__all__ = [
    "MIN_DESKTOP_TRACKED_VERSION",
    "MeshtasticDesktopDownloader",
    "_is_desktop_prerelease",
    "_is_desktop_prerelease_by_name",
]
