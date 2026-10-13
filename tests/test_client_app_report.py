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


@pytest.mark.parametrize(
    "failure_stage", ["token_normalization", "initialization", "cache_clear"]
)
def test_failed_run_suppresses_up_to_date_and_recovers(mocker, failure_stage):
    integration = DownloadCLIIntegration()
    config = {"SAVE_CLIENT_APPS": True}
    orchestrator = mocker.Mock(
        download_results=[],
        failed_downloads=[],
        nightly_run_state=None,
        release_check_failed=False,
        pipeline_lock_skipped=False,
        wifi_skipped=False,
    )
    orchestrator.run_download_pipeline.return_value = ([], [])
    orchestrator.get_latest_versions.return_value = {}
    normalize_token = mocker.patch(
        "fetchtastic.download.cli_integration.get_effective_github_token",
        return_value=None,
        side_effect=(
            ValueError("token normalization failed")
            if failure_stage == "token_normalization"
            else None
        ),
    )

    def initialize(config):
        integration.config = config
        integration.orchestrator = orchestrator

    initialization = mocker.patch.object(
        integration,
        "_initialize_components",
        side_effect=(
            OSError("initialization failed")
            if failure_stage == "initialization"
            else initialize
        ),
    )
    integration.config = config
    mocker.patch.object(integration, "_clear_caches", return_value=False)
    mocker.patch.object(integration, "get_latest_versions", return_value={})
    notify = mocker.patch(
        "fetchtastic.download.cli_integration.send_up_to_date_notification"
    )
    log = mocker.Mock()

    def summarize(report):
        integration.log_download_results_summary(
            elapsed_seconds=1,
            logger_override=log,
            **report._asdict(),
        )

    report = integration.main(config, force_refresh=True)
    assert report == DownloadReport.empty()
    assert integration.download_run_failed is True
    summarize(report)
    notify.assert_not_called()
    assert not any("up to date" in str(call) for call in log.info.call_args_list)
    assert any("Download run failed" in str(call) for call in log.info.call_args_list)

    initialization.side_effect = initialize
    normalize_token.side_effect = None
    report = integration.main(config)
    assert integration.download_run_failed is False
    summarize(report)
    notify.assert_called_once_with(config)


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
