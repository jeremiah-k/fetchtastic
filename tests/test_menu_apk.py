import json

import pytest

from fetchtastic import menu_apk

pytestmark = [pytest.mark.user_interface]


@pytest.fixture
def mock_apk_assets():
    """
    Provide a list of dictionaries representing release assets for tests.

    Each dictionary contains a "name" key with a filename. The returned list includes three APK filenames and one non-APK file to exercise asset filtering in tests.

    Returns:
        assets (list[dict]): List of asset dictionaries, e.g.:
            [
                {"name": "meshtastic-app-release-2.7.4.apk"},
                {"name": "meshtastic-app-debug-2.7.4.apk"},
                {"name": "nRF_Connect_Device_Manager-release-2.7.4.apk"},
                {"name": "some-other-file.txt"},
            ]
    """
    return [
        {"name": "meshtastic-app-release-2.7.4.apk"},
        {"name": "meshtastic-app-debug-2.7.4.apk"},
        {"name": "nRF_Connect_Device_Manager-release-2.7.4.apk"},
        {"name": "some-other-file.txt"},
    ]


@pytest.fixture
def mock_apk_assets_mixed_case():
    """
    Provide mock release assets including APK filenames with mixed-case extensions for testing.

    Returns:
        list[dict]: A list of asset dictionaries with a 'name' key. Includes APK filenames with lowercase, uppercase, and mixed-case extensions, plus a non-APK file.
    """
    return [
        {"name": "meshtastic-app-release-2.7.4.apk"},
        {"name": "meshtastic-app-debug-2.7.4.APK"},  # Uppercase extension
        {"name": "meshtastic-app-beta-2.7.4.Apk"},  # Mixed case extension
        {"name": "some-other-file.txt"},
    ]


def test_fetch_apk_assets(mocker, mock_apk_assets):
    """Test fetching APK assets from GitHub."""
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"assets": mock_apk_assets}]
    mock_make_request = mocker.patch("fetchtastic.menu_apk.make_github_api_request")
    mock_make_request.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()

    assert len(assets) == 3
    assert "meshtastic-app-release-2.7.4.apk" in assets
    assert "meshtastic-app-debug-2.7.4.apk" in assets
    assert "nRF_Connect_Device_Manager-release-2.7.4.apk" in assets
    # Check sorting
    assert assets[0] == "meshtastic-app-debug-2.7.4.apk"


def test_fetch_apk_assets_case_insensitive(mocker, mock_apk_assets_mixed_case):
    """Test fetching APK assets with case-insensitive extension matching."""
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"assets": mock_apk_assets_mixed_case}]
    mock_make_request = mocker.patch("fetchtastic.menu_apk.make_github_api_request")
    mock_make_request.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()

    assert len(assets) == 3
    assert "meshtastic-app-release-2.7.4.apk" in assets
    assert "meshtastic-app-debug-2.7.4.APK" in assets
    assert "meshtastic-app-beta-2.7.4.Apk" in assets
    assert "some-other-file.txt" not in assets


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("meshtastic-app-release-2.3.2.apk", "meshtastic-app-release.apk"),
        ("app-debug-1.0.0.apk", "app-debug.apk"),
    ],
)
def test_extract_base_name(filename, expected):
    """Test the base name extraction logic."""
    assert menu_apk.extract_base_name(filename) == expected


def test_select_assets(mocker):
    """Test the user asset selection logic."""
    mock_pick = mocker.patch("fetchtastic.menu_apk.pick")
    assets = ["meshtastic-app-release-2.3.2.apk", "meshtastic-app-debug-2.3.2.apk"]

    # 1. User selects one asset
    mock_pick.return_value = [("meshtastic-app-release-2.3.2.apk", 0)]
    selected = menu_apk.select_assets(assets)
    assert selected == {"selected_assets": ["meshtastic-app-release.apk"]}

    # 2. User selects nothing
    mock_pick.return_value = []
    selected = menu_apk.select_assets(assets)
    assert selected is None


def test_run_menu(mocker):
    """Test the main menu orchestration."""
    mock_fetch = mocker.patch(
        "fetchtastic.menu_apk.fetch_apk_assets", return_value=["asset1.apk"]
    )
    mock_select = mocker.patch(
        "fetchtastic.menu_apk.select_assets",
        return_value={"selected_assets": ["base-pattern"]},
    )

    # 1. Successful flow
    result = menu_apk.run_menu()
    assert result == {"selected_assets": ["base-pattern"]}
    mock_fetch.assert_called_once()
    mock_select.assert_called_once_with(["asset1.apk"])

    # 2. User selects nothing
    mock_select.return_value = None
    result = menu_apk.run_menu()
    assert result is None


def test_fetch_apk_assets_error_handling(mocker):
    """Test error handling in fetch_apk_assets."""
    # Test JSON decode error
    mock_response = mocker.MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_make_request = mocker.patch("fetchtastic.menu_apk.make_github_api_request")
    mock_make_request.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()
    assert assets == []

    # Test non-list response
    mock_response.json.return_value = {"not": "a list"}
    mock_make_request.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()
    assert assets == []

    # Test empty releases list
    mock_response.json.return_value = []
    mock_make_request.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()
    assert assets == []


def test_run_menu_exception_handling(mocker):
    """Test exception handling in run_menu."""
    # Test exception during fetch
    mocker.patch(
        "fetchtastic.menu_apk.fetch_apk_assets", side_effect=Exception("Network error")
    )

    result = menu_apk.run_menu()
    assert result is None


def test_fetch_apk_assets_debug_logging(mocker, mock_apk_assets):
    """Test debug logging in fetch_apk_assets."""
    mock_api_request = mocker.patch("fetchtastic.menu_apk.make_github_api_request")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"assets": mock_apk_assets}]
    mock_api_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_apk.logger")

    assets = menu_apk.fetch_apk_assets()

    # Should log debug message about fetched releases
    mock_logger.debug.assert_called_with("Fetched 1 Android releases from GitHub API")
    assert len(assets) == 3


def test_fetch_apk_assets_debug_logging_no_list_response(mocker):
    """Test debug logging in fetch_apk_assets when response is not a list."""
    mock_api_request = mocker.patch("fetchtastic.menu_apk.make_github_api_request")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"not": "a list"}
    mock_api_request.return_value = mock_response
    mock_logger = mocker.patch("fetchtastic.menu_apk.logger")

    assets = menu_apk.fetch_apk_assets()

    # Should not log debug message since response is not a list
    mock_logger.debug.assert_not_called()
    assert assets == []
