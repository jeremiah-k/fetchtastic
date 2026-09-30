"""Cross-process advisory lock for automatic download runs.

The lock file is a persistent rendezvous file in the user's private state
directory, scoped by a hash of the configured ``DOWNLOAD_DIR``. Ownership
is provided by the operating system for the lifetime of an open file handle,
not by inspecting and replacing a pathname. This avoids stale-file takeover,
PID-reuse, heartbeat, and ownership-check/remove races: when a process exits,
the OS releases its lock even if the rendezvous file remains.

The rendezvous file must live on a filesystem that supports the platform
lock primitive. Download directories frequently do not: on Termux the
configured directory is usually Android shared storage (a FUSE mount
without ``flock`` support), so the file is kept in the app-private state
directory instead, where locking works on every supported platform.

POSIX uses ``fcntl.flock`` and Windows uses ``msvcrt.locking``. The payload is
only diagnostic metadata for log messages; it is never used to decide
ownership. If locking itself is unavailable, callers can distinguish that from
real contention and preserve Fetchtastic's best-effort fallback behavior.
"""

import errno
import hashlib
import json
import os
import platform
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, BinaryIO, Optional

import platformdirs

from fetchtastic.log_utils import logger


def _termux_private_home() -> Optional[str]:
    """Return the Termux app-private home directory, or None off Termux.

    Termux marks its prefix with ``com.termux`` in ``$PREFIX``. ``$HOME`` is
    read directly (``expanduser`` would fall back to the passwd entry when
    unset, which is not the Termux home); when unset, the home is derived
    from ``$PREFIX`` (``<prefix>/../home``).
    """
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" not in prefix:
        return None
    home = os.environ.get("HOME", "")
    if home.strip():
        return home
    return os.path.join(os.path.dirname(prefix), "home")


def default_run_lock_dir() -> str:
    """Return the directory holding run-lock rendezvous files.

    On Termux this resolves under the app-private home directory (full
    path on ext4, where ``flock`` works); shared storage is never used.
    Elsewhere the user state directory keeps the lock file out of the
    download tree.
    """
    termux_home = _termux_private_home()
    if termux_home is not None:
        return os.path.join(termux_home, ".local", "state", "fetchtastic", "run-locks")
    return os.path.join(platformdirs.user_state_dir("fetchtastic"), "run-locks")


def get_run_lock_path(download_dir: str) -> str:
    """Return the rendezvous lock path scoped to ``download_dir``.

    The scope hash is taken over the canonical path (``realpath`` resolves
    symlink aliases so one physical download directory maps to one lock,
    ``normcase`` normalizes Windows case), while distinct download
    directories still lock independently in the shared state directory.
    """
    canonical = os.path.normcase(os.path.realpath(os.path.expanduser(download_dir)))
    scope = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(default_run_lock_dir(), f"fetchtastic-run-{scope}.lock")


class RunLockAcquireResult(Enum):
    """Outcome of attempting to acquire the download-run lock."""

    ACQUIRED = "acquired"
    CONTENDED = "contended"
    UNAVAILABLE = "unavailable"


def _prepare_lock_file(handle: BinaryIO) -> None:
    """Prepare an opened rendezvous file for the platform lock primitive."""
    if os.name != "nt":
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def _try_lock_file(handle: BinaryIO) -> bool:
    """Try to take an exclusive non-blocking OS lock on ``handle``."""
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    raise OSError(f"unsupported OS lock backend: {os.name}")


def _unlock_file(handle: BinaryIO) -> None:
    """Release the OS lock held on ``handle``."""
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    raise OSError(f"unsupported OS lock backend: {os.name}")


class RunLock:
    """Best-effort process-lifetime advisory lock for one download directory."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._token: Optional[str] = None
        self._handle: Optional[BinaryIO] = None

    @property
    def locked(self) -> bool:
        """Return whether this instance currently owns the OS lock."""
        return self._handle is not None

    def acquire(self) -> RunLockAcquireResult:
        """Try to acquire the lock without waiting.

        ``CONTENDED`` means another process currently owns the OS lock.
        ``UNAVAILABLE`` means the lock could not be created, opened, prepared,
        or written; callers may choose to proceed unlocked. The method never
        raises expected filesystem or locking errors.
        """
        if self.locked:
            return RunLockAcquireResult.ACQUIRED

        parent = os.path.dirname(self.lock_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                logger.debug("Run lock unavailable (cannot create %s): %s", parent, exc)
                return RunLockAcquireResult.UNAVAILABLE

        try:
            handle = open(self.lock_path, "a+b")
        except OSError as exc:
            logger.debug(
                "Run lock unavailable (cannot open %s): %s", self.lock_path, exc
            )
            return RunLockAcquireResult.UNAVAILABLE

        try:
            _prepare_lock_file(handle)
            if not _try_lock_file(handle):
                handle.close()
                return RunLockAcquireResult.CONTENDED
        except OSError as exc:
            logger.debug("Run lock unavailable (%s): %s", self.lock_path, exc)
            handle.close()
            return RunLockAcquireResult.UNAVAILABLE

        token = uuid.uuid4().hex
        payload = {
            "token": token,
            "pid": os.getpid(),
            "host": platform.node(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._write_payload(handle, payload)
        except OSError as exc:
            logger.debug(
                "Run lock payload write failed for %s: %s", self.lock_path, exc
            )
            self._clear_payload(handle)
            try:
                _unlock_file(handle)
            except OSError:
                pass
            handle.close()
            return RunLockAcquireResult.UNAVAILABLE

        self._token = token
        self._handle = handle
        return RunLockAcquireResult.ACQUIRED

    def release(self) -> None:
        """Release this instance's OS lock. Never removes the rendezvous path."""
        handle = self._handle
        if handle is None:
            self._token = None
            return
        self._handle = None
        self._token = None
        self._clear_payload(handle)
        try:
            _unlock_file(handle)
        except OSError as exc:
            logger.debug("Run lock unlock failed for %s: %s", self.lock_path, exc)
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def describe_holder(self) -> str:
        """Return diagnostic metadata written by the current lock holder."""
        try:
            with open(self.lock_path, "r", encoding="utf-8") as handle:
                info = json.load(handle)
        except (OSError, ValueError):
            return "unknown holder"
        if not isinstance(info, dict):
            return "unknown holder"
        parts = []
        pid = info.get("pid")
        if isinstance(pid, int) and pid > 0:
            parts.append(f"PID {pid}")
        host = info.get("host")
        if isinstance(host, str) and host:
            parts.append(f"host {host}")
        acquired = info.get("acquired_at")
        if isinstance(acquired, str) and acquired:
            parts.append(f"started {acquired}")
        return ", ".join(parts) or "unknown holder"

    def __enter__(self) -> "RunLock":
        """Acquire the lock for context-manager use and return this instance."""
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        """Release the lock when leaving a context-manager scope."""
        self.release()

    def _write_payload(self, handle: BinaryIO, payload: dict[str, Any]) -> None:
        """Replace diagnostic payload bytes while the OS lock is held."""
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        handle.seek(0)
        handle.truncate(0)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    def _clear_payload(self, handle: BinaryIO) -> None:
        """Best-effort clear diagnostic metadata before unlocking the file."""
        try:
            handle.seek(0)
            handle.truncate(0)
            if os.name == "nt":
                # msvcrt.locking owns a one-byte range; keep that byte present
                # until _unlock_file() releases the range.
                handle.write(b"\0")
            handle.flush()
        except OSError:
            pass
