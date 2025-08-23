import pytest

from fetchtastic import menu_apk


@pytest.fixture
def mock_apk_assets():
    """
    Return a list of dictionaries simulating release assets.

    Each dict contains a "name" key. The list includes three APK filenames and one non-APK file to emulate mixed release assets for tests.
    """
    return [
        {"name": "meshtastic-app-release-2.7.4.apk"},
        {"name": "meshtastic-app-debug-2.7.4.apk"},
        {"name": "nRF_Connect_Device_Manager-release-2.7.4.apk"},
        {"name": "some-other-file.txt"},
    ]


def test_fetch_apk_assets(mocker, mock_apk_assets):
    """Test fetching APK assets from GitHub."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    # The API returns a list of releases, we care about the first one's assets
    mock_response.json.return_value = [{"assets": mock_apk_assets}]
    mock_get.return_value = mock_response

    assets = menu_apk.fetch_apk_assets()

    assert len(assets) == 3
    assert "meshtastic-app-debug-2.7.4.apk" in assets
    assert "meshtastic-app-release-2.7.4.apk" in assets
    assert "nRF_Connect_Device_Manager-release-2.7.4.apk" in assets
    # Check sorting
    assert assets[0] == "meshtastic-app-debug-2.7.4.apk"


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
