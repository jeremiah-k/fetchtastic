import requests
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
from pick import pick

from fetchtastic.constants import (
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    get_effective_github_token,
    make_github_api_request,
)

# Module-level constants for repository content filtering
EXCLUDED_DIRS = [".git", ".github", "node_modules", "__pycache__", ".vscode"]
EXCLUDED_FILES = [
    ".gitignore",
    "LICENSE",
    "README.md",
    "meshtastic-deb.asc",
    "meshtastic-deb.gpg",
]


def _process_repo_contents(contents):
    """
    Process raw JSON contents from GitHub API and return sorted items.
    """
    # Filter for directories and files, excluding specific directories and files
    repo_items = []

    for item in contents:
        if item["type"] == "dir":
            if item["name"] not in EXCLUDED_DIRS and not item["name"].startswith("."):
                # Store directory info
                repo_items.append(
                    {"name": item["name"], "path": item["path"], "type": "dir"}
                )
        elif item["type"] == "file":
            if item["name"] not in EXCLUDED_FILES:
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
    firmware_dirs = [d for d in dirs if d["name"].startswith(FIRMWARE_DIR_PREFIX)]
    other_dirs = [d for d in dirs if not d["name"].startswith(FIRMWARE_DIR_PREFIX)]

    # Sort firmware directories by base version (x.y.z) desc, fallback to name
    def _fw_dir_key(d):
        name = d["name"]
        version_str = name.removeprefix(FIRMWARE_DIR_PREFIX)
        try:
            parsed = parse_version(version_str)
        except InvalidVersion:
            return (parse_version("0"), name)
        return (parsed, name)

    firmware_dirs.sort(key=_fw_dir_key, reverse=True)
    # Sort other directories alphabetically
    other_dirs.sort(key=lambda x: x["name"])

    # Sort files alphabetically
    files.sort(key=lambda x: x["name"])

    # Combine sorted lists: directories first, then files
    sorted_items = firmware_dirs + other_dirs + files

    return sorted_items


def fetch_repo_contents(path="", allow_env_token=True, github_token=None):
    """
    Fetch contents (directories and files) from Meshtastic GitHub Pages repository.

    Given an optional repository-relative path (no leading or trailing slashes required), queries GitHub Contents API for that path and returns a list of items describing directories and files found. Hidden and common infrastructure directories (e.g., .git, node_modules) and common repo-level files (e.g., README.md, LICENSE) are excluded. Returned directory entries have keys: "name", "path", "type" == "dir". File entries have keys: "name", "path", "type" == "file", and "download_url".

    Sorting:
    - Directories are returned before files.
    - Directories whose names start with "firmware-" are listed first (sorted descending by name), then other directories sorted ascending.
    - Files are sorted ascending by name.

    Parameters:
        path (str): Optional repository-relative path to list (leading/trailing slashes will be trimmed). Use an empty string to list repository root.
        allow_env_token (bool): Whether to allow using GITHUB_TOKEN from environment for authentication.

    Returns:
        list: A list of dictionaries representing directories and files. Returns an empty list on network, parsing, or unexpected errors.
    """
    # GitHub API URL for repository contents
    base_url = MESHTASTIC_GITHUB_IO_CONTENTS_URL
    # Ensure proper URL construction - avoid double slashes
    if path:
        path = path.strip("/")  # Remove leading/trailing slashes
        api_url = f"{base_url}/{path}"
    else:
        api_url = base_url

    # Get effective token for authentication logic
    get_effective_github_token(github_token, allow_env_token)

    try:
        response = make_github_api_request(
            api_url,
            github_token=github_token,
            allow_env_token=allow_env_token,
            timeout=GITHUB_API_TIMEOUT,
        )
        contents = response.json()

        return _process_repo_contents(contents)

    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching repository contents from GitHub API: {e}")
        return []
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
    Fetches directories from meshtastic.github.io repository.
    Returns a list of directory names, excluding common hidden directories.

    Note: This function is kept for backward compatibility.
    """
    items = fetch_repo_contents()
    return [item["name"] for item in items if item["type"] == "dir"]


# Backward compatibility alias
def fetch_directory_contents(path=""):
    """
    Fetch only files from directory contents for backward compatibility.

    Parameters:
        path (str): Optional repository-relative path to list.

    Returns:
        list: A list of dictionaries representing files only (directories filtered out).
    """
    all_items = fetch_repo_contents(path)

    # Filter to return only files, not directories
    return [item for item in all_items if item.get("type") == "file"]


def select_item(items, current_path=""):
    """
    Displays a menu for user to select a repository item (directory or file).
    Returns selected item information.

    Args:
        items: List of items (directories and files) to display
        current_path: Current path in repository (for display purposes)
    """
    if not items:
        print("No items found in repository.")
        return None

    # Create display names for menu
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
    if current_path and isinstance(index, int):
        index -= 1

    # Adjust for the quit option which is always at the end
    if index == len(items):
        # This shouldn't happen as we already handled quit option above
        return {"type": "quit"}

    return items[index]


def select_files(files):
    """
    Displays a menu for user to select files to download.
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
Note: Selected files will be downloaded to repo-dls directory.
Select "[Quit]" to exit without downloading."""

    selected_options = pick(
        file_names, title, multiselect=True, min_selection_count=0, indicator="*"
    )

    if not selected_options:
        print("No files selected for download.")
        return None

    # Check if quit option was selected
    for option in selected_options:
        option_name = option[0] if isinstance(option, (tuple, list)) else str(option)
        if option_name == "[Quit]":
            print("Exiting without downloading.")
            return None

    # Get the full file information for selected files
    selected_files = []
    for option in selected_options:
        option_name = option[0] if isinstance(option, (tuple, list)) else str(option)
        for file in files:
            if file["name"] == option_name:
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
            - "directory" (str): repository path containing the selected files (empty string for root).
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
