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
        return self._run_dfu_apps_menu()

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
        if is_first_run:
            prompt_text = f"How many versions of DFU/flashing apps would you like to keep? (default is {current_versions}): "
        else:
            prompt_text = f"How many versions of DFU/flashing apps would you like to keep? (current: {current_versions}): "
        dfu_apps_versions_to_keep = input(prompt_text).strip() or str(current_versions)
        config["DFU_APPS_VERSIONS_TO_KEEP"] = int(dfu_apps_versions_to_keep)

        return config

    def _run_dfu_apps_menu(self) -> Optional[Dict[str, Any]]:
        """Run the DFU apps selection menu using pick."""
        from pick import pick

        print("\n" + "=" * 60)
        print("DFU/Firmware Flashing Apps Selection")
        print("=" * 60)

        # Define available DFU apps
        dfu_apps = [
            {
                "id": "nordic_dfu",
                "name": "Nordic DFU Library APK",
                "description": "Android DFU library for flashing Nordic nRF devices",
                "repo": "NordicSemiconductor/Android-DFU-Library",
            }
        ]

        # Create options for pick
        options = []
        for app in dfu_apps:
            option_text = (
                f"{app['name']} - {app['description']} (Repository: {app['repo']})"
            )
            options.append(option_text)

        try:
            from pick import pick

            # Use pick for multi-selection
            selected_options, selected_indices = pick(
                options,
                "Select DFU/flashing apps to download (SPACE to select, ENTER to confirm):",
                multiselect=True,
                min_selection_count=1,
            )

            # Get selected app IDs
            selected_apps = [dfu_apps[i]["id"] for i in selected_indices]
            print(
                f"\nSelected DFU apps: {', '.join([dfu_apps[i]['name'] for i in selected_indices])}"
            )

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

        return {"SELECTED_DFU_APPS": selected_apps}
