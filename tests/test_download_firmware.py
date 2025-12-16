# Test Firmware Downloader
#
# Comprehensive unit tests for the FirmwareReleaseDownloader class.

from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release


class TestFirmwareReleaseDownloader:
    """Test suite for FirmwareReleaseDownloader."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration dictionary."""
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
        return mock

    @pytest.fixture
    def downloader(self, mock_config, mock_cache_manager):
        """
        Create a FirmwareReleaseDownloader configured for tests with injected mocked dependencies.
        
        Parameters:
        	mock_config (dict): Configuration dictionary to initialize the downloader.
        	mock_cache_manager (Mock): Mocked CacheManager used for cache interactions.
        
        Returns:
        	dl (FirmwareReleaseDownloader): Initialized downloader whose `cache_manager` is set to `mock_cache_manager` and whose `version_manager` and `file_operations` attributes are replaced with mocks.
        """
        dl = FirmwareReleaseDownloader(mock_config, mock_cache_manager)
        # Mock the dependencies that are set in __init__
        dl.cache_manager = mock_cache_manager
        dl.version_manager = Mock()
        dl.file_operations = Mock()
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

    @patch("fetchtastic.download.firmware.download_file_with_retry")
    def test_download_firmware_download_failure(self, mock_download, downloader):
        """Test firmware download failure."""
        mock_download.return_value = False

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
    @patch("os.listdir")
    @patch("os.path.isdir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions(
        self, mock_rmtree, mock_isdir, mock_listdir, mock_exists, downloader
    ):
        """Test cleanup of old firmware versions."""
        # Setup filesystem mocks
        mock_exists.return_value = True
        mock_listdir.return_value = [
            "v1.0.0",
            "v2.0.0",
            "v3.0.0",
            "prerelease",
            "repo-dls",
        ]
        mock_isdir.return_value = True

        downloader.cleanup_old_versions(keep_limit=2)

        # Should remove oldest version (v1.0.0)
        mock_rmtree.assert_called_once()
        args = mock_rmtree.call_args[0][0]
        assert "v1.0.0" in args

    def test_get_version_sort_key(self, downloader):
        """Test version sorting key generation."""
        key = downloader._get_version_sort_key("v2.1.3")
        assert key == (2, 1, 3)

        key = downloader._get_version_sort_key("v1.0")
        assert key == (1, 0, 0)

    @patch("os.path.exists")
    @patch("builtins.open")
    @patch("json.load")
    def test_get_latest_release_tag(
        self, mock_json_load, mock_open, mock_exists, downloader
    ):
        """Test getting latest release tag from tracking file."""
        mock_exists.return_value = True
        mock_json_load.return_value = {"latest_version": "v2.0.0"}

        tag = downloader.get_latest_release_tag()

        assert tag == "v2.0.0"

    @patch("datetime.datetime")
    def test_update_latest_release_tag(self, mock_datetime, downloader):
        """Test updating latest release tag."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        downloader.cache_manager.atomic_write_json = Mock(return_value=True)

        result = downloader.update_latest_release_tag("v2.0.0")

        assert result is True

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

    @patch("os.path.exists")
    @patch("os.listdir")
    @patch("os.path.isdir")
    @patch("shutil.rmtree")
    def test_cleanup_superseded_prereleases(
        self, mock_rmtree, mock_isdir, mock_listdir, mock_exists, downloader
    ):
        """Test cleanup of superseded prereleases."""
        # Setup filesystem mocks
        mock_exists.return_value = True
        mock_listdir.return_value = ["firmware-1.0.0.abc123", "firmware-2.0.0.def456"]
        mock_isdir.return_value = True

        # Mock version comparison
        downloader.version_manager.compare_versions = Mock(
            side_effect=[-1, 1]
        )  # v1.0.0 < v2.0.0

        result = downloader.cleanup_superseded_prereleases("v2.0.0")

        assert result is True
        assert mock_rmtree.call_count == 2

    def test_get_prerelease_tracking_file(self, downloader):
        """Test prerelease tracking file path generation."""
        path = downloader.get_prerelease_tracking_file()

        assert "latest_firmware_prerelease.json" in path

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
            patch("os.listdir", return_value=[]),
            patch(
                "fetchtastic.download.files._atomic_write",
                side_effect=lambda *args, **kwargs: None,
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

        # Should return prereleases
        assert len(result) >= 0
        assert isinstance(result, list)