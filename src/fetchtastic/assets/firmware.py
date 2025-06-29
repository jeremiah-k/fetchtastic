# src/fetchtastic/assets/firmware.py

"""
Meshtastic firmware asset handler.
"""

from typing import Any, Dict, List, Optional

from .. import menu_firmware
from .base import AssetType, BaseAssetHandler


class MeshtasticFirmwareAsset(BaseAssetHandler):
    """Handler for Meshtastic firmware assets."""

    def __init__(self):
        asset_type = AssetType(
            id="firmware",
            name="Meshtastic Firmware",
            description="Official Meshtastic firmware releases from GitHub",
            config_key="SAVE_FIRMWARE",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "Meshtastic Firmware"

    def get_description(self) -> str:
        return "Official Meshtastic firmware releases from GitHub"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the firmware selection menu."""
        return menu_firmware.run_menu()

    def get_config_keys(self) -> List[str]:
        return [
            "SAVE_FIRMWARE",
            "SELECTED_FIRMWARE_ASSETS",
            "FIRMWARE_VERSIONS_TO_KEEP",
            "CHECK_PRERELEASES",
            "AUTO_EXTRACT",
            "EXTRACT_PATTERNS",
            "EXCLUDE_PATTERNS",
        ]

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate firmware configuration."""
        if not config.get("SAVE_FIRMWARE", False):
            return True  # Not enabled, so valid

        # Check if firmware assets are selected
        selected_assets = config.get("SELECTED_FIRMWARE_ASSETS", [])
        return len(selected_assets) > 0

    def get_download_info(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Get firmware download information."""
        return {
            "enabled": config.get("SAVE_FIRMWARE", False),
            "selected_assets": config.get("SELECTED_FIRMWARE_ASSETS", []),
            "versions_to_keep": config.get("FIRMWARE_VERSIONS_TO_KEEP", 2),
            "check_prereleases": config.get("CHECK_PRERELEASES", False),
            "auto_extract": config.get("AUTO_EXTRACT", False),
            "extract_patterns": config.get("EXTRACT_PATTERNS", []),
            "exclude_patterns": config.get("EXCLUDE_PATTERNS", []),
        }

    def setup_additional_config(
        self, config: Dict[str, Any], is_first_run: bool
    ) -> Dict[str, Any]:
        """Setup additional firmware-specific configuration."""
        # Determine default number of versions to keep based on platform
        from ..setup_config import is_termux

        default_versions_to_keep = 2 if is_termux() else 3

        # Prompt for number of versions to keep
        current_versions = config.get(
            "FIRMWARE_VERSIONS_TO_KEEP", default_versions_to_keep
        )
        if is_first_run:
            prompt_text = f"How many versions of the firmware would you like to keep? (default is {current_versions}): "
        else:
            prompt_text = f"How many versions of the firmware would you like to keep? (current: {current_versions}): "
        firmware_versions_to_keep = input(prompt_text).strip() or str(current_versions)
        config["FIRMWARE_VERSIONS_TO_KEEP"] = int(firmware_versions_to_keep)

        # Prompt for pre-release downloads
        check_prereleases_current = config.get("CHECK_PRERELEASES", False)
        check_prereleases_default = "yes" if check_prereleases_current else "no"
        check_prereleases = (
            input(
                f"Would you like to check for and download pre-release firmware from meshtastic.github.io? [y/n] (default: {check_prereleases_default}): "
            )
            .strip()
            .lower()
            or check_prereleases_default[0]
        )
        config["CHECK_PRERELEASES"] = check_prereleases == "y"

        # Prompt for automatic extraction
        auto_extract_current = config.get("AUTO_EXTRACT", False)
        auto_extract_default = "yes" if auto_extract_current else "no"
        auto_extract = (
            input(
                f"Would you like to automatically extract specific files from firmware zip archives? [y/n] (default: {auto_extract_default}): "
            )
            .strip()
            .lower()
            or auto_extract_default[0]
        )
        config["AUTO_EXTRACT"] = auto_extract == "y"

        if auto_extract == "y":
            self._setup_extraction_patterns(config)

        return config

    def _setup_extraction_patterns(self, config: Dict[str, Any]):
        """Setup extraction patterns for firmware."""
        print(
            "Enter the keywords to match for extraction from the firmware zip files, separated by spaces."
        )
        print(
            "Example: rak4631- tbeam t1000-e- tlora-v2-1-1_6- device- littlefs- bleota"
        )

        # Check if there are existing patterns
        if config.get("EXTRACT_PATTERNS"):
            current_patterns = " ".join(config.get("EXTRACT_PATTERNS", []))
            print(f"Current patterns: {current_patterns}")

            # Ask if user wants to keep or change patterns
            keep_patterns_default = "yes"
            keep_patterns = (
                input(
                    f"Do you want to keep the current extraction patterns? [y/n] (default: {keep_patterns_default}): "
                )
                .strip()
                .lower()
                or keep_patterns_default[0]
            )

            if keep_patterns == "y":
                # Keep existing patterns
                print(f"Keeping current extraction patterns: {current_patterns}")
            else:
                # Get new patterns
                extract_patterns = input("Enter new extraction patterns: ").strip()
                if extract_patterns:
                    config["EXTRACT_PATTERNS"] = extract_patterns.split()
                    print(f"Extraction patterns updated to: {extract_patterns}")
                else:
                    print("No patterns entered. Keeping current patterns.")
        else:
            # No existing patterns, get new ones
            extract_patterns = input("Extraction patterns: ").strip()
            if extract_patterns:
                config["EXTRACT_PATTERNS"] = extract_patterns.split()
                print(f"Extraction patterns set to: {extract_patterns}")
            else:
                config["AUTO_EXTRACT"] = False
                config["EXTRACT_PATTERNS"] = []
                print(
                    "No patterns selected, no files will be extracted. Run setup again if you wish to change this."
                )
                config["EXCLUDE_PATTERNS"] = []
                return

        # Setup exclude patterns if extraction is enabled
        if config.get("AUTO_EXTRACT", False) and config.get("EXTRACT_PATTERNS"):
            self._setup_exclude_patterns(config)

    def _setup_exclude_patterns(self, config: Dict[str, Any]):
        """Setup exclude patterns for firmware extraction."""
        exclude_default = "yes" if config.get("EXCLUDE_PATTERNS") else "no"
        exclude_prompt = f"Would you like to exclude any patterns from extraction? [y/n] (default: {exclude_default}): "
        exclude_choice = input(exclude_prompt).strip().lower() or exclude_default[0]

        if exclude_choice == "y":
            print("Enter the keywords to exclude from extraction, separated by spaces.")
            print("Example: .hex tcxo request s3-core")

            # Check if there are existing exclude patterns
            if config.get("EXCLUDE_PATTERNS"):
                current_excludes = " ".join(config.get("EXCLUDE_PATTERNS", []))
                print(f"Current exclude patterns: {current_excludes}")

                # Ask if user wants to keep or change exclude patterns
                keep_excludes_default = "yes"
                keep_excludes = (
                    input(
                        f"Do you want to keep the current exclude patterns? [y/n] (default: {keep_excludes_default}): "
                    )
                    .strip()
                    .lower()
                    or keep_excludes_default[0]
                )

                if keep_excludes == "y":
                    # Keep existing exclude patterns
                    current_excludes = " ".join(config.get("EXCLUDE_PATTERNS", []))
                    print(f"Keeping current exclude patterns: {current_excludes}")
                else:
                    # Get new exclude patterns
                    exclude_patterns = input("Enter new exclude patterns: ").strip()
                    if exclude_patterns:
                        config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                        print(f"Exclude patterns updated to: {exclude_patterns}")
                    else:
                        print("No exclude patterns entered. Keeping current patterns.")
            else:
                # No existing exclude patterns, get new ones
                exclude_patterns = input("Exclude patterns: ").strip()
                if exclude_patterns:
                    config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                    print(f"Exclude patterns set to: {exclude_patterns}")
                else:
                    config["EXCLUDE_PATTERNS"] = []
                    print("No exclude patterns set.")
        else:
            config["EXCLUDE_PATTERNS"] = []
