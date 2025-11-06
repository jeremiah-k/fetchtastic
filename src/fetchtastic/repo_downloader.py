# src/fetchtastic/repo_downloader.py

import os
import platform
import shutil

from fetchtastic import menu_repo
from fetchtastic.constants import (
    EXECUTABLE_PERMISSIONS,
    REPO_DOWNLOADS_DIR,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.log_utils import logger  # Import new logger
from fetchtastic.utils import download_file_with_retry


def download_repo_files(selected_files, download_dir):  # log_message_func removed
    """
    Download the specified repository files into a repository-specific downloads directory.

    Sanitizes file names to prevent path traversal and saves files under
    download_dir/firmware/REPO_DOWNLOADS_DIR/<selected_files["directory"]> (or the repo directory if empty).
    Sets executable permissions for files whose original name ends with SHELL_SCRIPT_EXTENSION.
    Skips items missing required fields or that fail to download; returns only the successfully downloaded file paths.

    Parameters:
        selected_files (dict): Mapping with keys:
            - "directory" (str): Subdirectory name inside the repo downloads directory.
            - "files" (iterable[dict]): Each dict must include "name" (str) and "download_url" (str).
        download_dir (str): Base path under which the repository downloads directory will be created.

    Returns:
        list[str]: Absolute paths to files that were successfully downloaded.
    """
    # Removed local log_message_func definition

    if (
        not selected_files
        or "directory" not in selected_files
        or "files" not in selected_files
    ):
        logger.info("No files selected for download.")  # Was log_message_func
        return []

    directory = selected_files["directory"]
    files = selected_files["files"]

    # Create repo downloads directory if it doesn't exist
    repo_dir = os.path.join(download_dir, "firmware", REPO_DOWNLOADS_DIR)
    try:
        os.makedirs(repo_dir, exist_ok=True)

        # Resolve and validate target directory to prevent traversal
        if directory:
            candidate = os.path.join(repo_dir, directory)
        else:
            candidate = repo_dir
        real_repo = os.path.realpath(repo_dir)
        real_target = os.path.realpath(candidate)
        try:
            common = os.path.commonpath([real_repo, real_target])
        except ValueError:
            common = None
        if common != real_repo:
            logger.warning(
                "Sanitized unsafe repository subdirectory '%s'; using base repo directory",
                directory,
            )
            dir_path = real_repo
        else:
            dir_path = real_target
        os.makedirs(dir_path, exist_ok=True)
    except OSError as e:
        logger.error(
            f"Error creating base directories for repo downloads ({repo_dir} or {dir_path}): {e}",
            exc_info=True,
        )  # Was log_message_func
        return []  # Cannot proceed if base directories can't be created

    downloaded_files = []

    for file_item in files:  # Renamed to avoid conflict with built-in 'file'
        try:
            # Safely retrieve required keys and validate early
            file_name = file_item.get("name")
            download_url = file_item.get("download_url")

            if not file_name or not download_url:
                logger.warning(
                    f"Skipping file item with missing data: name='{file_name}', download_url='{download_url}'"
                )
                continue

            # Ensure we only ever write to the intended directory
            safe_name = os.path.basename(file_name)
            if safe_name != file_name:
                logger.warning(
                    f"Sanitized file name from '{file_name}' to '{safe_name}'"
                )
            file_path = os.path.join(dir_path, safe_name)

            # download_file_with_retry now uses the global logger
            if download_file_with_retry(
                download_url, file_path
            ):  # Removed log_message_func
                # download_file_with_retry already logs the download completion
                # Set executable permissions for shell script files (moved here, after successful download)
                if file_name.lower().endswith(SHELL_SCRIPT_EXTENSION):
                    try:
                        os.chmod(file_path, EXECUTABLE_PERMISSIONS)
                        logger.debug(
                            f"Set executable permissions for {file_name}"
                        )  # Was current_log_func
                    except OSError as e_chmod:  # Specific for os.chmod
                        logger.warning(
                            f"Error setting permissions for {file_name}: {e_chmod}"
                        )  # Was current_log_func
                downloaded_files.append(file_path)
            else:
                # download_file_with_retry now logs its own detailed errors.
                logger.error(
                    f"Failed to download or validate {file_name} from {download_url}."
                )  # Was current_log_func
                # Temp file cleanup is handled by download_file_with_retry on its failures.

        except (KeyError, TypeError) as e_file_data:
            # Error accessing file data like name or download_url
            malformed_file_info = str(file_item)[
                :100
            ]  # Log part of the problematic item
            logger.error(
                f"Malformed file data encountered: {malformed_file_info}. Error: {e_file_data}. Skipping this item.",
                exc_info=True,
            )  # Was log_message_func
        except (
            Exception
        ) as e_loop:  # Catch-all for other unexpected errors in this iteration of the loop
            # This should be rare if download_file_with_retry handles its part and data is fine.
            file_name_for_log = (
                file_item.get("name", "unknown_file")
                if isinstance(file_item, dict)
                else "unknown_file"
            )
            logger.error(
                f"Unexpected error processing file '{file_name_for_log}' in download loop: {e_loop}",
                exc_info=True,
            )  # Was log_message_func

    return downloaded_files


def clean_repo_directory(download_dir):  # log_message_func removed
    """
    Remove all contents of the repository downloads directory under the given download directory.

    This function targets the directory "<download_dir>/firmware/{REPO_DOWNLOADS_DIR}". If that directory does not exist it returns True (nothing to clean). It attempts to remove every file, symlink, and subdirectory inside that repo directory.

    Parameters:
        download_dir (str): Base download directory that contains the 'firmware' folder.

    Returns:
        bool: True if the repository downloads directory was cleaned (or did not exist); False if an I/O error occurred while removing contents.
    """
    # Removed local log_message_func definition

    repo_dir = os.path.join(download_dir, "firmware", REPO_DOWNLOADS_DIR)

    if not os.path.exists(repo_dir):
        logger.info(
            "Repo-dls directory does not exist. Nothing to clean."
        )  # Was log_message_func
        return True

    try:
        # Remove all contents of the repo directory
        for item in os.listdir(repo_dir):
            item_path = os.path.join(repo_dir, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.remove(item_path)
                logger.info(f"Removed file: {item_path}")  # Was log_message_func
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
                logger.info(f"Removed directory: {item_path}")  # Was log_message_func

        logger.info(
            f"Successfully cleaned the repo directory: {repo_dir}"
        )  # Was log_message_func
        return True
    except (OSError, IOError) as e:
        logger.error(
            f"Error cleaning repo directory {repo_dir}: {e}", exc_info=True
        )  # Was log_message_func
        return False


def main(config):  # log_message_func removed
    """
    Run the repository downloader flow using settings from config.

    Starts an interactive repository file browser, downloads the selected files into the directory specified by config["DOWNLOAD_DIR"], and on Windows optionally prompts to open the download folder. Logs progress and errors via the module logger.

    Parameters:
        config (dict): Configuration mapping; must contain the key "DOWNLOAD_DIR" with the path to the base download directory.
    """
    # Removed local log_message_func definition

    # menu_repo is now imported at module level

    download_dir = config.get("DOWNLOAD_DIR")
    if not download_dir:
        logger.error("Download directory not configured.")  # Was log_message_func
        return

    logger.info("Starting Repository File Browser...")  # Was log_message_func

    # Run the menu to select files
    selected_files = (
        menu_repo.run_menu()
    )  # Assuming menu_repo.run_menu() doesn't need log_message_func

    if not selected_files:
        logger.info("No files selected for download. Exiting.")  # Was log_message_func
        return

    # Download the selected files
    downloaded_files = download_repo_files(  # log_message_func removed from call
        selected_files, download_dir
    )

    if downloaded_files:
        logger.info(
            f"Successfully downloaded {len(downloaded_files)} files."
        )  # Was log_message_func
        for file_path in downloaded_files:
            logger.info(f"  - {os.path.basename(file_path)}")  # Was log_message_func

        # Show the download directory
        download_folder = (
            os.path.dirname(downloaded_files[0]) if downloaded_files else ""
        )
        if download_folder:
            logger.info(
                f"\nFiles were saved to: {download_folder}"
            )  # Was log_message_func

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
                        os.startfile(download_folder)  # nosec B606
                except OSError as e:  # os.startfile can raise OSError
                    logger.error(
                        f"Error opening folder {download_folder} with os.startfile: {e}",
                        exc_info=True,
                    )  # Was log_message_func
                except (
                    Exception
                ) as e_open_generic:  # Catch other potential errors if any
                    logger.error(
                        f"Unexpected error opening folder {download_folder}: {e_open_generic}",
                        exc_info=True,
                    )  # Was log_message_func
    else:
        logger.info("No files were downloaded.")  # Was log_message_func
