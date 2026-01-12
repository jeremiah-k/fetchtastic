from pathlib import Path

import pytest

from fetchtastic.constants import APKS_DIR_NAME, FIRMWARE_DIR_NAME
from fetchtastic.download.cli_integration import DownloadCLIIntegration
from fetchtastic.download.interfaces import DownloadResult


@pytest.mark.unit
@pytest.mark.core_downloads
def test_skipped_downloads_not_reported_as_new_versions(tmp_path):
    integration = DownloadCLIIntegration()
    # Initialize the components
    integration.config = {"DOWNLOAD_DIR": str(tmp_path)}
    integration.orchestrator = type(
        "Orchestrator",
        (),
        {
            "get_latest_versions": lambda self: {
                "android": "v1.0.0",
                "firmware": "v1.0.0",
            }
        },
    )()

    # Mock the downloaders
    mock_android = type(
        "MockAndroidDownloader",
        (),
        {
            "get_latest_release_tag": lambda self: "v1.0.0",
            "version_manager": type(
                "MockVersionManager",
                (),
                {"compare_versions": lambda self, v1, v2: 1 if v1 > v2 else -1},
            )(),
            "get_version_manager": lambda self: self.version_manager,
        },
    )()
    mock_firmware = type(
        "MockFirmwareDownloader", (), {"get_latest_release_tag": lambda self: "v1.0.0"}
    )()

    integration.android_downloader = mock_android  # type: ignore
    integration.firmware_downloader = mock_firmware  # type: ignore

    skipped_firmware = DownloadResult(
        success=True,
        release_tag="v2.0.0",
        file_path=Path(tmp_path / FIRMWARE_DIR_NAME / "v2.0.0" / "firmware.zip"),
        file_type="firmware",
        was_skipped=True,
    )
    skipped_android = DownloadResult(
        success=True,
        release_tag="v2.0.0",
        file_path=Path(tmp_path / APKS_DIR_NAME / "v2.0.0" / "app.apk"),
        file_type="android",
        was_skipped=True,
    )

    (
        downloaded,
        new_fw,
        apks,
        new_apks,
        _downloaded_firmware_prereleases,
        _downloaded_apk_prereleases,
    ) = integration._convert_results_to_legacy_format(
        [skipped_firmware, skipped_android]
    )

    assert downloaded == []
    # Skipped assets should not be reported as new versions
    assert new_fw == []
    assert apks == []
    assert new_apks == []
    assert _downloaded_firmware_prereleases == []
    assert _downloaded_apk_prereleases == []
