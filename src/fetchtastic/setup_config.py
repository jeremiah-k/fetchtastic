# src/fetchtastic/setup_config.py

import getpass
import os
import platform
import random
import re
import shutil
import string
import subprocess
import sys
from datetime import datetime
from typing import Callable, Optional, Sequence, Set

import platformdirs
import yaml

from fetchtastic import menu_apk, menu_firmware
from fetchtastic.constants import (
    CONFIG_FILE_NAME,
    DEFAULT_CHECK_APK_PRERELEASES,
    MESHTASTIC_DIR_NAME,
)

# Recommended default exclude patterns for firmware extraction
# These patterns exclude specialized variants and debug files that most users don't need
# Patterns use fnmatch (glob-style) matching against the base filename
RECOMMENDED_EXCLUDE_PATTERNS = [
    "*.hex",  # hex files (debug/raw files)
    "*tcxo*",  # TCXO related files (crystal oscillator)
    "*s3-core*",  # S3 core files (specific hardware)
    "*request*",  # request files (debug/test files) - NOTE: May be too broad, consider review
    "*rak4631_*",  # RAK4631 underscore variants (like rak4631_eink)
    "*heltec_*",  # Heltec underscore variants
    "*tbeam_*",  # T-Beam underscore variants
    "*tlora_*",  # TLORA underscore variants
    "*_tft*",  # TFT display variants
    "*_oled*",  # OLED display variants
    "*_lcd*",  # LCD display variants
    "*_epaper*",  # e-paper display variants
    "*_eink*",  # e-ink display variants
]

# Cron job schedule configurations
CRON_SCHEDULES = {
    "hourly": {"schedule": "0 * * * *", "desc": "hourly"},
    "daily": {"schedule": "0 3 * * *", "desc": "daily at 3 AM"},
}

# Import Windows-specific modules if on Windows
if platform.system() == "Windows":
    try:
        import winshell

        WINDOWS_MODULES_AVAILABLE = True
    except ImportError:
        WINDOWS_MODULES_AVAILABLE = False
        print(
            "Windows detected. For full Windows integration, install optional dependencies:"
        )
        print("pipx install -e .[win]")
        print("or if using pip: pip install fetchtastic[win]")
else:
    WINDOWS_MODULES_AVAILABLE = False

# Windows Start Menu folder for Fetchtastic
WINDOWS_START_MENU_FOLDER = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Fetchtastic",
)

# Supported setup sections for partial reconfiguration
SETUP_SECTION_CHOICES: Set[str] = {
    "base",  # Base directory and environment-specific options
    "android",  # Android APK download preferences
    "firmware",  # Firmware download preferences (including prereleases/extraction)
    "notifications",  # NTFY configuration
    "automation",  # Cron/startup automation choices
    "github",  # GitHub API token configuration
}

SECTION_SHORTCUTS = {
    "b": "base",
    "a": "android",
    "f": "firmware",
    "n": "notifications",
    "m": "automation",
    "g": "github",
}


def is_termux():
    """
    Check if the script is running in a Termux environment.
    """
    return "com.termux" in os.environ.get("PREFIX", "")


def is_fetchtastic_installed_via_pip():
    """
    Check if fetchtastic is installed via pip (not pipx).

    Returns:
        bool: True if installed via pip, False otherwise
    """
    try:
        # Check if fetchtastic is in pip list
        result = subprocess.run(
            ["pip", "list"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return "fetchtastic" in result.stdout.lower()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        # pip command not found or failed to execute
        pass
    return False


def is_fetchtastic_installed_via_pipx():
    """
    Check if fetchtastic is installed via pipx.

    Returns:
        bool: True if installed via pipx, False otherwise
    """
    try:
        # Check if fetchtastic is in pipx list
        result = subprocess.run(
            ["pipx", "list"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return "fetchtastic" in result.stdout.lower()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        # pipx command not found or failed to execute
        pass
    return False


def get_fetchtastic_installation_method():
    """
    Determine how fetchtastic is currently installed.

    Returns:
        str: 'pip', 'pipx', or 'unknown'
    """
    if is_fetchtastic_installed_via_pipx():
        return "pipx"
    elif is_fetchtastic_installed_via_pip():
        return "pip"
    else:
        return "unknown"


def migrate_pip_to_pipx():
    """
    Migrate fetchtastic from pip to pipx installation in Termux.

    Returns:
        bool: True if migration successful, False otherwise
    """
    if not is_termux():
        print("Migration is only supported in Termux.")
        return False

    if get_fetchtastic_installation_method() != "pip":
        print("Fetchtastic is not installed via pip. No migration needed.")
        return True

    print("\n" + "=" * 50)
    print("MIGRATING FROM PIP TO PIPX")
    print("=" * 50)
    print("We recommend using pipx for better package isolation.")
    print("This will:")
    print("1. Backup your current configuration")
    print("2. Install pipx if not available")
    print("3. Uninstall fetchtastic from pip")
    print("4. Install fetchtastic with pipx")
    print("5. Restore your configuration")
    print()

    migrate = (
        input("Do you want to migrate to pipx? [y/n] (default: yes): ").strip().lower()
        or "y"
    )
    if migrate != "y":
        print("Migration cancelled. You can continue using pip, but we recommend pipx.")
        return False

    try:
        import shutil

        # Step 1: Backup configuration
        print("1. Backing up configuration...")
        config_backup = None
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config_backup = f.read()
            print("   Configuration backed up.")
        else:
            print("   No existing configuration found.")

        # Step 2: Install pipx if needed
        print("2. Ensuring pipx is available...")
        pipx_path = shutil.which("pipx")
        if not pipx_path:
            print("   Installing pipx...")
            result = subprocess.run(
                ["pip", "install", "--user", "pipx"], capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"   Failed to install pipx: {result.stderr}")
                return False

            # Ensure pipx path
            result = subprocess.run(
                ["python", "-m", "pipx", "ensurepath"], capture_output=True, text=True
            )
            print("   pipx installed successfully.")
        else:
            print("   pipx is already available.")

        # Step 3: Uninstall from pip
        print("3. Uninstalling fetchtastic from pip...")
        result = subprocess.run(
            ["pip", "uninstall", "fetchtastic", "-y"], capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"   Warning: Failed to uninstall from pip: {result.stderr}")
        else:
            print("   Uninstalled from pip successfully.")

        # Step 4: Install with pipx
        print("4. Installing fetchtastic with pipx...")
        result = subprocess.run(
            ["pipx", "install", "fetchtastic"], capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"   Failed to install with pipx: {result.stderr}")
            # Try to restore pip installation
            print("   Attempting to restore pip installation...")
            subprocess.run(["pip", "install", "fetchtastic"], check=False)
            return False
        else:
            print("   Installed with pipx successfully.")

        # Step 5: Restore configuration
        if config_backup:
            print("5. Restoring configuration...")
            with open(CONFIG_FILE, "w") as f:
                f.write(config_backup)
            print("   Configuration restored.")

        print("\n" + "=" * 50)
        print("MIGRATION COMPLETED SUCCESSFULLY!")
        print("=" * 50)
        print("Fetchtastic is now installed via pipx.")
        print("You can now use 'pipx upgrade fetchtastic' to upgrade.")
        print("Your configuration has been preserved.")
        print()

        return True

    except Exception as e:
        print(f"Migration failed with error: {e}")
        print("You can continue using the pip installation.")
        return False


def get_platform():
    """
    Determine the platform on which the script is running.
    """
    if is_termux():
        return "termux"
    elif platform.system() == "Darwin":
        return "mac"
    elif platform.system() == "Linux":
        return "linux"
    else:
        return "unknown"


def get_downloads_dir():
    """
    Get the default downloads directory based on the platform.
    """
    # For Termux, use ~/storage/downloads
    if is_termux():
        storage_downloads = os.path.expanduser("~/storage/downloads")
        if os.path.exists(storage_downloads):
            return storage_downloads
    # For other environments, use standard Downloads directories
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, "Downloads")
    if os.path.exists(downloads_dir):
        return downloads_dir
    downloads_dir = os.path.join(home_dir, "Download")
    if os.path.exists(downloads_dir):
        return downloads_dir
    # Fallback to home directory
    return home_dir


# Default directories
DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_BASE_DIR = os.path.join(DOWNLOADS_DIR, MESHTASTIC_DIR_NAME)

# Get the config directory using platformdirs
CONFIG_DIR = platformdirs.user_config_dir("fetchtastic")

# Old config file location (for migration)
OLD_CONFIG_FILE = os.path.join(DEFAULT_BASE_DIR, CONFIG_FILE_NAME)

# New config file location using platformdirs
CONFIG_FILE = os.path.join(CONFIG_DIR, CONFIG_FILE_NAME)

# These will be set during setup or when loading config
BASE_DIR = DEFAULT_BASE_DIR


def config_exists(directory=None):
    """
    Return whether a Fetchtastic configuration file exists and its path.

    If `directory` is provided, checks for CONFIG_FILE_NAME inside that directory.
    If `directory` is None, checks the new platformdirs location (CONFIG_FILE) first,
    then the legacy location (OLD_CONFIG_FILE).

    Returns:
        (bool, str|None): Tuple where the first element is True if a config file was
        found, and the second element is the full path to the found config file or
        None if not found.
    """
    if directory:
        config_path = os.path.join(directory, CONFIG_FILE_NAME)
        if os.path.exists(config_path):
            return True, config_path
        return False, None

    # Check new location first
    if os.path.exists(CONFIG_FILE):
        return True, CONFIG_FILE

    # Then check old location
    if os.path.exists(OLD_CONFIG_FILE):
        return True, OLD_CONFIG_FILE

    return False, None


def check_storage_setup():
    """
    Ensure Termux storage and the Downloads directory are available and writable.

    This function is intended for Termux environments: it verifies that ~/storage and
    ~/storage/downloads exist and are writable. If they are not, it repeatedly
    invokes setup_storage() and prompts the user to grant storage permissions,
    waiting for the user to confirm before re-checking. The function returns only
    after storage access is confirmed.

    Returns:
        bool: True when storage access and the Downloads directory are available and writable.

    Side effects:
        - May call setup_storage().
        - Blocks for interactive user input while awaiting permission grant.
    """
    # Check if the Termux storage directory and Downloads are set up and writable
    storage_dir = os.path.expanduser("~/storage")
    storage_downloads = os.path.expanduser("~/storage/downloads")

    while True:
        if (
            os.path.exists(storage_dir)
            and os.path.exists(storage_downloads)
            and os.access(storage_downloads, os.W_OK)
        ):
            print("Termux storage access is already set up.")
            return True
        else:
            print("Termux storage access is not set up or permission was denied.")
            # Run termux-setup-storage
            setup_storage()
            print("Please grant storage permissions when prompted.")
            input("Press Enter after granting storage permissions to continue...")
            # Re-check if storage is set up
            continue


def _prompt_for_setup_sections() -> Optional[Set[str]]:
    """
    Prompt the user to choose which setup sections to run when an existing configuration is detected.

    Displays the available setup sections with single-letter shortcuts and accepts a comma/space/semicolon-separated
    selection. Pressing ENTER (an empty response) or entering one of the keywords "all", "full", or "everything"
    signals a full run and the function returns None. Shortcuts defined in SECTION_SHORTCUTS and full section names
    from SETUP_SECTION_CHOICES are accepted; invalid tokens cause the prompt to repeat until a valid selection is given.

    Returns:
        Optional[Set[str]]: A set of chosen section names (subset of SETUP_SECTION_CHOICES), or None to indicate
        the full setup should be run.
    """

    print(
        "\nExisting configuration detected. You can re-run the full wizard or update specific areas."
    )
    print(
        "Press ENTER to run the full setup, or choose one or more sections (comma separated):"
    )
    print("  [b] base           — base directory and general settings")
    print("  [a] android        — Android APK download preferences")
    print("  [f] firmware       — firmware download preferences")
    print("  [n] notifications  — NTFY server/topic settings")
    print("  [m] automation     — scheduled/automatic execution options")
    print("  [g] github         — GitHub API token (rate-limit boost)")

    while True:
        response = input(
            "Selection (examples: f, android; default: full setup): "
        ).strip()
        if not response:
            return None

        tokens = [tok for tok in re.split(r"[\s,;]+", response) if tok]
        selected: Set[str] = set()
        for token in tokens:
            lowered = token.strip().lower()
            if not lowered:
                continue
            if lowered in {"all", "full", "everything"}:
                return None
            if lowered in SECTION_SHORTCUTS:
                selected.add(SECTION_SHORTCUTS[lowered])
                continue
            if lowered in SETUP_SECTION_CHOICES:
                selected.add(lowered)
                continue
            print(
                f"Unrecognised section '{token}'. Please choose from the listed options."
            )
            selected.clear()
            break

        if selected:
            return selected


def _setup_downloads(
    config: dict, is_partial_run: bool, wants: Callable[[str], bool]
) -> tuple[dict, bool, bool]:
    """
    Configure which asset types to download (APKs, firmware, or both), optionally re-run selection menus, and update the provided config.

    If running a full setup (is_partial_run is False) the user is prompted to choose APKs, firmware, or both. In a partial run the function will prompt only for sections indicated by the `wants` callable and will default to values already present in `config`.

    Behavior and side effects:
    - Updates `config["SAVE_APKS"]` and `config["SAVE_FIRMWARE"]`.
    - When APKs are enabled and the APK menu is (re)run, sets `config["SELECTED_APK_ASSETS"]` to the chosen assets.
    - When firmware is enabled and the firmware menu is (re)run, sets `config["SELECTED_FIRMWARE_ASSETS"]` to the chosen assets.
    - Prints guidance and informational messages; may return early if neither asset type is selected.

    Parameters:
        config: Mutable configuration dictionary that will be updated.
        is_partial_run: If True, only prompts sections for which `wants(section)` returns True and will reuse existing config defaults when appropriate.
        wants: Callable that accepts a section name (e.g., "android" or "firmware") and returns True when that section should be processed in this run.

    Returns:
        Tuple of (updated_config, save_apks, save_firmware) where save_apks and save_firmware are booleans indicating the final selection.
    """
    # Prompt to save APKs, firmware, or both
    if not is_partial_run:
        save_choice = (
            input(
                "Would you like to download APKs, firmware, or both? [a/f/b] (default: both): "
            )
            .strip()
            .lower()
            or "both"
        )
        if save_choice == "a":
            save_apks = True
            save_firmware = False
        elif save_choice == "f":
            save_apks = False
            save_firmware = True
        else:
            save_apks = True
            save_firmware = True
    else:
        save_apks = config.get("SAVE_APKS", False)
        save_firmware = config.get("SAVE_FIRMWARE", False)
        if wants("android"):
            current_apk_default = "y" if save_apks else "n"
            choice = (
                input(
                    f"Download Android APKs? [y/n] (current: {current_apk_default}): "
                )
                .strip()
                .lower()
                or current_apk_default
            )
            save_apks = choice == "y"
        if wants("firmware"):
            current_fw_default = "y" if save_firmware else "n"
            choice = (
                input(
                    f"Download firmware releases? [y/n] (current: {current_fw_default}): "
                )
                .strip()
                .lower()
                or current_fw_default
            )
            save_firmware = choice == "y"

    config["SAVE_APKS"] = save_apks
    config["SAVE_FIRMWARE"] = save_firmware

    # Run the menu scripts based on user choices
    # Small tip to help users choose precise firmware patterns
    if save_firmware and (not is_partial_run or wants("firmware")):
        print("\nTips for precise selection:")
        print(
            "- Use the separator seen in filenames to target a family (e.g., 'rak4631-' vs 'rak4631_')."
        )
        print(
            "- 'rak4631-' matches base RAK4631 files (e.g., firmware-rak4631-...),"
            " while 'rak4631_' matches underscore variants (e.g., firmware-rak4631_eink-...).",
            sep="",
        )
        print("- You can re-run 'fetchtastic setup' anytime to adjust your patterns.\n")
    if save_apks and (not is_partial_run or wants("android")):
        rerun_menu = True
        if is_partial_run:
            keep_existing = (
                input("Re-run the Android APK selection menu? [y/n] (default: yes): ")
                .strip()
                .lower()
            )
            if keep_existing == "n":
                rerun_menu = False
        if rerun_menu:
            apk_selection = menu_apk.run_menu()
            if not apk_selection:
                print("No APK assets selected. APKs will not be downloaded.")
                save_apks = False
                config["SAVE_APKS"] = False
            else:
                config["SELECTED_APK_ASSETS"] = apk_selection["selected_assets"]

    # --- APK Pre-release Configuration ---
    if save_apks and (not is_partial_run or wants("android")):
        check_apk_prereleases_current = config.get(
            "CHECK_APK_PRERELEASES", DEFAULT_CHECK_APK_PRERELEASES
        )  # Default: True. APK prereleases are typically more stable than firmware prereleases and safer to enable by default.
        check_apk_prereleases_default = "yes" if check_apk_prereleases_current else "no"
        check_apk_prereleases_input = (
            input(
                f"\nWould you like to check for and download pre-release APKs from GitHub? [y/n] (default: {check_apk_prereleases_default}): "
            )
            .strip()
            .lower()
            or check_apk_prereleases_default
        )
        config["CHECK_APK_PRERELEASES"] = check_apk_prereleases_input[0] == "y"

    if save_firmware and (not is_partial_run or wants("firmware")):
        rerun_menu = True
        if is_partial_run:
            keep_existing = (
                input(
                    "Re-run the firmware asset selection menu? [y/n] (default: yes): "
                )
                .strip()
                .lower()
            )
            if keep_existing == "n":
                rerun_menu = False
        if rerun_menu:
            firmware_selection = menu_firmware.run_menu()
            if not firmware_selection:
                print("No firmware assets selected. Firmware will not be downloaded.")
                save_firmware = False
                config["SAVE_FIRMWARE"] = False
            else:
                config["SELECTED_FIRMWARE_ASSETS"] = firmware_selection[
                    "selected_assets"
                ]

    # If both save_apks and save_firmware are False, inform the user and exit setup
    if not save_apks and not save_firmware:
        print("Please select at least one type of asset to download (APK or firmware).")
        print("Run 'fetchtastic setup' again and select at least one asset.")
        return config, save_apks, save_firmware

    return config, save_apks, save_firmware


def _setup_android(config: dict, is_first_run: bool, default_versions: int) -> dict:
    """
    Prompt for how many Android APK versions to keep and save the choice to the config.

    Reads the current value from config["ANDROID_VERSIONS_TO_KEEP"] (falls back to default_versions if absent),
    prompts the user (prompt wording changes when is_first_run is True), converts the response to int, and stores
    it back into config["ANDROID_VERSIONS_TO_KEEP"]. If the user input is not a valid integer, the existing
    value is retained.

    Parameters:
        config (dict): Configuration dictionary to read and update; modified in place.
        is_first_run (bool): If True, use first-run phrasing in the prompt.
        default_versions (int): Fallback value used when the config does not already contain a value.

    Returns:
        dict: The updated configuration dictionary with "ANDROID_VERSIONS_TO_KEEP" set.
    """
    current_versions = config.get("ANDROID_VERSIONS_TO_KEEP", default_versions)
    if is_first_run:
        prompt_text = f"How many versions of the Android app would you like to keep? (default is {current_versions}): "
    else:
        prompt_text = f"How many versions of the Android app would you like to keep? (current: {current_versions}): "
    raw = input(prompt_text).strip() or str(current_versions)
    try:
        config["ANDROID_VERSIONS_TO_KEEP"] = int(raw)
    except ValueError:
        print("Invalid number — keeping current value.")
        config["ANDROID_VERSIONS_TO_KEEP"] = int(current_versions)
    return config


def configure_exclude_patterns(config: dict) -> None:
    """
    Interactively configure firmware exclude patterns and save them to the provided config.

    This function runs an interactive prompt that:
    - Offers the built-in RECOMMENDED_EXCLUDE_PATTERNS as a starting set.
    - Lets the user accept the defaults and optionally add more patterns, or enter a fully custom space-separated list.
    - Normalizes input by trimming whitespace, removing empty entries, and deduplicating while preserving order.
    - Confirms the final list with the user before saving.

    Effects:
    - Writes the finalized list of patterns to config["EXCLUDE_PATTERNS"] (a list of strings).
    - Does not persist the config to disk; callers should save the configuration if desired.
    """
    while True:  # Loop for retry capability
        print("\n--- Exclude Pattern Configuration ---")
        print(
            "Some firmware files are specialized variants (like display-specific versions)"
        )
        print("that most users don't need. We can exclude these automatically.")

        # Offer recommended defaults
        recommended_str = " ".join(RECOMMENDED_EXCLUDE_PATTERNS)
        use_defaults_default = "yes"
        use_defaults = (
            input(
                f"Would you like to use our recommended exclude patterns?\n"
                f"These skip common specialized variants and debug files: [y/n] (default: {use_defaults_default}): "
            )
            .strip()
            .lower()
            or use_defaults_default[0]
        )

        if use_defaults == "y":
            # Start with recommended patterns
            exclude_patterns = RECOMMENDED_EXCLUDE_PATTERNS.copy()
            print(f"Using recommended exclude patterns: {recommended_str}")

            # Ask for additional patterns
            add_more_default = "no"
            add_more = (
                input(
                    f"Would you like to add any additional exclude patterns? [y/n] (default: {add_more_default}): "
                )
                .strip()
                .lower()
                or add_more_default[0]
            )

            if add_more == "y":
                additional_patterns = input(
                    "Enter additional patterns (space-separated): "
                ).strip()
                if additional_patterns:
                    exclude_patterns.extend(additional_patterns.split())
        else:
            # User doesn't want defaults, get custom patterns
            custom_patterns = input(
                "Enter your exclude patterns (space-separated, or press Enter for none): "
            ).strip()
            if custom_patterns:
                exclude_patterns = custom_patterns.split()
            else:
                exclude_patterns = []

        # Normalize and de-duplicate while preserving order
        stripped_patterns = [p.strip() for p in exclude_patterns if p.strip()]
        exclude_patterns = list(dict.fromkeys(stripped_patterns))

        # Show final list and confirm
        if exclude_patterns:
            final_patterns_str = " ".join(exclude_patterns)
            print(f"\nFinal exclude patterns: {final_patterns_str}")
        else:
            print(
                "\nNo exclude patterns will be used. All matching files will be extracted."
            )

        confirm_default = "yes"
        confirm = (
            input(f"Is this correct? [y/n] (default: {confirm_default}): ")
            .strip()
            .lower()
            or confirm_default[0]
        )

        if confirm == "y":
            # Save the configuration and break the loop
            config["EXCLUDE_PATTERNS"] = exclude_patterns
            if exclude_patterns:
                print(f"Exclude patterns configured: {' '.join(exclude_patterns)}")
            else:
                print("No exclude patterns configured.")
            break
        else:
            # User wants to reconfigure, loop will continue
            print("Let's reconfigure the exclude patterns...")


def _setup_firmware(config: dict, is_first_run: bool, default_versions: int) -> dict:
    """
    Configure firmware-related settings in the provided config via interactive prompts.

    Updates the config in place with keys related to firmware retention, automatic extraction, extraction/exclusion patterns, and prerelease handling:
    FIRMWARE_VERSIONS_TO_KEEP, AUTO_EXTRACT, EXTRACT_PATTERNS, EXCLUDE_PATTERNS, CHECK_PRERELEASES, SELECTED_PRERELEASE_ASSETS.

    Parameters:
        config (dict): Configuration mapping to read defaults from and write updated values into.
        is_first_run (bool): When True, prompts use first-run wording and defaults.
        default_versions (int): Fallback number of firmware versions to keep when not present in config.

    Returns:
        dict: The same config object passed in, updated with firmware-related settings.
    """

    # Prompt for firmware versions to keep
    current_versions = config.get("FIRMWARE_VERSIONS_TO_KEEP", default_versions)
    if is_first_run:
        prompt_text = f"How many versions of the firmware would you like to keep? (default is {current_versions}): "
    else:
        prompt_text = f"How many versions of the firmware would you like to keep? (current: {current_versions}): "
    raw = input(prompt_text).strip() or str(current_versions)
    try:
        config["FIRMWARE_VERSIONS_TO_KEEP"] = int(raw)
    except ValueError:
        print("Invalid number — keeping current value.")
        config["FIRMWARE_VERSIONS_TO_KEEP"] = int(current_versions)

    # --- File Extraction Configuration ---
    print("\n--- File Extraction Configuration ---")
    print("Configure which files to extract from downloaded firmware archives.")

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

    if config["AUTO_EXTRACT"]:
        print(
            "Enter the keywords to match for extraction from the firmware zip files, separated by spaces."
        )
        print("Example: rak4631- tbeam t1000-e- tlora-v2-1-1_6- device-")

        current_patterns = config.get("EXTRACT_PATTERNS", [])
        if isinstance(current_patterns, str):
            current_patterns = current_patterns.split()
            config["EXTRACT_PATTERNS"] = current_patterns
        if current_patterns:
            print(f"Current patterns: {' '.join(current_patterns)}")
            keep_patterns_default = "yes"
            keep_patterns_input = (
                input(
                    f"Do you want to keep the current extraction patterns? [y/n] (default: {keep_patterns_default}): "
                )
                .strip()
                .lower()
                or keep_patterns_default
            )
            if keep_patterns_input[0] != "y":
                current_patterns = []  # Clear to prompt for new ones

        if not current_patterns:
            extract_patterns_input = input("Extraction patterns: ").strip()
            if extract_patterns_input:
                config["EXTRACT_PATTERNS"] = extract_patterns_input.split()
                print(f"Extraction patterns set to: {extract_patterns_input}")
            else:
                # User entered no patterns, so disable auto-extract
                config["AUTO_EXTRACT"] = False
                config["EXTRACT_PATTERNS"] = []
                print("No extraction patterns provided; disabling auto-extraction.")

        # Configure exclude patterns only if extraction is enabled and patterns are set
        if config.get("AUTO_EXTRACT") and config.get("EXTRACT_PATTERNS"):
            # In non-interactive environments (like CI), skip interactive prompts
            if not sys.stdin.isatty() or os.environ.get("CI"):
                config["EXCLUDE_PATTERNS"] = RECOMMENDED_EXCLUDE_PATTERNS.copy()
                print("Using recommended exclude patterns (non-interactive mode).")
            else:
                configure_exclude_patterns(config)
        else:
            config["EXCLUDE_PATTERNS"] = []
    else:
        # If auto-extract is off, clear all related settings
        config["AUTO_EXTRACT"] = False
        config["EXTRACT_PATTERNS"] = []
        config["EXCLUDE_PATTERNS"] = []

    # --- Pre-release Configuration ---
    check_prereleases_current = config.get("CHECK_PRERELEASES", False)
    check_prereleases_default = "yes" if check_prereleases_current else "no"
    check_prereleases_input = (
        input(
            f"\nWould you like to check for and download pre-release firmware from meshtastic.github.io? [y/n] (default: {check_prereleases_default}): "
        )
        .strip()
        .lower()
        or check_prereleases_default
    )
    config["CHECK_PRERELEASES"] = check_prereleases_input[0] == "y"

    if config["CHECK_PRERELEASES"]:
        # Use a copy to avoid aliasing EXTRACT_PATTERNS
        prerelease_patterns = list(config.get("EXTRACT_PATTERNS", []))
        config["SELECTED_PRERELEASE_ASSETS"] = prerelease_patterns

        if prerelease_patterns:
            print(
                f"Using your extraction patterns for pre-release selection: {' '.join(prerelease_patterns)}"
            )
        else:
            # Correct the message to be accurate
            print(
                "No extraction patterns set. No pre-release files will be downloaded."
            )
            print(
                "To select specific pre-release files, first set up extraction patterns."
            )
    else:
        # Prereleases disabled, clear the setting
        config["SELECTED_PRERELEASE_ASSETS"] = []

    return config


def _configure_cron_job(install_crond_needed: bool = False) -> None:
    """
    Prompt the user for a cron frequency and configure a Fetchtastic cron job accordingly.

    If the chosen frequency is not "none", the function will install the Termux crond service first when requested and then create/update the cron job at the selected cadence. If the user selects "none", no cron job is configured and a message is printed.

    Parameters:
        install_crond_needed (bool): If True, install and enable the Termux crond service before configuring the cron job.
    """
    frequency = _prompt_for_cron_frequency()
    if frequency != "none":
        if install_crond_needed:
            install_crond()
        setup_cron_job(frequency)
    else:
        print("Cron job has not been set up.")


def _prompt_for_cron_frequency() -> str:
    """
    Prompt the user to choose how often Fetchtastic should run its scheduled check.

    Returns:
        frequency (str): 'hourly', 'daily', or 'none'.
    """
    choices = {"h": "hourly", "d": "daily", "n": "none"}
    while True:
        cron_choice = (
            input(
                "How often should Fetchtastic check for updates? [h/d/n] (h=hourly, d=daily, n=none, default: hourly): "
            )
            .strip()
            .lower()
            or "h"
        )
        if cron_choice in choices:
            return choices[cron_choice]
        else:
            print(f"Invalid choice '{cron_choice}'. Please enter 'h', 'd', or 'n'.")


def _setup_automation(
    config: dict, is_partial_run: bool, wants: Callable[[str], bool]
) -> dict:
    """
    Configure platform-specific automation for Fetchtastic (cron jobs, startup/boot shortcuts).

    Depending on the platform, this function will offer to create, remove, or reconfigure:
    - Windows: a startup shortcut to run Fetchtastic on user login.
    - Termux: a scheduled cron job and an optional boot script to run on device boot.
    - Linux/macOS: a scheduled cron job and an optional reboot/startup cron entry.

    Parameters:
        config (dict): Current configuration dictionary that may be read and updated.
        is_partial_run (bool): If True, only run when the caller indicates the automation section is desired.
        wants (Callable[[str], bool]): Predicate that returns True if a named setup section should be processed.

    Returns:
        dict: The potentially updated configuration dictionary.
    """
    if not is_partial_run or wants("automation"):
        if platform.system() == "Windows":
            # Windows doesn't support cron jobs, but we can offer to create a startup shortcut
            if WINDOWS_MODULES_AVAILABLE:
                # Check if startup shortcut already exists
                startup_folder = winshell.startup()
                startup_shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")

                if os.path.exists(startup_shortcut_path):
                    startup_option = (
                        input(
                            "Fetchtastic is already set to run at startup. Would you like to remove this? [y/n] (default: no): "
                        )
                        .strip()
                        .lower()
                        or "n"
                    )
                    if startup_option == "y":
                        try:
                            # Also remove the batch file if it exists
                            batch_dir = os.path.join(CONFIG_DIR, "batch")
                            batch_path = os.path.join(
                                batch_dir, "fetchtastic_startup.bat"
                            )
                            if os.path.exists(batch_path):
                                os.remove(batch_path)

                            # Remove the shortcut
                            os.remove(startup_shortcut_path)
                            print(
                                "✓ Startup shortcut removed. Fetchtastic will no longer run automatically at startup."
                            )
                        except Exception as e:
                            print(f"Failed to remove startup shortcut: {e}")
                            print("You can manually remove it from: " + startup_folder)
                    else:
                        print(
                            "✓ Fetchtastic will continue to run automatically at startup."
                        )
                else:
                    startup_option = (
                        input(
                            "Would you like to run Fetchtastic automatically on Windows startup? [y/n] (default: yes): "
                        )
                        .strip()
                        .lower()
                        or "y"
                    )
                    if startup_option == "y":
                        if create_startup_shortcut():
                            print(
                                "✓ Fetchtastic will now run automatically when Windows starts."
                            )
                        else:
                            print(
                                "Failed to create startup shortcut. You can manually set up Fetchtastic to run at startup."
                            )
                            print(
                                "You can use Windows Task Scheduler or add a shortcut to: "
                                + startup_folder
                            )
                    else:
                        print("Fetchtastic will not run automatically on startup.")
            else:
                # Don't show this message again since we already showed it earlier
                pass
        elif is_termux():
            # Termux: Ask about cron job and boot script individually
            # Check if cron job already exists
            cron_job_exists = check_cron_job_exists()
            if cron_job_exists:
                cron_prompt = (
                    input(
                        "A cron job is already set up. Do you want to reconfigure it? [y/n] (default: no): "
                    )
                    .strip()
                    .lower()
                    or "n"
                )
                if cron_prompt == "y":
                    # First, remove existing cron job
                    remove_cron_job()
                    print("Existing cron job removed for reconfiguration.")

                    # Configure cron job
                    _configure_cron_job(install_crond_needed=True)
                else:
                    print("Cron job configuration left unchanged.")
            else:
                # Configure cron job
                _configure_cron_job(install_crond_needed=True)

            # Check if boot script already exists
            boot_script_exists = check_boot_script_exists()
            if boot_script_exists:
                boot_prompt = (
                    input(
                        "A boot script is already set up. Do you want to reconfigure it? [y/n] (default: no): "
                    )
                    .strip()
                    .lower()
                    or "n"
                )
                if boot_prompt == "y":
                    # First, remove existing boot script
                    remove_boot_script()
                    print("Existing boot script removed for reconfiguration.")

                    # Then set up new boot script
                    setup_boot_script()
                    print("Boot script has been reconfigured.")
                else:
                    print("Boot script configuration left unchanged.")
            else:
                # Ask if the user wants to set up a boot script
                boot_default = "yes"  # Default to 'yes'
                setup_boot = (
                    input(
                        f"Do you want Fetchtastic to run on device boot? [y/n] (default: {boot_default}): "
                    )
                    .strip()
                    .lower()
                    or boot_default[0]
                )
                if setup_boot == "y":
                    setup_boot_script()
                else:
                    print("Boot script has not been set up.")

        else:
            # Linux/Mac: Check if any Fetchtastic cron jobs exist
            any_cron_jobs_exist = check_any_cron_jobs_exist()
            if any_cron_jobs_exist:
                cron_prompt = (
                    input(
                        "Fetchtastic cron jobs are already set up. Do you want to reconfigure them? [y/n] (default: no): "
                    )
                    .strip()
                    .lower()
                    or "n"
                )
                if cron_prompt == "y":
                    # First, remove existing cron jobs
                    remove_cron_job()
                    remove_reboot_cron_job()
                    print("Existing cron jobs removed for reconfiguration.")

                    # Configure cron job
                    _configure_cron_job(install_crond_needed=False)

                    # Ask if they want to set up a reboot cron job
                    boot_default = "yes"
                    setup_reboot = (
                        input(
                            f"Do you want Fetchtastic to run on system startup? [y/n] (default: {boot_default}): "
                        )
                        .strip()
                        .lower()
                        or boot_default[0]
                    )
                    if setup_reboot == "y":
                        setup_reboot_cron_job()
                        print("Reboot cron job has been set up.")
                    else:
                        print("Reboot cron job will not be set up.")
                else:
                    print("Cron job configurations left unchanged.")
            else:
                # No existing cron jobs, ask if they want to set them up
                # Configure cron job
                _configure_cron_job(install_crond_needed=False)

                # Ask if they want to set up a reboot cron job
                boot_default = "yes"
                setup_reboot = (
                    input(
                        f"Do you want Fetchtastic to run on system startup? [y/n] (default: {boot_default}): "
                    )
                    .strip()
                    .lower()
                    or boot_default[0]
                )
                if setup_reboot == "y":
                    setup_reboot_cron_job()
                else:
                    print("Reboot cron job has not been set up.")

    return config


def _setup_notifications(config: dict) -> dict:
    """
    Configure NTFY-based notifications interactively and return the updated config.

    Prompts the user to enable or disable NTFY notifications, collect the NTFY server URL
    and topic name when enabling, and set whether notifications should be sent only for
    new downloads. Updates these keys in the provided config: `NTFY_TOPIC`, `NTFY_SERVER`,
    and `NOTIFY_ON_DOWNLOAD_ONLY`. If notifications are disabled (or the user confirms
    disabling), the corresponding keys are cleared/false.

    Parameters:
        config (dict): Current configuration dictionary to be modified in-place and returned.

    Returns:
        dict: The updated configuration dictionary.
    """

    has_ntfy_config = bool(config.get("NTFY_TOPIC")) and bool(config.get("NTFY_SERVER"))
    notifications_default = "yes" if has_ntfy_config else "no"

    notifications = (
        input(
            f"Would you like to set up notifications via NTFY? [y/n] (default: {notifications_default}): "
        )
        .strip()
        .lower()
        or notifications_default[0]
    )

    if notifications == "y":
        # Get NTFY server
        current_server = config.get("NTFY_SERVER", "ntfy.sh")
        ntfy_server = (
            input(f"Enter the NTFY server (current: {current_server}): ").strip()
            or current_server
        )

        if not ntfy_server.startswith("http://") and not ntfy_server.startswith(
            "https://"
        ):
            ntfy_server = "https://" + ntfy_server

        # Get topic name
        if config.get("NTFY_TOPIC"):
            current_topic = config.get("NTFY_TOPIC")
        else:
            current_topic = "fetchtastic-" + "".join(
                random.choices(string.ascii_lowercase + string.digits, k=6)
            )

        topic_name = (
            input(f"Enter a unique topic name (current: {current_topic}): ").strip()
            or current_topic
        )

        # Update config
        config["NTFY_TOPIC"] = topic_name
        config["NTFY_SERVER"] = ntfy_server

        # Display information
        full_topic_url = f"{ntfy_server.rstrip('/')}/{topic_name}"
        print(f"Notifications enabled using topic: {topic_name}")
        if is_termux():
            print("Subscribe by pasting the topic name in the ntfy app.")
        else:
            print(
                "Subscribe by visiting the full topic URL in your browser or ntfy app."
            )
        print(f"Full topic URL: {full_topic_url}")

        # Offer to copy to clipboard
        if is_termux():
            copy_prompt_text = "Do you want to copy the topic name to the clipboard? [y/n] (default: yes): "
            text_to_copy = topic_name
        else:
            copy_prompt_text = "Do you want to copy the topic URL to the clipboard? [y/n] (default: yes): "
            text_to_copy = full_topic_url

        copy_to_clipboard = input(copy_prompt_text).strip().lower() or "y"
        if copy_to_clipboard == "y":
            success = copy_to_clipboard_func(text_to_copy)
            if success:
                if is_termux():
                    print("Topic name copied to clipboard.")
                else:
                    print("Topic URL copied to clipboard.")
            else:
                print("Failed to copy to clipboard.")

        # Ask if the user wants notifications only when new files are downloaded
        notify_on_download_only_default = (
            "yes" if config.get("NOTIFY_ON_DOWNLOAD_ONLY", False) else "no"
        )
        notify_on_download_only = (
            input(
                f"Do you want to receive notifications only when new files are downloaded? [y/n] (default: {notify_on_download_only_default}): "
            )
            .strip()
            .lower()
            or notify_on_download_only_default[0]
        )
        config["NOTIFY_ON_DOWNLOAD_ONLY"] = (
            True if notify_on_download_only == "y" else False
        )

    else:
        # User chose not to use notifications
        if has_ntfy_config:
            # Ask for confirmation to disable existing notifications
            disable_confirm = (
                input(
                    "You currently have notifications enabled. Are you sure you want to disable them? [y/n] (default: no): "
                )
                .strip()
                .lower()
                or "n"
            )

            if disable_confirm == "y":
                config["NTFY_TOPIC"] = ""
                config["NTFY_SERVER"] = ""
                config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
                print("Notifications have been disabled.")
            else:
                print("Keeping existing notification settings.")
        else:
            # No existing notifications, just confirm they're disabled
            config["NTFY_TOPIC"] = ""
            config["NTFY_SERVER"] = ""
            config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
            print("Notifications will remain disabled.")

    return config


def _setup_github(config: dict) -> dict:
    """
    Configure GitHub API token for higher rate limits interactively.

    Prompts the user to optionally set up a GitHub personal access token for API requests.
    Explains the rate limit benefits (60/hour vs 5000/hour) and provides guidance
    on token creation. Updates the GITHUB_TOKEN key in the provided config.

    Parameters:
        config (dict): Current configuration dictionary to be modified in-place and returned.

    Returns:
        dict: The updated configuration dictionary with GITHUB_TOKEN potentially set.
    """
    print("\n" + "=" * 60)
    print("GitHub API Token Configuration")
    print("=" * 60)
    print()
    print("GitHub API requests have different rate limits:")
    print("  • Without token: 60 requests per hour")
    print("  • With personal token: 5,000 requests per hour")
    print()
    print("A token is optional. You can use one if you want to avoid rate limits.")
    print()

    # Check if token already exists
    current_token = config.get("GITHUB_TOKEN")
    if current_token:
        masked_token = current_token[:4] + "..." if len(current_token) > 4 else "***"
        print(f"Current status: Token configured ({masked_token})")
        change_choice = (
            input("Would you like to change the GitHub token? [y/n] (default: no): ")
            .strip()
            .lower()
        )
        if change_choice not in ["y", "yes"]:
            print("Keeping existing GitHub token configuration.")
            return config
    else:
        print("No GitHub token currently configured.")

    print()
    setup_choice = (
        input("Would you like to set up a GitHub token now? [y/n] (default: no): ")
        .strip()
        .lower()
    )

    if setup_choice in ["y", "yes"]:
        print("\nTo create a GitHub personal access token:")
        print("1. Visit: https://github.com/settings/tokens")
        print("2. Click 'Generate new token (classic)'")
        print("3. Give it a descriptive name (e.g., 'Fetchtastic')")
        print("4. Select 'public_repo' scope only (no additional permissions needed)")
        print("5. Click 'Generate token'")
        print("6. Copy the token and paste it below")
        print()

        token = getpass.getpass(
            "Enter your GitHub personal access token (or press Enter to skip): "
        ).strip()

        if token:
            # Enhanced validation - GitHub tokens have specific prefixes and formats
            # Classic PATs: start with "ghp_" and are 40 characters total (including prefix)
            # Fine-grained PATs: start with "github_pat_"
            # OAuth tokens: start with "gho_"
            # GitHub App user tokens: start with "ghu_"
            # GitHub App installation tokens: start with "ghs_"
            # GitHub App refresh tokens: start with "ghr_"
            valid_prefixes = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")

            if token.startswith(valid_prefixes) and len(token) >= 20:
                config["GITHUB_TOKEN"] = token
                print("✓ GitHub token saved successfully!")
                print(
                    "  This will be used for API authentication (5000 requests/hour)."
                )
            else:
                print(
                    "⚠ Invalid token format. GitHub tokens must start with one of: "
                    "ghp_ (classic PAT), github_pat_ (fine-grained PAT), gho_ (OAuth), "
                    "ghu_ (GitHub App user), ghs_ (GitHub App installation), ghr_ (GitHub App refresh)."
                )
                print("  No changes saved. Please try again if needed.")
        else:
            print("No token entered. Continuing without GitHub token.")
            config.pop("GITHUB_TOKEN", None)
    else:
        print("Skipping GitHub token setup.")
        config.pop("GITHUB_TOKEN", None)

    print()
    return config


def _setup_base(
    config: dict, is_partial_run: bool, is_first_run: bool, wants: Callable[[str], bool]
) -> dict:
    """
    Handle base directory setup, Termux packages, and Windows shortcuts.

    Args:
        config: Current configuration dictionary
        is_partial_run: Whether this is a partial setup run
        is_first_run: Whether this is the first time setup is being run
        wants: Function to check if a section should be processed

    Returns:
        Updated configuration dictionary
    """
    global BASE_DIR

    # Install required Termux packages first
    if is_termux() and (not is_partial_run or wants("base")):
        install_termux_packages()
        # Check if storage is set up
        check_storage_setup()
        print("Termux storage is set up.")

        # Check for pip installation and offer migration to pipx
        if get_fetchtastic_installation_method() == "pip":
            print("\n" + "=" * 60)
            print("NOTICE: Fetchtastic is installed via pip")
            print("=" * 60)
            print("We now recommend using pipx for better package isolation.")
            print("pipx provides:")
            print("• Isolated environments for each package")
            print("• Better dependency management")
            print("• Consistent experience across platforms")
            print("\nTo migrate to pipx:")
            print("1. Install pipx: pkg install python-pipx")
            print("2. Uninstall current version: pip uninstall fetchtastic")
            print("3. Install with pipx: pipx install fetchtastic")
            print("4. Restart your terminal")
            print("=" * 60)

            migrate_to_pipx = (
                input("Would you like to migrate to pipx now? [y/n] (default: no): ")
                .strip()
                .lower()
                or "n"
            )

            if migrate_to_pipx == "y":
                print("Starting migration to pipx...")
                try:
                    # Install pipx if not already installed
                    pkg_exe = shutil.which("pkg") or "pkg"
                    subprocess.run(
                        [pkg_exe, "install", "python-pipx"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ pipx installed")

                    # Uninstall current fetchtastic
                    pip_exe = shutil.which("pip") or "pip"
                    subprocess.run(
                        [pip_exe, "uninstall", "fetchtastic", "-y"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ Removed pip installation")

                    # Install with pipx
                    pipx_exe = shutil.which("pipx") or "pipx"
                    subprocess.run(
                        [pipx_exe, "install", "fetchtastic"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ Installed with pipx")

                    print("\nMigration complete! Please restart your terminal.")
                    print("You can then run 'fetchtastic setup' to continue.")
                    sys.exit(0)

                except subprocess.CalledProcessError as e:
                    print(f"Migration failed: {e}")
                    if e.stderr:
                        print(f"Error details:\n{e.stderr.decode(errors='ignore')}")
                    print("You can migrate manually later using the steps above.")

        from fetchtastic.log_utils import logger

        separator = "=" * 60
        logger.info(f"{separator}\n")

    # Ask for base directory as the first question
    exists, config_path = config_exists()

    if exists:
        # Load existing configuration
        config = load_config()
        print(
            "Existing configuration found. You can keep current settings or change them."
        )
        current_base_dir = config.get("BASE_DIR", DEFAULT_BASE_DIR)
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (current: {current_base_dir}): "
        )
    else:
        # Initialize default configuration
        config = {}
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (default: {DEFAULT_BASE_DIR}): "
        )

    if not is_partial_run or wants("base"):
        # Prompt for base directory
        base_dir_input = input(base_dir_prompt).strip()

        if base_dir_input:
            # User entered a custom directory
            base_dir = os.path.expanduser(base_dir_input)

            # Check if there's a config file in the specified directory
            exists_in_dir, _ = config_exists(base_dir)
            if exists_in_dir and base_dir != BASE_DIR:
                print(f"Found existing configuration in {base_dir}")
                # Load the configuration from the specified directory
                config = load_config(base_dir)
            else:
                # No config in the specified directory or it's the same as current
                BASE_DIR = base_dir
                # Keep CONFIG_FILE in the platformdirs location
                # CONFIG_FILE should not be changed here
        else:
            # User accepted the default/current directory
            if is_first_run:
                base_dir = DEFAULT_BASE_DIR
            else:
                base_dir = config.get("BASE_DIR", DEFAULT_BASE_DIR)

            # Expand user directory if needed (e.g., ~/Downloads/Meshtastic)
            base_dir = os.path.expanduser(base_dir)

            # Update global variables
            BASE_DIR = base_dir
            # Keep CONFIG_FILE in the platformdirs location
            # CONFIG_FILE should not be changed here
    else:
        # Partial run retaining the existing base directory
        BASE_DIR = os.path.expanduser(config.get("BASE_DIR", DEFAULT_BASE_DIR))

    # Store the base directory in the config
    config["BASE_DIR"] = BASE_DIR

    # Create the base directory if it doesn't exist
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)

    # On Windows, handle shortcuts
    if platform.system() == "Windows" and (not is_partial_run or wants("base")):
        # Always create a shortcut to the config file in the base directory without asking
        if WINDOWS_MODULES_AVAILABLE:
            create_config_shortcut(CONFIG_FILE, BASE_DIR)

            # Check if Start Menu shortcuts already exist
            if os.path.exists(WINDOWS_START_MENU_FOLDER):
                create_menu = (
                    input(
                        "Fetchtastic shortcuts already exist in the Start Menu. Would you like to update them? [y/n] (default: yes): "
                    )
                    .strip()
                    .lower()
                    or "y"
                )
            else:
                create_menu = (
                    input(
                        "Would you like to create Fetchtastic shortcuts in the Start Menu? (recommended) [y/n] (default: yes): "
                    )
                    .strip()
                    .lower()
                    or "y"
                )

            if create_menu == "y":
                create_windows_menu_shortcuts(CONFIG_FILE, BASE_DIR)
        else:
            print(
                "Windows shortcuts not available. Install optional dependencies for full Windows integration:"
            )
            print("pip install fetchtastic[windows]")

    return config


def run_setup(sections: Optional[Sequence[str]] = None):
    """
    Run the interactive Fetchtastic setup wizard.

    Guides the user through creating or migrating configuration, selecting assets (APKs and/or firmware), retention and extraction settings, notification (NTFY) setup, and scheduling/startup options. Behavior is platform-aware: on Termux it may install required packages, configure storage, and offer pip→pipx migration; on Windows it can create Start Menu and startup shortcuts; on Linux/macOS/Termux it can install/remove cron or boot jobs. The function persists settings to the configured YAML file, updates the global BASE_DIR (and related config keys), may create or migrate CONFIG_FILE, and optionally triggers an initial download run (calls downloader.main()).

    When ``sections`` is provided the wizard focuses only on those configuration areas (drawn from
    :data:`SETUP_SECTION_CHOICES`). Other settings are preserved using the existing values from the
    configuration file, so users can quickly tweak a single area without stepping through the full
    workflow.
    """
    global BASE_DIR
    partial_sections: Optional[Set[str]] = None
    if sections:
        section_names = [s.lower() for s in sections]
        invalid = sorted({s for s in section_names if s not in SETUP_SECTION_CHOICES})
        if invalid:
            raise ValueError("Unsupported setup section(s): " + ", ".join(invalid))
        partial_sections = set(section_names)

    config_present, _ = config_exists()
    if not partial_sections and config_present:
        user_sections = _prompt_for_setup_sections()
        if user_sections:
            partial_sections = user_sections

    is_partial_run = partial_sections is not None

    def wants(section: str) -> bool:
        """Return True when the current run should prompt for the given section."""

        return partial_sections is None or section in partial_sections

    if is_partial_run:
        section_list = ", ".join(sorted(partial_sections))
        print(f"Updating Fetchtastic setup sections: {section_list}")
    else:
        print("Running Fetchtastic Setup...")

    # Determine if this is a first run
    config_present, _ = config_exists()
    is_first_run = not config_present

    # Handle base setup (Termux packages, base directory, Windows shortcuts)
    config = _setup_base({}, is_partial_run, is_first_run, wants)

    # Handle download type selection and asset menus
    config, save_apks, save_firmware = _setup_downloads(config, is_partial_run, wants)

    # If both save_apks and save_firmware are False, exit setup
    if not save_apks and not save_firmware:
        return

    # Determine default number of versions to keep based on platform
    default_versions_to_keep = 2 if is_termux() else 3

    # Handle Android configuration
    if save_apks and (not is_partial_run or wants("android")):
        config = _setup_android(config, is_first_run, default_versions_to_keep)
    # Handle firmware configuration
    if save_firmware and (not is_partial_run or wants("firmware")):
        config = _setup_firmware(config, is_first_run, default_versions_to_keep)

    # Ask if the user wants to only download when connected to Wi-Fi (Termux only)
    if is_termux():
        if not is_partial_run or wants("base"):
            wifi_only_default = "yes" if config.get("WIFI_ONLY", True) else "no"
            wifi_only = (
                input(
                    f"Do you want to only download when connected to Wi-Fi? [y/n] (default: {wifi_only_default}): "
                )
                .strip()
                .lower()
                or wifi_only_default[0]
            )
            config["WIFI_ONLY"] = True if wifi_only == "y" else False
    else:
        if not is_partial_run or wants("base"):
            # For non-Termux environments, remove WIFI_ONLY from config if it exists
            config.pop("WIFI_ONLY", None)

    # Set the download directory to the same as the base directory
    download_dir = BASE_DIR
    config["DOWNLOAD_DIR"] = download_dir

    # Record the version at which setup was last run
    try:
        from importlib.metadata import PackageNotFoundError, version

        current_version = version("fetchtastic")
        config["LAST_SETUP_VERSION"] = current_version
        config["LAST_SETUP_DATE"] = datetime.now().isoformat()
    except PackageNotFoundError:
        # If we can't get the version, just record the date
        config["LAST_SETUP_DATE"] = datetime.now().isoformat()

    # Make sure the config directory exists
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except OSError as e:
            print(f"Error creating config directory: {e}")

    # Handle automation configuration
    config = _setup_automation(config, is_partial_run, wants)

    # Handle notifications configuration
    if not is_partial_run or wants("notifications"):
        config = _setup_notifications(config)

    # Handle GitHub token configuration
    if not is_partial_run or wants("github"):
        config = _setup_github(config)

    # Persist configuration after all interactive sections
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f)
    print(f"Configuration saved to: {CONFIG_FILE}")

    if not is_partial_run:
        # Ask if the user wants to perform a first run
        if platform.system() == "Windows":
            # On Windows, we'll just tell them how to run it
            print("Setup complete. Run 'fetchtastic download' to start downloading.")
            if WINDOWS_MODULES_AVAILABLE:
                print("You can also use the shortcuts created in the Start Menu.")

            # If running from a batch file or shortcut, pause at the end
            if os.environ.get("PROMPT") is None or "cmd.exe" in os.environ.get(
                "COMSPEC", ""
            ):
                print("\nPress Enter to close this window...")
                input()
        else:
            # On other platforms, offer to run it now
            perform_first_run = (
                input(
                    "Would you like to start the first run now? [y/n] (default: yes): "
                )
                .strip()
                .lower()
                or "y"
            )
            if perform_first_run == "y":
                from fetchtastic import (
                    downloader,  # Local import to break circular dependency
                )

                print(
                    "Setup complete. Starting first run, this may take a few minutes..."
                )
                downloader.main()
            else:
                print(
                    "Setup complete. Run 'fetchtastic download' to start downloading."
                )
    else:
        print("Selected setup sections updated. Run 'fetchtastic download' when ready.")


def check_for_updates():
    """
    Check whether a newer release of Fetchtastic is available on PyPI.

    Performs a local read of the installed package version and queries the PyPI JSON API
    for the latest release. Compares versions using PEP 440-aware parsing.

    Returns:
        tuple: (current_version, latest_version, update_available)
            - current_version (str): the installed fetchtastic version or "unknown" if it cannot be determined.
            - latest_version (str|None): the latest version string from PyPI, or None if the lookup failed.
            - update_available (bool): True if a newer release exists on PyPI, False otherwise (including on lookup errors).
    """
    try:
        # Get current version
        from importlib.metadata import version

        current_version = version("fetchtastic")

        # Get latest version from PyPI
        import requests

        response = requests.get("https://pypi.org/pypi/fetchtastic/json", timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_version = data["info"]["version"]
            # Use packaging.version for proper version comparison
            from packaging import version as pkg_version

            current_ver = pkg_version.parse(current_version)
            latest_ver = pkg_version.parse(latest_version)
            return current_version, latest_version, latest_ver > current_ver
        return current_version, None, False
    except Exception:
        # If anything fails, just return that no update is available
        try:
            from importlib.metadata import version

            return version("fetchtastic"), None, False
        except Exception:
            return "unknown", None, False


def get_upgrade_command():
    """
    Returns the appropriate shell command to upgrade Fetchtastic for the current platform and installation method.

    On Termux, selects between pip and pipx based on how Fetchtastic was installed. On other platforms, defaults to pipx.
    """
    if is_termux():
        # Check how fetchtastic is installed in Termux
        install_method = get_fetchtastic_installation_method()
        if install_method == "pip":
            return "pip install --upgrade fetchtastic"
        else:
            # Default to pipx for new installations and pipx installations
            return "pipx upgrade fetchtastic"
    else:
        return "pipx upgrade fetchtastic"


def should_recommend_setup():
    """
    Determine whether running the interactive setup should be recommended.

    Checks for an existing configuration and compares the recorded setup version to the currently installed package version.
    Returns a tuple (should_recommend, reason, last_setup_version, current_version):

    - should_recommend (bool): True if setup is recommended (no config, missing recorded setup version, version changed, or an error occurred); False if setup appears up-to-date.
    - reason (str): Short human-readable explanation for the recommendation.
    - last_setup_version (str | None): Version value stored in the configuration under "LAST_SETUP_VERSION", or None if unavailable.
    - current_version (str | None): Currently installed fetchtastic package version as reported by importlib.metadata, or None if it could not be determined.
    """
    try:
        config = load_config()
        if not config:
            return True, "No configuration found", None, None

        last_setup_version = config.get("LAST_SETUP_VERSION")
        if not last_setup_version:
            return True, "Setup version not tracked", None, None

        # Get current version
        from importlib.metadata import version

        current_version = version("fetchtastic")

        if last_setup_version != current_version:
            return (
                True,
                f"Version changed from {last_setup_version} to {current_version}",
                last_setup_version,
                current_version,
            )

        return False, "Setup is current", last_setup_version, current_version

    except Exception:
        return True, "Could not determine setup status", None, None


def display_version_info(show_update_message=True):
    """
    Retrieves the current and latest Fetchtastic version information and update status.

    Returns:
        A tuple of (current_version, latest_version, update_available), where update_available is True if a newer version is available.
    """
    current_version, latest_version, update_available = check_for_updates()

    # Return version information without printing
    # The caller will handle logging/printing as appropriate
    return current_version, latest_version, update_available


def migrate_config():
    """
    Migrates the configuration from the old location to the new location.

    Returns:
        bool: True if migration was successful, False otherwise.
    """
    # Import here to avoid circular imports
    from fetchtastic.log_utils import logger

    # Check if old config exists
    if not os.path.exists(OLD_CONFIG_FILE):
        return False

    # Check if new config directory exists, create if not
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating config directory: {e}")
            return False

    # Load the old config
    try:
        with open(OLD_CONFIG_FILE, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading old config: {e}")
        return False

    # Save to new location
    try:
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

        # Remove the old file after successful migration
        try:
            os.remove(OLD_CONFIG_FILE)
            logger.info(f"Configuration migrated to {CONFIG_FILE} and old file removed")
        except Exception as e:
            logger.error(
                f"Configuration migrated to {CONFIG_FILE} but failed to remove old file: {e}"
            )

        return True
    except Exception as e:
        logger.error(f"Error saving config to new location: {e}")
        return False


def prompt_for_migration():
    """
    Automatically migrates the configuration from the old location to the new location
    without prompting the user.

    Returns:
        bool: Always returns True to indicate migration should proceed.
    """
    # Import here to avoid circular imports
    from fetchtastic.log_utils import logger

    logger.info(f"Found configuration file at old location: {OLD_CONFIG_FILE}")
    logger.info(f"Automatically migrating to the new location: {CONFIG_FILE}")
    return True


def create_windows_menu_shortcuts(config_file_path, base_dir):
    """
    Create Windows Start Menu shortcuts and supporting batch files for Fetchtastic.

    Creates a Fetchtastic folder in the user's Start Menu containing shortcuts to:
    - a download runner, repository browser, setup, update checker (all implemented as batch files placed in CONFIG_DIR/batch),
    - the Fetchtastic configuration file,
    - the Meshtastic downloads base directory,
    - the Fetchtastic log file.

    This function is a no-op on non-Windows platforms or when required Windows modules are unavailable.

    Parameters:
        config_file_path (str): Full path to the Fetchtastic YAML configuration file used as the target for the configuration shortcut.
        base_dir (str): Path to the Meshtastic downloads base directory used as the target for the downloads-folder shortcut.

    Returns:
        bool: True if shortcuts and batch files were created successfully; False if running on a non-Windows platform, required Windows modules are missing, or any error occurred while creating files/shortcuts.

    Side effects:
        - May create CONFIG_DIR/batch and several .bat files.
        - May create or recreate the Start Menu folder at WINDOWS_START_MENU_FOLDER and write .lnk shortcuts.
        - May create the user log directory and an empty log file if none exists.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        # Completely remove the Start Menu folder and recreate it
        # This ensures we don't have any leftover shortcuts
        import shutil

        # First, make sure the parent directory exists
        parent_dir = os.path.dirname(WINDOWS_START_MENU_FOLDER)
        if not os.path.exists(parent_dir):
            print(f"Warning: Parent directory {parent_dir} does not exist")
            # Try to create the parent directory structure
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                print(f"Error creating parent directory: {e}")
                return False

        # Now handle the Fetchtastic folder
        if os.path.exists(WINDOWS_START_MENU_FOLDER):
            print(f"Removing existing Start Menu folder: {WINDOWS_START_MENU_FOLDER}")
            try:
                # Try to remove the entire folder
                shutil.rmtree(WINDOWS_START_MENU_FOLDER)
                print("Successfully removed existing shortcuts folder")
            except Exception as e:
                print(f"Warning: Could not remove shortcuts folder: {e}")
                # Try to remove individual files as a fallback
                try:
                    # First list all files
                    files = os.listdir(WINDOWS_START_MENU_FOLDER)
                    print(f"Found {len(files)} files in shortcuts folder")

                    # Try to remove each file
                    for file in files:
                        file_path = os.path.join(WINDOWS_START_MENU_FOLDER, file)
                        try:
                            if os.path.isfile(file_path):
                                os.remove(file_path)
                                print(f"Removed: {file}")
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                                print(f"Removed directory: {file}")
                        except Exception as e3:
                            print(f"Could not remove {file}: {e3}")

                    print("Attempted to remove individual files")
                except Exception as e2:
                    print(f"Warning: Could not clean shortcuts folder: {e2}")
                    print("Will attempt to overwrite existing shortcuts")

        # Create a fresh Fetchtastic folder in the Start Menu
        print(f"Creating Start Menu folder: {WINDOWS_START_MENU_FOLDER}")
        try:
            os.makedirs(WINDOWS_START_MENU_FOLDER, exist_ok=True)
        except Exception as e:
            print(f"Error creating Start Menu folder: {e}")
            return False

        # Get the path to the fetchtastic executable
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return False

        # Create batch files in the config directory instead of the Start Menu
        batch_dir = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        # Create a batch file for download that pauses at the end
        download_batch_path = os.path.join(batch_dir, "fetchtastic_download.bat")
        with open(download_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Download\n")
            f.write(f'"{fetchtastic_path}" download\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for repo browse that pauses at the end
        repo_batch_path = os.path.join(batch_dir, "fetchtastic_repo_browse.bat")
        with open(repo_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Repository Browser\n")
            f.write(f'"{fetchtastic_path}" repo browse\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for setup that pauses at the end
        setup_batch_path = os.path.join(batch_dir, "fetchtastic_setup.bat")
        with open(setup_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Setup\n")
            f.write(f'"{fetchtastic_path}" setup\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for checking updates that pauses at the end
        update_batch_path = os.path.join(batch_dir, "fetchtastic_update.bat")
        with open(update_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Update Check\n")
            f.write("echo Checking for Fetchtastic updates...\n")
            f.write("echo.\n")

            # Use pipx to upgrade fetchtastic with improved logic
            pipx_path = shutil.which("pipx")
            if pipx_path:
                f.write("echo Attempting to upgrade Fetchtastic...\n")
                f.write(f'"{pipx_path}" upgrade fetchtastic\n')
                f.write("if %ERRORLEVEL% EQU 0 (\n")
                f.write("    echo Upgrade completed successfully!\n")
                f.write(") else (\n")
                f.write("    echo Upgrade failed or already at latest version.\n")
                f.write("    echo Trying force reinstall...\n")
                f.write(f'    "{pipx_path}" install "fetchtastic[win]" --force\n')
                f.write("    if %ERRORLEVEL% EQU 0 (\n")
                f.write("        echo Force reinstall completed successfully!\n")
                f.write("    ) else (\n")
                f.write(
                    "        echo Force reinstall failed. Trying uninstall/reinstall...\n"
                )
                f.write(
                    f'        "{pipx_path}" uninstall fetchtastic --force >nul 2>&1\n'
                )
                f.write(f'        "{pipx_path}" install "fetchtastic[win]"\n')
                f.write("        if %ERRORLEVEL% EQU 0 (\n")
                f.write("            echo Reinstall completed successfully!\n")
                f.write("        ) else (\n")
                f.write(
                    "            echo All upgrade methods failed. Please check your internet connection.\n"
                )
                f.write("        )\n")
                f.write("    )\n")
                f.write(")\n")
                f.write("echo.\n")
                f.write("echo Checking final version...\n")
                f.write("fetchtastic version\n")
            else:
                # Fallback to pip if pipx is not found
                pip_path = shutil.which("pip")
                if pip_path:
                    f.write(f'"{pip_path}" install --upgrade "fetchtastic[win]"\n')
                else:
                    f.write("echo Error: Neither pipx nor pip was found in PATH.\n")
                    f.write("echo Please install pipx or pip to upgrade Fetchtastic.\n")
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create shortcut for fetchtastic download (using batch file)
        download_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Download.lnk"
        )
        winshell.CreateShortcut(
            Path=download_shortcut_path,
            Target=download_batch_path,
            Description="Download Meshtastic firmware and APKs",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for fetchtastic setup (using batch file)
        setup_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Setup.lnk"
        )
        winshell.CreateShortcut(
            Path=setup_shortcut_path,
            Target=setup_batch_path,
            Description="Configure Fetchtastic settings",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for fetchtastic repo browse (using batch file)
        repo_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Repository Browser.lnk"
        )
        winshell.CreateShortcut(
            Path=repo_shortcut_path,
            Target=repo_batch_path,
            Description="Browse and download files from the Meshtastic repository",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut to configuration file
        config_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Configuration.lnk"
        )
        winshell.CreateShortcut(
            Path=config_shortcut_path,
            Target=config_file_path,
            Description=f"Edit Fetchtastic Configuration File ({CONFIG_FILE_NAME})",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut to Meshtastic base directory
        base_dir_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Meshtastic Downloads.lnk"
        )
        winshell.CreateShortcut(
            Path=base_dir_shortcut_path,
            Target=base_dir,
            Description="Open Meshtastic Downloads Folder",
            Icon=(
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "explorer.exe"),
                0,
            ),
        )

        # Create shortcut to log file
        # First check if there's a log file in the base directory (old location)
        base_log_file = os.path.join(BASE_DIR, "fetchtastic.log")
        if os.path.exists(base_log_file):
            log_file = base_log_file
        else:
            # Use the platformdirs log location
            log_dir = platformdirs.user_log_dir("fetchtastic")
            log_file = os.path.join(log_dir, "fetchtastic.log")
            # Create log directory if it doesn't exist
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            # Create an empty log file if it doesn't exist
            if not os.path.exists(log_file):
                with open(log_file, "w") as f:
                    f.write("# Fetchtastic log file\n")
                print(f"Created empty log file at: {log_file}")

        log_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Log.lnk"
        )
        winshell.CreateShortcut(
            Path=log_shortcut_path,
            Target=log_file,
            Description="View Fetchtastic Log File",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for checking updates
        update_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic - Check for Updates.lnk"
        )
        winshell.CreateShortcut(
            Path=update_shortcut_path,
            Target=update_batch_path,
            Description="Check for and install Fetchtastic updates",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        print("Shortcuts created in Start Menu")
        return True
    except Exception as e:
        print(f"Failed to create Windows Start Menu shortcuts: {e}")
        return False


def create_config_shortcut(config_file_path, target_dir):
    """
    Creates a shortcut to the configuration file in the target directory.
    Only works on Windows.

    Args:
        config_file_path: Path to the configuration file
        target_dir: Directory where to create the shortcut

    Returns:
        bool: True if shortcut was created successfully, False otherwise
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        shortcut_path = os.path.join(target_dir, "fetchtastic_yaml.lnk")

        # Create the shortcut using winshell
        winshell.CreateShortcut(
            Path=shortcut_path,
            Target=config_file_path,
            Description=f"Fetchtastic Configuration File ({CONFIG_FILE_NAME})",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        print(f"Created shortcut to configuration file at: {shortcut_path}")
        return True
    except Exception as e:
        print(f"Failed to create shortcut to configuration file: {e}")
        return False


def create_startup_shortcut():
    """
    Creates a shortcut to run fetchtastic on Windows startup.
    Only works on Windows.

    Returns:
        bool: True if shortcut was created successfully, False otherwise
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        # Get the path to the fetchtastic executable
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return False

        # Get the startup folder path
        startup_folder = winshell.startup()

        # Create batch files in the config directory instead of the startup folder
        batch_dir = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        # Create a batch file for startup that runs silently
        batch_path = os.path.join(batch_dir, "fetchtastic_startup.bat")
        with open(batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Automatic Download\n")
            f.write(f'"{fetchtastic_path}" download\n')
            # Don't pause at the end for startup - we want it to run silently

        # Create the shortcut to the batch file
        shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")

        # Use direct shortcut creation without WindowStyle parameter
        try:
            # First try with WindowStyle parameter (newer versions of winshell)
            winshell.CreateShortcut(
                Path=shortcut_path,
                Target=batch_path,
                Description="Run Fetchtastic on startup",
                Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
                WindowStyle=7,  # Minimized
            )
        except TypeError:
            # If WindowStyle is not supported, use basic parameters
            winshell.CreateShortcut(
                Path=shortcut_path,
                Target=batch_path,
                Description="Run Fetchtastic on startup",
                Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
            )

        print(f"Created startup shortcut at: {shortcut_path}")
        return True
    except Exception as e:
        print(f"Failed to create startup shortcut: {e}")
        return False


def copy_to_clipboard_func(text):
    """
    Copies the provided text to the clipboard, depending on the platform.
    """
    if is_termux():
        # Termux environment
        try:
            subprocess.run(
                ["termux-clipboard-set"], input=text.encode("utf-8"), check=True
            )
            return True
        except Exception as e:
            print(f"An error occurred while copying to clipboard: {e}")
            return False
    elif platform.system() == "Windows" and WINDOWS_MODULES_AVAILABLE:
        # Windows environment with win32com available
        try:
            import win32clipboard

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
            win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            print(f"An error occurred while copying to clipboard: {e}")
            return False
    else:
        # Other platforms
        system = platform.system()
        try:
            if system == "Darwin":
                # macOS
                subprocess.run("pbcopy", text=True, input=text, check=True)
                return True
            elif system == "Linux":
                # Linux
                if shutil.which("xclip"):
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=text.encode("utf-8"),
                        check=True,
                    )
                    return True
                elif shutil.which("xsel"):
                    subprocess.run(
                        ["xsel", "--clipboard", "--input"],
                        input=text.encode("utf-8"),
                        check=True,
                    )
                    return True
                else:
                    print(
                        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
                    )
                    return False
            else:
                print("Clipboard functionality is not supported on this platform.")
                return False
        except Exception as e:
            print(f"An error occurred while copying to clipboard: {e}")
            return False


def install_termux_packages():
    """
    Installs required packages in the Termux environment.
    """
    # Install termux-api, termux-services, and cronie if they are not installed
    packages_to_install = []
    # Check for termux-api
    if shutil.which("termux-battery-status") is None:
        packages_to_install.append("termux-api")
    # Check for termux-services
    if shutil.which("sv-enable") is None:
        packages_to_install.append("termux-services")
    # Check for cronie
    if shutil.which("crond") is None:
        packages_to_install.append("cronie")
    if packages_to_install:
        print("Installing required Termux packages...")
        subprocess.run(["pkg", "install"] + packages_to_install + ["-y"], check=True)
        print("Required Termux packages installed.")
    else:
        print("All required Termux packages are already installed.")


def setup_storage():
    """
    Runs termux-setup-storage to set up storage access in Termux.
    """
    # Run termux-setup-storage
    print("Setting up Termux storage access...")
    try:
        subprocess.run(["termux-setup-storage"], check=True)
    except subprocess.CalledProcessError:
        print("An error occurred while setting up Termux storage.")
        print("Please grant storage permissions when prompted.")


def install_crond():
    """
    Installs and enables the crond service in Termux.
    """
    if is_termux():
        try:
            crond_path = shutil.which("crond")
            if crond_path is None:
                print("Installing cronie...")
                # Install cronie
                subprocess.run(["pkg", "install", "cronie", "-y"], check=True)
                print("cronie installed.")
            else:
                print("cronie is already installed.")
            # Enable crond service
            subprocess.run(["sv-enable", "crond"], check=True)
            print("crond service enabled.")
        except Exception as e:
            print(f"An error occurred while installing or enabling crond: {e}")
    else:
        # For non-Termux environments, crond installation is not needed
        pass


def setup_cron_job(frequency="hourly"):
    """
    Configure or replace a system cron entry to run Fetchtastic on a regular schedule.

    This function updates the user's crontab by removing existing Fetchtastic entries (except `@reboot` lines) and writing a single scheduled entry that invokes the Fetchtastic downloader. On Windows the function is a no-op. On Termux the entry uses the plain `fetchtastic download` command; on other platforms it uses the `fetchtastic` executable discovered in PATH. If an invalid frequency is provided, the function falls back to the hourly schedule.

    Parameters:
        frequency (str): Schedule key specifying cadence; commonly `"hourly"` or `"daily"`. Defaults to `"hourly"` when omitted or invalid.
    """
    # Skip cron job setup on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    # Validate frequency and get schedule info
    if frequency not in CRON_SCHEDULES:
        print(f"Warning: Invalid cron frequency '{frequency}'. Defaulting to hourly.")
        frequency = "hourly"

    schedule_info = CRON_SCHEDULES[frequency]
    cron_schedule = schedule_info["schedule"]
    frequency_desc = schedule_info["desc"]

    try:
        # Get current crontab entries
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            existing_cron = ""
        else:
            existing_cron = result.stdout.strip()

        # Remove existing Fetchtastic cron jobs (excluding @reboot ones)
        cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
        cron_lines = [
            line
            for line in cron_lines
            if not (
                ("# fetchtastic" in line or "fetchtastic download" in line)
                and not line.strip().startswith("@reboot")
            )
        ]

        # Add new cron job
        if is_termux():
            cron_lines.append(f"{cron_schedule} fetchtastic download  # fetchtastic")
        else:
            # Non-Termux environments
            fetchtastic_path = shutil.which("fetchtastic")
            if not fetchtastic_path:
                print("Error: fetchtastic executable not found in PATH.")
                return
            cron_lines.append(
                f"{cron_schedule} {fetchtastic_path} download  # fetchtastic"
            )

        # Join cron lines
        new_cron = "\n".join(cron_lines)

        # Ensure new_cron ends with a newline
        if not new_cron.endswith("\n"):
            new_cron += "\n"

        # Update crontab
        process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_cron)
        print(f"Cron job added to run Fetchtastic {frequency_desc}.")
    except Exception as e:
        print(f"An error occurred while setting up the cron job: {e}")


def remove_cron_job():
    """
    Removes the Fetchtastic daily cron job from the crontab.
    On Windows, this function does nothing as cron jobs are not supported.
    """
    # Skip cron job removal on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    try:
        # Get current crontab entries
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            existing_cron = result.stdout.strip()
            # Remove existing Fetchtastic cron jobs (excluding @reboot)
            cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
            cron_lines = [
                line
                for line in cron_lines
                if not (
                    ("# fetchtastic" in line or "fetchtastic download" in line)
                    and not line.strip().startswith("@reboot")
                )
            ]
            # Join cron lines
            new_cron = "\n".join(cron_lines)
            # Ensure new_cron ends with a newline
            if not new_cron.endswith("\n"):
                new_cron += "\n"
            # Update crontab
            process = subprocess.Popen(
                ["crontab", "-"], stdin=subprocess.PIPE, text=True
            )
            process.communicate(input=new_cron)
            print("Daily cron job removed.")
    except Exception as e:
        print(f"An error occurred while removing the cron job: {e}")


def setup_boot_script():
    """
    Sets up a boot script in Termux to run Fetchtastic on device boot.
    """
    boot_dir = os.path.expanduser("~/.termux/boot")
    boot_script = os.path.join(boot_dir, "fetchtastic.sh")
    if not os.path.exists(boot_dir):
        os.makedirs(boot_dir)
        print("Created the Termux:Boot directory.")
        print(
            "Please install Termux:Boot from F-Droid and run it once to enable boot scripts."
        )
    # Write the boot script
    with open(boot_script, "w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write("sleep 30\n")
        f.write("fetchtastic download\n")
    os.chmod(boot_script, 0o700)
    print("Boot script created to run Fetchtastic on device boot.")
    print(
        "Note: The script may not run on boot until you have installed and run Termux:Boot at least once."
    )


def remove_boot_script():
    """
    Removes the boot script from Termux.
    """
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print("Boot script removed.")


def setup_reboot_cron_job():
    """
    Sets up a cron job to run Fetchtastic on system startup (non-Termux).
    On Windows, this function does nothing as cron jobs are not supported.
    """
    # Skip cron job setup on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    try:
        # Get current crontab entries
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            existing_cron = ""
        else:
            existing_cron = result.stdout.strip()

        # Remove existing @reboot Fetchtastic cron jobs
        cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
        cron_lines = [
            line
            for line in cron_lines
            if not (
                ("# fetchtastic" in line or "fetchtastic download" in line)
                and line.strip().startswith("@reboot")
            )
        ]

        # Add new @reboot cron job
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return
        cron_lines.append(f"@reboot {fetchtastic_path} download  # fetchtastic")

        # Join cron lines
        new_cron = "\n".join(cron_lines)

        # Ensure new_cron ends with a newline
        if not new_cron.endswith("\n"):
            new_cron += "\n"

        # Update crontab
        process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_cron)
        print("Reboot cron job added to run Fetchtastic on system startup.")
    except Exception as e:
        print(f"An error occurred while setting up the reboot cron job: {e}")


def remove_reboot_cron_job():
    """
    Removes the reboot cron job from the crontab.
    On Windows, this function does nothing as cron jobs are not supported.
    """
    # Skip cron job removal on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return
    try:
        # Get current crontab entries
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            existing_cron = result.stdout.strip()
            # Remove existing @reboot Fetchtastic cron jobs
            cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
            cron_lines = [
                line
                for line in cron_lines
                if not (
                    ("# fetchtastic" in line or "fetchtastic download" in line)
                    and line.strip().startswith("@reboot")
                )
            ]
            # Join cron lines
            new_cron = "\n".join(cron_lines)
            # Ensure new_cron ends with a newline
            if not new_cron.endswith("\n"):
                new_cron += "\n"
            # Update crontab
            process = subprocess.Popen(
                ["crontab", "-"], stdin=subprocess.PIPE, text=True
            )
            process.communicate(input=new_cron)
            print("Reboot cron job removed.")
    except Exception as e:
        print(f"An error occurred while removing the reboot cron job: {e}")


def check_cron_job_exists():
    """
    Checks if a Fetchtastic daily cron job already exists.
    On Windows, always returns False as cron jobs are not supported.
    """
    # Skip cron job check on Windows
    if platform.system() == "Windows":
        return False

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return False
        existing_cron = result.stdout.strip()
        return any(
            ("# fetchtastic" in line or "fetchtastic download" in line)
            for line in existing_cron.splitlines()
            if not line.strip().startswith("@reboot")
        )
    except Exception as e:
        print(f"An error occurred while checking for existing cron jobs: {e}")
        return False


def check_boot_script_exists():
    """
    Checks if a Fetchtastic boot script already exists (Termux).
    """
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    return os.path.exists(boot_script)


def check_any_cron_jobs_exist():
    """
    Checks if any Fetchtastic cron jobs (daily or reboot) already exist.
    On Windows, always returns False as cron jobs are not supported.
    """
    # Skip cron job check on Windows
    if platform.system() == "Windows":
        return False

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return False
        existing_cron = result.stdout.strip()
        return any(
            ("# fetchtastic" in line or "fetchtastic download" in line)
            for line in existing_cron.splitlines()
        )
    except Exception as e:
        print(f"An error occurred while checking for existing cron jobs: {e}")
        return False


def load_config(directory=None):
    """
    Load the Fetchtastic configuration YAML and update module state.

    If `directory` is provided, loads CONFIG_FILE_NAME from that directory (backwards-compatibility or explicit load),
    sets the global BASE_DIR to that directory, and warns if the file is in a non-standard location.
    If `directory` is not provided, prefers the platformdirs-managed CONFIG_FILE, falling back to the old location OLD_CONFIG_FILE.
    When a loaded config contains a "BASE_DIR" key, the global BASE_DIR is updated from that value.

    Parameters:
        directory (str | None): Optional directory to load the config from. If None, the function checks CONFIG_FILE
            then OLD_CONFIG_FILE.

    Returns:
        dict | None: The parsed configuration dictionary on success, or None if no configuration file was found.
    """
    global BASE_DIR

    if directory:
        # This is for backward compatibility or when explicitly loading from a specific directory
        config_path = os.path.join(directory, CONFIG_FILE_NAME)
        if not os.path.exists(config_path):
            return None

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        # Update global variables
        BASE_DIR = directory

        # If we're loading from a non-standard location, check if we should migrate
        if config_path != CONFIG_FILE and config_path != OLD_CONFIG_FILE:
            print(f"Found configuration in non-standard location: {config_path}")
            print(f"Consider migrating to the standard location: {CONFIG_FILE}")

        return config
    else:
        # First check if config exists in the platformdirs location
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = yaml.safe_load(f)

            # Update BASE_DIR from config
            if "BASE_DIR" in config:
                BASE_DIR = config["BASE_DIR"]

            return config

        # Then check the old location
        elif os.path.exists(OLD_CONFIG_FILE):
            with open(OLD_CONFIG_FILE, "r") as f:
                config = yaml.safe_load(f)

            # Update BASE_DIR from config
            if "BASE_DIR" in config:
                BASE_DIR = config["BASE_DIR"]

            # Suggest migration
            print(f"Using configuration from old location: {OLD_CONFIG_FILE}")
            print(
                f"Consider running setup to migrate to the new location: {CONFIG_FILE}"
            )

            return config

        return None
