import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from fetchtastic.download.prerelease_history import PrereleaseHistoryManager


class _SimpleCacheManager:
    def read_json(self, file_path: str):
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)

    def atomic_write_json(self, file_path: str, data):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True


class _BrokenReadCacheManager:
    def read_json(self, file_path: str):
        return None

    def atomic_write_json(self, file_path: str, data):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True


@pytest.mark.unit
@pytest.mark.core_downloads
def test_returns_early_when_tracking_dir_does_not_exist(tmp_path):
    manager = PrereleaseHistoryManager()
    nonexistent = str(tmp_path / "no_such_dir")
    result = manager.manage_prerelease_tracking_files(
        nonexistent,
        current_prereleases=[
            {"prerelease_version": "1.0.0-rc.1", "base_version": "1.0.0"}
        ],
        cache_manager=_SimpleCacheManager(),
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.core_downloads
def test_removes_invalid_tracking_files_when_read_returns_none(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    invalid_file = tracking_subdir / "prerelease_1.0.0-rc.1_1.0.0.json"
    invalid_file.write_text("{}", encoding="utf-8")

    valid_file = tracking_subdir / "prerelease_1.0.0-rc.2_1.0.0.json"
    valid_file.write_text(
        json.dumps(
            {
                "prerelease_version": "1.0.0-rc.2",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) + timedelta(hours=24)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[],
        cache_manager=_BrokenReadCacheManager(),
    )

    assert not invalid_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_removes_invalid_tracking_files_missing_required_keys(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    missing_keys_file = tracking_subdir / "prerelease_bad_data_1.0.0.json"
    missing_keys_file.write_text(
        json.dumps({"prerelease_version": "1.0.0-rc.1"}),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[],
        cache_manager=_SimpleCacheManager(),
    )

    assert not missing_keys_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_handles_oserror_removing_invalid_file(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    bad_file = tracking_subdir / "prerelease_bad_1.0.0.json"
    bad_file.write_text(
        json.dumps({"prerelease_version": "bad"}),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with patch("os.remove", side_effect=OSError("permission denied")):
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[],
            cache_manager=_SimpleCacheManager(),
        )

    assert bad_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_creates_tracking_subdir_if_missing(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) + timedelta(hours=24)
                ).isoformat(),
            }
        ],
        cache_manager=_SimpleCacheManager(),
    )

    tracking_subdir = tracking_dir / "prerelease_tracking"
    assert tracking_subdir.is_dir()
    files = list(tracking_subdir.iterdir())
    assert len(files) == 1


@pytest.mark.unit
@pytest.mark.core_downloads
def test_skips_superseded_current_prerelease_from_write(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
            },
            {
                "prerelease_version": "1.0.1-rc.1",
                "base_version": "1.0.1",
            },
        ],
        cache_manager=_SimpleCacheManager(),
    )

    files = sorted(p.name for p in tracking_subdir.iterdir())
    assert len(files) == 1
    assert "1.0.1" in files[0]


@pytest.mark.unit
@pytest.mark.core_downloads
def test_skips_current_prerelease_without_version_keys(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {"prerelease_version": "", "base_version": "1.0.0"},
            {"prerelease_version": "1.0.0-rc.1", "base_version": ""},
            {"prerelease_version": "", "base_version": ""},
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
            },
        ],
        cache_manager=_SimpleCacheManager(),
    )

    files = list(tracking_subdir.iterdir())
    assert len(files) == 1


@pytest.mark.unit
@pytest.mark.core_downloads
def test_skips_invalid_current_prerelease_data(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {"only_prerelease_version": "1.0.0-rc.1"},
        ],
        cache_manager=_SimpleCacheManager(),
    )

    files = list(tracking_subdir.iterdir())
    assert len(files) == 0


@pytest.mark.unit
@pytest.mark.core_downloads
def test_cleanup_skips_entry_with_empty_prerelease_version(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    existing_file = tracking_subdir / "prerelease___1.0.0.json"
    existing_file.write_text(
        json.dumps(
            {
                "prerelease_version": "",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {
                "prerelease_version": "1.0.1-rc.1",
                "base_version": "1.0.1",
            }
        ],
        cache_manager=_SimpleCacheManager(),
    )

    assert existing_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_handles_oserror_during_cleanup_removal(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    existing_file = tracking_subdir / "prerelease_1.0.0-rc.1_1.0.0.json"
    existing_file.write_text(
        json.dumps(
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with patch("os.remove", side_effect=OSError("locked")):
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[],
            cache_manager=_SimpleCacheManager(),
        )

    assert existing_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_write_updates_existing_tracking_file(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    old_file = tracking_subdir / "prerelease_1.0.0-rc.1_1.0.0.json"
    old_file.write_text(
        json.dumps(
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    new_data = {
        "prerelease_version": "1.0.0-rc.1",
        "base_version": "1.0.0",
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=48)
        ).isoformat(),
    }

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[new_data],
        cache_manager=_SimpleCacheManager(),
    )

    with open(old_file, encoding="utf-8") as f:
        refreshed = json.load(f)
    assert refreshed["expiry_timestamp"] == new_data["expiry_timestamp"]


@pytest.mark.unit
@pytest.mark.core_downloads
def test_create_prerelease_tracking_data_without_commit_hash():
    manager = PrereleaseHistoryManager()
    data = manager.create_prerelease_tracking_data(
        prerelease_version="1.2.3-rc.1",
        base_version="1.2.3",
        expiry_hours=48.0,
    )
    assert data["prerelease_version"] == "1.2.3-rc.1"
    assert data["base_version"] == "1.2.3"
    assert "commit_hash" not in data
    assert "expiry_timestamp" in data
    assert "created_at" in data
    expiry = datetime.fromisoformat(data["expiry_timestamp"])
    assert expiry > datetime.now(timezone.utc) - timedelta(minutes=1)


@pytest.mark.unit
@pytest.mark.core_downloads
def test_create_prerelease_tracking_data_with_commit_hash():
    manager = PrereleaseHistoryManager()
    data = manager.create_prerelease_tracking_data(
        prerelease_version="1.2.3-rc.2",
        base_version="1.2.3",
        expiry_hours=24.0,
        commit_hash="abc123def456",
    )
    assert data["prerelease_version"] == "1.2.3-rc.2"
    assert data["base_version"] == "1.2.3"
    assert data["commit_hash"] == "abc123def456"


@pytest.mark.unit
@pytest.mark.core_downloads
def test_should_cleanup_superseded_prerelease_returns_true():
    manager = PrereleaseHistoryManager()
    current = {"base_version": "1.0.0"}
    new = {"base_version": "1.0.1"}
    assert manager.should_cleanup_superseded_prerelease(current, new) is True


@pytest.mark.unit
@pytest.mark.core_downloads
def test_should_cleanup_superseded_prerelease_returns_false():
    manager = PrereleaseHistoryManager()
    current = {
        "base_version": "1.0.1",
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat(),
    }
    new = {"base_version": "1.0.0"}
    assert manager.should_cleanup_superseded_prerelease(current, new) is False


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_expired():
    manager = PrereleaseHistoryManager()
    existing = {
        "expiry_timestamp": (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(),
        "base_version": "1.0.0",
    }
    assert manager.get_prerelease_tracking_cleanup_reason(existing, []) == "expired"


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_superseded():
    manager = PrereleaseHistoryManager()
    existing = {
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat(),
        "base_version": "1.0.0",
    }
    current = [{"base_version": "1.0.1"}]
    assert (
        manager.get_prerelease_tracking_cleanup_reason(existing, current)
        == "superseded"
    )


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_returns_none_when_valid():
    manager = PrereleaseHistoryManager()
    existing = {
        "expiry_timestamp": (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat(),
        "base_version": "1.0.1",
    }
    current = [{"base_version": "1.0.0"}]
    assert manager.get_prerelease_tracking_cleanup_reason(existing, current) is None


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_handles_naive_expiry_timestamp():
    manager = PrereleaseHistoryManager()
    naive_past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    existing = {
        "expiry_timestamp": naive_past.isoformat(),
        "base_version": "1.0.0",
    }
    assert manager.get_prerelease_tracking_cleanup_reason(existing, []) == "expired"


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_handles_invalid_expiry_timestamp():
    manager = PrereleaseHistoryManager()
    existing = {
        "expiry_timestamp": "not-a-valid-timestamp",
        "base_version": "1.0.0",
    }
    current = [{"base_version": "1.0.1"}]
    assert (
        manager.get_prerelease_tracking_cleanup_reason(existing, current)
        == "superseded"
    )


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_returns_none_no_base_version():
    manager = PrereleaseHistoryManager()
    existing = {}
    assert manager.get_prerelease_tracking_cleanup_reason(existing, []) is None


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_cleanup_reason_skips_new_without_base_version():
    manager = PrereleaseHistoryManager()
    existing = {"base_version": "1.0.0"}
    current = [{"other_key": "value"}, {"base_version": ""}]
    assert manager.get_prerelease_tracking_cleanup_reason(existing, current) is None


@pytest.mark.unit
@pytest.mark.core_downloads
def test_no_tracking_subdir_still_writes_current(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir(parents=True)

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[
            {
                "prerelease_version": "2.0.0-rc.1",
                "base_version": "2.0.0",
            }
        ],
        cache_manager=_SimpleCacheManager(),
    )

    tracking_subdir = tracking_dir / "prerelease_tracking"
    assert tracking_subdir.is_dir()
    files = list(tracking_subdir.iterdir())
    assert len(files) == 1
    assert "2.0.0" in files[0].name


@pytest.mark.unit
@pytest.mark.core_downloads
def test_current_prerelease_set_ignores_non_string_values(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    existing_file = tracking_subdir / "prerelease_1.0.0-rc.1_1.0.0.json"
    existing_file.write_text(
        json.dumps(
            {
                "prerelease_version": "1.0.0-rc.1",
                "base_version": "1.0.0",
                "expiry_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    manager = PrereleaseHistoryManager()
    with patch.object(
        manager.version_manager,
        "validate_version_tracking_data",
        return_value=False,
    ):
        manager.manage_prerelease_tracking_files(
            str(tracking_dir),
            current_prereleases=[
                {"prerelease_version": 123, "base_version": "1.0.1"},
                {"prerelease_version": None, "base_version": "1.0.2"},
            ],
            cache_manager=_SimpleCacheManager(),
        )

    assert not existing_file.exists()


@pytest.mark.unit
@pytest.mark.core_downloads
def test_scan_ignores_non_matching_filenames(tmp_path):
    tracking_dir = tmp_path / "tracking"
    tracking_subdir = tracking_dir / "prerelease_tracking"
    tracking_subdir.mkdir(parents=True)

    other_file = tracking_subdir / "other_data.json"
    other_file.write_text("{}", encoding="utf-8")

    readme = tracking_subdir / "prerelease_README.txt"
    readme.write_text("info", encoding="utf-8")

    manager = PrereleaseHistoryManager()
    manager.manage_prerelease_tracking_files(
        str(tracking_dir),
        current_prereleases=[],
        cache_manager=_SimpleCacheManager(),
    )

    assert other_file.exists()
    assert readme.exists()
