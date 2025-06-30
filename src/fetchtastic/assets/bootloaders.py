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
        if is_first_run:
            prompt_text = f"How many versions of bootloaders would you like to keep? (default is {current_versions}): "
        else:
            prompt_text = f"How many versions of bootloaders would you like to keep? (current: {current_versions}): "
        bootloader_versions_to_keep = input(prompt_text).strip() or str(
            current_versions
        )
        config["BOOTLOADER_VERSIONS_TO_KEEP"] = int(bootloader_versions_to_keep)

        return config

    def _run_bootloader_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the bootloader selection menu using pick."""
        from pick import pick

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

        # Create options for pick and check for preselected items
        options = []
        preselected = []
        current_types = config.get("SELECTED_BOOTLOADER_TYPES", [])

        for i, category in enumerate(bootloader_categories):
            option_text = f"{category['name']} - {category['description']}"
            options.append(option_text)

            # Check if this category is currently selected
            if category["id"] in current_types:
                preselected.append(i)

        try:
            from pick import pick

            # Use pick for multi-selection
            # Note: pick library doesn't support default_index with multiselect properly
            if preselected:
                print(
                    f"Current selections: {', '.join([options[i] for i in preselected])}"
                )

            result = pick(
                options,
                "Select bootloader types to download (SPACE to select, ENTER to confirm):",
                multiselect=True,
                min_selection_count=1,
            )

            # Handle different pick result formats
            if isinstance(result, tuple) and len(result) == 2:
                # Format: (selected_options, selected_indices)
                selected_options, selected_indices = result
            elif isinstance(result, list) and result and isinstance(result[0], tuple):
                # Format: [(option_text, index), (option_text, index), ...]
                selected_options = [item[0] for item in result]
                selected_indices = [item[1] for item in result]
            else:
                # Fallback - treat as list of options and find indices
                selected_options = result if isinstance(result, list) else [result]
                selected_indices = []
                for option in selected_options:
                    try:
                        idx = options.index(option)
                        selected_indices.append(idx)
                    except ValueError:
                        continue

            # Get selected category IDs
            selected_types = [bootloader_categories[i]["id"] for i in selected_indices]
            print(
                f"\nSelected bootloader types: {', '.join([bootloader_categories[i]['name'] for i in selected_indices])}"
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
        """Select stock bootloader assets using pick."""
        from pick import pick

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

        # Create options for pick and check for preselected items
        options = []
        preselected = []
        current_assets = config.get("SELECTED_BOOTLOADER_ASSETS", {})
        current_stock = current_assets.get("stock_bootloaders", [])

        for i, option in enumerate(stock_options):
            option_text = f"{option['name']} - {option['description']}"
            options.append(option_text)

            # Check if this bootloader is currently selected
            if option["id"] in current_stock:
                preselected.append(i)

        try:
            from pick import pick

            # Use pick for multi-selection
            # Note: pick library doesn't support default_index with multiselect properly
            if preselected:
                print(
                    f"Current selections: {', '.join([options[i] for i in preselected])}"
                )

            result = pick(
                options,
                "Select stock bootloaders to download (SPACE to select, ENTER to confirm):",
                multiselect=True,
                min_selection_count=1,
            )

            # Handle different pick result formats
            if isinstance(result, tuple) and len(result) == 2:
                # Format: (selected_options, selected_indices)
                selected_options, selected_indices = result
            elif isinstance(result, list) and result and isinstance(result[0], tuple):
                # Format: [(option_text, index), (option_text, index), ...]
                selected_options = [item[0] for item in result]
                selected_indices = [item[1] for item in result]
            else:
                # Fallback - treat as list of options and find indices
                selected_options = result if isinstance(result, list) else [result]
                selected_indices = []
                for option in selected_options:
                    try:
                        idx = options.index(option)
                        selected_indices.append(idx)
                    except ValueError:
                        continue

            # Get selected bootloader IDs
            selected_bootloaders = [stock_options[i]["id"] for i in selected_indices]
            print(
                f"\nSelected stock bootloaders: {', '.join([stock_options[i]['name'] for i in selected_indices])}"
            )

            return selected_bootloaders

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return []

    def _select_otafix_bootloaders(self) -> List[str]:
        """Select OTA-fix bootloader assets."""
        print("\n" + "=" * 40)
        print("OTA-Fix Modified Bootloaders")
        print("=" * 40)
        print(
            "This will download from: https://github.com/oltaco/Adafruit_nRF52_Bootloader_OTAFIX"
        )
        print("Includes README and setup instructions.")
        print()

        confirm = input("Download OTA-fix bootloaders? [y/n]: ").strip().lower()
        if confirm == "y":
            return ["otafix_all"]
        return []
