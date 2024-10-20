# app/setup_config.py

import os
import sys
import yaml
import subprocess
import random
import string
import shutil
from . import menu_apk
from . import menu_firmware
from . import downloader  # Import downloader to perform first run

def get_downloads_dir():
    # For Termux, use ~/storage/downloads
    if 'com.termux' in os.environ.get('PREFIX', ''):
        storage_downloads = os.path.expanduser("~/storage/downloads")
        if os.path.exists(storage_downloads):
            return storage_downloads
    # For other environments, use standard Downloads directories
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, 'Downloads')
    if os.path.exists(downloads_dir):
        return downloads_dir
    downloads_dir = os.path.join(home_dir, 'Download')
    if os.path.exists(downloads_dir):
        return downloads_dir
    # Fallback to home directory
    return home_dir

DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_CONFIG_DIR = os.path.join(DOWNLOADS_DIR, 'Meshtastic')
CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, 'fetchtastic.yaml')

def config_exists():
    return os.path.exists(CONFIG_FILE)

def is_termux():
    return 'com.termux' in os.environ.get('PREFIX', '')

def check_storage_setup():
    # Check if the Termux storage directory and Downloads are set up and writable
    storage_dir = os.path.expanduser("~/storage")
    storage_downloads = os.path.expanduser("~/storage/downloads")

    while True:
        if os.path.exists(storage_dir) and os.path.exists(storage_downloads) and os.access(storage_downloads, os.W_OK):
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
        print("Existing configuration found. You can keep current settings or change them.")
    else:
        # Initialize default configuration
        config = {}

    # Prompt to save APKs, firmware, or both
    save_choice = input(f"Would you like to download APKs, firmware, or both? [a/f/b] (default: both): ").strip().lower() or 'both'
    if save_choice == 'a':
        save_apks = True
        save_firmware = False
    elif save_choice == 'f':
        save_apks = False
        save_firmware = True
    else:
        save_apks = True
        save_firmware = True
    config['SAVE_APKS'] = save_apks
    config['SAVE_FIRMWARE'] = save_firmware

    # Run the menu scripts based on user choices
    if save_apks:
        apk_selection = menu_apk.run_menu()
        if not apk_selection:
            print("No APK assets selected. APKs will not be downloaded.")
            save_apks = False
            config['SAVE_APKS'] = False
        else:
            config['SELECTED_APK_ASSETS'] = apk_selection['selected_assets']
    if save_firmware:
        firmware_selection = menu_firmware.run_menu()
        if not firmware_selection:
            print("No firmware assets selected. Firmware will not be downloaded.")
            save_firmware = False
            config['SAVE_FIRMWARE'] = False
        else:
            config['SELECTED_FIRMWARE_ASSETS'] = firmware_selection['selected_assets']

    # If both save_apks and save_firmware are False, inform the user and exit setup
    if not save_apks and not save_firmware:
        print("Please select at least one type of asset to download (APK or firmware).")
        print("Run 'fetchtastic setup' again and select at least one asset.")
        return

    # Prompt for number of versions to keep
    if save_apks:
        current_versions = config.get('ANDROID_VERSIONS_TO_KEEP', 2)
        android_versions_to_keep = input(f"How many versions of the Android app would you like to keep? (default is {current_versions}): ").strip() or str(current_versions)
        config['ANDROID_VERSIONS_TO_KEEP'] = int(android_versions_to_keep)
    if save_firmware:
        current_versions = config.get('FIRMWARE_VERSIONS_TO_KEEP', 2)
        firmware_versions_to_keep = input(f"How many versions of the firmware would you like to keep? (default is {current_versions}): ").strip() or str(current_versions)
        config['FIRMWARE_VERSIONS_TO_KEEP'] = int(firmware_versions_to_keep)

        # Prompt for automatic extraction
        auto_extract_default = 'yes' if config.get('AUTO_EXTRACT', False) else 'no'
        auto_extract = input(f"Would you like to automatically extract specific files from firmware zip archives? [y/n] (default: {auto_extract_default}): ").strip().lower() or auto_extract_default[0]
        if auto_extract == 'y':
            print("Enter the keywords to match for extraction from the firmware zip files, separated by spaces.")
            print("Example: rak4631- tbeam-2 t1000-e- tlora-v2-1-1_6-")
            if config.get('EXTRACT_PATTERNS'):
                current_patterns = ' '.join(config.get('EXTRACT_PATTERNS', []))
                print(f"Current patterns: {current_patterns}")
                extract_patterns = input("Extraction patterns (leave blank to keep current): ").strip()
                if extract_patterns:
                    config['AUTO_EXTRACT'] = True
                    config['EXTRACT_PATTERNS'] = extract_patterns.split()
                else:
                    # Keep existing patterns
                    config['AUTO_EXTRACT'] = True
            else:
                extract_patterns = input("Extraction patterns: ").strip()
                if extract_patterns:
                    config['AUTO_EXTRACT'] = True
                    config['EXTRACT_PATTERNS'] = extract_patterns.split()
                else:
                    config['AUTO_EXTRACT'] = False
                    print("No extraction patterns provided. Extraction will be skipped.")
                    print("You can run 'fetchtastic setup' again to set extraction patterns.")
        else:
            config['AUTO_EXTRACT'] = False

    # Ask if the user wants to only download when connected to Wi-Fi
    wifi_only_default = 'yes' if config.get('WIFI_ONLY', True) else 'no'
    wifi_only = input(f"Do you want to only download when connected to Wi-Fi? [y/n] (default: {wifi_only_default}): ").strip().lower() or wifi_only_default[0]
    config['WIFI_ONLY'] = True if wifi_only == 'y' else False

    # Set the download directory to the same as the config directory
    download_dir = DEFAULT_CONFIG_DIR
    config['DOWNLOAD_DIR'] = download_dir

    # Save configuration to YAML file before proceeding
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f)

    # Ask if the user wants to set up a cron job
    cron_default = 'yes'  # Default to 'yes'
    setup_cron = input(f"Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: {cron_default}): ").strip().lower() or cron_default[0]
    if setup_cron == 'y':
        install_crond()
        setup_cron_job()
    else:
        remove_cron_job()
        print("Cron job has been removed.")

    # Ask if the user wants to run Fetchtastic on boot
    boot_default = 'yes'  # Default to 'yes'
    run_on_boot = input(f"Do you want Fetchtastic to run on device boot? [y/n] (default: {boot_default}): ").strip().lower() or boot_default[0]
    if run_on_boot == 'y':
        setup_boot_script()
    else:
        remove_boot_script()
        print("Boot script has been removed.")

    # Prompt for NTFY server configuration
    notifications_default = 'yes'  # Default to 'yes'
    notifications = input(f"Would you like to set up notifications via NTFY? [y/n] (default: {notifications_default}): ").strip().lower() or 'y'
    if notifications == 'y':
        ntfy_server = input(f"Enter the NTFY server (current: {config.get('NTFY_SERVER', 'ntfy.sh')}): ").strip() or config.get('NTFY_SERVER', 'ntfy.sh')
        if not ntfy_server.startswith('http://') and not ntfy_server.startswith('https://'):
            ntfy_server = 'https://' + ntfy_server

        current_topic = config.get('NTFY_TOPIC', 'fetchtastic-' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))
        topic_name = input(f"Enter a unique topic name (current: {current_topic}): ").strip() or current_topic

        config['NTFY_TOPIC'] = topic_name
        config['NTFY_SERVER'] = ntfy_server

        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(config, f)

        full_topic_url = f"{ntfy_server.rstrip('/')}/{topic_name}"
        print(f"Notifications set up using topic: {topic_name}")
        print(f"Subscribe by pasting the topic name in the ntfy app.")
        print(f"Full topic URL: {full_topic_url}")

        copy_to_clipboard = input("Do you want to copy the topic name to the clipboard? [y/n] (default: yes): ").strip().lower() or 'y'
        if copy_to_clipboard == 'y':
            copy_to_clipboard_termux(topic_name)
            print("Topic name copied to clipboard.")
        else:
            print("You can copy the topic name from above.")

    else:
        config['NTFY_TOPIC'] = ''
        config['NTFY_SERVER'] = ''
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(config, f)
        print("Notifications have been disabled.")

    # Ask if the user wants to perform a first run
    perform_first_run = input("Would you like to start the first run now? [y/n] (default: yes): ").strip().lower() or 'y'
    if perform_first_run == 'y':
        print("Starting first run, this may take a few minutes...")
        downloader.main()
    else:
        print("Setup complete. Run 'fetchtastic download' to start downloading.")

def copy_to_clipboard_termux(text):
    try:
        subprocess.run(['termux-clipboard-set'], input=text.encode('utf-8'), check=True)
    except Exception as e:
        print(f"An error occurred while copying to clipboard: {e}")

def install_termux_packages():
    # Install termux-api, termux-services, and cronie if they are not installed
    packages_to_install = []
    # Check for termux-api
    if shutil.which('termux-battery-status') is None:
        packages_to_install.append('termux-api')
    # Check for termux-services
    if shutil.which('sv-enable') is None:
        packages_to_install.append('termux-services')
    # Check for cronie
    if shutil.which('crond') is None:
        packages_to_install.append('cronie')
    if packages_to_install:
        print("Installing required Termux packages...")
        subprocess.run(['pkg', 'install'] + packages_to_install + ['-y'], check=True)
        print("Required Termux packages installed.")
    else:
        print("All required Termux packages are already installed.")

def setup_storage():
    # Run termux-setup-storage
    print("Setting up Termux storage access...")
    try:
        subprocess.run(['termux-setup-storage'], check=True)
    except subprocess.CalledProcessError as e:
        print("An error occurred while setting up Termux storage.")
        print("Please grant storage permissions when prompted.")

def install_crond():
    try:
        crond_path = shutil.which('crond')
        if crond_path is None:
            print("Installing cronie...")
            # Install cronie
            subprocess.run(['pkg', 'install', 'cronie', '-y'], check=True)
            print("cronie installed.")
        else:
            print("cronie is already installed.")
        # Enable crond service
        subprocess.run(['sv-enable', 'crond'], check=True)
        print("crond service enabled.")
    except Exception as e:
        print(f"An error occurred while installing or enabling crond: {e}")

def setup_cron_job():
    try:
        # Get current crontab entries
        result = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            existing_cron = ''
        else:
            existing_cron = result.stdout

        # Remove existing fetchtastic cron jobs
        new_cron = '\n'.join([line for line in existing_cron.split('\n') if 'fetchtastic download' not in line])
        # Add new cron job
        new_cron += f"\n0 3 * * * fetchtastic download\n"
        # Update crontab
        process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
        process.communicate(input=new_cron)
        print("Cron job added to run Fetchtastic daily at 3 AM.")
    except Exception as e:
        print(f"An error occurred while setting up the cron job: {e}")

def remove_cron_job():
    try:
        # Get current crontab entries
        result = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            existing_cron = result.stdout
            # Remove existing fetchtastic cron jobs
            new_cron = '\n'.join([line for line in existing_cron.split('\n') if 'fetchtastic download' not in line])
            # Update crontab
            process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
            process.communicate(input=new_cron)
            print("Cron job removed.")
    except Exception as e:
        print(f"An error occurred while removing the cron job: {e}")

def is_cron_job_set():
    try:
        result = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0 and 'fetchtastic download' in result.stdout:
            return True
        else:
            return False
    except Exception:
        return False

def setup_boot_script():
    boot_dir = os.path.expanduser("~/.termux/boot")
    boot_script = os.path.join(boot_dir, "fetchtastic.sh")
    if not os.path.exists(boot_dir):
        os.makedirs(boot_dir)
        print("Created the Termux:Boot directory.")
        print("It seems that Termux:Boot is not installed or hasn't been run yet.")
        print("Please install Termux:Boot from F-Droid and run it once to enable boot scripts.")
    with open(boot_script, 'w') as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write("fetchtastic download\n")
    os.chmod(boot_script, 0o700)
    print("Boot script created to run Fetchtastic on device boot.")
    print("Note: The script may not run on boot until you have installed and run Termux:Boot at least once.")

def remove_boot_script():
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print("Boot script removed.")

def load_config():
    if not config_exists():
        return None
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    return config
