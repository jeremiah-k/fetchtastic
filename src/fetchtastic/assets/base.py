# src/fetchtastic/assets/base.py

"""
Base classes for the asset management system.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AssetType:
    """Represents an asset type that can be downloaded."""

    id: str
    name: str
    description: str
    enabled: bool = False
    config_key: str = ""

    def __post_init__(self):
        if not self.config_key:
            self.config_key = f"SAVE_{self.id.upper()}"


class BaseAssetHandler(ABC):
    """Base class for all asset handlers."""

    def __init__(self, asset_type: AssetType):
        self.asset_type = asset_type

    @abstractmethod
    def get_display_name(self) -> str:
        """Return the display name for this asset type."""
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return a description of this asset type."""
        pass

    @abstractmethod
    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Run the asset selection menu for this asset type.

        Args:
            config: Current configuration dictionary

        Returns:
            Dictionary with selection results or None if cancelled
        """
        pass

    @abstractmethod
    def get_config_keys(self) -> List[str]:
        """Return list of configuration keys this asset type uses."""
        pass

    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate configuration for this asset type."""
        pass

    @abstractmethod
    def get_download_info(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Get download information for this asset type."""
        pass

    def setup_additional_config(
        self, config: Dict[str, Any], is_first_run: bool
    ) -> Dict[str, Any]:
        """
        Setup additional configuration specific to this asset type.
        Override in subclasses if needed.

        Args:
            config: Current configuration dictionary
            is_first_run: Whether this is the first time setup is being run

        Returns:
            Updated configuration dictionary
        """
        return config


class AssetManager:
    """Manages all available asset types."""

    def __init__(self):
        self.handlers: Dict[str, BaseAssetHandler] = {}
        self.asset_types: List[AssetType] = []

    def register_handler(self, handler: BaseAssetHandler):
        """Register an asset handler."""
        self.handlers[handler.asset_type.id] = handler
        if handler.asset_type not in self.asset_types:
            self.asset_types.append(handler.asset_type)

    def get_handler(self, asset_id: str) -> Optional[BaseAssetHandler]:
        """Get handler for a specific asset type."""
        return self.handlers.get(asset_id)

    def get_all_asset_types(self) -> List[AssetType]:
        """Get all registered asset types."""
        return self.asset_types.copy()

    def get_enabled_asset_types(self) -> List[AssetType]:
        """Get all enabled asset types."""
        return [asset for asset in self.asset_types if asset.enabled]

    def validate_all_configs(self, config: Dict[str, Any]) -> bool:
        """Validate configuration for all enabled asset types."""
        for asset_type in self.get_enabled_asset_types():
            handler = self.get_handler(asset_type.id)
            if handler and not handler.validate_config(config):
                return False
        return True
