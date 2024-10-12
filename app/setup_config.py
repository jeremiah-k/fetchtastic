# app/setup_config.py

import os
import yaml
import subprocess
from . import menu_apk
from . import menu_firmware
from . import downloader  # Import downloader to perform first run

# Define the default configuration directory
HOME_DIR = os.path.expanduser("~")

# Try to find the Downloads directory
DOWNLOADS_DIR = os.path.join(HOME_DIR, 'Downloads')
if not os.path.exists(DOWNLOADS_DIR):
    # Try other common locations
    DOWNLOADS_DIR = os.path.join(HOME_DIR, 'Download')
    if not os.path.exists(DOWNLOADS_DIR):
        # Use HOME_DIR if Downloads directory is not found
        DOWNLOADS_DIR = HOME_DIR

DEFAULT_CONFIG_DIR = os.path.join(DOWNLOADS_DIR, 'Fetchtastic')
CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, 'fetchtastic.yaml')

def config_exists():
    return os.path.exists(CONFIG_FILE)

def run_setup():
    print("Running Fetchtastic Setup...")
    if not os.path.exists(DEFAULT_CONFIG_DIR):
        os.makedirs(DEFAULT_CONFIG_DIR)

    config = {}

    # Prompt to save APKs, firmware, or both
    save_choice = input("Do you want to save APKs, firmware, or both? [a/f/b] (default: b): ").strip().lower() or 'b'
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
            save_apks = False
            config['SAVE_APKS'] = False
        else:
            config['SELECTED_APK_ASSETS'] = apk_selection['selected_assets']
    if save_firmware:
        firmware_selection = menu_firmware.run_menu()
        if not firmware_selection:
            save_firmware = False
            config['SAVE_FIRMWARE'] = False
        else:
            config['SELECTED_FIRMWARE_ASSETS'] = firmware_selection['selected_assets']

    # Prompt for number of versions to keep
    if save_apks:
        android_versions_to_keep = input("Enter the number of different versions of the Android app to keep (default: 2): ").strip() or '2'
        config['ANDROID_VERSIONS_TO_KEEP'] = int(android_versions_to_keep)
    if save_firmware:
        firmware_versions_to_keep = input("Enter the number of different versions of the firmware to keep (default: 2): ").strip() or '2'
        config['FIRMWARE_VERSIONS_TO_KEEP'] = int(firmware_versions_to_keep)

        # Prompt for automatic extraction
        auto_extract = input("Do you want to automatically extract specific files from firmware zips? [y/n] (default: n): ").strip().lower() or 'n'
        if auto_extract == 'y':
            extract_patterns = input("Enter the strings to match for extraction from the firmware .zip files, separated by spaces: ").strip()
            if extract_patterns:
                config['AUTO_EXTRACT'] = True
                config['EXTRACT_PATTERNS'] = extract_patterns.split()
            else:
                config['AUTO_EXTRACT'] = False
        else:
            config['AUTO_EXTRACT'] = False

    # Set the download directory to the same as the config directory
    download_dir = DEFAULT_CONFIG_DIR
    config['DOWNLOAD_DIR'] = download_dir

    # Prompt for NTFY server configuration
    notifications = input("Do you want to set up notifications via NTFY? [y/n] (default: y): ").strip().lower() or 'y'
    if notifications == 'y':
        ntfy_server = input("Enter the NTFY server (default: ntfy.sh): ").strip() or 'ntfy.sh'
        if not ntfy_server.startswith('http://') and not ntfy_server.startswith('https://'):
            ntfy_server = 'https://' + ntfy_server
        topic_name = input("Enter a unique topic name (default: fetchtastic): ").strip() or 'fetchtastic'
        ntfy_topic = f"{ntfy_server}/{topic_name}"
        config['NTFY_SERVER'] = ntfy_topic
    else:
        config['NTFY_SERVER'] = ''

    # Save configuration to YAML file
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f)

    print(f"Setup complete. Configuration saved at {CONFIG_FILE}")

    # Ask if the user wants to perform a first run
    perform_first_run = input("Do you want to perform a first run now? [y/n] (default: y): ").strip().lower() or 'y'
    if perform_first_run == 'y':
        print("Performing first run, this may take a few minutes...")
        downloader.main()

    # Ask if the user wants to set up a cron job
    setup_cron = input("Do you want to add a cron job to run Fetchtastic daily at 3 AM? [y/n] (default: y): ").strip().lower() or 'y'
    if setup_cron == 'y':
        # Install crond if not already installed
        install_crond()
        # Call function to set up cron job
        setup_cron_job()
    else:
        print("Skipping cron job setup.")

def install_crond():
    try:
        # Check if crond is installed
        result = subprocess.run(['which', 'crond'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print("Installing crond...")
            subprocess.run(['pkg', 'install', 'termux-services', '-y'], check=True)
            subprocess.run(['sv-enable', 'crond'], check=True)
            print("crond installed and started.")
        else:
            print("crond is already installed.")
    except Exception as e:
        print(f"An error occurred while installing crond: {e}")

def setup_cron_job():
    try:
        # Get current crontab entries
        result = subprocess.run(['crontab', '-l'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            existing_cron = ''
        else:
            existing_cron = result.stdout

        # Check for existing cron jobs related to fetchtastic
        if 'fetchtastic download' in existing_cron:
            print("An existing cron job for Fetchtastic was found:")
            print(existing_cron)
            keep_cron = input("Do you want to keep the existing crontab entry? [y/n] (default: y): ").strip().lower() or 'y'
            if keep_cron == 'n':
                # Remove existing fetchtastic cron jobs
                new_cron = '\n'.join([line for line in existing_cron.split('\n') if 'fetchtastic download' not in line])
                # Update crontab
                process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
                process.communicate(input=new_cron)
                print("Existing Fetchtastic cron job removed.")
                # Ask if they want to add a new cron job
                add_cron = input("Do you want to add a new crontab entry to run Fetchtastic daily at 3 AM? [y/n] (default: y): ").strip().lower() or 'y'
                if add_cron == 'y':
                    # Add new cron job
                    new_cron += f"\n0 3 * * * fetchtastic download\n"
                    # Update crontab
                    process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
                    process.communicate(input=new_cron)
                    print("New cron job added.")
                else:
                    print("Skipping cron job installation.")
            else:
                print("Keeping existing crontab entry.")
        else:
            # No existing fetchtastic cron job
            add_cron = input("Do you want to add a crontab entry to run Fetchtastic daily at 3 AM? [y/n] (default: y): ").strip().lower() or 'y'
            if add_cron == 'y':
                # Add new cron job
                new_cron = existing_cron.strip() + f"\n0 3 * * * fetchtastic download\n"
                # Update crontab
                process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
                process.communicate(input=new_cron)
                print("Cron job added to run Fetchtastic daily at 3 AM.")
            else:
                print("Skipping cron job installation.")
    except Exception as e:
        print(f"An error occurred while setting up the cron job: {e}")

def load_config():
    if not config_exists():
        return None
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    return config
