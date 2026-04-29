import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from fetchtastic.download.prerelease_history import PrereleaseHistoryManager


class _SimpleCacheManager:
    def read_json(self, file_path: str):
        """
        Read and parse JSON content from a UTF-8 encoded file.

        Parameters:
            file_path (str): Path to the JSON file to read.

        Returns:
            The Python object resulting from parsing the JSON content (commonly a dict or list).
        """
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)

    def atomic_write_json(self, file_path: str, data):
        """
        Write JSON-serializable data to a file using UTF-8 encoding, overwriting any existing content.

        Parameters:
            file_path (str): Destination file path.
            data: JSON-serializable object to write to the file.

        Returns:
            `True` if the data was written successfully.
        """
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True


@pytest.mark.unit
@pytest.mark.core_downloads
def test_manage_prerelease_tracking_files_cleanup_pattern_is_scoped(tmp_path):
    """
    Ensure fnmatch-based cleanup only deletes files for the targeted prerelease_version.
    """
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()

    # Two tracking files for the same prerelease_version (should be deleted).
    to_delete_1 = tracking_dir / "prerelease_v1.2.3.abc123_v1.2.3.json"
    to_delete_2 = tracking_dir / "prerelease_v1.2.3.abc123_v1.2.3-extra.json"

    # Similar-looking files that must remain (not superseded by current_prereleases).
    to_keep_1 = tracking_dir / "prerelease_v1.2.4.abc1234_v1.2.4.json"
    to_keep_2 = tracking_dir / "prerelease_v1.2.4.abc12_v1.2.4.json"

    existing_entry = {
        "prerelease_version": "v1.2.3.abc123",
        "base_version": "1.2.3",
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    for path in (to_delete_1, to_delete_2):
        path.write_text(json.dumps(existing_entry), encoding="utf-8")

    keep_entry = {
        "prerelease_version": "v1.2.4.abc1234",
        "base_version": "1.2.4",
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    for path in (to_keep_1, to_keep_2):
        path.write_text(json.dumps(keep_entry), encoding="utf-8")

    # Current prerelease has a newer base version, triggering cleanup of existing_entry.
    current_prereleases = [
        {
            "prerelease_version": "v1.2.4.def456",
            "base_version": "1.2.4",
            "expiry_timestamp": (
                datetime.now(timezone.utc) + timedelta(hours=24)
            ).isoformat(),
        }
    ]

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=current_prereleases,
        cache_manager=_SimpleCacheManager(),
    )

    assert not to_delete_1.exists()
    assert not to_delete_2.exists()
    assert to_keep_1.exists()
    assert to_keep_2.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_expired_tracking_metadata_cleanup_logs_debug_only(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking_file = tracking_dir / "prerelease_v2.7.14-closed.10_v2.7.14.json"
    tracking_file.write_text(
        json.dumps(
            {
                "prerelease_version": "v2.7.14-closed.10",
                "base_version": "v2.7.14",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    app_prerelease_dir = tmp_path / "app" / "prerelease" / "v2.7.14-closed.10"
    app_prerelease_dir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    with (
        patch("fetchtastic.download.prerelease_history.logger.debug") as debug_log,
        patch("fetchtastic.download.prerelease_history.logger.info") as info_log,
    ):
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[],
            cache_manager=_SimpleCacheManager(),
        )

    assert not tracking_file.exists()
    assert app_prerelease_dir.exists()
    assert any(
        call.args[:2] == ("Removed %s prerelease tracking metadata file: %s", "expired")
        for call in debug_log.call_args_list
    )
    assert not info_log.called
    assert not any(
        "Cleaned up prerelease" in call.args[0] and "metadata" not in call.args[0]
        for call in debug_log.call_args_list
    )


@pytest.mark.unit
@pytest.mark.core_downloads
def test_superseded_tracking_metadata_cleanup_logs_debug(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking_file = tracking_dir / "prerelease_v2.7.14-closed.10_v2.7.14.json"
    tracking_file.write_text(
        json.dumps(
            {
                "prerelease_version": "v2.7.14-closed.10",
                "base_version": "v2.7.14",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with patch("fetchtastic.download.prerelease_history.logger.debug") as debug_log:
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[
                {
                    "prerelease_version": "v2.7.15-open.1",
                    "base_version": "v2.7.15",
                    "expiry_timestamp": (
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                }
            ],
            cache_manager=_SimpleCacheManager(),
        )

    assert not tracking_file.exists()
    assert any(
        call.args[:2]
        == ("Removed %s prerelease tracking metadata file: %s", "superseded")
        for call in debug_log.call_args_list
    )


# --- Regression: current-but-expired prerelease tracking refreshed silently ---


@pytest.mark.unit
@pytest.mark.core_downloads
def test_current_expired_metadata_not_removed_but_refreshed(tmp_path):
    """
    Expired tracking metadata for a prerelease that is still current
    must not be removed — atomic_write_json refreshes it silently.
    """
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking_file = tracking_dir / "prerelease_v2.7.14-closed.10_v2.7.14.json"
    tracking_file.write_text(
        json.dumps(
            {
                "prerelease_version": "v2.7.14-closed.10",
                "base_version": "v2.7.14",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with (patch("fetchtastic.download.prerelease_history.logger.debug") as debug_log,):
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[
                {
                    "prerelease_version": "v2.7.14-closed.10",
                    "base_version": "v2.7.14",
                    "expiry_timestamp": (
                        datetime.now(timezone.utc) + timedelta(hours=24)
                    ).isoformat(),
                }
            ],
            cache_manager=_SimpleCacheManager(),
        )

    # Tracking file still exists — it was silently refreshed by atomic_write_json
    assert tracking_file.exists()
    # Fresh data written
    with open(tracking_file, encoding="utf-8") as f:
        refreshed = json.load(f)
    assert refreshed["base_version"] == "v2.7.14"
    assert datetime.fromisoformat(refreshed["expiry_timestamp"]) > datetime.now(
        timezone.utc
    )

    # No removal log message for expired current prerelease
    removal_calls = [
        call
        for call in debug_log.call_args_list
        if call.args[:2]
        == ("Removed %s prerelease tracking metadata file: %s", "expired")
    ]
    assert not removal_calls


@pytest.mark.unit
@pytest.mark.core_downloads
def test_expired_noncurrent_metadata_still_removed(tmp_path):
    """
    Expired tracking metadata for a prerelease NOT in current_prereleases
    is still removed with DEBUG metadata wording.
    """
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking_file = tracking_dir / "prerelease_v2.7.14-closed.9_v2.7.14.json"
    tracking_file.write_text(
        json.dumps(
            {
                "prerelease_version": "v2.7.14-closed.9",
                "base_version": "v2.7.14",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with patch("fetchtastic.download.prerelease_history.logger.debug") as debug_log:
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[
                {
                    "prerelease_version": "v2.7.14-closed.10",
                    "base_version": "v2.7.14",
                }
            ],
            cache_manager=_SimpleCacheManager(),
        )

    assert not tracking_file.exists()
    assert any(
        call.args[:2] == ("Removed %s prerelease tracking metadata file: %s", "expired")
        for call in debug_log.call_args_list
    )
