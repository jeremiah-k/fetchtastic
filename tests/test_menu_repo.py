import pytest
import requests
from pick import Option

from fetchtastic import menu_repo

pytestmark = [pytest.mark.user_interface]


@pytest.fixture
def mock_repo_contents():
    """
    Return a mock list of items shaped like the GitHub repository contents API.

    The list includes a mix of directories and files used by tests:
    - Directories: three firmware/event entries and `.git`/`.github` (both intended to be excluded by the fetching logic).
    - Files: `index.html`, `meshtastic-deb.asc`, and `README.md` (included by the production logic; only `.git` is excluded).

    Returns:
        list[dict]: Mock content entries with keys like `name`, `path`, `type`, and optionally `download_url`.
    """
    return [
        # Directories
        {
            "name": "firmware-2.7.4.c1f4f79",
            "path": "firmware-2.7.4.c1f4f79",
            "type": "dir",
        },
        {
            "name": "firmware-2.7.3.cf574c7",
            "path": "firmware-2.7.3.cf574c7",
            "type": "dir",
        },
        {"name": "event", "path": "event", "type": "dir"},
        {"name": ".git", "path": ".git", "type": "dir"},  # Should be excluded
        {"name": ".github", "path": ".github", "type": "dir"},  # Should be excluded
        # Files
        {
            "name": "index.html",
            "path": "index.html",
            "type": "file",
            "download_url": "url1",
        },
        {
            "name": "meshtastic-deb.asc",
            "path": "meshtastic-deb.asc",
            "type": "file",
            "download_url": "url2",
        },
        {
            "name": "README.md",
            "path": "README.md",
            "type": "file",
            "download_url": "url3",
        },
    ]


def test_fetch_repo_contents(mocker, mock_repo_contents):
    """Test fetching and processing of repository contents."""
    import fetchtastic.utils

    mock_response = mocker.MagicMock()
    mock_response.json.return_value = mock_repo_contents
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.return_value = mock_response

    # Reset rate limit cache to avoid cached rate limit issues
    mocker.patch.object(fetchtastic.utils, "_rate_limit_cache_loaded", False)
    mocker.patch.object(fetchtastic.utils, "_rate_limit_cache", {})
    items = menu_repo.fetch_repo_contents()

    # Check filtering - should be 6 items (3 dirs, 3 files) - .git excluded
    assert len(items) == 6
    assert not any(item["name"] == ".git" for item in items)
    assert not any(item["name"] == ".github" for item in items)

    # Check sorting
    assert (
        items[0]["name"] == "firmware-2.7.4.c1f4f79"
    )  # Firmware dirs sorted descending
    assert items[1]["name"] == "firmware-2.7.3.cf574c7"
    assert items[2]["name"] == "event"  # Other dirs sorted ascending
    assert items[3]["name"] == "README.md"  # Files sorted ascending
    assert items[4]["name"] == "index.html"
    assert items[5]["name"] == "meshtastic-deb.asc"


def test_fetch_repo_contents_uses_cache_manager(mocker):
    """Test fetch_repo_contents uses cache manager when provided."""

    class FakeCache:
        def __init__(self):
            self.args = None

        def get_repo_contents(self, path, github_token=None, allow_env_token=True):
            self.args = (path, github_token, allow_env_token)
            return [
                {"name": "dir1", "path": "dir1", "type": "dir"},
                {
                    "name": "file1.txt",
                    "path": "file1.txt",
                    "type": "file",
                    "download_url": "https://example.com/file1.txt",
                },
            ]

    cache = FakeCache()
    mocker.patch(
        "fetchtastic.menu_repo.make_github_api_request",
        side_effect=AssertionError("API request should not be called"),
    )

    items = menu_repo.fetch_repo_contents(
        "subdir", allow_env_token=False, github_token="token", cache_manager=cache
    )

    assert cache.args == ("subdir", "token", False)
    assert [item["name"] for item in items] == ["dir1", "file1.txt"]


def test_select_item(mocker):
    """Test the user item selection menu logic."""
    # Patch where pick is looked up, which is in the menu_repo module
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    items = [
        {"name": "dir1", "path": "dir1", "type": "dir"},
        {"name": "file1.txt", "path": "file1.txt", "type": "file"},
    ]

    # 1. Select a directory
    mock_pick.return_value = (Option(label="dir1/", value=items[0]), 0)
    selected = menu_repo.select_item(items)
    assert selected["type"] == "dir"
    assert selected["name"] == "dir1"

    # 2. Select "Go back"
    mock_pick.return_value = (
        Option(label="[Go back to parent directory]", value={"type": "back"}),
        0,
    )
    selected = menu_repo.select_item(items, current_path="some/path")
    assert selected["type"] == "back"

    # 3. Select "Quit"
    mock_pick.return_value = (Option(label="[Quit]", value={"type": "quit"}), 1)
    selected = menu_repo.select_item(items)
    assert selected["type"] == "quit"


def test_select_files(mocker):
    """Test the user file selection menu logic."""
    # Patch where pick is looked up
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    files = [
        {"name": "file1.txt", "download_url": "url1"},
        {"name": "file2.txt", "download_url": "url2"},
    ]

    # 1. Select some files
    mock_pick.return_value = [
        (Option(label="file1.txt", value=files[0]), 0),
        (Option(label="file2.txt", value=files[1]), 1),
    ]
    selected = menu_repo.select_files(files)
    assert len(selected) == 2
    assert selected[0]["name"] == "file1.txt"

    # 2. Select "Quit"
    mock_pick.return_value = [(Option(label="[Quit]", value={"type": "quit"}), 2)]
    selected = menu_repo.select_files(files)
    assert selected == {"type": "quit"}

    # 3. Select nothing
    mock_pick.return_value = []
    selected = menu_repo.select_files(files)
    assert selected is None


def test_fetch_repo_contents_with_path(mocker, mock_repo_contents):
    """Test fetching repository contents with a specific path."""
    import fetchtastic.utils

    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = mock_repo_contents
    mock_make_request.return_value = mock_response

    # Reset rate limit cache to avoid cached rate limit issues
    mocker.patch.object(fetchtastic.utils, "_rate_limit_cache_loaded", False)
    mocker.patch.object(fetchtastic.utils, "_rate_limit_cache", {})
    menu_repo.fetch_repo_contents("firmware-2.7.4.c1f4f79")

    # Verify the URL was constructed correctly with proper parameters
    expected_url = "https://api.github.com/repos/meshtastic/meshtastic.github.io/contents/firmware-2.7.4.c1f4f79"
    from fetchtastic.constants import GITHUB_API_TIMEOUT

    mock_make_request.assert_called_once_with(
        expected_url,
        github_token=None,
        allow_env_token=True,  # Warning logic now centralized in make_github_api_request
        timeout=GITHUB_API_TIMEOUT,
    )


def test_fetch_repo_contents_request_exception(mocker):
    """Test handling of request exceptions."""
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.side_effect = requests.RequestException("Network error")
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.warning.assert_called_once()
    args, _ = mock_logger.warning.call_args
    assert "Could not fetch repository contents from GitHub API" in args[0]


def test_fetch_repo_contents_json_error(mocker):
    """Test handling of JSON parsing errors."""
    mock_response = mocker.MagicMock()
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Error parsing repository contents response" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_contents_key_error(mocker):
    """Test handling of missing keys in response."""
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"invalid": "data"}]  # Missing required keys
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Error parsing repository contents response" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_contents_unexpected_error(mocker):
    """Test handling of unexpected errors."""
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.side_effect = Exception("Unexpected error")
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Unexpected error fetching repository contents" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_directories(mocker):
    """Test the backward compatibility function for fetching directories."""
    mock_contents = [
        {"name": "dir1", "type": "dir"},
        {"name": "dir2", "type": "dir"},
        {"name": "file1.txt", "type": "file"},
    ]
    mocker.patch(
        "fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_contents
    )

    directories = menu_repo.fetch_repo_directories()

    assert directories == ["dir1", "dir2"]


def test_build_firmware_commit_times_no_commits(mocker):
    """Test prerelease commit time mapping when no commits are returned."""
    from fetchtastic.download.cache import CacheManager

    cache_manager = CacheManager()
    mocker.patch(
        "fetchtastic.menu_repo.PrereleaseHistoryManager.fetch_recent_repo_commits",
        return_value=[],
    )

    result = menu_repo._build_firmware_commit_times(
        cache_manager, github_token="token", allow_env_token=True
    )

    assert result == {}


def test_build_firmware_commit_times_exception(mocker):
    """Test prerelease commit time mapping when commit fetch fails."""
    from fetchtastic.download.cache import CacheManager

    cache_manager = CacheManager()
    mocker.patch(
        "fetchtastic.menu_repo.PrereleaseHistoryManager.fetch_recent_repo_commits",
        side_effect=requests.RequestException("boom"),
    )

    result = menu_repo._build_firmware_commit_times(
        cache_manager, github_token=None, allow_env_token=True
    )

    assert result == {}


def test_build_firmware_commit_times_success(mocker):
    """Test prerelease commit time mapping success path."""
    from datetime import datetime, timezone

    from fetchtastic.download.cache import CacheManager

    cache_manager = CacheManager()
    mocker.patch(
        "fetchtastic.menu_repo.PrereleaseHistoryManager.fetch_recent_repo_commits",
        return_value=[
            {
                "commit": {
                    "message": "2.7.14.e959000 meshtastic/firmware@e959000",
                    "committer": {"date": "2025-01-02T00:00:00Z"},
                }
            }
        ],
    )
    expected = {"firmware-2.7.14.e959000": datetime(2025, 1, 2, tzinfo=timezone.utc)}
    mocker.patch(
        "fetchtastic.menu_repo.PrereleaseHistoryManager.extract_prerelease_directory_timestamps",
        return_value=expected,
    )

    result = menu_repo._build_firmware_commit_times(
        cache_manager, github_token="token", allow_env_token=False
    )

    assert result == expected


def test_run_repository_downloader_menu_handles_exception(mocker):
    """Test that run_repository_downloader_menu logs and returns None on errors."""
    mocker.patch("fetchtastic.menu_repo.run_menu", side_effect=RuntimeError("boom"))
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    result = menu_repo.run_repository_downloader_menu({"BASE_DIR": "/tmp"})

    assert result is None
    mock_logger.error.assert_called_once()


def test_run_repository_downloader_menu_no_selection(mocker):
    """Test run_repository_downloader_menu when no files are selected."""
    mocker.patch("fetchtastic.menu_repo.run_menu", return_value=None)
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    result = menu_repo.run_repository_downloader_menu({"DOWNLOAD_DIR": "/tmp"})

    assert result is None
    mock_logger.info.assert_called_once_with("No files selected for download.")


def test_fetch_directory_contents(mocker):
    """Test the backward compatibility function for fetching directory contents."""
    mock_contents = [
        {"name": "dir1", "type": "dir"},
        {"name": "file1.txt", "type": "file"},
        {"name": "file2.txt", "type": "file"},
    ]
    mocker.patch(
        "fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_contents
    )

    files = menu_repo.fetch_directory_contents("some_directory")

    assert len(files) == 2
    assert all(item["type"] == "file" for item in files)
    assert files[0]["name"] == "file1.txt"
    assert files[1]["name"] == "file2.txt"


def test_select_item_empty_items():
    """Test select_item with empty items list."""
    result = menu_repo.select_item([])
    assert result is None


def test_select_item_with_current_path(mocker):
    """Test select_item with current path (shows go back and current directory)."""
    mock_pick = mocker.patch("fetchtastic.menu_repo._pick_menu")
    items = [{"name": "file1.txt", "path": "file1.txt", "type": "file"}]

    # Test selecting current directory when in a subdirectory
    mock_pick.return_value = (
        Option(
            label="[Select files in this directory (1 file)]",
            value={"type": "current"},
        ),
        1,
    )
    selected = menu_repo.select_item(items, current_path="some/path")

    assert selected["type"] == "current"


def test_select_files_empty_files():
    """Test select_files with empty files list."""
    result = menu_repo.select_files([])
    assert result is None


def test_run_menu_no_items(mocker):
    """Test run_menu when no items are found."""
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=[])
    mock_print = mocker.patch("builtins.print")

    result = menu_repo.run_menu()

    assert result is None
    mock_print.assert_any_call("No items found in the repository. Exiting.")


def test_run_menu_uses_cache_with_empty_config(mocker):
    """Test run_menu initializes cache with empty config dict."""
    build_mock = mocker.patch(
        "fetchtastic.menu_repo._build_firmware_commit_times", return_value={}
    )
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=[])
    mocker.patch("builtins.print")

    result = menu_repo.run_menu({})

    assert result is None
    build_mock.assert_called_once()


def test_run_menu_quit_immediately(mocker):
    """Test run_menu when user quits immediately."""
    mock_items = [{"name": "dir1", "type": "dir", "path": "dir1"}]
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_items)
    mocker.patch("fetchtastic.menu_repo.select_item", return_value={"type": "quit"})
    mock_print = mocker.patch("builtins.print")

    result = menu_repo.run_menu()

    assert result is None
    mock_print.assert_any_call("Exiting repository browser.")


def test_run_menu_go_back_navigation(mocker):
    """Test run_menu navigation with going back to parent directory."""
    mock_items = [{"name": "dir1", "type": "dir", "path": "dir1"}]
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_items)

    # First go back, then quit
    select_calls = [{"type": "back"}, {"type": "quit"}]
    mocker.patch("fetchtastic.menu_repo.select_item", side_effect=select_calls)
    mock_print = mocker.patch("builtins.print")

    result = menu_repo.run_menu()

    assert result is None
    mock_print.assert_any_call("Exiting repository browser.")


def test_run_menu_user_cancels_file_selection(mocker):
    """Test run_menu when user cancels file selection."""
    mock_items_root = [{"name": "dir1", "type": "dir", "path": "dir1"}]
    mock_items_subdir = [
        {
            "name": "file1.txt",
            "type": "file",
            "path": "dir1/file1.txt",
            "download_url": "url1",
        }
    ]

    fetch_calls = [
        mock_items_root,
        mock_items_subdir,
        mock_items_subdir,
    ]  # Called multiple times
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", side_effect=fetch_calls)

    # Select directory, then open current directory file selection, then quit
    select_item_calls = [
        {"name": "dir1", "type": "dir", "path": "dir1"},
        {"type": "current"},
        {"type": "quit"},
    ]
    mocker.patch("fetchtastic.menu_repo.select_item", side_effect=select_item_calls)

    # User cancels file selection
    mock_select_files = mocker.patch(
        "fetchtastic.menu_repo.select_files", return_value=None
    )

    result = menu_repo.run_menu()

    assert result is None
    mock_select_files.assert_called_once()


def test_run_menu_exception_handling(mocker):
    """Test run_menu exception handling."""
    mocker.patch(
        "fetchtastic.menu_repo.fetch_repo_contents", side_effect=Exception("Test error")
    )
    mock_print = mocker.patch("builtins.print")

    result = menu_repo.run_menu()

    assert result is None
    mock_print.assert_any_call("An error occurred: Test error")


def test_run_menu_complex_path_navigation(mocker):
    """Test run_menu with complex path navigation (going back from nested path)."""
    mock_items = [{"name": "dir1", "type": "dir", "path": "dir1"}]
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_items)

    # Simulate being in a nested path and going back
    select_calls = [{"type": "back"}, {"type": "quit"}]
    mocker.patch("fetchtastic.menu_repo.select_item", side_effect=select_calls)

    result = menu_repo.run_menu()
    assert result is None


def test_fetch_repo_contents_debug_logging(mocker, mock_repo_contents):
    """Test debug logging in fetch_repo_contents."""
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = mock_repo_contents
    mock_make_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    # Should log debug message about fetched items
    mock_logger.debug.assert_called_with(
        f"Fetched {len(mock_repo_contents)} items from repository"
    )
    assert len(items) == 6  # Filtered items


def test_fetch_repo_contents_debug_logging_no_list_response(mocker):
    """Test debug logging in fetch_repo_contents when response is not a list."""
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"not": "a list"}
    mock_make_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    # Should not log debug message since response is not a list
    mock_logger.debug.assert_not_called()
    assert items == []


def test_fetch_repo_contents_http_error(mocker):
    """Test HTTP error handling in fetch_repo_contents."""
    mock_make_request = mocker.patch("fetchtastic.menu_repo.make_github_api_request")
    mock_make_request.side_effect = requests.HTTPError("404 Not Found")
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.warning.assert_called_once()
    args, _ = mock_logger.warning.call_args
    assert "HTTP error fetching repository contents from GitHub API" in args[0]


def test_run_menu_select_item_none(mocker):
    """Test run_menu when select_item returns None."""
    mock_items = [{"name": "dir1", "type": "dir", "path": "dir1"}]
    mocker.patch("fetchtastic.menu_repo.fetch_repo_contents", return_value=mock_items)
    mocker.patch("fetchtastic.menu_repo.select_item", return_value=None)

    result = menu_repo.run_menu()

    assert result is None


def test_process_repo_contents_invalid_version():
    """Test _process_repo_contents with invalid version in firmware directory name."""
    # Create mock data with invalid version directory
    contents = [
        {
            "name": "firmware-invalid-version",
            "path": "firmware-invalid-version",
            "type": "dir",
        },
        {
            "name": "firmware-2.7.4.c1f4f79",
            "path": "firmware-2.7.4.c1f4f79",
            "type": "dir",
        },
    ]

    items = menu_repo._process_repo_contents(contents)

    # Should still process both items
    assert len(items) == 2
    # Valid firmware version should sort ahead of invalid entries
    assert items[0]["name"] == "firmware-2.7.4.c1f4f79"
    assert items[1]["name"] == "firmware-invalid-version"


def test_process_repo_contents_sort_by_commit_time():
    """Test _process_repo_contents sorting using commit timestamps."""
    from datetime import datetime, timezone

    contents = [
        {
            "name": "firmware-2.7.4.c1f4f79",
            "path": "firmware-2.7.4.c1f4f79",
            "type": "dir",
        },
        {
            "name": "firmware-2.7.4.ddee111",
            "path": "firmware-2.7.4.ddee111",
            "type": "dir",
        },
    ]

    commit_times = {
        "firmware-2.7.4.ddee111": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "firmware-2.7.4.c1f4f79": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }

    items = menu_repo._process_repo_contents(
        contents, firmware_commit_times=commit_times
    )

    assert items[0]["name"] == "firmware-2.7.4.ddee111"
    assert items[1]["name"] == "firmware-2.7.4.c1f4f79"
