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
        """Run the Android apps shopping cart selection menu."""
        print("\n" + "=" * 60)
        print("Android Apps Selection")
        print("=" * 60)

        # Use shopping cart style selection
        selected_patterns = self._run_android_shopping_cart(config)

        if not selected_patterns:
            print("No Android apps selected.")
            return None

        # Convert to flat pattern list for backward compatibility
        all_patterns = []
        for app_patterns in selected_patterns.values():
            all_patterns.extend(app_patterns)

        return {
            "SELECTED_ANDROID_APPS": selected_patterns,
            "SELECTED_ANDROID_PATTERNS": selected_patterns,
        }

    def _run_android_shopping_cart(
        self, config: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """
        Run shopping cart style Android app selection.

        Returns:
            Dict mapping app IDs to their selected patterns
        """
        from fetchtastic.ui_utils import single_select_with_info

        # Track selected patterns across all apps
        selected_patterns = {}

        # Get existing selections from config
        existing_patterns = config.get("SELECTED_ANDROID_APPS", {})
        if existing_patterns:
            selected_patterns = existing_patterns.copy()

        # Define available Android apps
        available_apps = {
            "meshtastic_official": {
                "name": "Meshtastic Official Client",
                "description": "Official Meshtastic Android app with multiple build variants",
            },
            "nordic_dfu": {
                "name": "Nordic DFU App",
                "description": "Nordic Device Firmware Update app for flashing nRF devices",
            },
        }

        while True:
            # Build app menu with current selections
            app_choices = []
            for app_id, app_info in available_apps.items():
                # Count how many patterns are selected for this app
                app_patterns = selected_patterns.get(app_id, [])
                count = len(app_patterns)

                if count > 0:
                    title = f"{app_info['name']} ({count} selected)"
                    description = f"Selected: {', '.join(app_patterns)}"
                else:
                    title = f"{app_info['name']} (0 selected)"
                    description = app_info["description"]

                app_choices.append(
                    {
                        "title": title,
                        "value": app_id,
                        "description": description,
                    }
                )

            # Add control options
            app_choices.extend(
                [
                    {
                        "title": "--- Actions ---",
                        "value": "separator",
                        "description": "",
                    },
                    {
                        "title": f"Finish Selection ({sum(len(patterns) for patterns in selected_patterns.values())} total)",
                        "value": "finish",
                        "description": (
                            "Continue with selected apps"
                            if selected_patterns
                            else "No apps selected"
                        ),
                    },
                    {
                        "title": "Cancel",
                        "value": "cancel",
                        "description": "Cancel Android app configuration",
                    },
                ]
            )

            print("\n" + "=" * 60)
            print("Android App Selection - Shopping Cart")
            print("=" * 60)
            if selected_patterns:
                total_patterns = sum(
                    len(patterns) for patterns in selected_patterns.values()
                )
                print(
                    f"Currently selected: {total_patterns} app variants across {len(selected_patterns)} apps"
                )
            else:
                print("No apps selected yet")

            try:
                choice = single_select_with_info(
                    message="Select app to configure or choose action:",
                    choices=app_choices,
                    default=None,
                )

                if choice is None or choice == "cancel":
                    print("\nSelection cancelled.")
                    return {}
                elif choice == "finish":
                    return selected_patterns
                elif choice == "separator":
                    continue  # Ignore separator selection
                else:
                    # User selected an app - enter asset selection
                    updated_patterns = self._select_assets_for_app(
                        choice,
                        available_apps[choice],
                        selected_patterns.get(choice, []),
                    )
                    if updated_patterns is not None:
                        if updated_patterns:
                            selected_patterns[choice] = updated_patterns
                        elif choice in selected_patterns:
                            # Remove app if no patterns selected
                            del selected_patterns[choice]
                    # Continue loop to show updated main menu

            except KeyboardInterrupt:
                print("\nSelection cancelled.")
                return {}

    def _select_assets_for_app(
        self, app_id: str, app_info: Dict[str, str], current_patterns: List[str]
    ) -> List[str]:
        """
        Select asset patterns for a specific app.

        Returns:
            Updated list of patterns for this app, or None if cancelled
        """
        if app_id == "meshtastic_official":
            return self._select_meshtastic_android_patterns_new(current_patterns)
        elif app_id == "nordic_dfu":
            return self._select_nordic_dfu_patterns(current_patterns)
        else:
            return current_patterns

    def _select_meshtastic_android_patterns_new(
        self, current_patterns: List[str]
    ) -> List[str]:
        """Select patterns for Meshtastic Official Client."""
        from fetchtastic.ui_utils import multi_select_with_info

        # Define Meshtastic Android app patterns
        pattern_options = [
            {
                "title": "Google Play Store Release",
                "value": "googleRelease-",
                "description": "Official release APK from Google Play Store",
            },
            {
                "title": "F-Droid Release",
                "value": "fdroidRelease-",
                "description": "F-Droid compatible APK without Google services",
            },
            {
                "title": "Debug Build",
                "value": "debug-",
                "description": "Debug APK for development and testing",
            },
            {
                "title": "Android App Bundle",
                "value": "release-.*\\.aab$",
                "description": "Android App Bundle (AAB) for distribution",
            },
        ]

        print(f"\n" + "=" * 40)
        print("Meshtastic Official Client - Asset Selection")
        print("=" * 40)
        if current_patterns:
            print(f"Currently selected: {', '.join(current_patterns)}")
        else:
            print("No assets selected for this app")

        try:
            selected_patterns = multi_select_with_info(
                message="Select Meshtastic app variants (space to select, enter when done):",
                choices=pattern_options,
                preselected=current_patterns,
                min_selection=0,
            )

            return (
                selected_patterns if selected_patterns is not None else current_patterns
            )

        except KeyboardInterrupt:
            return current_patterns

    def _select_nordic_dfu_patterns(self, current_patterns: List[str]) -> List[str]:
        """Select patterns for Nordic DFU app."""
        from fetchtastic.ui_utils import confirm_prompt

        print(f"\n" + "=" * 40)
        print("Nordic DFU App - Asset Selection")
        print("=" * 40)
        print("Nordic DFU app downloads all available APK files.")

        currently_selected = bool(current_patterns)
        if currently_selected:
            print("Currently selected: Nordic DFU APK files")
        else:
            print("Not currently selected")

        try:
            include_dfu = confirm_prompt(
                "Include Nordic DFU app?",
                default=currently_selected,
            )

            if include_dfu:
                return [".*\\.apk$"]  # Download all APK files for DFU app
            else:
                return []

        except KeyboardInterrupt:
            return current_patterns

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
