import time
from unittest.mock import patch

import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration

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
    integration.orchestrator.get_latest_versions.return_value = {
        "firmware": "v2.8.0",
        "android": "v1.8.0",
        "firmware_prerelease": "firmware-2.8.0",
        "android_prerelease": "v1.8.0-rc1",
    }
    return integration


def _call_summary(
    integration,
    downloaded_fw,
    downloaded_apks,
    failed=None,
    new_fw=None,
    new_apks=None,
):
    failed = failed or []
    new_fw = new_fw or []
    new_apks = new_apks or []
    integration.log_download_results_summary(
        elapsed_seconds=1.2,
        downloaded_firmwares=downloaded_fw,
        downloaded_apks=downloaded_apks,
        failed_downloads=failed,
        latest_firmware_version="v2.8.0",
        latest_apk_version="v1.8.0",
        new_firmware_versions=new_fw,
        new_apk_versions=new_apks,
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
            integration.config, ["v2.8.0"], ["v1.8.1"]
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


def test_summary_calls_up_to_date_even_when_download_only_setting_true(integration):
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
        mock_up_to_date.assert_called_once()
        assert mock_up_to_date.call_args[0][0]["NOTIFY_ON_DOWNLOAD_ONLY"] is True


def test_summary_sends_new_releases_notification(integration):
    with (
        patch(
            "fetchtastic.download.cli_integration.send_new_releases_available_notification"
        ) as mock_new_releases,
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
    ):
        _call_summary(integration, [], [], new_fw=["v3.0.0"], new_apks=[])
        mock_new_releases.assert_called_once_with(
            integration.config,
            ["v3.0.0"],
            [],
            downloads_skipped_reason="Downloads skipped because downloaded assets already match the latest releases.",
        )
        mock_up_to_date.assert_not_called()
