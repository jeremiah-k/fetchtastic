# Tests for get_zips_needing_extraction method
#
# Comprehensive unit tests for every branch of
# FirmwareReleaseDownloader.get_zips_needing_extraction.

import os
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import FIRMWARE_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config(tmp_path):
    """Provide a mock configuration dictionary using tmp_path."""
    return {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "AUTO_EXTRACT": True,
        "EXTRACT_PATTERNS": ["*.bin"],
        "EXCLUDE_PATTERNS": [],
        "SELECTED_FIRMWARE_ASSETS": [],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
    }


@pytest.fixture
def mock_cache_manager(tmp_path):
    """Mock CacheManager instance using tmp_path."""
    mock = Mock(spec=CacheManager)
    mock.cache_dir = str(tmp_path / "cache")
    mock.get_cache_file_path.side_effect = lambda file_name: os.path.join(
        mock.cache_dir, file_name
    )
    return mock


@pytest.fixture
def downloader(mock_config, mock_cache_manager):
    """Create a FirmwareReleaseDownloader with mocked dependencies."""
    dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)
    dl.cache_manager = mock_cache_manager
    dl.file_operations = Mock()
    dl.version_manager = Mock()
    real_version_manager = VersionManager()
    dl.version_manager.get_release_tuple.side_effect = (
        real_version_manager.get_release_tuple
    )
    dl.version_manager.extract_clean_version.side_effect = (
        real_version_manager.extract_clean_version
    )
    # Stub internal methods that are tested elsewhere
    dl._get_release_storage_tag = Mock(return_value="v2.3.2")
    dl._get_exclude_patterns = Mock(return_value=[])
    dl._matches_exclude_patterns = Mock(return_value=False)
    return dl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_release(tag: str = "v2.3.2", assets=None) -> Release:
    """Create a Release with optional assets."""
    release = Release(
        tag_name=tag,
        prerelease=False,
        published_at="2025-01-01T00:00:00Z",
    )
    if assets is not None:
        release.assets = list(assets)
    return release


def _setup_version_dir(
    downloader: FirmwareReleaseDownloader,
    storage_tag: str = "v2.3.2",
    zip_names: list | None = None,
) -> str:
    """Create the version directory and optional zip files on disk.

    Returns the version_dir path.
    """
    version_dir = os.path.join(downloader.download_dir, FIRMWARE_DIR_NAME, storage_tag)
    os.makedirs(version_dir, exist_ok=True)
    if zip_names:
        for name in zip_names:
            Path(os.path.join(version_dir, name)).touch()
    return version_dir


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.core_downloads
class TestGetZipsNeedingExtraction:
    """Test suite for the get_zips_needing_extraction method.

    Covers every branch of the method as implemented in
    src/fetchtastic/download/firmware.py lines 1094-1148.
    """

    pytestmark: ClassVar[list] = [pytest.mark.unit, pytest.mark.core_downloads]

    # --- Branch 1: AUTO_EXTRACT is False → returns [] ---

    def test_returns_empty_when_auto_extract_disabled(self, downloader):
        downloader.config["AUTO_EXTRACT"] = False
        release = _make_release()
        assert downloader.get_zips_needing_extraction(release) == []

    # --- Branch 2: EXTRACT_PATTERNS is empty list → returns [] ---

    def test_returns_empty_when_extract_patterns_empty_list(self, downloader):
        downloader.config["EXTRACT_PATTERNS"] = []
        release = _make_release()
        assert downloader.get_zips_needing_extraction(release) == []

    # --- Branch 3: EXTRACT_PATTERNS is a string → wrapped into list ---

    def test_string_extract_patterns_wrapped_in_list(self, downloader):
        """When EXTRACT_PATTERNS is a string it must be wrapped into a single-element list before being passed downstream."""
        downloader.config["EXTRACT_PATTERNS"] = "*.bin"
        version_dir = _setup_version_dir(downloader, "v2.3.2", ["device.zip"])
        asset = Asset(
            name="device.zip",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        downloader.file_operations.check_extraction_needed.return_value = True

        result = downloader.get_zips_needing_extraction(release)
        assert len(result) == 1
        # The wrapped list — not the raw string — must reach check_extraction_needed.
        downloader.file_operations.check_extraction_needed.assert_called_once_with(
            os.path.join(version_dir, "device.zip"),
            version_dir,
            ["*.bin"],
            [],
        )

    # --- Branch 4: _get_release_storage_tag raises ValueError → returns [] ---

    def test_returns_empty_on_value_error_from_storage_tag(self, downloader):
        downloader._get_release_storage_tag.side_effect = ValueError("unsafe tag")
        release = _make_release(tag="../../etc")
        result = downloader.get_zips_needing_extraction(release)
        assert result == []

    # --- Branch 5: version_dir doesn't exist → returns [] ---

    def test_returns_empty_when_version_dir_not_exists(self, downloader):
        # _get_release_storage_tag returns "v2.3.2" but we never create the dir
        release = _make_release()
        result = downloader.get_zips_needing_extraction(release)
        assert result == []

    # --- Branch 6: Asset with empty name → skipped ---

    def test_skips_asset_with_empty_name(self, downloader):
        _setup_version_dir(downloader, "v2.3.2")
        asset = Asset(name="", download_url="https://example.com/empty.zip", size=100)
        release = _make_release(assets=[asset])
        result = downloader.get_zips_needing_extraction(release)
        assert result == []
        downloader.file_operations.check_extraction_needed.assert_not_called()

    # --- Branch 7: Asset name doesn't end with .zip (case-insensitive) ---

    def test_skips_non_zip_asset(self, downloader):
        _setup_version_dir(downloader, "v2.3.2")
        asset = Asset(
            name="firmware.bin",
            download_url="https://example.com/f.bin",
            size=100,
        )
        release = _make_release(assets=[asset])
        result = downloader.get_zips_needing_extraction(release)
        assert result == []
        downloader.file_operations.check_extraction_needed.assert_not_called()

    def test_accepts_uppercase_zip_extension(self, downloader):
        """The .zip check is case-insensitive (.ZIP should pass)."""
        _setup_version_dir(downloader, "v2.3.2", ["device.ZIP"])
        asset = Asset(
            name="device.ZIP",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        downloader.file_operations.check_extraction_needed.return_value = True

        result = downloader.get_zips_needing_extraction(release)
        assert len(result) == 1
        assert result[0].name == "device.ZIP"

    # --- Branch 8: Asset doesn't match SELECTED_FIRMWARE_ASSETS → skipped ---

    @patch("fetchtastic.download.firmware.matches_selected_patterns")
    def test_skips_asset_not_matching_selected_patterns(self, mock_matches, downloader):
        mock_matches.return_value = False
        downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]
        _setup_version_dir(downloader, "v2.3.2")
        asset = Asset(
            name="heltec.zip",
            download_url="https://example.com/h.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        result = downloader.get_zips_needing_extraction(release)
        assert result == []
        mock_matches.assert_called_once_with("heltec.zip", ["rak4631"])

    # --- Branch 9: Asset matches exclude patterns → skipped ---

    def test_skips_asset_matching_exclude_patterns(self, downloader):
        downloader._matches_exclude_patterns.return_value = True
        _setup_version_dir(downloader, "v2.3.2")
        asset = Asset(
            name="device-debug.zip",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        result = downloader.get_zips_needing_extraction(release)
        assert result == []
        downloader.file_operations.check_extraction_needed.assert_not_called()

    # --- Branch 10: Zip file doesn't exist on disk → skipped ---

    def test_skips_asset_when_zip_not_on_disk(self, downloader):
        _setup_version_dir(downloader, "v2.3.2")  # dir exists, no zip files
        asset = Asset(
            name="device.zip",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        result = downloader.get_zips_needing_extraction(release)
        assert result == []

    # --- Branch 11: check_extraction_needed returns True → asset added ---

    def test_adds_asset_when_extraction_needed(self, downloader):
        version_dir = _setup_version_dir(downloader, "v2.3.2", ["device.zip"])
        asset = Asset(
            name="device.zip",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        downloader.file_operations.check_extraction_needed.return_value = True

        result = downloader.get_zips_needing_extraction(release)
        assert len(result) == 1
        assert result[0] is asset
        downloader.file_operations.check_extraction_needed.assert_called_once_with(
            os.path.join(version_dir, "device.zip"),
            version_dir,
            ["*.bin"],
            [],
        )

    # --- Branch 12: check_extraction_needed returns False → NOT added ---

    def test_skips_asset_when_extraction_not_needed(self, downloader):
        _setup_version_dir(downloader, "v2.3.2", ["device.zip"])
        asset = Asset(
            name="device.zip",
            download_url="https://example.com/d.zip",
            size=100,
        )
        release = _make_release(assets=[asset])
        downloader.file_operations.check_extraction_needed.return_value = False

        result = downloader.get_zips_needing_extraction(release)
        assert result == []

    # --- Branch 13: Mixed assets → correct subset returned ---

    def test_mixed_assets_returns_correct_subset(self, downloader):
        _setup_version_dir(downloader, "v2.3.2", ["device-a.zip", "device-b.zip"])
        asset_a = Asset(
            name="device-a.zip",
            download_url="https://example.com/a.zip",
            size=100,
        )
        asset_b = Asset(
            name="device-b.zip",
            download_url="https://example.com/b.zip",
            size=200,
        )
        asset_nonzip = Asset(
            name="readme.txt",
            download_url="https://example.com/r.txt",
            size=50,
        )
        release = _make_release(assets=[asset_a, asset_b, asset_nonzip])

        # Only device-a needs extraction
        def check_side_effect(zip_path, vdir, patterns, excludes):
            return "device-a" in zip_path

        downloader.file_operations.check_extraction_needed.side_effect = (
            check_side_effect
        )

        result = downloader.get_zips_needing_extraction(release)
        assert len(result) == 1
        assert result[0] is asset_a

    # --- Branch 14: SELECTED_FIRMWARE_ASSETS is empty → all pass that filter ---

    @patch("fetchtastic.download.firmware.matches_selected_patterns")
    def test_empty_selected_firmware_assets_passes_all(self, mock_matches, downloader):
        """When SELECTED_FIRMWARE_ASSETS is empty the selected-pattern check
        is short-circuited and every asset passes that filter."""
        downloader.config["SELECTED_FIRMWARE_ASSETS"] = []
        _setup_version_dir(downloader, "v2.3.2", ["dev1.zip", "dev2.zip"])
        asset1 = Asset(
            name="dev1.zip",
            download_url="https://example.com/1.zip",
            size=100,
        )
        asset2 = Asset(
            name="dev2.zip",
            download_url="https://example.com/2.zip",
            size=200,
        )
        release = _make_release(assets=[asset1, asset2])
        downloader.file_operations.check_extraction_needed.return_value = True

        result = downloader.get_zips_needing_extraction(release)
        assert len(result) == 2
        assert result[0] is asset1
        assert result[1] is asset2
        # matches_selected_patterns must never be called (short-circuited)
        mock_matches.assert_not_called()
