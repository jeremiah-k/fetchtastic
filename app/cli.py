# app/cli.py

import argparse
from . import downloader
from . import setup_config

def main():
    parser = argparse.ArgumentParser(description="Fetchtastic - Meshtastic Firmware and APK Downloader")
    subparsers = parser.add_subparsers(dest='command')

    # Command to run setup
    parser_setup = subparsers.add_parser('setup', help='Run the setup process')

    # Command to download firmware and APKs
    parser_download = subparsers.add_parser('download', help='Download firmware and APKs')

    # Command to display NTFY topic
    parser_topic = subparsers.add_parser('topic', help='Display the current NTFY topic')

    # Command to clean/remove Fetchtastic files and settings
    parser_clean = subparsers.add_parser('clean', help='Remove Fetchtastic configuration, downloads, and cron jobs')

    args = parser.parse_args()

    if args.command == 'setup':
        # Run the setup process
        setup_config.run_setup()
    elif args.command == 'download':
        # Check if configuration exists
        if not setup_config.config_exists():
            print("No configuration found. Running setup.")
            setup_config.run_setup()
        # Run the downloader
        downloader.main()
    elif args.command == 'topic':
        # Display the NTFY topic
        config = setup_config.load_config()
        if config and config.get('NTFY_SERVER'):
            print(f"Current NTFY topic URL: {config['NTFY_SERVER']}")
        else:
            print("Notifications are not set up. Run 'fetchtastic setup' to configure notifications.")
    elif args.command == 'clean':
        # Run the clean process
        setup_config.run_clean()
    elif args.command is None:
        # No command provided
        print("No command provided.")
        print("For help and available commands, run 'fetchtastic --help'.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
