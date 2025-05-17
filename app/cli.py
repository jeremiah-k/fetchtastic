# app/cli.py

import argparse
import os
import platform
import shutil
import subprocess
import sys

from . import downloader, repo_downloader, setup_config


def main():
    parser = argparse.ArgumentParser(
        description="Fetchtastic - Meshtastic Firmware and APK Downloader"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Command to run setup
    subparsers.add_parser("setup", help="Run the setup process")

    # Command to download firmware and APKs
    subparsers.add_parser("download", help="Download firmware and APKs")

    # Command to display NTFY topic
    subparsers.add_parser("topic", help="Display the current NTFY topic")

    # Command to clean/remove Fetchtastic files and settings
    subparsers.add_parser(
        "clean", help="Remove Fetchtastic configuration, downloads, and cron jobs"
    )

    # Command to display version
    subparsers.add_parser("version", help="Display Fetchtastic version")

    # Command to display help
    subparsers.add_parser("help", help="Display help information")

    # Command to interact with the meshtastic.github.io repository
    repo_parser = subparsers.add_parser(
        "repo",
        help="Interact with the meshtastic.github.io repository",
        description="Browse and download files from the meshtastic.github.io repository or clean the repository download directory.",
    )
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command")

    # Repo download command
    repo_subparsers.add_parser(
        "download",
        help="Browse and download files from the meshtastic.github.io repository",
        description="Browse directories in the meshtastic.github.io repository, select files, and download them to the repo directory.",
    )

    # Repo clean command
    repo_subparsers.add_parser(
        "clean",
        help="Clean the repository download directory",
        description="Remove all files and directories from the repository download directory (firmware/repo).",
    )

    args = parser.parse_args()

    if args.command == "setup":
        # Run the setup process
        setup_config.run_setup()
    elif args.command == "download":
        # Check if configuration exists
        if not setup_config.config_exists():
            print("No configuration found. Running setup.")
            setup_config.run_setup()
        else:
            # Run the downloader
            downloader.main()
    elif args.command == "topic":
        # Display the NTFY topic and prompt to copy to clipboard
        config = setup_config.load_config()
        if config and config.get("NTFY_SERVER") and config.get("NTFY_TOPIC"):
            ntfy_server = config["NTFY_SERVER"].rstrip("/")
            ntfy_topic = config["NTFY_TOPIC"]
            full_url = f"{ntfy_server}/{ntfy_topic}"
            print(f"Current NTFY topic URL: {full_url}")
            print(f"Topic name: {ntfy_topic}")

            if setup_config.is_termux():
                copy_prompt_text = "Do you want to copy the topic name to the clipboard? [y/n] (default: yes): "
                text_to_copy = ntfy_topic
            else:
                copy_prompt_text = "Do you want to copy the topic URL to the clipboard? [y/n] (default: yes): "
                text_to_copy = full_url

            copy_to_clipboard = input(copy_prompt_text).strip().lower() or "y"
            if copy_to_clipboard == "y":
                success = copy_to_clipboard_func(text_to_copy)
                if success:
                    if setup_config.is_termux():
                        print("Topic name copied to clipboard.")
                    else:
                        print("Topic URL copied to clipboard.")
                else:
                    print("Failed to copy to clipboard.")
            else:
                print("You can copy the topic information from above.")
        else:
            print(
                "Notifications are not set up. Run 'fetchtastic setup' to configure notifications."
            )
    elif args.command == "clean":
        # Run the clean process
        run_clean()
    elif args.command == "version":
        print(f"Fetchtastic version {get_fetchtastic_version()}")
    elif args.command == "help":
        # Check if a subcommand was specified
        if len(sys.argv) > 2:
            help_command = sys.argv[2]
            if help_command == "repo":
                # Show help for repo command
                repo_parser.print_help()
                # Check if there's a repo subcommand specified
                if len(sys.argv) > 3:
                    repo_subcommand = sys.argv[3]
                    if repo_subcommand == "download":
                        # Find the download subparser and print its help
                        for action in repo_subparsers._actions:
                            if isinstance(action, argparse._SubParsersAction):
                                download_parser = action.choices.get("download")
                                if download_parser:
                                    print("\nRepo download command help:")
                                    download_parser.print_help()
                                break
                    elif repo_subcommand == "clean":
                        # Find the clean subparser and print its help
                        for action in repo_subparsers._actions:
                            if isinstance(action, argparse._SubParsersAction):
                                clean_parser = action.choices.get("clean")
                                if clean_parser:
                                    print("\nRepo clean command help:")
                                    clean_parser.print_help()
                                break
            else:
                # Show general help
                parser.print_help()
        else:
            # No subcommand specified, show general help
            parser.print_help()
    elif args.command == "repo":
        # Handle repo subcommands
        if not setup_config.config_exists():
            print("No configuration found. Running setup.")
            setup_config.run_setup()

        config = setup_config.load_config()
        if not config:
            print("Configuration not found. Please run 'fetchtastic setup' first.")
            return

        if args.repo_command == "download":
            # Run the repository downloader
            repo_downloader.main(config)
        elif args.repo_command == "clean":
            # Clean the repository directory
            run_repo_clean(config)
        else:
            # No repo subcommand provided
            repo_parser.print_help()
    elif args.command is None:
        # No command provided
        parser.print_help()
    else:
        parser.print_help()


def copy_to_clipboard_func(text):
    if setup_config.is_termux():
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


def run_clean():
    print(
        "This will remove Fetchtastic configuration files, downloaded files, and cron job entries."
    )
    confirm = (
        input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower()
        or "n"
    )
    if confirm != "y":
        print("Clean operation cancelled.")
        return

    # Remove configuration file
    config_file = setup_config.CONFIG_FILE
    if os.path.exists(config_file):
        os.remove(config_file)
        print(f"Removed configuration file: {config_file}")

    # Remove contents of download directory
    download_dir = setup_config.DEFAULT_CONFIG_DIR
    if os.path.exists(download_dir):
        for item in os.listdir(download_dir):
            item_path = os.path.join(download_dir, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.remove(item_path)
                    print(f"Removed file: {item_path}")
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    print(f"Removed directory: {item_path}")
            except Exception as e:
                print(f"Failed to delete {item_path}. Reason: {e}")
        print(f"Cleaned contents of download directory: {download_dir}")

    # Remove cron job entries
    try:
        # Get current crontab entries
        result = subprocess.run(
            ["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            existing_cron = result.stdout.strip()
            # Remove existing fetchtastic cron jobs
            cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
            cron_lines = [
                line
                for line in cron_lines
                if "# fetchtastic" not in line and "fetchtastic download" not in line
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
            print("Removed Fetchtastic cron job entries.")
    except Exception as e:
        print(f"An error occurred while removing cron jobs: {e}")

    # Remove boot script if exists
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print(f"Removed boot script: {boot_script}")

    print(
        "The downloaded files and Fetchtastic configuration have been removed from your system."
    )


def run_repo_clean(config):
    """
    Cleans the repository download directory.
    """
    print(
        "This will remove all files downloaded from the meshtastic.github.io repository."
    )
    confirm = (
        input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower()
        or "n"
    )
    if confirm != "y":
        print("Clean operation cancelled.")
        return

    # Clean the repo directory
    download_dir = config.get("DOWNLOAD_DIR")
    if not download_dir:
        print("Download directory not configured.")
        return

    success = repo_downloader.clean_repo_directory(download_dir)
    if success:
        print("Repository directory cleaned successfully.")
    else:
        print("Failed to clean repository directory.")


def get_fetchtastic_version():
    try:
        from importlib.metadata import version
    except ImportError:
        # For Python < 3.8
        from importlib_metadata import version
    try:
        return version("fetchtastic")
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
