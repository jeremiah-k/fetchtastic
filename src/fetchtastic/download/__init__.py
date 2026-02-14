"""
Fetchtastic Download Subsystem - Modular Architecture

This package provides a clean, extensible architecture for downloading various
Meshtastic artifacts (Android APKs, firmware, bootloaders, etc.) with clear
separation of concerns and interface-driven design.

Core Components:
- interfaces: Base interfaces and protocols
- android: Meshtastic Android app downloader
- firmware: Firmware release downloader
- repository: Repository file downloader
- orchestrator: Download pipeline coordination
- version: Version management utilities
- cache: Caching infrastructure
- files: File operations and utilities

Async Support:
- async_client: AsyncGitHubClient for async HTTP operations
- async_downloader: AsyncDownloaderMixin for async downloads
- BaseDownloader: Now includes async_download() method
"""

from .android import MeshtasticAndroidAppDownloader
from .async_client import AsyncDownloadError, AsyncGitHubClient, create_async_client
from .async_downloader import (
    AsyncDownloaderBase,
    AsyncDownloaderMixin,
    download_with_progress,
)
from .base import BaseDownloader
from .cache import CacheManager
from .cli_integration import DownloadCLIIntegration
from .config_utils import get_prerelease_patterns
from .files import FileOperations
from .firmware import FirmwareReleaseDownloader
from .interfaces import (
    Asset,
    Downloader,
    DownloadResult,
    DownloadSource,
    DownloadTask,
    Release,
)
from .orchestrator import DownloadOrchestrator
from .prerelease_history import PrereleaseHistoryManager
from .repository import RepositoryDownloader
from .version import VersionManager

__all__ = [
    # Interfaces
    "DownloadTask",
    "DownloadSource",
    "Downloader",
    "DownloadResult",
    "Release",
    "Asset",
    # Base classes
    "BaseDownloader",
    # Async support
    "AsyncGitHubClient",
    "AsyncDownloadError",
    "AsyncDownloaderMixin",
    "AsyncDownloaderBase",
    "create_async_client",
    "download_with_progress",
    # Downloaders
    "MeshtasticAndroidAppDownloader",
    "FirmwareReleaseDownloader",
    "RepositoryDownloader",
    # Orchestration
    "DownloadOrchestrator",
    # CLI Integration
    "DownloadCLIIntegration",
    # Core components
    "VersionManager",
    "PrereleaseHistoryManager",
    "CacheManager",
    "FileOperations",
    # Configuration utilities
    "get_prerelease_patterns",
]
