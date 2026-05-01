# Test Firmware Downloader
#
# Comprehensive unit tests for the FirmwareReleaseDownloader class.

import json
import os
import zipfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import ANY, Mock, patch

import pytest
import requests

from fetchtastic import log_utils
from fetchtastic.constants import (
    FILE_TYPE_FIRMWARE_MANIFEST,
    FIRMWARE_DIR_NAME,
    FIRMWARE_PRERELEASES_DIR_NAME,
    LATEST_POINTER_NAME,
    RELEASE_SCAN_COUNT,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.version import VersionManager


# Module-level fixtures shared across test classes
@pytest.fixture
def mock_config(tmp_path):
    """Provide a mock configuration dictionary using tmp_path."""
    return {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "CHECK_FIRMWARE_PRERELEASES": True,
        "SELECTED_PRERELEASE_ASSETS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        "FILTER_REVOKED_RELEASES": True,
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
    dl.version_manager = Mock()
    dl.file_operations = Mock()
    real_version_manager = VersionManager()
    dl.version_manager.get_release_tuple.side_effect = (
        real_version_manager.get_release_tuple
    )
    dl.version_manager.extract_clean_version.side_effect = (
        real_version_manager.extract_clean_version
    )
    return dl


class TestFirmwareReleaseDownloader:
    """Test suite for FirmwareReleaseDownloader."""

    pytestmark: ClassVar[list] = [pytest.mark.unit, pytest.mark.core_downloads]

    def _expected_cleanup_fetch_limit(
        self, keep_limit: int, keep_last_beta: bool, filter_revoked: bool = True
    ) -> int:
        """
        Compute the numeric limit to request from the release API when cleaning up old firmware versions.

        Parameters:
            keep_limit (int): Number of releases to keep locally.
            keep_last_beta (bool): If True, ensure at least RELEASE_SCAN_COUNT releases are considered to retain the most recent beta alongside kept releases.
            filter_revoked (bool): If True, add an additional RELEASE_SCAN_COUNT to account for revocation filtering.

        Returns:
            int: The calculated fetch limit to pass to get_releases.
        """
        base = max(keep_limit, RELEASE_SCAN_COUNT) if keep_last_beta else keep_limit
        if filter_revoked:
            base += RELEASE_SCAN_COUNT
        return min(base, 100)

    def test_init(self, mock_config, mock_cache_manager):
        """Test downloader initialization."""
        with (
            patch("fetchtastic.download.base.VersionManager") as mock_version,
            patch("fetchtastic.download.firmware.PrereleaseHistoryManager"),
        ):
            dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)

            assert dl.config == mock_config
            assert (
                dl.firmware_releases_url
                == "https://api.github.com/repos/meshtastic/firmware/releases"
            )
            assert dl.latest_release_file == "latest_firmware_release.json"
            mock_version.assert_called_once()

    def test_get_target_path_for_release(self, downloader):
        """Test target path generation for firmware releases."""
        path = downloader.get_target_path_for_release("v1.0.0", "firmware.zip")

        expected = os.path.join(
            downloader.config["DOWNLOAD_DIR"], "firmware", "v1.0.0", "firmware.zip"
        )
        assert path == expected

    def test_ensure_release_notes_writes_file(self, tmp_path):
        """Release notes should be written alongside firmware assets."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            body="Release notes for v1.0.0",
        )

        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        notes_file = Path(notes_path)
        assert notes_file.exists()
        assert "release_notes-v1.0.0.md" in str(notes_file)
        assert "Release notes for v1.0.0" in notes_file.read_text(encoding="utf-8")

    def test_ensure_release_notes_revoked_directory(self, tmp_path):
        """Revoked firmware releases should store notes under a -revoked folder."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        release = Release(
            tag_name="v1.0.1",
            prerelease=False,
            name="(Revoked)",
            body="Revoked due to regressions.",
        )

        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        assert "v1.0.1-revoked" in notes_path
        assert Path(notes_path).exists()

    def test_ensure_release_notes_alpha_directory(self, tmp_path):
        """Alpha firmware releases should store notes under an -alpha folder."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        release = Release(
            tag_name="v1.0.2",
            prerelease=False,
            name="Meshtastic Firmware 1.0.2 Alpha",
            body="Alpha notes for v1.0.2",
        )

        base_dir = Path(config["DOWNLOAD_DIR"]) / FIRMWARE_DIR_NAME
        old_dir = base_dir / "v1.0.2"
        old_dir.mkdir(parents=True)

        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        assert "v1.0.2-alpha" in notes_path
        assert (base_dir / "v1.0.2-alpha").exists()
        assert not old_dir.exists()

    def test_ensure_release_notes_alpha_revoked_directory(self, tmp_path):
        """Revoked alpha firmware releases should store notes under -revoked suffix (replacing channel suffix)."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        release = Release(
            tag_name="v1.0.3",
            prerelease=False,
            name="Meshtastic Firmware 1.0.3 Alpha (Revoked)",
            body="This release was revoked due to regressions.",
        )

        base_dir = Path(config["DOWNLOAD_DIR"]) / FIRMWARE_DIR_NAME
        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        assert "v1.0.3-revoked" in notes_path
        assert (base_dir / "v1.0.3-revoked").exists()

    def test_ensure_release_notes_unsafe_tag(self, tmp_path):
        """Unsafe tags should skip release notes storage."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        release = Release(
            tag_name="../v1.0.4",
            prerelease=False,
            body="Bad tag notes",
        )

        assert downloader.ensure_release_notes(release) is None

    @patch("fetchtastic.download.github_source.make_github_api_request")
    def test_get_releases_success(self, mock_request, downloader):
        """Test successful release fetching from GitHub."""
        # Mock cache to return None so it falls back to API
        downloader.cache_manager.read_releases_cache_entry.return_value = None
        downloader.cache_manager.write_releases_cache_entry = Mock()

        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "tag_name": "v1.0.0",
                "prerelease": False,
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631.zip",
                        "browser_download_url": "https://example.com/firmware-rak4631.zip",
                        "size": 1000000,
                    }
                ],
            }
        ]
        mock_request.return_value = mock_response

        releases = downloader.get_releases(limit=10)

        assert len(releases) == 1
        assert releases[0].tag_name == "v1.0.0"
        assert releases[0].prerelease is False
        assert len(releases[0].assets) == 1

    @patch("fetchtastic.download.github_source.make_github_api_request")
    def test_get_releases_skips_malformed_entries(self, mock_request, downloader):
        """Malformed releases/assets should be skipped without dropping valid releases."""
        downloader.cache_manager.read_releases_cache_entry.return_value = None
        downloader.cache_manager.write_releases_cache_entry = Mock()

        mock_response = Mock()
        mock_response.json.return_value = [
            {
                # Missing tag_name
                "prerelease": False,
                "assets": [
                    {
                        "name": "firmware-invalid.zip",
                        "browser_download_url": "https://example.com/firmware-invalid.zip",
                        "size": 100,
                    }
                ],
            },
            {
                "tag_name": "v1.1.0",
                "prerelease": False,
                # Invalid size should skip this asset, then skip release (no valid assets)
                "assets": [
                    {
                        "name": "firmware-bad-size.zip",
                        "browser_download_url": "https://example.com/firmware-bad-size.zip",
                        "size": "not-an-int",
                    }
                ],
            },
            {
                "tag_name": "v1.1.1",
                "prerelease": False,
                # Blank download URL should skip this asset, then skip release.
                "assets": [
                    {
                        "name": "firmware-no-url.zip",
                        "browser_download_url": " ",
                        "size": 500,
                    }
                ],
            },
            {
                "tag_name": "v1.0.0",
                "prerelease": False,
                "assets": [
                    {
                        "name": "firmware-rak4631.zip",
                        "browser_download_url": "https://example.com/firmware-rak4631.zip",
                        "size": 1000000,
                    }
                ],
            },
        ]
        mock_request.return_value = mock_response

        releases = downloader.get_releases(limit=10)

        assert len(releases) == 1
        assert releases[0].tag_name == "v1.0.0"
        assert len(releases[0].assets) == 1

    def test_get_assets_firmware_filtering(self, downloader):
        """Test that get_assets returns all assets from the release."""
        asset1 = Mock(spec=Asset)
        asset1.name = "firmware-rak4631.zip"
        asset1.download_url = "url1"
        asset1.size = 1000

        asset2 = Mock(spec=Asset)
        asset2.name = "firmware-tbeam.zip"
        asset2.download_url = "url2"
        asset2.size = 2000

        asset3 = Mock(spec=Asset)
        asset3.name = "readme.txt"
        asset3.download_url = "url3"
        asset3.size = 100

        release = Mock(spec=Release)
        release.assets = [asset1, asset2, asset3]

        assets = downloader.get_assets(release)

        assert len(assets) == 3
        assert assets[0].name == "firmware-rak4631.zip"
        assert assets[1].name == "firmware-tbeam.zip"
        assert assets[2].name == "readme.txt"

    def test_get_download_url(self, downloader):
        """Test download URL retrieval."""
        asset = Mock(spec=Asset)
        asset.download_url = "https://example.com/firmware.zip"

        url = downloader.get_download_url(asset)

        assert url == "https://example.com/firmware.zip"

    def test_download_firmware_success(self, downloader):
        """
        Verify that downloading and extracting a firmware asset succeeds and returns expected metadata.

        Parameters:
            downloader (FirmwareReleaseDownloader): Fixture instance under test with verification and extraction methods mocked.
        """
        # Setup mocks
        downloader.is_asset_complete = Mock(return_value=False)
        downloader.download = Mock(return_value=True)

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"
        asset.download_url = "https://example.com/firmware.zip"
        asset.size = 1000000

        # Mock verification and extraction
        downloader.verify = Mock(return_value=True)
        downloader.extract_firmware = Mock(return_value=["firmware.bin"])

        result = downloader.download_firmware(release, asset)

        assert result.success is True
        assert result.release_tag == "v1.0.0"
        assert "firmware-rak4631.zip" in str(result.file_path)
        downloader.download.assert_called_once()

    def test_download_firmware_skips_revoked_when_filtered(self, downloader):
        """Revoked releases are skipped when revoked filtering is enabled."""
        downloader.config["FILTER_REVOKED_RELEASES"] = True
        downloader.is_release_revoked = Mock(return_value=True)
        downloader.download = Mock()
        downloader.verify = Mock(return_value=True)

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"
        asset.download_url = "https://example.com/firmware.zip"
        asset.size = 1000000

        result = downloader.download_firmware(release, asset)

        assert result.success is True
        assert result.was_skipped is True
        assert result.file_path == Path(
            os.path.join(downloader.download_dir, FIRMWARE_DIR_NAME)
        )
        assert result.error_type == "revoked_release"
        assert result.error_details == {
            "revoked": True,
            "filter_revoked_releases": True,
        }
        downloader.download.assert_not_called()
        downloader.verify.assert_not_called()

    def test_download_firmware_download_failure(self, downloader):
        """Test firmware download failure."""
        # Force the internal download call to report a failure without real I/O
        downloader.download = Mock(return_value=False)

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"
        asset.download_url = "https://example.com/firmware.zip"
        asset.size = 1000000

        result = downloader.download_firmware(release, asset)

        assert result.success is False
        assert result.error_type == "network_error"

    def test_extract_firmware_success(self, downloader, tmp_path):
        """Test successful firmware extraction."""
        # Mock release and asset
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"

        zip_path = tmp_path / "firmware-rak4631.zip"
        zip_path.write_bytes(b"dummy zip")
        downloader.get_target_path_for_release = Mock(return_value=str(zip_path))

        # Mock file operations
        downloader.file_operations.validate_extraction_patterns.return_value = True
        downloader.file_operations.check_extraction_needed.return_value = True
        downloader.file_operations.extract_archive = Mock(return_value=["firmware.bin"])
        downloader.file_operations.generate_hash_for_extracted_files.return_value = None

        result = downloader.extract_firmware(release, asset, ["*.bin"], ["readme*"])

        assert result.success is True
        assert result.extracted_files == ["firmware.bin"]
        downloader.file_operations.extract_archive.assert_called_once()

    def test_extract_firmware_no_matches_is_warning(self, downloader, tmp_path):
        """Test extraction when no files match patterns."""

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rp2040.zip"

        zip_path = tmp_path / "firmware-rp2040.zip"
        zip_path.write_bytes(b"dummy zip")
        downloader.get_target_path_for_release = Mock(return_value=str(zip_path))

        downloader.file_operations.validate_extraction_patterns.return_value = True
        downloader.file_operations.check_extraction_needed.return_value = True
        downloader.file_operations.extract_archive.return_value = []

        result = downloader.extract_firmware(release, asset, ["*.bin"], ["readme*"])

        assert result.success is True
        assert result.was_skipped is True
        assert result.extracted_files == []

    def test_validate_extraction_patterns(self, downloader):
        """Test extraction pattern validation."""
        # Mock file operations
        downloader.file_operations.validate_extraction_patterns = Mock(
            side_effect=[True, False]
        )

        # Valid patterns
        result = downloader.validate_extraction_patterns(["*.bin", "*.elf"], ["*.tmp"])
        assert result is True

        # Invalid patterns with path traversal
        result = downloader.validate_extraction_patterns(["../*.bin"], [])
        assert result is False

    def test_check_extraction_needed(self, downloader):
        """Test extraction needed check."""
        # Mock file operations
        downloader.file_operations.check_extraction_needed = Mock(return_value=True)

        result = downloader.check_extraction_needed(
            "/tmp/firmware.zip", "/tmp/extract", ["*.bin"], ["*.tmp"]
        )

        assert result is True
        downloader.file_operations.check_extraction_needed.assert_called_once()

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup of old firmware versions."""
        # Setup filesystem mocks
        mock_exists.return_value = True

        # Create mock directory entries for os.scandir
        mock_v1 = Mock()
        mock_v1.name = "v1.0.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v1.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v2.0.0"

        mock_v3 = Mock()
        mock_v3.name = "v3.0.0"
        mock_v3.is_symlink.return_value = False
        mock_v3.is_dir.return_value = True
        mock_v3.path = "/mock/firmware/v3.0.0"

        mock_prerelease = Mock()
        mock_prerelease.name = "prerelease"
        mock_prerelease.is_symlink.return_value = False
        mock_prerelease.is_dir.return_value = True
        mock_prerelease.path = "/mock/firmware/prerelease"

        mock_repo_dls = Mock()
        mock_repo_dls.name = "repo-dls"
        mock_repo_dls.is_symlink.return_value = False
        mock_repo_dls.is_dir.return_value = True
        mock_repo_dls.path = "/mock/firmware/repo-dls"

        mock_scandir.return_value.__enter__.return_value = [
            mock_v1,
            mock_v2,
            mock_v3,
            mock_prerelease,
            mock_repo_dls,
        ]

        downloader.get_releases = Mock(
            return_value=[Release(tag_name="v3.0.0"), Release(tag_name="v2.0.0")]
        )

        downloader.cleanup_old_versions(keep_limit=2)

        # Should remove version not in the keep set (v1.0.0)
        mock_rmtree.assert_called_once()
        args = mock_rmtree.call_args[0][0]
        assert "v1.0.0" in args
        expected_limit = self._expected_cleanup_fetch_limit(
            keep_limit=2, keep_last_beta=False
        )
        downloader.get_releases.assert_called_once_with(limit=expected_limit)

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_unsafe_tags(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup when release tags contain unsafe characters."""
        mock_exists.return_value = True

        # Create mock directory entries for os.scandir
        mock_v1 = Mock()
        mock_v1.name = "v1.0.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v1.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v2.0.0"

        mock_scandir.return_value.__enter__.return_value = [mock_v1, mock_v2]

        downloader.get_releases = Mock(
            return_value=[
                Release(tag_name="v1.0.0"),
                Release(tag_name="../../../unsafe"),
            ]
        )

        downloader.cleanup_old_versions(keep_limit=2)

        # Should remove v2.0.0 since only v1.0.0 is safe
        mock_rmtree.assert_called_once()
        args = mock_rmtree.call_args[0][0]
        assert "v2.0.0" in args
        assert "v1.0.0" not in args
        # Warning is logged but caplog testing is optional

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_keep_zero(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup with keep_limit=0 removes all versions."""
        # Setup filesystem mocks
        mock_exists.return_value = True

        # Create mock DirEntry objects for os.scandir
        mock_entry1 = Mock()
        mock_entry1.name = "v1.0.0"
        mock_entry1.is_dir.return_value = True
        mock_entry1.is_symlink.return_value = False
        mock_entry1.path = "/path/to/firmware/v1.0.0"

        mock_entry2 = Mock()
        mock_entry2.name = "v2.0.0"
        mock_entry2.is_dir.return_value = True
        mock_entry2.is_symlink.return_value = False
        mock_entry2.path = "/path/to/firmware/v2.0.0"

        mock_scandir.return_value.__enter__ = Mock(
            return_value=[mock_entry1, mock_entry2]
        )
        mock_scandir.return_value.__exit__ = Mock(return_value=None)

        downloader.get_releases = Mock(return_value=[])

        downloader.cleanup_old_versions(keep_limit=0)

        # Should remove all versions
        expected_limit = self._expected_cleanup_fetch_limit(
            keep_limit=0, keep_last_beta=False
        )
        downloader.get_releases.assert_called_once_with(limit=expected_limit)
        assert mock_rmtree.call_count == 2
        calls = mock_rmtree.call_args_list
        removed_paths = {call[0][0] for call in calls}
        assert any("v1.0.0" in path for path in removed_paths)
        assert any("v2.0.0" in path for path in removed_paths)

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_negative_keep_limit(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup with negative keep_limit skips cleanup."""
        # Setup filesystem mocks
        mock_exists.return_value = True

        # Create mock directory entries for os.scandir
        mock_v1 = Mock()
        mock_v1.name = "v1.0.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v1.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v2.0.0"

        mock_scandir.return_value.__enter__.return_value = [mock_v1, mock_v2]

        # Mock get_releases to track calls
        downloader.get_releases = Mock()

        # Should skip cleanup entirely for negative keep_limit
        downloader.cleanup_old_versions(keep_limit=-1)

        # Should not call get_releases or rmtree for negative keep_limit
        downloader.get_releases.assert_not_called()
        mock_scandir.assert_not_called()
        mock_rmtree.assert_not_called()

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_skips_when_keep_set_mismatched(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Skip cleanup when expected tags do not match existing directories."""
        mock_exists.return_value = True
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        mock_v1 = Mock()
        mock_v1.name = "v1.0.0-alpha"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v1.0.0-alpha"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0-alpha"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v2.0.0-alpha"

        mock_scandir.return_value.__enter__.return_value = [mock_v1, mock_v2]

        downloader.get_releases = Mock(
            return_value=[Release(tag_name="v2.0.0"), Release(tag_name="v1.0.0")]
        )

        downloader.cleanup_old_versions(keep_limit=2)

        mock_rmtree.assert_not_called()

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_all_unsafe_tags(
        self, mock_rmtree, mock_scandir, mock_exists, downloader, mocker
    ):
        """Cleanup should bail when no safe tags are available to keep."""
        # Force the firmware directory to appear present.
        mock_exists.return_value = True

        # Every release tag should fail sanitization to hit the warning/return path.
        downloader.get_releases = Mock(
            return_value=[Release(tag_name="../unsafe"), Release(tag_name="..\\bad")]
        )
        mocker.patch.object(
            downloader, "_sanitize_required", side_effect=ValueError("unsafe")
        )

        # Capture warnings for the empty keep set scenario.
        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        downloader.cleanup_old_versions(keep_limit=2)

        # No filesystem deletion should happen when the keep set is empty.
        mock_rmtree.assert_not_called()
        mock_scandir.assert_not_called()
        assert mock_logger.warning.called

    @patch("fetchtastic.download.github_source.make_github_api_request")
    def test_get_releases_negative_limit(self, mock_api_request, downloader):
        """Test get_releases with negative limit uses default behavior."""
        # Mock API response
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_api_request.return_value = mock_response

        # Test with negative limit - should use default behavior
        with (
            patch.object(downloader.cache_manager, "build_url_cache_key") as mock_key,
            patch.object(
                downloader.cache_manager, "read_releases_cache_entry"
            ) as mock_read,
            patch.object(
                downloader.cache_manager, "write_releases_cache_entry"
            ) as _mock_write,
        ):
            mock_read.return_value = None
            mock_key.return_value = "test_key"

            result = downloader.get_releases(limit=-1)

            # Should call get_releases with default behavior (no limit validation in params)
            assert result == []
            # Should have made API call with default params (per_page=8)
            mock_api_request.assert_called_once()

    def test_get_latest_release_tag(self, mock_config, tmp_path):
        """Test getting latest release tag from cache file."""
        cache_manager = CacheManager(str(tmp_path))
        downloader = FirmwareReleaseDownloader(mock_config, cache_manager)
        cache_file = cache_manager.get_cache_file_path(downloader.latest_release_file)
        Path(cache_file).write_text(json.dumps({"latest_version": "v2.0.0"}))

        tag = downloader.get_latest_release_tag()

        assert tag == "v2.0.0"

    @patch("datetime.datetime")
    def test_update_latest_release_tag(self, mock_datetime, downloader):
        """Test updating latest release tag."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        downloader.cache_manager.atomic_write_json = Mock(return_value=True)
        cache_path = os.path.join(
            downloader.cache_manager.cache_dir, "latest_firmware_release.json"
        )
        downloader.cache_manager.get_cache_file_path.return_value = cache_path

        result = downloader.update_latest_release_tag("v2.0.0")

        assert result is True
        downloader.cache_manager.atomic_write_json.assert_called_once_with(
            cache_path, ANY
        )

    def test_get_prerelease_patterns(self, downloader):
        """Test getting prerelease patterns from config."""
        patterns = downloader._get_prerelease_patterns()

        assert "rak4631" in patterns

    def test_matches_exclude_patterns(self, downloader):
        """Test exclude pattern matching."""
        assert (
            downloader._matches_exclude_patterns("firmware-debug.zip", ["*debug*"])
            is True
        )
        assert (
            downloader._matches_exclude_patterns("firmware.zip", ["*debug*"]) is False
        )

    def test_fetch_prerelease_directory_listing(self, downloader):
        """Test fetching prerelease directory listing."""
        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {"name": "firmware-rak4631-v1.0.0.abc123.zip", "download_url": "url1"},
                {"name": "readme.txt", "download_url": "url2"},
            ]
        )

        listing = downloader._fetch_prerelease_directory_listing(
            "prerelease_dir", force_refresh=True
        )

        assert len(listing) == 2
        downloader.cache_manager.get_repo_contents.assert_called_once()

    def test_download_manifests_includes_release_manifest_json(
        self, downloader, tmp_path
    ):
        """Both per-device *.mt.json and release-level firmware-*.json files should be downloaded."""
        downloader.download_dir = str(tmp_path)

        def _write_manifest(_url: str, target: str) -> bool:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            if target.endswith(".mt.json"):
                Path(target).write_text(
                    json.dumps(
                        {
                            "hwModelSlug": "RAK4631",
                            "files": [],
                            "part": [],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                Path(target).write_text("{}", encoding="utf-8")
            return True

        downloader.download = Mock(side_effect=_write_manifest)
        downloader.verify = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-rak4631-2.7.20.abcdef0.mt.json",
                    download_url="https://example.invalid/rak.mt.json",
                    size=100,
                ),
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/release.json",
                    size=200,
                ),
                Asset(
                    name="firmware-rak4631-2.7.20.abcdef0.zip",
                    download_url="https://example.invalid/rak.zip",
                    size=300,
                ),
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 2
        assert all(result.success for result in results)
        assert all(
            result.file_type == FILE_TYPE_FIRMWARE_MANIFEST for result in results
        )
        downloaded_names = sorted(
            Path(str(result.file_path)).name for result in results
        )
        assert downloaded_names == [
            "firmware-2.7.20.abcdef0.json",
            "firmware-rak4631-2.7.20.abcdef0.mt.json",
        ]

    def test_parse_manifest_data_includes_ui_flags(self, downloader):
        """Manifest parsing should preserve has_mui and has_inkhud fields."""
        parsed = downloader._parse_manifest_data(
            {
                "version": "2.7.20.abcdef0",
                "hwModelSlug": "RAK4631",
                "has_mui": True,
                "has_inkhud": False,
            }
        )

        assert parsed is not None
        assert parsed.has_mui is True
        assert parsed.has_inkhud is False

    def test_parse_manifest_data_returns_none_on_exception(self, downloader):
        """Manifest parsing should return None when dataclass construction fails."""
        with patch(
            "fetchtastic.download.firmware.FirmwareManifest",
            side_effect=TypeError("bad data"),
        ):
            parsed = downloader._parse_manifest_data({"version": "1.0.0"})

        assert parsed is None

    def test_is_release_manifest_name_detects_release_json(self, downloader):
        """Release-level manifest names should be detected."""
        assert (
            downloader._is_release_manifest_name("firmware-2.7.20.abcdef0.json") is True
        )
        assert downloader._is_release_manifest_name("FIRMWARE-2.7.20.json") is True

    def test_is_release_manifest_name_rejects_device_manifest(self, downloader):
        """Per-device manifests (.mt.json) should be rejected."""
        assert (
            downloader._is_release_manifest_name(
                "firmware-rak4631-2.7.20.abcdef0.mt.json"
            )
            is False
        )

    def test_is_release_manifest_name_rejects_non_firmware_prefix(self, downloader):
        """Non-firmware prefixed files should be rejected."""
        assert downloader._is_release_manifest_name("other-2.7.20.json") is False
        assert downloader._is_release_manifest_name("config.json") is False

    def test_is_manifest_asset_name_accepts_both_types(self, downloader):
        """Both per-device and release-level manifests should be accepted."""
        assert (
            downloader._is_manifest_asset_name(
                "firmware-rak4631-2.7.20.abcdef0.mt.json"
            )
            is True
        )
        assert (
            downloader._is_manifest_asset_name("firmware-2.7.20.abcdef0.json") is True
        )

    def test_is_manifest_asset_name_rejects_non_manifest(self, downloader):
        """Non-manifest files should be rejected."""
        assert (
            downloader._is_manifest_asset_name("firmware-rak4631-2.7.20.zip") is False
        )
        assert downloader._is_manifest_asset_name("readme.txt") is False

    def test_download_manifests_skips_unsafe_tag(self, downloader):
        """Unsafe release tags should skip manifest downloads."""
        release = Release(tag_name="../v1.0.0", prerelease=False, assets=[])
        results = downloader.download_manifests(release)
        assert results == []

    def test_download_manifests_skips_unsafe_asset_name_and_continues(
        self, downloader, tmp_path
    ):
        """Unsafe manifest filenames should fail per-asset without aborting the release."""
        downloader.download_dir = str(tmp_path)

        def _write_manifest(_url: str, target: str) -> bool:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text("{}", encoding="utf-8")
            return True

        downloader.download = Mock(side_effect=_write_manifest)
        downloader.verify = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-../unsafe.json",
                    download_url="https://example.invalid/unsafe.json",
                    size=10,
                ),
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/release.json",
                    size=2,
                ),
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 2
        assert results[0].success is False
        assert results[0].error_type == "validation_error"
        assert results[1].success is True

    def test_download_manifests_verification_failure(self, downloader, tmp_path):
        """Manifest verification failure should be reported and file cleaned up."""
        downloader.download_dir = str(tmp_path)

        def _write_invalid_manifest(_url: str, target: str) -> bool:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text("{}", encoding="utf-8")
            return True

        downloader.download = Mock(side_effect=_write_invalid_manifest)
        downloader.verify = Mock(return_value=False)
        downloader.cleanup_file = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/manifest.json",
                    size=100,
                )
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_type == "validation_error"
        downloader.cleanup_file.assert_called_once()

    def test_download_manifests_manifest_read_oserror_is_filesystem_error(
        self, downloader, tmp_path
    ):
        """Manifest read I/O errors should be classified as filesystem errors."""
        downloader.download_dir = str(tmp_path)
        downloader.download = Mock(return_value=True)
        downloader.verify = Mock(return_value=True)
        downloader.cleanup_file = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/manifest.json",
                    size=100,
                )
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_type == "filesystem_error"
        assert results[0].is_retryable is False
        downloader.cleanup_file.assert_called_once()

    def test_download_manifests_download_failure(self, downloader, tmp_path):
        """Manifest download failure should be reported."""
        downloader.download_dir = str(tmp_path)
        downloader.download = Mock(return_value=False)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/manifest.json",
                    size=100,
                )
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_type == "network_error"

    def test_download_manifests_existing_invalid_json_redownloads(
        self, downloader, tmp_path
    ):
        """Existing manifest with invalid JSON should be redownloaded."""
        downloader.download_dir = str(tmp_path)

        target_path = downloader.get_target_path_for_release(
            "v2.7.20", "firmware-2.7.20.abcdef0.json"
        )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_text("not valid json", encoding="utf-8")

        def _write_manifest(_url: str, target: str) -> bool:
            Path(target).write_text('{"valid":true}', encoding="utf-8")
            return True

        downloader.download = Mock(side_effect=_write_manifest)
        downloader.verify = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/manifest.json",
                    size=14,
                )
            ],
        )

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].was_skipped is False
        downloader.download.assert_called_once()

    def test_matches_prerelease_selection_returns_true_without_patterns(
        self, downloader
    ):
        """_matches_prerelease_selection should return True when no patterns are configured."""
        assert downloader._matches_prerelease_selection("any-file.zip", []) is True

    def test_matches_prerelease_selection_matches_patterns(self, downloader, mocker):
        """_matches_prerelease_selection should delegate to matches_extract_patterns."""
        mock_match = mocker.patch(
            "fetchtastic.download.firmware.matches_extract_patterns", return_value=True
        )
        assert (
            downloader._matches_prerelease_selection(
                "firmware-rak4631.zip", ["rak4631"]
            )
            is True
        )
        mock_match.assert_called_once()

    def test_matches_prerelease_selection_keeps_release_manifest(
        self, downloader, mocker
    ):
        """_matches_prerelease_selection should keep release-level manifest even without pattern match."""
        mocker.patch(
            "fetchtastic.download.firmware.matches_extract_patterns", return_value=False
        )
        assert (
            downloader._matches_prerelease_selection(
                "firmware-2.7.20.abcdef0.json", ["rak4631"]
            )
            is True
        )

    def test_download_manifests_redownloads_when_existing_size_mismatches(
        self, downloader, tmp_path
    ):
        """Existing valid JSON should still be redownloaded when file size mismatches."""
        downloader.download_dir = str(tmp_path)
        downloader.verify = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/release.json",
                    size=10,
                )
            ],
        )
        target_path = downloader.get_target_path_for_release(
            "v2.7.20", "firmware-2.7.20.abcdef0.json"
        )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_text("{}", encoding="utf-8")

        def _write_manifest(_url: str, target: str) -> bool:
            Path(target).write_text('{"fresh":1}', encoding="utf-8")
            return True

        downloader.download = Mock(side_effect=_write_manifest)

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].was_skipped is False
        downloader.download.assert_called_once()

    def test_download_manifests_skips_when_json_and_size_match(
        self, downloader, tmp_path
    ):
        """Existing manifest should be skipped only when JSON is valid and size matches."""
        downloader.download_dir = str(tmp_path)
        downloader.verify = Mock(return_value=True)

        release = Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-2.7.20.abcdef0.json",
                    download_url="https://example.invalid/release.json",
                    size=2,
                )
            ],
        )
        target_path = downloader.get_target_path_for_release(
            "v2.7.20", "firmware-2.7.20.abcdef0.json"
        )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_text("{}", encoding="utf-8")
        downloader.download = Mock(return_value=True)

        results = downloader.download_manifests(release)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].was_skipped is True
        downloader.download.assert_not_called()

    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_superseded_prereleases(
        self, mock_rmtree, mock_scandir, downloader
    ):
        """Test cleanup of superseded prereleases."""

        # Create mock directory entries for os.scandir
        mock_firmware1 = Mock()
        mock_firmware1.name = "firmware-1.0.0.abc123"
        mock_firmware1.is_symlink.return_value = False
        mock_firmware1.is_dir.return_value = True
        mock_firmware1.path = "/mock/prerelease/firmware-1.0.0.abc123"

        mock_firmware2 = Mock()
        mock_firmware2.name = "firmware-2.0.0.def456"
        mock_firmware2.is_symlink.return_value = False
        mock_firmware2.is_dir.return_value = True
        mock_firmware2.path = "/mock/prerelease/firmware-2.0.0.def456"

        mock_symlink = Mock()
        mock_symlink.name = "firmware-0.9.0.symlink"
        mock_symlink.is_symlink.return_value = True
        mock_symlink.is_dir.return_value = True
        mock_symlink.path = "/mock/prerelease/firmware-0.9.0.symlink"

        mock_file = Mock()
        mock_file.name = "firmware-0.8.0.txt"
        mock_file.is_symlink.return_value = False
        mock_file.is_dir.return_value = False
        mock_file.path = "/mock/prerelease/firmware-0.8.0.txt"

        mock_scandir.return_value.__enter__.return_value = [
            mock_firmware1,
            mock_firmware2,
            mock_symlink,
            mock_file,
        ]

        result = downloader.cleanup_superseded_prereleases("v2.0.0")

        assert result is True
        assert mock_rmtree.call_count == 2

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_keeps_prerelease_tags(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup retains prerelease-tagged releases in the keep set."""
        mock_exists.return_value = True
        downloader.get_releases = Mock(
            return_value=[
                Release(tag_name="v2.7.17.83c6161", prerelease=True),
                Release(tag_name="v2.7.16.a597230", prerelease=True),
            ]
        )

        entry_keep1 = Mock()
        entry_keep1.name = "v2.7.17.83c6161-alpha"
        entry_keep1.is_symlink.return_value = False
        entry_keep1.is_dir.return_value = True
        entry_keep1.path = "/mock/firmware/v2.7.17.83c6161-alpha"

        entry_keep2 = Mock()
        entry_keep2.name = "v2.7.16.a597230-alpha"
        entry_keep2.is_symlink.return_value = False
        entry_keep2.is_dir.return_value = True
        entry_keep2.path = "/mock/firmware/v2.7.16.a597230-alpha"

        entry_remove = Mock()
        entry_remove.name = "v2.7.15.567b8ea-alpha"
        entry_remove.is_symlink.return_value = False
        entry_remove.is_dir.return_value = True
        entry_remove.path = "/mock/firmware/v2.7.15.567b8ea-alpha"

        mock_scandir.return_value.__enter__.return_value = [
            entry_keep1,
            entry_keep2,
            entry_remove,
        ]

        downloader.cleanup_old_versions(keep_limit=2)

        mock_rmtree.assert_called_once_with("/mock/firmware/v2.7.15.567b8ea-alpha")

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_matches_channel_suffix_bases(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Ensure base tags are matched even when releases use channel suffixes."""
        mock_exists.return_value = True
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        firmware_dir = os.path.join(downloader.download_dir, FIRMWARE_DIR_NAME)
        entry_keep = Mock()
        entry_keep.name = "v1.0.0"
        entry_keep.is_symlink.return_value = False
        entry_keep.is_dir.return_value = True
        entry_keep.path = os.path.join(firmware_dir, "v1.0.0")

        entry_remove = Mock()
        entry_remove.name = "v0.9.0"
        entry_remove.is_symlink.return_value = False
        entry_remove.is_dir.return_value = True
        entry_remove.path = os.path.join(firmware_dir, "v0.9.0")

        mock_scandir.return_value.__enter__.return_value = [
            entry_keep,
            entry_remove,
        ]
        mock_scandir.return_value.__exit__.return_value = None

        downloader.get_releases = Mock(return_value=[Release(tag_name="v1.0.0-beta")])

        downloader.cleanup_old_versions(keep_limit=1)

        mock_rmtree.assert_called_once_with(entry_remove.path)

    def test_get_prerelease_tracking_file(self, downloader):
        """Test prerelease tracking file path generation."""
        path = downloader.get_prerelease_tracking_file()
        expected_path = downloader.cache_manager.get_cache_file_path(
            downloader.latest_prerelease_file
        )
        assert path == expected_path

    def test_should_download_prerelease_enabled(self, downloader):
        """Test prerelease download decision with prereleases enabled."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True

        result = downloader.should_download_prerelease("v1.0.0-beta")

        assert result is True

    def test_should_download_prerelease_disabled(self, downloader):
        """Test prerelease download decision with prereleases disabled."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = False

        result = downloader.should_download_prerelease("v1.0.0-beta")

        assert result is False

    @patch("datetime.datetime")
    def test_update_prerelease_tracking(self, mock_datetime, downloader):
        """Test updating prerelease tracking."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        downloader.cache_manager.atomic_write_json = Mock(return_value=True)

        result = downloader.update_prerelease_tracking("v1.0.0-beta")

        assert result is True

    def test_manage_prerelease_tracking_files(self, downloader):
        """Test prerelease tracking file management."""
        # Mock dependencies
        with (
            patch.object(downloader, "get_releases", return_value=[]),
            patch("os.path.exists", return_value=True),
            patch(
                "os.scandir",
                return_value=Mock(
                    __enter__=Mock(return_value=[]), __exit__=Mock(return_value=None)
                ),
            ),
            patch(
                "fetchtastic.download.files._atomic_write",
                return_value=None,
            ),  # Prevent temp file creation
            patch("os.remove"),
        ):
            downloader.manage_prerelease_tracking_files()

            # Method should complete without error
            # Note: temp file removal from atomic_write is expected

    @pytest.mark.unit
    @pytest.mark.core_downloads
    def test_download_repo_prerelease_firmware_success(self, downloader):
        """Test repo prerelease firmware download method exists and returns proper types."""
        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
            patch.object(
                downloader.cache_manager, "get_repo_directories", return_value=[]
            ),
        ):
            results, failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v1.0.0")
            )

        # Should return proper tuple structure
        assert isinstance(results, list)
        assert isinstance(failed, list)
        assert latest is None or isinstance(latest, str)
        assert summary is None or isinstance(summary, dict)

    @pytest.mark.unit
    @pytest.mark.core_downloads
    def test_download_repo_prerelease_firmware_missing_directory(self, downloader):
        active_dir = "firmware-2.7.18.99d9191"
        history_entries = [
            {"identifier": "99d9191", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[],
            ),
            patch.object(
                downloader, "_download_prerelease_assets", return_value=([], [], False)
            ),
        ):
            results, failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.17.9058cce")
            )

        assert results == []
        assert failed == []
        assert latest == active_dir
        assert summary is not None
        assert summary["history_entries"] == history_entries

    @pytest.mark.unit
    @patch("os.path.exists")
    def test_cleanup_old_versions_skips_when_no_releases_with_keep_last_beta(
        self, mock_exists, downloader
    ):
        """Skip cleanup when keep_last_beta is enabled but no releases are available."""
        mock_exists.return_value = True
        downloader.get_releases = Mock(return_value=[])

        with patch("os.scandir") as mock_scandir:
            downloader.cleanup_old_versions(keep_limit=1, keep_last_beta=True)

        mock_scandir.assert_not_called()

    @pytest.mark.unit
    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_adds_beta_tag(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Most recent beta is kept when keep_last_beta is enabled."""
        mock_exists.return_value = True
        firmware_dir = os.path.join(downloader.download_dir, FIRMWARE_DIR_NAME)

        entry_stable = Mock()
        entry_stable.name = "v1.0.1"
        entry_stable.is_symlink.return_value = False
        entry_stable.is_dir.return_value = True
        entry_stable.path = os.path.join(firmware_dir, "v1.0.1")

        entry_beta = Mock()
        entry_beta.name = "v1.0.0-beta"
        entry_beta.is_symlink.return_value = False
        entry_beta.is_dir.return_value = True
        entry_beta.path = os.path.join(firmware_dir, "v1.0.0-beta")

        entry_old = Mock()
        entry_old.name = "v0.9.0"
        entry_old.is_symlink.return_value = False
        entry_old.is_dir.return_value = True
        entry_old.path = os.path.join(firmware_dir, "v0.9.0")

        mock_scandir.return_value.__enter__.return_value = [
            entry_stable,
            entry_beta,
            entry_old,
        ]
        mock_scandir.return_value.__exit__.return_value = None

        stable = Release(tag_name="v1.0.1", prerelease=False)
        beta = Release(tag_name="v1.0.0-beta", prerelease=False)
        downloader.release_history_manager.get_release_channel = Mock(
            side_effect=lambda release: (
                "beta" if release.tag_name == "v1.0.0-beta" else ""
            )
        )
        downloader.get_releases = Mock(return_value=[stable, beta])

        downloader.cleanup_old_versions(
            keep_limit=1,
            keep_last_beta=True,
            cached_releases=[stable, beta],
        )

        mock_rmtree.assert_called_once_with(entry_old.path)

    @pytest.mark.unit
    @patch("os.path.exists")
    @patch("os.scandir")
    def test_cleanup_old_versions_warns_on_unsafe_beta_tag(
        self, mock_scandir, mock_exists, downloader
    ):
        """Unsafe beta tag emits a warning during cleanup."""
        mock_exists.return_value = True
        mock_scandir.return_value.__enter__.return_value = []
        mock_scandir.return_value.__exit__.return_value = None

        stable = Release(tag_name="v1.0.1", prerelease=False)
        beta = Release(tag_name="v1.0.0-beta", prerelease=False)
        downloader.release_history_manager.get_release_channel = Mock(
            side_effect=lambda release: (
                "beta" if release.tag_name == "v1.0.0-beta" else ""
            )
        )

        def _sanitize(tag, _label):
            if tag == "v1.0.0-beta":
                raise ValueError("unsafe")
            return tag

        downloader._sanitize_required = Mock(side_effect=_sanitize)
        downloader.get_releases = Mock(return_value=[stable, beta])

        with patch("fetchtastic.download.firmware.logger.warning") as mock_warning:
            downloader.cleanup_old_versions(
                keep_limit=1,
                keep_last_beta=True,
                cached_releases=[stable, beta],
            )

        mock_warning.assert_any_call(
            "Skipping unsafe beta release tag during cleanup: %s",
            beta.tag_name,
        )

    @pytest.mark.unit
    @patch("os.path.exists")
    @patch("os.scandir")
    def test_cleanup_old_versions_skips_symlinks(
        self, mock_scandir, mock_exists, downloader
    ):
        """Symlinks in the firmware directory are skipped during cleanup."""
        mock_exists.return_value = True
        downloader.config["FILTER_REVOKED_RELEASES"] = False
        entry_symlink = Mock()
        entry_symlink.name = "v1.0.0"
        entry_symlink.is_symlink.return_value = True
        entry_symlink.is_dir.return_value = False
        entry_symlink.path = "/mock/firmware/v1.0.0"

        mock_scandir.return_value.__enter__.return_value = [entry_symlink]
        mock_scandir.return_value.__exit__.return_value = None

        with patch("fetchtastic.download.firmware.logger.warning") as mock_warning:
            downloader.cleanup_old_versions(keep_limit=0, cached_releases=[])

        mock_warning.assert_any_call(
            "Skipping symlink in firmware directory during cleanup: %s",
            entry_symlink.name,
        )

    @patch("os.path.exists")
    @patch("os.scandir")
    def test_cleanup_old_versions_preserves_latest_pointer_symlink(
        self, mock_scandir, mock_exists, downloader
    ):
        """Firmware cleanup silently skips managed latest symlink."""
        mock_exists.return_value = True
        downloader.config["FILTER_REVOKED_RELEASES"] = False
        mock_latest = Mock()
        mock_latest.name = LATEST_POINTER_NAME
        mock_latest.is_symlink.return_value = True
        mock_latest.is_dir.return_value = False
        mock_latest.path = "/mock/firmware/latest"

        mock_scandir.return_value.__enter__.return_value = [mock_latest]
        mock_scandir.return_value.__exit__.return_value = None

        with (
            patch("fetchtastic.download.firmware.logger.warning") as mock_warning,
            patch("fetchtastic.download.firmware.logger.debug") as mock_debug,
        ):
            downloader.cleanup_old_versions(keep_limit=0, cached_releases=[])

        mock_warning.assert_not_called()
        debug_messages = [c[0][0] for c in mock_debug.call_args_list]
        assert not any(
            LATEST_POINTER_NAME in msg and "pointer" in msg.lower()
            for msg in debug_messages
        )

    @patch("os.path.exists")
    @patch("os.scandir")
    def test_cleanup_old_versions_preserves_latest_pointer_non_symlink(
        self, mock_scandir, mock_exists, downloader
    ):
        """Firmware cleanup preserves non-symlink latest entry and logs a debug message."""
        mock_exists.return_value = True
        downloader.config["FILTER_REVOKED_RELEASES"] = False
        mock_latest = Mock()
        mock_latest.name = LATEST_POINTER_NAME
        mock_latest.is_symlink.return_value = False
        mock_latest.is_dir.return_value = True
        mock_latest.path = "/mock/firmware/latest"

        mock_scandir.return_value.__enter__.return_value = [mock_latest]
        mock_scandir.return_value.__exit__.return_value = None

        with patch("fetchtastic.download.firmware.logger.debug") as mock_debug:
            downloader.cleanup_old_versions(keep_limit=0, cached_releases=[])

        mock_debug.assert_any_call(
            "Preserving non-symlink latest entry that may block latest pointer creation: %s",
            mock_latest.path,
        )

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_latest_dir_does_not_trip_mismatch_guard(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """A non-symlink latest directory is ignored by the mismatch guard and preserved."""
        mock_exists.return_value = True
        downloader.config["FILTER_REVOKED_RELEASES"] = False
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        mock_latest = Mock()
        mock_latest.name = LATEST_POINTER_NAME
        mock_latest.is_symlink.return_value = False
        mock_latest.is_dir.return_value = True
        mock_latest.path = "/mock/firmware/latest"

        mock_keep = Mock()
        mock_keep.name = "v2.0.0"
        mock_keep.is_symlink.return_value = False
        mock_keep.is_dir.return_value = True
        mock_keep.path = "/mock/firmware/v2.0.0"

        mock_scandir.return_value.__enter__.return_value = [mock_latest, mock_keep]
        mock_scandir.return_value.__exit__.return_value = None
        downloader.get_releases = Mock(return_value=[Release(tag_name="v2.0.0")])

        with (
            patch("fetchtastic.download.firmware.logger.warning") as mock_warning,
            patch("fetchtastic.download.firmware.logger.debug") as mock_debug,
        ):
            downloader.cleanup_old_versions(keep_limit=1)

        assert not any(
            call.args
            == (
                "Skipping firmware cleanup: keep set does not match existing directories.",
            )
            for call in mock_warning.call_args_list
        )
        mock_debug.assert_any_call(
            "Preserving non-symlink latest entry that may block latest pointer creation: %s",
            mock_latest.path,
        )
        mock_rmtree.assert_not_called()

    def test_update_latest_pointer_for_release_coerces_false_string(self, downloader):
        downloader.config["CREATE_LATEST_SYMLINKS"] = "false"
        release = Release(tag_name="v2.0.0", prerelease=False)

        with patch(
            "fetchtastic.download.firmware.update_latest_pointer"
        ) as mock_latest:
            assert downloader.update_latest_pointer_for_release(release) is False

        mock_latest.assert_not_called()

    @pytest.mark.unit
    def test_get_expiry_timestamp_format(self, downloader):
        """Expiry timestamp is returned as an ISO 8601 UTC string."""
        expiry = downloader._get_expiry_timestamp()
        assert "T" in expiry
        assert "+00:00" in expiry or "Z" in expiry

    def test_handle_prereleases_with_repo_download(self, downloader):
        """Test prerelease handling with repo downloads."""
        releases = [
            Mock(tag_name="v1.0.0-beta", prerelease=True, published_at="2023-01-01"),
        ]
        result = downloader.handle_prereleases(releases)

        # Firmware GitHub prerelease flags are treated as stable.
        assert result == []

    def test_update_release_history_logs_summary(self, downloader):
        """Firmware history updates should emit channel/status summaries."""
        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )
        downloader.release_history_manager.log_release_channel_summary = Mock()
        downloader.release_history_manager.log_release_status_summary = Mock()
        downloader.release_history_manager.log_duplicate_base_versions = Mock()

        history = downloader.update_release_history([Release(tag_name="v1.0.0")])

        assert history == {"entries": {}}
        downloader.release_history_manager.log_release_channel_summary.assert_called_once()
        downloader.release_history_manager.log_release_status_summary.assert_called_once()
        downloader.release_history_manager.log_duplicate_base_versions.assert_called_once()

    def test_cleanup_file_delegates_to_file_ops(self, downloader):
        """cleanup_file should call the file operations cleanup helper."""
        downloader.file_operations.cleanup_file = Mock(return_value=True)

        assert downloader.cleanup_file("/tmp/file.bin") is True
        downloader.file_operations.cleanup_file.assert_called_once_with("/tmp/file.bin")

    def test_write_release_notes_existing_file(self, tmp_path):
        """Existing release notes should be returned without rewriting."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release_dir = tmp_path / "downloads" / "firmware" / "v1.2.0"
        release_dir.mkdir(parents=True)
        notes_path = release_dir / "release_notes-v1.2.0.md"
        notes_path.write_text("Existing notes", encoding="utf-8")

        result = downloader._write_release_notes(
            release_dir=str(release_dir),
            release_tag="v1.2.0",
            body="New notes",
            base_dir=str(tmp_path / "downloads" / "firmware"),
        )

        assert result == str(notes_path)

    def test_write_release_notes_existing_symlink(self, tmp_path):
        """Symlinked release notes should be rejected."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release_dir = tmp_path / "downloads" / "firmware" / "v1.2.1"
        release_dir.mkdir(parents=True)
        notes_path = release_dir / "release_notes-v1.2.1.md"
        target_path = tmp_path / "target.md"
        target_path.write_text("Target", encoding="utf-8")

        try:
            os.symlink(target_path, notes_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        with patch.object(log_utils.logger, "warning") as mock_warning:
            result = downloader._write_release_notes(
                release_dir=str(release_dir),
                release_tag="v1.2.1",
                body="New notes",
                base_dir=str(tmp_path / "downloads" / "firmware"),
            )

        assert result is None
        assert mock_warning.called

    def test_write_release_notes_path_escape(self, tmp_path):
        """Release notes should not be written outside the base directory."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        base_dir = tmp_path / "downloads" / "firmware"
        release_dir = base_dir / ".." / "escape"

        result = downloader._write_release_notes(
            release_dir=str(release_dir),
            release_tag="v1.3.0",
            body="Notes",
            base_dir=str(base_dir),
        )

        assert result is None

    def test_write_release_notes_empty_after_sanitize(self, tmp_path):
        """Empty notes after sanitization should skip writing."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        with patch(
            "fetchtastic.download.base.strip_unwanted_chars", return_value="   "
        ):
            result = downloader._write_release_notes(
                release_dir=str(tmp_path / "downloads" / "firmware" / "v1.4.0"),
                release_tag="v1.4.0",
                body="Notes",
                base_dir=str(tmp_path / "downloads" / "firmware"),
            )

        assert result is None

    def test_write_release_notes_atomic_write_failure(self, tmp_path):
        """Failed atomic writes should return None."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        downloader.cache_manager.atomic_write_text = Mock(return_value=False)

        result = downloader._write_release_notes(
            release_dir=str(tmp_path / "downloads" / "firmware" / "v1.5.0"),
            release_tag="v1.5.0",
            body="Notes",
            base_dir=str(tmp_path / "downloads" / "firmware"),
        )

        assert result is None

    def test_get_release_storage_tag_rename_failure(self, tmp_path):
        """Rename failures should fall back to the existing directory tag."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        firmware_dir = tmp_path / "downloads" / "firmware"
        firmware_dir.mkdir(parents=True)
        alternate_dir = firmware_dir / "v1.0.0-revoked"
        alternate_dir.mkdir()

        release = Release(tag_name="v1.0.0", prerelease=False)

        with patch("os.rename", side_effect=OSError("boom")):
            storage_tag = downloader._get_release_storage_tag(release)

        assert storage_tag == "v1.0.0-revoked"

    def test_get_release_storage_tag_multiple_existing(self, tmp_path):
        """Multiple candidate directories should return the first match."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)
        firmware_dir = tmp_path / "downloads" / "firmware"
        firmware_dir.mkdir(parents=True)
        (firmware_dir / "v1.0.1-alpha").mkdir()
        (firmware_dir / "v1.0.1-beta").mkdir()

        release = Release(tag_name="v1.0.1", prerelease=False)
        storage_tag = downloader._get_release_storage_tag(release)

        assert storage_tag in {"v1.0.1-alpha", "v1.0.1-beta"}

    def test_get_storage_tag_candidates_with_suffixes_disabled(self, downloader):
        """Suffix candidates should remain discoverable when ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES is False."""
        # Even with suffixes disabled, discovery should include legacy channel directories.
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False
        release = Release(tag_name="v1.0.2", prerelease=False)
        candidates = downloader._get_storage_tag_candidates(release, "v1.0.2")

        # Standard suffixes (alpha, beta, rc) should remain discoverable.
        assert "v1.0.2-alpha" in candidates
        assert "v1.0.2-beta" in candidates
        assert "v1.0.2-rc" in candidates
        assert "v1.0.2-revoked" in candidates

    def test_is_release_complete_unsafe_tag(self, downloader):
        """Unsafe tags should return False during completeness checks."""
        release = Release(tag_name="../v1.0.0", prerelease=False, assets=[])

        assert downloader.is_release_complete(release) is False

    def test_is_release_complete_missing_dir(self, downloader):
        """Missing release directories should return False."""
        release = Release(tag_name="v9.9.9", prerelease=False, assets=[])

        assert downloader.is_release_complete(release) is False

    def test_is_release_complete_missing_asset_file(self, downloader, tmp_path, mocker):
        """Missing asset files in an existing release directory should return False."""
        # Ensure the downloader points at a real temporary directory.
        downloader.download_dir = str(tmp_path)

        # Use a simple release tag to avoid channel suffix handling in this test.
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        release = Release(tag_name="v1.2.3", prerelease=False)
        asset = Asset(
            name="firmware-rak4631-1.2.3.bin",
            download_url="https://example.com/fw.bin",
            size=123,
        )
        release.assets.append(asset)

        # Create the release directory but intentionally omit the asset file.
        version_dir = tmp_path / "firmware" / "v1.2.3"
        version_dir.mkdir(parents=True)

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        assert downloader.is_release_complete(release) is False
        assert mock_logger.debug.called

    def test_is_release_complete_zip_uses_hash_baseline(
        self, downloader, tmp_path, mocker
    ):
        """ZIP checks should use hash verification directly when a baseline hash exists."""
        downloader.download_dir = str(tmp_path)
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        release = Release(tag_name="v1.2.3", prerelease=False)
        asset_path = tmp_path / "firmware" / "v1.2.3" / "firmware-rak4631.zip"
        asset_path.parent.mkdir(parents=True)
        asset_path.write_bytes(b"zip bytes")
        release.assets.append(
            Asset(
                name="firmware-rak4631.zip",
                download_url="https://example.com/fw.zip",
                size=asset_path.stat().st_size,
            )
        )

        mocker.patch(
            "fetchtastic.download.firmware.load_file_hash", return_value="hash"
        )
        verify_mock = mocker.patch.object(downloader, "verify", return_value=True)
        zip_file_ctor = mocker.patch("fetchtastic.download.firmware.zipfile.ZipFile")

        assert downloader.is_release_complete(release) is True
        verify_mock.assert_called_once_with(str(asset_path))
        zip_file_ctor.assert_not_called()

    def test_is_release_complete_zip_without_hash_runs_zip_and_verify(
        self, downloader, tmp_path, mocker
    ):
        """ZIP checks without a baseline hash should validate archive and then verify hash."""
        downloader.download_dir = str(tmp_path)
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        release = Release(tag_name="v1.2.4", prerelease=False)
        version_dir = tmp_path / "firmware" / "v1.2.4"
        version_dir.mkdir(parents=True)
        asset_path = version_dir / "firmware-rak4631.zip"
        with zipfile.ZipFile(asset_path, "w") as zf:
            zf.writestr("content.txt", "ok")

        release.assets.append(
            Asset(
                name="firmware-rak4631.zip",
                download_url="https://example.com/fw.zip",
                size=asset_path.stat().st_size,
            )
        )

        mocker.patch("fetchtastic.download.firmware.load_file_hash", return_value=None)
        verify_mock = mocker.patch.object(downloader, "verify", return_value=True)

        assert downloader.is_release_complete(release) is True
        verify_mock.assert_called_once_with(str(asset_path))

    def test_extract_firmware_missing_zip(self, downloader):
        """Missing ZIP files should return a validation error result."""
        release = Release(tag_name="v1.0.0", prerelease=False)
        asset = Asset(
            name="firmware-test.zip",
            download_url="https://example.com/fw.zip",
            size=100,
        )

        result = downloader.extract_firmware(release, asset, ["*.bin"], [])

        assert result.success is False
        assert result.error_type == "validation_error"

    def test_cleanup_superseded_prereleases_error(self, downloader):
        """Superseded prerelease cleanup should handle version errors."""
        with patch.object(VersionManager, "get_release_tuple", side_effect=ValueError):
            assert downloader.cleanup_superseded_prereleases("v1.2.3") is False

    @pytest.mark.unit
    @pytest.mark.core_downloads
    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_keeps_most_recent_beta(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test that KEEP_LAST_BETA ensures most recent beta is kept."""
        mock_exists.return_value = True
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        # Create mock directory entries: v3.0.0 (stable, newest), v2.0.0 (beta), v1.9.0 (alpha, oldest)
        mock_v3 = Mock()
        mock_v3.name = "v3.0.0"
        mock_v3.is_symlink.return_value = False
        mock_v3.is_dir.return_value = True
        mock_v3.path = "/mock/firmware/v3.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v2.0.0"

        mock_v1 = Mock()
        mock_v1.name = "v1.9.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v1.9.0"

        mock_scandir.return_value.__enter__.return_value = [mock_v3, mock_v2, mock_v1]

        # Mock releases with proper limit behavior (matching the fix in firmware.py)
        all_releases = [
            Release(
                tag_name="v3.0.0",
                published_at="2025-01-15T00:00:00Z",
                name="v3.0.0",
            ),
            Release(
                tag_name="v2.0.0",
                published_at="2025-01-10T00:00:00Z",
                name="v2.0.0 beta",
            ),
            Release(
                tag_name="v1.9.0",
                published_at="2025-01-05T00:00:00Z",
                name="v1.9.0 alpha",
            ),
        ]
        downloader.get_releases = Mock(
            side_effect=lambda limit: all_releases[:limit] if limit > 0 else []
        )

        downloader.release_history_manager.get_release_channel = Mock(
            side_effect=lambda r: (
                "stable"
                if r.tag_name == "v3.0.0"
                else "beta" if r.tag_name == "v2.0.0" else "alpha"
            )
        )
        downloader._sanitize_required = Mock(side_effect=lambda tag, _: tag)

        # With KEEP_LAST_BETA=True and keep_limit=1:
        # - v3.0.0 (stable, newest) should be kept (within keep_limit)
        # - v2.0.0 (beta, most recent beta) should be kept (KEEP_LAST_BETA)
        # - v1.9.0 (alpha, oldest) should be removed
        downloader.cleanup_old_versions(keep_limit=1, keep_last_beta=True)

        # Only v1.9.0 should be removed
        assert mock_rmtree.call_count == 1
        mock_rmtree.assert_called_once_with("/mock/firmware/v1.9.0")
        expected_limit = self._expected_cleanup_fetch_limit(
            keep_limit=1, keep_last_beta=True
        )
        downloader.get_releases.assert_called_once_with(limit=expected_limit)

    @pytest.mark.unit
    @pytest.mark.core_downloads
    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_without_keep_last_beta(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup without KEEP_LAST_BETA uses normal logic."""
        mock_exists.return_value = True
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False

        mock_v1 = Mock()
        mock_v1.name = "v2.0.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/firmware/v2.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v1.9.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/firmware/v1.9.0"

        mock_scandir.return_value.__enter__.return_value = [mock_v1, mock_v2]

        # Mock releases with proper limit behavior
        all_releases = [
            Release(
                tag_name="v2.0.0",
                published_at="2025-01-10T00:00:00Z",
                name="v2.0.0",
            ),
        ]
        downloader.get_releases = Mock(
            side_effect=lambda limit: all_releases[:limit] if limit > 0 else []
        )

        downloader._sanitize_required = Mock(return_value="v2.0.0")

        # With KEEP_LAST_BETA=False (default), only v2.0.0 should be kept (keep_limit=1)
        downloader.cleanup_old_versions(keep_limit=1, keep_last_beta=False)

        # v1.9.0 should be removed
        assert mock_rmtree.call_count == 1
        mock_rmtree.assert_called_once_with("/mock/firmware/v1.9.0")
        expected_limit = self._expected_cleanup_fetch_limit(
            keep_limit=1, keep_last_beta=False
        )
        downloader.get_releases.assert_called_once_with(limit=expected_limit)

    def test_download_firmware_exception_uses_firmware_dir(self, downloader, tmp_path):
        """Ensure validation errors fall back to the firmware directory."""
        release = Release(tag_name="v2.0.0", prerelease=False)
        asset = Asset(
            name="firmware-test.bin",
            download_url="https://example.com/fw.bin",
            size=4096,
        )
        downloader.download_dir = str(tmp_path)

        with patch.object(
            downloader,
            "get_target_path_for_release",
            side_effect=ValueError("bad path"),
        ):
            result = downloader.download_firmware(release, asset)

        assert result.success is False
        assert result.error_type == "validation_error"
        expected_path = Path(tmp_path) / FIRMWARE_DIR_NAME
        assert Path(result.file_path) == expected_path


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_latest_version_logs_invalid_tracking_version():
    """Ensure invalid tracking versions produce a debug log and are returned."""
    vm = VersionManager()
    vm.read_version_tracking_file = Mock(return_value={"version": "bad-version!"})
    vm.compare_versions = Mock(return_value=1)

    with patch.object(log_utils.logger, "debug") as mock_debug:
        version = vm.get_latest_version_from_tracking_files(["dummy.json"], Mock())

    assert version == "bad-version!"
    assert mock_debug.called
    assert "does not match expected pattern" in mock_debug.call_args[0][0]


@pytest.mark.unit
@pytest.mark.core_downloads
class TestFirmwareUncoveredBranches:
    """Targeted tests for previously uncovered branches in firmware.py."""

    # Lines 138-142: Edge case in _filter_revoked_releases with string config values
    def test_filter_revoked_releases_string_values(
        self, mock_config, mock_cache_manager
    ):
        """Test that _filter_revoked_releases handles string config values."""
        # Test various string values
        for true_val in ["1", "true", "True", "yes", "YES", "y", "Y", "on", "ON"]:
            mock_config["FILTER_REVOKED_RELEASES"] = true_val
            dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)
            assert dl._filter_revoked_releases is True, f"Failed for {true_val}"

        for false_val in [
            "0",
            "false",
            "False",
            "no",
            "NO",
            "n",
            "N",
            "off",
            "OFF",
            "",
            "   ",
        ]:
            mock_config["FILTER_REVOKED_RELEASES"] = false_val
            dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)
            assert dl._filter_revoked_releases is False, f"Failed for {false_val}"

    # Lines 378-395: Manifest file handling - multiple channel directories
    def test_get_release_storage_tag_multiple_channel_dirs(self, tmp_path):
        """Test handling when multiple channel-suffixed directories exist."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        firmware_dir = tmp_path / "downloads" / "firmware"
        firmware_dir.mkdir(parents=True)

        # Create multiple channel directories
        (firmware_dir / "v1.0.0-alpha").mkdir()
        (firmware_dir / "v1.0.0-beta").mkdir()
        (firmware_dir / "v1.0.0-rc").mkdir()

        release = Release(tag_name="v1.0.0", prerelease=False)

        with patch.object(
            downloader.release_history_manager, "get_release_channel", return_value=""
        ):
            storage_tag = downloader._get_release_storage_tag(release)

        # Should return one of the existing channel directories
        assert storage_tag in ["v1.0.0-alpha", "v1.0.0-beta", "v1.0.0-rc"]

    # Lines 575-577: Verification failure cleanup
    def test_download_firmware_verification_failure_cleanup(self, downloader, tmp_path):
        """Test that verification failure triggers cleanup."""
        downloader.download_dir = str(tmp_path)

        release = Mock(spec=Release)
        release.tag_name = "v2.0.0"
        release.prerelease = False
        release.name = "v2.0.0"
        release.body = ""

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"
        asset.download_url = "https://example.com/firmware.zip"
        asset.size = 1000000

        # Mock download to succeed but verification to fail
        downloader.download = Mock(return_value=True)
        downloader.verify = Mock(return_value=False)
        downloader.cleanup_file = Mock(return_value=True)
        downloader.is_asset_complete = Mock(return_value=False)

        result = downloader.download_firmware(release, asset)

        assert result.success is False
        assert result.error_type == "validation_error"
        downloader.cleanup_file.assert_called_once()

    # Lines 866, 871, 874, 879-882: Asset filtering edge cases
    def test_is_release_complete_empty_asset_name(self, downloader, tmp_path, mocker):
        """Test is_release_complete with empty asset names."""
        downloader.download_dir = str(tmp_path)
        version_dir = tmp_path / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)

        # Create asset with empty/invalid name
        asset_empty = Asset(
            name="", download_url="https://example.com/fw.zip", size=100
        )
        asset_invalid = Asset(
            name="  ", download_url="https://example.com/fw2.zip", size=100
        )

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            assets=[asset_empty, asset_invalid],
        )

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        result = downloader.is_release_complete(release)

        # Should return False because no valid assets to check
        assert result is False
        assert mock_logger.debug.called

    # Lines 896-905: Asset filtering with file size mismatch
    def test_is_release_complete_size_mismatch(self, downloader, tmp_path):
        """Test is_release_complete when file sizes don't match."""
        downloader.download_dir = str(tmp_path)
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False
        version_dir = tmp_path / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)

        # Create a file with wrong size
        asset_file = version_dir / "firmware-rak4631.bin"
        asset_file.write_bytes(b"data")  # size = 4

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-rak4631.bin",
                    download_url="https://example.com/fw.bin",
                    size=100,  # Mismatched size
                )
            ],
        )

        result = downloader.is_release_complete(release)

        assert result is False

    # Lines 914-920, 926-927, 929-941: ZIP file handling edge cases
    def test_is_release_complete_zip_hash_verification_error(
        self, downloader, tmp_path, mocker
    ):
        """Test ZIP handling when hash verification raises OSError."""
        downloader.download_dir = str(tmp_path)
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False
        version_dir = tmp_path / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)

        # Create a valid zip file
        asset_path = version_dir / "firmware-rak4631.zip"
        with zipfile.ZipFile(asset_path, "w") as zf:
            zf.writestr("content.txt", "data")

        # Mock to have hash baseline but OSError on verification
        mocker.patch(
            "fetchtastic.download.firmware.load_file_hash", return_value="hash123"
        )
        downloader.verify = Mock(side_effect=OSError("IO error"))

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-rak4631.zip",
                    download_url="https://example.com/fw.zip",
                    size=asset_path.stat().st_size,
                )
            ],
        )

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        result = downloader.is_release_complete(release)

        assert result is False
        # Check that debug was called with the error message
        debug_calls = [
            call
            for call in mock_logger.debug.call_args_list
            if len(call.args) >= 2
            and isinstance(call.args[0], str)
            and "Error during hash verification" in call.args[0]
        ]
        assert len(debug_calls) > 0

    # Lines 1031: Extraction patterns validation failure
    def test_extract_firmware_invalid_patterns(self, downloader, tmp_path):
        """Test extract_firmware with invalid extraction patterns."""
        # Create the directory structure needed
        version_dir = tmp_path / "downloads" / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)
        # Create a dummy zip file
        zip_path = version_dir / "firmware-rak4631.zip"
        zip_path.write_bytes(b"dummy content")

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"

        downloader.file_operations.validate_extraction_patterns.return_value = False

        result = downloader.extract_firmware(release, asset, ["*.bin"], [])

        assert result.success is False
        assert result.error_type == "validation_error"
        assert "Invalid extraction patterns" in result.error_message

    # Lines 1043: Extraction not needed (files already exist)
    def test_extract_firmware_extraction_not_needed(self, downloader, tmp_path):
        """Test extract_firmware when extraction is not needed."""
        # Create the directory structure needed
        version_dir = tmp_path / "downloads" / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)
        # Create a dummy zip file
        zip_path = version_dir / "firmware-rak4631.zip"
        zip_path.write_bytes(b"dummy content")

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"

        downloader.file_operations.validate_extraction_patterns.return_value = True
        downloader.file_operations.check_extraction_needed.return_value = False

        result = downloader.extract_firmware(release, asset, ["*.bin"], [])

        assert result.success is True
        assert result.was_skipped is True
        assert result.extracted_files == []

    # Lines 1081-1083: Device manifest extraction error handling
    def test_extract_firmware_extraction_error(self, downloader, tmp_path):
        """Test extract_firmware error handling with zipfile.BadZipFile."""
        # Create the directory structure needed
        version_dir = tmp_path / "downloads" / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)
        # Create a dummy zip file (invalid)
        zip_path = version_dir / "firmware-rak4631.zip"
        zip_path.write_bytes(b"not a valid zip")

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"

        downloader.file_operations.validate_extraction_patterns.return_value = True
        downloader.file_operations.check_extraction_needed.return_value = True
        downloader.file_operations.extract_archive.side_effect = zipfile.BadZipFile(
            "Bad zip file"
        )

        result = downloader.extract_firmware(release, asset, ["*.bin"], [])

        assert result.success is False
        assert result.error_type == "extraction_error"

    # Lines 1125: Cleanup with missing firmware directory
    def test_cleanup_old_versions_missing_directory(self, downloader):
        """Test cleanup when firmware directory doesn't exist."""
        with patch("os.path.exists", return_value=False):
            # Should not raise an exception
            downloader.cleanup_old_versions(keep_limit=2)

    # Lines 1228->1251: Cleanup edge case - empty keep set with keep_limit > 0
    @patch("os.path.exists")
    def test_cleanup_old_versions_empty_keep_set(self, mock_exists, downloader, mocker):
        """Test cleanup when no safe tags are found to keep."""
        mock_exists.return_value = True

        # Mock get_releases to return releases
        downloader.get_releases = Mock(return_value=[Release(tag_name="v1.0.0")])

        # Mock collect_non_revoked_releases to return empty non_revoked list
        downloader.collect_non_revoked_releases = Mock(return_value=([], [], 8))

        # Mock sanitize_required to fail for all tags
        downloader._sanitize_required = Mock(side_effect=ValueError("unsafe"))

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        downloader.cleanup_old_versions(keep_limit=2)

        mock_logger.warning.assert_any_call(
            "Skipping firmware cleanup: no safe release tags found to keep."
        )

    # Lines 1310->1298, 1314: Release completion check with directory edge cases
    def test_is_release_complete_oserror_on_file_size(
        self, downloader, tmp_path, mocker
    ):
        """Test is_release_complete when file size check raises OSError."""
        downloader.download_dir = str(tmp_path)
        downloader.config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = False
        version_dir = tmp_path / "firmware" / "v1.0.0"
        version_dir.mkdir(parents=True)

        # Create a file
        asset_file = version_dir / "firmware-rak4631.bin"
        asset_file.write_bytes(b"data")

        # Patch os.path.getsize to raise OSError
        mocker.patch("os.path.getsize", side_effect=OSError("IO error"))

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            assets=[
                Asset(
                    name="firmware-rak4631.bin",
                    download_url="https://example.com/fw.bin",
                    size=4,
                )
            ],
        )

        result = downloader.is_release_complete(release)

        assert result is False

    # Lines 1322-1333: Cleanup error handling
    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_rmtree_error(
        self, mock_rmtree, mock_scandir, mock_exists, downloader, mocker
    ):
        """Test cleanup when rmtree raises OSError."""
        mock_exists.return_value = True
        mock_rmtree.side_effect = OSError("Permission denied")

        entry = Mock()
        entry.name = "v0.9.0"
        entry.is_symlink.return_value = False
        entry.is_dir.return_value = True
        entry.path = "/mock/firmware/v0.9.0"

        mock_scandir.return_value.__enter__.return_value = [entry]
        mock_scandir.return_value.__exit__.return_value = None

        downloader.get_releases = Mock(return_value=[Release(tag_name="v1.0.0")])
        downloader._sanitize_required = Mock(return_value="v1.0.0")
        downloader._get_comparable_base_tag = Mock(return_value="v1.0.0")
        downloader.collect_non_revoked_releases = Mock(
            return_value=([Release(tag_name="v1.0.0")], [Release(tag_name="v1.0.0")], 8)
        )

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        downloader.cleanup_old_versions(keep_limit=1)

        mock_logger.error.assert_any_call(
            "Error removing old firmware version %s: %s",
            "v0.9.0",
            ANY,
        )

    # Lines 1348-1349: Cleanup outer error handling
    @patch("os.path.exists")
    def test_cleanup_old_versions_outer_oserror(self, mock_exists, downloader, mocker):
        """Test cleanup outer OSError handling."""
        mock_exists.side_effect = OSError("Permission denied")

        mock_logger = mocker.patch("fetchtastic.download.firmware.logger")

        downloader.cleanup_old_versions(keep_limit=2)

        mock_logger.error.assert_called_with("Error during firmware cleanup: %s", ANY)

    # Lines 1517-1518: Prerelease directory naming edge case
    def test_download_prerelease_assets_unsafe_directory_name(
        self, downloader, tmp_path
    ):
        """Test _download_prerelease_assets with unsafe directory name."""
        downloader.download_dir = str(tmp_path)

        # Path traversal attempt
        result = downloader._download_prerelease_assets(
            "../etc/passwd",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=False,
        )

        assert result == ([], [], False)

    # Lines 1535, 1539-1542: Prerelease asset filtering with empty names
    def test_download_prerelease_assets_empty_name(self, downloader, tmp_path, mocker):
        """Test _download_prerelease_assets with empty file names."""
        downloader.download_dir = str(tmp_path)

        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {"type": "file", "name": "", "download_url": "url1", "size": 100},
                {"type": "file", "name": None, "download_url": "url2", "size": 200},
                {
                    "type": "file",
                    "name": "valid.zip",
                    "download_url": "url3",
                    "size": 300,
                },
            ]
        )

        mocker.patch(
            "fetchtastic.download.firmware.download_file_with_retry", return_value=True
        )
        mocker.patch(
            "fetchtastic.download.firmware.verify_file_integrity", return_value=False
        )

        successes, _failures, any_downloaded = downloader._download_prerelease_assets(
            "test-dir",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=True,
        )

        # Only valid.zip should be attempted
        assert len(successes) == 1
        assert any_downloaded is True

    # Lines 1557: Missing URL in prerelease asset
    def test_download_prerelease_assets_missing_url(self, downloader, tmp_path, mocker):
        """Test _download_prerelease_assets with missing download URL."""
        downloader.download_dir = str(tmp_path)

        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {
                    "type": "file",
                    "name": "no_url.zip",
                    "download_url": None,
                    "size": 100,
                },
                {
                    "type": "file",
                    "name": "no_browser_url.zip",
                    "browser_download_url": None,
                    "size": 200,
                },
            ]
        )

        successes, failures, any_downloaded = downloader._download_prerelease_assets(
            "test-dir",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=True,
        )

        # Both should be skipped due to missing URLs
        assert len(successes) == 0
        assert len(failures) == 0
        assert any_downloaded is False

    # Lines 1562-1587, 1593-1596: ZIP validation and file operations in prerelease
    @patch("fetchtastic.download.firmware.download_file_with_retry")
    def test_download_prerelease_assets_zip_without_hash_baseline(
        self, mock_download, downloader, tmp_path, mocker
    ):
        """Test ZIP validation in prerelease when no hash baseline exists."""
        downloader.download_dir = str(tmp_path)

        # Create existing ZIP file
        prerelease_dir = tmp_path / "firmware" / "prerelease" / "test-dir"
        prerelease_dir.mkdir(parents=True)
        zip_path = prerelease_dir / "existing.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("content.txt", "data")

        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {
                    "type": "file",
                    "name": "existing.zip",
                    "download_url": "https://example.com/existing.zip",
                    "size": zip_path.stat().st_size,
                }
            ]
        )

        # No hash baseline, but valid ZIP
        mocker.patch("fetchtastic.download.firmware.load_file_hash", return_value=None)
        mocker.patch(
            "fetchtastic.download.firmware.verify_file_integrity", return_value=True
        )

        _successes, _failures, _any_downloaded = downloader._download_prerelease_assets(
            "test-dir",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=False,
        )

        assert len(_successes) == 1
        assert _successes[0].was_skipped is True

    # Lines 1593-1596: Executable permissions for .sh files
    @patch("fetchtastic.download.firmware.download_file_with_retry")
    @patch("os.chmod")
    def test_download_prerelease_assets_sets_executable_permissions(
        self, mock_chmod, mock_download, downloader, tmp_path, mocker
    ):
        """Test that .sh files get executable permissions on Unix."""
        downloader.download_dir = str(tmp_path)

        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {
                    "type": "file",
                    "name": "script.sh",
                    "download_url": "https://example.com/script.sh",
                    "size": 100,
                }
            ]
        )

        mock_download.return_value = True
        mocker.patch("os.name", "posix")

        _successes, _failures, _any_downloaded = downloader._download_prerelease_assets(
            "test-dir",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=True,
        )

        assert len(_successes) == 1
        mock_chmod.assert_called_once()

    # Lines 1608-1631: Error handling in prerelease download
    @patch("fetchtastic.download.firmware.download_file_with_retry")
    def test_download_prerelease_assets_network_error(
        self, mock_download, downloader, tmp_path, mocker
    ):
        """Test network error handling in prerelease download."""
        downloader.download_dir = str(tmp_path)

        downloader.cache_manager.get_repo_contents = Mock(
            return_value=[
                {
                    "type": "file",
                    "name": "test.zip",
                    "download_url": "https://example.com/test.zip",
                    "size": 100,
                }
            ]
        )

        mock_download.side_effect = requests.RequestException("Network error")

        _successes, failures, _any_downloaded = downloader._download_prerelease_assets(
            "test-dir",
            selected_patterns=[],
            exclude_patterns=[],
            force_refresh=True,
        )

        assert len(failures) == 1
        assert failures[0].error_type == "network_error"
        assert failures[0].is_retryable is True

    # Lines 1707, 1721: Error handling in download_repo_prerelease_firmware
    def test_download_repo_prerelease_firmware_disabled(self, downloader):
        """Test download_repo_prerelease_firmware when prereleases are disabled."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = False
        downloader.config["CHECK_PRERELEASES"] = False

        result = downloader.download_repo_prerelease_firmware("v1.0.0")

        assert result == ([], [], None, None)

    def test_download_repo_prerelease_firmware_no_expected_version(self, downloader):
        """Test download_repo_prerelease_firmware when expected version can't be calculated."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True

        with patch.object(
            VersionManager,
            "calculate_expected_prerelease_version",
            return_value=None,
        ):
            result = downloader.download_repo_prerelease_firmware("invalid-version")

        assert result == ([], [], None, None)

    # Lines 1754-1758: Channel handling - non-list directory response
    def test_download_repo_prerelease_firmware_non_list_dirs(
        self, downloader, tmp_path
    ):
        """Test handling when get_repo_directories returns non-list."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value="not-a-list",  # Should be handled gracefully
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            # Should not raise
            result = downloader.download_repo_prerelease_firmware("v2.7.17.9058cce")

        assert isinstance(result, tuple)

    # Lines 1764-1777: Fallback prerelease directory scan error
    def test_download_repo_prerelease_firmware_fallback_scan_error(
        self, downloader, tmp_path
    ):
        """Test fallback scan error handling in download_repo_prerelease_firmware."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                side_effect=requests.RequestException("API error"),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            # Should handle the exception gracefully
            result = downloader.download_repo_prerelease_firmware("v2.7.17.9058cce")

        assert isinstance(result, tuple)

    # Lines 1786->1793: Prerelease directory not in repo
    def test_download_repo_prerelease_firmware_dir_not_in_repo(
        self, downloader, tmp_path
    ):
        """Test when active_dir is not in repo directories."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=["other-dir"],  # active_dir not present
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-test123", [{"identifier": "test123"}]),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.17.9058cce")

        # Should return with summary but no downloads
        assert result[0] == []  # successes
        assert result[1] == []  # failures
        assert result[2] is None  # active_dir
        assert result[3] is not None  # prerelease_summary

    def test_download_repo_prerelease_firmware_skips_missing_active_dirs(
        self, downloader, tmp_path
    ):
        """When latest active history dir is gone, use active dirs still in repo."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "identifier": "7be5426",
                "directory": "firmware-2.7.23.7be5426",
                "status": "active",
            },
            {
                "identifier": "2a858be",
                "directory": "firmware-2.7.23.2a858be",
                "status": "active",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=["firmware-2.7.23.7be5426"],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.2a858be", history_entries),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        assert result[2] == "firmware-2.7.23.7be5426"
        download_assets.assert_called_once()
        assert download_assets.call_args.args[0] == "firmware-2.7.23.7be5426"

    def test_download_repo_prerelease_firmware_backfills_all_existing_active_dirs(
        self, downloader, tmp_path
    ):
        """Download every active history prerelease directory that still exists upstream."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "identifier": "2a858be",
                "directory": "firmware-2.7.23.2a858be",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "identifier": "7be5426",
                "directory": "firmware-2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.2a858be",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.2a858be", history_entries),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # After deterministic sorting, newest by dir string tiebreaker
        assert result[2] == "firmware-2.7.23.7be5426"
        assert [call.args[0] for call in download_assets.call_args_list] == [
            "firmware-2.7.23.2a858be",
            "firmware-2.7.23.7be5426",
        ]

    def test_download_repo_prerelease_firmware_syncs_existing_repo_dirs(
        self, downloader, tmp_path
    ):
        """Backfill matching prerelease dirs that still exist even if history missed them."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "identifier": "2.7.23.c0e52e6",
                "directory": "firmware-2.7.23.c0e52e6",
                "status": "deleted",
            },
            {
                "identifier": "2.7.23.7be5426",
                "directory": "firmware-2.7.23.7be5426",
                "status": "active",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.c0e52e6",
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.2a858be",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.7be5426", history_entries),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # After deterministic sorting, newest by dir string tiebreaker
        assert result[2] == "firmware-2.7.23.7be5426"
        assert [call.args[0] for call in download_assets.call_args_list] == [
            "firmware-2.7.23.2a858be",
            "firmware-2.7.23.7be5426",
        ]

    def test_download_repo_prerelease_firmware_fallback_downloads_all_existing_dirs(
        self, downloader, tmp_path
    ):
        """Fallback repo scanning should download every matching available prerelease dir."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.2a858be",
                    "firmware-2.7.24.bad9999",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # After deterministic sorting, the newest (by directory string tiebreaker) is returned
        assert result[2] == "firmware-2.7.23.7be5426"
        assert [call.args[0] for call in download_assets.call_args_list] == [
            "firmware-2.7.23.2a858be",
            "firmware-2.7.23.7be5426",
        ]

    # Lines 1797-1835: Release notes logging
    def test_log_prerelease_summary(self, downloader):
        """Test log_prerelease_summary with various entry statuses."""
        history_entries = [
            {"identifier": "abc123", "status": "active"},
            {"identifier": "def456", "status": "active"},
            {"identifier": "ghi789", "status": "deleted"},
            {"identifier": "", "status": "active"},  # Empty identifier
        ]

        with patch("fetchtastic.download.firmware.logger") as mock_logger:
            downloader.log_prerelease_summary(history_entries, "v2.7.16", "v2.7.17")

        # Should log summary info with proper counts
        # Total entries with identifier = 3, deleted = 1, active = 2
        info_calls = [call for call in mock_logger.info.call_args_list]
        # Check that at least one call contains the expected format
        summary_found = False
        for call in info_calls:
            args = call[0] if call[0] else call[1].get("args", ())
            if (
                len(args) >= 4
                and isinstance(args[0], str)
                and "Prereleases since" in args[0]
            ):
                summary_found = True
                break
        assert (
            summary_found
        ), f"Expected 'Prereleases since' log call not found in {info_calls}"

    # Lines 1860-1906: Release notes detailed logging
    def test_log_prerelease_summary_detailed(self, downloader):
        """Test detailed prerelease logging with all statuses."""
        history_entries = [
            {"identifier": "latest123", "status": "active"},
            {"identifier": "old456", "status": "deleted"},
            {"identifier": "current789", "status": "active"},
        ]

        with patch("fetchtastic.download.firmware.logger") as mock_logger:
            downloader.log_prerelease_summary(history_entries, "v2.7.16", "v2.7.17")

        # Should log the detailed list header
        mock_logger.info.assert_any_call("Prerelease commits for %s:", "v2.7.17")

    # Lines 2007-2023: Tracking file read error handling
    def test_should_download_prerelease_tracking_read_error(
        self, downloader, tmp_path, mocker
    ):
        """Test should_download_prerelease when tracking file read fails."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        tracking_file = tmp_path / "prerelease_tracking.json"
        tracking_file.write_text("invalid json")

        with (
            patch.object(
                downloader.cache_manager,
                "get_cache_file_path",
                return_value=str(tracking_file),
            ),
            patch.object(
                downloader.cache_manager,
                "read_json",
                side_effect=ValueError("Invalid JSON"),
            ),
        ):
            result = downloader.should_download_prerelease("v2.0.0")

        assert result is True  # Should default to download on error

    # Lines 2046-2051, 2059-2077: Version tracking file handling
    @patch("os.scandir")
    def test_manage_prerelease_tracking_files_not_found_error(
        self, mock_scandir, downloader
    ):
        """Test FileNotFoundError handling in manage_prerelease_tracking_files."""
        mock_scandir.side_effect = FileNotFoundError("Directory not found")

        # Should not raise
        downloader.manage_prerelease_tracking_files()

    def test_manage_prerelease_tracking_files_read_error(
        self, downloader, tmp_path, mocker
    ):
        """Test tracking file read error handling."""
        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir(parents=True)

        # Create a tracking file with proper prefix
        tracking_file = tracking_dir / "prerelease_test.json"
        tracking_file.write_text(
            '{"latest_version": "v1.0.0", "base_version": "v1.0.0"}'
        )

        # Create a mock entry that looks like a DirEntry
        class MockEntry:
            def __init__(self, name, path):
                self.name = name
                self.path = path

        mock_entry = MockEntry("prerelease_test.json", str(tracking_file))

        with (
            patch.object(
                downloader,
                "get_prerelease_tracking_file",
                return_value=str(tracking_file),
            ),
            patch("os.scandir") as mock_scandir,
            patch.object(
                downloader.cache_manager,
                "read_json",
                side_effect=OSError("Read error"),
            ),
            patch("fetchtastic.download.firmware.logger") as mock_logger,
        ):
            mock_scandir.return_value.__enter__ = Mock(return_value=iter([mock_entry]))
            mock_scandir.return_value.__exit__ = Mock(return_value=None)

            downloader.manage_prerelease_tracking_files()

        # Verify debug log was called about read error
        debug_calls = [
            call
            for call in mock_logger.debug.call_args_list
            if len(call.args) >= 2
            and isinstance(call.args[0], str)
            and "read error" in call.args[0].lower()
        ]
        assert len(debug_calls) > 0

    # Lines 2117, 2123: Comparison edge cases in cleanup_superseded_prereleases
    def test_cleanup_superseded_prereleases_empty_tag(self, downloader):
        """Test cleanup_superseded_prereleases with empty/whitespace tag."""
        result = downloader.cleanup_superseded_prereleases("v")
        assert result is False

        result = downloader.cleanup_superseded_prereleases("   ")
        assert result is False

    def test_cleanup_superseded_prereleases_invalid_version_tuple(self, downloader):
        """Test when version tuple can't be extracted."""
        with patch.object(
            VersionManager,
            "get_release_tuple",
            return_value=None,
        ):
            result = downloader.cleanup_superseded_prereleases("v1.2.3")

        assert result is False

    # Lines 2144->2135, 2148->2135, 2150->2135: Channel suffix logic in cleanup
    @patch("os.scandir")
    def test_cleanup_superseded_prereleases_skips_non_firmware_prefix(
        self, mock_scandir, downloader
    ):
        """Test that directories without firmware- prefix are skipped."""
        mock_entry = Mock()
        mock_entry.name = "not-firmware-prefix"
        mock_entry.is_symlink.return_value = False
        mock_entry.is_dir.return_value = True
        mock_entry.path = "/mock/prerelease/not-firmware-prefix"

        mock_scandir.return_value.__enter__.return_value = [mock_entry]

        with patch.object(
            VersionManager,
            "get_release_tuple",
            return_value=(2, 0, 0),
        ):
            result = downloader.cleanup_superseded_prereleases("v2.0.0")

        assert result is False  # Nothing was removed

    # Lines 2166-2174: Version extraction edge cases
    @patch("os.scandir")
    def test_cleanup_superseded_prereleases_value_error_on_version(
        self, mock_scandir, downloader
    ):
        """Test ValueError handling when extracting version from directory name."""
        mock_entry = Mock()
        mock_entry.name = "firmware-invalid.version.name"
        mock_entry.is_symlink.return_value = False
        mock_entry.is_dir.return_value = True
        mock_entry.path = "/mock/prerelease/firmware-invalid.version.name"

        mock_scandir.return_value.__enter__.return_value = [mock_entry]

        with patch.object(
            VersionManager,
            "get_release_tuple",
            return_value=(2, 0, 0),
        ):
            result = downloader.cleanup_superseded_prereleases("v2.0.0")

        # Should not raise, and should return False since nothing was removed
        assert result is False

    @patch("os.scandir")
    def test_cleanup_superseded_prereleases_oserror_on_removal(
        self, mock_scandir, downloader, mocker
    ):
        """Test OSError handling when removing superseded prerelease."""
        mock_entry = Mock()
        mock_entry.name = "firmware-1.0.0.abc123"
        mock_entry.is_symlink.return_value = False
        mock_entry.is_dir.return_value = True
        mock_entry.path = "/mock/prerelease/firmware-1.0.0.abc123"

        mock_scandir.return_value.__enter__.return_value = [mock_entry]

        with (
            patch.object(
                VersionManager,
                "get_release_tuple",
                return_value=(2, 0, 0),
            ),
            patch("shutil.rmtree", side_effect=OSError("Permission denied")),
            patch("fetchtastic.download.firmware.logger") as mock_logger,
        ):
            result = downloader.cleanup_superseded_prereleases("v2.0.0")

        # Should log error and continue
        mock_logger.error.assert_called()
        assert result is False  # No successful cleanup

    # Lines 1348-1350: Cleanup scandir error at outer level
    @patch("os.path.exists")
    def test_cleanup_old_versions_scandir_error(self, mock_exists, downloader, mocker):
        """Test outer OSError handling when scandir fails."""
        mock_exists.return_value = True

        # Need releases for the first part of the method to succeed
        downloader.get_releases = Mock(return_value=[Release(tag_name="v1.0.0")])
        downloader._sanitize_required = Mock(return_value="v1.0.0")
        downloader._get_comparable_base_tag = Mock(return_value="v1.0.0")
        downloader.collect_non_revoked_releases = Mock(
            return_value=([Release(tag_name="v1.0.0")], [Release(tag_name="v1.0.0")], 8)
        )

        with (
            patch("os.scandir", side_effect=OSError("Permission denied")),
            patch("fetchtastic.download.firmware.logger") as mock_logger,
        ):
            downloader.cleanup_old_versions(keep_limit=2)

        # The error should be caught and logged
        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if "cleaning up" in str(call).lower() or "cleanup" in str(call).lower()
        ]
        assert len(error_calls) > 0


# Backwards compatibility - ensure existing test class still works


@pytest.mark.unit
@pytest.mark.core_downloads
class TestFirmwarePrereleaseBaselineDerivation:
    """Tests that firmware prerelease baseline derivation produces correct version families."""

    def test_baseline_from_official_tag_derives_next_patch(self):
        """calculate_expected_prerelease_version should derive 2.7.23 from v2.7.22."""
        vm = VersionManager()
        clean = vm.extract_clean_version("v2.7.22")
        assert clean == "v2.7.22"
        expected = vm.calculate_expected_prerelease_version(clean)
        assert expected == "2.7.23"

    def test_baseline_from_hash_suffixed_tag_still_clean(self):
        """extract_clean_version should strip the hash suffix for baseline derivation."""
        vm = VersionManager()
        clean = vm.extract_clean_version("v2.7.16.9058cce")
        assert clean == "v2.7.16"
        expected = vm.calculate_expected_prerelease_version(clean)
        assert expected == "2.7.17"

    def test_baseline_derivation_consistent_with_latest_release(self):
        """When v2.7.22 is the latest release, derived prerelease must be 2.7.23 not 2.7.17."""
        vm = VersionManager()
        official_clean = vm.extract_clean_version("v2.7.22")
        official_expected = vm.calculate_expected_prerelease_version(official_clean)
        hash_suffixed_clean = vm.extract_clean_version("v2.7.16.9058cce")
        hash_suffixed_expected = vm.calculate_expected_prerelease_version(
            hash_suffixed_clean
        )
        assert official_expected == "2.7.23"
        assert hash_suffixed_expected == "2.7.17"
        assert official_expected != hash_suffixed_expected

    def test_expected_family_from_hash_suffixed_tag(self, downloader):
        """download_repo_prerelease_firmware should compute expected_version 2.7.23 from v2.7.22.96dd647."""
        active_dir = "firmware-2.7.23.abcdef1"
        history_entries = [
            {"identifier": "abcdef1", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[active_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
        ):
            _results, _failed, _latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        assert summary is not None
        assert summary["expected_version"] == "2.7.23"
        assert summary["expected_version"] != "2.7.16"
        assert summary["clean_latest_release"] == "v2.7.22"

    def test_hash_suffixed_tag_baseline_tuple(self):
        """get_release_tuple should derive (2, 7, 22) from 2.7.22.96dd647, not include the hash or use wrong baseline."""
        vm = VersionManager()
        release_tuple = vm.get_release_tuple("2.7.22.96dd647")
        assert release_tuple is not None
        assert release_tuple == (2, 7, 22)
        assert len(release_tuple) == 3
        assert release_tuple != (2, 7, 15)

    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_superseded_prereleases_hash_suffixed_tag(
        self, mock_rmtree, mock_scandir, downloader
    ):
        """cleanup_superseded_prereleases should use (2, 7, 22) as baseline from v2.7.22.96dd647."""
        mock_prerelease_keep = Mock()
        mock_prerelease_keep.name = "firmware-2.7.23.abcdef1"
        mock_prerelease_keep.is_symlink.return_value = False
        mock_prerelease_keep.is_dir.return_value = True
        mock_prerelease_keep.path = "/mock/prerelease/firmware-2.7.23.abcdef1"

        mock_prerelease_remove = Mock()
        mock_prerelease_remove.name = "firmware-2.7.22.oldhash"
        mock_prerelease_remove.is_symlink.return_value = False
        mock_prerelease_remove.is_dir.return_value = True
        mock_prerelease_remove.path = "/mock/prerelease/firmware-2.7.22.oldhash"

        mock_scandir.return_value.__enter__.return_value = [
            mock_prerelease_keep,
            mock_prerelease_remove,
        ]
        mock_scandir.return_value.__exit__.return_value = None

        result = downloader.cleanup_superseded_prereleases("v2.7.22.96dd647")

        assert result is True
        mock_rmtree.assert_called_once_with("/mock/prerelease/firmware-2.7.22.oldhash")

    # =========================================================================
    # Tests for deterministic prerelease directory sorting (review fix 1)
    # =========================================================================

    def test_sort_prerelease_dirs_orders_by_version_then_string(self, downloader):
        """_sort_prerelease_dirs should sort by version tuple then directory string."""
        unsorted = [
            "firmware-2.7.25.999999",
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.24.cccccc",
            "firmware-2.7.23.bbbbbb",
        ]
        sorted_dirs = downloader._sort_prerelease_dirs(unsorted)
        assert sorted_dirs == [
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.23.bbbbbb",
            "firmware-2.7.24.cccccc",
            "firmware-2.7.25.999999",
        ]

    def test_sort_prerelease_dirs_dedupes(self, downloader):
        """_sort_prerelease_dirs should remove duplicate entries."""
        dirs = [
            "firmware-2.7.24.cccccc",
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.24.cccccc",
        ]
        sorted_dirs = downloader._sort_prerelease_dirs(dirs)
        assert sorted_dirs == [
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.24.cccccc",
        ]

    def test_sort_prerelease_dirs_newest_last(self, downloader):
        """After sorting, the last element is the newest prerelease dir."""
        dirs = [
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.25.999999",
            "firmware-2.7.24.cccccc",
        ]
        sorted_dirs = downloader._sort_prerelease_dirs(dirs)
        assert sorted_dirs[-1] == "firmware-2.7.25.999999"

    # =========================================================================
    # Tests for _get_active_prerelease_dirs_from_history tightening (review fix 4)
    # =========================================================================

    def test_get_active_prerelease_dirs_from_history_active_only(self, downloader):
        """Only explicitly active entries should be returned."""
        entries = [
            {"directory": "firmware-2.7.23.aaaaaa", "status": "active"},
            {"directory": "firmware-2.7.23.bbbbbb", "status": "deleted"},
            {"directory": "firmware-2.7.23.cccccc", "status": "active"},
            {
                "directory": "firmware-2.7.23.dddddd"
            },  # no status key, not explicitly active
            {"directory": "firmware-2.7.23.eeeeee", "status": "unknown"},
        ]
        result = downloader._get_active_prerelease_dirs_from_history(entries)
        assert result == ["firmware-2.7.23.aaaaaa", "firmware-2.7.23.cccccc"]

    def test_get_active_prerelease_dirs_from_history_respects_removed_at(
        self, downloader
    ):
        """Entries with removed_at should be excluded even if status is active."""
        entries = [
            {"directory": "firmware-2.7.23.aaaaaa", "status": "active"},
            {
                "directory": "firmware-2.7.23.bbbbbb",
                "status": "active",
                "removed_at": "2024-01-01",
            },
        ]
        result = downloader._get_active_prerelease_dirs_from_history(entries)
        assert result == ["firmware-2.7.23.aaaaaa"]

    def test_get_active_prerelease_dirs_from_history_active_flag(self, downloader):
        """Entries with active=True should be included."""
        entries = [
            {"directory": "firmware-2.7.23.aaaaaa", "active": True},
            {"directory": "firmware-2.7.23.bbbbbb", "active": False},
        ]
        result = downloader._get_active_prerelease_dirs_from_history(entries)
        assert result == ["firmware-2.7.23.aaaaaa"]

    # =========================================================================
    # Tests for error handling in second repo scan (review fixes 2/3)
    # =========================================================================

    def test_download_repo_prerelease_firmware_second_scan_error(
        self, downloader, tmp_path
    ):
        """Exception in second repo availability scan should not crash prerelease processing."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                side_effect=requests.RequestException("Second scan failed"),
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
        ):
            results, failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        # Should still complete without raising; history-derived active dir preserved
        assert isinstance(results, list)
        assert isinstance(failed, list)
        assert latest == active_dir

    def test_download_repo_prerelease_firmware_second_scan_non_list(
        self, downloader, tmp_path
    ):
        """Non-list response from second repo scan should be handled gracefully."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value="not-a-list",
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
        ):
            results, failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        assert isinstance(results, list)
        assert isinstance(failed, list)
        assert latest == active_dir

    # =========================================================================
    # Tests for unsorted repo directory listings (review fix 6)
    # =========================================================================

    def test_download_repo_prerelease_firmware_unsorted_fallback_dirs(
        self, downloader, tmp_path
    ):
        """Fallback scan with unsorted repo dirs should download all matching and return the sorted newest."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        # All dirs must match expected_version "2.7.23"
        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.bbbbbb",
                    "firmware-2.7.23.aaaaaa",
                    "firmware-2.7.23.2a858be",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            result = downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # The returned active_dir should be the sorted newest (dir string tiebreaker)
        # String comparison: '2' < 'a' < 'b', so newest = bbbbbb
        assert result[2] == "firmware-2.7.23.bbbbbb"
        # All matching dirs should be downloaded in deterministic sorted order (by dir string)
        call_args = [call.args[0] for call in download_assets.call_args_list]
        assert call_args == [
            "firmware-2.7.23.2a858be",
            "firmware-2.7.23.aaaaaa",
            "firmware-2.7.23.bbbbbb",
        ]

    def test_download_repo_prerelease_firmware_unsorted_history_dirs(
        self, downloader, tmp_path
    ):
        """History-derived dirs in arbitrary order should be sorted deterministically."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        history_entries = [
            {
                "identifier": "2a858be",
                "directory": "firmware-2.7.23.2a858be",
                "status": "active",
            },
            {
                "identifier": "7be5426",
                "directory": "firmware-2.7.23.7be5426",
                "status": "active",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=["firmware-2.7.23.7be5426", "firmware-2.7.23.2a858be"],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ) as download_assets,
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.2a858be", history_entries),
            ),
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # Download order should be deterministic: sorted by version tuple then directory string
        call_args = [call.args[0] for call in download_assets.call_args_list]
        assert call_args == [
            "firmware-2.7.23.2a858be",
            "firmware-2.7.23.7be5426",
        ]

    # =========================================================================
    # Tests for synthetic entries in prerelease summary (review fix 5)
    # =========================================================================

    def test_download_repo_prerelease_firmware_summary_includes_synthetic_entries(
        self, downloader, tmp_path
    ):
        """Prerelease summary should include synthetic entries for repo-discovered dirs not in history."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        history_entries = [
            {
                "identifier": "7be5426",
                "directory": "firmware-2.7.23.7be5426",
                "status": "active",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.2a858be",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.7be5426", history_entries),
            ),
        ):
            _results, _failed, _latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        assert summary is not None
        available_entries = summary.get("available_history_entries")
        assert available_entries is not None
        available_dirs = {e["directory"] for e in available_entries}
        assert "firmware-2.7.23.7be5426" in available_dirs
        assert "firmware-2.7.23.2a858be" in available_dirs
        # Find the synthetic entry
        synthetic = [
            e for e in available_entries if e["directory"] == "firmware-2.7.23.2a858be"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["status"] == "active"
        assert synthetic[0]["active"] is True
        assert synthetic[0]["identifier"] == "2.7.23.2a858be"

    def test_download_repo_prerelease_firmware_summary_excludes_stale_active_entries(
        self, downloader, tmp_path
    ):
        """Prerelease summary should exclude stale active history entries not present in the repo."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        history_entries = [
            {
                "identifier": "aaaaaa",
                "directory": "firmware-2.7.23.aaaaaa",
                "status": "active",
            },
            {
                "identifier": "bbbbbb",
                "directory": "firmware-2.7.23.bbbbbb",
                "status": "active",
            },
            {
                "identifier": "cccccc",
                "directory": "firmware-2.7.23.cccccc",
                "status": "deleted",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.aaaaaa",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.aaaaaa", history_entries),
            ),
        ):
            _results, _failed, _latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        assert summary is not None
        available_entries = summary.get("available_history_entries")
        assert available_entries is not None
        available_dirs = {e["directory"] for e in available_entries}
        # Available active dir should be included
        assert "firmware-2.7.23.aaaaaa" in available_dirs
        # Deleted dir should be included
        assert "firmware-2.7.23.cccccc" in available_dirs
        # Stale active dir missing from repo should be excluded
        assert "firmware-2.7.23.bbbbbb" not in available_dirs

    def test_download_repo_prerelease_firmware_summary_fallback_no_history(
        self, downloader, tmp_path
    ):
        """Repo-discovered prereleases with no history should still produce a summary."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.aaaaaa",
                    "firmware-2.7.23.bbbbbb",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            _results, _failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        assert summary is not None
        assert summary["history_entries"] == []
        assert summary["clean_latest_release"] == "v2.7.22"
        assert summary["expected_version"] == "2.7.23"
        available_entries = summary.get("available_history_entries")
        assert available_entries is not None
        available_dirs = {e["directory"] for e in available_entries}
        assert "firmware-2.7.23.aaaaaa" in available_dirs
        assert "firmware-2.7.23.bbbbbb" in available_dirs
        assert latest == "firmware-2.7.23.bbbbbb"

    def test_download_repo_prerelease_firmware_empty_repo_scan_preserves_history(
        self, downloader, tmp_path
    ):
        """Empty repo availability scan should preserve history-derived active dirs."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        deleted_dir = "firmware-2.7.23.cccccc"
        history_entries = [
            {
                "identifier": "aaaaaa",
                "directory": active_dir,
                "status": "active",
            },
            {
                "identifier": "bbbbbb",
                "directory": "firmware-2.7.23.bbbbbb",
                "status": "active",
            },
            {
                "identifier": "cccccc",
                "directory": deleted_dir,
                "status": "deleted",
            },
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
        ):
            _results, _failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        # History-derived active dirs should be preserved even with empty repo scan
        # Sorted order puts bbbbbb last (dir string tiebreaker)
        assert latest == "firmware-2.7.23.bbbbbb"
        assert summary is not None
        available_entries = summary.get("available_history_entries")
        assert available_entries is not None
        available_dirs = {e["directory"] for e in available_entries}
        # All history-derived active dirs preserved when repo scan is empty
        assert active_dir in available_dirs
        assert "firmware-2.7.23.bbbbbb" in available_dirs
        # Deleted dir should still be included in summary
        assert deleted_dir in available_dirs

    def test_download_repo_prerelease_firmware_tracks_all_active_dirs_when_no_failures(
        self, downloader, tmp_path
    ):
        """All deterministic active dirs should be tracked when there are no failures."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        older_dir = "firmware-2.7.23.aaaaaa"
        newer_dir = "firmware-2.7.23.bbbbbb"
        history_entries = [
            {"identifier": "aaaaaa", "directory": older_dir, "status": "active"},
            {"identifier": "bbbbbb", "directory": newer_dir, "status": "active"},
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[older_dir, newer_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                side_effect=[
                    ([], [], True),  # older downloads new files
                    ([], [], False),  # newer already exists (skipped)
                ],
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(newer_dir, history_entries),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.update_prerelease_tracking"
            ) as mock_track,
        ):
            _results, _failed, latest, _summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        # Newer dir should be returned as latest (sorted order)
        assert latest == newer_dir
        # Both dirs should be tracked (no failures, so all active_dirs tracked)
        assert mock_track.call_count == 2
        track_dirs = [call.args[1] for call in mock_track.call_args_list]
        assert track_dirs == [older_dir, newer_dir]

    def test_download_repo_prerelease_firmware_tracks_only_downloaded_when_failures(
        self, downloader, tmp_path
    ):
        """Only successfully downloaded dirs should be tracked when there are failures."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        success_dir = "firmware-2.7.23.aaaaaa"
        fail_dir = "firmware-2.7.23.bbbbbb"
        history_entries = [
            {"identifier": "aaaaaa", "directory": success_dir, "status": "active"},
            {"identifier": "bbbbbb", "directory": fail_dir, "status": "active"},
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[success_dir, fail_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                side_effect=[
                    ([], [], True),  # success downloads
                    ([], [Mock(success=False)], False),  # fail_dir has failures
                ],
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(fail_dir, history_entries),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.update_prerelease_tracking"
            ) as mock_track,
        ):
            _results, _failed, latest, _summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )

        # Only the successfully downloaded dir should be tracked
        assert mock_track.call_count == 1
        assert mock_track.call_args.args[1] == success_dir

    def test_download_repo_prerelease_latest_uses_successful_dir_despite_older_failure(
        self, downloader, tmp_path
    ):
        """A newer skipped/successful prerelease can update latest even when tracking is empty."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.config["CREATE_LATEST_SYMLINKS"] = True
        downloader.download_dir = str(tmp_path)

        older_dir = "firmware-2.7.23.aaaaaa"
        newer_dir = "firmware-2.7.23.bbbbbb"
        history_entries = [
            {"identifier": "aaaaaa", "directory": older_dir, "status": "active"},
            {"identifier": "bbbbbb", "directory": newer_dir, "status": "active"},
        ]
        skipped_success = DownloadResult(success=True, was_skipped=True)
        failure = DownloadResult(success=False)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[older_dir, newer_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                side_effect=[
                    ([], [failure], False),
                    ([skipped_success], [], False),
                ],
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(newer_dir, history_entries),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.update_prerelease_tracking"
            ) as mock_track,
            patch("fetchtastic.download.firmware.update_latest_pointer") as mock_latest,
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        mock_track.assert_not_called()
        mock_latest.assert_called_once_with(
            str(tmp_path / FIRMWARE_DIR_NAME / FIRMWARE_PRERELEASES_DIR_NAME),
            newer_dir,
            LATEST_POINTER_NAME,
        )

    def test_download_repo_prerelease_latest_uses_newest_successful_dir(
        self, downloader, tmp_path
    ):
        """Latest pointer chooses the chronologically newest successful prerelease dir."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.config["CREATE_LATEST_SYMLINKS"] = True
        downloader.download_dir = str(tmp_path)

        older_dir = "firmware-2.7.23.aaaaaa"
        newer_dir = "firmware-2.7.23.bbbbbb"
        history_entries = [
            {
                "identifier": "aaaaaa",
                "directory": older_dir,
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "identifier": "bbbbbb",
                "directory": newer_dir,
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]
        success = DownloadResult(success=True)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[older_dir, newer_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([success], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(newer_dir, history_entries),
            ),
            patch("fetchtastic.download.firmware.update_latest_pointer") as mock_latest,
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        mock_latest.assert_called_once_with(
            str(tmp_path / FIRMWARE_DIR_NAME / FIRMWARE_PRERELEASES_DIR_NAME),
            newer_dir,
            LATEST_POINTER_NAME,
        )

    def test_download_repo_prerelease_latest_coerces_false_string(
        self, downloader, tmp_path
    ):
        """String false disables repo-prerelease latest pointer updates."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.config["CREATE_LATEST_SYMLINKS"] = "false"
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "directory": active_dir, "status": "active"}
        ]
        success = DownloadResult(success=True)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[active_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([success], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch("fetchtastic.download.firmware.update_latest_pointer") as mock_latest,
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        mock_latest.assert_not_called()

    def test_download_repo_prerelease_latest_not_updated_without_successful_dir(
        self, downloader, tmp_path
    ):
        """No successful prerelease result means no latest pointer update."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.config["CREATE_LATEST_SYMLINKS"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "directory": active_dir, "status": "active"}
        ]

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[active_dir],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch("fetchtastic.download.firmware.update_latest_pointer") as mock_latest,
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        mock_latest.assert_not_called()


@pytest.mark.unit
@pytest.mark.core_downloads
class TestPrereleaseAvailabilityVerification:
    """Tests for prerelease repo availability verification (force_refresh and fallback reuse)."""

    def test_second_scan_uses_force_refresh_false_by_default(
        self, downloader, tmp_path
    ):
        """When history provides active_dirs and force_refresh=False, second scan calls get_repo_directories with force_refresh=False."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[active_dir, "other-dir"],
            ) as mock_get_dirs,
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # Should call get_repo_directories once (availability scan) with force_refresh=False
        assert mock_get_dirs.call_count == 1
        assert mock_get_dirs.call_args[1].get("force_refresh") is False

    def test_second_scan_uses_force_refresh_true_when_requested(
        self, downloader, tmp_path
    ):
        """When force_refresh=True, the repo directory call still receives force_refresh=True."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        active_dir = "firmware-2.7.23.aaaaaa"
        history_entries = [
            {"identifier": "aaaaaa", "status": "active", "directory": active_dir}
        ]

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(active_dir, history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[active_dir, "other-dir"],
            ) as mock_get_dirs,
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
        ):
            downloader.download_repo_prerelease_firmware(
                "v2.7.22.96dd647", force_refresh=True
            )

        # Should call get_repo_directories with force_refresh=True
        assert mock_get_dirs.call_count == 1
        assert mock_get_dirs.call_args[1].get("force_refresh") is True

    def test_fallback_reuses_repo_dirs_for_availability_scan(
        self, downloader, tmp_path
    ):
        """When fallback scan already fetched repo dirs, the availability scan reuses them."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        with (
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.aaaaaa",
                    "firmware-2.7.23.bbbbbb",
                ],
            ) as mock_get_dirs,
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=(None, []),
            ),
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        # Should call get_repo_directories exactly once (fallback scan reused for availability)
        assert mock_get_dirs.call_count == 1

    # =========================================================================
    # Regression tests for repo-prerelease chronology selection (maint-430-1)
    # =========================================================================

    def test_select_latest_prerelease_dir_history_chronology_beats_hash_order(
        self, downloader, tmp_path
    ):
        """History chronology (added_at) should beat hash/string ordering."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.4ee9598"

    def test_select_latest_prerelease_dir_returns_newest_active_dir(
        self, downloader, tmp_path
    ):
        """The returned active_dir from download_repo_prerelease_firmware uses chronology."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]
        success_result = DownloadResult(
            success=True,
            release_tag="firmware-2.7.23.4ee9598",
            file_path=str(tmp_path / "test"),
            was_skipped=True,
        )

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.4ee9598", history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.4ee9598",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([success_result], [], False),
            ),
        ):
            successes, failures, active_dir, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")
            )
        assert active_dir == "firmware-2.7.23.4ee9598"

    def test_select_latest_prerelease_dir_update_pointer_uses_chronology(
        self, downloader, tmp_path
    ):
        """update_latest_pointer should be called with the chronologically newest dir."""
        downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True
        downloader.download_dir = str(tmp_path)

        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]
        success_result = DownloadResult(
            success=True,
            release_tag="firmware-2.7.23.4ee9598",
            file_path=str(tmp_path / "test"),
            was_skipped=True,
        )

        with (
            patch(
                "fetchtastic.download.firmware.PrereleaseHistoryManager.get_latest_active_prerelease_from_history",
                return_value=("firmware-2.7.23.4ee9598", history_entries),
            ),
            patch.object(
                downloader.cache_manager,
                "get_repo_directories",
                return_value=[
                    "firmware-2.7.23.7be5426",
                    "firmware-2.7.23.4ee9598",
                ],
            ),
            patch.object(
                downloader,
                "_download_prerelease_assets",
                return_value=([success_result], [], False),
            ),
            patch(
                "fetchtastic.download.firmware.update_latest_pointer"
            ) as mock_pointer,
        ):
            downloader.download_repo_prerelease_firmware("v2.7.22.96dd647")

        mock_pointer.assert_called_once_with(
            ANY, "firmware-2.7.23.4ee9598", LATEST_POINTER_NAME
        )

    def test_select_latest_prerelease_dir_deleted_newer_does_not_win(
        self, downloader, tmp_path
    ):
        """A newer history entry marked deleted should not win over an older active one."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "deleted",
                "added_at": "2024-01-02T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.7be5426"

    def test_select_latest_prerelease_dir_deleted_via_removed_at(
        self, downloader, tmp_path
    ):
        """An entry with removed_at should be excluded even if status is active."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "active",
                "added_at": "2024-01-02T00:00:00Z",
                "removed_at": "2024-01-03T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.7be5426"

    def test_select_latest_prerelease_dir_repo_scan_does_not_outrank_history(
        self, downloader, tmp_path
    ):
        """Repo-discovered synthetic dirs should not outrank real history-backed dirs."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.7be5426"

    def test_select_latest_prerelease_dir_fallback_deterministic_without_history(
        self, downloader, tmp_path
    ):
        """When no history exists, fallback to deterministic sort."""
        downloader.download_dir = str(tmp_path)
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
            "firmware-2.7.24.aaaaaa",
        ]
        result = downloader._select_latest_prerelease_dir(candidate_dirs, [])
        sorted_dirs = downloader._sort_prerelease_dirs(candidate_dirs)
        assert result == sorted_dirs[-1]

    def test_select_latest_prerelease_dir_history_order_without_added_at(
        self, downloader, tmp_path
    ):
        """When added_at is missing, history list order (oldest-to-newest) should decide."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
            },
            {
                "directory": "firmware-2.7.23.4ee9598",
                "identifier": "2.7.23.4ee9598",
                "status": "active",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.4ee9598"

    def test_select_latest_prerelease_dir_empty_candidates(self, downloader, tmp_path):
        """Returns None when candidate_dirs is empty."""
        downloader.download_dir = str(tmp_path)
        result = downloader._select_latest_prerelease_dir([], [])
        assert result is None

    def test_select_latest_prerelease_dir_candidate_not_in_history(
        self, downloader, tmp_path
    ):
        """A candidate not in history should still be considered with fallback ranking."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.4ee9598",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.4ee9598"

    def test_select_latest_prerelease_dir_mixed_history_and_no_history(
        self, downloader, tmp_path
    ):
        """History-backed candidate beats non-history candidate even if non-history sorts higher."""
        downloader.download_dir = str(tmp_path)
        history_entries = [
            {
                "directory": "firmware-2.7.23.7be5426",
                "identifier": "2.7.23.7be5426",
                "status": "active",
                "added_at": "2024-01-01T00:00:00Z",
            },
        ]
        candidate_dirs = [
            "firmware-2.7.23.7be5426",
            "firmware-2.7.24.aaaaaa",
        ]
        result = downloader._select_latest_prerelease_dir(
            candidate_dirs, history_entries
        )
        assert result == "firmware-2.7.23.7be5426"

    # =========================================================================
    # Tests for expected-symlink warning suppression in cleanup
    # =========================================================================

    def test_cleanup_superseded_prereleases_silently_skips_latest_symlink(
        self, downloader, tmp_path
    ):
        """The expected 'latest' symlink in prerelease folder should not produce a warning."""
        downloader.download_dir = str(tmp_path)
        prerelease_dir = tmp_path / FIRMWARE_DIR_NAME / FIRMWARE_PRERELEASES_DIR_NAME
        prerelease_dir.mkdir(parents=True)

        old_prerelease = prerelease_dir / "firmware-2.7.20.aaaaaa"
        old_prerelease.mkdir()
        latest_link = prerelease_dir / LATEST_POINTER_NAME
        latest_link.symlink_to(old_prerelease)

        with patch.object(
            downloader,
            "_get_release_storage_tag",
            return_value="v2.7.22",
            create=True,
        ):
            with patch.object(log_utils, "logger") as mock_logger:
                downloader.cleanup_superseded_prereleases("v2.7.22")

        warning_calls = [call for call in mock_logger.warning.call_args_list]
        for call in warning_calls:
            assert (
                LATEST_POINTER_NAME not in str(call) or "expected" in str(call).lower()
            )
