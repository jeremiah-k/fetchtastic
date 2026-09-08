from pathlib import Path

import pytest

from fetchtastic.constants import FIRMWARE_DIR_NAME, FIRMWARE_PRERELEASES_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader


@pytest.fixture
def downloader(tmp_path: Path) -> FirmwareReleaseDownloader:
    return FirmwareReleaseDownloader(
        {"DOWNLOAD_DIR": str(tmp_path / "downloads")},
        CacheManager(cache_dir=str(tmp_path / "cache")),
    )


def _prerelease_dir(downloader: FirmwareReleaseDownloader) -> Path:
    path = (
        Path(downloader.download_dir)
        / FIRMWARE_DIR_NAME
        / FIRMWARE_PRERELEASES_DIR_NAME
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_v_prefixed_prerelease_directory_is_removed_when_superseded(
    downloader: FirmwareReleaseDownloader,
) -> None:
    prerelease_dir = _prerelease_dir(downloader)
    superseded = prerelease_dir / "firmware-v2.7.12.abcdef1"
    superseded.mkdir()

    assert downloader.cleanup_superseded_prereleases("v2.7.12") is True
    assert not superseded.exists()


def test_unparsable_prerelease_directory_is_preserved(
    downloader: FirmwareReleaseDownloader,
) -> None:
    prerelease_dir = _prerelease_dir(downloader)
    unparsable = prerelease_dir / "firmware-preview-build"
    unparsable.mkdir()

    assert downloader.cleanup_superseded_prereleases("v2.7.12") is False
    assert unparsable.is_dir()


def test_short_unparsable_prerelease_remains_valid_latest_target(
    downloader: FirmwareReleaseDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    prerelease_dir = _prerelease_dir(downloader)
    retained = prerelease_dir / "firmware-v2.8"
    retained.mkdir()
    observed: set[str] = set()

    def capture_retained(_base_dir: str, retained_names: set[str]) -> None:
        observed.update(retained_names)

    monkeypatch.setattr(
        downloader,
        "_cleanup_invalid_prerelease_latest_pointer",
        capture_retained,
    )

    assert downloader.cleanup_superseded_prereleases("v2.7.12") is False
    assert retained.is_dir()
    assert retained.name in observed


def test_numeric_build_suffix_does_not_change_semver_supersession(
    downloader: FirmwareReleaseDownloader,
) -> None:
    prerelease_dir = _prerelease_dir(downloader)
    superseded = prerelease_dir / "firmware-2.7.12.1"
    superseded.mkdir()

    assert downloader.cleanup_superseded_prereleases("v2.7.12") is True
    assert not superseded.exists()


def test_incomplete_release_baseline_does_not_prune_prereleases(
    downloader: FirmwareReleaseDownloader,
) -> None:
    prerelease_dir = _prerelease_dir(downloader)
    candidate = prerelease_dir / "firmware-2.7.12.abcdef1"
    candidate.mkdir()

    assert downloader.cleanup_superseded_prereleases("v2.7") is False
    assert candidate.is_dir()
