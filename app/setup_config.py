# app/setup_config.py

import os
import platform
import random
import shutil
import string
import subprocess

import yaml

from . import downloader  # Import downloader to perform first run
from . import menu_apk, menu_firmware


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


DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_CONFIG_DIR = os.path.join(DOWNLOADS_DIR, "Meshtastic")
CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "fetchtastic.yaml")


def config_exists():
    """
    Check if the configuration file exists.
    """
    return os.path.exists(CONFIG_FILE)


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
    print("Running Fetchtastic Setup...")

    # Install required Termux packages first
    if is_termux():
        install_termux_packages()
        # Check if storage is set up
        check_storage_setup()
        print("Termux storage is set up.")

    # Proceed with the rest of the setup
    if not os.path.exists(DEFAULT_CONFIG_DIR):
        os.makedirs(DEFAULT_CONFIG_DIR)

    config = {}
    if config_exists():
        # Load existing configuration
        config = load_config()
        print(
            "Existing configuration found. You can keep current settings or change them."
        )
        is_first_run = False
    else:
        # Initialize default configuration
        config = {}
        is_first_run = True

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
        if auto_extract == "y":
            print(
                "Enter the keywords to match for extraction from the firmware zip files, separated by spaces."
            )
            print("Example: rak4631- tbeam-2 t1000-e- tlora-v2-1-1_6- device-")
            if config.get("EXTRACT_PATTERNS"):
                current_patterns = " ".join(config.get("EXTRACT_PATTERNS", []))
                print(f"Current patterns: {current_patterns}")
                extract_patterns = input(
                    "Extraction patterns (leave blank to keep current): "
                ).strip()
                if extract_patterns:
                    config["AUTO_EXTRACT"] = True
                    config["EXTRACT_PATTERNS"] = extract_patterns.split()
                else:
                    # Keep existing patterns
                    pass
            else:
                extract_patterns = input("Extraction patterns: ").strip()
                if extract_patterns:
                    config["AUTO_EXTRACT"] = True
                    config["EXTRACT_PATTERNS"] = extract_patterns.split()
                else:
                    config["AUTO_EXTRACT"] = False
                    print(
                        "No patterns selected, no files will be extracted. Run setup again if you wish to change this."
                    )
                    # Skip exclude patterns prompt
                    config["EXCLUDE_PATTERNS"] = []
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
                    print("Example: .hex tcxo")
                    if config.get("EXCLUDE_PATTERNS"):
                        current_excludes = " ".join(config.get("EXCLUDE_PATTERNS", []))
                        print(f"Current exclude patterns: {current_excludes}")
                        exclude_patterns = input(
                            "Exclude patterns (leave blank to keep current): "
                        ).strip()
                        if exclude_patterns:
                            config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                        else:
                            # Keep existing patterns
                            pass
                    else:
                        exclude_patterns = input("Exclude patterns: ").strip()
                        if exclude_patterns:
                            config["EXCLUDE_PATTERNS"] = exclude_patterns.split()
                        else:
                            config["EXCLUDE_PATTERNS"] = []
                else:
                    # User chose not to exclude patterns
                    config["EXCLUDE_PATTERNS"] = []
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

    # Set the download directory to the same as the config directory
    download_dir = DEFAULT_CONFIG_DIR
    config["DOWNLOAD_DIR"] = download_dir

    # Save configuration to YAML file before proceeding
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(config, f)

    # Cron job setup
    if is_termux():
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
                install_crond()
                setup_cron_job()
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
                setup_boot_script()
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
                    remove_cron_job()
                    print("Daily cron job has been removed.")

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
                    remove_reboot_cron_job()
                    print("Reboot cron job has been removed.")
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
    notifications_default = "yes"  # Default to 'yes'
    notifications = (
        input(
            f"Would you like to set up notifications via NTFY? [y/n] (default: {notifications_default}): "
        )
        .strip()
        .lower()
        or "y"
    )
    if notifications == "y":
        ntfy_server = input(
            f"Enter the NTFY server (current: {config.get('NTFY_SERVER', 'ntfy.sh')}): "
        ).strip() or config.get("NTFY_SERVER", "ntfy.sh")
        if not ntfy_server.startswith("http://") and not ntfy_server.startswith(
            "https://"
        ):
            ntfy_server = "https://" + ntfy_server

        current_topic = config.get(
            "NTFY_TOPIC",
            "fetchtastic-"
            + "".join(random.choices(string.ascii_lowercase + string.digits, k=6)),
        )
        topic_name = (
            input(f"Enter a unique topic name (current: {current_topic}): ").strip()
            or current_topic
        )

        config["NTFY_TOPIC"] = topic_name
        config["NTFY_SERVER"] = ntfy_server

        # Save configuration with NTFY settings
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)

        full_topic_url = f"{ntfy_server.rstrip('/')}/{topic_name}"
        print(f"Notifications set up using topic: {topic_name}")
        if is_termux():
            print("Subscribe by pasting the topic name in the ntfy app.")
        else:
            print(
                "Subscribe by visiting the full topic URL in your browser or ntfy app."
            )
        print(f"Full topic URL: {full_topic_url}")

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
        else:
            print("You can copy the topic information from above.")

        # Ask if the user wants notifications only when new files are downloaded
        notify_on_download_only_default = (
            "yes" if config.get("NOTIFY_ON_DOWNLOAD_ONLY", False) else "yes"
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

    else:
        config["NTFY_TOPIC"] = ""
        config["NTFY_SERVER"] = ""
        config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f)
        print("Notifications have been disabled.")

    # Ask if the user wants to perform a first run
    perform_first_run = (
        input("Would you like to start the first run now? [y/n] (default: yes): ")
        .strip()
        .lower()
        or "y"
    )
    if perform_first_run == "y":
        print("Starting first run, this may take a few minutes...")
        downloader.main()
    else:
        print("Setup complete. Run 'fetchtastic download' to start downloading.")


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
    """
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
    """
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
    """
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
    """
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
    """
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
    """
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


def load_config():
    """
    Loads the configuration from the YAML file.
    """
    if not config_exists():
        return None
    with open(CONFIG_FILE, "r") as f:
        config = yaml.safe_load(f)
    return config
