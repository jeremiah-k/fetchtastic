# Test Android Downloader
#
# Comprehensive unit tests for the MeshtasticAndroidAppDownloader class.

import json
import os
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import pytest
import requests

from fetchtastic.constants import APKS_DIR_NAME
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads, pytest.mark.user_interface]


class TestMeshtasticAndroidAppDownloader:
    """Test suite for MeshtasticAndroidAppDownloader."""

    @pytest.fixture
    def mock_config(self, tmp_path):
        """
        Provide a mock configuration dictionary used by tests.

        Returns:
            dict: Configuration with keys:
                DOWNLOAD_DIR (str): base download directory path.
                CHECK_APK_PRERELEASES (bool): whether APK prereleases should be considered.
                SELECTED_APK_ASSETS (list[str]): substrings used to select APK assets.
                EXCLUDE_PATTERNS (list[str]): glob patterns to exclude assets.
                GITHUB_TOKEN (str): placeholder GitHub API token.
        """
        return {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "CHECK_APK_PRERELEASES": True,
            "SELECTED_APK_ASSETS": ["universal"],
            "EXCLUDE_PATTERNS": ["*beta*"],
            "GITHUB_TOKEN": "test_token",
        }

    @pytest.fixture
    def mock_cache_manager(self, tmp_path):
        """
        Create a Mock configured to emulate CacheManager behavior for tests.

        The mock has its spec set to CacheManager, a `cache_dir` attribute pointing to a temporary cache directory, and `get_cache_file_path(file_name)` configured to return a path within that cache directory.

        Returns:
            Mock: A unittest.mock.Mock instance with spec=CacheManager and cache helpers configured.
        """
        mock = Mock(spec=CacheManager)
        mock.cache_dir = str(tmp_path / "cache")
        mock.get_cache_file_path.side_effect = lambda file_name: os.path.join(
            mock.cache_dir, file_name
        )
        return mock

    @pytest.fixture
    def downloader(self, mock_config, mock_cache_manager):
        """Create a MeshtasticAndroidAppDownloader instance with mocked dependencies."""
        dl = MeshtasticAndroidAppDownloader(mock_config, mock_cache_manager)
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
        with patch("fetchtastic.download.base.VersionManager") as mock_version:
            dl = MeshtasticAndroidAppDownloader(mock_config, mock_cache_manager)

            assert dl.config == mock_config
            assert (
                dl.android_releases_url
                == "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
            )
            assert dl.latest_release_file == "latest_android_release.json"
            mock_version.assert_called_once()

    def test_get_target_path_for_release(self, downloader, tmp_path):
        """Test target path generation for APK releases."""
        path = downloader.get_target_path_for_release("v1.0.0", "meshtastic.apk")

        expected = os.path.join(
            str(tmp_path / "downloads"), APKS_DIR_NAME, "v1.0.0", "meshtastic.apk"
        )
        assert path == expected

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_success(self, mock_request, downloader):
        """Test successful release fetching from GitHub."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "tag_name": "v2.7.0",
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

        # Mock cache manager to return None (cache miss) so API is called
        downloader.cache_manager.read_releases_cache_entry.return_value = None

        releases = downloader.get_releases(limit=10)

        assert len(releases) == 1
        assert releases[0].tag_name == "v2.7.0"
        assert releases[0].prerelease is False
        assert len(releases[0].assets) == 1
        assert releases[0].assets[0].name == "meshtastic.apk"

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_filters_legacy_android_tags(self, mock_request, downloader):
        """Legacy pre-2.7.0 tags should be skipped entirely."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "tag_name": "v2.6.9-open.1",
                "prerelease": False,
                "published_at": "2022-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "meshtastic.apk",
                        "browser_download_url": "https://example.com/old.apk",
                        "size": 1000000,
                    }
                ],
            },
            {
                "tag_name": "v2.7.0",
                "prerelease": False,
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "meshtastic.apk",
                        "browser_download_url": "https://example.com/new.apk",
                        "size": 1000000,
                    }
                ],
            },
        ]
        mock_request.return_value = mock_response
        downloader.cache_manager.read_releases_cache_entry.return_value = None

        releases = downloader.get_releases(limit=10)

        assert [release.tag_name for release in releases] == ["v2.7.0"]

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_marks_legacy_prerelease_by_tag(
        self, mock_request, downloader
    ):
        """Legacy -open/-closed tags should mark releases as prerelease."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "tag_name": "v2.7.1-open.1",
                "prerelease": False,
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "meshtastic.apk",
                        "browser_download_url": "https://example.com/pr.apk",
                        "size": 1000000,
                    }
                ],
            }
        ]
        mock_request.return_value = mock_response
        downloader.cache_manager.read_releases_cache_entry.return_value = None

        releases = downloader.get_releases(limit=10)

        assert len(releases) == 1
        assert releases[0].prerelease is True

    @patch("fetchtastic.download.android.make_github_api_request")
    def test_get_releases_api_error(self, mock_request, downloader):
        """Test handling of GitHub API errors."""
        mock_request.side_effect = requests.RequestException("API Error")

        # Force cache miss so the API is called and the exception path is exercised
        downloader.cache_manager.read_releases_cache_entry.return_value = None
        releases = downloader.get_releases()

        assert releases == []
        mock_request.assert_called_once()

    def test_get_assets_apk_only(self, downloader):
        """Test that only APK assets are returned."""
        release = Mock(spec=Release)
        asset1 = Mock(spec=Asset)
        asset1.name = "meshtastic.apk"
        asset1.download_url = "url1"
        asset1.size = 1000

        asset2 = Mock(spec=Asset)
        asset2.name = "meshtastic.aab"
        asset2.download_url = "url2"
        asset2.size = 2000

        asset3 = Mock(spec=Asset)
        asset3.name = "readme.txt"
        asset3.download_url = "url3"
        asset3.size = 100

        release.assets = [asset1, asset2, asset3]

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

    @patch("fetchtastic.download.base.utils.download_file_with_retry")
    @patch("os.path.exists")
    @patch("os.path.getsize")
    @patch("os.makedirs")
    def test_download_apk_already_complete(
        self, mock_makedirs, mock_getsize, mock_exists, mock_download, downloader
    ):
        """Test APK download skip when file already complete."""
        # Setup mocks
        mock_exists.return_value = True
        mock_getsize.return_value = 1000000

        release = Mock(spec=Release)
        release.tag_name = "v1.0.0"

        asset = Mock(spec=Asset)
        asset.name = "meshtastic.apk"
        asset.download_url = "https://example.com/meshtastic.apk"
        asset.size = 1000000

        # Mock verification
        downloader.verify = Mock(return_value=True)
        downloader.file_operations.get_file_size = Mock(return_value=1000000)

        result = downloader.download_apk(release, asset)

        assert result.success is True
        assert result.was_skipped is True
        assert result.release_tag == "v1.0.0"
        assert "meshtastic.apk" in str(result.file_path)
        mock_download.assert_not_called()

    @patch("fetchtastic.download.base.utils.download_file_with_retry")
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
    @patch("os.scandir")
    @patch("shutil.rmtree")
    def test_cleanup_old_versions(
        self, mock_rmtree, mock_scandir, mock_exists, downloader
    ):
        """Test cleanup of old Android versions."""
        # Setup filesystem mocks
        mock_exists.return_value = True

        # Create mock directory entries for os.scandir
        mock_v1 = Mock()
        mock_v1.name = "v1.0.0"
        mock_v1.is_symlink.return_value = False
        mock_v1.is_dir.return_value = True
        mock_v1.path = "/mock/android/v1.0.0"

        mock_v2 = Mock()
        mock_v2.name = "v2.0.0"
        mock_v2.is_symlink.return_value = False
        mock_v2.is_dir.return_value = True
        mock_v2.path = "/mock/android/v2.0.0"

        mock_v3 = Mock()
        mock_v3.name = "v3.0.0"
        mock_v3.is_symlink.return_value = False
        mock_v3.is_dir.return_value = True
        mock_v3.path = "/mock/android/v3.0.0"

        mock_not_version = Mock()
        mock_not_version.name = "not_version"
        mock_not_version.is_symlink.return_value = False
        mock_not_version.is_dir.return_value = True
        mock_not_version.path = "/mock/android/not_version"

        mock_scandir.return_value.__enter__.return_value = [
            mock_v1,
            mock_v2,
            mock_v3,
            mock_not_version,
        ]

        downloader.cleanup_old_versions(keep_limit=2)

        # Should remove oldest version (v1.0.0)
        mock_rmtree.assert_called_once()
        args = mock_rmtree.call_args[0][0]
        assert "v1.0.0" in args
        # Verify version manager was called to sort versions (exact count may vary)
        assert downloader.version_manager.get_release_tuple.call_count >= 1

    def test_is_version_directory(self, downloader):
        """Test version directory detection."""
        assert downloader._is_version_directory("v1.0.0") is True
        assert downloader._is_version_directory("v1.0") is True
        assert downloader._is_version_directory("not_version") is False

    @patch("fetchtastic.download.android.datetime")
    def test_update_latest_release_tag(self, mock_datetime, downloader, tmp_path):
        """Test updating latest release tag."""
        mock_datetime.now.return_value = Mock()
        mock_datetime.now.return_value.isoformat.return_value = "2023-01-01T00:00:00"

        # Mock atomic write
        downloader.cache_manager.atomic_write_json = Mock(return_value=True)
        downloader.cache_manager.get_cache_file_path.return_value = str(
            tmp_path / "cache" / "latest_android_release.json"
        )

        result = downloader.update_latest_release_tag("v1.0.0")

        assert result is True
        downloader.cache_manager.atomic_write_json.assert_called_once_with(
            str(tmp_path / "cache" / "latest_android_release.json"), ANY
        )

    def test_get_latest_release_tag_from_cache(self, mock_config, tmp_path):
        cache_manager = CacheManager(str(tmp_path))
        downloader = MeshtasticAndroidAppDownloader(mock_config, cache_manager)
        json_path = cache_manager.get_cache_file_path(downloader.latest_release_file)
        Path(json_path).write_text(json.dumps({"latest_version": "v1.0.0"}))

        assert downloader.get_latest_release_tag() == "v1.0.0"

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

    def test_check_extraction_needed(self, downloader, tmp_path):
        """Test extraction needed check."""
        # APK downloader doesn't support extraction
        result = downloader.check_extraction_needed(
            str(tmp_path / "test.apk"), str(tmp_path), ["*.zip"], ["*.tmp"]
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

        expected_path = downloader.cache_manager.get_cache_file_path(
            downloader.latest_prerelease_file
        )
        assert path == expected_path

    def test_update_prerelease_tracking(self, downloader):
        downloader.cache_manager.atomic_write_json = Mock(return_value=True)

        result = downloader.update_prerelease_tracking("v1.0.0-beta")

        assert result is True

    @patch("fetchtastic.download.android.PrereleaseHistoryManager")
    def test_manage_prerelease_tracking_files(
        self, mock_prerelease_manager_class, downloader
    ):
        """Test prerelease tracking file management."""
        # Mock the prerelease manager class
        mock_prerelease_manager = Mock()
        mock_prerelease_manager_class.return_value = mock_prerelease_manager

        # Mock config to enable prerelease checking
        downloader.config["CHECK_PRERELEASES"] = True

        # Mock directory existence check and cache manager read_json to return empty dict
        with (
            patch("os.path.exists", return_value=True),
            patch("os.listdir", return_value=[]),
            patch(
                "fetchtastic.download.android.MeshtasticAndroidAppDownloader.get_releases",
                return_value=[],
            ),
            patch.object(downloader.cache_manager, "read_json", return_value={}),
        ):
            downloader.manage_prerelease_tracking_files()

        mock_prerelease_manager.manage_prerelease_tracking_files.assert_called_once()

    def test_is_apk_prerelease_by_name(self):
        """Test legacy APK prerelease detection by name."""
        from fetchtastic.download.android import _is_apk_prerelease_by_name

        # Test legacy Meshtastic prerelease indicators
        assert _is_apk_prerelease_by_name("v1.0.0-open.1") is True
        assert _is_apk_prerelease_by_name("v1.0.0-closed.1") is True
        assert _is_apk_prerelease_by_name("v1.0.0-OPEN.1") is True  # Case insensitive

        # Test regular releases and standard prerelease indicators
        assert _is_apk_prerelease_by_name("v1.0.0") is False
        assert _is_apk_prerelease_by_name("v1.0.0-alpha") is False
        assert _is_apk_prerelease_by_name("v1.0.0-rc1") is False

    def test_is_apk_prerelease_release_dict(self):
        """Test APK prerelease detection from release dict."""
        from fetchtastic.download.android import _is_apk_prerelease

        release_data = {"prerelease": True, "tag_name": "v1.0.0-beta"}
        assert _is_apk_prerelease(release_data) is True

        release_data = {"prerelease": False, "tag_name": "v1.0.0"}
        assert _is_apk_prerelease(release_data) is False

    def test_handle_prereleases_with_tracking(self, downloader):
        """Test prerelease handling with tracking updates."""
        # Mock prerelease data - GitHub prereleases are identified by prerelease=True
        prerelease_releases = [
            Mock(
                spec=Release,
                tag_name="v1.0.1-beta",
                prerelease=True,
                published_at="2023-01-01T00:00:00Z",
            )
        ]
        stable_releases = [Mock(spec=Release, tag_name="v1.0.0", prerelease=False)]
        all_releases = stable_releases + prerelease_releases

        # Mock version manager for expected version calculation
        downloader.version_manager = Mock()
        downloader.version_manager.calculate_expected_prerelease_version.return_value = (
            "1.0.1"
        )
        downloader.version_manager.extract_clean_version.return_value = "v1.0.1"
        downloader.version_manager.filter_prereleases_by_pattern.return_value = [
            "v1.0.1-beta"
        ]

        result = downloader.handle_prereleases(all_releases)

        # Should return prereleases that match expected base version
        assert len(result) == 1
        assert result[0].tag_name == "v1.0.1-beta"
