# src/fetchtastic/assets/android.py

"""
Meshtastic Android APK asset handler.
"""

from typing import Any, Dict, List, Optional

from .base import AssetType, BaseAssetHandler


class MeshtasticAndroidAsset(BaseAssetHandler):
    """Handler for Meshtastic Android APK assets."""

    def __init__(self):
        asset_type = AssetType(
            id="android",
            name="Meshtastic Android APKs",
            description="Official Meshtastic Android app releases with multiple build variants (release, debug, F-Droid)",
            config_key="SAVE_APKS",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "Meshtastic Android APKs"

    def get_description(self) -> str:
        return "Meshtastic Android app releases from GitHub"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the enhanced Android APK selection menu."""
        return self._run_android_apps_menu(config)

    def get_config_keys(self) -> List[str]:
        return [
            "SAVE_APKS",
            "SELECTED_APK_ASSETS",  # Legacy key for backward compatibility
            "SELECTED_ANDROID_APPS",  # New key for app selection
            "SELECTED_ANDROID_PATTERNS",  # New key for pattern selection
            "ANDROID_VERSIONS_TO_KEEP",
        ]

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate Android APK configuration."""
        if not config.get("SAVE_APKS", False):
            return True  # Not enabled, so valid

        # Check if APK assets are selected
        selected_assets = config.get("SELECTED_APK_ASSETS", [])
        return len(selected_assets) > 0

    def _run_android_apps_menu(
        self, config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run the enhanced Android apps selection menu."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 60)
        print("Android Apps Selection")
        print("=" * 60)

        # Define available Android apps with enhanced information
        android_app_options = [
            {
                "title": "Meshtastic Android (Official)",
                "value": "meshtastic_android",
                "description": "Official Meshtastic Android app with multiple build variants (release, debug, fdroid)",
            },
            {
                "title": "Nordic DFU Library",
                "value": "nordic_dfu_legacy",
                "description": "Legacy Nordic DFU library for flashing nRF devices (deprecated, use Device Manager instead)",
            },
        ]

        # Get preselected apps from config
        current_apps = config.get("SELECTED_ANDROID_APPS", [])
        # Support legacy config key for backward compatibility
        if not current_apps:
            current_apps = config.get("SELECTED_APK_ASSETS", [])

        try:
            # Show preselection info if any
            if current_apps:
                app_names = [
                    app["title"]
                    for app in android_app_options
                    if app["value"] in current_apps
                ]
                if app_names:
                    show_preselection_info(app_names)

            selected_apps = multi_select_with_info(
                message="Select Android apps to download:",
                choices=android_app_options,
                preselected=current_apps,
                min_selection=1,
            )

            if not selected_apps:
                print("No Android apps selected.")
                return None

            # For each selected app, get specific patterns
            selected_patterns = {}
            for app_id in selected_apps:
                if app_id == "meshtastic_android":
                    patterns = self._select_meshtastic_android_patterns(config)
                    if patterns:
                        selected_patterns[app_id] = patterns
                elif app_id == "nordic_dfu_legacy":
                    # For legacy DFU, just use a simple pattern
                    selected_patterns[app_id] = [".*\\.apk$"]

            if not selected_patterns:
                print("No specific patterns selected.")
                return None

            return {
                "SELECTED_ANDROID_APPS": selected_apps,
                "SELECTED_ANDROID_PATTERNS": selected_patterns,
                # Maintain backward compatibility
                "SELECTED_APK_ASSETS": list(
                    selected_patterns.get("meshtastic_android", [])
                ),
            }

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

    def _select_meshtastic_android_patterns(self, config: Dict[str, Any]) -> List[str]:
        """Select specific Meshtastic Android app patterns."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("Meshtastic Android App Variants")
        print("=" * 40)

        # Define Meshtastic Android app patterns with comprehensive descriptions
        pattern_options = [
            {
                "title": "Google Play Store Release",
                "value": ".*release.*\\.apk$",
                "description": "Official Google Play Store release builds with Google services (recommended for most users)",
            },
            {
                "title": "F-Droid Release",
                "value": ".*fdroid.*\\.apk$",
                "description": "F-Droid compatible builds without proprietary Google dependencies (open source stores)",
            },
        ]

        # Get current patterns from config
        current_patterns = config.get("SELECTED_ANDROID_PATTERNS", {}).get(
            "meshtastic_android", []
        )
        # Support legacy config for backward compatibility
        if not current_patterns:
            current_patterns = config.get("SELECTED_APK_ASSETS", [])

        try:
            # Show preselection info if any
            if current_patterns:
                show_preselection_info(current_patterns)

            selected_patterns = multi_select_with_info(
                message="Select Meshtastic Android app variants:",
                choices=pattern_options,
                preselected=current_patterns,
                min_selection=1,
            )

            if not selected_patterns:
                print("No patterns selected.")
                return []

            print(f"\nSelected patterns: {', '.join(selected_patterns)}")
            return selected_patterns

        except (KeyboardInterrupt, EOFError):
            print("\nPattern selection cancelled.")
            return []

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
