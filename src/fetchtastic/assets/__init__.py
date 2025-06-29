# src/fetchtastic/assets/__init__.py

"""
Asset management system for Fetchtastic.

This module provides a modular architecture for handling different types of assets
(firmware, APKs, bootloaders, DFU apps, etc.) with consistent interfaces and
extensible functionality.
"""

from .android import MeshtasticAndroidAsset
from .base import AssetManager, AssetType
from .bootloaders import BootloaderAsset
from .dfu_apps import DFUAppsAsset
from .firmware import MeshtasticFirmwareAsset

__all__ = [
    "AssetType",
    "AssetManager",
    "MeshtasticFirmwareAsset",
    "MeshtasticAndroidAsset",
    "BootloaderAsset",
    "DFUAppsAsset",
]
