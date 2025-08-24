import pytest
import requests

from fetchtastic import menu_repo


@pytest.fixture
def mock_repo_contents():
    """
    Return a mock list of items shaped like the GitHub repository contents API.

    The list includes a mix of directories and files used by tests:
    - Directories: three firmware/event entries and one `.git` (the `.git` entry is intended to be excluded by the fetching logic).
    - Files: `index.html`, `meshtastic-deb.asc`, and `README.md` (the README and some other files are expected to be filtered out by the production logic).

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
        },  # Should be excluded
    ]


def test_fetch_repo_contents(mocker, mock_repo_contents):
    """Test fetching and processing of repository contents."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = mock_repo_contents
    mock_get.return_value = mock_response

    items = menu_repo.fetch_repo_contents()

    # Check filtering - should be 4 items (3 dirs, 1 file) - README.md and meshtastic-deb.asc filtered
    assert len(items) == 4
    assert not any(item["name"] == ".git" for item in items)
    assert not any(item["name"] == "README.md" for item in items)

    # Check sorting
    assert (
        items[0]["name"] == "firmware-2.7.4.c1f4f79"
    )  # Firmware dirs sorted descending
    assert items[1]["name"] == "firmware-2.7.3.cf574c7"
    assert items[2]["name"] == "event"  # Other dirs sorted ascending
    assert items[3]["name"] == "index.html"  # Files sorted ascending


def test_select_item(mocker):
    """Test the user item selection menu logic."""
    # Patch where pick is looked up, which is in the menu_repo module
    mock_pick = mocker.patch("fetchtastic.menu_repo.pick")
    items = [
        {"name": "dir1", "path": "dir1", "type": "dir"},
        {"name": "file1.txt", "path": "file1.txt", "type": "file"},
    ]

    # 1. Select a directory
    mock_pick.return_value = ("dir1/", 0)
    selected = menu_repo.select_item(items)
    assert selected["type"] == "dir"
    assert selected["name"] == "dir1"

    # 2. Select "Go back"
    mock_pick.return_value = ("[Go back to parent directory]", 0)
    selected = menu_repo.select_item(items, current_path="some/path")
    assert selected["type"] == "back"

    # 3. Select "Quit"
    mock_pick.return_value = ("[Quit]", 1)  # Index depends on options
    selected = menu_repo.select_item(items)
    assert selected["type"] == "quit"


def test_select_files(mocker):
    """Test the user file selection menu logic."""
    # Patch where pick is looked up
    mock_pick = mocker.patch("fetchtastic.menu_repo.pick")
    files = [
        {"name": "file1.txt", "download_url": "url1"},
        {"name": "file2.txt", "download_url": "url2"},
    ]

    # 1. Select some files
    mock_pick.return_value = [("file1.txt", 0), ("file2.txt", 1)]
    selected = menu_repo.select_files(files)
    assert len(selected) == 2
    assert selected[0]["name"] == "file1.txt"

    # 2. Select "Quit"
    mock_pick.return_value = [("[Quit]", 2)]
    selected = menu_repo.select_files(files)
    assert selected is None

    # 3. Select nothing
    mock_pick.return_value = []
    selected = menu_repo.select_files(files)
    assert selected is None


def test_fetch_repo_contents_with_path(mocker, mock_repo_contents):
    """Test fetching repository contents with a specific path."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = mock_repo_contents
    mock_get.return_value = mock_response

    menu_repo.fetch_repo_contents("firmware-2.7.4.c1f4f79")

    # Verify the URL was constructed correctly
    expected_url = "https://api.github.com/repos/meshtastic/meshtastic.github.io/contents/firmware-2.7.4.c1f4f79"
    mock_get.assert_called_once_with(expected_url, timeout=10)


def test_fetch_repo_contents_request_exception(mocker):
    """Test handling of request exceptions."""
    mock_get = mocker.patch("requests.get")
    mock_get.side_effect = requests.RequestException("Network error")
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Error fetching repository contents from GitHub API" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_contents_json_error(mocker):
    """Test handling of JSON parsing errors."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_get.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Error parsing repository contents response" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_contents_key_error(mocker):
    """Test handling of missing keys in response."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"invalid": "data"}]  # Missing required keys
    mock_get.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_repo.logger")

    items = menu_repo.fetch_repo_contents()

    assert items == []
    mock_logger.error.assert_called_once()
    assert "Error parsing repository contents response" in str(
        mock_logger.error.call_args
    )


def test_fetch_repo_contents_unexpected_error(mocker):
    """Test handling of unexpected errors."""
    mock_get = mocker.patch("requests.get")
    mock_get.side_effect = Exception("Unexpected error")
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
    """Test select_item with current path (shows go back option)."""
    mock_pick = mocker.patch("fetchtastic.menu_repo.pick")
    items = [{"name": "file1.txt", "path": "file1.txt", "type": "file"}]

    # Test selecting a file when in a subdirectory
    mock_pick.return_value = ("file1.txt", 1)  # Index 1 because "Go back" is at index 0
    selected = menu_repo.select_item(items, current_path="some/path")

    assert selected["type"] == "file"
    assert selected["name"] == "file1.txt"


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

    # Select directory, then quit from file selection, then quit from directory
    select_item_calls = [
        {"name": "dir1", "type": "dir", "path": "dir1"},
        {"type": "quit"},
    ]
    mocker.patch("fetchtastic.menu_repo.select_item", side_effect=select_item_calls)

    # User cancels file selection
    mocker.patch("fetchtastic.menu_repo.select_files", return_value=None)

    result = menu_repo.run_menu()

    assert result is None


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
