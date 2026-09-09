from pathlib import Path

import pytest

from fetchtastic.constants import FIRMWARE_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Release


@pytest.fixture
def downloader(tmp_path: Path) -> FirmwareReleaseDownloader:
    cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
    return FirmwareReleaseDownloader(
        {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "FILTER_REVOKED_RELEASES": False,
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": False,
        },
        cache_manager,
    )


def _firmware_dir(downloader: FirmwareReleaseDownloader) -> Path:
    path = Path(downloader.download_dir) / FIRMWARE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_tracked_stale_release_is_pruned_when_keep_set_is_disjoint(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    stale = firmware_dir / "v1.0.0"
    stale.mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {"entries": {"v1.0.0": {"tag_name": "v1.0.0"}}},
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert not stale.exists()


def test_untracked_release_is_preserved_when_keep_set_is_disjoint(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    local_only = firmware_dir / "v1.0.0"
    local_only.mkdir()

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert local_only.is_dir()


@pytest.mark.parametrize(
    "history_entry",
    ["corrupt", {}, {"tag_name": "v9.9.9"}],
)
def test_malformed_history_cannot_authorize_reconciliation_deletion(
    downloader: FirmwareReleaseDownloader, history_entry: object
) -> None:
    firmware_dir = _firmware_dir(downloader)
    local_only = firmware_dir / "v1.0.0"
    local_only.mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {"entries": {"v1.0.0": history_entry}},
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert local_only.is_dir()


def test_retained_release_layout_is_preserved_until_canonical_dir_exists(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    channel_dir = firmware_dir / "v3.0.0-alpha"
    channel_dir.mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {"entries": {"v3.0.0": {"tag_name": "v3.0.0"}}},
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert channel_dir.is_dir()


def test_tracked_alternate_layout_is_pruned_after_canonical_dir_exists(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    canonical = firmware_dir / "v3.0.0"
    alternate = firmware_dir / "v3.0.0-alpha"
    canonical.mkdir()
    alternate.mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {"entries": {"v3.0.0": {"tag_name": "v3.0.0"}}},
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert canonical.is_dir()
    assert not alternate.exists()


def test_non_version_history_tag_cannot_authorize_reconciliation_deletion(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    local_only = firmware_dir / "notes"
    local_only.mkdir()
    (firmware_dir / "v1.0.0").mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {"entries": {"notes": {"tag_name": "notes"}}},
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert local_only.is_dir()


def test_hash_suffixed_history_tag_still_authorizes_stale_release_cleanup(
    downloader: FirmwareReleaseDownloader,
) -> None:
    firmware_dir = _firmware_dir(downloader)
    stale = firmware_dir / "v2.7.25.104df5f"
    stale.mkdir()
    assert downloader.cache_manager.atomic_write_json(
        downloader.release_history_path,
        {
            "entries": {
                "v2.7.25.104df5f": {"tag_name": "v2.7.25.104df5f"}
            }
        },
    )

    downloader.cleanup_old_versions(
        1,
        cached_releases=[Release(tag_name="v3.0.0")],
    )

    assert not stale.exists()
