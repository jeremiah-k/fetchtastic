# app/cli.py

import argparse
import subprocess
import os
import shutil
from . import downloader
from . import setup_config

def main():
    parser = argparse.ArgumentParser(description="Fetchtastic - Meshtastic Firmware and APK Downloader")
    subparsers = parser.add_subparsers(dest='command')

    # Command to run setup
    subparsers.add_parser('setup', help='Run the setup process')

    # Command to download firmware and APKs
    subparsers.add_parser('download', help='Download firmware and APKs')

    # Command to display NTFY topic
    subparsers.add_parser('topic', help='Display the current NTFY topic')

    # Command to clean/remove Fetchtastic files and settings
    subparsers.add_parser('clean', help='Remove Fetchtastic configuration, downloads, and cron jobs')

    args = parser.parse_args()

    if args.command == 'setup':
        # Run the setup process
        setup_config.run_setup()
    elif args.command == 'download':
        # Check if configuration exists
        if not setup_config.config_exists():
            print("No configuration found. Running setup.")
            setup_config.run_setup()
        else:
            # Run the downloader
            downloader.main()
    elif args.command == 'topic':
        # Display the NTFY topic and prompt to copy to clipboard
        config = setup_config.load_config()
        if config and config.get('NTFY_SERVER') and config.get('NTFY_TOPIC'):
            ntfy_server = config['NTFY_SERVER'].rstrip('/')
            ntfy_topic = config['NTFY_TOPIC']
            full_url = f"{ntfy_server}/{ntfy_topic}"
            print(f"Current NTFY topic URL: {full_url}")
            print(f"Topic name: {ntfy_topic}")
            copy_to_clipboard = input("Do you want to copy the topic name to the clipboard? [y/n] (default: yes): ").strip().lower() or 'y'
            if copy_to_clipboard == 'y':
                copy_to_clipboard_termux(ntfy_topic)
                print("Topic name copied to clipboard.")
            else:
                print("You can copy the topic name from above.")
        else:
            print("Notifications are not set up. Run 'fetchtastic setup' to configure notifications.")
    elif args.command == 'clean':
        # Run the clean process
        run_clean()
    elif args.command is None:
        # No command provided
        print("No command provided.")
        print("For help and available commands, run 'fetchtastic --help'.")
    else:
        parser.print_help()

def copy_to_clipboard_termux(text):
    try:
        subprocess.run(['termux-clipboard-set'], input=text.encode('utf-8'), check=True)
    except Exception as e:
        print(f"An error occurred while copying to clipboard: {e}")

def run_clean():
    print("This will remove Fetchtastic configuration files, downloaded files, and cron job entries.")
    confirm = input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower() or 'n'
    if confirm != 'y':
        print("Clean operation cancelled.")
        return

    # Remove configuration file
    config_file = setup_config.CONFIG_FILE
    if os.path.exists(config_file):
        os.remove(config_file)
        print(f"Removed configuration file: {config_file}")

    # Remove download directory
    download_dir = setup_config.DEFAULT_CONFIG_DIR
    if os.path.exists(download_dir):
        shutil.rmtree(download_dir)
        print(f"Removed download directory: {download_dir}")

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

    # Remove boot script if exists
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print(f"Removed boot script: {boot_script}")

    print("Fetchtastic has been cleaned from your system.")
    print("If you installed Fetchtastic via pip and wish to uninstall it, run 'pip uninstall fetchtastic'.")

if __name__ == "__main__":
    main()
