# app/menu_repo.py


import requests
from pick import pick


def fetch_repo_contents(path=""):
    """
    Fetches contents (directories and files) from the meshtastic.github.io repository.
    Returns a list of items with their names, paths, and types.

    Args:
        path: Optional path within the repository to fetch contents from
    """
    # GitHub API URL for the repository contents
    base_url = "https://api.github.com/repos/meshtastic/meshtastic.github.io/contents"
    api_url = f"{base_url}/{path}" if path else base_url

    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        contents = response.json()

        # Filter for directories and files, excluding specific directories and files
        repo_items = []
        excluded_dirs = [".git", "node_modules", "__pycache__", ".vscode"]
        excluded_files = [
            ".gitignore",
            "LICENSE",
            "README.md",
            "meshtastic-deb.asc",
            "meshtastic-deb.gpg",
        ]

        for item in contents:
            if item["type"] == "dir":
                if item["name"] not in excluded_dirs and (
                    not item["name"].startswith(".") or item["name"] == ".github"
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
    except Exception as e:
        print(f"Error fetching repository contents: {e}")
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
            display_names.append(f"📁 {item['name']}/")
        else:
            display_names.append(f"📄 {item['name']}")

    # Add navigation options
    if current_path:
        display_names.insert(0, "⬆️ Go back to parent directory")

    # Always add a quit option
    display_names.append("❌ Quit")

    # Add a title that shows the current path
    path_display = f" - {current_path}" if current_path else ""
    title = f"Select an item to browse{path_display} (press ENTER to confirm):"

    option, index = pick(display_names, title, indicator="*")

    # Handle "Go back" option
    if current_path and index == 0:
        # Return a special value to indicate going back
        return {"type": "back"}

    # Handle "Quit" option
    if option == "❌ Quit":
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
    file_names.append("❌ Quit")

    title = """Select the files you want to download (press SPACE to select, ENTER to confirm):
Note: Selected files will be downloaded to the repo-dls directory.
Select "❌ Quit" to exit without downloading."""

    selected_options = pick(
        file_names, title, multiselect=True, min_selection_count=0, indicator="*"
    )

    if not selected_options:
        print("No files selected for download.")
        return None

    # Check if the quit option was selected
    for option in selected_options:
        if option[0] == "❌ Quit":
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
    Runs the repository browsing menu and returns the selected files.
    """
    try:
        current_path = ""
        selected_files = []

        while True:
            print(
                f"Fetching contents from meshtastic.github.io repository{' - ' + current_path if current_path else ''}..."
            )
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

            # If we get here, a file was selected
            # Ask if the user wants to download this file
            print(f"Selected file: {selected_item['name']}")
            download_prompt = (
                "Do you want to download this file? [y/n] (default: yes): "
            )
            download_choice = input(download_prompt).strip().lower() or "y"

            if download_choice == "y":
                selected_files = [selected_item]
                break
            else:
                # Go back to the current directory listing
                continue

        if not selected_files:
            return None

        # Extract the directory part from the file path
        directory = current_path

        return {"directory": directory, "files": selected_files}
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
