"""
Comprehensive tests for menu_apk and menu_firmware modules.

Tests additional error scenarios, edge cases, and error handling
for both APK and firmware menu functionality including:
- Network errors and API failures
- Malformed data handling
- Edge cases with empty or invalid data
- Exception propagation and error recovery
"""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from fetchtastic import menu_apk, menu_firmware

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]


class TestMenuAPKErrorHandling:
    """Test error handling in menu_apk module."""

    def test_fetch_apk_assets_network_error(self):
        """Test handling of network errors when fetching APK assets."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_req.side_effect = requests.RequestException("Connection failed")

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_timeout(self):
        """Test handling of timeout when fetching APK assets."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_req.side_effect = requests.Timeout("Request timeout")

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_invalid_json(self):
        """Test handling of invalid JSON response."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.side_effect = json.JSONDecodeError("Invalid", "", 0)
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_missing_assets_key(self):
        """Test handling when release has no assets key."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            # Release without "assets" key
            mock_response.json.return_value = [{"tag_name": "v2.5.0"}]
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_none_assets(self):
        """Test handling when assets is None."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [{"assets": None}]
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_assets_not_list(self):
        """Test handling when assets is not a list."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [{"assets": "not a list"}]
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_run_menu_json_decode_error(self):
        """Test run_menu handles JSON decode errors."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = json.JSONDecodeError("Invalid", "", 0)

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_value_error(self):
        """Test run_menu handles ValueError."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = ValueError("Invalid value")

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_request_exception(self):
        """Test run_menu handles requests.RequestException."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = requests.RequestException("Network error")

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_os_error(self):
        """Test run_menu handles OSError."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = OSError("File system error")

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_type_error(self):
        """Test run_menu handles TypeError."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = TypeError("Type error")

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_key_error(self):
        """Test run_menu handles KeyError."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = KeyError("Missing key")

            result = menu_apk.run_menu()

            assert result is None

    def test_run_menu_attribute_error(self):
        """Test run_menu handles AttributeError."""
        with patch("fetchtastic.menu_apk.fetch_apk_assets") as mock_fetch:
            mock_fetch.side_effect = AttributeError("Missing attribute")

            result = menu_apk.run_menu()

            assert result is None


class TestMenuFirmwareErrorHandling:
    """Test error handling in menu_firmware module."""

    def test_fetch_firmware_assets_network_error(self):
        """Test handling of network errors when fetching firmware assets."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_req.side_effect = requests.RequestException("Connection failed")

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_timeout(self):
        """Test handling of timeout when fetching firmware assets."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_req.side_effect = requests.Timeout("Request timeout")

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_invalid_json(self):
        """Test handling of invalid JSON response."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.side_effect = json.JSONDecodeError("Invalid", "", 0)
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_missing_assets_key(self):
        """Test handling when release has no assets key."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [{"tag_name": "v2.5.0"}]
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_none_assets(self):
        """Test handling when assets is None."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [{"assets": None}]
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_assets_not_list(self):
        """Test handling when assets is not a list."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [{"assets": "not a list"}]
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_run_menu_json_decode_error(self):
        """Test run_menu handles JSON decode errors."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = json.JSONDecodeError("Invalid", "", 0)

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_value_error(self):
        """Test run_menu handles ValueError."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = ValueError("Invalid value")

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_request_exception(self):
        """Test run_menu handles requests.RequestException."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = requests.RequestException("Network error")

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_os_error(self):
        """Test run_menu handles OSError."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = OSError("File system error")

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_type_error(self):
        """Test run_menu handles TypeError."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = TypeError("Type error")

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_key_error(self):
        """Test run_menu handles KeyError."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = KeyError("Missing key")

            result = menu_firmware.run_menu()

            assert result is None

    def test_run_menu_attribute_error(self):
        """Test run_menu handles AttributeError."""
        with patch("fetchtastic.menu_firmware.fetch_firmware_assets") as mock_fetch:
            mock_fetch.side_effect = AttributeError("Missing attribute")

            result = menu_firmware.run_menu()

            assert result is None


class TestMenuAPKEdgeCases:
    """Test edge cases for menu_apk module."""

    def test_fetch_apk_assets_empty_response(self):
        """Test fetching when API returns empty list."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = []
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_no_apk_files(self):
        """Test fetching when release has no APK files."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [
                {
                    "assets": [
                        {"name": "firmware.bin"},
                        {"name": "readme.txt"},
                    ]
                }
            ]
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == []

    def test_fetch_apk_assets_missing_name(self):
        """Test handling of assets without name field."""
        with patch("fetchtastic.menu_apk.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [
                {
                    "assets": [
                        {"name": "app.apk"},
                        {"url": "http://example.com"},  # No name
                        {"name": None},  # Name is None
                    ]
                }
            ]
            mock_req.return_value = mock_response

            result = menu_apk.fetch_apk_assets()

            assert result == ["app.apk"]

    def test_select_assets_empty_selection(self):
        """Test select_assets with empty user selection."""
        with patch("fetchtastic.menu_apk.pick") as mock_pick:
            mock_pick.return_value = []

            result = menu_apk.select_assets(["app.apk"])

            assert result is None

    def test_select_assets_single_selection(self):
        """Test select_assets with single selection."""
        with (
            patch("fetchtastic.menu_apk.pick") as mock_pick,
            patch("fetchtastic.menu_apk.extract_base_name") as mock_extract,
        ):
            mock_pick.return_value = [("app-release-1.0.0.apk", 0)]
            mock_extract.return_value = "app-release.apk"

            result = menu_apk.select_assets(["app-release-1.0.0.apk"])

            assert result == {"selected_assets": ["app-release.apk"]}


class TestMenuFirmwareEdgeCases:
    """Test edge cases for menu_firmware module."""

    def test_fetch_firmware_assets_empty_response(self):
        """Test fetching when API returns empty list."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = []
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == []

    def test_fetch_firmware_assets_missing_name(self):
        """Test handling of assets without name field."""
        with patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req:
            mock_response = Mock()
            mock_response.json.return_value = [
                {
                    "assets": [
                        {"name": "firmware.bin"},
                        {"url": "http://example.com"},  # No name
                        {"name": None},  # Name is None
                        {"name": ""},  # Empty name
                    ]
                }
            ]
            mock_req.return_value = mock_response

            result = menu_firmware.fetch_firmware_assets()

            assert result == ["firmware.bin"]

    def test_select_assets_empty_selection(self):
        """
        Verify select_assets returns None when the user selects nothing.
        """
        with patch("fetchtastic.menu_firmware.pick") as mock_pick:
            mock_pick.return_value = []

            result = menu_firmware.select_assets(["firmware.bin"])

            assert result is None

    def test_select_assets_multiple_selections(self):
        """Test select_assets with multiple selections."""
        with (
            patch("fetchtastic.menu_firmware.pick") as mock_pick,
            patch("fetchtastic.menu_firmware.extract_base_name") as mock_extract,
        ):
            mock_pick.return_value = [
                ("firmware-rak4631-2.5.0.bin", 0),
                ("firmware-tbeam-2.5.0.bin", 1),
            ]
            mock_extract.side_effect = [
                "firmware-rak4631.bin",
                "firmware-tbeam.bin",
            ]

            result = menu_firmware.select_assets(
                ["firmware-rak4631-2.5.0.bin", "firmware-tbeam-2.5.0.bin"]
            )

            assert result == {
                "selected_assets": ["firmware-rak4631.bin", "firmware-tbeam.bin"]
            }


class TestMenuIntegration:
    """Test integration scenarios for menu modules."""

    def test_apk_menu_full_flow_success(self):
        """Test full APK menu flow with successful selection."""
        with (
            patch("fetchtastic.menu_apk.make_github_api_request") as mock_req,
            patch("fetchtastic.menu_apk.pick") as mock_pick,
            patch("fetchtastic.menu_apk.extract_base_name") as mock_extract,
        ):
            # Setup mock API response
            mock_response = Mock()
            mock_response.json.return_value = [
                {
                    "assets": [
                        {"name": "app-release-1.0.0.apk"},
                        {"name": "app-debug-1.0.0.apk"},
                    ]
                }
            ]
            mock_req.return_value = mock_response

            # Setup mock user selection
            mock_pick.return_value = [("app-release-1.0.0.apk", 0)]
            mock_extract.return_value = "app-release.apk"

            result = menu_apk.run_menu()

            assert result == {"selected_assets": ["app-release.apk"]}

    def test_firmware_menu_full_flow_success(self):
        """Test full firmware menu flow with successful selection."""
        with (
            patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req,
            patch("fetchtastic.menu_firmware.pick") as mock_pick,
            patch("fetchtastic.menu_firmware.extract_base_name") as mock_extract,
        ):
            # Setup mock API response
            mock_response = Mock()
            mock_response.json.return_value = [
                {
                    "assets": [
                        {"name": "firmware-rak4631-2.5.0.bin"},
                        {"name": "firmware-tbeam-2.5.0.bin"},
                    ]
                }
            ]
            mock_req.return_value = mock_response

            # Setup mock user selection
            mock_pick.return_value = [("firmware-rak4631-2.5.0.bin", 0)]
            mock_extract.return_value = "firmware-rak4631.bin"

            result = menu_firmware.run_menu()

            assert result == {"selected_assets": ["firmware-rak4631.bin"]}

    def test_apk_menu_user_cancels(self):
        """Test APK menu when user cancels selection."""
        with (
            patch("fetchtastic.menu_apk.make_github_api_request") as mock_req,
            patch("fetchtastic.menu_apk.pick") as mock_pick,
        ):
            # Setup mock API response
            mock_response = Mock()
            mock_response.json.return_value = [
                {"assets": [{"name": "app-release-1.0.0.apk"}]}
            ]
            mock_req.return_value = mock_response

            # User selects nothing
            mock_pick.return_value = []

            result = menu_apk.run_menu()

            assert result is None

    def test_firmware_menu_user_cancels(self):
        """Test firmware menu when user cancels selection."""
        with (
            patch("fetchtastic.menu_firmware.make_github_api_request") as mock_req,
            patch("fetchtastic.menu_firmware.pick") as mock_pick,
        ):
            # Setup mock API response
            mock_response = Mock()
            mock_response.json.return_value = [
                {"assets": [{"name": "firmware-rak4631-2.5.0.bin"}]}
            ]
            mock_req.return_value = mock_response

            # User selects nothing
            mock_pick.return_value = []

            result = menu_firmware.run_menu()

            assert result is None
