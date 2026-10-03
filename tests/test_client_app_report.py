"""Every installer format participates in the same release report."""

import pytest

from fetchtastic.download.cli_integration import DownloadCLIIntegration, DownloadReport
from fetchtastic.download.interfaces import DownloadResult

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.mark.parametrize("prerelease", [False, True])
def test_report_counts_mixed_installers_once(prerelease, mocker):
    integration = DownloadCLIIntegration()
    mocker.patch.object(integration, "_is_newer_version", return_value=True)
    kind = "client_app_prerelease" if prerelease else "client_app"
    results = [
        DownloadResult(
            success=True,
            release_tag="v2.8.0",
            file_type=kind,
            file_path=f"/downloads/app/v2.8.0/installer.{extension}",
        )
        for extension in ("apk", "dmg", "exe", "msi", "deb", "rpm", "AppImage")
    ]
    report = integration._collect_download_report(results, {"current": "v2.7.0"})
    versions = (
        report.downloaded_client_app_prereleases
        if prerelease
        else report.downloaded_client_apps
    )
    assert versions == ["v2.8.0"]
    assert report.new_client_app_versions == ["v2.8.0"]


def test_reports_have_independent_lists():
    first, second = DownloadReport.empty(), DownloadReport.empty()
    first.downloaded_client_apps.append("v2.8.0")
    assert second.downloaded_client_apps == []


def test_report_excludes_snapshots_and_skipped_installer_results():
    results = [
        DownloadResult(success=True, release_tag="snapshot", file_type="app_snapshot"),
        DownloadResult(
            success=True, release_tag="v2.8.0", file_type="client_app", was_skipped=True
        ),
    ]
    assert (
        DownloadCLIIntegration()._collect_download_report(results)
        == DownloadReport.empty()
    )


def test_failed_download_labels_share_client_app_family():
    from unittest.mock import Mock

    from fetchtastic.download.cli_integration import DownloadCLIIntegration
    from fetchtastic.download.interfaces import DownloadResult

    integration = DownloadCLIIntegration()
    integration.orchestrator = Mock()
    integration.orchestrator.failed_downloads = [
        DownloadResult(success=False, file_type=kind)
        for kind in (
            "android",
            "desktop",
            "client_app",
            "android_prerelease",
            "desktop_prerelease",
            "app_snapshot",
            "firmware_nightly",
        )
    ]
    assert [failure["type"] for failure in integration.get_failed_downloads()] == [
        "Client App",
        "Client App",
        "Client App",
        "Client App Prerelease",
        "Client App Prerelease",
        "Client App Snapshot",
        "Firmware Nightly",
    ]
