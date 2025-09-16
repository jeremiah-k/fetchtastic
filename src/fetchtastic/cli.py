# src/fetchtastic/cli.py

import argparse
import os
import platform
import shutil
import subprocess
from typing import List

import platformdirs

from fetchtastic import downloader, repo_downloader, setup_config
from fetchtastic.log_utils import logger
from fetchtastic.setup_config import (
    copy_to_clipboard_func,
    display_version_info,
    get_upgrade_command,
)


def main():
    # Logging is automatically initialized by importing log_utils

    """
    Entry point for the Fetchtastic command-line interface.

    Parses command-line arguments and dispatches commands:
    - setup: run initial configuration or update Windows integrations (--update-integrations on Windows).
    - download: ensure configuration (migrating if necessary) and run the firmware/APK downloader.
    - topic: show configured NTFY topic/URL and optionally copy it to the clipboard.
    - clean: remove Fetchtastic configuration, downloads, and scheduled tasks.
    - version: display current and available Fetchtastic versions and upgrade instructions if applicable.
    - repo: interact with the meshtastic.github.io repository with subcommands:
        - browse: browse and download repository files.
        - clean: remove files from the repository download directory.
    - help: show contextual help for commands and repo subcommands.

    Side effects:
    - May read/write configuration, modify files/directories, invoke setup and downloader routines, modify crontab/startup entries, and copy text to the clipboard.
    - Prints information and logs status messages.

    Returns:
        None
    """
    parser = argparse.ArgumentParser(
        description="Fetchtastic - Meshtastic Firmware and APK Downloader"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Command to run setup
    setup_parser = subparsers.add_parser("setup", help="Run the setup process")
    setup_parser.add_argument(
        "--section",
        action="append",
        choices=sorted(setup_config.SETUP_SECTION_CHOICES),
        help="Only re-run specific setup sections (can be passed multiple times)",
    )
    setup_parser.add_argument(
        "sections",
        nargs="*",
        metavar="SECTION",
        help="Positional shorthand for selecting setup sections (e.g. 'setup firmware')",
    )

    # Only add Windows-specific flag on Windows
    if platform.system() == "Windows":
        setup_parser.add_argument(
            "--update-integrations",
            action="store_true",
            help="Update Windows integrations (Start Menu shortcuts) without full setup",
        )

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
    help_parser = subparsers.add_parser("help", help="Display help information")
    help_parser.add_argument(
        "help_command",
        nargs="?",
        metavar="COMMAND",
        help="Command to get help for (e.g., 'repo', 'setup')",
    )
    help_parser.add_argument(
        "help_subcommand",
        nargs="?",
        metavar="SUBCOMMAND",
        help="Subcommand to get help for (e.g., 'browse', 'clean')",
    )

    # Command to interact with the meshtastic.github.io repository
    repo_parser = subparsers.add_parser(
        "repo",
        help="Interact with the meshtastic.github.io repository",
        description="Browse and download files from the meshtastic.github.io repository or clean the repository download directory.",
    )
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command")

    # Repo browse command
    repo_subparsers.add_parser(
        "browse",
        help="Browse and download files from the meshtastic.github.io repository",
        description="Browse directories in the meshtastic.github.io repository, select files, and download them to the repo-dls directory.",
    )

    # Repo clean command
    repo_subparsers.add_parser(
        "clean",
        help="Clean the repository download directory",
        description="Remove all files and directories from the repository download directory (firmware/repo-dls).",
    )

    args = parser.parse_args()

    if args.command == "setup":
        # Display version information
        current_version, latest_version, update_available = display_version_info()

        # Check if this is just an integrations update
        if hasattr(args, "update_integrations") and args.update_integrations:
            # Only update Windows integrations
            if platform.system() == "Windows":
                logger.info("Updating Windows integrations...")
                config = setup_config.load_config()
                if config:
                    success = setup_config.create_windows_menu_shortcuts(
                        setup_config.CONFIG_FILE,
                        config.get("BASE_DIR", setup_config.BASE_DIR),
                    )
                    if success:
                        logger.info("Windows integrations updated successfully!")
                    else:
                        logger.error("Failed to update Windows integrations.")
                else:
                    logger.error(
                        "No configuration found. Run 'fetchtastic setup' first."
                    )
            else:
                logger.info("Integration updates are only available on Windows.")
        else:
            # Run the full setup process (optionally limited to specific sections)
            combined_sections: List[str] = (args.section or []) + (args.sections or [])

            # Validate and deduplicate sections
            if combined_sections:
                allowed = set(setup_config.SETUP_SECTION_CHOICES)
                invalid = [s for s in combined_sections if s not in allowed]
                if invalid:
                    parser.error(
                        f"invalid sections: {', '.join(invalid)} "
                        f"(choose from {', '.join(sorted(allowed))})"
                    )
                # Deduplicate while preserving order
                combined_sections = list(dict.fromkeys(combined_sections))

            setup_config.run_setup(sections=combined_sections or None)

        # Remind about updates at the end if available
        if update_available:
            upgrade_cmd = get_upgrade_command()
            logger.info("\nUpdate Available")
            logger.info(
                f"A newer version (v{latest_version}) of Fetchtastic is available!"
            )
            logger.info(f"Run '{upgrade_cmd}' to upgrade.")
    elif args.command == "download":
        # Check if configuration exists
        exists, config_path = setup_config.config_exists()
        if not exists:
            logger.info("No configuration found. Running setup.")
            setup_config.run_setup()
        else:
            # Check if config is in old location and needs migration
            if config_path == setup_config.OLD_CONFIG_FILE and not os.path.exists(
                setup_config.CONFIG_FILE
            ):
                separator = "=" * 80
                logger.info(f"\n{separator}")
                logger.info("Configuration Migration")
                logger.info(separator)
                # Automatically migrate without prompting
                setup_config.prompt_for_migration()  # Just logs the migration message
                if setup_config.migrate_config():
                    logger.info(
                        "Configuration successfully migrated to the new location."
                    )
                    # Update config_path to the new location
                    config_path = setup_config.CONFIG_FILE
                    # Re-load the configuration from the new location
                    config = setup_config.load_config(config_path)
                else:
                    logger.error(
                        "Failed to migrate configuration. Continuing with old location."
                    )
                logger.info(f"{separator}\n")

            # Display the config file location
            logger.info(f"Using configuration from: {config_path}")

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

            try:
                resp = input(copy_prompt_text)
            except EOFError:
                resp = ""
            resp = (resp or "y").strip().lower()
            if resp in {"y", "yes"}:
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
        # Get version information
        current_version, latest_version, update_available = display_version_info()

        # Log version information
        logger.info(f"Fetchtastic v{current_version}")
        if update_available and latest_version:
            upgrade_cmd = get_upgrade_command()
            logger.info(f"A newer version (v{latest_version}) is available!")
            logger.info(f"Run '{upgrade_cmd}' to upgrade.")
    elif args.command == "help":
        # Handle help command
        help_command = args.help_command
        help_subcommand = args.help_subcommand
        show_help(
            parser,
            repo_parser,
            repo_subparsers,
            help_command,
            help_subcommand,
            subparsers,
        )
    elif args.command == "repo":
        # Display version information
        current_version, latest_version, update_available = display_version_info()

        # Handle repo subcommands
        exists, _ = setup_config.config_exists()
        if not exists:
            print("No configuration found. Running setup.")
            setup_config.run_setup()

        config = setup_config.load_config()
        if not config:
            print("Configuration not found. Please run 'fetchtastic setup' first.")
            return

        if args.repo_command == "browse":
            # Run the repository downloader
            repo_downloader.main(config)

            # Remind about updates at the end if available
            if update_available:
                upgrade_cmd = get_upgrade_command()
                logger.info("\nUpdate Available")
                logger.info(
                    f"A newer version (v{latest_version}) of Fetchtastic is available!"
                )
                logger.info(f"Run '{upgrade_cmd}' to upgrade.")
        elif args.repo_command == "clean":
            # Clean the repository directory
            run_repo_clean(config)

            # Remind about updates at the end if available
            if update_available:
                upgrade_cmd = get_upgrade_command()
                logger.info("\nUpdate Available")
                logger.info(
                    f"A newer version (v{latest_version}) of Fetchtastic is available!"
                )
                logger.info(f"Run '{upgrade_cmd}' to upgrade.")
        else:
            # No repo subcommand provided
            repo_parser.print_help()
    elif args.command is None:
        # No command provided
        parser.print_help()
    else:
        parser.print_help()


def show_help(
    parser,
    repo_parser,
    repo_subparsers,
    help_command,
    help_subcommand,
    main_subparsers=None,
):
    """
    Show contextual CLI help for a specific command or subcommand.

    If no command is supplied, prints the general help. Handles the "repo" command specially:
    prints repo help and, if a repo subcommand is supplied, prints that subcommand's help or an
    available-subcommands listing. For other known top-level commands, prints that command's help.
    If an unknown command is requested, prints an error and lists available commands when possible.

    Parameters:
        help_command (str or None): The top-level command to show help for (e.g., "repo", "setup").
        help_subcommand (str or None): The subcommand to show help for (e.g., "browse", "clean").
        main_subparsers (argparse._SubParsersAction, optional): The main parser's subparsers used
            to detect available top-level commands and print their help when present.

    Notes:
        - `parser`, `repo_parser`, and `repo_subparsers` are the argument parser objects used to
          render help; their types are evident from usage and are not documented here.
    """
    if not help_command:
        # No specific command requested, show general help
        parser.print_help()
        return

    if help_command == "repo":
        # Show repo command help
        repo_parser.print_help()

        if help_subcommand:
            # Show specific repo subcommand help (derived from argparse choices)
            subparser = repo_subparsers.choices.get(help_subcommand)
            if subparser:
                print(f"\nRepo '{help_subcommand}' command help:")
                subparser.print_help()
            else:
                available = ", ".join(sorted(repo_subparsers.choices.keys()))
                print(f"\nUnknown repo subcommand: {help_subcommand}")
                print(f"Available repo subcommands: {available}")
        return
    # Handle other main commands
    elif main_subparsers and help_command in main_subparsers.choices:
        subparser = main_subparsers.choices[help_command]
        print(f"Help for '{help_command}' command:")
        subparser.print_help()
    else:
        # Unknown command
        print(f"Unknown command: {help_command}")
        if main_subparsers:
            available_commands = sorted(main_subparsers.choices.keys())
            print(f"Available commands: {', '.join(available_commands)}")
        print("\nFor general help, use: fetchtastic help")


def run_clean():
    """
    Permanently removes Fetchtastic configuration, downloaded files, and related system integrations.

    This is a destructive operation that:
    - Prompts the user for confirmation (defaults to "no") before proceeding.
    - Deletes current and legacy configuration files and attempts to remove the configuration directory (and a "batch" subdirectory) if empty.
    - On Windows, attempts to remove Start Menu shortcuts, a startup shortcut, and a configuration shortcut when Windows-specific modules are available.
    - Deletes all files, symlinks, and subdirectories inside the configured download/base directory.
    - On non-Windows systems, removes Fetchtastic-related crontab entries if present.
    - Removes a Termux boot script (~/.termux/boot/fetchtastic.sh) if present.
    - Removes the Fetchtastic log file in the user log directory.

    Side effects:
    - Irreversibly deletes files and may modify the user's crontab and Start Menu/startup items.
    - The function prints progress and error messages to stdout.

    Use with caution; run only when you intend to fully remove Fetchtastic data and integrations.
    """
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

    # Remove configuration files (both old and new locations)
    config_file = setup_config.CONFIG_FILE
    old_config_file = setup_config.OLD_CONFIG_FILE

    if os.path.exists(config_file):
        os.remove(config_file)
        print(f"Removed configuration file: {config_file}")

    if os.path.exists(old_config_file):
        os.remove(old_config_file)
        print(f"Removed old configuration file: {old_config_file}")

    # Remove config directory if empty
    config_dir = setup_config.CONFIG_DIR
    if os.path.exists(config_dir):
        # If on Windows, remove batch files directory
        batch_dir = os.path.join(config_dir, "batch")
        if os.path.exists(batch_dir):
            try:
                shutil.rmtree(batch_dir)
                print(f"Removed batch files directory: {batch_dir}")
            except Exception as e:
                print(f"Failed to delete batch directory {batch_dir}. Reason: {e}")

        # Check if config directory is now empty
        if os.path.exists(config_dir) and not os.listdir(config_dir):
            try:
                os.rmdir(config_dir)
                print(f"Removed empty config directory: {config_dir}")
            except Exception as e:
                print(f"Failed to delete config directory {config_dir}. Reason: {e}")

    # Windows-specific cleanup
    if platform.system() == "Windows":
        # Check if Windows modules are available
        windows_modules_available = False
        try:
            import winshell

            windows_modules_available = True
        except ImportError:
            print(
                "Windows modules not available. Some Windows-specific items may not be removed."
            )

        if windows_modules_available:
            # Remove Start Menu shortcuts
            windows_start_menu_folder = setup_config.WINDOWS_START_MENU_FOLDER
            if os.path.exists(windows_start_menu_folder):
                try:
                    shutil.rmtree(windows_start_menu_folder)
                    print(
                        f"Removed Start Menu shortcuts folder: {windows_start_menu_folder}"
                    )
                except Exception as e:
                    print(f"Failed to remove Start Menu shortcuts folder. Reason: {e}")
                    # Try to remove individual files
                    try:
                        for item in os.listdir(windows_start_menu_folder):
                            item_path = os.path.join(windows_start_menu_folder, item)
                            try:
                                if os.path.isfile(item_path):
                                    os.remove(item_path)
                                    print(f"Removed Start Menu shortcut: {item}")
                                elif os.path.isdir(item_path):
                                    shutil.rmtree(item_path)
                                    print(f"Removed Start Menu directory: {item}")
                            except Exception as e2:
                                print(f"Failed to remove {item}. Reason: {e2}")
                    except Exception as e3:
                        print(f"Failed to list Start Menu shortcuts. Reason: {e3}")

            # Remove startup shortcut
            try:
                startup_folder = winshell.startup()
                startup_shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")
                if os.path.exists(startup_shortcut_path):
                    os.remove(startup_shortcut_path)
                    print(f"Removed startup shortcut: {startup_shortcut_path}")
            except Exception as e:
                print(f"Failed to remove startup shortcut. Reason: {e}")

            # Remove config shortcut in base directory
            download_dir = setup_config.BASE_DIR
            config_shortcut_path = os.path.join(download_dir, "fetchtastic_yaml.lnk")
            if os.path.exists(config_shortcut_path):
                try:
                    os.remove(config_shortcut_path)
                    print(f"Removed configuration shortcut: {config_shortcut_path}")
                except Exception as e:
                    print(f"Failed to remove configuration shortcut. Reason: {e}")

    # Remove contents of download directory
    download_dir = setup_config.BASE_DIR
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

    # Remove cron job entries (non-Windows platforms)
    if platform.system() != "Windows":
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
                # Remove existing fetchtastic cron jobs
                cron_lines = [
                    line for line in existing_cron.splitlines() if line.strip()
                ]
                cron_lines = [
                    line
                    for line in cron_lines
                    if "# fetchtastic" not in line
                    and "fetchtastic download" not in line
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

    # Remove boot script if exists (Termux-specific)
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print(f"Removed boot script: {boot_script}")

    # Remove log file
    log_dir = platformdirs.user_log_dir("fetchtastic")
    log_file = os.path.join(log_dir, "fetchtastic.log")
    if os.path.exists(log_file):
        try:
            os.remove(log_file)
            print(f"Removed log file: {log_file}")
        except Exception as e:
            print(f"Failed to remove log file. Reason: {e}")

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
