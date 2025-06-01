# src/fetchtastic/cli.py

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path # Added for add_file_logging

import platformdirs

from fetchtastic import downloader, repo_downloader, setup_config
# Removed log_error, log_info, setup_logging
from fetchtastic.log_utils import logger, add_file_logging, set_log_level # Import new logger utils
from fetchtastic.setup_config import display_version_info, get_upgrade_command


def main():
    # Logging is initialized by log_utils on import for console.
    # File logging and level adjustment will be done after arg parsing.

    parser = argparse.ArgumentParser(
        description="Fetchtastic - Meshtastic Firmware and APK Downloader"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase logging verbosity (e.g., -v for INFO, -vv for DEBUG)"
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

    # Configure logging level based on verbosity
    if args.verbose == 1:
        set_log_level("INFO") # Already default, but explicit if -v is given
    elif args.verbose >= 2:
        set_log_level("DEBUG")

    # Attempt to load config to get download_dir for file logging
    # This is a bit of a chicken-and-egg if setup hasn't run, but okay for now.
    # If config doesn't exist or DOWNLOAD_DIR isn't set, file logging won't be added here.
    temp_config_for_log = setup_config.load_config()
    if temp_config_for_log and temp_config_for_log.get("DOWNLOAD_DIR"):
        log_file_dir = Path(temp_config_for_log["DOWNLOAD_DIR"]) / "logs"
        add_file_logging(log_file_dir)
    else:
        logger.info("File logging not configured yet (config or DOWNLOAD_DIR not found).")


    if args.command == "setup":
        # Display version information
        current_version, latest_version, update_available = display_version_info() # Uses logger internally now

        # Run the setup process
        setup_config.run_setup() # Uses logger internally now

        # Remind about updates at the end if available
        if update_available and latest_version: # latest_version check added for safety
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
                    logger.info("Configuration successfully migrated to the new location.")
                    # Update config_path to the new location
                    config_path = setup_config.CONFIG_FILE
                    # Re-load the configuration from the new location
                    # config = setup_config.load_config(config_path) # This line was here, but config is not used after this.
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
            logger.info(f"Current NTFY topic URL: {full_url}") # Was print
            logger.info(f"Topic name: {ntfy_topic}") # Was print

            if setup_config.is_termux():
                copy_prompt_text = "Do you want to copy the topic name to the clipboard? [y/n] (default: yes): "
                text_to_copy = ntfy_topic
            else:
                copy_prompt_text = "Do you want to copy the topic URL to the clipboard? [y/n] (default: yes): "
                text_to_copy = full_url

            copy_to_clipboard_input = input(copy_prompt_text).strip().lower() or "y" # Renamed variable
            if copy_to_clipboard_input == "y":
                success = copy_to_clipboard_func(text_to_copy)
                if success:
                    if setup_config.is_termux():
                        logger.info("Topic name copied to clipboard.") # Was print
                    else:
                        logger.info("Topic URL copied to clipboard.") # Was print
                else:
                    logger.error("Failed to copy to clipboard.") # Was print
            else:
                logger.info("You can copy the topic information from above.") # Was print
        else:
            logger.warning( # Was print, changed to warning
                "Notifications are not set up. Run 'fetchtastic setup' to configure notifications."
            )
    elif args.command == "clean":
        # Run the clean process
        run_clean()
    elif args.command == "version":
        # Get version information
        current_version, latest_version, update_available = display_version_info() # Uses logger

        # Log version information (already done by display_version_info if using logger)
        # logger.info(f"Fetchtastic v{current_version}") # Redundant if display_version_info logs
        if update_available and latest_version: # latest_version check added for safety
            upgrade_cmd = get_upgrade_command()
            logger.info(f"A newer version (v{latest_version}) is available!") # display_version_info should handle this
            logger.info(f"Run '{upgrade_cmd}' to upgrade.") # display_version_info should handle this
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
                    if repo_subcommand == "browse":
                        # Find the browse subparser and print its help
                        for action in repo_subparsers._actions:
                            if isinstance(action, argparse._SubParsersAction):
                                browse_parser = action.choices.get("browse")
                                if browse_parser:
                                    logger.info("\nRepo browse command help:") # Was print
                                    browse_parser.print_help()
                                break
                    elif repo_subcommand == "clean":
                        # Find the clean subparser and print its help
                        for action in repo_subparsers._actions:
                            if isinstance(action, argparse._SubParsersAction):
                                clean_parser = action.choices.get("clean")
                                if clean_parser:
                                    logger.info("\nRepo clean command help:") # Was print
                                    clean_parser.print_help()
                                break
            else:
                # Show general help
                parser.print_help()
        else:
            # No subcommand specified, show general help
            parser.print_help()
    elif args.command == "repo":
        # Display version information
        current_version, latest_version, update_available = display_version_info() # Uses logger

        # Handle repo subcommands
        exists, _ = setup_config.config_exists()
        if not exists:
            logger.info("No configuration found. Running setup.") # Was print
            setup_config.run_setup()

        config = setup_config.load_config()
        if not config:
            logger.error("Configuration not found. Please run 'fetchtastic setup' first.") # Was print
            return

        if args.repo_command == "browse":
            # Run the repository downloader
            repo_downloader.main(config) # Assumes repo_downloader.main uses logger

            # Remind about updates at the end if available
            if update_available and latest_version: # latest_version check
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
            if update_available and latest_version: # latest_version check
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


def copy_to_clipboard_func(text):
    if setup_config.is_termux():
        # Termux environment
        try:
            subprocess.run(
                ["termux-clipboard-set"], input=text.encode("utf-8"), check=True
            )
            return True
        except Exception as e:
            logger.error(f"An error occurred while copying to clipboard: {e}", exc_info=True) # Was print
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
                    logger.warning( # Was print
                        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
                    )
                    return False
            else:
                logger.warning("Clipboard functionality is not supported on this platform.") # Was print
                return False
        except Exception as e:
            logger.error(f"An error occurred while copying to clipboard: {e}", exc_info=True) # Was print
            return False


def run_clean():
    logger.info( # Was print
        "This will remove Fetchtastic configuration files, downloaded files, and cron job entries."
    )
    confirm = (
        input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower()
        or "n"
    )
    if confirm != "y":
        logger.info("Clean operation cancelled.") # Was print
        return

    # Remove configuration files (both old and new locations)
    config_file = setup_config.CONFIG_FILE
    old_config_file = setup_config.OLD_CONFIG_FILE

    if os.path.exists(config_file):
        os.remove(config_file)
        logger.info(f"Removed configuration file: {config_file}") # Was print

    if os.path.exists(old_config_file):
        os.remove(old_config_file)
        logger.info(f"Removed old configuration file: {old_config_file}") # Was print

    # Remove config directory if empty
    config_dir = setup_config.CONFIG_DIR
    if os.path.exists(config_dir):
        # If on Windows, remove batch files directory
        batch_dir = os.path.join(config_dir, "batch")
        if os.path.exists(batch_dir):
            try:
                shutil.rmtree(batch_dir)
                logger.info(f"Removed batch files directory: {batch_dir}") # Was print
            except Exception as e:
                logger.error(f"Failed to delete batch directory {batch_dir}. Reason: {e}", exc_info=True) # Was print

        # Check if config directory is now empty
        if os.path.exists(config_dir) and not os.listdir(config_dir):
            try:
                os.rmdir(config_dir)
                logger.info(f"Removed empty config directory: {config_dir}") # Was print
            except Exception as e:
                logger.error(f"Failed to delete config directory {config_dir}. Reason: {e}", exc_info=True) # Was print

    # Windows-specific cleanup
    if platform.system() == "Windows":
        # Check if Windows modules are available
        windows_modules_available = False
        try:
            import winshell

            windows_modules_available = True
        except ImportError:
            logger.warning( # Was print
                "Windows modules not available. Some Windows-specific items may not be removed."
            )

        if windows_modules_available:
            # Remove Start Menu shortcuts
            windows_start_menu_folder = setup_config.WINDOWS_START_MENU_FOLDER
            if os.path.exists(windows_start_menu_folder):
                try:
                    shutil.rmtree(windows_start_menu_folder)
                    logger.info( # Was print
                        f"Removed Start Menu shortcuts folder: {windows_start_menu_folder}"
                    )
                except Exception as e:
                    logger.error(f"Failed to remove Start Menu shortcuts folder. Reason: {e}", exc_info=True) # Was print
                    # Try to remove individual files
                    try:
                        for item in os.listdir(windows_start_menu_folder):
                            item_path = os.path.join(windows_start_menu_folder, item)
                            try:
                                if os.path.isfile(item_path):
                                    os.remove(item_path)
                                    logger.info(f"Removed Start Menu shortcut: {item}") # Was print
                                elif os.path.isdir(item_path):
                                    shutil.rmtree(item_path)
                                    logger.info(f"Removed Start Menu directory: {item}") # Was print
                            except Exception as e2:
                                logger.error(f"Failed to remove {item}. Reason: {e2}", exc_info=True) # Was print
                    except Exception as e3:
                        logger.error(f"Failed to list Start Menu shortcuts. Reason: {e3}", exc_info=True) # Was print

            # Remove startup shortcut
            try:
                startup_folder = winshell.startup()
                startup_shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")
                if os.path.exists(startup_shortcut_path):
                    os.remove(startup_shortcut_path)
                    logger.info(f"Removed startup shortcut: {startup_shortcut_path}") # Was print
            except Exception as e:
                logger.error(f"Failed to remove startup shortcut. Reason: {e}", exc_info=True) # Was print

            # Remove config shortcut in base directory
            download_dir_base = setup_config.BASE_DIR # Renamed to avoid conflict
            config_shortcut_path = os.path.join(download_dir_base, "fetchtastic_yaml.lnk")
            if os.path.exists(config_shortcut_path):
                try:
                    os.remove(config_shortcut_path)
                    logger.info(f"Removed configuration shortcut: {config_shortcut_path}") # Was print
                except Exception as e:
                    logger.error(f"Failed to remove configuration shortcut. Reason: {e}", exc_info=True) # Was print

    # Remove contents of download directory
    download_dir_base_content = setup_config.BASE_DIR # Renamed to avoid conflict
    if os.path.exists(download_dir_base_content):
        for item in os.listdir(download_dir_base_content):
            item_path = os.path.join(download_dir_base_content, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.remove(item_path)
                    logger.info(f"Removed file: {item_path}") # Was print
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    logger.info(f"Removed directory: {item_path}") # Was print
            except Exception as e:
                logger.error(f"Failed to delete {item_path}. Reason: {e}", exc_info=True) # Was print
        logger.info(f"Cleaned contents of download directory: {download_dir_base_content}") # Was print

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
                logger.info("Removed Fetchtastic cron job entries.") # Was print
        except Exception as e:
            logger.error(f"An error occurred while removing cron jobs: {e}", exc_info=True) # Was print

    # Remove boot script if exists (Termux-specific)
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        logger.info(f"Removed boot script: {boot_script}") # Was print

    # Remove log file
    log_dir = platformdirs.user_log_dir("fetchtastic")
    log_file = os.path.join(log_dir, "fetchtastic.log")
    if os.path.exists(log_file):
        try:
            os.remove(log_file)
            logger.info(f"Removed log file: {log_file}") # Was print
        except Exception as e:
            logger.error(f"Failed to remove log file. Reason: {e}", exc_info=True) # Was print

    logger.info( # Was print
        "The downloaded files and Fetchtastic configuration have been removed from your system."
    )


def run_repo_clean(config):
    """
    Cleans the repository download directory.
    """
    logger.info( # Was print
        "This will remove all files downloaded from the meshtastic.github.io repository."
    )
    confirm = (
        input("Are you sure you want to proceed? [y/n] (default: no): ").strip().lower()
        or "n"
    )
    if confirm != "y":
        logger.info("Clean operation cancelled.") # Was print
        return

    # Clean the repo directory
    download_dir = config.get("DOWNLOAD_DIR")
    if not download_dir:
        logger.error("Download directory not configured.") # Was print
        return

    success = repo_downloader.clean_repo_directory(download_dir) # Assumes this func uses logger or returns status
    if success:
        logger.info("Repository directory cleaned successfully.") # Was print
    else:
        logger.error("Failed to clean repository directory.") # Was print


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
