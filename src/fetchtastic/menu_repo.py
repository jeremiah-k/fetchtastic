# src/fetchtastic/menu_repo.py


import time

import requests
from pick import pick

from fetchtastic.constants import (
    API_CALL_DELAY,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
)
from fetchtastic.log_utils import logger


def fetch_repo_contents(path=""):
    """
    Fetch contents (directories and files) from the Meshtastic GitHub Pages repository.

    Given an optional repository-relative path (no leading or trailing slashes required), queries the GitHub Contents API for that path and returns a list of items describing directories and files found. Hidden and common infrastructure directories (e.g., .git, node_modules) and common repo-level files (e.g., README.md, LICENSE) are excluded. Returned directory entries have keys: "name", "path", "type" == "dir". File entries have keys: "name", "path", "type" == "file", and "download_url".

    Sorting:
    - Directories are returned before files.
    - Directories whose names start with "firmware-" are listed first (sorted descending by name), then other directories sorted ascending.
    - Files are sorted ascending by name.

    Parameters:
        path (str): Optional repository-relative path to list (leading/trailing slashes will be trimmed). Use an empty string to list the repository root.

    Returns:
        list: A list of dictionaries representing directories and files. Returns an empty list on network, parsing, or unexpected errors.
    """
    # GitHub API URL for the repository contents
    base_url = MESHTASTIC_GITHUB_IO_CONTENTS_URL
    # Ensure proper URL construction - avoid double slashes
    if path:
        path = path.strip("/")  # Remove leading/trailing slashes
        api_url = f"{base_url}/{path}"
    else:
        api_url = base_url

    try:
        response = requests.get(api_url, timeout=GITHUB_API_TIMEOUT)
        response.raise_for_status()

        # Small delay to be respectful to GitHub API
        time.sleep(API_CALL_DELAY)

        contents = response.json()

        # Filter for directories and files, excluding specific directories and files
        repo_items = []
        excluded_dirs = [".git", ".github", "node_modules", "__pycache__", ".vscode"]
        excluded_files = [
            ".gitignore",
            "LICENSE",
            "README.md",
            "meshtastic-deb.asc",
            "meshtastic-deb.gpg",
        ]

        for item in contents:
            if item["type"] == "dir":
                if item["name"] not in excluded_dirs and not item["name"].startswith(
                    "."
                ):
                    # Store directory info
                    repo_items.append(
                        {"name": item["name"], "path": item["path"], "type": "dir"}
                    )
            elif item["type"] == "file":
                if item["name"] not in excluded_files:
                    # Store file info
                    repo_items.append(
                        {
                            "name": item["name"],
                            "path": item["path"],
                            "type": "file",
                            "download_url": item["download_url"],
                        }
                    )

        # Sort items: directories first, then files
        dirs = [d for d in repo_items if d["type"] == "dir"]
        files = [f for f in repo_items if f["type"] == "file"]

        # Sort directories: firmware directories first, then others alphabetically
        firmware_dirs = [d for d in dirs if d["name"].startswith("firmware-")]
        other_dirs = [d for d in dirs if not d["name"].startswith("firmware-")]

        # Sort firmware directories by version (assuming format firmware-x.y.z.commit)
        firmware_dirs.sort(key=lambda x: x["name"], reverse=True)
        # Sort other directories alphabetically
        other_dirs.sort(key=lambda x: x["name"])

        # Sort files alphabetically
        files.sort(key=lambda x: x["name"])

        # Combine the sorted lists: directories first, then files
        sorted_items = firmware_dirs + other_dirs + files

        return sorted_items
    except requests.RequestException as e:
        logger.error(f"Error fetching repository contents from GitHub API: {e}")
        return []
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing repository contents response: {e}")
        return []
    except Exception as e:
        logger.error(
            f"Unexpected error fetching repository contents: {e}", exc_info=True
        )
        return []


def fetch_repo_directories():
    """
    Fetches directories from the meshtastic.github.io repository.
    Returns a list of directory names, excluding common hidden directories.

    Note: This function is kept for backward compatibility.
    """
    contents = fetch_repo_contents()
    # Extract just the directory names for backward compatibility
    return [item["name"] for item in contents if item["type"] == "dir"]


def fetch_directory_contents(directory):
    """
    Fetches the contents of a specific directory in the repository.
    Returns a list of file information.

    Note: This function is kept for backward compatibility.
    It now returns only files, not subdirectories.
    """
    # Use the new fetch_repo_contents function
    contents = fetch_repo_contents(directory)

    # Filter to only include files
    files = [item for item in contents if item["type"] == "file"]

    return files


def select_item(items, current_path=""):
    """
    Displays a menu for the user to select a repository item (directory or file).
    Returns the selected item information.

    Args:
        items: List of items (directories and files) to display
        current_path: Current path in the repository (for display purposes)
    """
    if not items:
        print("No items found in the repository.")
        return None

    # Create display names for the menu
    display_names = []
    for item in items:
        if item["type"] == "dir":
            display_names.append(f"{item['name']}/")
        else:
            display_names.append(item["name"])

    # Add navigation options
    if current_path:
        display_names.insert(0, "[Go back to parent directory]")

    # Always add a quit option
    display_names.append("[Quit]")

    # Add a title that shows the current path
    path_display = f" - {current_path}" if current_path else ""
    title = f"Select an item to browse{path_display} (press ENTER to navigate, find a directory with files to select and download):"

    option, index = pick(display_names, title, indicator="*")

    # Handle "Go back" option
    if current_path and index == 0:
        # Return a special value to indicate going back
        return {"type": "back"}

    # Handle "Quit" option
    if option == "[Quit]":
        # Return a special value to indicate quitting
        return {"type": "quit"}

    # Adjust index if we added a "Go back" option
    if current_path:
        index -= 1

    # Adjust for the quit option which is always at the end
    if index == len(items):
        # This shouldn't happen as we already handled the quit option above
        return {"type": "quit"}

    return items[index]


def select_files(files):
    """
    Displays a menu for the user to select files to download.
    Returns a list of selected file information.
    """
    if not files:
        print("No files found in the selected directory.")
        return None

    # Create a list of file names for the menu
    file_names = [file["name"] for file in files]

    # Add a quit option
    file_names.append("[Quit]")

    title = """Select the files you want to download (press SPACE to select, ENTER to confirm):
Note: Selected files will be downloaded to the repo-dls directory.
Select "[Quit]" to exit without downloading."""

    selected_options = pick(
        file_names, title, multiselect=True, min_selection_count=0, indicator="*"
    )

    if not selected_options:
        print("No files selected for download.")
        return None

    # Check if the quit option was selected
    for option in selected_options:
        if option[0] == "[Quit]":
            print("Exiting without downloading.")
            return None

    # Get the full file information for selected files
    selected_files = []
    for option in selected_options:
        file_name = option[0]
        for file in files:
            if file["name"] == file_name:
                selected_files.append(file)
                break

    return selected_files


def run_menu():
    """
    Run an interactive CLI to browse the Meshtastic GitHub Pages repository and select files to download.

    Presents a navigable menu of directories and files (using fetch_repo_contents, select_item, and select_files), allowing the user to:
    - navigate into directories,
    - go back to parent directories,
    - multi-select files to download, or
    - quit/cancel at any time.

    Returns:
        dict: On success, a dictionary with:
            - "directory" (str): the repository path containing the selected files (empty string for root).
            - "files" (list): list of file dictionaries chosen by the user (each item matches entries returned by fetch_repo_contents).
        None: If the user quits/cancels, if no items/files are found, or if an error occurs during execution.

    Notes:
        - This function is interactive and intended for CLI use.
        - All errors are caught internally; on error it prints a message and returns None.
    """
    try:
        current_path = ""
        selected_files = []

        while True:
            items = fetch_repo_contents(current_path)

            if not items:
                print(f"No items found in {current_path or 'the repository'}. Exiting.")
                return None

            selected_item = select_item(items, current_path)
            if not selected_item:
                return None

            # Handle navigation
            if selected_item.get("type") == "back":
                # Go back to parent directory
                if "/" in current_path:
                    current_path = current_path.rsplit("/", 1)[0]
                else:
                    current_path = ""
                continue

            # Handle quit option
            if selected_item.get("type") == "quit":
                print("Exiting repository browser.")
                return None

            if selected_item["type"] == "dir":
                # Navigate into the directory
                current_path = selected_item["path"]
                continue

            # If we get here, we're in a directory with files
            # Get all files in the current directory
            files_in_dir = [item for item in items if item["type"] == "file"]

            if files_in_dir:
                # Use the select_files function to allow multi-selection
                selected_files = select_files(files_in_dir)
                if selected_files:
                    break
                else:
                    # User didn't select any files or chose to quit
                    # Go back to the current directory listing
                    continue
            else:
                # No files in this directory, go back to directory listing
                print("No files found in this directory.")
                continue

        if not selected_files:
            return None

        # Extract the directory part from the file path
        directory = current_path

        return {"directory": directory, "files": selected_files}
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
