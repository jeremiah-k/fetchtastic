import curses
from datetime import datetime
from typing import Any, Protocol

import requests  # type: ignore[import-untyped]
from pick import (
    KEYS_DOWN,
    KEYS_ENTER,
    KEYS_SELECT,
    KEYS_UP,
    Option,
    Picker,
    Position,
)

from fetchtastic.constants import (
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    FIRMWARE_DIR_PREFIX,
    GITHUB_API_TIMEOUT,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
    REPO_DOWNLOADS_DIR,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager
from fetchtastic.download.repository import RepositoryDownloader
from fetchtastic.download.version import VersionManager
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request


class CursesScreen(Protocol):
    """Protocol for curses screen objects used by MenuPicker."""

    def getmaxyx(self) -> tuple[int, int]:
        """
        Return the current screen dimensions.

        Returns:
            (rows, cols): A tuple with the number of rows (height) and columns (width) of the screen in character cells.
        """
        ...

    def getch(self) -> int:
        """
        Read a single key code from the screen input.

        Returns:
            int: Integer key code for the pressed key.
        """
        ...


# Module-level constants for repository content filtering
EXCLUDED_DIRS = [".git", ".github", "node_modules", "__pycache__", ".vscode"]
EXCLUDED_FILES: list[str] = [".gitignore"]
_VERSION_MANAGER = VersionManager()

_KEY_PAGE_UP = getattr(curses, "KEY_PPAGE", None)
_KEY_PAGE_DOWN = getattr(curses, "KEY_NPAGE", None)
KEYS_PAGE_UP = tuple(k for k in (_KEY_PAGE_UP,) if k is not None)
KEYS_PAGE_DOWN = tuple(k for k in (_KEY_PAGE_DOWN,) if k is not None)


class MenuPicker(Picker):
    """
    Picker extension that supports PageUp/PageDown for faster navigation.
    """

    def _page_step(self, screen: CursesScreen) -> int:
        """
        Compute how many rows to move when paging through the menu based on available screen rows and title lines.

        Returns:
            int: Number of rows to move for a page step (at least 1).
        """
        max_y, max_x = screen.getmaxyx()
        title_lines = len(self.get_title_lines(max_width=max_x))
        max_rows = max_y - self.position.y
        step = max_rows - title_lines - 1
        return max(1, step)

    def _is_action_option(self, option: Option) -> bool:
        """
        Check whether a menu Option represents a navigation action ('back' or 'quit').

        Parameters:
            option (Option): The menu option to inspect.

        Returns:
            bool: `True` if `option` is an Option whose `value` is a dict with `"type"` equal to `"back"` or `"quit"`, `False` otherwise.
        """
        if not isinstance(option, Option):
            return False
        if not isinstance(option.value, dict):
            return False
        return option.value.get("type") in {"back", "quit"}

    def run_loop(self, screen: CursesScreen, position: Position) -> Any:
        """
        Run the picker's interactive input loop, handling navigation, selection, and quit actions.

        This repeatedly draws the UI, reads a key from the provided screen, and updates or finalizes the picker's selection state according to navigation keys (page up/down, up/down), selection keys, enter, and configured quit keys.

        Parameters:
            screen (CursesScreen): Screen-like object used for drawing and reading key input.
            position (Position): Position for the picker; intentionally ignored in this override.

        Returns:
            For single-select mode: a tuple (selected_item, index) when an item is chosen, or (None, -1) if the user quit.
            For multi-select mode: a list of selected items (or action tuples) when the user confirms, or an empty list if the user quit.
        """
        while True:
            self.draw(screen)
            c = screen.getch()
            if self.quit_keys is not None and c in self.quit_keys:
                if self.multiselect:
                    return []
                return None, -1
            if c in KEYS_PAGE_UP:
                for _ in range(self._page_step(screen)):
                    self.move_up()
                continue
            if c in KEYS_PAGE_DOWN:
                for _ in range(self._page_step(screen)):
                    self.move_down()
                continue
            if c in KEYS_UP:
                self.move_up()
                continue
            if c in KEYS_DOWN:
                self.move_down()
                continue
            if c in KEYS_ENTER:
                if (
                    self.multiselect
                    and len(self.selected_indexes) < self.min_selection_count
                ):
                    continue
                if self.multiselect:
                    option = self.options[self.index]
                    if isinstance(option, Option) and self._is_action_option(option):
                        return [(option, self.index)]
                return self.get_selected()
            if c in KEYS_SELECT and self.multiselect:
                self.mark_index()


def _pick_menu(
    options: list[Option],
    title: str | None = None,
    indicator: str = "*",
    default_index: int = 0,
    multiselect: bool = False,
    min_selection_count: int = 0,
    screen: CursesScreen | None = None,
    position: Position | None = None,
    clear_screen: bool = True,
    quit_keys: list[int] | None = None,
) -> Any:
    """
    Present a terminal menu and return the user's selection(s).

    Parameters:
        options (list[Option]): List of menu options to display.
        title (str | None): Optional title shown above the menu.
        indicator (str): Character used to mark the current selection.
        default_index (int): Index highlighted when the menu opens.
        multiselect (bool): If True, allow selecting multiple options.
        min_selection_count (int): Minimum number of items required when multiselect is enabled.
        screen (CursesScreen | None): Optional pre-initialized curses screen to use for rendering.
        position (Position | None): Optional position for the picker; defaults to Position(0, 0) if None.
        clear_screen (bool): If True, clear the screen before drawing the menu.
        quit_keys (list[int] | None): Additional key codes that trigger quitting/canceling the menu.

    Returns:
        Any: For single-select mode, returns the chosen Option, or `(None, -1)` if canceled.
             For multi-select mode, returns a list of selected Option objects, or an empty list if canceled.
    """
    picker = MenuPicker(
        options,
        title=title,
        indicator=indicator,
        default_index=default_index,
        multiselect=multiselect,
        min_selection_count=min_selection_count,
        screen=screen,
        position=position or Position(0, 0),
        clear_screen=clear_screen,
        quit_keys=quit_keys,
    )
    return picker.start()


def _process_repo_contents(
    contents: list[dict[str, Any]],
    firmware_commit_times: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    """
    Process raw GitHub API content entries into a filtered, sorted list of directory and file items.

    Filters out entries listed in EXCLUDED_DIRS and EXCLUDED_FILES, converts directories to items with keys `name`, `path`, and `type: "dir"`, and files to items with keys `name`, `path`, `type: "file"`, and `download_url`. Directories are ordered with firmware directories (names starting with FIRMWARE_DIR_PREFIX) first; when `firmware_commit_times` is provided, firmware directories are ordered by commit timestamp (newest first) with release-version and name used as fallbacks. Non-firmware directories and files are sorted alphabetically by name.

    Parameters:
        contents (list[dict[str, Any]]): Raw JSON objects returned by the GitHub contents API.
        firmware_commit_times (dict[str, datetime] | None): Optional mapping from firmware directory name (lowercase) to its commit timestamp used to order firmware directories.

    Returns:
        list[dict[str, Any]]: Filtered and sorted list of items representing directories and files ready for display or further processing.
    """
    # Filter for directories and files, excluding specific directories and files
    repo_items = []

    for item in contents:
        if item["type"] == "dir":
            if item["name"] not in EXCLUDED_DIRS:
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

    firmware_commit_times = firmware_commit_times or {}
    version_manager = _VERSION_MANAGER

    def _lookup_commit_time(name: str) -> datetime | None:
        """
        Return the commit timestamp associated with a firmware directory name, using a case-insensitive lookup.

        Parameters:
                name (str): Firmware directory name to look up.

        Returns:
                datetime | None: The commit timestamp for the given directory name if present, otherwise `None`.
        """
        return firmware_commit_times.get(name.lower())

    # Sort firmware directories by commit time when available, otherwise by version.
    def _fw_dir_key(
        d: dict[str, Any],
    ) -> tuple[int, float, tuple, str] | tuple[tuple, str]:
        """
        Provide a sorting key for a firmware directory entry.

        Parameters:
            d (dict[str, Any]): Directory entry dictionary with a "name" key containing the directory name (expected to start with FIRMWARE_DIR_PREFIX).

        Returns:
            tuple: A tuple suitable for sorting firmware directories. If firmware commit timestamps are available, returns `(1 if a commit timestamp exists else 0, commit_timestamp (float), version_tuple, name)`; otherwise returns `(version_tuple, name)`.
        """
        name = d["name"]
        version_str = name.removeprefix(FIRMWARE_DIR_PREFIX)
        version_tuple = version_manager.get_release_tuple(version_str) or ()
        if firmware_commit_times:
            commit_time = _lookup_commit_time(name)
            commit_ts = commit_time.timestamp() if commit_time else 0.0
            return (1 if commit_time else 0, commit_ts, version_tuple, name)
        return (version_tuple, name)

    firmware_dirs.sort(key=_fw_dir_key, reverse=True)
    # Sort other directories alphabetically
    other_dirs.sort(key=lambda x: x["name"])

    # Sort files alphabetically
    files.sort(key=lambda x: x["name"])

    # Combine sorted lists: directories first, then files
    sorted_items = firmware_dirs + other_dirs + files

    return sorted_items


def fetch_repo_contents(
    path: str = "",
    allow_env_token: bool = True,
    github_token: str | None = None,
    cache_manager: CacheManager | None = None,
    firmware_commit_times: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve and process directory and file entries from the Meshtastic GitHub Pages repository for a given repository-relative path.

    Given an optional path (leading/trailing slashes are ignored), queries the GitHub Contents API and returns a sorted list of item dictionaries describing directories and files in that path. Directory items include "name", "path", and "type" == "dir". File items include "name", "path", "type" == "file", and "download_url". Entries listed in EXCLUDED_DIRS/EXCLUDED_FILES are omitted.

    Parameters:
        path (str): Repository-relative path to list; use an empty string for the repository root.
        allow_env_token (bool): Whether to permit using the GITHUB_TOKEN environment variable for authentication.
        github_token (str | None): Optional explicit GitHub token to use; if provided it overrides environment-based token usage.
        cache_manager (CacheManager | None): Optional cache manager for GitHub API responses.
        firmware_commit_times (dict[str, datetime] | None): Optional mapping of firmware directory names to commit timestamps for sorting.

    Returns:
        list: A list of dictionaries representing directories and files (directories appear before files). Returns an empty list on network, parsing, or other unexpected errors.
    """
    # GitHub API URL for repository contents
    base_url = MESHTASTIC_GITHUB_IO_CONTENTS_URL
    # Ensure proper URL construction - avoid double slashes
    if path:
        path = path.strip("/")  # Remove leading/trailing slashes
        api_url = f"{base_url}/{path}"
    else:
        api_url = base_url

    try:
        if cache_manager is not None:
            contents = cache_manager.get_repo_contents(
                path,
                github_token=github_token,
                allow_env_token=allow_env_token,
            )
        else:
            # Note: cache miss tracking is handled by the caller
            response = make_github_api_request(
                api_url,
                github_token=github_token,
                allow_env_token=allow_env_token,
                timeout=GITHUB_API_TIMEOUT,
            )
            contents = response.json()

        if isinstance(contents, list):
            logger.debug(f"Fetched {len(contents)} items from repository")

        if not isinstance(contents, list):
            logger.warning(
                f"Expected a list of repository contents from GitHub API, but got {type(contents).__name__}. Assuming empty directory."
            )
            return []

        return _process_repo_contents(
            contents, firmware_commit_times=firmware_commit_times
        )

    except requests.HTTPError as e:
        logger.warning(f"HTTP error fetching repository contents from GitHub API: {e}")
        return []
    except requests.RequestException as e:
        logger.warning(f"Could not fetch repository contents from GitHub API: {e}")
        return []
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing repository contents response: {e}")
        return []
    except Exception as e:
        logger.error(
            f"Unexpected error fetching repository contents: {e}", exc_info=True
        )
        return []


def fetch_repo_directories(
    path: str = "",
    allow_env_token: bool = True,
    github_token: str | None = None,
    cache_manager: CacheManager | None = None,
    firmware_commit_times: dict[str, datetime] | None = None,
) -> list[str]:
    """
    List directory names at the given repository path on meshtastic.github.io.

    Directories listed in EXCLUDED_DIRS are omitted from the results.

    Parameters:
        path (str): Repository-relative path to list (empty string for root).
        allow_env_token (bool): If True, allow using a GitHub token from the environment when making the API request.
        github_token (Optional[str]): Explicit GitHub token to use instead of an environment token.
        cache_manager (CacheManager | None): Optional cache manager for GitHub API responses.
        firmware_commit_times (dict[str, datetime] | None): Optional mapping of firmware directory names to commit timestamps for sorting.

    Returns:
        list[str]: Directory names found at the specified path.
    """
    items = fetch_repo_contents(
        path=path,
        allow_env_token=allow_env_token,
        github_token=github_token,
        cache_manager=cache_manager,
        firmware_commit_times=firmware_commit_times,
    )
    return [item["name"] for item in items if item["type"] == "dir"]


# Backward compatibility alias
def fetch_directory_contents(
    path: str = "",
    allow_env_token: bool = True,
    github_token: str | None = None,
    cache_manager: CacheManager | None = None,
    firmware_commit_times: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch only files from directory contents for backward compatibility.

    Parameters:
        path (str): Optional repository-relative path to list.
        allow_env_token (bool): If True, allow using a GitHub token from the environment when making the API request.
        github_token (Optional[str]): Explicit GitHub token to use instead of an environment token.
        cache_manager (CacheManager | None): Optional cache manager for GitHub API responses.
        firmware_commit_times (dict[str, datetime] | None): Optional mapping of firmware directory names to commit timestamps for sorting.

    Returns:
        list: A list of dictionaries representing files only (directories filtered out).
    """
    all_items = fetch_repo_contents(
        path=path,
        allow_env_token=allow_env_token,
        github_token=github_token,
        cache_manager=cache_manager,
        firmware_commit_times=firmware_commit_times,
    )

    # Filter to return only files, not directories
    return [item for item in all_items if item.get("type") == "file"]


def _build_firmware_commit_times(
    cache_manager: CacheManager,
    github_token: str | None,
    allow_env_token: bool,
) -> dict[str, datetime]:
    """
    Build a mapping of firmware directory names to commit timestamps using recent repository history.

    Attempts to fetch recent commits via PrereleaseHistoryManager (using the provided cache and token settings) and returns a mapping from prerelease directory name to its latest commit timestamp. If fetching commits fails, an empty dict is returned.

    Parameters:
        cache_manager (CacheManager): Cache manager used to read/write prerelease commit history.
        github_token (str | None): GitHub token to use for API requests, or None to rely on environment/config.
        allow_env_token (bool): Whether using a token from the environment is permitted when `github_token` is None.

    Returns:
        dict[str, datetime]: Mapping of firmware directory name to commit timestamp; empty on failure.
    """
    prerelease_manager = PrereleaseHistoryManager()
    try:
        commits = prerelease_manager.fetch_recent_repo_commits(
            DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
            cache_manager=cache_manager,
            github_token=github_token,
            allow_env_token=allow_env_token,
        )
    except (OSError, ValueError, TypeError, requests.RequestException) as exc:
        logger.debug(
            "Could not build prerelease commit history for repo sorting: %s", exc
        )
        return {}

    return prerelease_manager.extract_prerelease_directory_timestamps(commits)


def select_item(
    items: list[dict[str, Any]], current_path: str = ""
) -> dict[str, Any] | None:
    """
    Present a navigation menu for repository items and return the user's chosen action or item.

    Parameters:
        items (list[dict[str, Any]]): Repository entries where each item has at least a "type" key with value "dir" or "file".
        current_path (str): Path shown in menu title to indicate the current directory (empty for root).

    Returns:
        dict[str, Any] | None: The selected value:
          - For directory selection: directory item dict (contains its metadata).
          - For choosing files in current directory: {"type": "current"}.
          - For going up one level: {"type": "back"}.
          - For quitting: {"type": "quit"}.
          - `None` if no valid selection was made.
    """
    if not items:
        print("No items found in repository.")
        return None

    dirs = [item for item in items if item.get("type") == "dir"]
    files = [item for item in items if item.get("type") == "file"]

    # Create display options for the menu.
    display_options: list[Option] = []
    if current_path:
        display_options.append(
            Option(label="[Go back to parent directory]", value={"type": "back"})
        )
    if files:
        file_count = len(files)
        file_label = "file" if file_count == 1 else "files"
        display_options.append(
            Option(
                label=f"[Select files in this directory ({file_count} {file_label})]",
                value={"type": "current"},
            )
        )
    for item in dirs:
        display_options.append(Option(label=f"{item['name']}/", value=item))

    if files:
        display_options.append(Option(label="Files:", enabled=False))
        for file_info in files:
            display_options.append(
                Option(label=f"  - {file_info['name']}", enabled=False)
            )

    # Always add a quit option
    display_options.append(Option(label="[Quit]", value={"type": "quit"}))

    # Add a title that shows the current path
    path_display = f" - {current_path}" if current_path else ""
    title = (
        f"Select an item to browse{path_display} (ENTER to open, PageUp/PageDown to jump). "
        "Use [Quit] to exit."
    )

    option, _index = _pick_menu(display_options, title, indicator="*")

    if isinstance(option, Option):
        return option.value
    return None


def select_files(
    files: list[dict[str, Any]],
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    Present a multi-select menu allowing the user to choose repository files for download.

    Parameters:
        files (list[dict[str, Any]]): List of file dictionaries from the repository API. Each dictionary must include a "name" key; other keys (e.g., "download_url", "size") are preserved and returned.

    Returns:
        list[dict[str, Any]] | dict[str, Any] | None: A list of the selected file dictionaries in the same format as `files`,
        a dict like `{"type": "back"}` or `{"type": "quit"}` when a navigation action is chosen,
        or `None` if the user cancels or no files are selected.
    """
    if not files:
        print("No files found in the selected directory.")
        return None

    display_options: list[Option] = [
        Option(label="[Back]", value={"type": "back"}),
        Option(label="[Quit]", value={"type": "quit"}),
        Option(label="Files:", enabled=False),
    ]
    for file_info in files:
        display_options.append(Option(label=file_info["name"], value=file_info))
    display_options.append(Option(label="[Back]", value={"type": "back"}))

    title = (
        "Select the files you want to download (SPACE to select, ENTER to confirm, "
        "PageUp/PageDown to jump).\n"
        f"Selected files will be downloaded to {REPO_DOWNLOADS_DIR}. "
        "Use [Back] to return or [Quit] to exit."
    )

    selected_options = _pick_menu(
        display_options,
        title,
        multiselect=True,
        min_selection_count=0,
        indicator="*",
    )

    if not selected_options:
        print("No files selected for download.")
        return None

    # Process selected options
    selected_files = []
    action_type = None
    for option in selected_options:
        option_obj = option[0] if isinstance(option, (tuple, list)) else option
        if isinstance(option_obj, Option) and isinstance(option_obj.value, dict):
            opt_type = option_obj.value.get("type")
            if opt_type == "quit":
                return {"type": "quit"}
            if opt_type == "back":
                action_type = "back"
                continue
            selected_files.append(option_obj.value)

    if action_type:
        return {"type": action_type}

    if not selected_files:
        print("No files selected for download.")
        return None
    return selected_files


def run_menu(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    Interactively browse the Meshtastic GitHub Pages repository and select one or more files to download.

    Parameters:
        config (dict | None): Optional configuration used to supply a GitHub token and cache settings.

    Returns:
        result (dict or None): If files were selected, a dict with:
            - "directory" (str): repository path containing the selected files (empty string for root).
            - "files" (list): list of file dictionaries chosen by the user (each matches entries returned by fetch_repo_contents).
        If the user cancels, no files are selected, or an error occurs, returns None.
    """
    try:
        current_path = ""
        selected_files: list[dict[str, Any]] = []
        github_token: str | None = None
        allow_env_token = True
        cache_manager: CacheManager | None = None
        if config is not None:
            github_token = config.get("GITHUB_TOKEN")
            allow_env_token = config.get("ALLOW_ENV_TOKEN", True)
            cache_manager = CacheManager()
        firmware_commit_times: dict[str, datetime] = {}

        if cache_manager is not None:
            firmware_commit_times = _build_firmware_commit_times(
                cache_manager, github_token, allow_env_token
            )

        while True:
            items = fetch_repo_contents(
                current_path,
                allow_env_token=allow_env_token,
                github_token=github_token,
                cache_manager=cache_manager,
                firmware_commit_times=firmware_commit_times,
            )

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

            if selected_item.get("type") == "current":
                # Show file selection for the current directory
                files_in_dir = [item for item in items if item["type"] == "file"]
                if files_in_dir:
                    selection = select_files(files_in_dir)
                    if isinstance(selection, dict):
                        if selection.get("type") == "quit":
                            print("Exiting repository browser.")
                            return None
                        if selection.get("type") == "back":
                            continue
                    elif selection:
                        selected_files = selection
                        break
                    continue
                print("No files found in this directory.")
                continue

            if selected_item.get("type") == "dir":
                # Navigate into the directory
                current_path = selected_item["path"]
                continue

        if not selected_files:
            return None

        # Extract the directory part from the file path
        directory = current_path

        return {"directory": directory, "files": selected_files}
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def run_repository_downloader_menu(config):
    """
    Orchestrates an interactive repository browsing and download workflow.

    Presents the repository browsing menu, downloads the user's selected files using RepositoryDownloader, and aggregates successful results.

    Parameters:
        config (dict): Configuration options for the downloader (e.g., destination directory, timeouts, credentials, and other download-related settings).

    Returns:
        List[str] | None: List of filesystem paths for successfully downloaded files, or `None` if the operation was cancelled, errored, or no files were downloaded.
    """
    try:
        # Get user selection from the menu
        selected_files = run_menu(config)
        if not selected_files:
            logger.info("No files selected for download.")
            return None

        # Create repository downloader instance
        repo_downloader = RepositoryDownloader(config)

        # Convert selected files to the format expected by the new downloader
        files_to_download = []
        for file_info in selected_files["files"]:
            file_data = {
                "name": file_info["name"],
                "download_url": file_info["download_url"],
                "size": file_info.get("size", 0),
            }
            files_to_download.append(file_data)

        # Download the files using the new downloader
        download_results = repo_downloader.download_repository_files_batch(
            files_to_download, selected_files["directory"]
        )

        # Process results
        successful_downloads = []
        for result in download_results:
            if result.success:
                successful_downloads.append(str(result.file_path))
                logger.info(f"Successfully downloaded: {result.file_path}")
            else:
                logger.error(f"Failed to download: {result.error_message}")

        if successful_downloads:
            logger.info(f"Successfully downloaded {len(successful_downloads)} files.")
            return successful_downloads
        else:
            logger.info("No files were downloaded successfully.")
            return None

    except Exception as e:
        logger.error(f"Error in repository downloader workflow: {e}", exc_info=True)
        return None
