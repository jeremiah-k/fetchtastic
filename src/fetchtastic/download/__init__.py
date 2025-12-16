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
"""

from .android import MeshtasticAndroidAppDownloader
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
