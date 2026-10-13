"""Tests for the cross-process download run lock."""

import errno
import json
import os
import sys
from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.run_lock import RunLock, RunLockAcquireResult

# Rendezvous filename used by the RunLock unit tests, which exercise the lock
# primitive at explicit paths; production resolves names via get_run_lock_path.
TEST_LOCK_FILENAME = ".fetchtastic-run.lock"

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def test_acquire_holds_os_lock_and_release_makes_it_available(tmp_path):
    lock_path = tmp_path / TEST_LOCK_FILENAME
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
    lock_path = tmp_path / TEST_LOCK_FILENAME
    with RunLock(str(lock_path)) as lock:
        assert lock.locked is True
        assert lock_path.exists()
    replacement = RunLock(str(lock_path))
    assert replacement.acquire() is RunLockAcquireResult.ACQUIRED
    replacement.release()


def test_holder_description_comes_from_diagnostic_payload(tmp_path):
    lock_path = tmp_path / TEST_LOCK_FILENAME
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
    lock_path = tmp_path / TEST_LOCK_FILENAME
    first = RunLock(str(lock_path))
    assert first.acquire() is RunLockAcquireResult.ACQUIRED
    os.utime(lock_path, (1, 1))

    second = RunLock(str(lock_path))
    assert second.acquire() is RunLockAcquireResult.CONTENDED
    first.release()


def test_idle_rendezvous_file_never_blocks_next_run(tmp_path):
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock_path.write_text("stale diagnostic bytes", encoding="utf-8")
    os.utime(lock_path, (1, 1))

    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    lock.release()


def test_payload_write_failure_returns_unavailable_without_holding_lock(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / TEST_LOCK_FILENAME
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
    lock = RunLock(str(parent_as_file / TEST_LOCK_FILENAME))

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
    from fetchtastic.download import run_lock as run_lock_module
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    lock_dir = tmp_path / "state" / "run-locks"
    monkeypatch.setattr(run_lock_module, "default_run_lock_dir", lambda: str(lock_dir))

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    holder = RunLock(run_lock_module.get_run_lock_path(str(tmp_path)))
    assert holder.acquire() is RunLockAcquireResult.ACQUIRED

    orchestrator = DownloadOrchestrator(config)
    calls = _patched_pipeline(orchestrator, monkeypatch)
    results, failures = orchestrator.run_download_pipeline()

    assert results == [] and failures == []
    assert calls == []
    assert orchestrator.pipeline_lock_skipped is True
    holder.release()


def test_pipeline_runs_and_releases_kernel_lock(tmp_path, monkeypatch):
    from fetchtastic.download import run_lock as run_lock_module
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    lock_dir = tmp_path / "state" / "run-locks"
    monkeypatch.setattr(run_lock_module, "default_run_lock_dir", lambda: str(lock_dir))

    config = {"DOWNLOAD_DIR": str(tmp_path)}
    orchestrator = DownloadOrchestrator(config)
    calls = _patched_pipeline(orchestrator, monkeypatch)

    results, failures = orchestrator.run_download_pipeline()

    assert results == [] and failures == []
    assert orchestrator.pipeline_lock_skipped is False
    assert "firmware" in calls and "app" in calls
    # The rendezvous file lives in the state directory, not the download dir,
    # and is idle and immediately re-acquirable after the run.
    assert not list(tmp_path.glob("*.lock"))
    probe = RunLock(run_lock_module.get_run_lock_path(str(tmp_path)))
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
    integration.orchestrator.available_new_client_app_versions = []
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
            downloaded_client_apps=[],
            failed_downloads=[],
            latest_firmware_version="",
            latest_client_app_version="",
        )

    mock_up_to_date.assert_not_called()
    logged = " ".join(str(call) for call in mock_log.info.call_args_list)
    assert "another fetchtastic download run is active" in logged
    assert "up to date" not in logged


def test_run_lock_path_scopes_by_download_dir(tmp_path, monkeypatch):
    """Distinct download directories lock independently; one directory is stable."""
    from fetchtastic.download import run_lock as run_lock_module

    lock_dir = tmp_path / "state" / "run-locks"
    monkeypatch.setattr(run_lock_module, "default_run_lock_dir", lambda: str(lock_dir))
    dir_a = tmp_path / "downloads-a"
    dir_b = tmp_path / "downloads-b"

    path_a1 = run_lock_module.get_run_lock_path(str(dir_a))
    path_a2 = run_lock_module.get_run_lock_path(str(dir_a))
    path_b = run_lock_module.get_run_lock_path(str(dir_b))

    assert path_a1 == path_a2
    assert path_a1 != path_b
    # The rendezvous file never lives inside the download tree: shared or
    # synced download storage may not support the OS lock primitive.
    assert str(tmp_path / "downloads-a") not in path_a1
    assert path_a1.startswith(str(lock_dir))


def test_run_lock_dir_on_termux_uses_private_home(monkeypatch):
    """Termux resolves the lock dir from $HOME/$PREFIX, never shared storage."""
    from fetchtastic.download import run_lock as run_lock_module

    home = "/data/data/com.termux/files/home"
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setenv("HOME", home)

    lock_dir = run_lock_module.default_run_lock_dir()

    assert lock_dir == os.path.join(home, ".local", "state", "fetchtastic", "run-locks")

    # With $HOME unset, the private home is derived from $PREFIX's parent.
    monkeypatch.delenv("HOME")
    expanded = run_lock_module.default_run_lock_dir()
    assert expanded == os.path.join(home, ".local", "state", "fetchtastic", "run-locks")


def test_run_lock_dir_off_termux_uses_user_state_dir(monkeypatch, tmp_path):
    """Off Termux the lock dir follows the user state directory."""
    from fetchtastic.download import run_lock as run_lock_module

    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(
        run_lock_module.platformdirs,
        "user_state_dir",
        lambda _name: str(tmp_path / "state"),
    )

    lock_dir = run_lock_module.default_run_lock_dir()

    assert lock_dir == os.path.join(str(tmp_path / "state"), "run-locks")


@pytest.mark.skipif(
    os.name == "nt", reason="creating directory symlinks on Windows needs privileges"
)
def test_run_lock_path_resolves_symlink_aliases(tmp_path, monkeypatch):
    """One physical download directory maps to one lock through any alias."""
    from fetchtastic.download import run_lock as run_lock_module

    lock_dir = tmp_path / "state" / "run-locks"
    monkeypatch.setattr(run_lock_module, "default_run_lock_dir", lambda: str(lock_dir))
    real_dir = tmp_path / "downloads"
    real_dir.mkdir()
    alias = tmp_path / "downloads-alias"
    alias.symlink_to(real_dir, target_is_directory=True)

    assert run_lock_module.get_run_lock_path(str(alias)) == (
        run_lock_module.get_run_lock_path(str(real_dir))
    )

    holder = RunLock(run_lock_module.get_run_lock_path(str(real_dir)))
    assert holder.acquire() is RunLockAcquireResult.ACQUIRED
    contender = RunLock(run_lock_module.get_run_lock_path(str(alias)))
    assert contender.acquire() is RunLockAcquireResult.CONTENDED
    holder.release()


class _FakeMsvcrt:
    """Minimal msvcrt stand-in so the Windows lock backend runs on any OS."""

    LK_NBLCK = 0
    LK_UNLCK = 2

    def __init__(self, error=None):
        self.calls = []
        self._error = error
        self.held = False

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))
        if self._error is not None:
            raise self._error
        if mode == self.LK_NBLCK:
            if self.held:
                raise OSError(errno.EACCES, "Permission denied")
            self.held = True
        elif mode == self.LK_UNLCK:
            self.held = False


def test_prepare_lock_file_seeds_empty_file_on_windows(monkeypatch, tmp_path):
    """The Windows byte-range lock needs a NUL byte seeded into empty files."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock_path.write_bytes(b"")
    monkeypatch.setattr(os, "name", "nt")

    with open(lock_path, "a+b") as handle:
        from fetchtastic.download.run_lock import _prepare_lock_file

        _prepare_lock_file(handle)
        assert handle.tell() == 0
    assert lock_path.read_bytes() == b"\0"


def test_prepare_lock_file_keeps_existing_payload_on_windows(monkeypatch, tmp_path):
    """A file that already carries bytes is not truncated by preparation."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock_path.write_bytes(b"{}\n")
    monkeypatch.setattr(os, "name", "nt")

    with open(lock_path, "a+b") as handle:
        from fetchtastic.download.run_lock import _prepare_lock_file

        _prepare_lock_file(handle)
        assert handle.tell() == 0
    assert lock_path.read_bytes() == b"{}\n"


def test_try_lock_file_reraises_unexpected_errno(monkeypatch, tmp_path):
    """A flock failure other than EACCES/EAGAIN is not read as contention."""
    import errno
    import fcntl

    from fetchtastic.download.run_lock import _try_lock_file

    lock_path = tmp_path / TEST_LOCK_FILENAME

    def _eperm(fd, flags):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(fcntl, "flock", _eperm)
    with open(lock_path, "a+b") as handle:
        with pytest.raises(OSError) as excinfo:
            _try_lock_file(handle)
    assert excinfo.value.errno == errno.EPERM


def test_windows_lock_backend_full_cycle(monkeypatch, tmp_path):
    """The msvcrt backend prepares, locks, and unlocks through RunLock."""
    fake = _FakeMsvcrt()
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    lock_path = tmp_path / TEST_LOCK_FILENAME

    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    assert fake.calls and fake.calls[0][1] == _FakeMsvcrt.LK_NBLCK
    # The seeded NUL byte carries the diagnostic payload while held.
    assert lock_path.read_bytes().startswith(b'{"')

    contender = RunLock(str(lock_path))
    assert contender.acquire() is RunLockAcquireResult.CONTENDED

    lock.release()
    assert fake.calls[-1][1] == _FakeMsvcrt.LK_UNLCK
    # Clearing keeps the byte alive on nt so the range stays releasable.
    assert lock_path.read_bytes() == b"\0"
    replacement = RunLock(str(lock_path))
    assert replacement.acquire() is RunLockAcquireResult.ACQUIRED
    replacement.release()


def test_windows_lock_contention_returns_false(monkeypatch, tmp_path):
    """A contended msvcrt range (EACCES) is reported, not raised."""
    import errno

    from fetchtastic.download.run_lock import _try_lock_file

    fake = _FakeMsvcrt(error=OSError(errno.EACCES, "Permission denied"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    lock_path = tmp_path / TEST_LOCK_FILENAME

    with open(lock_path, "a+b") as handle:
        assert _try_lock_file(handle) is False


def test_windows_lock_fatal_errno_reraises(monkeypatch, tmp_path):
    """An msvcrt failure other than a contention errno propagates."""
    import errno

    from fetchtastic.download.run_lock import _try_lock_file

    fake = _FakeMsvcrt(error=OSError(errno.EPERM, "Operation not permitted"))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    lock_path = tmp_path / TEST_LOCK_FILENAME

    with open(lock_path, "a+b") as handle:
        with pytest.raises(OSError) as excinfo:
            _try_lock_file(handle)
    assert excinfo.value.errno == errno.EPERM


def test_unsupported_platform_backend_raises(monkeypatch, tmp_path):
    """An unknown os.name has no lock backend; both primitives refuse."""
    from fetchtastic.download.run_lock import _try_lock_file, _unlock_file

    monkeypatch.setattr(os, "name", "sunos")
    lock_path = tmp_path / TEST_LOCK_FILENAME

    with open(lock_path, "a+b") as handle:
        with pytest.raises(OSError, match="unsupported OS lock backend"):
            _try_lock_file(handle)
        with pytest.raises(OSError, match="unsupported OS lock backend"):
            _unlock_file(handle)


def test_acquire_is_reentrant_when_already_locked(tmp_path):
    """Acquiring again on a locked instance returns ACQUIRED without reopening."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    handle = lock._handle

    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    assert lock._handle is handle
    lock.release()


def test_acquire_works_for_bare_filename_in_cwd(tmp_path, monkeypatch):
    """A rendezvous name without a directory component skips parent creation."""
    monkeypatch.chdir(tmp_path)

    lock = RunLock("run.lock")
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    lock.release()


def test_acquire_unavailable_when_lock_path_is_directory(tmp_path):
    """An unopenable rendezvous path degrades to UNAVAILABLE."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock_path.mkdir()

    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.UNAVAILABLE
    assert lock.locked is False


def test_acquire_unavailable_when_locking_fails_unexpectedly(monkeypatch, tmp_path):
    """A fatal lock failure closes the handle and reports UNAVAILABLE."""
    import errno
    import fcntl

    lock_path = tmp_path / TEST_LOCK_FILENAME

    def _eperm(fd, flags):
        raise OSError(errno.EPERM, "Operation not permitted")

    with monkeypatch.context() as m:
        m.setattr(fcntl, "flock", _eperm)
        lock = RunLock(str(lock_path))
        assert lock.acquire() is RunLockAcquireResult.UNAVAILABLE
        assert lock.locked is False

    # The failed handle was closed, so the next attempt starts clean.
    recovery = RunLock(str(lock_path))
    assert recovery.acquire() is RunLockAcquireResult.ACQUIRED
    recovery.release()


def test_acquire_unavailable_when_payload_write_and_unlock_both_fail(
    monkeypatch, tmp_path
):
    """Payload failure degrades to UNAVAILABLE even if unlock also fails."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock = RunLock(str(lock_path))

    def _fail_write(_handle, _payload):
        raise OSError("disk full")

    def _fail_unlock(_handle):
        raise OSError("unlock failed")

    monkeypatch.setattr(lock, "_write_payload", _fail_write)
    monkeypatch.setattr("fetchtastic.download.run_lock._unlock_file", _fail_unlock)
    assert lock.acquire() is RunLockAcquireResult.UNAVAILABLE
    assert lock.locked is False


def test_release_without_handle_is_noop(tmp_path):
    """Releasing an unowned lock is a safe no-op, including repeated calls."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock = RunLock(str(lock_path))
    lock.release()
    lock.release()
    assert lock.locked is False


def test_release_survives_unlock_failure(tmp_path, monkeypatch):
    """An unlock failure is logged and the handle is still closed."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED

    def _fail_unlock(_handle):
        raise OSError("kernel refused unlock")

    monkeypatch.setattr("fetchtastic.download.run_lock._unlock_file", _fail_unlock)
    lock.release()
    assert lock.locked is False


class _CloseRaises:
    """File-object proxy whose close() fails, for exercising close guards."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        raise OSError("close failed")


def test_release_survives_close_failure(tmp_path):
    """A failing handle close does not break release."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    lock = RunLock(str(lock_path))
    assert lock.acquire() is RunLockAcquireResult.ACQUIRED
    lock._handle = _CloseRaises(lock._handle)

    lock.release()
    assert lock.locked is False


def test_clear_payload_swallows_write_errors(tmp_path):
    """Clearing diagnostics on an unwritable handle is best-effort."""

    class _TruncateRaises:
        def seek(self, *_args):
            return 0

        def truncate(self, *_args):
            raise OSError("cannot truncate")

        def flush(self):
            return None

    RunLock._clear_payload(None, _TruncateRaises())


@pytest.mark.parametrize(
    "payload_bytes, expected",
    [
        (None, "unknown holder"),
        (b"not json", "unknown holder"),
        (b"[]", "unknown holder"),
        (b'{"pid": 0}', "unknown holder"),
        (b'{"pid": 123}', "PID 123"),
        (b'{"host": "box"}', "host box"),
        (b'{"acquired_at": "2026-01-01T00:00:00+00:00"}', "started 2026-01-01"),
        (
            b'{"pid": 5, "host": "box", "acquired_at": "2026-01-01T00:00:00+00:00"}',
            "PID 5, host box, started 2026-01-01",
        ),
    ],
)
def test_describe_holder_handles_payload_variants(tmp_path, payload_bytes, expected):
    """Holder diagnostics degrade to 'unknown holder' for unusable payloads."""
    lock_path = tmp_path / TEST_LOCK_FILENAME
    if payload_bytes is not None:
        lock_path.write_bytes(payload_bytes)

    lock = RunLock(str(lock_path))
    assert lock.describe_holder().startswith(expected)
