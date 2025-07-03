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
            description="Official Meshtastic firmware releases for all supported devices (ESP32, nRF52, RP2040)",
            config_key="SAVE_FIRMWARE",
        )
        super().__init__(asset_type)

    def get_display_name(self) -> str:
        return "Meshtastic Firmware"

    def get_description(self) -> str:
        return "Meshtastic firmware releases from GitHub"

    def run_selection_menu(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run the firmware selection menu with system choice."""
        return self._run_firmware_system_menu(config)

    def get_config_keys(self) -> List[str]:
        return [
            "SAVE_FIRMWARE",
            "SELECTED_FIRMWARE_ASSETS",  # Legacy key for pattern-based system
            "FIRMWARE_SYSTEM",  # New key for system selection (new vs legacy)
            "SELECTED_FIRMWARE_MANUFACTURERS",  # New key for manufacturer selection
            "SELECTED_FIRMWARE_DEVICES",  # New key for device selection
            "FIRMWARE_VERSIONS_TO_KEEP",
            "CHECK_PRERELEASES",
            "AUTO_EXTRACT",
            "EXTRACT_PATTERNS",
            "EXCLUDE_PATTERNS",
        ]

    def _run_firmware_system_menu(
        self, config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run firmware system selection menu (new vs legacy)."""
        from fetchtastic.ui_utils import single_select_with_info

        print("\n" + "=" * 60)
        print("Firmware Download System Selection")
        print("=" * 60)

        # Define firmware system options with comprehensive descriptions
        system_options = [
            {
                "title": "API-Based System",
                "value": "api_based",
                "description": "Organize by manufacturer and device model using official Meshtastic hardware list (recommended for most users)",
            },
            {
                "title": "Legacy Pattern System",
                "value": "legacy_system",
                "description": "Use existing regex pattern-based firmware selection system (advanced users and custom builds)",
            },
        ]

        # Get current system preference from config (default to API-based system)
        current_system = config.get("FIRMWARE_SYSTEM", "api_based")

        try:
            selected_system = single_select_with_info(
                message="Choose firmware download system:",
                choices=system_options,
                default=None,  # No default highlighting
            )

            if not selected_system:
                print("No system selected.")
                return None

            if selected_system == "api_based":
                return self._run_api_based_firmware_menu(config)
            else:
                return self._run_legacy_firmware_menu(config)

        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

    def _run_legacy_firmware_menu(
        self, config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run the legacy firmware selection menu."""
        # Get preselected patterns from config
        preselected_patterns = config.get("SELECTED_FIRMWARE_ASSETS", [])
        result = menu_firmware.run_menu(preselected_patterns)

        if result:
            # Ensure the system preference is saved
            result["FIRMWARE_SYSTEM"] = "legacy_system"

        return result

    def _run_api_based_firmware_menu(
        self, config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Run the API-based hardware firmware selection menu."""
        import requests

        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 60)
        print("Hardware-Based Firmware Selection")
        print("=" * 60)

        try:
            # Fetch hardware list from Meshtastic API with robust error handling
            print("Fetching hardware list...")

            # Try web flasher first (most reliable), then other sources
            hardware_urls = [
                "https://raw.githubusercontent.com/meshtastic/web-flasher/refs/heads/main/public/data/hardware-list.json",
                "https://raw.githubusercontent.com/meshtastic/meshtastic/refs/heads/master/protobufs/meshtastic/hardware.json",
            ]

            hardware_list = None
            for url in hardware_urls:
                try:
                    # Create session with retry strategy for network resilience
                    import requests
                    from requests.adapters import HTTPAdapter
                    from urllib3.util.retry import Retry

                    session = requests.Session()
                    retry_strategy = Retry(
                        total=2,
                        connect=2,
                        backoff_factor=0.5,
                        status_forcelist=[502, 503, 504, 429],
                    )
                    adapter = HTTPAdapter(max_retries=retry_strategy)
                    session.mount("https://", adapter)

                    response = session.get(url, timeout=5)
                    response.raise_for_status()
                    hardware_list = response.json()
                    print(f"Successfully fetched hardware list from {url}")
                    break
                except (
                    requests.exceptions.RequestException,
                    requests.exceptions.Timeout,
                ) as e:
                    print(f"Failed to fetch from {url}: {e}")
                    continue
                except Exception as e:
                    print(f"Unexpected error fetching from {url}: {e}")
                    continue

            if not hardware_list:
                print(
                    "Failed to fetch hardware list from all sources. Using offline fallback."
                )
                # Provide a minimal fallback for essential devices
                hardware_list = [
                    {
                        "hwModel": 9,
                        "hwModelSlug": "RAK4631",
                        "platformioTarget": "rak4631",
                        "architecture": "nrf52840",
                        "activelySupported": True,
                        "supportLevel": 1,
                        "displayName": "RAK WisBlock 4631",
                        "tags": ["RAK"],
                    },
                    {
                        "hwModel": 4,
                        "hwModelSlug": "TBEAM",
                        "platformioTarget": "tbeam",
                        "architecture": "esp32",
                        "activelySupported": True,
                        "supportLevel": 1,
                        "displayName": "LILYGO T-Beam",
                        "tags": ["LilyGo"],
                    },
                    {
                        "hwModel": 7,
                        "hwModelSlug": "T_ECHO",
                        "platformioTarget": "t-echo",
                        "architecture": "nrf52840",
                        "activelySupported": True,
                        "supportLevel": 1,
                        "displayName": "LILYGO T-Echo",
                        "tags": ["LilyGo"],
                    },
                ]

            # Group devices by manufacturer tags
            manufacturers = {}
            for device in hardware_list:
                if not device.get("activelySupported", False):
                    continue  # Skip unsupported devices

                tags = device.get("tags", ["Other"])
                for tag in tags:
                    if tag not in manufacturers:
                        manufacturers[tag] = []
                    manufacturers[tag].append(device)

            # Create manufacturer selection options
            manufacturer_options = []
            for manufacturer in sorted(manufacturers.keys()):
                device_count = len(manufacturers[manufacturer])
                manufacturer_options.append(
                    {
                        "title": manufacturer,
                        "value": manufacturer,
                        "description": f"{device_count} supported devices",
                    }
                )

            # Get preselected manufacturers from config
            current_manufacturers = config.get("SELECTED_FIRMWARE_MANUFACTURERS", [])

            # Show preselection info if any
            if current_manufacturers:
                show_preselection_info(current_manufacturers)

            # Use shopping cart style selection
            firmware_targets = self._run_manufacturer_shopping_cart(
                manufacturers, config
            )

            if not firmware_targets:
                print("No devices selected.")
                print("Falling back to legacy pattern system...")
                return self._run_legacy_firmware_menu(config)

            # Ask about additional utility files
            include_erase_files = self._ask_for_erase_files()

            result = {
                "FIRMWARE_SYSTEM": "api_based",
                "SELECTED_FIRMWARE_TARGETS": firmware_targets,
            }

            if include_erase_files:
                result["INCLUDE_ERASE_FILES"] = True

            return result

        except requests.RequestException as e:
            print(f"Error fetching hardware list: {e}")
            print("Falling back to legacy system...")
            return self._run_legacy_firmware_menu(config)
        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.")
            return None

    def _run_manufacturer_shopping_cart(
        self, manufacturers: Dict[str, List[Dict]], config: Dict[str, Any]
    ) -> List[str]:
        """
        Run shopping cart style manufacturer/device selection.

        Returns:
            List of selected firmware targets
        """
        from fetchtastic.ui_utils import single_select_with_info

        # Track selected devices across all manufacturers
        selected_targets = []

        # Get existing selections from config
        existing_targets = config.get("SELECTED_FIRMWARE_TARGETS", [])
        if existing_targets:
            selected_targets = existing_targets.copy()

        while True:
            # Build manufacturer menu with current selections
            manufacturer_choices = []
            for manufacturer, devices in manufacturers.items():
                # Count how many devices are selected for this manufacturer
                manufacturer_targets = [
                    device["platformioTarget"]
                    for device in devices
                    if device["platformioTarget"] in selected_targets
                ]
                count = len(manufacturer_targets)

                if count > 0:
                    title = f"{manufacturer} ({count} selected)"
                    description = f"Selected: {', '.join(manufacturer_targets)}"
                else:
                    title = f"{manufacturer} (0 selected)"
                    description = f"{len(devices)} available devices"

                manufacturer_choices.append(
                    {
                        "title": title,
                        "value": manufacturer,
                        "description": description,
                    }
                )

            # Add control options
            manufacturer_choices.extend(
                [
                    {
                        "title": "--- Actions ---",
                        "value": "separator",
                        "description": "",
                    },
                    {
                        "title": f"Finish Selection ({len(selected_targets)} devices total)",
                        "value": "finish",
                        "description": (
                            "Continue with selected devices"
                            if selected_targets
                            else "No devices selected - will use legacy system"
                        ),
                    },
                    {
                        "title": "Cancel",
                        "value": "cancel",
                        "description": "Cancel firmware configuration",
                    },
                ]
            )

            print("\n" + "=" * 60)
            print("Device Selection - Shopping Cart")
            print("=" * 60)
            if selected_targets:
                print(f"Currently selected: {', '.join(selected_targets)}")
            else:
                print("No devices selected yet")

            try:
                choice = single_select_with_info(
                    message="Select manufacturer to configure or choose action:",
                    choices=manufacturer_choices,
                    default=None,
                )

                if choice is None or choice == "cancel":
                    print("\nSelection cancelled.")
                    return []
                elif choice == "finish":
                    return selected_targets
                elif choice == "separator":
                    continue  # Ignore separator selection
                else:
                    # User selected a manufacturer - enter device selection
                    manufacturer_devices = manufacturers[choice]
                    updated_targets = self._select_devices_for_manufacturer(
                        choice, manufacturer_devices, selected_targets
                    )
                    if updated_targets is not None:
                        selected_targets = updated_targets
                    # Continue loop to show updated main menu

            except KeyboardInterrupt:
                print("\nSelection cancelled.")
                return []

    def _select_devices_for_manufacturer(
        self, manufacturer: str, devices: List[Dict], current_targets: List[str]
    ) -> List[str]:
        """
        Select devices for a specific manufacturer.

        Returns:
            Updated list of all selected targets, or None if cancelled
        """
        from fetchtastic.ui_utils import multi_select_with_info

        # Build device options
        device_options = []
        for device in devices:
            target = device["platformioTarget"]
            arch = device.get("architecture", "unknown")
            title = f"{device['displayName']} - {target}"
            description = f"Target: {target} | Arch: {arch}"

            device_options.append(
                {
                    "title": title,
                    "value": target,
                    "description": description,
                }
            )

        # Find currently selected devices for this manufacturer
        manufacturer_targets = [
            device["platformioTarget"]
            for device in devices
            if device["platformioTarget"] in current_targets
        ]

        print(f"\n" + "=" * 40)
        print(f"{manufacturer} Device Selection")
        print("=" * 40)
        if manufacturer_targets:
            print(f"Currently selected: {', '.join(manufacturer_targets)}")
        else:
            print("No devices selected for this manufacturer")

        try:
            selected_devices = multi_select_with_info(
                message=f"Select {manufacturer} devices (space to select, enter when done):",
                choices=device_options,
                preselected=manufacturer_targets,
                min_selection=0,
            )

            if selected_devices is None:
                # User cancelled
                return None

            # Update the global target list
            updated_targets = current_targets.copy()

            # Remove all previous selections for this manufacturer
            for device in devices:
                if device["platformioTarget"] in updated_targets:
                    updated_targets.remove(device["platformioTarget"])

            # Add new selections for this manufacturer
            updated_targets.extend(selected_devices)

            return updated_targets

        except KeyboardInterrupt:
            return None

    def _ask_for_erase_files(self) -> bool:
        """Ask user if they want to include factory erase files for nRF52 devices."""
        from fetchtastic.ui_utils import confirm_prompt

        print("\n" + "=" * 60)
        print("Additional Utility Files")
        print("=" * 60)
        print("Factory erase files can be used to completely reset nRF52 devices.")
        print(
            "These files are useful for troubleshooting or preparing devices for fresh installs."
        )

        try:
            include_erase = confirm_prompt(
                "Include factory erase files for nRF52 devices?",
                default=True,
            )
            return include_erase if include_erase is not None else False
        except KeyboardInterrupt:
            return False

    def _select_manufacturer_devices(
        self, manufacturer: str, devices: List[Dict], config: Dict[str, Any]
    ) -> List[str]:
        """Select specific devices for a manufacturer."""
        from fetchtastic.ui_utils import (
            multi_select_with_info,
            show_preselection_info,
        )

        print("\n" + "=" * 40)
        print(f"{manufacturer} Device Selection")
        print("=" * 40)

        # Create device selection options
        device_options = []
        for device in devices:
            display_name = device.get("displayName", "Unknown Device")
            platform_target = device.get("platformioTarget", "unknown")
            architecture = device.get("architecture", "unknown")
            support_level = device.get("supportLevel", "unknown")

            description = f"Target: {platform_target} | Arch: {architecture} | Support: {support_level}"

            device_options.append(
                {
                    "title": display_name,
                    "value": platform_target,
                    "description": description,
                }
            )

        # Get preselected devices from config
        current_devices = config.get("SELECTED_FIRMWARE_DEVICES", {}).get(
            manufacturer, []
        )

        try:
            # Show preselection info if any
            if current_devices:
                show_preselection_info(current_devices)

            selected_devices = multi_select_with_info(
                message=f"Select {manufacturer} devices:",
                choices=device_options,
                preselected=current_devices,
                min_selection=0,  # Allow no selection
            )

            if not selected_devices:
                print(f"No {manufacturer} devices selected.")
                return []

            print(f"\nSelected {manufacturer} devices: {', '.join(selected_devices)}")
            return selected_devices

        except (KeyboardInterrupt, EOFError):
            print("\nDevice selection cancelled.")
            return []

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
        from fetchtastic.ui_utils import confirm_prompt, text_input

        firmware_versions_to_keep = text_input(
            "How many versions of the firmware would you like to keep?",
            default=str(current_versions),
        )

        if firmware_versions_to_keep is None:
            print("Setup cancelled.")
            return config

        try:
            config["FIRMWARE_VERSIONS_TO_KEEP"] = int(firmware_versions_to_keep)
        except ValueError:
            print(f"Invalid number entered. Using default: {current_versions}")
            config["FIRMWARE_VERSIONS_TO_KEEP"] = current_versions

        # Prompt for pre-release downloads
        check_prereleases_current = config.get("CHECK_PRERELEASES", False)
        check_prereleases = confirm_prompt(
            "Would you like to check for and download pre-release firmware from meshtastic.github.io?",
            default=check_prereleases_current,
        )

        if check_prereleases is None:
            print("Setup cancelled.")
            return config

        config["CHECK_PRERELEASES"] = check_prereleases

        # Prompt for automatic extraction
        auto_extract_current = config.get("AUTO_EXTRACT", False)
        auto_extract = confirm_prompt(
            "Would you like to automatically extract specific files from firmware zip archives?",
            default=auto_extract_current,
        )

        if auto_extract is None:
            print("Setup cancelled.")
            return config

        config["AUTO_EXTRACT"] = auto_extract

        if auto_extract:
            self._setup_extraction_patterns(config)

        return config

    def _setup_extraction_patterns(self, config: Dict[str, Any]):
        """Setup extraction patterns for firmware."""
        from fetchtastic.ui_utils import text_input

        example_text = (
            "Example: rak4631- tbeam t1000-e- tlora-v2-1-1_6- device- littlefs- bleota"
        )

        # Check if there are existing patterns
        if config.get("EXTRACT_PATTERNS"):
            current_patterns = " ".join(config.get("EXTRACT_PATTERNS", []))
            print(f"Current patterns: {current_patterns}")

            # Ask if user wants to keep or change patterns
            from fetchtastic.ui_utils import confirm_prompt

            keep_patterns = confirm_prompt(
                "Do you want to keep the current extraction patterns?", default=True
            )

            if keep_patterns is None:
                print("Setup cancelled.")
                return config
            elif keep_patterns:
                # Keep existing patterns
                print(f"Keeping current extraction patterns: {current_patterns}")
            else:
                # Get new patterns with proper questionary styling
                prompt_message = f"Enter new extraction patterns:\n{example_text}"
                extract_patterns = text_input(prompt_message)

                if extract_patterns is None:
                    print("Setup cancelled.")
                    return config
                elif extract_patterns.strip():
                    config["EXTRACT_PATTERNS"] = extract_patterns.strip().split()
                    print(f"Extraction patterns updated to: {extract_patterns}")
                else:
                    print("No patterns entered. Keeping current patterns.")
        else:
            # No existing patterns, get new ones with proper questionary styling
            prompt_message = f"Extraction patterns:\n{example_text}"
            extract_patterns = text_input(prompt_message)

            if extract_patterns is None:
                print("Setup cancelled.")
                return config
            elif extract_patterns.strip():
                config["EXTRACT_PATTERNS"] = extract_patterns.strip().split()
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
        from fetchtastic.ui_utils import confirm_prompt, text_input

        exclude_default = bool(config.get("EXCLUDE_PATTERNS"))
        exclude_choice = confirm_prompt(
            "Would you like to exclude any patterns from extraction?",
            default=exclude_default,
        )

        if exclude_choice is None:
            print("Setup cancelled.")
            return config
        elif exclude_choice:
            exclude_example = "Example: .hex tcxo request s3-core"

            # Check if there are existing exclude patterns
            if config.get("EXCLUDE_PATTERNS"):
                current_excludes = " ".join(config.get("EXCLUDE_PATTERNS", []))
                print(f"Current exclude patterns: {current_excludes}")

                # Ask if user wants to keep or change exclude patterns
                keep_excludes = confirm_prompt(
                    "Do you want to keep the current exclude patterns?", default=True
                )

                if keep_excludes is None:
                    print("Setup cancelled.")
                    return config
                elif keep_excludes:
                    # Keep existing exclude patterns
                    current_excludes = " ".join(config.get("EXCLUDE_PATTERNS", []))
                    print(f"Keeping current exclude patterns: {current_excludes}")
                else:
                    # Get new exclude patterns with proper questionary styling
                    prompt_message = f"Enter new exclude patterns:\n{exclude_example}"
                    exclude_patterns = text_input(prompt_message)

                    if exclude_patterns is None:
                        print("Setup cancelled.")
                        return config
                    elif exclude_patterns.strip():
                        config["EXCLUDE_PATTERNS"] = exclude_patterns.strip().split()
                        print(f"Exclude patterns updated to: {exclude_patterns}")
                    else:
                        print("No exclude patterns entered. Keeping current patterns.")
            else:
                # No existing exclude patterns, get new ones with proper questionary styling
                prompt_message = f"Exclude patterns:\n{exclude_example}"
                exclude_patterns = text_input(prompt_message)

                if exclude_patterns is None:
                    print("Setup cancelled.")
                    return config
                elif exclude_patterns.strip():
                    config["EXCLUDE_PATTERNS"] = exclude_patterns.strip().split()
                    print(f"Exclude patterns set to: {exclude_patterns}")
                else:
                    config["EXCLUDE_PATTERNS"] = []
                    print("No exclude patterns set.")
        else:
            config["EXCLUDE_PATTERNS"] = []
