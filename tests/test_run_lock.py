"""Tests for the cross-process download run lock (download/run_lock.py).

The lock serializes overlapping fetchtastic download runs and must never
get stuck: a dead holder is taken over immediately (POSIX PID probe), an
unprobeable holder (Windows / another host / malformed payload) is taken
over after the TTL, and takeover/release never delete a lock they do not
own.
"""

import json
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetchtastic.constants import (
    RUN_LOCK_FILENAME,
    RUN_LOCK_STALE_TTL_SECONDS,
)
from fetchtastic.download.run_lock import RunLock

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def _write_lock(
    path: Path,
    *,
    pid=os.getpid(),
    host=platform.node(),
    acquired_at=None,
    extra=None,
) -> None:
    payload = {
        "token": "manual-test-token",
        "pid": pid,
        "host": host,
        "acquired_at": acquired_at or datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _dead_pid() -> int:
    """A PID that is provably not running (above any default pid_max)."""
    return 2**31 - 1


# ------------------------------------------------------------------
# acquire / release basics
# ------------------------------------------------------------------


def test_acquire_creates_lock_and_release_removes_it(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    lock = RunLock(str(lock_path))

    assert lock.acquire() is True
    assert lock.locked is True
    assert lock_path.exists()

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()

    lock.release()
    assert lock.locked is False
    assert not lock_path.exists()


def test_second_acquire_fails_while_holder_alive(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))
    assert first.acquire() is True

    second = RunLock(str(lock_path))
    assert second.acquire() is False
    assert second.locked is False
    # The live holder's lock is untouched by the failed attempt.
    assert lock_path.exists()

    first.release()
    assert RunLock(str(lock_path)).acquire() is True


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    with RunLock(str(lock_path)) as lock:
        assert lock.locked is True
        assert lock_path.exists()
    assert not lock_path.exists()


# ------------------------------------------------------------------
# stale takeover
# ------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="PID liveness probe is POSIX-only")
def test_dead_holder_taken_over_immediately(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    # Fresh timestamp, same host, but the holder process is gone: the PID
    # probe must recover instantly instead of waiting out the TTL.
    _write_lock(lock_path, pid=_dead_pid())

    assert RunLock(str(lock_path)).acquire() is True


@pytest.mark.skipif(os.name != "posix", reason="PID liveness probe is POSIX-only")
def test_live_same_host_holder_respected_regardless_of_age(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    old = (
        datetime.now(timezone.utc) - timedelta(seconds=RUN_LOCK_STALE_TTL_SECONDS * 10)
    ).isoformat()
    # Old timestamp but the PID (this test process) is alive: held.
    _write_lock(lock_path, pid=os.getpid(), acquired_at=old)

    assert RunLock(str(lock_path)).acquire() is False


def test_remote_host_holder_respected_until_ttl(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    _write_lock(lock_path, host="some-other-host")

    assert RunLock(str(lock_path)).acquire() is False

    old = (
        datetime.now(timezone.utc) - timedelta(seconds=RUN_LOCK_STALE_TTL_SECONDS + 60)
    ).isoformat()
    _write_lock(lock_path, host="some-other-host", acquired_at=old)

    assert RunLock(str(lock_path)).acquire() is True


def test_malformed_lock_respected_until_ttl(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    lock_path.write_text("not json at all", encoding="utf-8")
    os.utime(lock_path, (time.time(), time.time()))

    assert RunLock(str(lock_path)).acquire() is False

    stale = time.time() - (RUN_LOCK_STALE_TTL_SECONDS + 60)
    os.utime(lock_path, (stale, stale))

    assert RunLock(str(lock_path)).acquire() is True


def test_release_never_removes_successor_lock(tmp_path):
    lock_path = tmp_path / RUN_LOCK_FILENAME
    first = RunLock(str(lock_path))
    assert first.acquire() is True

    # Simulate a TTL takeover: a successor replaced our lock.
    _write_lock(lock_path, extra={"token": "successor-token"})
    first.release()

    # The successor's lock survives our release.
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["token"] == "successor-token"


# ------------------------------------------------------------------
# orchestrator integration
# ------------------------------------------------------------------


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


def test_pipeline_skips_when_run_lock_held(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    orchestrator = DownloadOrchestrator(config)
    _patched_pipeline(orchestrator, monkeypatch)

    live_lock = tmp_path / RUN_LOCK_FILENAME
    _write_lock(live_lock, pid=os.getpid())

    results, failures = orchestrator.run_download_pipeline()

    assert results == [] and failures == []
    assert orchestrator.pipeline_lock_skipped is True
    # The pipeline body never ran and the holder's lock is untouched.
    assert os.path.exists(live_lock)


def test_pipeline_creates_and_releases_lock(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    orchestrator = DownloadOrchestrator(config)
    calls = _patched_pipeline(orchestrator, monkeypatch)

    results, failures = orchestrator.run_download_pipeline()

    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls and "app" in calls
    # The lock is released after the run.
    assert not (tmp_path / RUN_LOCK_FILENAME).exists()


def test_pipeline_takes_over_stale_lock(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    orchestrator = DownloadOrchestrator(config)
    _patched_pipeline(orchestrator, monkeypatch)

    stale_lock = tmp_path / RUN_LOCK_FILENAME
    old = (
        datetime.now(timezone.utc) - timedelta(seconds=RUN_LOCK_STALE_TTL_SECONDS + 60)
    ).isoformat()
    _write_lock(stale_lock, host="crashed-host", acquired_at=old)

    orchestrator.run_download_pipeline()

    assert orchestrator.pipeline_lock_skipped is False
    assert not stale_lock.exists()


def test_pipeline_runs_unlocked_without_download_dir(tmp_path, monkeypatch):
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    orchestrator = DownloadOrchestrator({"DOWNLOAD_DIR": ""})
    calls = _patched_pipeline(orchestrator, monkeypatch)

    orchestrator.run_download_pipeline()

    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls


# ------------------------------------------------------------------
# CLI summary gating
# ------------------------------------------------------------------


def test_cli_summary_reports_skipped_run_instead_of_up_to_date(tmp_path):
    from unittest.mock import Mock, patch

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
    logged = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "another fetchtastic download run is active" in logged
    assert "up to date" not in logged
