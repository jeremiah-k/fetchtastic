"""
Test notifications module for Fetchtastic.

This module tests notification functionality including NTFY server integration,
download completion notifications, and up-to-date notifications.
"""

from unittest.mock import MagicMock, patch

import pytest

from fetchtastic import notifications

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]


class TestSendNtfyNotification:
    """Test suite for send_ntfy_notification function."""

    @patch("fetchtastic.notifications.requests.post")
    @patch("fetchtastic.notifications.logger")
    def test_send_notification_success(self, mock_logger, mock_post):
        """Test successful notification sending."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        notifications.send_ntfy_notification(
            "https://ntfy.sh", "test-topic", "Test message", "Test title"
        )

        mock_post.assert_called_once_with(
            "https://ntfy.sh/test-topic",
            data=b"Test message",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Title": "Test title",
            },
            timeout=notifications.NTFY_REQUEST_TIMEOUT,
        )
        mock_logger.debug.assert_called_once_with(
            "Notification sent to https://ntfy.sh/test-topic"
        )

    @patch("fetchtastic.notifications.requests.post")
    @patch("fetchtastic.notifications.logger")
    def test_send_notification_without_title(self, mock_logger, mock_post):
        """Test notification sending without title."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        notifications.send_ntfy_notification(
            "https://ntfy.sh", "test-topic", "Test message"
        )

        mock_post.assert_called_once_with(
            "https://ntfy.sh/test-topic",
            data=b"Test message",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=notifications.NTFY_REQUEST_TIMEOUT,
        )

    @patch("fetchtastic.notifications.requests.post")
    @patch("fetchtastic.notifications.logger")
    def test_send_notification_failure(self, mock_logger, mock_post):
        """Test notification sending failure."""
        import requests

        mock_post.side_effect = requests.exceptions.RequestException("Network error")

        notifications.send_ntfy_notification(
            "https://ntfy.sh", "test-topic", "Test message"
        )

        mock_logger.warning.assert_called_once()
        assert "Network error" in mock_logger.warning.call_args[0][0]

    def test_send_notification_missing_server(self):
        """Test that no notification is sent when server is missing."""
        with patch("fetchtastic.notifications.requests.post") as mock_post:
            notifications.send_ntfy_notification(None, "test-topic", "Test message")
            mock_post.assert_not_called()

    def test_send_notification_missing_topic(self):
        """Test that no notification is sent when topic is missing."""
        with patch("fetchtastic.notifications.requests.post") as mock_post:
            notifications.send_ntfy_notification(
                "https://ntfy.sh", None, "Test message"
            )
            mock_post.assert_not_called()


class TestSendDownloadCompletionNotification:
    """Test suite for send_download_completion_notification function."""

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_completion_notification_firmware_only(self, mock_datetime, mock_send):
        """Test completion notification for firmware downloads only."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_download_completion_notification(
            config, ["2.7.4", "2.8.0"], []
        )

        expected_message = (
            "Downloaded Firmware versions: 2.7.4, 2.8.0\n2024-01-01T12:00:00"
        )
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Download Completed",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_completion_notification_apk_only(self, mock_datetime, mock_send):
        """Test completion notification for APK downloads only."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_download_completion_notification(
            config, [], ["1.2.3", "1.3.0"]
        )

        expected_message = (
            "Downloaded Android APK versions: 1.2.3, 1.3.0\n2024-01-01T12:00:00"
        )
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Download Completed",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_completion_notification_both(self, mock_datetime, mock_send):
        """Test completion notification for both firmware and APK downloads."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_download_completion_notification(
            config, ["2.7.4"], ["1.2.3"]
        )

        expected_message = "Downloaded Firmware versions: 2.7.4\nDownloaded Android APK versions: 1.2.3\n2024-01-01T12:00:00"
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Download Completed",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    def test_send_completion_notification_no_downloads(self, mock_send):
        """Test that no notification is sent when no downloads occurred."""
        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_download_completion_notification(config, [], [])
        mock_send.assert_not_called()


class TestSendNewReleasesAvailableNotification:
    """Test suite for send_new_releases_available_notification function."""

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_new_releases_notification_firmware_only(
        self, mock_datetime, mock_send
    ):
        """Test new releases notification for firmware only."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_new_releases_available_notification(
            config, ["2.7.4", "2.8.0"], [], "Downloads skipped due to dry-run"
        )

        expected_message = "Downloads skipped due to dry-run\nFirmware versions available: 2.7.4, 2.8.0\n2024-01-01T12:00:00"
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Downloads Skipped",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_new_releases_notification_apk_only(self, mock_datetime, mock_send):
        """Test new releases notification for APK only."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_new_releases_available_notification(
            config, [], ["1.2.3", "1.3.0"]
        )

        expected_message = (
            "Android APK versions available: 1.2.3, 1.3.0\n2024-01-01T12:00:00"
        )
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Downloads Skipped",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    def test_send_new_releases_notification_disabled_download_only(self, mock_send):
        """Test that download-only setting suppresses new releases notifications."""
        config = {
            "NTFY_SERVER": "https://ntfy.sh",
            "NTFY_TOPIC": "test",
            "NOTIFY_ON_DOWNLOAD_ONLY": True,
        }
        notifications.send_new_releases_available_notification(
            config, ["2.7.4"], ["1.2.3"]
        )

        mock_send.assert_not_called()

    @patch("fetchtastic.notifications.send_ntfy_notification")
    def test_send_new_releases_notification_no_releases(self, mock_send):
        """Test that no notification is sent when no new releases."""
        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_new_releases_available_notification(config, [], [])
        mock_send.assert_not_called()


class TestSendUpToDateNotification:
    """Test suite for send_up_to_date_notification function."""

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_up_to_date_notification_enabled(self, mock_datetime, mock_send):
        """Test up-to-date notification when enabled."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {
            "NTFY_SERVER": "https://ntfy.sh",
            "NTFY_TOPIC": "test",
            "NOTIFY_ON_DOWNLOAD_ONLY": False,
        }
        notifications.send_up_to_date_notification(config)

        expected_message = "All assets are up to date.\n2024-01-01T12:00:00"
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Up to Date",
        )

    @patch("fetchtastic.notifications.send_ntfy_notification")
    def test_send_up_to_date_notification_disabled(self, mock_send):
        """Test that no up-to-date notification is sent when disabled."""
        config = {
            "NTFY_SERVER": "https://ntfy.sh",
            "NTFY_TOPIC": "test",
            "NOTIFY_ON_DOWNLOAD_ONLY": True,
        }
        notifications.send_up_to_date_notification(config)
        mock_send.assert_not_called()

    @patch("fetchtastic.notifications.send_ntfy_notification")
    @patch("fetchtastic.notifications.datetime")
    def test_send_up_to_date_notification_default_disabled(
        self, mock_datetime, mock_send
    ):
        """Test up-to-date notification with default NOTIFY_ON_DOWNLOAD_ONLY (False)."""
        mock_datetime.now.return_value.astimezone.return_value.isoformat.return_value = (
            "2024-01-01T12:00:00"
        )

        config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}
        notifications.send_up_to_date_notification(config)

        expected_message = "All assets are up to date.\n2024-01-01T12:00:00"
        mock_send.assert_called_once_with(
            "https://ntfy.sh",
            "test",
            expected_message,
            title="Fetchtastic Up to Date",
        )
