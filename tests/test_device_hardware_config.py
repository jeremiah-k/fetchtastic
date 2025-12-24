"""
Tests for DeviceHardwareManager configuration in FirmwareDownloader.

This module tests new configurable DeviceHardwareManager feature
added in cache branch.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader

DEVICE_HARDWARE_API_URL = "https://api.meshtastic.org/resource/deviceHardware"
DEVICE_HARDWARE_CACHE_HOURS = 24


@pytest.fixture
def test_config():
    """
    Provide a baseline test configuration dictionary used by tests.

    Returns:
        dict: Configuration with the following keys:
            DOWNLOAD_DIR (str): Default download directory path.
            FIRMWARE_VERSIONS_TO_KEEP (int): Number of firmware versions to retain.
            SELECTED_PATTERNS (list[str]): Filename patterns to include.
            EXCLUDE_PATTERNS (list[str]): Filename patterns to exclude.
            GITHUB_TOKEN (str): Token used for GitHub API calls in tests.
            CHECK_FIRMWARE_PRERELEASES (bool): Whether prerelease firmware should be checked.
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test_firmware",
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
    }


@pytest.fixture
def firmware_downloader(test_config):
    """
    Create a FirmwareReleaseDownloader configured for tests.

    Parameters:
        test_config (dict): Configuration dictionary to initialize the downloader.

    Returns:
        FirmwareReleaseDownloader: Instance initialized with the provided configuration and a new CacheManager.
    """
    cache_manager = CacheManager()
    return FirmwareReleaseDownloader(test_config, cache_manager)


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
        downloader = FirmwareReleaseDownloader(test_config, cache_manager)

        with tempfile.TemporaryDirectory() as tmpdir:
            test_config["DOWNLOAD_DIR"] = tmpdir
            prerelease_dir = Path(tmpdir) / "firmware" / "prereleases" / "test_dir"
            prerelease_dir.mkdir(parents=True)

            downloader.download_dir = tmpdir

            with patch.object(
                downloader,
                "_fetch_prerelease_directory_listing",
                return_value=[],
            ):
                successes, failures, downloaded = (
                    downloader._download_prerelease_assets(
                        remote_dir="test_dir",
                        selected_patterns=[],
                        exclude_patterns=[],
                        force_refresh=False,
                    )
                )

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
        downloader = FirmwareReleaseDownloader(test_config, cache_manager)

        with tempfile.TemporaryDirectory() as tmpdir:
            test_config["DOWNLOAD_DIR"] = tmpdir
            prerelease_dir = Path(tmpdir) / "firmware" / "prereleases" / "test_dir"
            prerelease_dir.mkdir(parents=True)

            downloader.download_dir = tmpdir

            with patch.object(
                downloader,
                "_fetch_prerelease_directory_listing",
                return_value=[],
            ):
                successes, failures, downloaded = (
                    downloader._download_prerelease_assets(
                        remote_dir="test_dir",
                        selected_patterns=[],
                        exclude_patterns=[],
                        force_refresh=False,
                    )
                )

                mock_dhm_class.assert_called_once()
                call_kwargs = mock_dhm_class.call_args[1]
                assert call_kwargs["enabled"] is False
                assert call_kwargs["cache_hours"] == 48
                assert call_kwargs["api_url"] == "https://custom.example.com/api"
