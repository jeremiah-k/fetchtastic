# src/fetchtastic/setup_config.py

import os
from typing import List, Dict, Any, Optional, Tuple, Callable # Ensured Callable is here
import platform
import random
import shutil
import string
import subprocess
import sys

import platformdirs
import yaml

from fetchtastic import downloader
from fetchtastic import menu_apk, menu_firmware
from fetchtastic.log_utils import logger # Import new logger

WINDOWS_MODULES_AVAILABLE: bool
if platform.system() == "Windows":
    try:
        import winshell # type: ignore
        WINDOWS_MODULES_AVAILABLE = True
    except ImportError:
        WINDOWS_MODULES_AVAILABLE = False
        logger.warning( # Was print
            "Windows detected. For full Windows integration, install optional dependencies:"
        )
        logger.warning("pipx install -e .[win]") # Was print
        logger.warning("or if using pip: pip install fetchtastic[win]") # Was print
else:
    WINDOWS_MODULES_AVAILABLE = False

WINDOWS_START_MENU_FOLDER: str = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Fetchtastic",
)

def is_termux() -> bool:
    """
    Check if the script is running in a Termux environment.

    Returns:
        bool: True if running in Termux, False otherwise.
    """
    return "com.termux" in os.environ.get("PREFIX", "")

def get_platform() -> str:
    """
    Determine the platform on which the script is running.

    Returns:
        str: A string identifying the platform ("termux", "mac", "linux", "unknown").
    """
    if is_termux():
        return "termux"
    elif platform.system() == "Darwin":
        return "mac"
    elif platform.system() == "Linux":
        return "linux"
    else:
        return "unknown"

def validate_version_count(value: str, current_versions_str: str, min_val: int = 1, max_val: int = 10) -> int:
    """
    Validates and converts the input string for version count to an integer.

    Args:
        value (str): The input string from the user.
        current_versions_str (str): The string representation of the current/default number of versions.
        min_val (int): The minimum allowed version count.
        max_val (int): The maximum allowed version count.

    Returns:
        int: The validated version count.

    Raises:
        ValueError: If the input is not a valid integer or is outside the allowed range.
    """
    effective_value: str = value if value else current_versions_str
    try:
        count: int = int(effective_value)
        if not (min_val <= count <= max_val):
            raise ValueError(f"Version count must be between {min_val} and {max_val}. You entered '{count}'.")
        return count
    except ValueError as e:
        if "between" in str(e):
                raise
        else:
                raise ValueError(f"Invalid input. Please enter a number between {min_val} and {max_val}. You entered '{effective_value}'.")

def get_downloads_dir() -> str:
    """
    Get the default downloads directory based on the platform.

    Returns:
        str: The path to the default downloads directory.
    """
    storage_downloads: str
    if is_termux():
        storage_downloads = os.path.expanduser("~/storage/downloads")
        if os.path.exists(storage_downloads):
            return storage_downloads

    home_dir: str = os.path.expanduser("~")
    downloads_dir: str

    downloads_dir = os.path.join(home_dir, "Downloads")
    if os.path.exists(downloads_dir):
        return downloads_dir
    downloads_dir = os.path.join(home_dir, "Download")
    if os.path.exists(downloads_dir):
        return downloads_dir
    return home_dir

DOWNLOADS_DIR: str = get_downloads_dir()
DEFAULT_BASE_DIR: str = os.path.join(DOWNLOADS_DIR, "Meshtastic")
CONFIG_DIR: str = platformdirs.user_config_dir("fetchtastic") # type: ignore
OLD_CONFIG_FILE: str = os.path.join(DEFAULT_BASE_DIR, "fetchtastic.yaml")
CONFIG_FILE: str = os.path.join(CONFIG_DIR, "fetchtastic.yaml")
BASE_DIR: str = DEFAULT_BASE_DIR

def _perform_initial_platform_checks() -> None:
    """
    Performs initial platform-specific checks and setup.
    This includes Termux package installation, storage setup, and config directory creation.
    """
    if is_termux():
        install_termux_packages() # Uses logger internally
        check_storage_setup() # Uses logger internally
        logger.info("Termux storage is set up.") # Was print

    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating config directory {CONFIG_DIR}: {e}", exc_info=True) # Was print

def _handle_config_migration() -> None:
    """
    Handles migration of configuration from an old location to the new
    platform-specific directory if needed.
    """
    if os.path.exists(OLD_CONFIG_FILE) and not os.path.exists(CONFIG_FILE):
        # No need to import log_error, log_info locally, logger is module-level
        separator: str = "=" * 80
        logger.info(f"\n{separator}")
        logger.info("Configuration Migration")
        logger.info(separator)
        prompt_for_migration() # Uses logger internally
        if migrate_config(): # Uses logger internally
            logger.info(f"Configuration successfully migrated to: {CONFIG_FILE}")
        else:
            logger.error(f"Failed to migrate configuration. Please check permissions for {OLD_CONFIG_FILE} and {CONFIG_DIR}.")
        logger.info(f"{separator}\n")

def _initialize_or_load_config() -> Tuple[Dict[str, Any], bool]:
    """
    Initializes a new configuration dictionary or loads an existing one.
    Determines if this is the first run based on config existence.

    Returns:
        Tuple[Dict[str, Any], bool]: A tuple containing:
            - config (Dict[str, Any]): The configuration dictionary.
            - is_first_run (bool): True if no existing config was found, False otherwise.
    """
    config: Dict[str, Any] = {}
    exists: bool
    exists, _ = config_exists()
    is_first_run: bool = not exists
    if exists:
        loaded_config: Optional[Dict[str, Any]] = load_config()
        if loaded_config is not None:
            config = loaded_config
            logger.info("Existing configuration found. You can keep current settings or change them.") # Was print
        else:
            logger.warning("Could not load existing configuration. Starting with defaults.") # Was print, changed to warning
            config = {}
            is_first_run = True
    else:
        logger.info("No existing configuration found. Starting with defaults.") # Was print
        config = {}
    return config, is_first_run

def _configure_base_directory(config: Dict[str, Any], is_first_run_param: bool) -> Dict[str, Any]:
    """
    Configures the base directory for Fetchtastic downloads.
    Updates the global BASE_DIR and the 'BASE_DIR' key in the config dictionary.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
        is_first_run_param (bool): Whether this is the first time setup is being run.

    Returns:
        Dict[str, Any]: The updated configuration dictionary.
    """
    global BASE_DIR
    is_first_run: bool = is_first_run_param

    current_base_dir_display: str = str(config.get("BASE_DIR", DEFAULT_BASE_DIR))
    prompt_message: str = (
        f"Enter the base directory for Fetchtastic (default: {DEFAULT_BASE_DIR}): "
        if is_first_run
        else f"Enter the base directory for Fetchtastic (current: {current_base_dir_display}): "
    )
    base_dir_input: str = input(prompt_message).strip()

    chosen_base_dir: str = DEFAULT_BASE_DIR
    if base_dir_input:
        chosen_base_dir = os.path.expanduser(base_dir_input)
    elif not is_first_run:
        chosen_base_dir = str(config.get("BASE_DIR", DEFAULT_BASE_DIR))

    BASE_DIR = os.path.expanduser(chosen_base_dir)
    config["BASE_DIR"] = BASE_DIR
    if not os.path.exists(BASE_DIR):
        try:
            os.makedirs(BASE_DIR)
            logger.info(f"Created base directory: {BASE_DIR}") # Was print
        except Exception as e:
            logger.error(f"Error creating base directory {BASE_DIR}: {e}", exc_info=True) # Was print
    return config

def _configure_windows_shortcuts(config_file_param: str, base_dir_param: str) -> None:
    """
    Handles creation of Windows Start Menu and configuration file shortcuts
    if running on Windows and `winshell` module is available.

    Args:
        config_file_param (str): Path to the configuration file.
        base_dir_param (str): Path to the base directory for downloads.
    """
    if platform.system() == "Windows" and WINDOWS_MODULES_AVAILABLE:
        create_config_shortcut(config_file_param, base_dir_param)
        prompt_text: str
        if os.path.exists(WINDOWS_START_MENU_FOLDER):
            prompt_text = "Fetchtastic shortcuts already exist in the Start Menu. Would you like to update them? [y/n] (default: yes): "
        else:
            prompt_text = "Would you like to create Fetchtastic shortcuts in the Start Menu? (recommended) [y/n] (default: yes): "
        create_menu: str = input(prompt_text).strip().lower() or "y"
        if create_menu == "y":
            create_windows_menu_shortcuts(config_file_param, base_dir_param) # Uses logger internally
    elif platform.system() == "Windows" and not WINDOWS_MODULES_AVAILABLE:
        logger.warning("Windows shortcuts not available. Install optional dependencies for full Windows integration (see README).") # Was print

def _configure_asset_types_and_patterns(config: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, bool]:
    """
    Configures which asset types (APKs, firmware) to download and their specific patterns
    by prompting the user. It updates the configuration dictionary with these choices.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
                                 This dictionary will be updated with keys like
                                 'SAVE_APKS', 'SAVE_FIRMWARE',
                                 'SELECTED_APK_ASSETS', 'SELECTED_FIRMWARE_ASSETS'.

    Returns:
        Tuple[Dict[str, Any], bool, bool]: A tuple containing:
            - config (Dict[str, Any]): The updated configuration dictionary.
            - save_apks (bool): True if APKs should be saved, False otherwise.
            - save_firmware (bool): True if firmware should be saved, False otherwise.
    """
    save_choice: str = input("Would you like to download APKs, firmware, or both? [a/f/b] (default: both): ").strip().lower() or "both"
    save_apks: bool = save_choice in ["a", "both"]
    save_firmware: bool = save_choice in ["f", "both"]
    config["SAVE_APKS"] = save_apks
    config["SAVE_FIRMWARE"] = save_firmware

    selected_assets_key: str
    if save_apks:
        apk_selection: Optional[Dict[str, Any]] = menu_apk.run_menu() # type: ignore[no-any-return]
        selected_assets_key = "SELECTED_APK_ASSETS"
        if not apk_selection or not apk_selection.get("selected_assets"):
            logger.warning("No APK assets selected. APKs will not be downloaded.") # Was print
            config["SAVE_APKS"] = False
            save_apks = False
        else:
            config[selected_assets_key] = apk_selection["selected_assets"]

    if save_firmware:
        firmware_selection: Optional[Dict[str, Any]] = menu_firmware.run_menu() # type: ignore[no-any-return]
        selected_assets_key = "SELECTED_FIRMWARE_ASSETS"
        if not firmware_selection or not firmware_selection.get("selected_assets"):
            logger.warning("No firmware assets selected. Firmware will not be downloaded.") # Was print
            config["SAVE_FIRMWARE"] = False
            save_firmware = False
        else:
            config[selected_assets_key] = firmware_selection["selected_assets"]

    return config, save_apks, save_firmware

def _configure_version_counts(config: Dict[str, Any], save_apks: bool, save_firmware: bool, is_first_run: bool) -> Dict[str, Any]:
    """
    Configures how many versions of APKs and firmware to keep, based on user input.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
        save_apks (bool): Flag indicating if APKs are to be saved.
        save_firmware (bool): Flag indicating if firmware is to be saved.
        is_first_run (bool): Flag indicating if this is the first run of the setup.

    Returns:
        Dict[str, Any]: The updated configuration dictionary.
    """
    default_versions_to_keep: int = 2 if is_termux() else 3
    current_val: int
    prompt_prefix: str
    prompt: str
    user_input: str

    if save_apks:
        current_val = config.get("ANDROID_VERSIONS_TO_KEEP", default_versions_to_keep) # type: ignore[assignment]
        prompt_prefix = "default is" if is_first_run else "current:"
        while True:
            prompt = f"How many versions of the Android app would you like to keep? ({prompt_prefix} {current_val}, min: 1, max: 10): "
            user_input = input(prompt).strip()
            try:
                config["ANDROID_VERSIONS_TO_KEEP"] = validate_version_count(user_input, str(current_val))
                break
            except ValueError as e:
                logger.error(e) # Was print
    if save_firmware:
        current_val = config.get("FIRMWARE_VERSIONS_TO_KEEP", default_versions_to_keep) # type: ignore[assignment]
        prompt_prefix = "default is" if is_first_run else "current:"
        while True:
            prompt = f"How many versions of the firmware would you like to keep? ({prompt_prefix} {current_val}, min: 1, max: 10): "
            user_input = input(prompt).strip()
            try:
                config["FIRMWARE_VERSIONS_TO_KEEP"] = validate_version_count(user_input, str(current_val))
                break
            except ValueError as e:
                logger.error(e) # Was print
    return config

def _configure_firmware_options(config: Dict[str, Any], config_file_path_param: str) -> Dict[str, Any]:
    """
    Configures firmware-specific options like pre-release checks and auto-extraction settings.
    Saves the configuration intermittently as these options are set.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
                                 Expected keys like 'CHECK_PRERELEASES', 'AUTO_EXTRACT',
                                 'EXTRACT_PATTERNS', 'EXCLUDE_PATTERNS' will be updated.
        config_file_path_param (str): Path to the configuration file for saving changes.

    Returns:
        Dict[str, Any]: The updated configuration dictionary.
    """
    current_prerelease_check: bool = bool(config.get("CHECK_PRERELEASES", False))
    prerelease_default_str: str = "yes" if current_prerelease_check else "no"
    prerelease_input: str = input(f"Would you like to check for and download pre-release firmware? [y/n] (default: {prerelease_default_str}): ").strip().lower() or prerelease_default_str[0]
    config["CHECK_PRERELEASES"] = (prerelease_input == "y")

    with open(config_file_path_param, "w") as f: yaml.dump(config, f)

    current_auto_extract: bool = bool(config.get("AUTO_EXTRACT", False))
    auto_extract_default_str: str = "yes" if current_auto_extract else "no"
    auto_extract_input: str = input(f"Would you like to automatically extract specific files from firmware zip archives? [y/n] (default: {auto_extract_default_str}): ").strip().lower() or auto_extract_default_str[0]
    config["AUTO_EXTRACT"] = (auto_extract_input == "y")

    with open(config_file_path_param, "w") as f: yaml.dump(config, f)

    if config["AUTO_EXTRACT"]:
        logger.info("Enter keywords for firmware extraction (space-separated). E.g., rak4631- tbeam device-") # Was print
        current_patterns_list: List[str] = config.get("EXTRACT_PATTERNS", []) # type: ignore
        current_patterns_str: str = " ".join(current_patterns_list)
        new_patterns_str: str
        keep_input: str

        if current_patterns_str:
            logger.info(f"Current patterns: {current_patterns_str}") # Was print
            keep_input = input("Keep current extraction patterns? [y/n] (default: yes): ").strip().lower() or "y"
            if keep_input == "n":
                new_patterns_str = input("Enter new extraction patterns: ").strip()
                config["EXTRACT_PATTERNS"] = new_patterns_str.split() if new_patterns_str else []
        else:
            new_patterns_str = input("Extraction patterns: ").strip()
            config["EXTRACT_PATTERNS"] = new_patterns_str.split() if new_patterns_str else []

        if not config.get("EXTRACT_PATTERNS"):
            logger.warning("No extraction patterns set. Disabling auto-extraction.") # Was print
            config["AUTO_EXTRACT"] = False
            config["EXCLUDE_PATTERNS"] = []
        else:
            with open(config_file_path_param, "w") as f: yaml.dump(config, f)

            current_excludes_list: List[str] = config.get("EXCLUDE_PATTERNS", []) # type: ignore
            current_excludes_str: str = " ".join(current_excludes_list)
            exclude_default_choice: str = "yes" if current_excludes_str else "no"
            exclude_prompt_main: str = input(f"Exclude any patterns from extraction? [y/n] (default: {exclude_default_choice}): ").strip().lower() or exclude_default_choice[0]
            new_excludes_str: str
            keep_excludes_input: str

            if exclude_prompt_main == 'y':
                logger.info("Enter keywords to exclude from extraction (space-separated). E.g., .hex tcxo") # Was print
                if current_excludes_str:
                    logger.info(f"Current exclude patterns: {current_excludes_str}") # Was print
                    keep_excludes_input = input("Keep current exclude patterns? [y/n] (default: yes): ").strip().lower() or "y"
                    if keep_excludes_input == 'n':
                        new_excludes_str = input("Enter new exclude patterns: ").strip()
                        config["EXCLUDE_PATTERNS"] = new_excludes_str.split() if new_excludes_str else []
                else:
                    new_excludes_str = input("Exclude patterns: ").strip()
                    config["EXCLUDE_PATTERNS"] = new_excludes_str.split() if new_excludes_str else []
            else:
                if not current_excludes_str : # Ensure this logic remains correct
                     config["EXCLUDE_PATTERNS"] = []
    else:
        config["EXTRACT_PATTERNS"] = []
        config["EXCLUDE_PATTERNS"] = []

    with open(config_file_path_param, "w") as f: yaml.dump(config, f)
    return config

def _configure_termux_wifi_only(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Configures the 'Wi-Fi only' download option for Termux environments.
    If not in Termux, this option is removed from the config.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
                                 The 'WIFI_ONLY' key may be updated or removed.

    Returns:
        Dict[str, Any]: The updated configuration dictionary.
    """
    if is_termux():
        current_wifi_only: bool = bool(config.get("WIFI_ONLY", True))
        wifi_default_str: str = "yes" if current_wifi_only else "no"
        wifi_input: str = input(f"Only download when on Wi-Fi? [y/n] (default: {wifi_default_str}): ").strip().lower() or wifi_default_str[0]
        config["WIFI_ONLY"] = (wifi_input == "y")
    else:
        config.pop("WIFI_ONLY", None)
    return config

def _finalize_config_and_save(config: Dict[str, Any], base_dir_param: str, config_file_param: str) -> None:
    """
    Sets the final 'DOWNLOAD_DIR' in the configuration to the provided base directory
    and saves the entire configuration dictionary to the specified YAML file.
    It also ensures the directory for the configuration file exists.

    Args:
        config (Dict[str, Any]): The final configuration dictionary to save.
        base_dir_param (str): The base directory path, which will be set as 'DOWNLOAD_DIR'.
        config_file_param (str): The full path to the configuration file where settings will be saved.
    """
    config["DOWNLOAD_DIR"] = base_dir_param

    final_config_dir: str = os.path.dirname(config_file_param)
    if not os.path.exists(final_config_dir):
        try:
            os.makedirs(final_config_dir, exist_ok=True)
            logger.info(f"Created configuration directory: {final_config_dir}") # Was print
        except Exception as e:
            logger.critical(f"Error creating configuration directory {final_config_dir}: {e}", exc_info=True) # Was print
            logger.critical("Cannot save configuration. Please check permissions and path.") # Was print
            return

    try:
        with open(config_file_param, "w") as f:
            yaml.dump(config, f)
        logger.info(f"Configuration saved to: {config_file_param}") # Was print
    except Exception as e:
        logger.critical(f"Error saving configuration to {config_file_param}: {e}", exc_info=True) # Was print

def _configure_scheduling_and_startup(config_dir_param: str) -> None:
    """
    Configures cron jobs for Unix-like systems (Linux, macOS, Termux)
    or Windows startup tasks for automatic execution of Fetchtastic.

    Args:
        config_dir_param (str): Path to the configuration directory, used for storing
                                batch files on Windows for startup tasks.
    """
    if platform.system() == "Windows":
        if WINDOWS_MODULES_AVAILABLE:
            startup_folder: str = winshell.startup() # type: ignore
            startup_shortcut_path: str = os.path.join(startup_folder, "Fetchtastic.lnk")
            startup_option: str
            if os.path.exists(startup_shortcut_path):
                startup_option = input("Fetchtastic is already set to run at startup. Remove? [y/n] (default: no): ").strip().lower() or "n"
                if startup_option == "y":
                    try:
                        batch_dir: str = os.path.join(config_dir_param, "batch")
                        batch_path: str = os.path.join(batch_dir, "fetchtastic_startup.bat")
                        if os.path.exists(batch_path): os.remove(batch_path)
                        os.remove(startup_shortcut_path)
                        logger.info("✓ Startup shortcut removed.") # Was print
                    except Exception as e:
                        logger.error(f"Failed to remove startup shortcut: {e}", exc_info=True) # Was print
            else:
                startup_option = input("Run Fetchtastic automatically on Windows startup? [y/n] (default: yes): ").strip().lower() or "y"
                if startup_option == "y":
                    if create_startup_shortcut(): # Uses logger internally
                        logger.info("✓ Fetchtastic will now run automatically on startup.") # Was print
                    else:
                        logger.error("Failed to create startup shortcut.") # Was print
    elif is_termux():
        if check_cron_job_exists(): # Uses logger internally
            if (input("Cron job already set up. Reconfigure? [y/n] (default: no): ").strip().lower() or "n") == 'y':
                remove_cron_job() # Uses logger internally
                install_crond() # Uses logger internally
                setup_cron_job() # Uses logger internally
        else:
            if (input("Schedule Fetchtastic daily at 3 AM? [y/n] (default: yes): ").strip().lower() or "y") == 'y':
                install_crond() # Uses logger internally
                setup_cron_job() # Uses logger internally

        if check_boot_script_exists(): # Uses logger internally
            if (input("Boot script already set up. Reconfigure? [y/n] (default: no): ").strip().lower() or "n") == 'y':
                remove_boot_script() # Uses logger internally
                setup_boot_script() # Uses logger internally
        else:
            if (input("Run Fetchtastic on device boot? [y/n] (default: yes): ").strip().lower() or "y") == 'y':
                setup_boot_script() # Uses logger internally
    else: # Linux/Mac (non-Termux)
        if check_any_cron_jobs_exist(): # Uses logger internally
            if (input("Fetchtastic cron jobs found. Reconfigure? [y/n] (default: no): ").strip().lower() or "n") == 'y':
                remove_cron_job() # Uses logger internally
                remove_reboot_cron_job() # Uses logger internally
                if (input("Schedule daily at 3 AM? [y/n] (default: yes): ").strip().lower() or "y") == 'y': setup_cron_job() # Uses logger
                if (input("Run on system startup? [y/n] (default: yes): ").strip().lower() or "y") == 'y': setup_reboot_cron_job() # Uses logger
        else:
            if (input("Schedule daily at 3 AM? [y/n] (default: yes): ").strip().lower() or "y") == 'y': setup_cron_job() # Uses logger
            if (input("Run on system startup? [y/n] (default: yes): ").strip().lower() or "y") == 'y': setup_reboot_cron_job() # Uses logger

def _configure_notifications(config: Dict[str, Any], config_file_param: str) -> Dict[str, Any]:
    """
    Configures NTFY notification settings based on user input.
    Updates and saves the configuration dictionary.

    Args:
        config (Dict[str, Any]): The current configuration dictionary.
        config_file_param (str): Path to the configuration file for saving changes.

    Returns:
        Dict[str, Any]: The updated configuration dictionary.
    """
    has_ntfy_config: bool = bool(config.get("NTFY_TOPIC")) and bool(config.get("NTFY_SERVER"))
    notifications_default_str: str = "yes" if has_ntfy_config else "no"
    notifications_input: str = input(f"Set up notifications via NTFY? [y/n] (default: {notifications_default_str}): ").strip().lower() or notifications_default_str[0]

    if notifications_input == "y":
        current_server: str = str(config.get("NTFY_SERVER", "ntfy.sh"))
        ntfy_server: str = input(f"Enter NTFY server (current: {current_server}): ").strip() or current_server
        if not ntfy_server.startswith(("http://", "https://")):
            ntfy_server = "https://" + ntfy_server

        default_topic: str = "fetchtastic-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        current_topic: str = str(config.get("NTFY_TOPIC", default_topic))
        topic_name: str = input(f"Enter unique topic name (current: {current_topic}): ").strip() or current_topic

        config["NTFY_TOPIC"] = topic_name
        config["NTFY_SERVER"] = ntfy_server
        with open(config_file_param, "w") as f: yaml.dump(config, f)

        full_topic_url: str = f"{ntfy_server.rstrip('/')}/{topic_name}"
        logger.info(f"Notifications enabled for topic: {full_topic_url}") # Was print

        text_to_copy: str = topic_name if is_termux() else full_topic_url
        copy_prompt: str = "Copy to clipboard? [y/n] (default: yes): "
        if (input(copy_prompt).strip().lower() or "y") == "y":
            if copy_to_clipboard_func(text_to_copy): # Uses logger for errors
                logger.info("Copied to clipboard.") # Was print
            else:
                logger.error("Failed to copy.") # Was print (already an error log in func)

        notify_only_current: bool = bool(config.get("NOTIFY_ON_DOWNLOAD_ONLY", False))
        notify_default_str: str = "yes" if notify_only_current else "no"
        notify_input: str = input(f"Notify only when new files are downloaded? [y/n] (default: {notify_default_str}): ").strip().lower() or notify_default_str[0]
        config["NOTIFY_ON_DOWNLOAD_ONLY"] = (notify_input == "y")
        with open(config_file_param, "w") as f: yaml.dump(config, f)
        logger.info("Notification settings saved.") # Was print
    else:
        if has_ntfy_config and (input("Disable existing notifications? [y/n] (default: no): ").strip().lower() or "n") == "y":
            config["NTFY_TOPIC"] = ""
            config["NTFY_SERVER"] = ""
            config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
            with open(config_file_param, "w") as f: yaml.dump(config, f)
            logger.info("Notifications disabled.") # Was print
        elif not has_ntfy_config:
             config["NTFY_TOPIC"] = ""
             config["NTFY_SERVER"] = ""
             config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
             with open(config_file_param, "w") as f: yaml.dump(config, f)
             logger.info("Notifications will remain disabled.") # Was print
        else:
            logger.info("Keeping existing notification settings.") # Was print
    return config

def _prompt_for_first_run() -> None:
    """
    Asks the user if they want to perform the first download run after setup.
    On Windows, provides instructions on how to run manually.
    """
    if platform.system() == "Windows":
        logger.info("Setup complete. Run 'fetchtastic download' to start downloading.") # Was print
        if WINDOWS_MODULES_AVAILABLE: logger.info("You can also use Start Menu shortcuts.") # Was print
        if os.environ.get("PROMPT") is None or "cmd.exe" in os.environ.get("COMSPEC", ""): # type: ignore
            input("\nPress Enter to close this window...")
    else:
        if (input("Start first download run now? [y/n] (default: yes): ").strip().lower() or "y") == "y":
            logger.info("Setup complete. Starting first run...") # Was print
            downloader.main() # Assumes downloader.main uses logger
        else:
            logger.info("Setup complete. Run 'fetchtastic download' to start.") # Was print

def run_setup() -> None:
    """
    Runs the main setup process for Fetchtastic.
    This includes platform checks, configuration loading/migration,
    setting base directories, choosing assets, configuring versions,
    firmware options, scheduling, notifications, and prompting for a first run.
    """
    global BASE_DIR, CONFIG_FILE
    logger.info("Running Fetchtastic Setup...") # Was print

    _perform_initial_platform_checks() # Uses logger
    _handle_config_migration() # Uses logger

    config: Dict[str, Any]
    is_first_run: bool
    config, is_first_run = _initialize_or_load_config() # Uses logger
    # _initialize_or_load_config ensures config is a dict

    config = _configure_base_directory(config, is_first_run) # Uses logger

    _configure_windows_shortcuts(CONFIG_FILE, BASE_DIR) # Uses logger

    save_apks: bool
    save_firmware: bool
    config, save_apks, save_firmware = _configure_asset_types_and_patterns(config) # Uses logger
    if not save_apks and not save_firmware:
        logger.warning("No assets selected to download. Please run 'fetchtastic setup' again to select assets.") # Was print
        return

    config = _configure_version_counts(config, save_apks, save_firmware, is_first_run) # Uses logger

    if save_firmware:
        config = _configure_firmware_options(config, CONFIG_FILE) # Uses logger

    config = _configure_termux_wifi_only(config) # Uses logger (indirectly through input)

    _finalize_config_and_save(config, BASE_DIR, CONFIG_FILE) # Uses logger
    _configure_scheduling_and_startup(CONFIG_DIR) # Uses logger
    config = _configure_notifications(config, CONFIG_FILE) # Uses logger

    _prompt_for_first_run() # Uses logger

def config_exists(directory: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Check if the configuration file exists.

    Args:
        directory (Optional[str]): Optional directory to check for config file.
                                   If None, checks both the new platformdirs
                                   location and the old location.

    Returns:
        Tuple[bool, Optional[str]]: (exists, path) where exists is a boolean
                                     indicating if the config exists, and path is
                                     the path to the config file if it exists,
                                     otherwise None.
    """
    config_path: Optional[str]
    if directory:
        config_path = os.path.join(directory, "fetchtastic.yaml")
        if os.path.exists(config_path):
            return True, config_path
        return False, None

    if os.path.exists(CONFIG_FILE):
        return True, CONFIG_FILE

    if os.path.exists(OLD_CONFIG_FILE): # This implies a migration might be needed or it's an old setup
        # This function is just for existence, not for loading logic.
        # The loading logic in load_config handles printing messages about old locations.
        return True, OLD_CONFIG_FILE

    return False, None

def check_storage_setup() -> bool:
    """
    For Termux: Check if the storage is set up and accessible.
    Continuously prompts to set up storage until successful.

    Returns:
        bool: True when storage access is confirmed.
    """
    storage_dir: str = os.path.expanduser("~/storage")
    storage_downloads: str = os.path.expanduser("~/storage/downloads")

    while True:
        if (
            os.path.exists(storage_dir)
            and os.path.exists(storage_downloads)
            and os.access(storage_downloads, os.W_OK)
        ):
            logger.info("Termux storage access is already set up.") # Was print
            return True
        else:
            logger.warning("Termux storage access is not set up or permission was denied.") # Was print
            setup_storage() # Uses logger internally
            logger.info("Please grant storage permissions when prompted.") # Was print
            input("Press Enter after granting storage permissions to continue...")
            # Re-check if storage is set up - loop will handle this

def check_for_updates() -> Tuple[Optional[str], Optional[str], bool]:
    """
    Check if a newer version of fetchtastic is available from PyPI.

    Returns:
        Tuple[Optional[str], Optional[str], bool]: A tuple containing:
            - current_version (Optional[str]): The currently installed version, or None if not found.
            - latest_version (Optional[str]): The latest version on PyPI, or None if lookup fails.
            - update_available (bool): True if a newer version is available, False otherwise.
    """
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    update_available: bool = False
    try:
        from importlib.metadata import version as get_version
        current_version = get_version("fetchtastic")

        import requests
        response: requests.Response = requests.get("https://pypi.org/pypi/fetchtastic/json", timeout=5)
        if response.status_code == 200:
            data: Dict[str, Any] = response.json()
            latest_version = data.get("info", {}).get("version")
            if current_version and latest_version:
                from packaging import version as pkg_version # type: ignore
                current_ver_parsed = pkg_version.parse(current_version)
                latest_ver_parsed = pkg_version.parse(latest_version)
                update_available = latest_ver_parsed > current_ver_parsed
        return current_version, latest_version, update_available
    except Exception: # Broad exception to catch import errors or request errors
        if not current_version:
            try:
                from importlib.metadata import version as get_version_fallback
                current_version = get_version_fallback("fetchtastic")
            except Exception:
                 current_version = "unknown"
        return current_version, None, False

def get_upgrade_command() -> str:
    """
    Returns the appropriate upgrade command based on the execution environment.

    Returns:
        str: The command string to upgrade the fetchtastic package.
    """
    if is_termux():
        return "pip install --upgrade fetchtastic"
    else:
        return "pipx upgrade fetchtastic"

def display_version_info(show_update_message: bool = True) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Retrieves and returns version information.
    Optionally, this function could print the information, but currently, it only returns it.

    Args:
        show_update_message (bool): This parameter is currently unused as printing is handled
                                   by the caller (_initial_setup_and_config). Kept for signature consistency.

    Returns:
        Tuple[Optional[str], Optional[str], bool]: A tuple containing:
            - current_version (Optional[str]): The currently installed version.
            - latest_version (Optional[str]): The latest version available.
            - update_available (bool): True if an update is available.
    """
    current_version, latest_version, update_available = check_for_updates()
    return current_version, latest_version, update_available

def migrate_config() -> bool:
    """
    Migrates the configuration from the old location (in DEFAULT_BASE_DIR)
    to the new platform-specific config directory (CONFIG_DIR).

    Returns:
        bool: True if migration was successful or not needed, False if an error occurred.
    """
    from fetchtastic.log_utils import log_error, log_info

    if not os.path.exists(OLD_CONFIG_FILE):
        logger.info("No old configuration file found to migrate.")
        return True

    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating new config directory {CONFIG_DIR}: {e}", exc_info=True)
            return False

    config_data: Optional[Dict[str, Any]] = None
    try:
        with open(OLD_CONFIG_FILE, "r") as f_old:
            config_data = yaml.safe_load(f_old)
        if not isinstance(config_data, dict): # Basic validation
            logger.error(f"Old configuration file {OLD_CONFIG_FILE} is not a valid YAML dictionary.")
            return False
    except Exception as e:
        logger.error(f"Error loading old configuration from {OLD_CONFIG_FILE}: {e}", exc_info=True)
        return False

    try:
        with open(CONFIG_FILE, "w") as f_new:
            yaml.dump(config_data, f_new)
        logger.info(f"Configuration successfully written to {CONFIG_FILE}")

        try:
            os.remove(OLD_CONFIG_FILE)
            logger.info(f"Old configuration file {OLD_CONFIG_FILE} removed.")
        except Exception as e:
            logger.error(f"Failed to remove old configuration file {OLD_CONFIG_FILE}: {e}", exc_info=True)
        return True
    except Exception as e:
        logger.error(f"Error saving configuration to new location {CONFIG_FILE}: {e}", exc_info=True)
        return False

def prompt_for_migration() -> bool:
    """
    Informs the user about the configuration migration process.
    Currently, this function is informational as migration is automatic.

    Returns:
        bool: Always returns True, as migration is attempted automatically if needed.
    """
    # No need for local import, logger is module-level
    logger.info(f"Found configuration file at old location: {OLD_CONFIG_FILE}")
    logger.info(f"Attempting to migrate to the new location: {CONFIG_FILE}")
    return True

def create_windows_menu_shortcuts(config_file_path: str, base_dir: str) -> bool:
    """
    Creates Windows Start Menu shortcuts for Fetchtastic operations and folders.
    This includes shortcuts for download, setup, repo browse, config file,
    downloads folder, log file, and update check.

    Args:
        config_file_path (str): Path to the fetchtastic.yaml configuration file.
        base_dir (str): Base directory where downloads are stored.

    Returns:
        bool: True if shortcuts were created successfully, False otherwise.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        import shutil
        if not os.path.exists(WINDOWS_START_MENU_FOLDER):
            try:
                os.makedirs(WINDOWS_START_MENU_FOLDER, exist_ok=True)
            except Exception as e:
                logger.error(f"Error creating Start Menu folder {WINDOWS_START_MENU_FOLDER}: {e}", exc_info=True) # Was print
                return False

        fetchtastic_path: Optional[str] = shutil.which("fetchtastic")
        if not fetchtastic_path:
            logger.error("Error: fetchtastic executable not found in PATH.") # Was print
            return False

        batch_dir: str = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        download_batch_path: str = os.path.join(batch_dir, "fetchtastic_download.bat")
        repo_batch_path: str = os.path.join(batch_dir, "fetchtastic_repo_browse.bat")
        setup_batch_path: str = os.path.join(batch_dir, "fetchtastic_setup.bat")
        update_batch_path: str = os.path.join(batch_dir, "fetchtastic_update.bat")

        with open(download_batch_path, "w") as f:
            f.write(f'@echo off\ntitle Fetchtastic Download\n"{fetchtastic_path}" download\necho.\necho Press any key to close...\npause >nul\n')
        with open(repo_batch_path, "w") as f:
            f.write(f'@echo off\ntitle Fetchtastic Repository Browser\n"{fetchtastic_path}" repo browse\necho.\necho Press any key to close...\npause >nul\n')
        with open(setup_batch_path, "w") as f:
            f.write(f'@echo off\ntitle Fetchtastic Setup\n"{fetchtastic_path}" setup\necho.\necho Press any key to close...\npause >nul\n')

        pipx_path: Optional[str] = shutil.which("pipx")
        pip_path: Optional[str] = shutil.which("pip")
        update_command: str = ""
        if pipx_path:
            update_command = f'"{pipx_path}" upgrade fetchtastic'
        elif pip_path:
            update_command = f'"{pip_path}" install --upgrade fetchtastic'
        else:
            update_command = 'echo Error: Neither pipx nor pip was found. Cannot create update shortcut.\npause'

        with open(update_batch_path, "w") as f:
            f.write(f'@echo off\ntitle Fetchtastic Update Check\necho Checking for Fetchtastic updates...\necho.\n{update_command}\necho.\necho Press any key to close...\npause >nul\n')

        shortcuts_to_create: List[Dict[str, Any]] = [
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic Download.lnk"), "target": download_batch_path, "desc": "Download Meshtastic firmware and APKs"},
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic Setup.lnk"), "target": setup_batch_path, "desc": "Configure Fetchtastic settings"},
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic Repository Browser.lnk"), "target": repo_batch_path, "desc": "Browse Meshtastic repository"},
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic Configuration.lnk"), "target": config_file_path, "desc": "Edit Fetchtastic Configuration"},
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Meshtastic Downloads.lnk"), "target": base_dir, "desc": "Open Meshtastic Downloads Folder"},
            {"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic - Check for Updates.lnk"), "target": update_batch_path, "desc": "Check for Fetchtastic updates"},
        ]

        log_dir: str = platformdirs.user_log_dir("fetchtastic") # type: ignore
        log_file: str = os.path.join(log_dir, "fetchtastic.log")
        if not os.path.exists(log_dir): os.makedirs(log_dir, exist_ok=True)
        if not os.path.exists(log_file): open(log_file, 'a').close()
        shortcuts_to_create.append({"path": os.path.join(WINDOWS_START_MENU_FOLDER, "Fetchtastic Log.lnk"), "target": log_file, "desc": "View Fetchtastic Log File"})

        shortcut_info: Dict[str, Any]
        for shortcut_info in shortcuts_to_create:
            winshell.CreateShortcut( # type: ignore
                Path=shortcut_info["path"],
                Target=shortcut_info["target"],
                Description=shortcut_info["desc"],
                Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0) if ".bat" in shortcut_info["target"] or ".yaml" in shortcut_info["target"] or ".log" in shortcut_info["target"] else (str(os.environ.get("WINDIR", "C:\\Windows")), 0)
            )
        logger.info("Shortcuts created/updated in Start Menu.") # Was print
        return True
    except Exception as e:
        logger.error(f"Failed to create Windows Start Menu shortcuts: {e}", exc_info=True) # Was print
        return False

def create_config_shortcut(config_file_path: str, target_dir: str) -> bool:
    """
    Creates a shortcut to the configuration file in the target directory (Windows only).

    Args:
        config_file_path (str): Full path to the configuration file (fetchtastic.yaml).
        target_dir (str): Directory where the shortcut will be created.

    Returns:
        bool: True if shortcut creation was successful or not applicable, False on error.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    shortcut_path: str = os.path.join(target_dir, "fetchtastic_config_shortcut.lnk")
    try:
        winshell.CreateShortcut( # type: ignore
            Path=shortcut_path,
            Target=config_file_path,
            Description="Fetchtastic Configuration File (fetchtastic.yaml)",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0)
        )
        logger.info(f"Created shortcut to configuration file at: {shortcut_path}") # Was print
        return True
    except Exception as e:
        logger.error(f"Failed to create shortcut to configuration file: {e}", exc_info=True) # Was print
        return False

def create_startup_shortcut() -> bool:
    """
    Creates a shortcut to run Fetchtastic on Windows startup.

    Returns:
        bool: True if shortcut creation was successful or not applicable, False on error.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        fetchtastic_path: Optional[str] = shutil.which("fetchtastic")
        if not fetchtastic_path:
            logger.error("Error: fetchtastic executable not found in PATH.") # Was print
            return False

        startup_folder: str = winshell.startup() # type: ignore
        batch_dir: str = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        batch_path: str = os.path.join(batch_dir, "fetchtastic_startup.bat")
        with open(batch_path, "w") as f:
            f.write(f'@echo off\ntitle Fetchtastic Automatic Download\n"{fetchtastic_path}" download\n')

        shortcut_path: str = os.path.join(startup_folder, "Fetchtastic.lnk")
        winshell.CreateShortcut( # type: ignore
            Path=shortcut_path,
            Target=batch_path,
            Description="Run Fetchtastic on startup",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
            WindowStyle=7,
        )
        logger.info(f"Created startup shortcut at: {shortcut_path}") # Was print
        return True
    except Exception as e:
        logger.error(f"Failed to create startup shortcut: {e}", exc_info=True) # Was print
        return False

def copy_to_clipboard_func(text: str) -> bool:
    """
    Copies the provided text to the system clipboard.
    Supports Termux, Windows, macOS, and Linux (via xclip/xsel).

    Args:
        text (str): The text to copy to the clipboard.

    Returns:
        bool: True if copying was successful, False otherwise.
    """
    try:
        if is_termux():
            subprocess.run(["termux-clipboard-set"], input=text.encode("utf-8"), check=True)
            return True
        elif platform.system() == "Windows" and WINDOWS_MODULES_AVAILABLE:
            import win32clipboard # type: ignore
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
            win32clipboard.CloseClipboard()
            return True
        elif platform.system() == "Darwin":
            subprocess.run("pbcopy", text=True, input=text, check=True)
            return True
        elif platform.system() == "Linux":
            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
                return True
            elif shutil.which("xsel"):
                subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode("utf-8"), check=True)
                return True
            else:
                logger.warning("xclip or xsel not found for clipboard functionality.") # Was print
                return False
        else:
            logger.warning("Clipboard functionality is not supported on this platform.") # Was print
            return False
    except Exception as e:
        logger.error(f"An error occurred while copying to clipboard: {e}", exc_info=True) # Was print
        return False

def install_termux_packages() -> None:
    """Installs required Termux packages: termux-api, termux-services, cronie if not already present."""
    packages_to_install: List[str] = []
    if shutil.which("termux-battery-status") is None: packages_to_install.append("termux-api")
    if shutil.which("sv-enable") is None: packages_to_install.append("termux-services")
    if shutil.which("crond") is None: packages_to_install.append("cronie")

    if packages_to_install:
        logger.info(f"Installing required Termux packages: {', '.join(packages_to_install)}...") # Was print
        try:
            subprocess.run(["pkg", "install"] + packages_to_install + ["-y"], check=True)
            logger.info("Required Termux packages installed.") # Was print
        except subprocess.CalledProcessError as e:
            logger.error(f"Error installing Termux packages: {e}", exc_info=True) # Was print
        except FileNotFoundError:
            logger.error("Error: 'pkg' command not found. Are you in Termux?") # Was print
    else:
        logger.info("All required Termux packages are already installed.") # Was print

def setup_storage() -> None:
    """Runs 'termux-setup-storage' to request storage access in Termux."""
    logger.info("Setting up Termux storage access...") # Was print
    try:
        subprocess.run(["termux-setup-storage"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during 'termux-setup-storage': {e}", exc_info=True) # Was print
        logger.warning("Please grant storage permissions when prompted.") # Was print
    except FileNotFoundError:
        logger.error("Error: 'termux-setup-storage' command not found. Are you in Termux with Termux:API installed?") # Was print

def install_crond() -> None:
    """Installs and enables the crond service in Termux if not already present."""
    if is_termux():
        try:
            if shutil.which("crond") is None:
                logger.info("Installing cronie (for crond)...") # Was print
                subprocess.run(["pkg", "install", "cronie", "-y"], check=True)
                logger.info("cronie installed.") # Was print
            else:
                logger.info("cronie (crond) is already installed.") # Was print

            if shutil.which("sv-enable") is None:
                logger.info("Installing termux-services...") # Was print
                subprocess.run(["pkg", "install", "termux-services", "-y"], check=True)
                logger.info("termux-services installed.") # Was print

            subprocess.run(["sv-enable", "crond"], check=True)
            logger.info("crond service enabled.") # Was print
        except Exception as e:
            logger.error(f"An error occurred while installing or enabling crond: {e}", exc_info=True) # Was print

def setup_cron_job() -> None:
    """
    Sets up a daily cron job to run 'fetchtastic download' at 3 AM.
    Handles existing crontab entries to avoid duplicates.
    Does nothing on Windows.
    """
    if platform.system() == "Windows":
        # print("Cron jobs are not supported on Windows.") # Already handled by _configure_scheduling_and_startup
        return

    try:
        fetchtastic_path: Optional[str] = shutil.which("fetchtastic")
        if not fetchtastic_path:
            logger.error("Error: fetchtastic executable not found in PATH. Cannot set up cron job.") # Was print
            return

        crontab_l_cmd: List[str] = ["crontab", "-l"]
        current_crontab: str = ""
        try:
            result: subprocess.CompletedProcess = subprocess.run(crontab_l_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                current_crontab = result.stdout
        except FileNotFoundError:
             logger.error("crontab command not found. Cannot setup cron job.") # Was print
             return

        job_command: str = f"{fetchtastic_path} download"
        job_comment: str = "# fetchtastic"
        cron_job_line: str = f"0 3 * * * {job_command}  {job_comment}"

        new_crontab_lines: List[str] = []
        job_exists: bool = False
        for line in current_crontab.splitlines():
            if job_command in line and job_comment in line:
                job_exists = True
                new_crontab_lines.append(cron_job_line)
            elif "# fetchtastic" in line or "fetchtastic download" in line :
                continue
            else:
                new_crontab_lines.append(line)

        if not job_exists:
            new_crontab_lines.append(cron_job_line)

        new_crontab: str = "\n".join(new_crontab_lines)
        if not new_crontab.endswith("\n"):
            new_crontab += "\n"

        process: subprocess.Popen = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_crontab)
        if process.returncode == 0:
            logger.info("Cron job for Fetchtastic set up successfully to run daily at 3 AM.") # Was print
        else:
            logger.error("Error setting up cron job. Please check crontab permissions or syntax.") # Was print

    except Exception as e:
        logger.error(f"An error occurred while setting up the cron job: {e}", exc_info=True) # Was print

def remove_cron_job() -> None:
    """
    Removes Fetchtastic daily cron jobs from the crontab.
    Does nothing on Windows.
    """
    if platform.system() == "Windows":
        return

    try:
        crontab_l_cmd: List[str] = ["crontab", "-l"]
        current_crontab: str = ""
        try:
            result: subprocess.CompletedProcess = subprocess.run(crontab_l_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                current_crontab = result.stdout
        except FileNotFoundError:
            logger.error("crontab command not found. Cannot remove cron job.") # Was print
            return

        new_crontab_lines: List[str] = []
        removed: bool = False
        for line in current_crontab.splitlines():
            if ("# fetchtastic" in line or "fetchtastic download" in line) and not line.strip().startswith("@reboot"):
                removed = True
                continue
            new_crontab_lines.append(line)

        if removed:
            new_crontab: str = "\n".join(new_crontab_lines)
            if not new_crontab.endswith("\n") and new_crontab:
                new_crontab += "\n"

            process: subprocess.Popen = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
            process.communicate(input=new_crontab)
            if process.returncode == 0:
                logger.info("Fetchtastic daily cron job removed.") # Was print
            else:
                logger.error("Error removing cron job from crontab.") # Was print
        else:
            logger.info("No Fetchtastic daily cron job found to remove.") # Was print

    except Exception as e:
        logger.error(f"An error occurred while removing the cron job: {e}", exc_info=True) # Was print

def setup_boot_script() -> None:
    """Sets up a boot script in Termux to run Fetchtastic on device boot."""
    if not is_termux():
        # logger.debug("Boot script setup is only for Termux.") # This is handled by calling context
        return

    boot_dir: str = os.path.expanduser("~/.termux/boot")
    boot_script_path: str = os.path.join(boot_dir, "fetchtastic.sh")
    if not os.path.exists(boot_dir):
        try:
            os.makedirs(boot_dir)
            logger.info(f"Created Termux:Boot directory: {boot_dir}") # Was print
        except Exception as e:
            logger.error(f"Error creating Termux:Boot directory {boot_dir}: {e}", exc_info=True) # Was print
            return

    script_content: str = "#!/data/data/com.termux/files/usr/bin/sh\nsleep 30\nfetchtastic download\n"
    try:
        with open(boot_script_path, "w") as f:
            f.write(script_content)
        os.chmod(boot_script_path, 0o700)
        logger.info(f"Boot script created at {boot_script_path}") # Was print
        logger.info("Note: Termux:Boot app must be installed and run once to enable boot scripts.") # Was print
    except Exception as e:
        logger.error(f"Error creating boot script {boot_script_path}: {e}", exc_info=True) # Was print

def remove_boot_script() -> None:
    """Removes the Fetchtastic boot script from Termux."""
    if not is_termux():
        return

    boot_script_path: str = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script_path):
        try:
            os.remove(boot_script_path)
            logger.info(f"Boot script {boot_script_path} removed.") # Was print
        except Exception as e:
            logger.error(f"Error removing boot script {boot_script_path}: {e}", exc_info=True) # Was print
    else:
        logger.info("No Fetchtastic boot script found to remove.") # Was print

def setup_reboot_cron_job() -> None:
    """
    Sets up a cron job to run Fetchtastic on system startup (@reboot).
    This is for non-Termux Linux/macOS systems. Does nothing on Windows or Termux.
    """
    if platform.system() == "Windows" or is_termux():
        return

    try:
        fetchtastic_path: Optional[str] = shutil.which("fetchtastic")
        if not fetchtastic_path:
            logger.error("Error: fetchtastic executable not found in PATH. Cannot set up @reboot cron job.") # Was print
            return

        crontab_l_cmd: List[str] = ["crontab", "-l"]
        current_crontab: str = ""
        try:
            result: subprocess.CompletedProcess = subprocess.run(crontab_l_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                current_crontab = result.stdout
        except FileNotFoundError:
            logger.error("crontab command not found. Cannot setup @reboot cron job.") # Was print
            return

        job_command: str = f"{fetchtastic_path} download"
        job_comment: str = "# fetchtastic_reboot"
        reboot_job_line: str = f"@reboot {job_command}  {job_comment}"

        new_crontab_lines: List[str] = []
        job_exists: bool = False
        for line in current_crontab.splitlines():
            if job_command in line and job_comment in line and line.strip().startswith("@reboot"):
                job_exists = True
                new_crontab_lines.append(reboot_job_line)
            elif "# fetchtastic_reboot" in line or (line.strip().startswith("@reboot") and "fetchtastic download" in line):
                continue
            else:
                new_crontab_lines.append(line)

        if not job_exists:
            new_crontab_lines.append(reboot_job_line)

        new_crontab: str = "\n".join(new_crontab_lines)
        if not new_crontab.endswith("\n"): new_crontab += "\n"

        process: subprocess.Popen = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_crontab)
        if process.returncode == 0:
            logger.info("Cron job for Fetchtastic on reboot set up successfully.") # Was print
        else:
            logger.error("Error setting up @reboot cron job.") # Was print

    except Exception as e:
        logger.error(f"An error occurred while setting up the @reboot cron job: {e}", exc_info=True) # Was print

def remove_reboot_cron_job() -> None:
    """
    Removes Fetchtastic @reboot cron jobs from the crontab.
    Does nothing on Windows or Termux.
    """
    if platform.system() == "Windows" or is_termux():
        return

    try:
        crontab_l_cmd: List[str] = ["crontab", "-l"]
        current_crontab: str = ""
        try:
            result: subprocess.CompletedProcess = subprocess.run(crontab_l_cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                current_crontab = result.stdout
        except FileNotFoundError:
            logger.error("crontab command not found. Cannot remove @reboot cron job.") # Was print
            return

        new_crontab_lines: List[str] = []
        removed: bool = False
        for line in current_crontab.splitlines():
            if ("# fetchtastic_reboot" in line or ("fetchtastic download" in line and line.strip().startswith("@reboot"))):
                removed = True
                continue
            new_crontab_lines.append(line)

        if removed:
            new_crontab: str = "\n".join(new_crontab_lines)
            if not new_crontab.endswith("\n") and new_crontab: new_crontab += "\n"

            process: subprocess.Popen = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
            process.communicate(input=new_crontab)
            if process.returncode == 0:
                logger.info("Fetchtastic @reboot cron job removed.") # Was print
            else:
                logger.error("Error removing @reboot cron job from crontab.") # Was print
        else:
            logger.info("No Fetchtastic @reboot cron job found to remove.") # Was print

    except Exception as e:
        logger.error(f"An error occurred while removing the @reboot cron job: {e}", exc_info=True) # Was print

def check_cron_job_exists() -> bool:
    """
    Checks if a Fetchtastic daily (non-reboot) cron job already exists.
    Returns False on Windows or if crontab command fails.
    """
    if platform.system() == "Windows": return False
    try:
        result: subprocess.CompletedProcess = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
        if result.returncode != 0: return False
        return any(("# fetchtastic" in line or "fetchtastic download" in line) and not line.strip().startswith("@reboot") for line in result.stdout.splitlines())
    except Exception:
        return False

def check_boot_script_exists() -> bool:
    """Checks if a Fetchtastic boot script exists for Termux."""
    if not is_termux(): return False
    boot_script_path: str = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    return os.path.exists(boot_script_path)

def check_any_cron_jobs_exist() -> bool:
    """
    Checks if any Fetchtastic-related cron jobs (daily or @reboot) exist.
    Returns False on Windows or if crontab command fails.
    """
    if platform.system() == "Windows": return False
    try:
        result: subprocess.CompletedProcess = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
        if result.returncode != 0: return False
        return any(("# fetchtastic" in line or "fetchtastic download" in line) for line in result.stdout.splitlines())
    except Exception:
        return False

def load_config(directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Loads the configuration from the YAML file (fetchtastic.yaml).
    It first checks the platform-specific config directory, then a potential old location.
    If `directory` is provided, it loads directly from there.
    Updates the global BASE_DIR if found in the loaded config.

    Args:
        directory (Optional[str]): A specific directory to load the config from.
                                   If None, uses standard locations.

    Returns:
        Optional[Dict[str, Any]]: The loaded configuration dictionary, or None if not found or error.
    """
    global BASE_DIR
    config_path: Optional[str] = None

    if directory:
        config_path = os.path.join(directory, "fetchtastic.yaml")
    elif os.path.exists(CONFIG_FILE):
        config_path = CONFIG_FILE
    elif os.path.exists(OLD_CONFIG_FILE):
        config_path = OLD_CONFIG_FILE
        logger.warning(f"Using configuration from old location: {OLD_CONFIG_FILE}") # Was print
        logger.warning(f"Consider running 'fetchtastic setup' to migrate to: {CONFIG_FILE}") # Was print

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config_data: Dict[str, Any] = yaml.safe_load(f)
            if isinstance(config_data, dict) and "BASE_DIR" in config_data: # Check if it's a dict before accessing keys
                BASE_DIR = str(config_data["BASE_DIR"]) # Update global BASE_DIR
            return config_data
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration file {config_path}: {e}", exc_info=True) # Was print
            return None
        except Exception as e:
            logger.error(f"Error loading configuration file {config_path}: {e}", exc_info=True) # Was print
            return None
    return None
