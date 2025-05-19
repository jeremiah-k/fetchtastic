# app/menu_repo.py


import requests
from pick import pick


def fetch_repo_directories():
    """
    Fetches directories from the meshtastic.github.io repository.
    Returns a list of directory names, excluding common hidden directories.
    """
    # GitHub API URL for the repository contents
    api_url = "https://api.github.com/repos/meshtastic/meshtastic.github.io/contents"

    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        contents = response.json()

        # Filter for directories, excluding common hidden directories
        repo_dirs = []
        excluded_dirs = [".github", ".git"]

        for item in contents:
            if (
                item["type"] == "dir"
                and item["name"] not in excluded_dirs
                and not item["name"].startswith(".")
            ):
                repo_dirs.append(item["name"])

        # Sort directories alphabetically, but put firmware directories first
        firmware_dirs = [d for d in repo_dirs if d.startswith("firmware-")]
        other_dirs = [d for d in repo_dirs if not d.startswith("firmware-")]

        # Sort firmware directories by version (assuming format firmware-x.y.z.commit)
        firmware_dirs.sort(reverse=True)
        # Sort other directories alphabetically
        other_dirs.sort()

        # Combine the sorted lists
        sorted_dirs = firmware_dirs + other_dirs

        return sorted_dirs
    except Exception as e:
        print(f"Error fetching repository directories: {e}")
        return []


def fetch_directory_contents(directory):
    """
    Fetches the contents of a specific directory in the repository.
    Returns a list of file names and their download URLs.
    """
    api_url = f"https://api.github.com/repos/meshtastic/meshtastic.github.io/contents/{directory}"

    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        contents = response.json()

        files = []
        for item in contents:
            if item["type"] == "file":
                files.append(
                    {
                        "name": item["name"],
                        "download_url": item["download_url"],
                        "path": item["path"],
                    }
                )

        # Sort files alphabetically
        files.sort(key=lambda x: x["name"])

        return files
    except Exception as e:
        print(f"Error fetching directory contents: {e}")
        return []


def select_directory(directories):
    """
    Displays a menu for the user to select a repository directory.
    Returns the selected directory name.
    """
    if not directories:
        print("No directories found in the repository.")
        return None

    title = "Select a directory to browse (press ENTER to confirm):"
    option, index = pick(directories, title, indicator="*")
    return option


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

    title = """Select the files you want to download (press SPACE to select, ENTER to confirm):
Note: Selected files will be downloaded to the repo-dls directory."""

    selected_options = pick(
        file_names, title, multiselect=True, min_selection_count=0, indicator="*"
    )

    if not selected_options:
        print("No files selected for download.")
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
        print("Fetching directories from meshtastic.github.io repository...")
        directories = fetch_repo_directories()

        if not directories:
            print("No directories found in the repository. Exiting.")
            return None

        selected_dir = select_directory(directories)
        if not selected_dir:
            return None

        print(f"Fetching files from {selected_dir}...")
        files = fetch_directory_contents(selected_dir)

        if not files:
            print(f"No files found in {selected_dir}. Exiting.")
            return None

        selected_files = select_files(files)
        if not selected_files:
            return None

        return {"directory": selected_dir, "files": selected_files}
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
