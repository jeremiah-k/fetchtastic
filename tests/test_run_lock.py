"""Tests for the cross-process download run lock."""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import RUN_LOCK_FILENAME
from fetchtastic.download.run_lock import RunLock, RunLockAcquireResult

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def test_acquire_holds_os_lock_and_release_makes_it_available(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))

    assert first.acquire() is RunLockAcquireResult.ACQUIRED
    assert first.locked is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()

    second = RunLock(str(lock_path))
    assert second.acquire() is RunLockAcquireResult.CONTENDED
    assert second.locked is False

    first.release()
    assert first.locked is False
    # The rendezvous path persists, but no process owns it now.
    assert lock_path.exists()
    replacement = RunLock(str(lock_path))
    assert replacement.acquire() is RunLockAcquireResult.ACQUIRED
    replacement.release()


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    with RunLock(str(lock_path)) as lock:
        assert lock.locked is True
        assert lock_path.exists()
    replacement = RunLock(str(lock_path))
    assert replacement.acquire() is RunLockAcquireResult.ACQUIRED
    replacement.release()


def test_holder_description_comes_from_diagnostic_payload(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))
    assert first.acquire() is RunLockAcquireResult.ACQUIRED

    second = RunLock(str(lock_path))
    assert second.acquire() is RunLockAcquireResult.CONTENDED
    description = second.describe_holder()
    assert f"PID {os.getpid()}" in description
    assert "started " in description
    first.release()


def test_old_mtime_never_steals_live_os_lock(tmp_path):
    """A live lock is authoritative regardless of timestamps or run duration."""
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))
    assert first.acquire() is RunLockAcquireResult.ACQUIRED
    os.utime(lock_path, (1, 1))

    second = RunLock(str(lock_path))
    assert second.acquire() is RunLockAcquireResult.CONTENDED
    first.release()


def test_idle_rendezvous_file_never_blocks_next_run(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    lock_path.write_text("stale diagnostic bytes", encoding="utf-8")
    os.utime(lock_path, (1, 1))

    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    lock.release()


def test_payload_write_failure_returns_unavailable_without_holding_lock(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))

    def _fail_write(_handle, _payload):
        raise OSError("disk full")

    monkeypatch.setattr(first, "_write_payload", _fail_write)
    assert first.acquire() is RunLockAcquireResult.UNAVAILABLE
    assert first.locked is False

    # A failed metadata write cannot strand or simulate contention. The next
    # process acquires the OS lock immediately even if the rendezvous file exists.
    second = RunLock(str(lock_path))
    assert second.acquire() is RunLockAcquireResult.ACQUIRED
    second.release()


def test_open_failure_returns_unavailable(tmp_path):
    parent_as_file = tmp_path / "not-a-directory"
    parent_as_file.write_text("x", encoding="utf-8")
    lock = RunLock(str(parent_as_file / RUN_LOCK_FILENAME))

    assert lock.acquire() is RunLockAcquireResult.UNAVAILABLE
    assert lock.locked is False


def _patched_pipeline(orchestrator, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        orchestrator, "_process_firmware_downloads", lambda: calls.append("firmware")
    )
    monkeypatch.setattr(
        orchestrator, "_process_client_app_downloads", lambda: calls.append("app")
    )
    monkeypatch.setattr(
        orchestrator, "_enhance_download_results_with_metadata", lambda: None
    )
    monkeypatch.setattr(orchestrator, "_retry_failed_downloads", lambda: None)
    monkeypatch.setattr(
        orchestrator, "_finalize_nightly_transaction_if_complete", lambda: None
    )
    monkeypatch.setattr(orchestrator, "_log_download_summary", lambda *_: None)
    return calls


def test_pipeline_skips_only_for_real_contention(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    holder = RunLock(str(tmp_path / RUN_LOCK_FILENAME))
    assert holder.acquire() is RunLockAcquireResult.ACQUIRED

    orchestrator = DownloadOrchestrator(config)
    calls = _patched_pipeline(orchestrator, monkeypatch)
    results, failures = orchestrator.run_download_pipeline()

    assert results == [] and failures == []
    assert calls == []
    assert orchestrator.pipeline_lock_skipped is True
    holder.release()


def test_pipeline_runs_and_releases_kernel_lock(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    orchestrator = DownloadOrchestrator(config)
    calls = _patched_pipeline(orchestrator, monkeypatch)

    results, failures = orchestrator.run_download_pipeline()

    assert results == [] and failures == []
    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls and "app" in calls
    # The persistent rendezvous file is idle and immediately re-acquirable.
    probe = RunLock(str(tmp_path / RUN_LOCK_FILENAME))
    assert probe.acquire() is RunLockAcquireResult.ACQUIRED
    probe.release()


def test_pipeline_runs_unlocked_when_locking_is_unavailable(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    orchestrator = DownloadOrchestrator({"DOWNLOAD_DIR": str(tmp_path)})
    calls = _patched_pipeline(orchestrator, monkeypatch)
    monkeypatch.setattr(
        orchestrator,
        "_acquire_run_lock",
        lambda: RunLockAcquireResult.UNAVAILABLE,
    )

    orchestrator.run_download_pipeline()

    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls and "app" in calls


def test_pipeline_runs_unlocked_without_download_dir(monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    orchestrator = DownloadOrchestrator({"DOWNLOAD_DIR": ""})
    calls = _patched_pipeline(orchestrator, monkeypatch)

    orchestrator.run_download_pipeline()

    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls and "app" in calls


def test_cli_summary_reports_skipped_run_instead_of_up_to_date(tmp_path):
    from fetchtastic.constants import NightlyRunState
    from fetchtastic.download.cli_integration import DownloadCLIIntegration

    integration = DownloadCLIIntegration()
    integration.config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "NOTIFY_ON_DOWNLOAD_ONLY": True,
    }
    integration.orchestrator = Mock()
    integration.orchestrator.nightly_run_state = NightlyRunState.UNCHECKED
    integration.orchestrator.latest_firmware_nightly_build_id = None
    integration.orchestrator.wifi_skipped = False
    integration.orchestrator.release_check_failed = False
    integration.orchestrator.pipeline_lock_skipped = True
    integration.orchestrator.download_results = []
    integration.orchestrator.failed_downloads = []
    integration.orchestrator.available_new_firmware_versions = []
    integration.orchestrator.available_new_apk_versions = []
    integration.orchestrator.get_latest_versions = Mock(return_value={})

    mock_log = Mock()
    with (
        patch(
            "fetchtastic.download.cli_integration.send_up_to_date_notification"
        ) as mock_up_to_date,
        patch(
            "fetchtastic.download.cli_integration.get_api_request_summary",
            return_value={},
        ),
    ):
        integration.log_download_results_summary(
            logger_override=mock_log,
            elapsed_seconds=1.0,
            downloaded_firmwares=[],
            downloaded_apks=[],
            failed_downloads=[],
            latest_firmware_version="",
            latest_apk_version="",
        )

    mock_up_to_date.assert_not_called()
    logged = " ".join(str(call) for call in mock_log.info.call_args_list)
    assert "another fetchtastic download run is active" in logged
    assert "up to date" not in logged
