# src/fetchtastic/setup_config.py

import os
import platform
import random
import shutil
import string
import subprocess
import sys

import platformdirs
import yaml

from fetchtastic import downloader  # Import downloader to perform first run
from fetchtastic import menu_apk, menu_firmware

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


def is_termux():
    """
    Check if the script is running in a Termux environment.
    """
    return "com.termux" in os.environ.get("PREFIX", "")


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
DEFAULT_BASE_DIR = os.path.join(DOWNLOADS_DIR, "Meshtastic")

# Get the config directory using platformdirs
CONFIG_DIR = platformdirs.user_config_dir("fetchtastic")

# Old config file location (for migration)
OLD_CONFIG_FILE = os.path.join(DEFAULT_BASE_DIR, "fetchtastic.yaml")

# New config file location using platformdirs
CONFIG_FILE = os.path.join(CONFIG_DIR, "fetchtastic.yaml")

# These will be set during setup or when loading config
BASE_DIR = DEFAULT_BASE_DIR


def config_exists(directory=None):
    """
    Check if the configuration file exists.

    Args:
        directory: Optional directory to check for config file. If None, checks both the new
                  platformdirs location and the old location.

    Returns:
        tuple: (exists, path) where exists is a boolean indicating if the config exists,
               and path is the path to the config file if it exists, otherwise None.
    """
    if directory:
        config_path = os.path.join(directory, "fetchtastic.yaml")
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
    For Termux: Check if the storage is set up and accessible.
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


def run_setup():
    global BASE_DIR, CONFIG_FILE
    print("Running Fetchtastic Setup...")

    # Install required Termux packages first
    if is_termux():
        install_termux_packages()
        # Check if storage is set up
        check_storage_setup()
        print("Termux storage is set up.")

    # Check if config directory exists, create if not
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            print(f"Error creating config directory: {e}")

    # Check for configuration in old location
    if os.path.exists(OLD_CONFIG_FILE) and not os.path.exists(CONFIG_FILE):
        # Import here to avoid circular imports
        from fetchtastic.log_utils import logger

        separator = "=" * 80
        logger.info(f"\n{separator}")
        logger.info("Configuration Migration")
        logger.info(separator)
        # Automatically migrate without prompting
        prompt_for_migration()  # Just logs the migration message
        if migrate_config():
            logger.info("Configuration successfully migrated to the new location.")
            # Update config_path to the new location for subsequent operations
            config_path = CONFIG_FILE
            # Re-load the configuration from the new location if it exists
            if os.path.exists(CONFIG_FILE):
                exists = True
        else:
            logger.error(
                "Failed to migrate configuration. Continuing with old location."
            )
        logger.info(f"{separator}\n")

    # Ask for base directory as the first question
    config = {}
    exists, config_path = config_exists()

    if exists:
        # Load existing configuration
        config = load_config()
        print(
            "Existing configuration found. You can keep current settings or change them."
        )
        is_first_run = False
        current_base_dir = config.get("BASE_DIR", DEFAULT_BASE_DIR)
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (current: {current_base_dir}): "
        )
    else:
        # Initialize default configuration
        config = {}
        is_first_run = True
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (default: {DEFAULT_BASE_DIR}): "
        )

    # Prompt for base directory
    base_dir_input = input(base_dir_prompt).strip()

    if base_dir_input:
        # User entered a custom directory
        base_dir = os.path.expanduser(base_dir_input)

        # Check if there's a config file in the specified directory
        if config_exists(base_dir) and base_dir != BASE_DIR:
            print(f"Found existing configuration in {base_dir}")
            # Load the configuration from the specified directory
            config = load_config(base_dir)
            is_first_run = False
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

    # Store the base directory in the config
    config["BASE_DIR"] = BASE_DIR

    # Create the base directory if it doesn't exist
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)

    # On Windows, handle shortcuts
    if platform.system() == "Windows":
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
            print("pipx install -e .[win]")
            print("or if using pip: pip install fetchtastic[win]")

    # Prompt to save APKs, firmware, or both
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
    config["SAVE_APKS"] = save_apks
    config["SAVE_FIRMWARE"] = save_firmware

    # Run the menu scripts based on user choices
    if save_apks:
        apk_selection = menu_apk.run_menu()
        if not apk_selection:
            print("No APK assets selected. APKs will not be downloaded.")
            save_apks = False
            config["SAVE_APKS"] = False
        else:
            config["SELECTED_APK_ASSETS"] = apk_selection["selected_assets"]
    if save_firmware:
        firmware_selection = menu_firmware.run_menu()
        if not firmware_selection:
            print("No firmware assets selected. Firmware will not be downloaded.")
            save_firmware = False
            config["SAVE_FIRMWARE"] = False
        else:
            config["SELECTED_FIRMWARE_ASSETS"] = firmware_selection["selected_assets"]

    # If both save_apks and save_firmware are False, inform the user and exit setup
    if not save_apks and not save_firmware:
        print("Please select at least one type of asset to download (APK or firmware).")
        print("Run 'fetchtastic setup' again and select at least one asset.")
        return

    # Determine default number of versions to keep based on platform
    default_versions_to_keep = 2 if is_termux() else 3

    # Prompt for number of versions to keep
    if save_apks:
        current_versions = config.get(
            "ANDROID_VERSIONS_TO_KEEP", default_versions_to_keep
        )
        if is_first_run:
            prompt_text = f"How many versions of the Android app would you like to keep? (default is {current_versions}): "
        else:
            prompt_text = f"How many versions of the Android app would you like to keep? (current: {current_versions}): "
        android_versions_to_keep = input(prompt_text).strip() or str(current_versions)
        config["ANDROID_VERSIONS_TO_KEEP"] = int(android_versions_to_keep)
    if save_firmware:
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
        # Make sure we're setting a boolean value, not a string
        config["CHECK_PRERELEASES"] = check_prereleases == "y"

        # Save configuration immediately to ensure this setting is preserved
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

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

        # Save the AUTO_EXTRACT setting immediately
        config["AUTO_EXTRACT"] = auto_extract == "y"

        # Save configuration to ensure this setting is preserved
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

        if auto_extract == "y":
            print(
                "Enter the keywords to match for extraction from the firmware zip files, separated by spaces."
            )
            print("Example: rak4631- tbeam t1000-e- tlora-v2-1-1_6- device-")

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
                    # Skip exclude patterns prompt
                    config["EXCLUDE_PATTERNS"] = []

            # Save configuration again after updating patterns
            with open(CONFIG_FILE, "w") as f:
                yaml.dump(config, f)
            # Prompt for exclude patterns if extraction is enabled
            if config.get("AUTO_EXTRACT", False) and config.get("EXTRACT_PATTERNS"):
                exclude_default = "yes" if config.get("EXCLUDE_PATTERNS") else "no"
                exclude_prompt = f"Would you like to exclude any patterns from extraction? [y/n] (default: {exclude_default}): "
                exclude_choice = (
                    input(exclude_prompt).strip().lower() or exclude_default[0]
                )
                if exclude_choice == "y":
                    print(
                        "Enter the keywords to exclude from extraction, separated by spaces."
                    )
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
                            print(
                                f"Keeping current exclude patterns: {current_excludes}"
                            )
                        else:
                            # Get new exclude patterns
                            exclude_patterns = input(
                                "Enter new exclude patterns: "
                            ).strip()
                            if exclude_patterns:
                                config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                                print(
                                    f"Exclude patterns updated to: {exclude_patterns}"
                                )
                            else:
                                config["EXCLUDE_PATTERNS"] = []
                                print(
                                    "No exclude patterns entered. All matching files will be extracted."
                                )
                    else:
                        # No existing exclude patterns, get new ones
                        exclude_patterns = input("Exclude patterns: ").strip()
                        if exclude_patterns:
                            config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                            print(f"Exclude patterns set to: {exclude_patterns}")
                        else:
                            config["EXCLUDE_PATTERNS"] = []
                            print(
                                "No exclude patterns entered. All matching files will be extracted."
                            )
                else:
                    # User chose not to exclude patterns
                    config["EXCLUDE_PATTERNS"] = []
                    print(
                        "No exclude patterns will be used. All matching files will be extracted."
                    )
            else:
                config["EXCLUDE_PATTERNS"] = []
        else:
            config["AUTO_EXTRACT"] = False
            config["EXTRACT_PATTERNS"] = []
            config["EXCLUDE_PATTERNS"] = []

    # Ask if the user wants to only download when connected to Wi-Fi (Termux only)
    if is_termux():
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
        # For non-Termux environments, remove WIFI_ONLY from config if it exists
        config.pop("WIFI_ONLY", None)

    # Set the download directory to the same as the base directory
    download_dir = BASE_DIR
    config["DOWNLOAD_DIR"] = download_dir

    # Make sure the config directory exists
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            print(f"Error creating config directory: {e}")

    # Save configuration to YAML file before proceeding
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f)

    print(f"Configuration saved to: {CONFIG_FILE}")

    # Cron job setup
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
                        batch_path = os.path.join(batch_dir, "fetchtastic_startup.bat")
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

                # Then set up new cron job
                install_crond()
                setup_cron_job()
                print("Cron job has been reconfigured.")
            else:
                print("Cron job configuration left unchanged.")
        else:
            # Ask if the user wants to set up a cron job
            cron_default = "yes"  # Default to 'yes'
            setup_cron = (
                input(
                    f"Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: {cron_default}): "
                )
                .strip()
                .lower()
                or cron_default[0]
            )
            if setup_cron == "y":
                install_crond()
                setup_cron_job()
            else:
                print("Cron job has not been set up.")

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

                # Ask if they want to set up daily cron job
                cron_default = "yes"
                setup_cron = (
                    input(
                        f"Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: {cron_default}): "
                    )
                    .strip()
                    .lower()
                    or cron_default[0]
                )
                if setup_cron == "y":
                    setup_cron_job()
                    print("Daily cron job has been set up.")
                else:
                    print("Daily cron job will not be set up.")

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
            # Ask if they want to set up daily cron job
            cron_default = "yes"
            setup_cron = (
                input(
                    f"Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: {cron_default}): "
                )
                .strip()
                .lower()
                or cron_default[0]
            )
            if setup_cron == "y":
                setup_cron_job()
            else:
                print("Daily cron job has not been set up.")

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

    # Prompt for NTFY server configuration
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

        # Save configuration with NTFY settings
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

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

        # Save configuration with the new setting
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

        print("Notification settings have been saved.")

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
                with open(CONFIG_FILE, "w") as f:
                    yaml.dump(config, f)
                print("Notifications have been disabled.")
            else:
                print("Keeping existing notification settings.")
        else:
            # No existing notifications, just confirm they're disabled
            config["NTFY_TOPIC"] = ""
            config["NTFY_SERVER"] = ""
            config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
            with open(CONFIG_FILE, "w") as f:
                yaml.dump(config, f)
            print("Notifications will remain disabled.")

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
            input("Would you like to start the first run now? [y/n] (default: yes): ")
            .strip()
            .lower()
            or "y"
        )
        if perform_first_run == "y":
            print("Setup complete. Starting first run, this may take a few minutes...")
            downloader.main()
        else:
            print("Setup complete. Run 'fetchtastic download' to start downloading.")


def check_for_updates():
    """
    Check if a newer version of fetchtastic is available.

    Returns:
        tuple: (current_version, latest_version, update_available)
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
    Returns the appropriate upgrade command based on the environment.

    Returns:
        str: The command to upgrade fetchtastic
    """
    if is_termux():
        return "pip install --upgrade fetchtastic"
    else:
        return "pipx upgrade fetchtastic"


def display_version_info(show_update_message=True):
    """
    Display version information and update message if a newer version is available.

    Args:
        show_update_message: Whether to show the update message if a newer version is available.
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
    Creates Windows Start Menu shortcuts for fetchtastic.

    Args:
        config_file_path: Path to the configuration file
        base_dir: Base directory for Meshtastic downloads

    Returns:
        bool: True if shortcuts were created successfully, False otherwise
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
            # Use pipx to upgrade fetchtastic
            pipx_path = shutil.which("pipx")
            if pipx_path:
                f.write(f'"{pipx_path}" upgrade fetchtastic\n')
            else:
                # Fallback to pip if pipx is not found
                pip_path = shutil.which("pip")
                if pip_path:
                    f.write(f'"{pip_path}" install --upgrade fetchtastic\n')
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
            Description="Edit Fetchtastic Configuration File (fetchtastic.yaml)",
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
            Description="Fetchtastic Configuration File (fetchtastic.yaml)",
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


def setup_cron_job():
    """
    Sets up the cron job to run Fetchtastic at scheduled times.
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
            cron_lines.append("0 3 * * * fetchtastic download  # fetchtastic")
        else:
            # Non-Termux environments
            fetchtastic_path = shutil.which("fetchtastic")
            if not fetchtastic_path:
                print("Error: fetchtastic executable not found in PATH.")
                return
            cron_lines.append(f"0 3 * * * {fetchtastic_path} download  # fetchtastic")

        # Join cron lines
        new_cron = "\n".join(cron_lines)

        # Ensure new_cron ends with a newline
        if not new_cron.endswith("\n"):
            new_cron += "\n"

        # Update crontab
        process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_cron)
        print("Cron job added to run Fetchtastic daily at 3 AM.")
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
    Loads the configuration from the YAML file.
    Updates global variables based on the loaded configuration.

    Args:
        directory: Optional directory to load config from. If None, uses the platformdirs location
                  or falls back to the old location.
    """
    global BASE_DIR

    if directory:
        # This is for backward compatibility or when explicitly loading from a specific directory
        config_path = os.path.join(directory, "fetchtastic.yaml")
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
