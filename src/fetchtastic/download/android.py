"""
Compatibility wrapper for the unified Meshtastic client app downloader.

Android APKs are now client app assets stored under app/<version>/ and
app/prerelease/<version>/ with Desktop installers from the same upstream
release feed. This module keeps legacy imports working without owning a
separate storage or cleanup lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fetchtastic.client_release_discovery import (
    is_android_asset_name,
)
from fetchtastic.constants import (
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
)

from .client_app import (
    MeshtasticClientAppDownloader,
)
from .client_app import (
    _is_apk_prerelease_by_name as _client_app_is_apk_prerelease_by_name,
)
from .interfaces import Asset, DownloadResult, Release
from .version import VersionManager


class MeshtasticAndroidAppDownloader(MeshtasticClientAppDownloader):
    """Backward-compatible APK-scoped wrapper for MeshtasticClientAppDownloader."""

    def get_assets(self, release: Release) -> list[Asset]:
        """Return APK assets only for legacy Android callers."""
        return [
            asset
            for asset in super().get_assets(release)
            if is_android_asset_name(asset.name)
        ]

    def should_download_asset(self, asset_name: str) -> bool:
        """Return whether an APK asset is selected for download."""
        return is_android_asset_name(asset_name) and super().should_download_asset(
            asset_name
        )

    def download_apk(self, release: Release, asset: Asset) -> DownloadResult:
        """Compatibility alias for the unified client app download method."""
        result = self.download_app(release, asset)
        if result.file_type == FILE_TYPE_CLIENT_APP:
            result.file_type = FILE_TYPE_ANDROID
        elif result.file_type == FILE_TYPE_CLIENT_APP_PRERELEASE:
            result.file_type = FILE_TYPE_ANDROID_PRERELEASE
        return result


def _is_apk_prerelease_by_name(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    """Return whether an Android tag should be treated as a tracked prerelease."""
    return _client_app_is_apk_prerelease_by_name(tag_name, version_manager)


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    """Return whether a release payload is an Android prerelease."""
    tag_name = (release or {}).get("tag_name", "")
    return isinstance(tag_name, str) and _is_apk_prerelease_by_name(tag_name)
