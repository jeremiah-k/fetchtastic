# src/fetchtastic/cli.py

import argparse
import os
import platform
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import platformdirs
import yaml

from fetchtastic import log_utils, setup_config
from fetchtastic.constants import (
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    MANAGED_DIRECTORIES,
    MANAGED_FILES,
    MSG_CLEANED_MANAGED_DIRS,
    MSG_FAILED_DELETE_MANAGED_DIR,
    MSG_FAILED_DELETE_MANAGED_FILE,
    MSG_PRESERVE_OTHER_FILES,
    MSG_REMOVED_MANAGED_DIR,
    MSG_REMOVED_MANAGED_FILE,
    REPO_DOWNLOADS_DIR,
    WINDOWS_SHORTCUT_FILE,
)
from fetchtastic.download import cli_integration as download_cli_integration
from fetchtastic.download.repository import RepositoryDownloader
from fetchtastic.utils import get_api_request_summary as _get_api_request_summary
from fetchtastic.utils import (
    reset_api_tracking,
)

get_api_request_summary = _get_api_request_summary

# Patch-friendly aliases for CLI tests.
copy_to_clipboard_func = setup_config.copy_to_clipboard_func


def display_version_info():
    """
    Return version information for the installed Fetchtastic package and the latest available release.

    Returns:
        Tuple[str | None, str | None, bool]: (current_version, latest_version, update_available)
    """
    return setup_config.display_version_info()


def get_upgrade_command():
    """
    Get the platform-appropriate shell command that performs an upgrade of Fetchtastic.

    Returns:
        upgrade_command (str): A command string suitable for display or execution to upgrade Fetchtastic on the current platform.
    """
    return setup_config.get_upgrade_command()


def _display_update_reminder(latest_version: str) -> None:
    """
    Announces that a newer Fetchtastic version is available to the user.

    Parameters:
        latest_version (str): The latest available version string (for example, "1.2.3").
    """
    upgrade_cmd = get_upgrade_command()
    log_utils.logger.info("\nUpdate Available")
    log_utils.logger.info(
        f"A newer version (v{latest_version}) of Fetchtastic is available!"
    )
    log_utils.logger.info(f"Run '{upgrade_cmd}' to upgrade.")


def _load_and_prepare_config() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Load the Fetchtastic configuration file, migrating it from the old location if detected.

    Attempts to load the current configuration; if a config exists in the legacy location and no new config file is present,
    a migration is attempted before loading.

    Returns:
        tuple: (config, config_path)
            config (dict[str, Any] | None): Loaded configuration mapping, or None if loading failed or no configuration exists.
            config_path (str | None): Filesystem path to the configuration file used, or None if no configuration was found.
    """
    exists, config_path = setup_config.config_exists()
    if exists and config_path == setup_config.OLD_CONFIG_FILE:
        # Check if config is in old location and needs migration
        if not os.path.exists(setup_config.CONFIG_FILE):
            separator = "=" * 80
            log_utils.logger.info(f"\n{separator}")
            log_utils.logger.info("Configuration Migration")
            log_utils.logger.info(separator)
            # Automatically migrate without prompting
            setup_config.prompt_for_migration()  # Just logs the migration message
            if setup_config.migrate_config():
                log_utils.logger.info(
                    "Configuration successfully migrated to new location."
                )
                config_path = setup_config.CONFIG_FILE
            else:
                log_utils.logger.error(
                    "Failed to migrate configuration. Continuing with old location."
                )
            log_utils.logger.info(f"{separator}\n")

    if exists:
        try:
            config = setup_config.load_config()
        except (ValueError, OSError, TypeError, yaml.YAMLError) as error:
            log_utils.logger.error(f"Failed to load configuration: {error}")
            config = None
    else:
        config = None
        config_path = None

    return config, config_path


def _ensure_config_loaded() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Ensure a valid configuration is available, running setup if none is found.

    Returns:
        Tuple[Optional[Dict[str, Any]], Optional[str]]: (config, config_path) where `config` is the loaded
        configuration dictionary or `None`, and `config_path` is the path to the configuration file or
        `None`. Returns `(None, None)` if setup does not produce a valid configuration.
    """
    config, config_path = _load_and_prepare_config()
    if config_path is None:
        if not sys.stdin.isatty():
            log_utils.logger.error(
                "No configuration found. Please run 'fetchtastic setup' in an interactive session to create one."
            )
            return None, None
        log_utils.logger.info("No configuration found. Running setup.")
        setup_config.run_setup(perform_initial_download=False)
        config, config_path = _load_and_prepare_config()
        if config_path is None:
            log_utils.logger.error("Setup did not create a valid configuration.")
            return None, None

    return config, config_path


def _prepare_command_run() -> Tuple[
    Optional[Dict[str, Any]],
    Optional[download_cli_integration.DownloadCLIIntegration],
]:
    """
    Ensure a valid configuration is loaded and create a DownloadCLIIntegration instance.

    If the loaded configuration contains a non-empty `LOG_LEVEL`, apply it before creating the integration.

    Returns:
        tuple: (`config`, `integration`)
            `config` (dict[str, Any] | None): The loaded configuration mapping, or `None` if no configuration is available.
            `integration` (download_cli_integration.DownloadCLIIntegration | None): The created integration instance, or `None` if configuration loading failed.
    """
    config, config_path = _ensure_config_loaded()
    if config_path is None:
        return None, None
    if config is None:
        log_utils.logger.error("Configuration file exists but could not be loaded.")
        return None, None

    # Apply configured log level if present and not empty
    if config and config.get("LOG_LEVEL"):
        log_utils.set_log_level(config["LOG_LEVEL"])

    integration = download_cli_integration.DownloadCLIIntegration()
    return config, integration


def _perform_cache_update(
    integration: download_cli_integration.DownloadCLIIntegration,
    config: Dict[str, Any],
) -> bool:
    """
    Attempt to update integration caches and log the outcome.

    Parameters:
        config (Dict[str, Any]): Loaded configuration.

    Returns:
        bool: `True` if the cache update succeeded, `False` otherwise.
    """
    success = integration.update_cache(config=config)
    if success:
        log_utils.logger.info("Caches cleared.")
    else:
        log_utils.logger.error("Failed to clear caches.")
    return success


def _handle_download_subcommand(
    args: argparse.Namespace,
    integration: download_cli_integration.DownloadCLIIntegration,
    config: Dict[str, Any],
) -> None:
    """
    Perform either a cache update or a download run based on the parsed command-line arguments.

    If args.update_cache is true, triggers a cache update via the provided integration and returns. Otherwise, invokes the integration to perform downloads (using args.force_download to control refresh), measures elapsed time, and logs a download results summary.

    Parameters:
        args: Parsed command-line namespace expected to contain at least `update_cache` and `force_download` flags.
        integration: DownloadCLIIntegration instance used to run cache updates or perform downloads and to log results.
        config: Configuration mapping passed to the integration for the operation.
    """
    if args.update_cache:
        _perform_cache_update(integration, config)
        return

    start_time = time.time()
    (
        downloaded_firmwares,
        new_firmware_versions,
        downloaded_apks,
        new_apk_versions,
        failed_downloads,
        latest_firmware_version,
        latest_apk_version,
    ) = integration.main(config=config, force_refresh=args.force_download)

    elapsed = time.time() - start_time
    integration.log_download_results_summary(
        logger_override=log_utils.logger,
        elapsed_seconds=elapsed,
        downloaded_firmwares=downloaded_firmwares,
        downloaded_apks=downloaded_apks,
        failed_downloads=failed_downloads,
        latest_firmware_version=latest_firmware_version,
        latest_apk_version=latest_apk_version,
        new_firmware_versions=new_firmware_versions,
        new_apk_versions=new_apk_versions,
    )


def main():
    # Logging is automatically initialized by importing log_utils

    """
    Entry point for the Fetchtastic command-line interface.

    Parses command-line arguments and dispatches subcommands: setup, download, topic,
    clean, version, repo, and help. Subcommands may read, create, migrate, or remove
    configuration; run interactive setup flows; invoke download or repository
    operations; modify system startup/cron entries; and copy text to the clipboard.
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
    download_parser = subparsers.add_parser(
        "download", help="Download firmware and APKs"
    )
    download_mode_group = download_parser.add_mutually_exclusive_group()
    download_mode_group.add_argument(
        "--force-download",
        "-f",
        dest="force_download",
        action="store_true",
        help="Force refresh by bypassing cache and rechecking all downloads",
    )
    download_mode_group.add_argument(
        "--update-cache",
        action="store_true",
        help="Clear cached data and exit without running downloads",
    )

    # Command to display NTFY topic
    subparsers.add_parser("topic", help="Display the current NTFY topic")

    # Command to manage caches
    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage cached data",
        description="Clear or refresh cached API data without downloading assets.",
    )
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_subparsers.add_parser(
        "update",
        help="Clear cached data and exit",
        description="Clear cached API data and exit without running downloads.",
    )

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
        description=(
            "Browse directories in the meshtastic.github.io repository, select files, "
            f"and download them to the {REPO_DOWNLOADS_DIR} directory."
        ),
    )

    # Repo clean command
    repo_subparsers.add_parser(
        "clean",
        help="Clean the repository download directory",
        description=(
            "Remove all files and directories from the repository download directory "
            f"({FIRMWARE_DIR_NAME}/{REPO_DOWNLOADS_DIR})."
        ),
    )

    args = parser.parse_args()

    if args.command == "setup":
        # Display version information
        current_version, latest_version, update_available = display_version_info()

        # Check if this is just an integrations update
        if hasattr(args, "update_integrations") and args.update_integrations:
            # Only update Windows integrations
            if platform.system() == "Windows":
                log_utils.logger.info("Updating Windows integrations...")
                config = setup_config.load_config()
                if config:
                    success = setup_config.create_windows_menu_shortcuts(
                        setup_config.CONFIG_FILE,
                        config.get("BASE_DIR", setup_config.BASE_DIR),
                    )
                    if success:
                        log_utils.logger.info(
                            "Windows integrations updated successfully!"
                        )
                    else:
                        log_utils.logger.error("Failed to update Windows integrations.")
                else:
                    log_utils.logger.error(
                        "No configuration found. Run 'fetchtastic setup' first."
                    )
            else:
                log_utils.logger.info(
                    "Integration updates are only available on Windows."
                )
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
            if update_available and latest_version:
                _display_update_reminder(latest_version)
    elif args.command == "download":
        config, integration = _prepare_command_run()
        if integration is None or config is None:
            sys.exit(1)

        # Run the downloader
        reset_api_tracking()
        _handle_download_subcommand(args, integration, config)
    elif args.command == "cache":
        config, integration = _prepare_command_run()
        if integration is None or config is None:
            sys.exit(1)
        _perform_cache_update(integration, config)
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
                    print("Failed to copy to clipboard.", file=sys.stderr)
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
        log_utils.logger.info(f"Fetchtastic v{current_version}")
        if update_available and latest_version:
            upgrade_cmd = get_upgrade_command()
            log_utils.logger.info(f"A newer version (v{latest_version}) is available!")
            log_utils.logger.info(f"Run '{upgrade_cmd}' to upgrade.")
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
            setup_config.run_setup(perform_initial_download=False)

        config = setup_config.load_config()
        if not config:
            log_utils.logger.error(
                "Configuration not found. Please run 'fetchtastic setup' first."
            )
            return

        if args.repo_command == "browse":
            # Run the repository downloader using the new menu integration
            from fetchtastic.menu_repo import run_repository_downloader_menu

            run_repository_downloader_menu(config)

            # Remind about updates at the end if available
            if update_available and latest_version:
                _display_update_reminder(latest_version)
        elif args.repo_command == "clean":
            # Clean the repository directory
            run_repo_clean(config)

            # Remind about updates at the end if available
            if update_available and latest_version:
                _display_update_reminder(latest_version)
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
    Permanently remove Fetchtastic configuration, Fetchtastic-managed downloads, platform integrations, and logs after explicit interactive confirmation.

    This operation deletes current and legacy configuration files, only Fetchtastic-managed files and directories inside the configured download directory, platform-specific integrations (for example, Windows Start Menu and startup shortcuts, non-Windows cron entries, and a Termux boot script), and the Fetchtastic log file. The removal is irreversible and requires the user to confirm interactively; non-managed files are preserved.
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

    def _try_remove(path: str, *, is_dir: bool = False, description: str) -> None:
        """
        Attempt to remove a filesystem path and report the outcome.

        If the given path does not exist, the function does nothing. If removal succeeds, a confirmation message is printed to stdout; if it fails, an error message with the failure reason is printed to stderr.

        Parameters:
            path (str): Path to the file or directory to remove.
            is_dir (bool): If true, treat `path` as a directory and remove it recursively; otherwise remove it as a file.
            description (str): Human-readable name for the item being removed used in status messages.
        """
        if not os.path.exists(path):
            return
        try:
            if is_dir:
                shutil.rmtree(path)
                print(f"Removed {description}: {path}")
            else:
                os.remove(path)
                print(f"Removed {description}: {path}")
        except OSError as e:
            print(
                f"Failed to delete {description} {path}. Reason: {e}",
                file=sys.stderr,
            )

    _try_remove(config_file, description="configuration file")
    _try_remove(old_config_file, description="old configuration file")

    # Remove config directory if empty
    config_dir = setup_config.CONFIG_DIR
    if os.path.exists(config_dir):
        # If on Windows, remove batch files directory
        batch_dir = os.path.join(config_dir, "batch")
        _try_remove(batch_dir, is_dir=True, description="batch files directory")

        # Check if config directory is now empty
        try:
            with os.scandir(config_dir) as it:
                is_empty = not any(it)
            if is_empty:
                _try_remove(
                    config_dir, is_dir=True, description="empty config directory"
                )
        except FileNotFoundError:
            pass

    # Windows-specific cleanup
    if platform.system() == "Windows":
        # Check if Windows modules are available
        windows_modules_available = False
        try:
            import winshell  # type: ignore[import]

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
                except OSError as e:
                    print(
                        f"Failed to remove Start Menu shortcuts folder. Reason: {e}",
                        file=sys.stderr,
                    )
                    # Try to remove individual files
                    try:
                        with os.scandir(windows_start_menu_folder) as it:
                            for entry in it:
                                try:
                                    if entry.is_file():
                                        os.remove(entry.path)
                                        print(
                                            f"Removed Start Menu shortcut: {entry.name}"
                                        )
                                    elif entry.is_dir():
                                        shutil.rmtree(entry.path)
                                        print(
                                            f"Removed Start Menu directory: {entry.name}"
                                        )
                                except OSError as e2:
                                    print(
                                        f"Failed to remove {entry.name}. Reason: {e2}",
                                        file=sys.stderr,
                                    )
                    except OSError as e3:
                        print(
                            f"Failed to list Start Menu shortcuts. Reason: {e3}",
                            file=sys.stderr,
                        )

            # Remove startup shortcut
            try:
                startup_folder = winshell.startup()  # type: ignore[name-defined]
                startup_shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")
                if os.path.exists(startup_shortcut_path):
                    os.remove(startup_shortcut_path)
                    print(f"Removed startup shortcut: {startup_shortcut_path}")
            except (OSError, AttributeError) as e:
                print(
                    f"Failed to remove startup shortcut. Reason: {e}", file=sys.stderr
                )

            # Remove config shortcut in base directory
            download_dir = setup_config.BASE_DIR
            config_shortcut_path = os.path.join(download_dir, WINDOWS_SHORTCUT_FILE)
            if os.path.exists(config_shortcut_path):
                try:
                    os.remove(config_shortcut_path)
                    print(f"Removed configuration shortcut: {config_shortcut_path}")
                except OSError as e:
                    print(
                        f"Failed to remove configuration shortcut. Reason: {e}",
                        file=sys.stderr,
                    )

    # Remove only managed directories from download directory
    download_dir = setup_config.BASE_DIR

    def _remove_managed_file(item_path: str) -> None:
        """
        Removes a managed file at the given filesystem path and logs the outcome.

        If removal succeeds, logs an informational message. If removal fails, logs an error and does not raise an exception.

        Parameters:
            item_path (str): Path of the file to remove.
        """
        try:
            os.remove(item_path)
            log_utils.logger.info(MSG_REMOVED_MANAGED_FILE.format(path=item_path))
        except OSError as e:
            log_utils.logger.error(
                MSG_FAILED_DELETE_MANAGED_FILE.format(path=item_path, error=e)
            )

    try:
        with os.scandir(download_dir) as it:
            for entry in it:
                # Check if this is a managed directory or file
                is_managed_dir = (
                    entry.name in MANAGED_DIRECTORIES
                    or entry.name.startswith(FIRMWARE_DIR_PREFIX)
                )
                is_managed_file = entry.name in MANAGED_FILES

                # First, handle symlinks to avoid traversal and ensure they are removed if managed.
                if entry.is_symlink():
                    if is_managed_dir or is_managed_file:
                        _remove_managed_file(entry.path)
                    continue

                # Handle actual directories
                if is_managed_dir and entry.is_dir():
                    try:
                        shutil.rmtree(entry.path)
                        log_utils.logger.info(
                            MSG_REMOVED_MANAGED_DIR.format(path=entry.path)
                        )
                    except OSError as e:
                        log_utils.logger.error(
                            MSG_FAILED_DELETE_MANAGED_DIR.format(
                                path=entry.path, error=e
                            )
                        )
                # Handle actual files
                elif is_managed_file and entry.is_file():
                    _remove_managed_file(entry.path)
    except FileNotFoundError:
        pass

    log_utils.logger.info(MSG_CLEANED_MANAGED_DIRS.format(path=download_dir))
    log_utils.logger.info(MSG_PRESERVE_OTHER_FILES)

    # Remove cron job entries (non-Windows platforms)
    if platform.system() != "Windows":
        setup_config.remove_cron_job()  # type: ignore[call-arg]
        setup_config.remove_reboot_cron_job()  # type: ignore[call-arg]

    # Remove boot script if exists (Termux-specific)
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    _try_remove(boot_script, description="boot script")

    # Remove log file
    log_dir = platformdirs.user_log_dir("fetchtastic")
    log_file = os.path.join(log_dir, "fetchtastic.log")
    _try_remove(log_file, description="log file")

    print(
        "The downloaded files and Fetchtastic configuration have been removed from your system."
    )


def run_repo_clean(config):
    """
    Prompt for confirmation and, if confirmed, remove downloaded files from the meshtastic.github.io repository for the given configuration.

    Prompts the user before proceeding; if the user confirms, invokes RepositoryDownloader to remove repository files, prints success or failure and a summary of removed files and directories, and logs the cleanup summary and any errors. Cleanup errors are written to stderr and recorded in the logger.

    Parameters:
        config: Configuration object used to locate the repository download directory and associated metadata.
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

    # Clean the repo directory using the new downloader
    repo_downloader = RepositoryDownloader(config)
    success = repo_downloader.clean_repository_directory()
    if success:
        print("Repository directory cleaned successfully.")
    else:
        print("Failed to clean repository directory.", file=sys.stderr)

    cleanup_summary = repo_downloader.get_cleanup_summary()
    summary_msg = (
        f"Repository cleanup summary: {cleanup_summary.get('removed_files', 0)} file(s), "
        f"{cleanup_summary.get('removed_dirs', 0)} dir(s) removed"
    )
    print(summary_msg)
    log_utils.logger.info(
        "Repository cleanup summary: %d file(s), %d dir(s) removed",
        cleanup_summary.get("removed_files", 0),
        cleanup_summary.get("removed_dirs", 0),
    )
    if cleanup_summary.get("errors"):
        for err in cleanup_summary.get("errors", []):
            print(f"Cleanup error: {err}", file=sys.stderr)
            log_utils.logger.warning(f"Repository cleanup error: {err}")


def get_fetchtastic_version():
    """
    Retrieve the installed Fetchtastic package version.

    Returns:
        version (str): The installed Fetchtastic version string, or "unknown" if the version cannot be determined.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        # For Python < 3.8
        from importlib_metadata import (  # type: ignore[import,no-redef]
            PackageNotFoundError,
            version,
        )
    try:
        return version("fetchtastic")
    except PackageNotFoundError:
        return "unknown"


if __name__ == "__main__":
    main()
