import time
from pathlib import Path

import pytest

from fetchtastic.download.interfaces import DownloadResult
from fetchtastic.download.orchestrator import DownloadOrchestrator


class _NoSleep:
    def __call__(self, *_args, **_kwargs):
        """
        A callable that ignores all positional and keyword arguments and always returns None.

        Returns:
            None: Always returns None.
        """
        return None


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """
    Replace time.sleep with a no-op callable for tests.

    This pytest fixture monkeypatches time.sleep to a callable that ignores arguments and returns None so sleep calls return immediately and tests avoid delays.
    """
    monkeypatch.setattr(time, "sleep", _NoSleep())


def test_retry_uses_real_download(monkeypatch, tmp_path):
    """Retry should attempt a real download using stored metadata."""
    config = {"MAX_RETRIES": 2, "RETRY_DELAY_SECONDS": 0, "RETRY_BACKOFF_FACTOR": 1}
    orch = DownloadOrchestrator(config)

    # Prepare file target
    target = tmp_path / "firmware" / "v1.0.0" / "fw.bin"
    target.parent.mkdir(parents=True)
    url = "https://example.invalid/fw.bin"

    # Patch firmware downloader to simulate a successful retry
    download_called = {}

    def fake_download(u, p):
        """
        Simulate a successful download by recording the requested URL and writing test bytes to the given target path.

        Parameters:
            u (str): URL that would be downloaded; stored into the shared `download_called["url"]`.
            p (str | pathlib.Path): Filesystem path where the downloaded bytes are written.

        Returns:
            bool: `True` if the simulated download succeeded, `False` otherwise.
        """
        download_called["url"] = u
        Path(p).write_bytes(b"data")
        return True

    monkeypatch.setattr(orch.firmware_downloader, "download", fake_download)
    monkeypatch.setattr(orch.firmware_downloader, "verify", lambda *_: True)

    failed = DownloadResult(
        success=False,
        release_tag="v1.0.0",
        file_path=target,
        download_url=url,
        file_size=10,
        file_type="firmware",
        is_retryable=True,
        error_type="network_error",
    )
    orch.failed_downloads = [failed]

    orch._retry_failed_downloads()

    # Should have moved to download_results, not remain failed
    assert any(r.success for r in orch.download_results)
    assert not orch.failed_downloads
    assert download_called["url"] == url


def test_orchestrator_refreshes_commits_before_processing(monkeypatch):
    config = {"MAX_RETRIES": 0}
    orch = DownloadOrchestrator(config)

    calls = []

    def fake_refresh():
        """
        Record a repository refresh and inject a sample recent commit into the orchestrator.

        Appends "refresh" to the surrounding `calls` list and sets `orch._recent_commits` to a single commit dictionary with `sha` set to "abc1234".
        """
        calls.append("refresh")
        orch._recent_commits = [{"sha": "abc1234"}]

    monkeypatch.setattr(orch, "_refresh_commit_history_cache", fake_refresh)
    monkeypatch.setattr(orch, "_process_android_downloads", lambda: calls.append("apk"))
    monkeypatch.setattr(
        orch, "_process_firmware_downloads", lambda: calls.append("firmware")
    )
    monkeypatch.setattr(orch, "_enhance_download_results_with_metadata", lambda: None)
    monkeypatch.setattr(orch, "_retry_failed_downloads", lambda: None)
    monkeypatch.setattr(orch, "_log_download_summary", lambda *_: None)

    orch.run_download_pipeline()

    assert "firmware" in calls
    assert "apk" in calls
    # Commit refresh is now lazy (only when prerelease filtering needs it).
