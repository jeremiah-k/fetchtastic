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

    args = parser.parse_args()

    if args.command == 'setup':
        # Run the setup process
        setup_config.run_setup()
    elif args.command == 'download' or args.command is None:
        # Check if configuration exists
        if not setup_config.config_exists():
            print("No configuration found. Running setup.")
            setup_config.run_setup()
        # Run the downloader
        downloader.main()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
