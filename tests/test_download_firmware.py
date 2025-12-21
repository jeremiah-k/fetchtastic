# Test Firmware Downloader
#
# Comprehensive unit tests for the FirmwareReleaseDownloader class.

import json
import os
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import pytest

from fetchtastic import log_utils
from fetchtastic.constants import FIRMWARE_DIR_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager


class TestFirmwareReleaseDownloader:
    """Test suite for FirmwareReleaseDownloader."""

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
        # Mock the dependencies that are set in __init__
        dl.cache_manager = mock_cache_manager
        dl.version_manager = Mock()
        dl.file_operations = Mock()
        real_version_manager = VersionManager()
        dl.version_manager.get_release_tuple.side_effect = (
            real_version_manager.get_release_tuple
        )
        return dl

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
        downloader.get_releases.assert_called_once_with(limit=2)

    @patch("os.path.exists")
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions_unsafe_tags(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup when release tags contain unsafe characters."""
        # Mock _sanitize_path_component to return None for unsafe tags
        with patch(
            "fetchtastic.download.firmware._sanitize_path_component"
        ) as mock_sanitize:
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
            mock_sanitize.side_effect = ["v1.0.0", None]  # Second tag is unsafe
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
        downloader.get_releases.assert_called_once_with(limit=0)
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
        entry_keep1.name = "v2.7.17.83c6161"
        entry_keep1.is_symlink.return_value = False
        entry_keep1.is_dir.return_value = True
        entry_keep1.path = "/mock/firmware/v2.7.17.83c6161"

        entry_keep2 = Mock()
        entry_keep2.name = "v2.7.16.a597230"
        entry_keep2.is_symlink.return_value = False
        entry_keep2.is_dir.return_value = True
        entry_keep2.path = "/mock/firmware/v2.7.16.a597230"

        entry_remove = Mock()
        entry_remove.name = "v2.7.15.567b8ea"
        entry_remove.is_symlink.return_value = False
        entry_remove.is_dir.return_value = True
        entry_remove.path = "/mock/firmware/v2.7.15.567b8ea"

        mock_scandir.return_value.__enter__.return_value = [
            entry_keep1,
            entry_keep2,
            entry_remove,
        ]

        downloader.cleanup_old_versions(keep_limit=2)

        mock_rmtree.assert_called_once_with("/mock/firmware/v2.7.15.567b8ea")

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

    def test_download_repo_prerelease_firmware_success(self, downloader):
        """Test repo prerelease firmware download method exists and returns proper types."""
        results, failed, latest = downloader.download_repo_prerelease_firmware("v1.0.0")

        # Should return proper tuple structure
        assert isinstance(results, list)
        assert isinstance(failed, list)
        assert latest is None or isinstance(latest, str)

    def test_handle_prereleases_with_repo_download(self, downloader):
        """Test prerelease handling with repo downloads."""
        releases = [
            Mock(tag_name="v1.0.0-beta", prerelease=True, published_at="2023-01-01"),
        ]
        result = downloader.handle_prereleases(releases)

        # Firmware GitHub prerelease flags are treated as stable.
        assert result == []

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
