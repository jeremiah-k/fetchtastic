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
            "SELECTED_BOOTLOADER_BRANDS",  # Legacy key for backward compatibility
            "SELECTED_BOOTLOADER_TYPES",  # New key for category selection (stock vs modified)
            "SELECTED_BOOTLOADER_ASSETS",  # Updated to store by category
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
        """Run the bootloader selection menu using questionary with brand/model organization."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 60)
        print("Bootloader Selection")
        print("=" * 60)

        # Define available bootloader categories with enhanced information
        bootloader_categories = [
            {
                "title": "Stock Device Bootloaders",
                "value": "stock_bootloaders",
                "description": "Original bootloaders for specific devices (T1000-E, RAK4631) - one-time downloads",
            },
            {
                "title": "Modified Bootloaders (OTA-fix)",
                "value": "modified_bootloaders",
                "description": "Enhanced Adafruit nRF52 bootloaders with OTA improvements - version tracked",
            },
        ]

        # Get preselected categories from config
        current_categories = config.get("SELECTED_BOOTLOADER_TYPES", [])
        preselected_categories = [
            category
            for category in current_categories
            if category in [b["value"] for b in bootloader_categories]
        ]

        try:
            # Show preselection info if any
            if preselected_categories:
                category_names = [
                    b["title"]
                    for b in bootloader_categories
                    if b["value"] in preselected_categories
                ]
                show_preselection_info(category_names)

            selected_categories = multi_select_with_info(
                message="Select bootloader types to configure:",
                choices=bootloader_categories,
                preselected=preselected_categories,
                min_selection=1,
            )

            if not selected_categories:
                print("No bootloader types selected.")
                return None

            print(
                f"\nSelected bootloader types: {', '.join([b['title'] for b in bootloader_categories if b['value'] in selected_categories])}"
            )

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

        # For each selected category, get specific selections
        selected_assets = {}

        for category_id in selected_categories:
            category = next(
                b for b in bootloader_categories if b["value"] == category_id
            )

            if category_id == "stock_bootloaders":
                assets = self._select_stock_bootloaders(config)
            elif category_id == "modified_bootloaders":
                assets = self._select_modified_bootloaders(config)
            else:
                continue

            if assets:
                selected_assets[category_id] = assets

        if not selected_assets:
            print("No specific bootloader assets selected.")
            return None

        return {
            "SELECTED_BOOTLOADER_TYPES": selected_categories,
            "SELECTED_BOOTLOADER_ASSETS": selected_assets,
        }

    def _select_stock_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select stock device bootloaders."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("Stock Device Bootloaders")
        print("=" * 40)

        # Define available stock bootloaders with enhanced information
        stock_options = [
            {
                "title": "Seeed T1000-E Tracker",
                "value": "t1000e_stock",
                "description": "Original bootloader for Seeed Card Tracker T1000-E (one-time download)",
            },
            {
                "title": "RAK4631 WisBlock Core",
                "value": "rak4631_stock",
                "description": "Original bootloader for RAK4631 WisBlock Core module (one-time download)",
            },
            {
                "title": "XIAO nRF52840 (Original)",
                "value": "xiao_nrf52840_stock",
                "description": "Original Seeed XIAO nRF52840 bootloader (one-time download)",
            },
        ]

        # Get preselected stock bootloaders from config
        current_stock = config.get("SELECTED_BOOTLOADER_ASSETS", {}).get(
            "stock_bootloaders", []
        )

        try:
            # Show preselection info if any
            if current_stock:
                show_preselection_info(current_stock)

            selected_stock = multi_select_with_info(
                message="Select stock device bootloaders:",
                choices=stock_options,
                preselected=current_stock,
                min_selection=1,
            )

            if not selected_stock:
                print("No stock bootloaders selected.")
                return []

            print(f"\nSelected stock bootloaders: {', '.join(selected_stock)}")
            return selected_stock

        except (KeyboardInterrupt, EOFError):
            print("\nStock bootloader selection cancelled.")
            return []

    def _select_modified_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select modified (OTA-fix) bootloaders with pattern-based selection."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("Modified Bootloaders (OTA-fix)")
        print("=" * 40)

        # Define common OTA-fix bootloader patterns with descriptions
        pattern_options = [
            {
                "title": "All RAK4631 Bootloaders",
                "value": ".*rak4631.*",
                "description": "Enhanced RAK4631 bootloaders with OTA improvements (hex, zip, uf2)",
            },
            {
                "title": "All XIAO nRF52840 Bootloaders",
                "value": ".*xiao_nrf52840.*",
                "description": "Enhanced XIAO nRF52840 bootloaders (regular and sense variants)",
            },
            {
                "title": "All ProMicro nRF52840 Bootloaders",
                "value": ".*promicro_nrf52840.*",
                "description": "Enhanced ProMicro nRF52840 bootloaders for DIY builds",
            },
            {
                "title": "UF2 Update Files Only",
                "value": ".*\\.uf2$",
                "description": "UF2 update files for direct flashing via USB mass storage",
            },
            {
                "title": "HEX Files Only",
                "value": ".*\\.hex$",
                "description": "HEX files for programming with external tools",
            },
            {
                "title": "ZIP Archives Only",
                "value": ".*\\.zip$",
                "description": "ZIP archive files containing multiple bootloader variants",
            },
            {
                "title": "All Enhanced Bootloaders",
                "value": ".*",
                "description": "All available enhanced bootloader files with OTA improvements",
            },
        ]

        # Get preselected modified bootloaders from config
        current_modified = config.get("SELECTED_BOOTLOADER_ASSETS", {}).get(
            "modified_bootloaders", []
        )

        try:
            # Show preselection info if any
            if current_modified:
                show_preselection_info(current_modified)

            selected_modified = multi_select_with_info(
                message="Select modified bootloader patterns:",
                choices=pattern_options,
                preselected=current_modified,
                min_selection=1,
            )

            if not selected_modified:
                print("No modified bootloader patterns selected.")
                return []

            print(f"\nSelected patterns: {', '.join(selected_modified)}")
            return selected_modified

        except (KeyboardInterrupt, EOFError):
            print("\nModified bootloader selection cancelled.")
            return []

    def _select_rak_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select RAK Wireless bootloader assets using questionary."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("RAK Wireless Bootloaders")
        print("=" * 40)

        # Define available RAK bootloaders with enhanced information
        rak_options = [
            {
                "title": "RAK4631 WisBlock Core",
                "value": "rak4631",
                "description": "Stock bootloader v0.4.3 for RAK4631 WisBlock Core module (one-time download)",
            },
            {
                "title": "RAK11200 WisBlock Core",
                "value": "rak11200",
                "description": "Stock bootloader for RAK11200 ESP32 WisBlock Core (one-time download)",
            },
        ]

        # Get preselected RAK bootloaders from config
        current_assets = config.get("SELECTED_BOOTLOADER_ASSETS", {})
        current_rak = current_assets.get("rak_wireless", [])
        preselected_rak = [
            opt["value"] for opt in rak_options if opt["value"] in current_rak
        ]

        try:
            # Show preselection info if any
            if preselected_rak:
                preselected_names = [
                    opt["title"]
                    for opt in rak_options
                    if opt["value"] in preselected_rak
                ]
                show_preselection_info(preselected_names)

            selected_bootloaders = multi_select_with_info(
                message="Select RAK Wireless bootloaders to download:",
                choices=rak_options,
                preselected=preselected_rak,
                min_selection=1,
            )

            if not selected_bootloaders:
                print("No RAK bootloaders selected.")
                return []

            print(
                f"\nSelected RAK bootloaders: {', '.join([opt['title'] for opt in rak_options if opt['value'] in selected_bootloaders])}"
            )

            return selected_bootloaders

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return []

    def _select_seeed_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select Seeed Studio bootloader assets using questionary."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("Seeed Studio Bootloaders")
        print("=" * 40)

        # Define available Seeed bootloaders with enhanced information
        seeed_options = [
            {
                "title": "T1000-E Tracker",
                "value": "t1000e",
                "description": "Stock bootloader v0.9.1 for T1000-E GPS tracker (one-time download)",
            },
            {
                "title": "XIAO nRF52840",
                "value": "xiao_nrf52840",
                "description": "Stock bootloader for XIAO nRF52840 development board (one-time download)",
            },
            {
                "title": "XIAO nRF52840 Sense",
                "value": "xiao_nrf52840_sense",
                "description": "Stock bootloader for XIAO nRF52840 Sense with sensors (one-time download)",
            },
        ]

        # Get preselected Seeed bootloaders from config
        current_assets = config.get("SELECTED_BOOTLOADER_ASSETS", {})
        current_seeed = current_assets.get("seeed_studio", [])
        preselected_seeed = [
            opt["value"] for opt in seeed_options if opt["value"] in current_seeed
        ]

        try:
            # Show preselection info if any
            if preselected_seeed:
                preselected_names = [
                    opt["title"]
                    for opt in seeed_options
                    if opt["value"] in preselected_seeed
                ]
                show_preselection_info(preselected_names)

            selected_bootloaders = multi_select_with_info(
                message="Select Seeed Studio bootloaders to download:",
                choices=seeed_options,
                preselected=preselected_seeed,
                min_selection=1,
            )

            if not selected_bootloaders:
                print("No Seeed bootloaders selected.")
                return []

            print(
                f"\nSelected Seeed bootloaders: {', '.join([opt['title'] for opt in seeed_options if opt['value'] in selected_bootloaders])}"
            )

            return selected_bootloaders

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return []

    def _select_diy_bootloaders(self, config: Dict[str, Any]) -> List[str]:
        """Select DIY/Modified bootloader assets using questionary with release selection."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("DIY/Modified Bootloaders")
        print("=" * 40)

        # Define available DIY/Modified bootloaders with enhanced information
        diy_options = [
            {
                "title": "OTA-Fix Bootloaders (All Variants)",
                "value": "otafix_all",
                "description": "Modified Adafruit nRF52 bootloaders with OTA improvements - tracks versions from repository",
            },
            {
                "title": "OTA-Fix Bootloaders (Select Specific)",
                "value": "otafix_specific",
                "description": "Choose specific OTA-fix bootloader variants and releases",
            },
        ]

        # Get preselected DIY bootloaders from config
        current_assets = config.get("SELECTED_BOOTLOADER_ASSETS", {})
        current_diy = current_assets.get("diy_modified", [])
        preselected_diy = [
            opt["value"] for opt in diy_options if opt["value"] in current_diy
        ]

        try:
            # Show preselection info if any
            if preselected_diy:
                preselected_names = [
                    opt["title"]
                    for opt in diy_options
                    if opt["value"] in preselected_diy
                ]
                show_preselection_info(preselected_names)

            selected_bootloaders = multi_select_with_info(
                message="Select DIY/Modified bootloaders to download:",
                choices=diy_options,
                preselected=preselected_diy,
                min_selection=1,
            )

            if not selected_bootloaders:
                print("No DIY bootloaders selected.")
                return []

            print(
                f"\nSelected DIY bootloaders: {', '.join([opt['title'] for opt in diy_options if opt['value'] in selected_bootloaders])}"
            )

            # If specific selection is chosen, allow pattern-based selection
            if "otafix_specific" in selected_bootloaders:
                specific_patterns = self._select_otafix_patterns(config)
                if specific_patterns:
                    # Replace the generic selection with specific patterns
                    selected_bootloaders = [
                        item
                        for item in selected_bootloaders
                        if item != "otafix_specific"
                    ]
                    selected_bootloaders.extend(specific_patterns)

            return selected_bootloaders

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return []

    def _select_otafix_patterns(self, config: Dict[str, Any]) -> List[str]:
        """Select specific OTA-fix bootloader patterns using regex-based selection."""
        from fetchtastic.setup_config import get_asset_patterns
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print("OTA-Fix Bootloader Pattern Selection")
        print("=" * 40)

        # Get current patterns from config
        current_patterns = get_asset_patterns(config, "bootloaders")

        # Define common OTA-fix bootloader patterns with descriptions
        pattern_options = [
            {
                "title": "All RAK4631 Bootloaders",
                "value": ".*rak4631.*",
                "description": "Matches all RAK4631 bootloader files (hex, zip, uf2)",
            },
            {
                "title": "All XIAO nRF52840 Bootloaders",
                "value": ".*xiao_nrf52840.*",
                "description": "Matches all XIAO nRF52840 bootloader files (regular and sense)",
            },
            {
                "title": "All ProMicro nRF52840 Bootloaders",
                "value": ".*promicro_nrf52840.*",
                "description": "Matches all ProMicro nRF52840 bootloader files",
            },
            {
                "title": "UF2 Update Files Only",
                "value": ".*\\.uf2$",
                "description": "Matches only UF2 update files for direct flashing",
            },
            {
                "title": "HEX Files Only",
                "value": ".*\\.hex$",
                "description": "Matches only HEX files for programming",
            },
            {
                "title": "ZIP Archives Only",
                "value": ".*\\.zip$",
                "description": "Matches only ZIP archive files",
            },
            {
                "title": "All Bootloader Files",
                "value": ".*",
                "description": "Matches all available bootloader files",
            },
        ]

        try:
            # Show preselection info if any
            if current_patterns:
                show_preselection_info(current_patterns)

            selected_patterns = multi_select_with_info(
                message="Select bootloader file patterns to download:",
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
