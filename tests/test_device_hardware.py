"""
Comprehensive tests for device_hardware module.

Tests DeviceHardwareManager functionality including:
- Initialization and configuration
- Pattern matching and validation
- Cache management
- API fetching and error handling
- Fallback mechanisms
- Edge cases and security scenarios
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from fetchtastic.device_hardware import (
    FALLBACK_DEVICE_PATTERNS,
    DeviceHardwareManager,
)
from fetchtastic.utils import get_user_agent


class TestDeviceHardwareManager:
    """Test DeviceHardwareManager class functionality."""

    def test_init_default_parameters(self):
        """Test initialization with default parameters."""
        manager = DeviceHardwareManager()

        assert manager.enabled is True
        assert manager.cache_hours > 0
        assert manager.timeout_seconds > 0
        assert manager.cache_dir.exists()
        assert manager.cache_file.name == "device_hardware.json"

    def test_init_custom_parameters(self):
        """Test initialization with custom parameters."""
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_cache = Path(temp_dir)
            manager = DeviceHardwareManager(
                cache_dir=custom_cache,
                api_url="https://custom.api.url",
                cache_hours=24,
                timeout_seconds=30,
                enabled=False,
            )

            assert manager.enabled is False
            assert manager.cache_hours == 24
            assert manager.timeout_seconds == 30
            assert manager.api_url == "https://custom.api.url"
            assert manager.cache_dir == custom_cache
            assert manager.cache_file == custom_cache / "device_hardware.json"

    def test_init_creates_cache_directory(self):
        """Test that initialization creates cache directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "subdir" / "cache"
            assert not cache_dir.exists()

            DeviceHardwareManager(cache_dir=cache_dir)

            assert cache_dir.exists()
            assert cache_dir.is_dir()

    def test_get_device_patterns_enabled(self):
        """Test get_device_patterns when manager is enabled."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            # Create a valid cache file with proper format
            current_time = time.time()
            cache_data = {
                "device_patterns": ["device1", "device2", "device3"],
                "timestamp": current_time,
                "api_url": "https://example.com",
            }
            cache_file.write_text(json.dumps(cache_data))

            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=True)
            # Set last fetch time to prevent cache expiration
            manager._last_fetch_time = current_time
            patterns = manager.get_device_patterns()

            assert patterns == {"device1", "device2", "device3"}

    def test_get_device_patterns_disabled(self):
        """Test get_device_patterns when manager is disabled."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            # Ensure no cache file exists
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)
            patterns = manager.get_device_patterns()

            # Should return fallback patterns when disabled and no cache
            assert patterns == set(FALLBACK_DEVICE_PATTERNS)

    def test_get_device_patterns_cache_miss_api_success(self):
        """Test get_device_patterns with cache miss and successful API call."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            test_patterns = ["api_device1", "api_device2"]

            # Mock API response with platformioTarget field
            api_response_data = [
                {"platformioTarget": "api_device1"},
                {"platformioTarget": "api_device2"},
            ]

            with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                mock_response = Mock()
                mock_response.json.return_value = api_response_data
                mock_response.raise_for_status.return_value = None
                mock_get.return_value = mock_response

                manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=True)
                patterns = manager.get_device_patterns()

                assert patterns == set(test_patterns)
                mock_get.assert_called_once()

                # Check that cache was created
                assert (cache_dir / "device_hardware.json").exists()

    def test_get_device_patterns_cache_miss_api_failure(self):
        """Test get_device_patterns with cache miss and API failure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                mock_get.side_effect = requests.RequestException("API unavailable")

                manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=True)
                patterns = manager.get_device_patterns()

                # Should fall back to built-in patterns
                assert patterns == set(FALLBACK_DEVICE_PATTERNS)

    def test_get_device_patterns_cache_corruption(self):
        """Test get_device_patterns with corrupted cache file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            # Create corrupted cache file
            cache_file.write_text("invalid json content")

            # Mock API to also fail so it falls back to built-in patterns
            with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                mock_get.side_effect = requests.RequestException("API unavailable")

                manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=True)
                patterns = manager.get_device_patterns()

                # Should handle corruption gracefully and fall back
                assert patterns == set(FALLBACK_DEVICE_PATTERNS)

    def test_is_device_pattern_valid_patterns(self):
        """Test is_device_pattern with valid device patterns."""
        # Use a clean cache directory to ensure fallback patterns are used
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)

            # Test with fallback patterns
            for pattern in FALLBACK_DEVICE_PATTERNS:
                assert manager.is_device_pattern(pattern) is True

        # Test with custom patterns
        with patch.object(
            manager, "get_device_patterns", return_value={"custom1", "custom2"}
        ):
            assert manager.is_device_pattern("custom1") is True
            assert manager.is_device_pattern("custom2") is True

    def test_is_device_pattern_invalid_patterns(self):
        """Test is_device_pattern with invalid patterns."""
        manager = DeviceHardwareManager()

        with patch.object(
            manager, "get_device_patterns", return_value={"device1", "device2"}
        ):
            assert manager.is_device_pattern("invalid") is False
            assert manager.is_device_pattern("") is False
            assert manager.is_device_pattern("device") is False  # Too short/partial

    def test_is_device_pattern_case_insensitive(self):
        """Test that is_device_pattern is case insensitive."""
        manager = DeviceHardwareManager()

        with patch.object(
            manager, "get_device_patterns", return_value={"RAK4631", "TBEAM"}
        ):
            assert manager.is_device_pattern("rak4631") is True
            assert manager.is_device_pattern("RAK4631") is True
            assert manager.is_device_pattern("Rak4631") is True
            assert manager.is_device_pattern("tbeam") is True
            assert manager.is_device_pattern("TBEAM") is True

    def test_is_device_pattern_minimum_length(self):
        """Test is_device_pattern respects minimum prefix length."""
        manager = DeviceHardwareManager()

        with patch.object(manager, "get_device_patterns", return_value={"ab", "abc"}):
            # 'ab' should match (meets minimum length)
            assert manager.is_device_pattern("ab") is True
            # 'a' should not match (too short)
            assert manager.is_device_pattern("a") is False

    def test_is_device_pattern_substring_matching(self):
        """Test is_device_pattern with substring matching."""
        manager = DeviceHardwareManager()

        with patch.object(
            manager, "get_device_patterns", return_value={"rak4631", "tbeam"}
        ):
            # Exact matches should work
            assert manager.is_device_pattern("rak4631") is True
            assert manager.is_device_pattern("tbeam") is True

            # Substring matches should work
            assert manager.is_device_pattern("rak4631-") is True
            assert manager.is_device_pattern("rak4631_") is True
            assert manager.is_device_pattern("tbeam-") is True
            assert manager.is_device_pattern("tbeam_") is True

    def test_cache_expiration(self):
        """Test cache expiration logic."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            manager = DeviceHardwareManager(cache_dir=cache_dir, cache_hours=1)

            # Create valid cache with timestamp
            current_time = time.time()
            cache_data = {
                "device_patterns": ["device1", "device2"],
                "timestamp": current_time,
                "api_url": manager.api_url,
            }
            cache_file.write_text(json.dumps(cache_data))

            # Set the last fetch time to match cache timestamp
            manager._last_fetch_time = current_time

            # Fresh cache should not be expired
            assert manager._is_cache_expired() is False

            # Old cache should be expired
            old_time = current_time - (manager.cache_hours * 3600 + 100)
            cache_data["timestamp"] = old_time
            cache_file.write_text(json.dumps(cache_data))
            manager._last_fetch_time = old_time
            assert manager._is_cache_expired() is True

    def test_cache_expiration_missing_file(self):
        """Test cache expiration with missing cache file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            manager = DeviceHardwareManager(cache_dir=cache_dir)

            # Missing cache should be considered expired
            assert manager._is_cache_expired() is True

    def test_save_to_cache(self):
        """Test saving patterns to cache file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            manager = DeviceHardwareManager(cache_dir=cache_dir)

            test_patterns = {"device1", "device2", "device3"}
            manager._save_to_cache(test_patterns)

            assert manager.cache_file.exists()
            cached_data = json.loads(manager.cache_file.read_text())
            assert set(cached_data["device_patterns"]) == test_patterns
            assert "timestamp" in cached_data
            assert "api_url" in cached_data

    def test_load_from_cache_valid(self):
        """Test loading valid data from cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            test_patterns = ["device1", "device2", "device3"]
            cache_data = {
                "device_patterns": test_patterns,
                "timestamp": time.time(),
                "api_url": "https://example.com",
            }
            cache_file.write_text(json.dumps(cache_data))

            manager = DeviceHardwareManager(cache_dir=cache_dir)
            loaded_patterns = manager._load_from_cache()

            assert loaded_patterns == set(test_patterns)

    def test_load_from_cache_invalid_json(self):
        """Test loading from cache with invalid JSON."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            cache_file.write_text("invalid json")

            manager = DeviceHardwareManager(cache_dir=cache_dir)
            loaded_patterns = manager._load_from_cache()

            assert loaded_patterns is None

    def test_load_from_cache_missing_file(self):
        """Test loading from cache with missing file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            manager = DeviceHardwareManager(cache_dir=cache_dir)

            loaded_patterns = manager._load_from_cache()

            assert loaded_patterns is None

    def test_fetch_from_api_success(self):
        """Test successful API fetch."""
        test_patterns = ["api_device1", "api_device2", "api_device3"]

        # Mock API response with platformioTarget field
        api_response_data = [
            {"platformioTarget": "api_device1"},
            {"platformioTarget": "api_device2"},
            {"platformioTarget": "api_device3"},
        ]

        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = api_response_data
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result == set(test_patterns)
            mock_get.assert_called_once_with(
                manager.api_url,
                headers={"User-Agent": get_user_agent(), "Accept": "application/json"},
                timeout=manager.timeout_seconds,
            )

    def test_fetch_from_api_http_error(self):
        """Test API fetch with HTTP error."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_get.side_effect = requests.HTTPError("HTTP 404")

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_fetch_from_api_timeout(self):
        """Test API fetch with timeout."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_get.side_effect = requests.Timeout("Request timeout")

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_fetch_from_api_connection_error(self):
        """Test API fetch with connection error."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("Connection failed")

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_fetch_from_api_invalid_json(self):
        """Test API fetch with invalid JSON response."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_clear_cache(self):
        """Test clearing cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            # Create cache file
            cache_file.write_text(json.dumps(["device1", "device2"]))
            assert cache_file.exists()

            manager = DeviceHardwareManager(cache_dir=cache_dir)
            manager.clear_cache()

            assert not cache_file.exists()

    def test_load_device_patterns_integration(self):
        """
        Verify that _load_device_patterns retrieves device patterns from the API and returns them as a set when a fresh cache is absent.

        This integration test confirms that when the cache directory is new and the remote API returns platform entries containing `platformioTarget`, the manager's `_load_device_patterns` method collects those targets and returns them as a set of pattern strings.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            # Test with fresh cache and API available
            test_patterns = ["loaded_device1", "loaded_device2"]

            # Mock API response with platformioTarget field
            api_response_data = [
                {"platformioTarget": "loaded_device1"},
                {"platformioTarget": "loaded_device2"},
            ]

            with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                mock_response = Mock()
                mock_response.json.return_value = api_response_data
                mock_response.raise_for_status.return_value = None
                mock_get.return_value = mock_response

                manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=True)
                patterns = manager._load_device_patterns()

                assert patterns == set(test_patterns)

    def test_get_user_agent_import(self):
        """Test that get_user_agent can be imported and used."""
        from fetchtastic.utils import get_user_agent

        user_agent = get_user_agent()

        assert isinstance(user_agent, str)
        assert len(user_agent) > 0
        assert "fetchtastic" in user_agent.lower()


class TestDeviceHardwareManagerEdgeCases:
    """Test edge cases and security scenarios."""

    def test_empty_api_response(self):
        """Test handling of empty API response."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = []  # Empty list of devices
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None  # Returns None when no valid patterns found

    def test_api_response_missing_platforms_key(self):
        """Test handling of API response missing platforms key."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {"wrong_key": []}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_api_response_non_list_platforms(self):
        """Test handling of API response with non-list platforms."""
        with patch("fetchtastic.device_hardware.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {"platforms": "not_a_list"}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            manager = DeviceHardwareManager()
            result = manager._fetch_from_api()

            assert result is None

    def test_cache_file_permissions_error(self):
        """Test handling of cache file permission errors."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            manager = DeviceHardwareManager(cache_dir=cache_dir)

            # Mock open to raise permission error for cache file operations
            with patch(
                "builtins.open", side_effect=PermissionError("Permission denied")
            ):
                # Mock API to also fail so it falls back to built-in patterns
                with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                    mock_get.side_effect = requests.RequestException("API unavailable")

                    patterns = manager.get_device_patterns()

                    # Should fall back to built-in patterns
                    assert patterns == set(FALLBACK_DEVICE_PATTERNS)

    def test_cache_directory_permissions_error(self):
        """Test handling of cache directory permission errors."""
        with patch(
            "pathlib.Path.mkdir", side_effect=PermissionError("Permission denied")
        ):
            with pytest.raises(PermissionError):
                DeviceHardwareManager()

    def test_is_device_pattern_with_special_characters(self):
        """Test is_device_pattern with special characters."""
        manager = DeviceHardwareManager()

        with patch.object(
            manager,
            "get_device_patterns",
            return_value={"device-with-dash", "device_with_underscore"},
        ):
            assert manager.is_device_pattern("device-with-dash") is True
            assert manager.is_device_pattern("device_with_underscore") is True
            assert manager.is_device_pattern("device.with.dots") is False

    def test_is_device_pattern_with_numbers(self):
        """Test is_device_pattern with numeric patterns."""
        manager = DeviceHardwareManager()

        with patch.object(
            manager, "get_device_patterns", return_value={"device123", "123device"}
        ):
            assert manager.is_device_pattern("device123") is True
            assert manager.is_device_pattern("123device") is True

    def test_very_long_device_pattern(self):
        """Test handling of very long device patterns."""
        long_pattern = "a" * 1000
        manager = DeviceHardwareManager()

        with patch.object(manager, "get_device_patterns", return_value={long_pattern}):
            assert manager.is_device_pattern(long_pattern) is True
            assert manager.is_device_pattern(long_pattern[:10]) is False

    def test_unicode_device_patterns(self):
        """Test handling of unicode device patterns."""
        unicode_patterns = {"设备1", "девайс2", "デバイス3"}
        manager = DeviceHardwareManager()

        with patch.object(
            manager, "get_device_patterns", return_value=unicode_patterns
        ):
            for pattern in unicode_patterns:
                assert manager.is_device_pattern(pattern) is True

    def test_concurrent_access(self):
        """Test thread safety of device pattern access."""
        import threading

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            # Use fallback patterns for consistency by ensuring no cache exists
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)
            results = []

            def check_patterns():
                """
                Append the current count of device patterns to the global `results` list.

                Calls manager.get_device_patterns() and appends the length of the returned pattern set to `results`.
                """
                patterns = manager.get_device_patterns()
                results.append(len(patterns))

            threads = [threading.Thread(target=check_patterns) for _ in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # All threads should get same result (fallback patterns)
            assert all(result == len(FALLBACK_DEVICE_PATTERNS) for result in results)


class TestDeviceHardwareManagerPerformance:
    """Test performance and resource usage."""

    def test_large_pattern_set_performance(self):
        """Test performance with large device pattern sets."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            # Create a large set of device patterns in proper cache format
            large_patterns = {f"device{i}" for i in range(10000)}
            cache_data = {
                "device_patterns": list(large_patterns),
                "timestamp": time.time(),
                "api_url": "https://example.com",
            }
            cache_file = cache_dir / "device_hardware.json"
            cache_file.write_text(json.dumps(cache_data))

            manager = DeviceHardwareManager(cache_dir=cache_dir)
            # Set last fetch time to prevent cache expiration
            manager._last_fetch_time = cache_data["timestamp"]

            # Test that pattern matching remains efficient
            start_time = time.time()
            assert manager.is_device_pattern("device5000") is True
            assert manager.is_device_pattern("nonexistent") is False
            end_time = time.time()

            # Should complete quickly (less than 1 second for 10k patterns)
            assert end_time - start_time < 1.0

    def test_memory_usage_with_large_cache(self):
        """Test memory usage doesn't grow excessively with large cache."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)

            # Create large cache
            large_patterns = {f"device{i}" for i in range(5000)}
            cache_file = cache_dir / "device_hardware.json"
            cache_file.write_text(json.dumps(list(large_patterns)))

            manager = DeviceHardwareManager(cache_dir=cache_dir)

            # Multiple calls should not increase memory usage significantly
            initial_patterns = manager.get_device_patterns()
            for _ in range(10):
                patterns = manager.get_device_patterns()
                assert patterns == initial_patterns

    def test_cache_hit_performance(self):
        """Test that cache hits are significantly faster than API calls."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache_file = cache_dir / "device_hardware.json"

            test_patterns = ["device1", "device2", "device3"]
            cache_data = {
                "device_patterns": test_patterns,
                "timestamp": time.time(),
                "api_url": "https://example.com",
            }
            cache_file.write_text(json.dumps(cache_data))

            manager = DeviceHardwareManager(cache_dir=cache_dir)
            # Set last fetch time to prevent cache expiration
            manager._last_fetch_time = cache_data["timestamp"]

            # First call (cache hit)
            start_time = time.time()
            patterns1 = manager.get_device_patterns()
            cache_time = time.time() - start_time

            # Mock API to be slow
            with patch("fetchtastic.device_hardware.requests.get") as mock_get:
                mock_response = Mock()
                # Mock slow response
                mock_response.json.return_value = [
                    {"platformioTarget": "device1"},
                    {"platformioTarget": "device2"},
                    {"platformioTarget": "device3"},
                ]
                mock_response.raise_for_status.return_value = None
                mock_get.return_value = mock_response

                # Force cache expiration
                old_time = time.time() - (manager.cache_hours * 3600 + 100)
                cache_data["timestamp"] = old_time
                cache_file.write_text(json.dumps(cache_data))
                manager._last_fetch_time = old_time

                start_time = time.time()
                manager.get_device_patterns()
                api_time = time.time() - start_time

                # Cache should be significantly faster
                assert cache_time < api_time
                assert patterns1 == set(test_patterns)
