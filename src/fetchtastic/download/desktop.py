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
    is_desktop_prerelease_tag,
    is_release_at_or_above_minimum,
    is_release_prerelease,
)

from .client_app import MeshtasticClientAppDownloader
from .version import VersionManager

MIN_DESKTOP_TRACKED_VERSION = (2, 7, 14)


class MeshtasticDesktopDownloader(MeshtasticClientAppDownloader):
    """Backward-compatible name for MeshtasticClientAppDownloader."""


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
    return is_release_prerelease(
        release or {},
        tag_prerelease_matcher=is_desktop_prerelease_tag,
    )
