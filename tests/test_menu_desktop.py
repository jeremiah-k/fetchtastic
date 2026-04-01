import pytest

from fetchtastic import menu_desktop
from fetchtastic.constants import MESHTASTIC_DESKTOP_RELEASES_URL

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]


def test_fetch_desktop_assets_filters_and_uses_desktop_releases_url(mocker):
    """fetch_desktop_assets should filter to desktop installers and use desktop URL."""
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = [
        {
            "assets": [
                {"name": "Meshtastic-2.7.14.dmg"},
                {"name": "Meshtastic-2.7.14.AppImage"},
                {"name": "notes.txt"},
            ]
        }
    ]
    mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
    mock_request.return_value = mock_response

    result = menu_desktop.fetch_desktop_assets()

    assert result == ["Meshtastic-2.7.14.AppImage", "Meshtastic-2.7.14.dmg"]
    mock_request.assert_called_once_with(MESHTASTIC_DESKTOP_RELEASES_URL)


def test_select_assets_uses_pick_indices(mocker):
    """select_assets should map selected options by index and skip headers."""
    mock_pick = mocker.patch("fetchtastic.menu_desktop.pick")
    mock_pattern = mocker.patch(
        "fetchtastic.menu_desktop.extract_wildcard_pattern",
        return_value="*Meshtastic*dmg*",
    )
    mock_pick.return_value = [("  Meshtastic-2.7.14.dmg", 1)]

    result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg"])

    assert result == {"selected_assets": ["*Meshtastic*dmg*"]}
    mock_pattern.assert_called_once_with("Meshtastic-2.7.14.dmg")


def test_run_menu_returns_none_when_no_assets(mocker):
    """run_menu should return None without calling select_assets when no assets exist."""
    mock_fetch = mocker.patch(
        "fetchtastic.menu_desktop.fetch_desktop_assets", return_value=[]
    )
    mock_select = mocker.patch("fetchtastic.menu_desktop.select_assets")

    result = menu_desktop.run_menu()

    assert result is None
    mock_fetch.assert_called_once()
    mock_select.assert_not_called()
