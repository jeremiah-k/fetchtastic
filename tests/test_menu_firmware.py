import pytest

from fetchtastic import menu_firmware


@pytest.fixture
def mock_firmware_assets():
    """
    Test fixture: returns a list of mock asset records.

    Each item is a dict with a "name" key representing a filename. The list includes three firmware ZIP filenames and one non-firmware file (used to verify filtering/handling in tests).

    Returns:
        list[dict]: Mock asset records, e.g. [{"name": "firmware-rak4631-2.7.4.c1f4f79.zip"}, ...].
    """
    return [
        {"name": "firmware-rak4631-2.7.4.c1f4f79.zip"},
        {"name": "firmware-tbeam-2.7.4.c1f4f79.zip"},
        {"name": "firmware-heltec-v3-2.7.4.c1f4f79.zip"},
        {"name": "some-other-file.txt"},
    ]


def test_fetch_firmware_assets(mocker, mock_firmware_assets):
    """Test fetching firmware assets from GitHub."""
    mock_get = mocker.patch("requests.get")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [{"assets": mock_firmware_assets}]
    mock_get.return_value = mock_response

    assets = menu_firmware.fetch_firmware_assets()

    assert len(assets) == 4  # All files are kept, unlike the apk menu
    assert "firmware-rak4631-2.7.4.c1f4f79.zip" in assets
    assert "firmware-tbeam-2.7.4.c1f4f79.zip" in assets
    assert "some-other-file.txt" in assets


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("firmware-rak4631-2.7.4.c1f4f79.zip", "firmware-rak4631.zip"),
        ("firmware-heltec-v3-2.7.4.c1f4f79.zip", "firmware-heltec-v3.zip"),
        ("meshtasticd_2.5.13.1a06f88_amd64.deb", "meshtasticd_amd64.deb"),
    ],
)
def test_extract_base_name(filename, expected):
    """Test the base name extraction logic."""
    assert menu_firmware.extract_base_name(filename) == expected


def test_select_assets(mocker):
    """Test the user asset selection logic."""
    mock_pick = mocker.patch("fetchtastic.menu_firmware.pick")
    assets = ["firmware-rak4631-2.7.4.c1f4f79.zip", "firmware-tbeam-2.7.4.c1f4f79.zip"]

    # 1. User selects one asset
    mock_pick.return_value = [("firmware-rak4631-2.7.4.c1f4f79.zip", 0)]
    selected = menu_firmware.select_assets(assets)
    assert selected == {"selected_assets": ["firmware-rak4631.zip"]}

    # 2. User selects nothing
    mock_pick.return_value = []
    selected = menu_firmware.select_assets(assets)
    assert selected is None


def test_run_menu(mocker):
    """Test the main menu orchestration."""
    mock_fetch = mocker.patch(
        "fetchtastic.menu_firmware.fetch_firmware_assets", return_value=["asset1.zip"]
    )
    mock_select = mocker.patch(
        "fetchtastic.menu_firmware.select_assets",
        return_value={"selected_assets": ["base-pattern"]},
    )

    # 1. Successful flow
    result = menu_firmware.run_menu()
    assert result == {"selected_assets": ["base-pattern"]}
    mock_fetch.assert_called_once()
    mock_select.assert_called_once_with(["asset1.zip"])

    # 2. User selects nothing
    mock_select.return_value = None
    result = menu_firmware.run_menu()
    assert result is None
