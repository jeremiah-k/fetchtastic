"""
Tests for DeviceHardwareManager configuration in FirmwareDownloader.

This module tests new configurable DeviceHardwareManager feature
added in cache branch.
"""

from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.constants import (
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader


@pytest.fixture
def test_config():
    """
    Provide a baseline test configuration dictionary used by tests.

    Returns:
        dict: Configuration with the following keys:
            FIRMWARE_VERSIONS_TO_KEEP (int): Number of firmware versions to retain.
            SELECTED_PATTERNS (list[str]): Filename patterns to include.
            EXCLUDE_PATTERNS (list[str]): Filename patterns to exclude.
            GITHUB_TOKEN (str): Token used for GitHub API calls in tests.
            CHECK_FIRMWARE_PRERELEASES (bool): Whether prerelease firmware should be checked.
    """
    return {
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
    }


class TestDeviceHardwareManagerConfig:
    """Test suite for DeviceHardwareManager configuration in FirmwareDownloader."""

    @patch("fetchtastic.download.firmware.DeviceHardwareManager")
    def test_device_hardware_manager_initialized_with_config(
        self, mock_dhm_class, test_config
    ):
        """Test DeviceHardwareManager is initialized with correct parameters."""
        mock_dhm_instance = MagicMock()
        mock_dhm_class.return_value = mock_dhm_instance

        cache_manager = CacheManager()
        _downloader = FirmwareReleaseDownloader(test_config, cache_manager)

        mock_dhm_class.assert_called_once()
        call_kwargs = mock_dhm_class.call_args[1]
        assert call_kwargs["enabled"] is True
        assert call_kwargs["cache_hours"] == DEVICE_HARDWARE_CACHE_HOURS
        assert call_kwargs["api_url"] == DEVICE_HARDWARE_API_URL

    @patch("fetchtastic.download.firmware.DeviceHardwareManager")
    def test_device_hardware_manager_initialized_with_custom_config(
        self, mock_dhm_class, test_config
    ):
        """Test DeviceHardwareManager is initialized with custom parameters."""
        mock_dhm_instance = MagicMock()
        mock_dhm_class.return_value = mock_dhm_instance

        test_config["DEVICE_HARDWARE_API"] = {
            "enabled": False,
            "cache_hours": 48,
            "api_url": "https://custom.example.com/api",
        }
        cache_manager = CacheManager()
        _downloader = FirmwareReleaseDownloader(test_config, cache_manager)

        mock_dhm_class.assert_called_once()
        call_kwargs = mock_dhm_class.call_args[1]
        assert call_kwargs["enabled"] is False
        assert call_kwargs["cache_hours"] == 48
        assert call_kwargs["api_url"] == "https://custom.example.com/api"
