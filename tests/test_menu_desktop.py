import json

import pytest
import requests

from fetchtastic import menu_desktop
from fetchtastic.constants import MESHTASTIC_DESKTOP_RELEASES_URL

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]


class TestGetPlatformLabel:
    def test_macos_extensions(self):
        assert menu_desktop._get_platform_label("file.dmg") == "macOS"
        assert menu_desktop._get_platform_label("FILE.DMG") == "macOS"

    def test_windows_extensions(self):
        assert menu_desktop._get_platform_label("file.msi") == "Windows"
        assert menu_desktop._get_platform_label("file.exe") == "Windows"
        assert menu_desktop._get_platform_label("FILE.MSI") == "Windows"

    def test_linux_extensions(self):
        assert menu_desktop._get_platform_label("file.deb") == "Linux"
        assert menu_desktop._get_platform_label("file.rpm") == "Linux"
        assert menu_desktop._get_platform_label("file.appimage") == "Linux"
        assert menu_desktop._get_platform_label("file.AppImage") == "Linux"
        assert menu_desktop._get_platform_label("FILE.APPIMAGE") == "Linux"

    def test_unrecognized_extension(self):
        assert menu_desktop._get_platform_label("file.txt") is None
        assert menu_desktop._get_platform_label("file.zip") is None


class TestExtractWildcardPattern:
    def test_basic_version(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14.dmg")
        assert result == "meshtastic.dmg"

    def test_linux_appimage(self):
        result = menu_desktop.extract_wildcard_pattern(
            "Meshtastic-2.7.14-linux-x86_64.AppImage"
        )
        assert result == "meshtastic-linux-x86_64.appimage"

    def test_windows_msi(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic_x64_2.7.14.msi")
        assert result == "meshtastic_x64.msi"

    def test_prerelease_version_rc(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14-rc1.dmg")
        assert result == "meshtastic.dmg"

    def test_prerelease_version_dev(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14.dev1.dmg")
        assert result == "meshtastic.dmg"

    def test_prerelease_version_beta(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14beta1.dmg")
        assert result == "meshtastic.dmg"

    def test_prerelease_version_alpha(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14-alpha1.dmg")
        assert result == "meshtastic.dmg"

    def test_prerelease_version_b(self):
        result = menu_desktop.extract_wildcard_pattern("Meshtastic-2.7.14b1.dmg")
        assert result == "meshtastic.dmg"


class TestFetchDesktopAssetsErrorHandling:
    def test_request_exception(self, mocker):
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.side_effect = requests.RequestException("Network error")
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.fetch_desktop_assets()

        assert result == []
        mock_logger.error.assert_called_once()

    def test_json_decode_error(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.fetch_desktop_assets()

        assert result == []
        mock_logger.error.assert_called_once()

    def test_non_list_response(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {"not": "a list"}
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.fetch_desktop_assets()

        assert result == []
        mock_logger.warning.assert_called_once()

    def test_empty_releases_list(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = []
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.fetch_desktop_assets()

        assert result == []
        mock_logger.warning.assert_called_once()

    def test_invalid_assets_data(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = [{"assets": "not a list"}]
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.fetch_desktop_assets()

        assert result == []
        mock_logger.warning.assert_called_once()

    def test_asset_not_dict(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = [{"assets": ["not a dict"]}]
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response

        result = menu_desktop.fetch_desktop_assets()

        assert result == []

    def test_asset_missing_name(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = [{"assets": [{"size": 123}]}]
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response

        result = menu_desktop.fetch_desktop_assets()

        assert result == []

    def test_debug_logging(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = [
            {"assets": [{"name": "Meshtastic-2.7.14.dmg"}]}
        ]
        mock_request = mocker.patch("fetchtastic.menu_desktop.make_github_api_request")
        mock_request.return_value = mock_response
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        menu_desktop.fetch_desktop_assets()

        mock_logger.debug.assert_called_with("Fetched 1 releases from GitHub API")


class TestSelectAssets:
    def test_other_category_handling(self, mocker):
        mock_pick = mocker.patch("fetchtastic.menu_desktop.pick")
        mock_pattern = mocker.patch(
            "fetchtastic.menu_desktop.extract_wildcard_pattern",
            return_value="unknown.tar.gz",
        )
        mock_pick.return_value = [("  unknown.tar.gz", 3)]

        result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg", "unknown.tar.gz"])

        assert result == {"selected_assets": ["unknown.tar.gz"]}
        mock_pattern.assert_called_once_with("unknown.tar.gz")

    def test_index_out_of_range(self, mocker):
        mock_pick = mocker.patch("fetchtastic.menu_desktop.pick")
        mock_pick.return_value = [("  Meshtastic-2.7.14.dmg", 999)]

        result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg"])

        assert result is None

    def test_empty_asset_name_in_option_map(self, mocker):
        mock_pick = mocker.patch("fetchtastic.menu_desktop.pick")
        mock_pick.return_value = [("--- macOS ---", 0)]

        result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg"])

        assert result is None

    def test_no_selection_prints_message(self, mocker, capsys):
        mock_pick = mocker.patch("fetchtastic.menu_desktop.pick")
        mock_pick.return_value = []

        result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg"])

        assert result is None
        captured = capsys.readouterr()
        assert "No desktop files selected" in captured.out


class TestRunMenuExceptionHandling:
    def test_successful_flow(self, mocker):
        mock_fetch = mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            return_value=["Meshtastic-2.7.14.dmg"],
        )
        mock_select = mocker.patch(
            "fetchtastic.menu_desktop.select_assets",
            return_value={"selected_assets": ["meshtastic.dmg"]},
        )

        result = menu_desktop.run_menu()

        assert result == {"selected_assets": ["meshtastic.dmg"]}
        mock_fetch.assert_called_once()
        mock_select.assert_called_once_with(["Meshtastic-2.7.14.dmg"])

    def test_select_assets_returns_none(self, mocker):
        mock_fetch = mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            return_value=["Meshtastic-2.7.14.dmg"],
        )
        mock_select = mocker.patch(
            "fetchtastic.menu_desktop.select_assets", return_value=None
        )

        result = menu_desktop.run_menu()

        assert result is None
        mock_fetch.assert_called_once()
        mock_select.assert_called_once()

    def test_json_decode_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=json.JSONDecodeError("Invalid JSON", "", 0),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_value_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=ValueError("Bad value"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_request_exception(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=requests.RequestException("Network error"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_os_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=OSError("IO error"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_type_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=TypeError("Type error"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_key_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=KeyError("missing_key"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_attribute_error(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=AttributeError("Attribute error"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()

    def test_generic_exception(self, mocker):
        mocker.patch(
            "fetchtastic.menu_desktop.fetch_desktop_assets",
            side_effect=RuntimeError("Unexpected error"),
        )
        mock_logger = mocker.patch("fetchtastic.menu_desktop.logger")

        result = menu_desktop.run_menu()

        assert result is None
        mock_logger.exception.assert_called_once()


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
        return_value="meshtastic.dmg",
    )
    mock_pick.return_value = [("  Meshtastic-2.7.14.dmg", 1)]

    result = menu_desktop.select_assets(["Meshtastic-2.7.14.dmg"])

    assert result == {"selected_assets": ["meshtastic.dmg"]}
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
