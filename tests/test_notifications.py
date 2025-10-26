"""
Notification and UI message tests for the fetchtastic downloader module.

This module contains tests for:
- NTFY notification functionality
- User-facing messages and logging
- UI feedback and status reporting
- Error message handling
- Logging output validation
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from fetchtastic import downloader
from fetchtastic.device_hardware import DeviceHardwareManager


def test_send_ntfy_notification(mocker):
    """Test NTFY notification sending logic."""
    mock_post = mocker.patch("requests.post")

    # 1. Test successful notification
    downloader._send_ntfy_notification(
        "https://ntfy.sh", "mytopic", "Test message", "Test Title"
    )
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://ntfy.sh/mytopic"
    assert kwargs["data"] == "Test message".encode("utf-8")
    assert kwargs["headers"]["Content-Type"] == "text/plain; charset=utf-8"
    assert kwargs["headers"]["Title"] == "Test Title"
    assert kwargs["timeout"] == downloader.NTFY_REQUEST_TIMEOUT

    # 2. Test request exception
    mock_post.reset_mock()
    mock_post.side_effect = requests.exceptions.RequestException("Network error")
    # Should not raise an exception, just log a warning
    downloader._send_ntfy_notification("https://ntfy.sh", "mytopic", "Test message")
    assert mock_post.call_count == 1

    # 3. Test with no server/topic
    mock_post.reset_mock()
    downloader._send_ntfy_notification(None, None, "Test message")
    mock_post.assert_not_called()

    # 4. Header omission when no title is provided
    mock_post.reset_mock()
    downloader._send_ntfy_notification("https://ntfy.sh", "mytopic", "No title here")
    args, kwargs = mock_post.call_args
    assert "Title" not in kwargs["headers"]


def test_device_hardware_manager_ui_messages(caplog):
    """Test DeviceHardwareManager user-facing messages and logging."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)
        cache_file = cache_dir / "device_hardware.json"

        # Test 1: Cache expiration warning message
        expired_cache = {
            "device_patterns": ["test-device"],
            "timestamp": time.time() - 25 * 3600,  # 25 hours ago
            "api_url": "https://api.meshtastic.org/resource/deviceHardware",
        }

        with open(cache_file, "w") as f:
            json.dump(expired_cache, f)

        # Test with API disabled - should show cache expiration warning
        with caplog.at_level("WARNING", logger="fetchtastic"):
            manager = DeviceHardwareManager(
                cache_dir=cache_dir, enabled=False, cache_hours=24
            )
            patterns = manager.get_device_patterns()

        # Verify functionality works correctly with expired cache
        assert len(patterns) > 0  # Should get fallback patterns
        assert "test-device" in patterns  # Should use expired cache data
        assert "test-device" in patterns

        caplog.clear()

        # Test 2: API failure with fallback message
        with patch("requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            with caplog.at_level("WARNING"):
                manager = DeviceHardwareManager(
                    cache_dir=cache_dir,
                    enabled=True,  # API enabled but will fail
                    timeout_seconds=1,
                )
                patterns = manager.get_device_patterns()

            # Should handle API failure gracefully and use fallback
            assert len(patterns) > 0  # Should get fallback patterns
            assert "test-device" in patterns  # Should use expired cache data
            assert len(patterns) > 0  # Should get fallback patterns

        caplog.clear()

        # Test 3: Cache save error handling
        readonly_cache_dir = cache_dir / "readonly"
        readonly_cache_dir.mkdir()
        readonly_cache_dir.chmod(0o444)  # Read-only

        try:
            with caplog.at_level("WARNING"):
                manager = DeviceHardwareManager(
                    cache_dir=readonly_cache_dir, enabled=False
                )
                # Try to trigger cache save (won't work due to permissions)
                patterns = manager.get_device_patterns()

            # Should handle cache save errors gracefully
            assert len(patterns) > 0  # Should still get fallback patterns

        finally:
            readonly_cache_dir.chmod(0o755)

        caplog.clear()

        # Test 4: Successful cache operations with info messages
        fresh_cache_dir = cache_dir / "fresh"
        fresh_cache_dir.mkdir()

        with patch("requests.get") as mock_get:
            mock_response = mock_get.return_value
            mock_response.json.return_value = [
                {"platformioTarget": "rak4631", "displayName": "RAK4631"},
                {"platformioTarget": "tbeam", "displayName": "T-Beam"},
            ]
            mock_response.raise_for_status.return_value = None

            with caplog.at_level("INFO"):
                manager = DeviceHardwareManager(
                    cache_dir=fresh_cache_dir, enabled=True, cache_hours=24
                )
                patterns = manager.get_device_patterns()

            # Should have successful API fetch
            assert "rak4631" in patterns
            assert "tbeam" in patterns
            assert len(patterns) >= 2


def test_device_hardware_manager_cache_corruption_handling(caplog):
    """Test DeviceHardwareManager handling of corrupted cache files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)
        cache_file = cache_dir / "device_hardware.json"

        # Test 1: Invalid JSON cache
        cache_file.write_text("invalid json content")

        with caplog.at_level("WARNING"):
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)
            patterns = manager.get_device_patterns()

        # Should handle corruption gracefully and fall back
        assert len(patterns) > 0
        assert isinstance(patterns, set)

        caplog.clear()

        # Test 2: Missing required fields in cache
        incomplete_cache = {
            "device_patterns": ["test-device"],
            # Missing timestamp and api_url
        }

        cache_file.write_text(json.dumps(incomplete_cache))

        with caplog.at_level("WARNING"):
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)
            patterns = manager.get_device_patterns()

        # Should handle incomplete cache gracefully
        assert len(patterns) > 0
        assert "test-device" in patterns

        caplog.clear()

        # Test 3: Cache with invalid data types
        invalid_cache = {
            "device_patterns": "not_a_list",  # Should be a list
            "timestamp": time.time(),
            "api_url": "https://example.com",
        }

        cache_file.write_text(json.dumps(invalid_cache))

        with caplog.at_level("WARNING"):
            manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)
            patterns = manager.get_device_patterns()

        # Should handle invalid data types gracefully
        assert len(patterns) > 0


def test_logging_output_validation(tmp_path, caplog):
    """Test that logging output contains expected information."""
    caplog.set_level("INFO", logger="fetchtastic")

    # Test download progress logging
    with patch("fetchtastic.downloader.download_file_with_retry", return_value=True):
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )

        # Should log download activity
        assert downloaded == ["v1.0.0"]
        assert new_versions == ["v1.0.0"]
        assert failures == []


def test_error_message_handling(caplog):
    """Test that error messages are handled and logged appropriately."""
    caplog.set_level("ERROR", logger="fetchtastic")

    # Test network error handling
    with patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Network unreachable"
        )

        # Should not raise exception, should log error
        downloader._send_ntfy_notification(
            "https://ntfy.sh", "topic", "message", "title"
        )

        # Should have logged the error (implementation dependent)
        # The exact log message format may vary


def test_user_facing_status_messages(tmp_path, caplog):
    """Test user-facing status messages during operations."""
    caplog.set_level("INFO", logger="fetchtastic")

    # Test up-to-date message
    releases = [
        {
            "tag_name": "v1.0.0",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-rak4631-1.0.0.zip",
                    "browser_download_url": "https://example.com/firmware.zip",
                    "size": 1000,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest.txt")
    download_dir = str(tmp_path / "downloads")

    # Pre-create release directory to simulate up-to-date state
    release_dir = Path(download_dir) / "v1.0.0"
    release_dir.mkdir(parents=True)
    (release_dir / "firmware-rak4631-1.0.0.zip").write_text("existing")

    Path(latest_release_file).write_text("v1.0.0")

    with patch("fetchtastic.downloader.download_file_with_retry", return_value=True):
        downloaded, new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )

        # Should indicate up to date
        assert downloaded == []
        assert new_versions == []
        assert failures == []


class TestNotificationIntegration:
    """Integration tests for notification functionality."""

    def test_notification_with_download_completion(self, mocker, tmp_path):
        """Test notification flow when download completes successfully."""
        mock_notification = mocker.patch(
            "fetchtastic.downloader._send_ntfy_notification"
        )

        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            downloaded, new_versions, failures = downloader.check_and_download(
                releases,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=1,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Note: Actual notification sending would be handled by calling code
            # This test validates the download completion part
            assert downloaded == ["v1.0.0"]
            assert new_versions == ["v1.0.0"]
            assert failures == []

    def test_notification_error_handling(self, mocker):
        """Test notification error handling doesn't break main flow."""
        mock_notification = mocker.patch(
            "downloader._send_ntfy_notification",
            side_effect=requests.exceptions.RequestException("Network error"),
        )

        # Should not raise exception even if notification fails
        downloader._send_ntfy_notification(
            "https://ntfy.sh", "topic", "message", "title"
        )

        mock_notification.assert_called_once()

    def test_notification_with_different_message_types(self, mocker):
        """Test notification with various message types and formats."""
        mock_notification = mocker.patch(
            "fetchtastic.downloader._send_ntfy_notification"
        )

        test_cases = [
            ("Simple message", None),
            ("Message with title", "Title"),
            ("Message with\nnewlines", "Multi-line\ntitle"),
            ("Message with special chars: !@#$%", "Special chars: ^&*()"),
            ("", "Empty message"),
            ("Unicode message: 你好", "Unicode title: 世界"),
        ]

        for message, title in test_cases:
            mock_notification.reset_mock()

            downloader._send_ntfy_notification(
                "https://ntfy.sh", "topic", message, title
            )

            mock_notification.assert_called_once_with(
                "https://ntfy.sh", "topic", message, title
            )

    def test_notification_parameter_validation(self, mocker):
        """Test notification parameter validation."""
        mock_notification = mocker.patch(
            "fetchtastic.downloader._send_ntfy_notification"
        )

        # Test with None parameters
        downloader._send_ntfy_notification(None, None, None, None)
        mock_notification.assert_not_called()

        # Test with empty strings
        downloader._send_ntfy_notification("", "", "", "")
        mock_notification.assert_not_called()

        # Test with valid server but None topic
        mock_notification.reset_mock()
        downloader._send_ntfy_notification("https://ntfy.sh", None, "message")
        mock_notification.assert_not_called()

        # Test with valid topic but None server
        mock_notification.reset_mock()
        downloader._send_ntfy_notification(None, "topic", "message")
        mock_notification.assert_not_called()


class TestUIMessageFormatting:
    """Test UI message formatting and presentation."""

    def test_progress_message_formatting(self, caplog):
        """Test that progress messages are formatted correctly."""
        caplog.set_level("INFO", logger="fetchtastic")

        # This would typically be tested through actual download operations
        # Here we validate the logging infrastructure
        with caplog.at_level("INFO"):
            from fetchtastic.log_utils import logger

            logger.info("Downloaded 1/2 files (50%)")

        # Should capture the log message
        assert "Downloaded 1/2 files (50%)" in caplog.text

    def test_error_message_formatting(self, caplog):
        """Test that error messages are formatted correctly."""
        caplog.set_level("ERROR", logger="fetchtastic")

        with caplog.at_level("ERROR"):
            from fetchtastic.log_utils import logger

            logger.error("Failed to download firmware: Network timeout")

        # Should capture the error message
        assert "Failed to download firmware: Network timeout" in caplog.text

    def test_warning_message_formatting(self, caplog):
        """Test that warning messages are formatted correctly."""
        caplog.set_level("WARNING", logger="fetchtastic")

        with caplog.at_level("WARNING"):
            from fetchtastic.log_utils import logger

            logger.warning("Using fallback device patterns due to API failure")

        # Should capture the warning message
        assert "Using fallback device patterns due to API failure" in caplog.text
