# app/setup_config.py

import os
import yaml
import subprocess
import random
import string
import shutil  # Added for shutil.which()
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

def run_setup():
    # Check if running in Termux
    if not is_termux():
        print("Warning: Fetchtastic is designed to run in Termux on Android.")
        print("For more information, visit https://github.com/jeremiah-k/fetchtastic/")
        return

    print("Running Fetchtastic Setup...")
    if not os.path.exists(DEFAULT_CONFIG_DIR):
        os.makedirs(DEFAULT_CONFIG_DIR)

    config = {}

    # Prompt to save APKs, firmware, or both
    save_choice = input("Would you like to download APKs, firmware, or both? [a/f/b] (default: both): ").strip().lower() or 'b'
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
        android_versions_to_keep = input("How many versions of the Android app would you like to keep? (default is 2): ").strip() or '2'
        config['ANDROID_VERSIONS_TO_KEEP'] = int(android_versions_to_keep)
    if save_firmware:
        firmware_versions_to_keep = input("How many versions of the firmware would you like to keep? (default is 2): ").strip() or '2'
        config['FIRMWARE_VERSIONS_TO_KEEP'] = int(firmware_versions_to_keep)

        # Prompt for automatic extraction
        auto_extract = input("Would you like to automatically extract specific files from firmware zip archives? [y/n] (default: no): ").strip().lower() or 'n'
        if auto_extract == 'y':
            print("Enter the keywords to match for extraction from the firmware zip files, separated by spaces.")
            print("Example: rak4631- tbeam-2 t1000-e- tlora-v2-1-1_6-")
            extract_patterns = input("Extraction patterns: ").strip()
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

    # Save configuration to YAML file before proceeding
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f)

    # Ask if the user wants to set up a cron job
    setup_cron = input("Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: yes): ").strip().lower() or 'y'
    if setup_cron == 'y':
        install_crond()
        setup_cron_job()
    else:
        print("Skipping cron job setup.")

    # Prompt for NTFY server configuration
    notifications = input("Would you like to set up notifications via NTFY? [y/n] (default: yes): ").strip().lower() or 'y'
    if notifications == 'y':
        ntfy_server = input("Enter the NTFY server (default: ntfy.sh): ").strip() or 'ntfy.sh'
        if not ntfy_server.startswith('http://') and not ntfy_server.startswith('https://'):
            ntfy_server = 'https://' + ntfy_server

        default_topic = 'fetchtastic-' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        topic_name = input(f"Enter a unique topic name (default: {default_topic}): ").strip() or default_topic

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

        print("Run 'fetchtastic topic' to view your current topic.")
        print("Run 'fetchtastic setup' again or edit the YAML file to change the topic.")
    else:
        config['NTFY_TOPIC'] = ''
        config['NTFY_SERVER'] = ''
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(config, f)
        print("Notifications have not been set up.")

    # Ask if the user wants to perform a first run
    perform_first_run = input("Would you like to start the first run now? [y/n] (default: yes): ").strip().lower() or 'y'
    if perform_first_run == 'y':
        print("Starting first run, this may take a few minutes...")
        downloader.main()
    else:
        print("Setup complete. Run 'fetchtastic download' to start downloading.")

def is_termux():
    return 'com.termux' in os.environ.get('PREFIX', '')

def copy_to_clipboard_termux(text):
    try:
        subprocess.run(['termux-clipboard-set'], input=text.encode('utf-8'), check=True)
    except Exception as e:
        print(f"An error occurred while copying to clipboard: {e}")

def install_crond():
    try:
        crond_path = shutil.which('crond')
        if crond_path is None:
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
            keep_cron = input("Do you want to keep the existing crontab entry? [y/n] (default: yes): ").strip().lower() or 'y'
            if keep_cron == 'n':
                # Remove existing fetchtastic cron jobs
                new_cron = '\n'.join([line for line in existing_cron.split('\n') if 'fetchtastic download' not in line])
                # Update crontab
                process = subprocess.Popen(['crontab', '-'], stdin=subprocess.PIPE, text=True)
                process.communicate(input=new_cron)
                print("Existing Fetchtastic cron job removed.")
                # Ask if they want to add a new cron job
                add_cron = input("Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: yes): ").strip().lower() or 'y'
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
            add_cron = input("Would you like to schedule Fetchtastic to run daily at 3 AM? [y/n] (default: yes): ").strip().lower() or 'y'
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

def run_clean():
    print("This will remove Fetchtastic configuration files, downloaded files, and cron job entries.")
    confirm = input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower() or 'n'
    if confirm != 'y':
        print("Clean operation cancelled.")
        return

    # Remove configuration file
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
        print(f"Removed configuration file: {CONFIG_FILE}")

    # Remove download directory
    if os.path.exists(DEFAULT_CONFIG_DIR):
        shutil.rmtree(DEFAULT_CONFIG_DIR)
        print(f"Removed download directory: {DEFAULT_CONFIG_DIR}")

    # Remove cron job entries
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
            print("Removed Fetchtastic cron job entries.")
    except Exception as e:
        print(f"An error occurred while removing cron jobs: {e}")

    print("Fetchtastic has been cleaned from your system.")
    print("If you installed Fetchtastic via pip and wish to uninstall it, run 'pip uninstall fetchtastic'.")

def load_config():
    if not config_exists():
        return None
    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    return config
