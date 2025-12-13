# Test Android Downloader
#
# Comprehensive unit tests for the MeshtasticAndroidAppDownloader class.

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.interfaces import Asset, DownloadResult, Release


class TestMeshtasticAndroidAppDownloader:
    """Test suite for MeshtasticAndroidAppDownloader."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration dictionary."""
        return {
            "DOWNLOAD_DIR": "/tmp/test",
            "CHECK_APK_PRERELEASES": True,
            "SELECTED_APK_ASSETS": ["universal"],
            "EXCLUDE_PATTERNS": ["*beta*"],
            "GITHUB_TOKEN": "test_token",
        }

    @pytest.fixture
    def downloader(self, mock_config):
        """Create a MeshtasticAndroidAppDownloader instance with mocked dependencies."""
        dl = MeshtasticAndroidAppDownloader(mock_config)
        # Mock the dependencies that are set in __init__
        dl.cache_manager = Mock()
        dl.version_manager = Mock()
        dl.file_operations = Mock()
        return dl

    def test_init(self, mock_config):
        """Test downloader initialization."""
        with (
            patch("fetchtastic.download.android.CacheManager") as mock_cache,
            patch("fetchtastic.download.android.VersionManager") as mock_version,
            patch(
                "fetchtastic.download.android.PrereleaseHistoryManager"
            ) as mock_prerelease,
        ):
            dl = MeshtasticAndroidAppDownloader(mock_config)

            assert dl.config == mock_config
            assert (
                dl.android_releases_url
                == "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
            )
            assert dl.latest_release_file == "latest_android_release.json"
            mock_cache.assert_called_once()
            mock_version.assert_called_once()
            mock_prerelease.assert_called_once()

    def test_get_target_path_for_release(self, downloader):
        """Test target path generation for APK releases."""
        path = downloader.get_target_path_for_release("v1.0.0", "meshtastic.apk")

        expected = "/tmp/test/android/v1.0.0/meshtastic.apk"
        assert path == expected

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_success(self, mock_request, downloader):
        """Test successful release fetching from GitHub."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "tag_name": "v1.0.0",
                "prerelease": False,
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "meshtastic.apk",
                        "browser_download_url": "https://example.com/meshtastic.apk",
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
        assert releases[0].assets[0].name == "meshtastic.apk"

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_api_error(self, mock_request, downloader):
        """Test handling of GitHub API errors."""
        mock_request.side_effect = Exception("API Error")

        releases = downloader.get_releases()

        assert releases == []

    def test_get_assets_apk_only(self, downloader):
        """Test that only APK assets are returned."""
        release = Mock(spec=Release)
        release.assets = [
            Mock(spec=Asset, name="meshtastic.apk", download_url="url1", size=1000),
            Mock(spec=Asset, name="meshtastic.aab", download_url="url2", size=2000),
            Mock(spec=Asset, name="readme.txt", download_url="url3", size=100),
        ]

        assets = downloader.get_assets(release)

        assert len(assets) == 1
        assert assets[0].name == "meshtastic.apk"

    def test_get_download_url(self, downloader):
        """Test download URL retrieval."""
        asset = Mock(spec=Asset)
        asset.download_url = "https://example.com/meshtastic.apk"

        url = downloader.get_download_url(asset)

        assert url == "https://example.com/meshtastic.apk"

    def test_should_download_asset_selected(self, downloader):
        """Test asset selection logic."""
        # Asset matches selected patterns
        assert downloader.should_download_asset("meshtastic-universal.apk") is True

        # Asset doesn't match selected patterns
        assert downloader.should_download_asset("meshtastic-arm.apk") is False

    def test_should_download_asset_excluded(self, downloader):
        """Test asset exclusion logic."""
        # Asset matches exclude patterns
        assert downloader.should_download_asset("meshtastic-beta.apk") is False

    @patch("fetchtastic.download.android.download_file_with_retry")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    def test_download_apk_success(
        self, mock_getsize, mock_exists, mock_download, downloader
    ):
        """Test successful APK download."""
        # Setup mocks
        mock_exists.return_value = True
        mock_getsize.return_value = 1000000
        mock_download.return_value = True

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "meshtastic.apk"
        asset.download_url = "https://example.com/meshtastic.apk"
        asset.size = 1000000

        # Mock verification
        downloader.verify = Mock(return_value=True)

        result = downloader.download_apk(release, asset)

        assert result.success is True
        assert result.release_tag == "v1.0.0"
        assert "meshtastic.apk" in result.file_path
        mock_download.assert_called_once()

    @patch("fetchtastic.download.android.download_file_with_retry")
    def test_download_apk_download_failure(self, mock_download, downloader):
        """Test APK download failure."""
        mock_download.return_value = False

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "meshtastic.apk"
        asset.download_url = "https://example.com/meshtastic.apk"
        asset.size = 1000000

        result = downloader.download_apk(release, asset)

        assert result.success is False
        assert result.error_type == "network_error"
        assert result.is_retryable is True

    @patch("os.path.exists")
    @patch("os.listdir")
    @patch("os.path.isdir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions(
        self, mock_rmtree, mock_isdir, mock_listdir, mock_exists, downloader
    ):
        """Test cleanup of old Android versions."""
        # Setup filesystem mocks
        mock_exists.return_value = True
        mock_listdir.return_value = ["v1.0.0", "v2.0.0", "v3.0.0", "not_version"]
        mock_isdir.return_value = True

        downloader.cleanup_old_versions(keep_limit=2)

        # Should remove oldest version (v1.0.0)
        mock_rmtree.assert_called_once()
        args = mock_rmtree.call_args[0][0]
        assert "v1.0.0" in args

    def test_is_version_directory(self, downloader):
        """Test version directory detection."""
        assert downloader._is_version_directory("v1.0.0") is True
        assert downloader._is_version_directory("v1.0") is True
        assert downloader._is_version_directory("not_version") is False

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
        mock_json_load.return_value = {"latest_version": "v1.0.0"}

        tag = downloader.get_latest_release_tag()

        assert tag == "v1.0.0"

    @patch("os.path.exists")
    def test_get_latest_release_tag_no_file(self, mock_exists, downloader):
        """Test getting latest release tag when file doesn't exist."""
        mock_exists.return_value = False

        tag = downloader.get_latest_release_tag()

        assert tag is None

    @patch("fetchtastic.download.android.datetime")
    def test_update_latest_release_tag(self, mock_datetime, downloader):
        """Test updating latest release tag."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        # Mock atomic write
        downloader.cache_manager.atomic_write_json = Mock(return_value=True)

        result = downloader.update_latest_release_tag("v1.0.0")

        assert result is True
        downloader.cache_manager.atomic_write_json.assert_called_once()

    def test_get_current_iso_timestamp(self, downloader):
        """Test ISO timestamp generation."""
        with patch("fetchtastic.download.android.datetime") as mock_datetime:
            mock_now = Mock()
            mock_now.isoformat.return_value = "2023-01-01T12:00:00"
            mock_datetime.now.return_value = mock_now
            mock_datetime.timezone.utc = Mock()

            timestamp = downloader._get_current_iso_timestamp()

            assert timestamp == "2023-01-01T12:00:00"

    def test_validate_extraction_patterns(self, downloader):
        """Test extraction pattern validation."""
        # APK downloader doesn't support extraction
        result = downloader.validate_extraction_patterns(["*.zip"], ["*.tmp"])
        assert result is False

    def test_check_extraction_needed(self, downloader):
        """Test extraction needed check."""
        # APK downloader doesn't support extraction
        result = downloader.check_extraction_needed(
            "/tmp/test.apk", "/tmp", ["*.zip"], ["*.tmp"]
        )
        assert result is False

    def test_should_download_prerelease_enabled(self, downloader):
        """Test prerelease download decision with prereleases enabled."""
        downloader.config["CHECK_APK_PRERELEASES"] = True

        result = downloader.should_download_prerelease("v1.0.0-beta")

        assert result is True

    def test_should_download_prerelease_disabled(self, downloader):
        """Test prerelease download decision with prereleases disabled."""
        downloader.config["CHECK_APK_PRERELEASES"] = False

        result = downloader.should_download_prerelease("v1.0.0-beta")

        assert result is False

    def test_get_prerelease_tracking_file(self, downloader):
        """Test prerelease tracking file path generation."""
        path = downloader.get_prerelease_tracking_file()

        assert "prerelease_tracking_android.json" in path

    @patch("fetchtastic.download.android.datetime")
    def test_update_prerelease_tracking(self, mock_datetime, downloader):
        """Test updating prerelease tracking."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        downloader.cache_manager.atomic_write_json = Mock(return_value=True)

        result = downloader.update_prerelease_tracking("v1.0.0-beta")

        assert result is True

    def test_manage_prerelease_tracking_files(self, downloader):
        """Test prerelease tracking file management."""
        # Mock the prerelease manager
        downloader.prerelease_manager.cleanup_old_prereleases = Mock()

        downloader.manage_prerelease_tracking_files()

        downloader.prerelease_manager.cleanup_old_prereleases.assert_called_once()

    def test_is_apk_prerelease_by_name(self):
        """Test APK prerelease detection by name."""
        from fetchtastic.download.android import _is_apk_prerelease_by_name

        assert _is_apk_prerelease_by_name("v1.0.0-alpha") is True
        assert _is_apk_prerelease_by_name("v1.0.0") is False
        assert _is_apk_prerelease_by_name("v1.0.0-rc1") is True

    def test_is_apk_prerelease_release_dict(self):
        """Test APK prerelease detection from release dict."""
        from fetchtastic.download.android import _is_apk_prerelease

        release_data = {"prerelease": True, "tag_name": "v1.0.0-beta"}
        assert _is_apk_prerelease(release_data) is True

        release_data = {"prerelease": False, "tag_name": "v1.0.0"}
        assert _is_apk_prerelease(release_data) is False

    @patch("fetchtastic.download.android.logger")
    def test_handle_prereleases_with_tracking(self, mock_logger, downloader):
        """Test prerelease handling with tracking updates."""
        # Mock prerelease data
        prerelease_releases = [
            Mock(spec=Release, tag_name="v1.0.0-beta", prerelease=True)
        ]

        # Mock existing releases check
        downloader.get_existing_releases = Mock(return_value=[])
        downloader.should_download_prerelease = Mock(return_value=True)
        downloader.download_apk = Mock(
            return_value=Mock(spec=DownloadResult, success=True)
        )
        downloader.update_prerelease_tracking = Mock(return_value=True)

        downloader.handle_prereleases(prerelease_releases, "universal")

        # Should attempt download and update tracking
        downloader.download_apk.assert_called_once()
        downloader.update_prerelease_tracking.assert_called_once_with("v1.0.0-beta")
