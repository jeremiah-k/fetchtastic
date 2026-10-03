"""Legacy import aliases for the shared client app downloader."""

from .client_app import MeshtasticClientAppDownloader as MeshtasticDesktopDownloader
from .client_app import (
    _is_client_app_prerelease_by_name as _is_desktop_prerelease_by_name,
)
from .client_app import is_client_app_prerelease as _is_desktop_prerelease

__all__ = [
    "MeshtasticDesktopDownloader",
    "_is_desktop_prerelease",
    "_is_desktop_prerelease_by_name",
]
