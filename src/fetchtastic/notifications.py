"""
Notification utilities for Fetchtastic.

This module provides functionality to send notifications via NTFY servers
when downloads are completed or when new releases are available.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import NTFY_REQUEST_TIMEOUT
from fetchtastic.log_utils import logger


def send_ntfy_notification(
    ntfy_server: Optional[str],
    ntfy_topic: Optional[str],
    message: str,
    title: Optional[str] = None,
) -> None:
    """
    Send a notification to an NTFY server topic.

    If both ntfy_server and ntfy_topic are provided, posts the given message
    (and optional title) to the constructed NTFY URL. Logs a debug message
    on success and logs a warning if the HTTP request fails. If either ntfy_server
    or ntfy_topic is missing, the function does nothing.

    Parameters:
        ntfy_server (Optional[str]): NTFY server URL (e.g., "https://ntfy.sh").
        ntfy_topic (Optional[str]): NTFY topic name.
        message (str): Message body to send.
        title (Optional[str]): Optional title for the notification.

    Side effects:
        - Sends HTTP POST request to NTFY server if configured.
        - Logs debug message on success or warning on failure.
    """
    if ntfy_server and ntfy_topic:
        ntfy_url: str = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
        try:
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
            }
            if title:
                # Encode UTF-8 bytes as latin-1 to pass through requests headers
                # This pattern works because NTFY server interprets the bytes as UTF-8
                headers["Title"] = title.encode("utf-8").decode("latin-1")
            response: requests.Response = requests.post(
                ntfy_url,
                data=message.encode("utf-8"),
                headers=headers,
                timeout=NTFY_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            logger.debug(f"Notification sent to {ntfy_url}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error sending notification to {ntfy_url}: {e}")


def send_download_completion_notification(
    config: Dict[str, Any],
    downloaded_firmwares: List[str],
    downloaded_apks: List[str],
    downloaded_firmware_prereleases: Optional[List[str]] = None,
    downloaded_apk_prereleases: Optional[List[str]] = None,
) -> None:
    """
    Send notification when downloads are completed successfully.

    Parameters:
        config (Dict[str, Any]): Configuration containing NTFY settings.
        downloaded_firmwares (List[str]): List of firmware versions that were downloaded.
        downloaded_apks (List[str]): List of APK versions that were downloaded.
        downloaded_firmware_prereleases (Optional[List[str]]): List of firmware prerelease versions that were downloaded.
        downloaded_apk_prereleases (Optional[List[str]]): List of APK prerelease versions that were downloaded.

    Side effects:
        - Sends notification to configured NTFY server/topic if downloads occurred.
    """
    ntfy_server = config.get("NTFY_SERVER", "")
    ntfy_topic = config.get("NTFY_TOPIC", "")

    downloaded_firmware_prereleases = downloaded_firmware_prereleases or []
    downloaded_apk_prereleases = downloaded_apk_prereleases or []

    if (
        not downloaded_firmwares
        and not downloaded_apks
        and not downloaded_firmware_prereleases
        and not downloaded_apk_prereleases
    ):
        return  # No downloads, no notification needed

    notification_messages: List[str] = []

    if downloaded_firmwares:
        message = f"Downloaded Firmware versions: {', '.join(downloaded_firmwares)}"
        notification_messages.append(message)

    if downloaded_firmware_prereleases:
        message = f"Downloaded Firmware prerelease versions: {', '.join(downloaded_firmware_prereleases)}"
        notification_messages.append(message)

    if downloaded_apks:
        message = f"Downloaded Android APK versions: {', '.join(downloaded_apks)}"
        notification_messages.append(message)

    if downloaded_apk_prereleases:
        message = f"Downloaded Android APK prerelease versions: {', '.join(downloaded_apk_prereleases)}"
        notification_messages.append(message)

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    notification_messages.append(timestamp)
    notification_message = "\n".join(notification_messages)

    send_ntfy_notification(
        ntfy_server,
        ntfy_topic,
        notification_message,
        title="Fetchtastic Download Completed",
    )


def send_new_releases_available_notification(
    config: Dict[str, Any],
    new_firmware_versions: List[str],
    new_apk_versions: List[str],
    downloads_skipped_reason: Optional[str] = None,
) -> None:
    """
    Notify the configured NTFY topic about new firmware or APK releases when downloads were skipped.

    Parameters:
        config (Dict[str, Any]): Configuration dictionary. Recognized keys:
            - "NTFY_SERVER": NTFY server base URL.
            - "NTFY_TOPIC": NTFY topic to post the notification to.
            - "NOTIFY_ON_DOWNLOAD_ONLY": if True, suppresses this notification.
        new_firmware_versions (List[str]): Available firmware version identifiers to report.
        new_apk_versions (List[str]): Available Android APK version identifiers to report.
        downloads_skipped_reason (Optional[str]): Human-readable reason why downloads were skipped;
            if provided it is included as the first line of the notification.

    Behavior:
        If "NOTIFY_ON_DOWNLOAD_ONLY" is True or there are no new firmware/APK versions, no notification is sent.
    """
    ntfy_server = config.get("NTFY_SERVER", "")
    ntfy_topic = config.get("NTFY_TOPIC", "")
    notify_on_download_only = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)

    if notify_on_download_only:
        return

    if not new_firmware_versions and not new_apk_versions:
        return  # No new releases, no notification needed

    message_lines: List[str] = []

    if downloads_skipped_reason:
        message_lines.append(downloads_skipped_reason)

    if new_firmware_versions:
        message_lines.append(
            f"Firmware versions available: {', '.join(new_firmware_versions)}"
        )

    if new_apk_versions:
        message_lines.append(
            f"Android APK versions available: {', '.join(new_apk_versions)}"
        )

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    message_lines.append(timestamp)
    notification_message = "\n".join(message_lines)

    send_ntfy_notification(
        ntfy_server,
        ntfy_topic,
        notification_message,
        title="Fetchtastic Downloads Skipped",
    )


def send_up_to_date_notification(config: Dict[str, Any]) -> None:
    """
    Send notification when all assets are up to date (if configured to do so).

    Parameters:
        config (Dict[str, Any]): Configuration containing NTFY settings.

    Side effects:
        - Sends notification to configured NTFY server/topic if not configured for download-only notifications.
    """
    ntfy_server = config.get("NTFY_SERVER", "")
    ntfy_topic = config.get("NTFY_TOPIC", "")
    notify_on_download_only = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)

    # Only send "up to date" notifications if not configured for download-only notifications
    if notify_on_download_only:
        return

    message = f"All assets are up to date.\n{datetime.now().astimezone().isoformat(timespec='seconds')}"

    send_ntfy_notification(
        ntfy_server,
        ntfy_topic,
        message,
        title="Fetchtastic Up to Date",
    )
