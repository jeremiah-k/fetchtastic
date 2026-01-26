# Test Android Downloader
#
# Comprehensive unit tests for the MeshtasticAndroidAppDownloader class.

import json
import os
from pathlib import Path
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest
import requests

from fetchtastic.constants import (
    APK_PRERELEASES_DIR_NAME,
    APKS_DIR_NAME,
    FILE_TYPE_ANDROID_PRERELEASE,
)
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads, pytest.mark.user_interface]


def _scandir_context(entries):
    """
    Create a context manager mock that yields the provided iterable of directory entries.

    Parameters:
        entries (iterable): Sequence or iterator to be returned by the context manager's __enter__.

    Returns:
        MagicMock: A mock context manager whose __enter__ returns `entries` and whose __exit__ returns False (does not suppress exceptions).
    """
    context = MagicMock()
    context.__enter__.return_value = entries
    context.__exit__.return_value = False
    return context


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
        """
        Constructs a MeshtasticAndroidAppDownloader preconfigured for tests with mocked collaborators.

        Creates a downloader using the provided mock configuration and cache manager, then replaces
        its runtime collaborators with test doubles: `cache_manager` is set to the provided mock,
        `version_manager` and `file_operations` are replaced with mocks, and the mock
        `version_manager` is delegated to a real VersionManager for `get_release_tuple` and
        `is_prerelease_version` behavior.

        Parameters:
            mock_config (dict): Configuration dictionary used to initialize the downloader.
            mock_cache_manager (Mock): Mocked CacheManager providing cache_dir and get_cache_file_path.

        Returns:
            MeshtasticAndroidAppDownloader: Downloader instance wired with the test doubles.
        """
        dl = MeshtasticAndroidAppDownloader(mock_config, mock_cache_manager)
        # Mock the dependencies that are set in __init__
        dl.cache_manager = mock_cache_manager
        dl.version_manager = Mock()
        dl.file_operations = Mock()
        real_version_manager = VersionManager()
        dl.version_manager.get_release_tuple.side_effect = (
            real_version_manager.get_release_tuple
        )
        dl.version_manager.is_prerelease_version.side_effect = (
            real_version_manager.is_prerelease_version
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

    def test_update_release_history_empty_returns_none(self, downloader):
        """Empty release lists should return None."""
        assert downloader.update_release_history([]) is None

    def test_update_release_history_logs_summary(self, downloader):
        """Android history updates should emit status summaries."""
        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )
        downloader.release_history_manager.log_release_status_summary = Mock()

        history = downloader.update_release_history([Release(tag_name="v1.0.0")])

        assert history == {"entries": {}}
        downloader.release_history_manager.log_release_status_summary.assert_called_once()

    def test_update_release_history_filters_prereleases(self, downloader):
        """Prereleases should be filtered out from release history tracking."""
        stable_release = Release(tag_name="v2.7.11", prerelease=False)
        prerelease1 = Release(tag_name="v2.7.11-open.1", prerelease=True)
        prerelease2 = Release(tag_name="v2.7.11-closed.1", prerelease=True)

        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )

        history = downloader.update_release_history(
            [stable_release, prerelease1, prerelease2]
        )

        assert history == {"entries": {}}
        downloader.release_history_manager.update_release_history.assert_called_once_with(
            [stable_release]
        )

    def test_update_release_history_filters_legacy_prereleases(self, downloader):
        """Legacy -open/-closed prereleases should be filtered even when prerelease flag is False."""
        stable_release = Release(tag_name="v2.7.11", prerelease=False)
        legacy_prerelease1 = Release(tag_name="v2.7.11-open.1", prerelease=False)
        legacy_prerelease2 = Release(tag_name="v2.7.11-closed.2", prerelease=False)

        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )

        history = downloader.update_release_history(
            [stable_release, legacy_prerelease1, legacy_prerelease2]
        )

        assert history == {"entries": {}}
        downloader.release_history_manager.update_release_history.assert_called_once_with(
            [stable_release]
        )

    def test_update_release_history_returns_none_with_only_prereleases(
        self, downloader
    ):
        """Release history should return None when only prereleases are provided."""
        prerelease1 = Release(tag_name="v2.7.11-open.1", prerelease=True)
        prerelease2 = Release(tag_name="v2.7.11-closed.1", prerelease=True)

        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )

        history = downloader.update_release_history([prerelease1, prerelease2])

        assert history is None
        downloader.release_history_manager.update_release_history.assert_not_called()

    def test_update_release_history_returns_none_with_only_legacy_prereleases(
        self, downloader
    ):
        """Release history should return None when only legacy prereleases are provided."""
        legacy_prerelease1 = Release(tag_name="v2.7.11-open.1", prerelease=False)
        legacy_prerelease2 = Release(tag_name="v2.7.11-closed.1", prerelease=False)

        downloader.release_history_manager.update_release_history = Mock(
            return_value={"entries": {}}
        )

        history = downloader.update_release_history(
            [legacy_prerelease1, legacy_prerelease2]
        )

        assert history is None
        downloader.release_history_manager.update_release_history.assert_not_called()

    def test_format_release_log_suffix(self, downloader):
        """Release log suffixes should omit channel details for Android."""
        downloader.release_history_manager.format_release_label = Mock(
            return_value="v1.0.0"
        )
        release = Release(tag_name="v1.0.0", prerelease=False)

        assert downloader.format_release_log_suffix(release) == ""

    def test_ensure_release_notes_unsafe_tag(self, downloader):
        """Unsafe tags should skip Android release note writes."""
        release = Release(tag_name="../v1.0.0", prerelease=False, body="notes")

        assert downloader.ensure_release_notes(release) is None

    def test_ensure_release_notes_writes_file(self, tmp_path):
        """Release notes should be written alongside Android APK assets."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)
        release = Release(
            tag_name="v2.7.10",
            prerelease=False,
            body="Android release notes",
        )

        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        notes_file = Path(notes_path)
        assert notes_file.exists()
        assert str(notes_file).endswith(
            os.path.join(APKS_DIR_NAME, "v2.7.10", "release_notes-v2.7.10.md")
        )

    def test_ensure_release_notes_prerelease_dir(self, tmp_path):
        """Prerelease APK notes should live under the prerelease directory."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {"DOWNLOAD_DIR": str(tmp_path / "downloads")}
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)
        release = Release(
            tag_name="v2.7.10-open.1",
            prerelease=True,
            body="Prerelease notes",
        )

        notes_path = downloader.ensure_release_notes(release)

        assert notes_path is not None
        notes_file = Path(notes_path)
        assert notes_file.exists()
        expected_suffix = os.path.join(
            APKS_DIR_NAME,
            APK_PRERELEASES_DIR_NAME,
            "v2.7.10-open.1",
            "release_notes-v2.7.10-open.1.md",
        )
        assert str(notes_file).endswith(expected_suffix)

    def test_get_target_path_for_release(self, downloader, tmp_path):
        """Test target path generation for APK releases."""
        path = downloader.get_target_path_for_release("v1.0.0", "meshtastic.apk")

        expected = os.path.join(
            str(tmp_path / "downloads"), APKS_DIR_NAME, "v1.0.0", "meshtastic.apk"
        )
        assert path == expected

    def test_get_target_path_for_prerelease(self, downloader, tmp_path):
        """Test target path generation for APK prereleases."""
        path = downloader.get_target_path_for_release(
            "v2.7.10-open.1", "meshtastic.apk"
        )

        expected = os.path.join(
            str(tmp_path / "downloads"),
            APKS_DIR_NAME,
            APK_PRERELEASES_DIR_NAME,
            "v2.7.10-open.1",
            "meshtastic.apk",
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

    def test_download_apk_prerelease_uses_prerelease_dir(self, downloader):
        """Test prerelease APKs are stored under the prerelease directory."""
        release = Release(tag_name="v2.7.10-open.1", prerelease=True)
        asset = Asset(
            name="meshtastic.apk",
            download_url="https://example.com/meshtastic.apk",
            size=100,
        )

        downloader._is_asset_complete_for_target = Mock(return_value=False)
        downloader.download = Mock(return_value=True)
        downloader.verify = Mock(return_value=True)

        result = downloader.download_apk(release, asset)

        expected = os.path.join(
            downloader.download_dir,
            APKS_DIR_NAME,
            APK_PRERELEASES_DIR_NAME,
            release.tag_name,
            asset.name,
        )
        assert str(result.file_path) == expected
        assert result.file_type == FILE_TYPE_ANDROID_PRERELEASE

    def test_is_asset_complete_for_target_size_mismatch(self, downloader):
        """Test asset completeness fails on size mismatch."""
        asset = Asset(
            name="app.apk",
            download_url="https://example.com/app.apk",
            size=100,
        )

        downloader.file_operations.get_file_size.return_value = 99
        downloader.verify = Mock()
        with patch("fetchtastic.download.android.os.path.exists", return_value=True):
            result = downloader._is_asset_complete_for_target("/tmp/app.apk", asset)

        assert result is False
        downloader.verify.assert_not_called()

    def test_is_asset_complete_for_target_verify_failure(self, downloader):
        """Test asset completeness fails when verification fails."""
        asset = Asset(
            name="app.apk",
            download_url="https://example.com/app.apk",
            size=100,
        )

        downloader.file_operations.get_file_size.return_value = 100
        downloader.verify = Mock(return_value=False)
        with patch("fetchtastic.download.android.os.path.exists", return_value=True):
            result = downloader._is_asset_complete_for_target("/tmp/app.apk", asset)

        assert result is False

    def test_is_asset_complete_for_target_zip_integrity_failure(self, downloader):
        """Test asset completeness fails when ZIP integrity fails."""
        asset = Asset(
            name="app.zip",
            download_url="https://example.com/app.zip",
            size=100,
        )

        downloader.file_operations.get_file_size.return_value = 100
        downloader.verify = Mock(return_value=True)
        downloader._is_zip_intact = Mock(return_value=False)
        with patch("fetchtastic.download.android.os.path.exists", return_value=True):
            result = downloader._is_asset_complete_for_target("/tmp/app.zip", asset)

        assert result is False
        downloader._is_zip_intact.assert_called_once_with("/tmp/app.zip")

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

    def test_cleanup_old_versions_delegates_to_prerelease_cleanup(self, downloader):
        """Test cleanup_old_versions delegates to prerelease-aware cleanup."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]
        downloader.cleanup_prerelease_directories = Mock()
        downloader.get_releases = Mock(return_value=releases)

        downloader.cleanup_old_versions(keep_limit=2)

        downloader.cleanup_prerelease_directories.assert_called_once_with(
            cached_releases=releases, keep_limit_override=2
        )

    def test_cleanup_prerelease_directories_removes_unexpected_entries(self, tmp_path):
        """Test unexpected entries are removed from APK directories."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "CHECK_APK_PRERELEASES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )

        prerelease_tag = "v2.7.10-open.1"
        misplaced = tmp_path / APKS_DIR_NAME / prerelease_tag
        misplaced.mkdir(parents=True)
        expected_dir = (
            tmp_path / APKS_DIR_NAME / APK_PRERELEASES_DIR_NAME / prerelease_tag
        )
        expected_dir.mkdir(parents=True)

        stable_dir = tmp_path / APKS_DIR_NAME / "v2.7.9"
        stable_dir.mkdir(parents=True)

        releases = [
            Release(tag_name="v2.7.9", prerelease=False),
            Release(tag_name=prerelease_tag, prerelease=True),
        ]

        downloader.cleanup_prerelease_directories(cached_releases=releases)

        assert not misplaced.exists()
        assert expected_dir.exists()
        assert stable_dir.exists()

    def test_cleanup_prerelease_directories_skips_when_keep_set_mismatched(
        self, tmp_path, mocker
    ):
        """Cleanup should skip when expected tags do not match existing entries."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "CHECK_APK_PRERELEASES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )

        android_dir = tmp_path / APKS_DIR_NAME
        android_dir.mkdir(parents=True)
        mismatched_dir = android_dir / "v1.0.0-alpha"
        mismatched_dir.mkdir()

        releases = [Release(tag_name="v1.0.0", prerelease=False)]

        mock_logger = mocker.patch("fetchtastic.download.android.logger")
        mock_rmtree = mocker.patch("fetchtastic.download.android._safe_rmtree")

        downloader.cleanup_prerelease_directories(cached_releases=releases)

        assert mismatched_dir.exists()
        mock_rmtree.assert_not_called()
        assert any(
            "keep set does not match" in str(call.args[0]).lower()
            for call in mock_logger.method_calls
            if call.args
        )

    def test_cleanup_prerelease_directories_skips_without_stable_releases(
        self, tmp_path, mocker
    ):
        """Cleanup should bail early when only prereleases are present."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "CHECK_APK_PRERELEASES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )

        # Ensure the base APK directory exists so cleanup logic proceeds.
        (tmp_path / APKS_DIR_NAME).mkdir(parents=True)

        # Provide only prerelease entries so the stable list is empty.
        releases = [
            Release(tag_name="v2.7.10-open.1", prerelease=True),
            Release(tag_name="v2.7.10-open.2", prerelease=True),
        ]

        mock_logger = mocker.patch("fetchtastic.download.android.logger")

        downloader.cleanup_prerelease_directories(cached_releases=releases)

        # Ensure we hit the early-return path and logged the reason.
        assert any(
            "no stable releases" in str(call.args[0]).lower()
            for call in mock_logger.method_calls
            if call.args
        )

    def test_cleanup_prerelease_directories_removes_superseded_prereleases(
        self, tmp_path
    ):
        """Test superseded prerelease directories are removed."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "CHECK_APK_PRERELEASES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )

        root_prerelease = tmp_path / APKS_DIR_NAME / "v2.7.10-open.1"
        root_prerelease.mkdir(parents=True)
        prerelease_dir = (
            tmp_path / APKS_DIR_NAME / APK_PRERELEASES_DIR_NAME / "v2.7.10-open.2"
        )
        prerelease_dir.mkdir(parents=True)
        misplaced_stable = (
            tmp_path / APKS_DIR_NAME / APK_PRERELEASES_DIR_NAME / "v2.7.9"
        )
        misplaced_stable.mkdir(parents=True)
        user_dir = tmp_path / APKS_DIR_NAME / APK_PRERELEASES_DIR_NAME / "notes"
        user_dir.mkdir(parents=True)
        stable_dir = tmp_path / APKS_DIR_NAME / "v2.7.10"
        stable_dir.mkdir(parents=True)

        releases = [
            Release(tag_name="v2.7.10", prerelease=False),
            Release(tag_name="v2.7.10-open.1", prerelease=True),
            Release(tag_name="v2.7.10-open.2", prerelease=True),
        ]

        downloader.cleanup_prerelease_directories(cached_releases=releases)

        assert not root_prerelease.exists()
        assert not prerelease_dir.exists()
        assert not misplaced_stable.exists()
        assert not user_dir.exists()
        assert stable_dir.exists()

    def test_cleanup_prerelease_directories_sorts_stable_releases(self, tmp_path):
        """Test cleanup keeps the newest stable releases by version."""
        config = {
            "DOWNLOAD_DIR": str(tmp_path),
            "CHECK_APK_PRERELEASES": True,
            "ANDROID_VERSIONS_TO_KEEP": 2,
        }
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )

        v27_9 = tmp_path / APKS_DIR_NAME / "v2.7.9"
        v27_9.mkdir(parents=True)
        v27_10 = tmp_path / APKS_DIR_NAME / "v2.7.10"
        v27_10.mkdir(parents=True)
        v28_0 = tmp_path / APKS_DIR_NAME / "v2.8.0"
        v28_0.mkdir(parents=True)

        releases = [
            Release(tag_name="v2.7.9", prerelease=False),
            Release(tag_name="v2.8.0", prerelease=False),
            Release(tag_name="v2.7.10", prerelease=False),
        ]

        downloader.cleanup_prerelease_directories(cached_releases=releases)

        assert not v27_9.exists()
        assert v27_10.exists()
        assert v28_0.exists()

    def test_cleanup_prerelease_directories_returns_without_cached_releases(
        self, downloader
    ):
        """Test cleanup skips work without cached releases."""
        with patch("fetchtastic.download.android.os.path.exists") as mock_exists:
            downloader.cleanup_prerelease_directories(cached_releases=None)

        mock_exists.assert_not_called()

    def test_cleanup_prerelease_directories_returns_when_android_dir_missing(
        self, downloader
    ):
        """Test cleanup returns when APK directory is missing."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]
        android_dir = os.path.join(downloader.download_dir, APKS_DIR_NAME)

        with patch(
            "fetchtastic.download.android.os.path.exists", return_value=False
        ) as mock_exists:
            downloader.cleanup_prerelease_directories(cached_releases=releases)

        mock_exists.assert_called_once_with(android_dir)

    def test_cleanup_prerelease_directories_warns_on_unsafe_tags(self, tmp_path):
        """Test cleanup warns on unsafe release and prerelease tags."""
        config = {"DOWNLOAD_DIR": str(tmp_path), "CHECK_APK_PRERELEASES": True}
        downloader = MeshtasticAndroidAppDownloader(
            config, CacheManager(cache_dir=str(tmp_path / "cache"))
        )
        releases = [Release(tag_name="..", prerelease=False)]
        downloader.handle_prereleases = Mock(
            return_value=[Release(tag_name="..", prerelease=True)]
        )

        android_dir = os.path.join(downloader.download_dir, APKS_DIR_NAME)

        def _exists(path):
            """
            Determine whether the given path is the Android APK directory.

            Parameters:
                path (str): Filesystem path to test.

            Returns:
                True if `path` is equal to the configured Android APK directory path, False otherwise.
            """
            return path == android_dir

        with (
            patch("fetchtastic.download.android.logger") as mock_logger,
            patch("fetchtastic.download.android.os.path.exists", side_effect=_exists),
            patch(
                "fetchtastic.download.android.os.scandir",
                return_value=_scandir_context([]),
            ),
        ):
            downloader.cleanup_prerelease_directories(cached_releases=releases)

        warning_args = [call.args for call in mock_logger.warning.call_args_list]
        assert any(
            len(args) > 1
            and args[0].startswith("Skipping unsafe")
            and args[1] == "release"
            for args in warning_args
        )
        assert any(
            len(args) > 1
            and args[0].startswith("Skipping unsafe")
            and args[1] == "prerelease"
            for args in warning_args
        )

    def test_cleanup_prerelease_directories_removes_unexpected_and_skips_symlink(
        self, downloader
    ):
        """Test cleanup removes unexpected entries and skips symlinks."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]
        downloader.handle_prereleases = Mock(
            return_value=[Release(tag_name="v1.0.1-open.1", prerelease=True)]
        )

        android_dir = os.path.join(downloader.download_dir, APKS_DIR_NAME)
        prerelease_dir = os.path.join(android_dir, APK_PRERELEASES_DIR_NAME)

        entry_symlink = Mock()
        entry_symlink.name = "link"
        entry_symlink.path = "/tmp/link"
        entry_symlink.is_symlink.return_value = True

        entry_allowed = Mock()
        entry_allowed.name = "v1.0.0"
        entry_allowed.path = "/tmp/v1.0.0"
        entry_allowed.is_symlink.return_value = False

        entry_unexpected = Mock()
        entry_unexpected.name = "junk"
        entry_unexpected.path = "/tmp/junk"
        entry_unexpected.is_symlink.return_value = False

        pre_allowed = Mock()
        pre_allowed.name = "v1.0.1-open.1"
        pre_allowed.path = "/tmp/pre"
        pre_allowed.is_symlink.return_value = False

        pre_unexpected = Mock()
        pre_unexpected.name = "extra"
        pre_unexpected.path = "/tmp/extra"
        pre_unexpected.is_symlink.return_value = False

        with (
            patch("fetchtastic.download.android.os.path.exists", return_value=True),
            patch(
                "fetchtastic.download.android.os.scandir",
                side_effect=[
                    _scandir_context([entry_symlink, entry_allowed, entry_unexpected]),
                    _scandir_context([pre_allowed, pre_unexpected]),
                ],
            ),
            patch("fetchtastic.download.android._safe_rmtree") as mock_rmtree,
        ):
            downloader.cleanup_prerelease_directories(cached_releases=releases)

        mock_rmtree.assert_any_call(entry_unexpected.path, android_dir, "junk")
        mock_rmtree.assert_any_call(pre_unexpected.path, prerelease_dir, "extra")
        assert all(
            call.args[0] != entry_symlink.path for call in mock_rmtree.call_args_list
        )

    def test_cleanup_prerelease_directories_handles_missing_prerelease_dir(
        self, downloader
    ):
        """Test cleanup returns when prerelease directory is missing."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]
        android_dir = os.path.join(downloader.download_dir, APKS_DIR_NAME)

        def _exists(path):
            """
            Determine whether the given path is the Android APK directory.

            Parameters:
                path (str): Filesystem path to test.

            Returns:
                True if `path` is equal to the configured Android APK directory path, False otherwise.
            """
            return path == android_dir

        with (
            patch("fetchtastic.download.android.os.path.exists", side_effect=_exists),
            patch(
                "fetchtastic.download.android.os.scandir",
                return_value=_scandir_context([]),
            ) as mock_scandir,
        ):
            downloader.cleanup_prerelease_directories(cached_releases=releases)

        mock_scandir.assert_called_once_with(android_dir)

    def test_cleanup_prerelease_directories_handles_scandir_filenotfound(
        self, downloader
    ):
        """Test cleanup ignores FileNotFoundError during scans."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]

        with (
            patch("fetchtastic.download.android.os.path.exists", return_value=True),
            patch(
                "fetchtastic.download.android.os.scandir",
                side_effect=FileNotFoundError,
            ),
        ):
            downloader.cleanup_prerelease_directories(cached_releases=releases)

    def test_cleanup_prerelease_directories_logs_oserror(self, downloader):
        """Test cleanup logs unexpected OSError."""
        releases = [Release(tag_name="v1.0.0", prerelease=False)]

        with (
            patch("fetchtastic.download.android.logger") as mock_logger,
            patch("fetchtastic.download.android.os.path.exists", return_value=True),
            patch(
                "fetchtastic.download.android.os.scandir", side_effect=OSError("boom")
            ),
        ):
            downloader.cleanup_prerelease_directories(cached_releases=releases)

        mock_logger.error.assert_called_once()

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
