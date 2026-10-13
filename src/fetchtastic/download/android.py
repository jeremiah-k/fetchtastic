"""Compatibility wrapper for the unified Meshtastic client app downloader."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fetchtastic.client_release_discovery import is_android_asset_name
from fetchtastic.constants import (
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
)

from .client_app import (
    MeshtasticClientAppDownloader,
    _is_client_app_prerelease_by_name,
)
from .interfaces import Asset, DownloadResult, Release
from .version import VersionManager


class MeshtasticAndroidAppDownloader(MeshtasticClientAppDownloader):
    """Keep legacy Android callers scoped to APK assets."""

    def get_assets(self, release: Release) -> list[Asset]:
        return [
            asset
            for asset in super().get_assets(release)
            if is_android_asset_name(asset.name)
        ]

    def should_download_asset(self, asset_name: str) -> bool:
        return is_android_asset_name(asset_name) and super().should_download_asset(
            asset_name
        )

    def download_apk(self, release: Release, asset: Asset) -> DownloadResult:
        result = self.download_app(release, asset)
        if result.file_type == FILE_TYPE_CLIENT_APP:
            result.file_type = FILE_TYPE_ANDROID
        elif result.file_type == FILE_TYPE_CLIENT_APP_PRERELEASE:
            result.file_type = FILE_TYPE_ANDROID_PRERELEASE
        return result


def _is_apk_prerelease_by_name(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    return _is_client_app_prerelease_by_name(tag_name, version_manager)


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    tag_name = (release or {}).get("tag_name", "")
    return isinstance(tag_name, str) and _is_apk_prerelease_by_name(tag_name)


__all__ = [
    "MeshtasticAndroidAppDownloader",
    "_is_apk_prerelease",
    "_is_apk_prerelease_by_name",
]
