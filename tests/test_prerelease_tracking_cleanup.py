import json
from datetime import datetime, timedelta, timezone

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
