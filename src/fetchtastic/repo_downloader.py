# src/fetchtastic/repo_downloader.py

import os
import platform
import shutil
import time

import requests

from fetchtastic.utils import download_file_with_retry
# We will use the log_message_func passed into download_repo_files


def download_repo_files(selected_files, download_dir, log_message_func=None):
    """
    Downloads selected files from the meshtastic.github.io repository.

    Args:
        selected_files: Dictionary containing directory and files information
        download_dir: Base download directory
        log_message_func: Function to log messages (optional)

    Returns:
        List of downloaded file paths
    """
    if log_message_func is None:

        def log_message_func(message):
            print(message)

    if (
        not selected_files
        or "directory" not in selected_files
        or "files" not in selected_files
    ):
        log_message_func("No files selected for download.")
        return []

    directory = selected_files["directory"]
    files = selected_files["files"]

    # Create repo-dls directory if it doesn't exist
    repo_dir = os.path.join(download_dir, "firmware", "repo-dls")
    try:
        if not os.path.exists(repo_dir):
            os.makedirs(repo_dir)

        # Create directory structure matching the repository path
        if directory:
            dir_path = os.path.join(repo_dir, directory)
        else:
            dir_path = repo_dir

        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
    except OSError as e:
        log_message_func(f"Error creating base directories for repo downloads ({repo_dir} or {dir_path}): {e}")
        return [] # Cannot proceed if base directories can't be created

    downloaded_files = []

    for file_item in files: # Renamed to avoid conflict with built-in 'file'
        try:
            file_name = file_item["name"] # Potential KeyError
            download_url = file_item["download_url"] # Potential KeyError
            file_path = os.path.join(dir_path, file_name)
            # temp_path is now internal to download_file_with_retry

            # Ensure log_message_func is correctly set up as it's passed to the utility
            current_log_func = log_message_func
            if current_log_func is None: # Should match the existing default setup in the function
                def default_logger_for_util(message_text: str) -> None: # Use a different name to avoid scope clash
                    print(message_text)
                current_log_func = default_logger_for_util

            if download_file_with_retry(download_url, file_path, current_log_func):
                log_message_func(f"Successfully processed {file_name}") # General success, specific logs in util
                # Set executable permissions for .sh files (moved here, after successful download)
                if file_name.endswith(".sh"):
                    try:
                        os.chmod(file_path, 0o755)
                        current_log_func(f"Set executable permissions for {file_name}")
                    except OSError as e_chmod: # Specific for os.chmod
                        current_log_func(f"Error setting permissions for {file_name}: {e_chmod}")
                downloaded_files.append(file_path)
            else:
                # download_file_with_retry now logs its own detailed errors.
                current_log_func(f"Failed to download or validate {file_name} from {download_url}.")
                # Temp file cleanup is handled by download_file_with_retry on its failures.

        except (KeyError, TypeError) as e_file_data:
            # Error accessing file data like name or download_url
            malformed_file_info = str(file_item)[:100] # Log part of the problematic item
            log_message_func(f"Malformed file data encountered: {malformed_file_info}. Error: {e_file_data}. Skipping this item.")
        except Exception as e_loop: # Catch-all for other unexpected errors in this iteration of the loop
            # This should be rare if download_file_with_retry handles its part and data is fine.
            file_name_for_log = file_item.get("name", "unknown_file") if isinstance(file_item, dict) else "unknown_file"
            log_message_func(f"Unexpected error processing file '{file_name_for_log}' in download loop: {e_loop}")

    return downloaded_files


def clean_repo_directory(download_dir, log_message_func=None):
    """
    Cleans the repo directory by removing all files and subdirectories.

    Args:
        download_dir: Base download directory
        log_message_func: Function to log messages (optional)

    Returns:
        Boolean indicating success
    """
    if log_message_func is None:

        def log_message_func(message):
            print(message)

    repo_dir = os.path.join(download_dir, "firmware", "repo-dls")

    if not os.path.exists(repo_dir):
        log_message_func("Repo-dls directory does not exist. Nothing to clean.")
        return True

    try:
        # Remove all contents of the repo directory
        for item in os.listdir(repo_dir):
            item_path = os.path.join(repo_dir, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.remove(item_path)
                log_message_func(f"Removed file: {item_path}")
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
                log_message_func(f"Removed directory: {item_path}")

        log_message_func(f"Successfully cleaned the repo directory: {repo_dir}")
        return True
    except (OSError, IOError) as e:
        log_message_func(f"Error cleaning repo directory {repo_dir}: {e}")
        return False


def main(config, log_message_func=None):
    """
    Main function to run the repository downloader.

    Args:
        config: Configuration dictionary
        log_message_func: Function to log messages (optional)

    Returns:
        None
    """
    if log_message_func is None:

        def log_message_func(message):
            print(message)

    from fetchtastic import menu_repo

    download_dir = config.get("DOWNLOAD_DIR")
    if not download_dir:
        log_message_func("Download directory not configured.")
        return

    log_message_func("Starting Repository File Browser...")

    # Run the menu to select files
    selected_files = menu_repo.run_menu()

    if not selected_files:
        log_message_func("No files selected for download. Exiting.")
        return

    # Download the selected files
    downloaded_files = download_repo_files(
        selected_files, download_dir, log_message_func
    )

    if downloaded_files:
        log_message_func(f"Successfully downloaded {len(downloaded_files)} files.")
        for file_path in downloaded_files:
            log_message_func(f"  - {os.path.basename(file_path)}")

        # Show the download directory
        download_folder = (
            os.path.dirname(downloaded_files[0]) if downloaded_files else ""
        )
        if download_folder:
            log_message_func(f"\nFiles were saved to: {download_folder}")

            # If on Windows, offer to open the folder
            if platform.system() == "Windows":
                try:
                    open_folder = (
                        input(
                            "\nWould you like to open this folder? [y/n] (default: yes): "
                        )
                        .strip()
                        .lower()
                        or "y"
                    )
                    if open_folder == "y":
                        os.startfile(download_folder)
                except OSError as e: # os.startfile can raise OSError
                    log_message_func(f"Error opening folder {download_folder} with os.startfile: {e}")
                except Exception as e_open_generic: # Catch other potential errors if any
                    log_message_func(f"Unexpected error opening folder {download_folder}: {e_open_generic}")
    else:
        log_message_func("No files were downloaded.")
