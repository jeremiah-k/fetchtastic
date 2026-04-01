import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from fetchtastic.constants import (
    APP_DIR_NAME,
    DESKTOP_DIR_NAME,
    DESKTOP_PRERELEASES_DIR_NAME,
    RELEASE_SCAN_COUNT,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.desktop import MeshtasticDesktopDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def mock_cache_manager(tmp_path):
    """Create a minimal cache manager mock for Desktop downloader tests."""
    mock = Mock(spec=CacheManager)
    mock.cache_dir = str(tmp_path / "cache")
    mock.get_cache_file_path.side_effect = lambda file_name: os.path.join(
        mock.cache_dir, file_name
    )
    return mock


@pytest.fixture
def downloader(tmp_path, mock_cache_manager):
    """Create a Desktop downloader with mocked dependencies."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "EXCLUDE_PATTERNS": [],
        "SELECTED_DESKTOP_PLATFORMS": [],
    }
    dl = MeshtasticDesktopDownloader(config, mock_cache_manager)
    dl.cache_manager = mock_cache_manager
    dl.version_manager = Mock()
    real_version_manager = VersionManager()
    dl.version_manager.get_release_tuple.side_effect = (
        real_version_manager.get_release_tuple
    )
    dl.version_manager.is_prerelease_version.side_effect = (
        real_version_manager.is_prerelease_version
    )
    return dl


def test_is_release_complete_uses_prerelease_directory(downloader, tmp_path):
    """Prerelease completeness checks should read from app/desktop/prereleases/<tag>."""
    downloader.verify = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20-open.1",
        prerelease=True,
        assets=[
            Asset(
                name="Meshtastic-2.7.20-open.1.AppImage",
                download_url="https://example.invalid/desktop",
                size=4,
            )
        ],
    )

    prerelease_dir = (
        tmp_path
        / "downloads"
        / APP_DIR_NAME
        / DESKTOP_DIR_NAME
        / DESKTOP_PRERELEASES_DIR_NAME
        / "v2.7.20-open.1"
    )
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "Meshtastic-2.7.20-open.1.AppImage").write_bytes(b"desk")

    assert downloader.is_release_complete(release) is True


def test_is_release_complete_ignores_non_installer_assets(downloader, tmp_path):
    """Completeness should only require installer assets selected by get_assets()."""
    downloader.verify = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[
            Asset(
                name="Meshtastic-2.7.20.dmg",
                download_url="https://example.invalid/dmg",
                size=4,
            ),
            Asset(
                name="Meshtastic-2.7.20.sha256",
                download_url="https://example.invalid/sha",
                size=64,
            ),
        ],
    )

    stable_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    stable_dir.mkdir(parents=True)
    (stable_dir / "Meshtastic-2.7.20.dmg").write_bytes(b"dmg!")

    assert downloader.is_release_complete(release) is True


def test_get_releases_uses_retention_default_for_scan_window(downloader):
    """Without explicit desktop retention config, initial scan should stay at RELEASE_SCAN_COUNT."""
    downloader.config.pop("DESKTOP_VERSIONS_TO_KEEP", None)
    downloader.github_source.fetch_raw_releases_data = Mock(return_value=[])

    releases = downloader.get_releases()

    assert releases == []
    downloader.github_source.fetch_raw_releases_data.assert_called_once_with(
        {"per_page": RELEASE_SCAN_COUNT}
    )
