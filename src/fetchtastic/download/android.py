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
    is_android_prerelease_tag,
    is_release_at_or_above_minimum,
    is_release_prerelease,
)

from .client_app import MeshtasticClientAppDownloader
from .version import VersionManager

MIN_ANDROID_TRACKED_VERSION = (2, 7, 0)


class MeshtasticAndroidAppDownloader(MeshtasticClientAppDownloader):
    """Backward-compatible name for MeshtasticClientAppDownloader."""


def _is_apk_prerelease_by_name(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    """Return whether an Android tag should be treated as a tracked prerelease."""
    if not is_android_prerelease_tag(tag_name):
        return False
    manager = version_manager or VersionManager()
    return is_release_at_or_above_minimum(
        tag_name,
        minimum_version=MIN_ANDROID_TRACKED_VERSION,
        version_manager=manager,
    )


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    """Return whether a release payload is an Android prerelease."""
    return is_release_prerelease(
        release or {},
        tag_prerelease_matcher=is_android_prerelease_tag,
    )
