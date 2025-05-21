# src/fetchtastic/repo_downloader.py

import os
import platform
import shutil
import time

import requests


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
    if not os.path.exists(repo_dir):
        os.makedirs(repo_dir)

    # Create directory structure matching the repository path
    if directory:
        dir_path = os.path.join(repo_dir, directory)
    else:
        dir_path = repo_dir

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    downloaded_files = []

    for file in files:
        file_name = file["name"]
        download_url = file["download_url"]
        file_path = os.path.join(dir_path, file_name)
        temp_path = file_path + ".tmp"

        try:
            log_message_func(f"Downloading {file_name} from {directory or 'root'}...")
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()

            # Download to a temporary file first
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            # Windows-specific handling for file operations
            if platform.system() == "Windows":
                # Try to move the file with retries for Windows
                max_retries = 3
                retry_delay = 1  # seconds
                for retry in range(max_retries):
                    try:
                        # Make sure the file is closed and not locked
                        import gc

                        gc.collect()  # Force garbage collection to release file handles

                        # Try to move the file
                        os.replace(temp_path, file_path)
                        break
                    except PermissionError as e:
                        if retry < max_retries - 1:
                            log_message_func(
                                f"File access error, retrying in {retry_delay} seconds: {e}"
                            )
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                        else:
                            # Last retry failed
                            raise
            else:
                # Non-Windows platforms
                os.replace(temp_path, file_path)

            # Set executable permissions for .sh files
            if file_name.endswith(".sh"):
                os.chmod(file_path, 0o755)
                log_message_func(f"Set executable permissions for {file_name}")

            log_message_func(f"Downloaded {file_name} to {file_path}")
            downloaded_files.append(file_path)

        except Exception as e:
            log_message_func(f"Error downloading {file_name}: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as e2:
                    log_message_func(f"Error removing temporary file {temp_path}: {e2}")

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
    except Exception as e:
        log_message_func(f"Error cleaning repo directory: {e}")
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
                except Exception as e:
                    log_message_func(f"Error opening folder: {e}")
    else:
        log_message_func("No files were downloaded.")
