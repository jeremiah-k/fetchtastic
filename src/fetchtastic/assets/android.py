# src/fetchtastic/assets/android.py

"""
Meshtastic Android APK asset handler.
"""

from typing import Any, Dict, List, Optional

from .. import menu_apk
from .base import AssetType, BaseAssetHandler


class MeshtasticAndroidAsset(BaseAssetHandler):
    """Handler for Meshtastic Android APK assets."""

    def __init__(self):
        asset_type = AssetType(
            id="android",
            name="Meshtastic Android APKs",
            description="Meshtastic Android app releases from GitHub",
            config_key="SAVE_APKS",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "Meshtastic Android APKs"

    def get_description(self) -> str:
        return "Meshtastic Android app releases from GitHub"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the APK selection menu."""
        # Get preselected patterns from config
        preselected_patterns = config.get("SELECTED_APK_ASSETS", [])
        return menu_apk.run_menu(preselected_patterns)

    def get_config_keys(self) -> List[str]:
        return ["SAVE_APKS", "SELECTED_APK_ASSETS", "ANDROID_VERSIONS_TO_KEEP"]

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate Android APK configuration."""
        if not config.get("SAVE_APKS", False):
            return True  # Not enabled, so valid

        # Check if APK assets are selected
        selected_assets = config.get("SELECTED_APK_ASSETS", [])
        return len(selected_assets) > 0

    def get_download_info(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Get Android APK download information."""
        return {
            "enabled": config.get("SAVE_APKS", False),
            "selected_assets": config.get("SELECTED_APK_ASSETS", []),
            "versions_to_keep": config.get("ANDROID_VERSIONS_TO_KEEP", 2),
        }

    def setup_additional_config(
        self, config: Dict[str, Any], is_first_run: bool
    ) -> Dict[str, Any]:
        """Setup additional Android-specific configuration."""
        # Determine default number of versions to keep based on platform
        from ..setup_config import is_termux

        default_versions_to_keep = 2 if is_termux() else 3

        # Prompt for number of versions to keep
        current_versions = config.get(
            "ANDROID_VERSIONS_TO_KEEP", default_versions_to_keep
        )
        from fetchtastic.ui_utils import text_input

        android_versions_to_keep = text_input(
            "How many versions of the Android app would you like to keep?",
            default=str(current_versions),
        )

        if android_versions_to_keep is None:
            print("Setup cancelled.")
            return config

        try:
            config["ANDROID_VERSIONS_TO_KEEP"] = int(android_versions_to_keep)
        except ValueError:
            print(f"Invalid number entered. Using default: {current_versions}")
            config["ANDROID_VERSIONS_TO_KEEP"] = current_versions

        return config
