from unittest.mock import patch

import pytest

from fetchtastic import menu_repo


@pytest.fixture
def mock_repo_contents():
    """Provides a mock list of items from the GitHub contents API."""
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
