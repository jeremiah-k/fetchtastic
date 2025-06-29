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
        return self._run_bootloader_menu()

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

    def _run_bootloader_menu(self) -> Optional[Dict[str, Any]]:
        """Run the bootloader selection menu."""
        print("\n" + "=" * 60)
        print("Bootloader Selection")
        print("=" * 60)
        print("Select the types of bootloaders you want to download:")
        print()

        # Define available bootloader categories
        bootloader_categories = [
            {
                "id": "stock_bootloaders",
                "name": "Stock Device Bootloaders",
                "description": "Original bootloaders for specific devices (T1000-E, RAK4631)",
                "selected": False,
            },
            {
                "id": "otafix_bootloaders",
                "name": "OTA-Fix Modified Bootloaders",
                "description": "Modified Adafruit nRF52 bootloaders with OTA improvements",
                "selected": False,
            },
        ]

        # Display menu and get selections
        print("Use SPACE to select/deselect, ENTER to confirm:")
        print()

        current_selection = 0
        while True:
            # Clear screen and show menu
            print("\033[H\033[J", end="")  # Clear screen
            print("Bootloader Selection")
            print("=" * 60)
            print("Select the types of bootloaders you want to download:")
            print("Use SPACE to select/deselect, ENTER to confirm, Q to quit")
            print()

            for i, category in enumerate(bootloader_categories):
                marker = "●" if category["selected"] else "○"
                cursor = "→ " if i == current_selection else "  "
                print(f"{cursor}{marker} {category['name']}")
                print(f"    {category['description']}")
                print()

            print(
                "Navigation: ↑/↓ arrows, SPACE to toggle, ENTER to confirm, Q to quit"
            )

            # Get user input
            try:
                import sys
                import termios
                import tty

                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                tty.setraw(sys.stdin.fileno())
                key = sys.stdin.read(1)
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

                if key == "\x1b":  # Arrow key sequence
                    key += sys.stdin.read(2)
                    if key == "\x1b[A":  # Up arrow
                        current_selection = (current_selection - 1) % len(
                            bootloader_categories
                        )
                    elif key == "\x1b[B":  # Down arrow
                        current_selection = (current_selection + 1) % len(
                            bootloader_categories
                        )
                elif key == " ":  # Space to toggle
                    bootloader_categories[current_selection]["selected"] = (
                        not bootloader_categories[current_selection]["selected"]
                    )
                elif key == "\r" or key == "\n":  # Enter to confirm
                    break
                elif key.lower() == "q":  # Quit
                    return None

            except (ImportError, OSError):
                # Fallback for systems without termios (like Windows)
                print("\nFallback menu (termios not available):")
                for i, category in enumerate(bootloader_categories):
                    marker = "[X]" if category["selected"] else "[ ]"
                    print(f"{i+1}. {marker} {category['name']}")
                    print(f"   {category['description']}")

                choice = input(
                    "\nEnter numbers to toggle (e.g., '1 2'), or 'done' to finish: "
                ).strip()
                if choice.lower() == "done":
                    break
                elif choice.lower() == "q":
                    return None
                else:
                    try:
                        for num in choice.split():
                            idx = int(num) - 1
                            if 0 <= idx < len(bootloader_categories):
                                bootloader_categories[idx]["selected"] = (
                                    not bootloader_categories[idx]["selected"]
                                )
                    except ValueError:
                        print(
                            "Invalid input. Please enter numbers separated by spaces."
                        )
                        input("Press Enter to continue...")

        # Get selected categories
        selected_types = [cat["id"] for cat in bootloader_categories if cat["selected"]]

        if not selected_types:
            print("\nNo bootloader types selected.")
            return None

        print(
            f"\nSelected bootloader types: {', '.join([cat['name'] for cat in bootloader_categories if cat['selected']])}"
        )

        # For each selected category, get specific selections
        selected_assets = {}

        for category_id in selected_types:
            category = next(
                cat for cat in bootloader_categories if cat["id"] == category_id
            )

            if category_id == "stock_bootloaders":
                assets = self._select_stock_bootloaders()
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

    def _select_stock_bootloaders(self) -> List[str]:
        """Select stock bootloader assets."""
        print("\n" + "=" * 40)
        print("Stock Device Bootloaders")
        print("=" * 40)
        print("Available stock bootloaders:")
        print("1. Seeed Studio T1000-E Tracker")
        print("2. RAK Wisblock 4631")
        print()

        choice = input("Select bootloaders (1,2 or 'all'): ").strip().lower()

        assets = []
        if choice == "all" or "1" in choice:
            assets.append("t1000e_stock")
        if choice == "all" or "2" in choice:
            assets.append("rak4631_stock")

        return assets

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
