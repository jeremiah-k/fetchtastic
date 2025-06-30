# src/fetchtastic/assets/bootloaders.py

"""
Bootloader asset handler for various device bootloaders.
"""

from typing import Any, Dict, List, Optional

from .base import AssetType, BaseAssetHandler


class BootloaderAsset(BaseAssetHandler):
    """Handler for bootloader assets."""

    def __init__(self):
        asset_type = AssetType(
            id="bootloaders",
            name="Device Bootloaders",
            description="Bootloaders for various Meshtastic-compatible devices",
            config_key="SAVE_BOOTLOADERS",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "Device Bootloaders"

    def get_description(self) -> str:
        return "Bootloaders for various Meshtastic-compatible devices"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the bootloader selection menu."""
        return self._run_bootloader_menu(config)

    def get_config_keys(self) -> List[str]:
        return [
            "SAVE_BOOTLOADERS",
            "SELECTED_BOOTLOADER_TYPES",
            "SELECTED_BOOTLOADER_ASSETS",
            "BOOTLOADER_VERSIONS_TO_KEEP",
        ]

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate bootloader configuration."""
        if not config.get("SAVE_BOOTLOADERS", False):
            return True  # Not enabled, so valid

        # Check if bootloader types are selected
        selected_types = config.get("SELECTED_BOOTLOADER_TYPES", [])
        return len(selected_types) > 0

    def get_download_info(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Get bootloader download information."""
        return {
            "enabled": config.get("SAVE_BOOTLOADERS", False),
            "selected_types": config.get("SELECTED_BOOTLOADER_TYPES", []),
            "versions_to_keep": config.get("BOOTLOADER_VERSIONS_TO_KEEP", 2),
        }

    def setup_additional_config(
        self, config: Dict[str, Any], is_first_run: bool
    ) -> Dict[str, Any]:
        """Setup additional bootloader-specific configuration."""
        # Determine default number of versions to keep based on platform
        from ..setup_config import is_termux

        default_versions_to_keep = 2 if is_termux() else 3

        # Prompt for number of versions to keep
        current_versions = config.get(
            "BOOTLOADER_VERSIONS_TO_KEEP", default_versions_to_keep
        )
        from fetchtastic.ui_utils import text_input

        bootloader_versions_to_keep = text_input(
            "How many versions of bootloaders would you like to keep?",
            default=str(current_versions),
        )

        if bootloader_versions_to_keep is None:
            print("Setup cancelled.")
            return config

        try:
            config["BOOTLOADER_VERSIONS_TO_KEEP"] = int(bootloader_versions_to_keep)
        except ValueError:
            print(f"Invalid number entered. Using default: {current_versions}")
            config["BOOTLOADER_VERSIONS_TO_KEEP"] = current_versions

        return config

    def _run_bootloader_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the bootloader selection menu using questionary."""
        from fetchtastic.ui_utils import (
            multi_select_with_preselection,
            show_preselection_info,
        )

        print("\n" + "=" * 60)
        print("Bootloader Selection")
        print("=" * 60)

        # Define available bootloader categories
        bootloader_categories = [
            {
                "id": "stock_bootloaders",
                "name": "Stock Device Bootloaders",
                "description": "Original bootloaders for specific devices (T1000-E, RAK4631)",
            },
            {
                "id": "otafix_bootloaders",
                "name": "OTA-Fix Modified Bootloaders",
                "description": "Modified Adafruit nRF52 bootloaders with OTA improvements",
            },
        ]

        # Create options and check for preselected items
        options = []
        preselected = []
        current_types = config.get("SELECTED_BOOTLOADER_TYPES", [])

        for category in bootloader_categories:
            option_text = f"{category['name']} - {category['description']}"
            options.append(option_text)

            # Check if this category is currently selected
            if category["id"] in current_types:
                preselected.append(option_text)

        try:
            # Show preselection info if any
            if preselected:
                show_preselection_info(preselected)

            selected_options = multi_select_with_preselection(
                message="Select bootloader types to download:",
                choices=options,
                preselected=preselected,
                min_selection=1,
            )

            if not selected_options:
                print("No bootloader types selected.")
                return None

            # Map selected options back to category IDs
            selected_types = []
            for selected_option in selected_options:
                for category in bootloader_categories:
                    expected_option = f"{category['name']} - {category['description']}"
                    if selected_option == expected_option:
                        selected_types.append(category["id"])
                        break

            print(
                f"\nSelected bootloader types: {', '.join([cat['name'] for cat in bootloader_categories if cat['id'] in selected_types])}"
            )

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

        # For each selected category, get specific selections
        selected_assets = {}

        for category_id in selected_types:
            category = next(
                cat for cat in bootloader_categories if cat["id"] == category_id
            )

            if category_id == "stock_bootloaders":
                assets = self._select_stock_bootloaders(config)
            elif category_id == "otafix_bootloaders":
                assets = self._select_otafix_bootloaders()
            else:
                continue

            if assets:
                selected_assets[category_id] = assets

        if not selected_assets:
            print("No specific bootloader assets selected.")
            return None

        return {
            "SELECTED_BOOTLOADER_TYPES": selected_types,
            "SELECTED_BOOTLOADER_ASSETS": selected_assets,
        }

    def _select_stock_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select stock bootloader assets using questionary."""
        from fetchtastic.ui_utils import (
            multi_select_with_preselection,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("Stock Device Bootloaders")
        print("=" * 40)

        # Define available stock bootloaders
        stock_options = [
            {
                "id": "t1000e",
                "name": "Seeed Studio T1000-E Tracker",
                "description": "Stock bootloader v0.9.1 (one-time download)",
            },
            {
                "id": "rak4631",
                "name": "RAK Wisblock 4631",
                "description": "Stock bootloader v0.4.3 (one-time download)",
            },
        ]

        # Create options and check for preselected items
        options = []
        preselected = []
        current_assets = config.get("SELECTED_BOOTLOADER_ASSETS", {})
        current_stock = current_assets.get("stock_bootloaders", [])

        for option in stock_options:
            option_text = f"{option['name']} - {option['description']}"
            options.append(option_text)

            # Check if this bootloader is currently selected
            if option["id"] in current_stock:
                preselected.append(option_text)

        try:
            # Show preselection info if any
            if preselected:
                show_preselection_info(preselected)

            selected_options = multi_select_with_preselection(
                message="Select stock bootloaders to download:",
                choices=options,
                preselected=preselected,
                min_selection=1,
            )

            if not selected_options:
                print("No stock bootloaders selected.")
                return []

            # Map selected options back to bootloader IDs
            selected_bootloaders = []
            for selected_option in selected_options:
                for option in stock_options:
                    expected_option = f"{option['name']} - {option['description']}"
                    if selected_option == expected_option:
                        selected_bootloaders.append(option["id"])
                        break

            print(
                f"\nSelected stock bootloaders: {', '.join([opt['name'] for opt in stock_options if opt['id'] in selected_bootloaders])}"
            )

            return selected_bootloaders

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return []

    def _select_otafix_bootloaders(self) -> List[str]:
        """Select OTA-fix bootloader assets."""
        from fetchtastic.ui_utils import confirm_prompt

        print("\n" + "=" * 40)
        print("OTA-Fix Modified Bootloaders")
        print("=" * 40)
        print(
            "This will download from: https://github.com/oltaco/Adafruit_nRF52_Bootloader_OTAFIX"
        )
        print("Includes README and setup instructions.")
        print()

        confirm = confirm_prompt("Download OTA-fix bootloaders?", default=True)
        if confirm:
            return ["otafix_all"]
        return []
