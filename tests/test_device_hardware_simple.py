# tests/test_device_hardware_simple.py

"""
Simple tests for device_hardware module functions.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from fetchtastic.device_hardware import DeviceHardwareManager


class TestDeviceHardwareManager:
    """Test DeviceHardwareManager class."""

    def test_init_default(self):
        """Test DeviceHardwareManager initialization with defaults."""
        manager = DeviceHardwareManager()

        assert manager.api_url == "https://api.meshtastic.org/hardware"
        assert manager.cache_hours == 24
        assert manager.timeout_seconds == 10

    def test_init_custom(self):
        """Test DeviceHardwareManager initialization with custom values."""
        custom_url = "https://custom.api.com"
        custom_cache_hours = 12
        custom_timeout = 5

        manager = DeviceHardwareManager(
            api_url=custom_url,
            cache_hours=custom_cache_hours,
            timeout_seconds=custom_timeout,
        )

        assert manager.api_url == custom_url
        assert manager.cache_hours == custom_cache_hours
        assert manager.timeout_seconds == custom_timeout

    def test_is_device_pattern_valid(self):
        """Test device pattern validation with valid patterns."""
        manager = DeviceHardwareManager()

        # Test valid patterns
        assert manager.is_device_pattern("rak4631") is True
        assert manager.is_device_pattern("tbeam") is True
        assert manager.is_device_pattern("heltec") is True
        assert manager.is_device_pattern("tlora-v2-1-1_6") is True

    def test_is_device_pattern_invalid(self):
        """Test device pattern validation with invalid patterns."""
        manager = DeviceHardwareManager()

        # Test invalid patterns
        assert manager.is_device_pattern("") is False
        assert manager.is_device_pattern("a") is False  # Too short
        assert manager.is_device_pattern("ab") is False  # Still too short
        assert manager.is_device_pattern("xyz") is False  # Not in fallback patterns
        assert manager.is_device_pattern("random-device") is False

    def test_is_device_pattern_case_sensitive(self):
        """Test device pattern validation is case sensitive."""
        manager = DeviceHardwareManager()

        # Test case sensitivity
        assert manager.is_device_pattern("RAK4631") is False  # Upper case
        assert manager.is_device_pattern("Rak4631") is False  # Mixed case
        assert manager.is_device_pattern("rak4631") is True  # Lower case

    def test_get_device_patterns_from_fallback(self, mocker):
        """Test getting device patterns from fallback when no cache/API available."""
        manager = DeviceHardwareManager()

        # Mock all loading methods to return None/empty
        mocker.patch.object(manager, "_load_from_cache", return_value=None)
        mocker.patch.object(manager, "_fetch_from_api", return_value=None)

        patterns = manager.get_device_patterns()

        # Should return fallback patterns
        assert "rak4631" in patterns
        assert "tbeam" in patterns
        assert "heltec" in patterns
        assert len(patterns) > 0

    def test_get_device_patterns_from_cache(self, mocker):
        """Test getting device patterns from cache."""
        manager = DeviceHardwareManager()
        cached_patterns = {"cached1", "cached2"}

        # Mock cache to return patterns and API to not be called
        mocker.patch.object(manager, "_load_from_cache", return_value=cached_patterns)
        mocker.patch.object(manager, "_fetch_from_api", return_value=None)

        patterns = manager.get_device_patterns()

        assert patterns == cached_patterns

    def test_get_device_patterns_from_api(self, mocker):
        """Test getting device patterns from API when cache is empty/expired."""
        manager = DeviceHardwareManager()
        api_patterns = {"api1", "api2"}

        # Mock cache to return None and API to return patterns
        mocker.patch.object(manager, "_load_from_cache", return_value=None)
        mocker.patch.object(manager, "_fetch_from_api", return_value=api_patterns)

        patterns = manager.get_device_patterns()

        assert patterns == api_patterns

    def test_load_from_cache_file_exists(self, tmp_path, mocker):
        """Test loading patterns from existing cache file."""
        manager = DeviceHardwareManager()
        cache_file = tmp_path / "device_hardware_cache.json"
        test_patterns = ["device1", "device2", "device3"]

        cache_data = {
            "timestamp": time.time() - 3600,  # 1 hour ago
            "patterns": test_patterns,
        }

        with patch(
            "fetchtastic.device_hardware.get_cache_file_path",
            return_value=str(cache_file),
        ):
            with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
                with patch("os.path.exists", return_value=True):
                    result = manager._load_from_cache()

                    assert result is not None
                    assert set(result) == set(test_patterns)

    def test_load_from_cache_no_file(self, mocker):
        """Test loading patterns when cache file doesn't exist."""
        manager = DeviceHardwareManager()

        with patch("os.path.exists", return_value=False):
            result = manager._load_from_cache()

            assert result is None

    def test_save_to_cache(self, tmp_path, mocker):
        """Test saving patterns to cache file."""
        manager = DeviceHardwareManager()
        cache_file = tmp_path / "device_hardware_cache.json"
        test_patterns = {"device1", "device2"}

        with patch(
            "fetchtastic.device_hardware.get_cache_file_path",
            return_value=str(cache_file),
        ):
            with patch("builtins.open", mock_open()) as mock_file:
                with patch("json.dump") as mock_json:
                    with patch("os.makedirs"):
                        manager._save_to_cache(test_patterns)

                        mock_file.assert_called_once_with(str(cache_file), "w")
                        mock_json.assert_called_once()

    def test_is_cache_expired_no_file(self, mocker):
        """Test cache expiration when cache file doesn't exist."""
        manager = DeviceHardwareManager()

        with patch("os.path.exists", return_value=False):
            result = manager._is_cache_expired()

            assert result is True

    def test_is_cache_expired_file_exists(self, tmp_path, mocker):
        """Test cache expiration with existing cache file."""
        manager = DeviceHardwareManager()
        cache_file = tmp_path / "device_hardware_cache.json"

        # Create cache file with old timestamp
        cache_data = {
            "timestamp": time.time() - (25 * 3600),  # 25 hours ago (expired)
            "patterns": ["device1"],
        }

        with patch(
            "fetchtastic.device_hardware.get_cache_file_path",
            return_value=str(cache_file),
        ):
            with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
                with patch("os.path.exists", return_value=True):
                    result = manager._is_cache_expired()

                    assert result is True

    def test_clear_cache(self, mocker):
        """Test clearing cache file."""
        manager = DeviceHardwareManager()

        with patch("os.path.exists", return_value=True):
            with patch("os.remove") as mock_remove:
                manager.clear_cache()

                mock_remove.assert_called_once()

    def test_clear_cache_no_file(self, mocker):
        """Test clearing cache when file doesn't exist."""
        manager = DeviceHardwareManager()

        with patch("os.path.exists", return_value=False):
            with patch("os.remove") as mock_remove:
                manager.clear_cache()

                mock_remove.assert_not_called()


# Need to import time for timestamp tests
import time
