# src/fetchtastic/assets/dfu_apps.py

"""
DFU/Firmware flashing apps asset handler.
"""

from typing import Any, Dict, List, Optional

from .base import AssetType, BaseAssetHandler


class DFUAppsAsset(BaseAssetHandler):
    """Handler for DFU/firmware flashing app assets."""

    def __init__(self):
        asset_type = AssetType(
            id="dfu_apps",
            name="DFU/Firmware Flashing Apps",
            description="Apps for flashing firmware and bootloaders to devices",
            config_key="SAVE_DFU_APPS",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "DFU/Firmware Flashing Apps"

    def get_description(self) -> str:
        return "Apps for flashing firmware and bootloaders to devices"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the DFU apps selection menu."""
        return self._run_dfu_apps_menu(config)

    def get_config_keys(self) -> List[str]:
        return ["SAVE_DFU_APPS", "SELECTED_DFU_APPS", "DFU_APPS_VERSIONS_TO_KEEP"]

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate DFU apps configuration."""
        if not config.get("SAVE_DFU_APPS", False):
            return True  # Not enabled, so valid

        # Check if DFU apps are selected
        selected_apps = config.get("SELECTED_DFU_APPS", [])
        return len(selected_apps) > 0

    def get_download_info(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Get DFU apps download information."""
        return {
            "enabled": config.get("SAVE_DFU_APPS", False),
            "selected_apps": config.get("SELECTED_DFU_APPS", []),
            "versions_to_keep": config.get("DFU_APPS_VERSIONS_TO_KEEP", 2),
        }

    def setup_additional_config(
        self, config: Dict[str, Any], is_first_run: bool
    ) -> Dict[str, Any]:
        """Setup additional DFU apps-specific configuration."""
        # Determine default number of versions to keep based on platform
        from ..setup_config import is_termux

        default_versions_to_keep = 2 if is_termux() else 3

        # Prompt for number of versions to keep
        current_versions = config.get(
            "DFU_APPS_VERSIONS_TO_KEEP", default_versions_to_keep
        )
        from fetchtastic.ui_utils import text_input

        dfu_apps_versions_to_keep = text_input(
            "How many versions of DFU/flashing apps would you like to keep?",
            default=str(current_versions),
        )

        if dfu_apps_versions_to_keep is None:
            print("Setup cancelled.")
            return config

        try:
            config["DFU_APPS_VERSIONS_TO_KEEP"] = int(dfu_apps_versions_to_keep)
        except ValueError:
            print(f"Invalid number entered. Using default: {current_versions}")
            config["DFU_APPS_VERSIONS_TO_KEEP"] = current_versions

        return config

    def _run_dfu_apps_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the DFU apps selection menu using enhanced UI."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 60)
        print("DFU/Firmware Flashing Apps Selection")
        print("=" * 60)

        # Define available DFU apps with enhanced information
        dfu_app_options = [
            {
                "title": "Nordic DFU Library APK",
                "value": "nordic_dfu",
                "description": "Android DFU library for flashing Nordic nRF devices (NordicSemiconductor/Android-DFU-Library)",
            },
            {
                "title": "nRF Connect Device Manager",
                "value": "nrf_device_manager",
                "description": "Nordic's official device management app with DFU capabilities (NordicSemiconductor/Android-nRF-Connect-Device-Manager)",
            },
        ]

        # Get preselected apps from config
        current_apps = config.get("SELECTED_DFU_APPS", [])

        try:
            # Show preselection info if any
            if current_apps:
                app_names = [
                    app["title"]
                    for app in dfu_app_options
                    if app["value"] in current_apps
                ]
                if app_names:
                    show_preselection_info(app_names)

            selected_apps = multi_select_with_info(
                message="Select DFU/flashing apps to download:",
                choices=dfu_app_options,
                preselected=current_apps,
                min_selection=1,
            )

            if not selected_apps:
                print("No DFU apps selected.")
                return None

            # Get selected app names for display
            selected_names = [
                app["title"] for app in dfu_app_options if app["value"] in selected_apps
            ]
            print(f"\nSelected DFU apps: {', '.join(selected_names)}")

            return {"SELECTED_DFU_APPS": selected_apps}

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None
