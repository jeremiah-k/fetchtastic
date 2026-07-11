import time
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import FILE_TYPE_APP_SNAPSHOT
from fetchtastic.download.cli_integration import DownloadCLIIntegration
from fetchtastic.download.interfaces import DownloadResult

pytestmark = [pytest.mark.user_interface, pytest.mark.unit]


@pytest.fixture
def integration(mocker):
    integration = DownloadCLIIntegration()
    integration.config = {
        "NTFY_SERVER": "https://ntfy.sh",
        "NTFY_TOPIC": "fetchtastic",
    }
    mocker.patch(
        "fetchtastic.download.cli_integration.get_api_request_summary", return_value={}
    )
    mocker.patch("fetchtastic.download.cli_integration.time", wraps=time)
    integration.orchestrator = mocker.MagicMock()
    integration.orchestrator.wifi_skipped = False
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": "v2.8.0",
        "android": "v1.8.0",
        "firmware_prerelease": "firmware-2.8.0",
        "android_prerelease": "v1.8.0-rc1",
        "desktop": "",
        "desktop_prerelease": "",
    }
    return integration


def _call_summary(
    integration,
    downloaded_fw,
    downloaded_apks,
    failed=None,
    new_fw=None,
    new_apks=None,
    downloaded_fw_prereleases=None,
    downloaded_apk_prereleases=None,
    downloaded_desktop=None,
    downloaded_desktop_prereleases=None,
    new_desktop=None,
):
    failed = failed or []
    new_fw = new_fw or []
    new_apks = new_apks or []
    downloaded_fw_prereleases = downloaded_fw_prereleases or []
    downloaded_apk_prereleases = downloaded_apk_prereleases or []
    downloaded_desktop = downloaded_desktop or []
    downloaded_desktop_prereleases = downloaded_desktop_prereleases or []
    new_desktop = new_desktop or []
    integration.log_download_results_summary(
        elapsed_seconds=1.2,
        downloaded_firmwares=downloaded_fw,
        downloaded_apks=downloaded_apks,
        downloaded_firmware_prereleases=downloaded_fw_prereleases,
        downloaded_apk_prereleases=downloaded_apk_prereleases,
        downloaded_desktop=downloaded_desktop,
        downloaded_desktop_prereleases=downloaded_desktop_prereleases,
        failed_downloads=failed,
        latest_firmware_version="v2.8.0",
        latest_apk_version="v1.8.0",
        latest_desktop_version="",
        new_firmware_versions=new_fw,
        new_apk_versions=new_apks,
        new_desktop_versions=new_desktop,
    )


def test_summary_sends_completion_notification(integration):
    with (
        patch(
            "fetchtastic.download.cli_integration.send_download_completion_notification"
        ) as mock_completion,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, ["v2.8.0"], ["v1.8.1"], [])
        mock_completion.assert_called_once_with(
            integration.config,
            ["v2.8.0"],
            ["v1.8.1"],
            [],
            [],
            [],
            [],
            downloaded_app_snapshots=[],
        )
        mock_up_to_date.assert_not_called()


def test_summary_sends_up_to_date_notification_when_no_download(integration):
    integration.config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
    with (
        patch(
            "fetchtastic.download.cli_integration.send_download_completion_notification"
        ) as mock_completion,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], [])
        mock_completion.assert_not_called()
        mock_up_to_date.assert_called_once_with(integration.config)


def test_summary_calls_up_to_date_when_download_only_setting_true(integration):
    integration.config["NOTIFY_ON_DOWNLOAD_ONLY"] = True
    with (
        patch(
            "fetchtastic.download.cli_integration.send_download_completion_notification"
        ) as mock_completion,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], [])
        mock_completion.assert_not_called()
        mock_up_to_date.assert_called_once_with(integration.config)
        assert integration.config["NOTIFY_ON_DOWNLOAD_ONLY"] is True


def test_summary_skips_up_to_date_notification_when_new_versions_available(integration):
    with (
        patch(
            "fetchtastic.download.cli_integration.send_download_completion_notification"
        ) as mock_completion,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], new_fw=["v3.0.0"], new_apks=[])
        mock_completion.assert_not_called()
        mock_up_to_date.assert_not_called()


def test_summary_skips_up_to_date_with_new_versions_when_download_only(integration):
    integration.config["NOTIFY_ON_DOWNLOAD_ONLY"] = True
    with patch(
        "fetchtastic.download.cli_integration.send_up_to_date_notification"
    ) as mock_up_to_date:
        _call_summary(integration, [], [], new_fw=["v3.0.0"], new_apks=[])
        mock_up_to_date.assert_not_called()


def test_summary_does_not_send_up_to_date_notification_on_failures(integration):
    with patch(
        "fetchtastic.download.cli_integration.send_up_to_date_notification"
    ) as mock_up_to_date:
        _call_summary(
            integration,
            [],
            [],
            failed=[
                {
                    "type": "Firmware",
                    "release_tag": "v1.0.0",
                    "file_name": "firmware.bin",
                    "url": "https://example.invalid/fw",
                    "error": "failed",
                }
            ],
        )
        mock_up_to_date.assert_not_called()


def test_summary_sends_skip_notification_when_wifi_skipped(integration):
    integration.orchestrator.wifi_skipped = True
    integration.orchestrator.available_new_firmware_versions = []
    integration.orchestrator.available_new_apk_versions = []
    with (
        patch(
            "fetchtastic.download.cli_integration.send_new_releases_available_notification"
        ) as mock_skip,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], new_fw=[], new_apks=[])
        mock_skip.assert_called_once_with(
            integration.config,
            [],
            [],
            downloads_skipped_reason="Downloads skipped: not connected to Wi-Fi.",
        )
        mock_up_to_date.assert_not_called()


def test_summary_sends_skip_notification_with_discovered_versions(integration):
    integration.orchestrator.wifi_skipped = True
    integration.orchestrator.available_new_firmware_versions = ["v2.7.20"]
    integration.orchestrator.available_new_apk_versions = ["v2.7.10"]

    with (
        patch(
            "fetchtastic.download.cli_integration.send_new_releases_available_notification"
        ) as mock_skip,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], new_fw=[], new_apks=[])
        mock_skip.assert_called_once_with(
            integration.config,
            ["v2.7.20"],
            ["v2.7.10"],
            downloads_skipped_reason="Downloads skipped: not connected to Wi-Fi.",
        )
        mock_up_to_date.assert_not_called()


# ------------------------------------------------------------------
# Snapshot versionCode extraction
# ------------------------------------------------------------------


def test_extract_snapshot_version_code_from_path():
    """_extract_snapshot_version_code parses versionCode from canonical path."""
    result = Mock()
    result.file_path = (
        "/data/app/snapshots/29321447/" "androidApp-fdroid-universal-debug-29321447.apk"
    )
    result.download_url = (
        "http://github.com/download/snapshot/"
        "androidApp-fdroid-universal-debug-29321447.apk"
    )
    vc = DownloadCLIIntegration._extract_snapshot_version_code(result)
    assert vc == "29321447"


def test_extract_snapshot_version_code_fallback_from_url():
    result = Mock()
    result.file_path = None
    result.download_url = (
        "http://github.com/release/snapshot/" "androidApp-google-universal-debug-42.apk"
    )
    vc = DownloadCLIIntegration._extract_snapshot_version_code(result)
    assert vc == "42"


# ------------------------------------------------------------------
# Populated snapshot notification (versionCode dedup + skip exclusion)
# ------------------------------------------------------------------


def test_populated_snapshot_notification_displays_version_code(integration):
    """Populated snapshot results produce versionCode in notification, deduplicated, skipped excluded."""
    integration.config["NOTIFY_ON_SNAPSHOTS"] = True
    vc_dir = "/data/app/snapshots/29321447"
    integration.orchestrator.download_results = [
        DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path=f"{vc_dir}/androidApp-fdroid-universal-debug-29321447.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
            was_skipped=False,
        ),
        DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path=f"{vc_dir}/androidApp-google-universal-debug-29321447.apk",
            download_url="http://y",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
            was_skipped=False,
        ),
        DownloadResult(
            success=True,
            release_tag="snapshot",
            file_path=f"{vc_dir}/androidApp-fdroid-arm64-v8a-debug-29321447.apk",
            download_url="http://z",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
            was_skipped=True,
        ),
    ]

    with patch(
        "fetchtastic.download.cli_integration.send_download_completion_notification"
    ) as mock_notify:
        integration.log_download_results_summary(
            logger_override=Mock(),
            elapsed_seconds=1.0,
            downloaded_firmwares=[],
            downloaded_apks=[],
            failed_downloads=[],
            latest_firmware_version="",
            latest_apk_version="",
        )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["downloaded_app_snapshots"] == ["29321447"]


# ------------------------------------------------------------------
# Failed snapshot isolation (P1 proof)
# ------------------------------------------------------------------


def test_failed_snapshot_not_reported_as_downloaded(integration):
    """Failed snapshot results must NOT appear in downloaded_app_snapshots."""
    integration.orchestrator = Mock()
    integration.orchestrator.wifi_skipped = False
    integration.orchestrator.download_results = [
        DownloadResult(
            success=False,
            release_tag="snapshot",
            file_path="/data/app/snapshots/29321447/androidApp-fdroid-universal-debug-29321447.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
            was_skipped=False,
        ),
    ]
    integration.orchestrator.log_firmware_release_history_summary = Mock()
    integration.orchestrator.get_latest_versions = Mock(return_value={})

    with (
        patch(
            "fetchtastic.download.cli_integration.send_download_completion_notification"
        ) as mock_notify,
        patch("fetchtastic.download.cli_integration.send_up_to_date_notification"),
    ):
        integration.log_download_results_summary(
            logger_override=Mock(),
            elapsed_seconds=1.0,
            downloaded_firmwares=[],
            downloaded_apks=[],
            failed_downloads=[],
            latest_firmware_version="",
            latest_apk_version="",
        )
    # No successful downloads → completion notification must not fire.
    mock_notify.assert_not_called()


def test_failed_snapshot_plus_successful_firmware(integration):
    """A failed snapshot must not appear in the notification when firmware succeeded."""
    integration.orchestrator = Mock()
    integration.orchestrator.wifi_skipped = False
    integration.orchestrator.download_results = [
        DownloadResult(
            success=False,
            release_tag="snapshot",
            file_path="/data/app/snapshots/29321447/androidApp-fdroid-universal-debug-29321447.apk",
            download_url="http://x",
            file_size=1,
            file_type=FILE_TYPE_APP_SNAPSHOT,
            was_skipped=False,
        ),
    ]
    integration.orchestrator.log_firmware_release_history_summary = Mock()
    integration.orchestrator.get_latest_versions = Mock(return_value={})

    with patch(
        "fetchtastic.download.cli_integration.send_download_completion_notification"
    ) as mock_notify:
        integration.log_download_results_summary(
            logger_override=Mock(),
            elapsed_seconds=1.0,
            downloaded_firmwares=["v2.8.0"],
            downloaded_apks=[],
            failed_downloads=[],
            latest_firmware_version="v2.8.0",
            latest_apk_version="",
        )

    mock_notify.assert_called_once()
    # The failed snapshot must NOT be counted — snapshot list stays empty.
    assert mock_notify.call_args.kwargs["downloaded_app_snapshots"] == []


# ------------------------------------------------------------------
# Direct notification function tests
# ------------------------------------------------------------------


def test_send_download_completion_notification_with_snapshots():
    """Direct call includes snapshot versionCode in the notification message."""
    from fetchtastic.notifications import send_download_completion_notification

    config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}

    with patch("fetchtastic.notifications.send_ntfy_notification") as mock_ntfy:
        send_download_completion_notification(
            config,
            ["v2.8.0"],
            ["v1.8.1"],
            downloaded_app_snapshots=["29321447"],
        )

    mock_ntfy.assert_called_once()
    message = mock_ntfy.call_args.args[2]
    assert "29321447" in message
    assert "snapshot debug builds" in message.lower()


def test_send_download_completion_notification_empty_snapshots_no_notification():
    """All lists empty (including snapshots) → no notification sent."""
    from fetchtastic.notifications import send_download_completion_notification

    config = {"NTFY_SERVER": "https://ntfy.sh", "NTFY_TOPIC": "test"}

    with patch("fetchtastic.notifications.send_ntfy_notification") as mock_ntfy:
        send_download_completion_notification(
            config, [], [], downloaded_app_snapshots=[]
        )

    mock_ntfy.assert_not_called()
