"""Advisory cross-process lock for download runs.

Overlapping fetchtastic invocations (for example a cron run racing a manual
one) can interleave destructive nightly steps: run A's tracking write can
overwrite the newer build run B just recorded, and A's retention cleanup can
delete B's freshly downloaded build directory. An advisory run lock held for
the duration of ``run_download_pipeline`` serializes those runs.

The lock can never get stuck:

- The holder's PID and host are stored in the lock file. On POSIX, a lock
  whose holder process is dead is taken over immediately (crash recovery),
  so a killed fetchtastic blocks the next run for at most one attempt.
- On Windows (no safe ``os.kill(pid, 0)`` probe) and for holders on other
  hosts, staleness is decided by age: a lock older than the TTL is taken
  over even though its holder cannot be probed.
- Takeover is atomic: the stale file is renamed aside before deletion, so a
  legitimate holder that re-checks never has its live lock unlinked, and two
  processes racing a takeover cannot both win.
- Release only unlinks the lock when its token still matches the one this
  instance wrote, so a run that was superseded after a TTL takeover cannot
  delete its successor's lock.

Known edge: POSIX PID reuse can make a dead holder's PID look alive. The
lock is then respected until that unrelated process exits; this is the
price of never stealing a provably-live holder's lock.
"""

import json
import os
import platform
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fetchtastic.constants import (
    RUN_LOCK_STALE_TTL_SECONDS,
    RUN_LOCK_TAKEOVER_ATTEMPTS,
)
from fetchtastic.log_utils import logger


def _pid_is_alive(pid: Any) -> bool:
    """Return True when ``pid`` is a running process (POSIX only).

    On Windows there is no safe liveness probe (``os.kill`` terminates the
    target for non-console signals), so the caller must fall back to the
    TTL. PermissionError means the process exists but is owned by another
    user, which still counts as alive.
    """
    if os.name != "posix":
        return False
    if not isinstance(pid, int) or pid <= 0:
        return False
    import signal

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Undecidable (for example a badPermissions variant): assume alive
        # so we never steal a live lock based on a probe failure.
        return True
    return True


class RunLock:
    """Best-effort advisory lock file with crash-safe stale takeover."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._token: Optional[str] = None

    @property
    def locked(self) -> bool:
        """True while this instance holds the lock."""
        return self._token is not None

    def acquire(self) -> bool:
        """Acquire the lock, taking over a stale lock when safe to do so.

        Returns False when a live holder (or a lock younger than the TTL
        whose holder cannot be probed) currently owns it. Never raises.
        """
        parent = os.path.dirname(self.lock_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                logger.debug("Run lock unavailable (cannot create %s): %s", parent, exc)
                return False

        for _ in range(RUN_LOCK_TAKEOVER_ATTEMPTS):
            token = uuid.uuid4().hex
            fd = None
            try:
                fd = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
            except FileExistsError:
                if self._takeover_if_stale():
                    continue
                return False
            except OSError as exc:
                logger.debug("Run lock unavailable (%s): %s", self.lock_path, exc)
                return False

            payload = {
                "token": token,
                "pid": os.getpid(),
                "host": platform.node(),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            except OSError as exc:
                # The lock file exists and carries a fresh mtime even without
                # content; the TTL path recovers if this process dies now.
                logger.debug(
                    "Run lock payload write failed for %s: %s", self.lock_path, exc
                )
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._token = token
            return True

        logger.debug(
            "Run lock takeover raced too many times for %s; treating as held",
            self.lock_path,
        )
        return False

    def release(self) -> None:
        """Release the lock if this instance still owns it. Never raises."""
        if self._token is None:
            return
        token = self._token
        self._token = None
        info, _mtime = self._read_lock()
        if not isinstance(info, dict) or info.get("token") != token:
            # A successor already took over; leave its lock alone.
            return
        discard = f"{self.lock_path}.release.{uuid.uuid4().hex}"
        try:
            os.replace(self.lock_path, discard)
        except OSError as exc:
            logger.debug("Run lock release failed for %s: %s", self.lock_path, exc)
            return
        try:
            os.unlink(discard)
        except OSError:
            pass

    def describe_holder(self) -> str:
        """Human-readable description of the current holder for log messages."""
        info, _mtime = self._read_lock()
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
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _read_lock(self) -> tuple[Optional[dict[str, Any]], Optional[float]]:
        """Return (payload-or-None, mtime-epoch-or-None) for the lock file."""
        try:
            with open(self.lock_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = None
        try:
            mtime = os.path.getmtime(self.lock_path)
        except OSError:
            mtime = None
        return data, mtime

    def _is_stale(self, info: Optional[dict[str, Any]], mtime: Optional[float]) -> bool:
        """Decide whether the existing lock may be taken over.

        Same-host holders are authoritative on POSIX: dead PID means stale
        (immediate crash recovery), live PID means held regardless of age.
        Holders that cannot be probed (Windows, other hosts, malformed
        payloads) are stale only once older than the TTL.
        """
        if mtime is None:
            # The file vanished mid-read; treat as stale so the caller
            # retries creation.
            return True

        age_seconds: Optional[float] = None
        acquired_at = info.get("acquired_at") if info else None
        if isinstance(acquired_at, str) and acquired_at:
            try:
                acquired = datetime.fromisoformat(acquired_at)
                if acquired.tzinfo is None:
                    acquired = acquired.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - acquired).total_seconds()
            except ValueError:
                age_seconds = None
        if age_seconds is None:
            age_seconds = max(0.0, time.time() - mtime)

        pid = info.get("pid") if info else None
        host = info.get("host") if info else None
        same_host = isinstance(host, str) and host == platform.node()

        if os.name == "posix" and same_host and isinstance(pid, int) and pid > 0:
            return not _pid_is_alive(pid)

        return age_seconds > RUN_LOCK_STALE_TTL_SECONDS

    def _takeover_if_stale(self) -> bool:
        """Atomically remove a stale lock. True when the caller should retry."""
        if not os.path.exists(self.lock_path):
            return True
        info, mtime = self._read_lock()
        if not self._is_stale(info, mtime):
            holder = self.describe_holder()
            logger.debug(
                "Run lock held by %s (%s); skipping concurrent run",
                holder,
                self.lock_path,
            )
            return False
        discard = f"{self.lock_path}.stale.{uuid.uuid4().hex}"
        try:
            # Rename-then-unlink: creation of the canonical path stays the
            # only way to claim the lock, so racing takers cannot both win
            # and a live holder's replacement lock is never unlinked.
            os.replace(self.lock_path, discard)
        except FileNotFoundError:
            return True
        except OSError as exc:
            logger.debug("Run lock takeover failed for %s: %s", self.lock_path, exc)
            return False
        logger.info(
            "Removed stale run lock (age beyond TTL or dead holder) at %s",
            self.lock_path,
        )
        try:
            os.unlink(discard)
        except OSError:
            pass
        return True
