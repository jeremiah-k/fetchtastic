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
        """Run the DFU apps selection menu."""
        print("\n" + "=" * 60)
        print("DFU/Firmware Flashing Apps Selection")
        print("=" * 60)
        print("Select the flashing apps you want to download:")
        print()

        # Define available DFU apps
        dfu_apps = [
            {
                "id": "nordic_dfu",
                "name": "Nordic DFU Library APK",
                "description": "Android DFU library for flashing Nordic nRF devices",
                "repo": "NordicSemiconductor/Android-DFU-Library",
                "selected": False,
            }
        ]

        # Display menu and get selections
        print("Use SPACE to select/deselect, ENTER to confirm:")
        print()

        current_selection = 0
        while True:
            # Clear screen and show menu
            print("\033[H\033[J", end="")  # Clear screen
            print("DFU/Firmware Flashing Apps Selection")
            print("=" * 60)
            print("Select the flashing apps you want to download:")
            print("Use SPACE to select/deselect, ENTER to confirm, Q to quit")
            print()

            for i, app in enumerate(dfu_apps):
                marker = "●" if app["selected"] else "○"
                cursor = "→ " if i == current_selection else "  "
                print(f"{cursor}{marker} {app['name']}")
                print(f"    {app['description']}")
                print(f"    Repository: {app['repo']}")
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
                        current_selection = (current_selection - 1) % len(dfu_apps)
                    elif key == "\x1b[B":  # Down arrow
                        current_selection = (current_selection + 1) % len(dfu_apps)
                elif key == " ":  # Space to toggle
                    dfu_apps[current_selection]["selected"] = not dfu_apps[
                        current_selection
                    ]["selected"]
                elif key == "\r" or key == "\n":  # Enter to confirm
                    break
                elif key.lower() == "q":  # Quit
                    return None

            except (ImportError, OSError):
                # Fallback for systems without termios (like Windows)
                print("\nFallback menu (termios not available):")
                for i, app in enumerate(dfu_apps):
                    marker = "[X]" if app["selected"] else "[ ]"
                    print(f"{i+1}. {marker} {app['name']}")
                    print(f"   {app['description']}")
                    print(f"   Repository: {app['repo']}")

                choice = input(
                    "\nEnter numbers to toggle (e.g., '1'), or 'done' to finish: "
                ).strip()
                if choice.lower() == "done":
                    break
                elif choice.lower() == "q":
                    return None
                else:
                    try:
                        for num in choice.split():
                            idx = int(num) - 1
                            if 0 <= idx < len(dfu_apps):
                                dfu_apps[idx]["selected"] = not dfu_apps[idx][
                                    "selected"
                                ]
                    except ValueError:
                        print(
                            "Invalid input. Please enter numbers separated by spaces."
                        )
                        input("Press Enter to continue...")

        # Get selected apps
        selected_apps = [app["id"] for app in dfu_apps if app["selected"]]

        if not selected_apps:
            print("\nNo DFU apps selected.")
            return None

        print(
            f"\nSelected DFU apps: {', '.join([app['name'] for app in dfu_apps if app['selected']])}"
        )

        return {"selected_apps": selected_apps}
