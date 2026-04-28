"""
Compatibility wrapper for the unified Meshtastic client app downloader.

Desktop installers are now client app assets stored under app/<version>/ and
app/prerelease/<version>/ with APKs from the same upstream release feed. This
module keeps legacy imports working without owning a separate storage or cleanup
lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fetchtastic.client_release_discovery import (
    is_desktop_asset_name,
    is_desktop_prerelease_tag,
    is_release_at_or_above_minimum,
)

from .client_app import MeshtasticClientAppDownloader
from .interfaces import Asset, DownloadResult, Release
from .version import VersionManager

MIN_DESKTOP_TRACKED_VERSION = (2, 7, 14)


class MeshtasticDesktopDownloader(MeshtasticClientAppDownloader):
    """Backward-compatible Desktop-scoped wrapper for MeshtasticClientAppDownloader."""

    def get_assets(self, release: Release) -> list[Asset]:
        """Return Desktop installer assets only for legacy Desktop callers."""
        return [
            asset
            for asset in super().get_assets(release)
            if is_desktop_asset_name(asset.name)
        ]

    def should_download_asset(self, asset_name: str) -> bool:
        """Return whether a Desktop installer asset is selected for download."""
        return is_desktop_asset_name(asset_name) and super().should_download_asset(
            asset_name
        )

    def download_desktop(self, release: Release, asset: Asset) -> DownloadResult:
        """Compatibility alias for the unified client app download method."""
        return self.download_app(release, asset)


def _is_desktop_prerelease_by_name(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    """Return whether a Desktop tag should be treated as a tracked prerelease."""
    if not is_desktop_prerelease_tag(tag_name):
        return False
    manager = version_manager or VersionManager()
    return is_release_at_or_above_minimum(
        tag_name,
        minimum_version=MIN_DESKTOP_TRACKED_VERSION,
        version_manager=manager,
    )


def _is_desktop_prerelease(release: Dict[str, Any]) -> bool:
    """Return whether a release payload is a Desktop prerelease."""
    tag_name = (release or {}).get("tag_name", "")
    return isinstance(tag_name, str) and _is_desktop_prerelease_by_name(tag_name)
