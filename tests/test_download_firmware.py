# Test Firmware Downloader
#
# Comprehensive unit tests for the FirmwareReleaseDownloader class.

import json
import os
from pathlib import Path
from typing import ClassVar
from unittest.mock import ANY, Mock, patch

import pytest

from fetchtastic import log_utils
from fetchtastic.constants import FIRMWARE_DIR_NAME, RELEASE_SCAN_COUNT
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager


class TestFirmwareReleaseDownloader:
    """Test suite for FirmwareReleaseDownloader."""

    pytestmark: ClassVar[list] = [pytest.mark.unit, pytest.mark.core_downloads]

    @pytest.fixture
    def mock_config(self):
        """
        Provide a mock configuration dictionary used by the test suite.

        Returns:
            dict: Test configuration containing:
                - DOWNLOAD_DIR (str): Base directory for downloads ("/tmp/test").
                - CHECK_FIRMWARE_PRERELEASES (bool): Whether prereleases are considered.
                - SELECTED_PRERELEASE_ASSETS (list[str]): Asset name substrings to select from prereleases.
                - EXCLUDE_PATTERNS (list[str]): Filename glob patterns to exclude.
                - GITHUB_TOKEN (str): Token used for GitHub API requests ("test_token").
        """
        return {
            "DOWNLOAD_DIR": "/tmp/test",
            "CHECK_FIRMWARE_PRERELEASES": True,
            "SELECTED_PRERELEASE_ASSETS": ["rak4631"],
            "EXCLUDE_PATTERNS": ["*debug*"],
            "GITHUB_TOKEN": "test_token",
        }

    @pytest.fixture
    def mock_cache_manager(self):
        """Mock CacheManager instance."""
        mock = Mock(spec=CacheManager)
        mock.cache_dir = "/tmp/cache"
        mock.get_cache_file_path.side_effect = lambda file_name: os.path.join(
            mock.cache_dir, file_name
        )
        return mock

    @pytest.fixture
    def downloader(self, mock_config, mock_cache_manager):
        """
        Create a FirmwareReleaseDownloader configured for tests with injected mocked dependencies.

        Parameters:
            mock_config (dict): Configuration values used to initialize the downloader.
            mock_cache_manager (Mock): Mocked CacheManager used for cache interactions.

        Returns:
            FirmwareReleaseDownloader: Initialized downloader whose `cache_manager` is set to `mock_cache_manager` and whose `version_manager` and `file_operations` are replaced with mocks. The `version_manager.get_release_tuple` delegates to a real VersionManager implementation.
        """
        dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)
        # Mock dependencies that are set in __init__
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

        expected = "/tmp/test/firmware/v1.0.0/firmware.zip"
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

    @patch("fetchtastic.download.firmware.make_github_api_request")
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

    @patch("fetchtastic.utils.download_file_with_retry")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    def test_download_firmware_success(
        self, mock_getsize, mock_exists, mock_download, downloader
    ):
        """
        Verify that downloading and extracting a firmware asset succeeds and returns expected metadata.

        Parameters:
            mock_getsize (Mock): Fixture mocking os.path.getsize used to simulate existing file size.
            mock_exists (Mock): Fixture mocking os.path.exists used to simulate file presence.
            mock_download (Mock): Fixture mocking the network download function; should emulate a successful download.
            downloader (FirmwareReleaseDownloader): Fixture instance under test with verification and extraction methods mocked.
        """
        # Setup mocks
        mock_exists.return_value = True
        mock_getsize.return_value = 1000000
        mock_download.return_value = True

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
        mock_download.assert_called_once()

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

    @patch("os.path.exists")
    def test_extract_firmware_success(self, mock_exists, downloader):
        """Test successful firmware extraction."""
        mock_exists.return_value = True

        # Mock release and asset
        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rak4631.zip"

        # Mock file operations
        downloader.file_operations.extract_archive = Mock(return_value=["firmware.bin"])

        result = downloader.extract_firmware(release, asset, ["*.bin"], ["readme*"])

        assert result.success is True
        assert result.extracted_files == ["firmware.bin"]
        downloader.file_operations.extract_archive.assert_called_once()

    @patch("os.path.exists")
    def test_extract_firmware_no_matches_is_warning(self, mock_exists, downloader):
        """Test extraction when no files match patterns."""
        mock_exists.return_value = True

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "firmware-rp2040.zip"

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
        assert mock_rmtree.call_count == 0

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

    @patch("fetchtastic.download.firmware.make_github_api_request")
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
        downloader.cache_manager.get_cache_file_path.return_value = (
            "/tmp/cache/latest_firmware_release.json"
        )

        result = downloader.update_latest_release_tag("v2.0.0")

        assert result is True
        downloader.cache_manager.atomic_write_json.assert_called_once_with(
            "/tmp/cache/latest_firmware_release.json", ANY
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
            patch.object(downloader, "_download_prerelease_assets") as mock_download,
        ):
            results, failed, latest, summary = (
                downloader.download_repo_prerelease_firmware("v2.7.17.9058cce")
            )

        assert results == []
        assert failed == []
        assert latest is None
        assert summary is not None
        assert summary["history_entries"] == history_entries
        mock_download.assert_not_called()

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

    def test_needs_download_size_mismatch(self, downloader):
        """Size mismatches should force downloads."""
        downloader.get_existing_file_path = Mock(return_value="/tmp/file.zip")
        downloader.file_operations.get_file_size = Mock(return_value=10)

        assert downloader.needs_download("v1.0.0", "file.zip", expected_size=20) is True

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
