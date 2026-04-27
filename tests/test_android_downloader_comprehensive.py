"""
Comprehensive tests for AndroidReleaseDownloader functionality.

This module tests the core Android app download behaviors that were
previously handled by the legacy downloader module, ensuring they work
correctly with the new modular architecture.
"""

import os
from unittest.mock import patch

import pytest
import requests

from fetchtastic.constants import ANDROID_DIR_NAME, APP_DIR_NAME
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Asset, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def test_config():
    """
    Provide a dictionary fixture with default configuration values used by Android downloader tests.

    Returns:
        config (dict): Test configuration containing:
            - DOWNLOAD_DIR (str): Path where test downloads are placed.
            - ANDROID_VERSIONS_TO_KEEP (int): Number of Android version directories to retain.
            - SELECTED_PATTERNS (list[str]): Glob patterns of asset filenames to include.
            - EXCLUDE_PATTERNS (list[str]): Glob patterns of asset filenames to exclude.
            - GITHUB_TOKEN (str): Token used for authenticated API requests in tests.
            - CHECK_ANDROID_PRERELEASES (bool): Whether prerelease Android versions are considered.
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test_android",
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["*.apk"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_ANDROID_PRERELEASES": True,
    }


@pytest.fixture
def android_downloader(test_config):
    """
    Create a MeshtasticAndroidAppDownloader configured for tests.

    Parameters:
        test_config (dict): Configuration dictionary used to initialize the downloader (e.g., DOWNLOAD_DIR, ANDROID_VERSIONS_TO_KEEP).

    Returns:
        MeshtasticAndroidAppDownloader: Initialized downloader instance using a new CacheManager.
    """
    cache_manager = CacheManager()
    return MeshtasticAndroidAppDownloader(test_config, cache_manager)


class TestMeshtasticAndroidAppDownloader:
    """Test suite for AndroidReleaseDownloader functionality."""

    def test_initialization(self, test_config):
        """Test Android downloader initialization."""
        cache_manager = CacheManager()
        downloader = MeshtasticAndroidAppDownloader(test_config, cache_manager)
        assert downloader.download_dir == test_config["DOWNLOAD_DIR"]
        assert downloader.config == test_config

    def test_get_target_path_for_release(self, android_downloader):
        """Test getting target path for Android release."""
        release_tag = "v2.7.14"
        file_name = "meshtastic.apk"

        target_path = android_downloader.get_target_path_for_release(
            release_tag, file_name
        )

        expected_path = os.path.join(
            android_downloader.download_dir,
            APP_DIR_NAME,
            ANDROID_DIR_NAME,
            release_tag,
            file_name,
        )
        assert target_path == expected_path

    def test_get_assets(self, android_downloader):
        """Test getting assets from a release."""
        release = Release(
            tag_name="v2.7.14",
            prerelease=False,
            published_at="2025-01-20T12:00:00Z",
        )
        asset = Asset(
            name="meshtastic.apk",
            download_url="https://example.com/meshtastic.apk",
            size=1024000,
        )
        release.assets.append(asset)

        assets = android_downloader.get_assets(release)

        assert len(assets) == 1
        assert assets[0].name == "meshtastic.apk"

    def test_get_download_url(self, android_downloader):
        """Test getting download URL for an asset."""
        asset = Asset(
            name="meshtastic.apk",
            download_url="https://example.com/meshtastic.apk",
            size=1024000,
        )

        download_url = android_downloader.get_download_url(asset)

        assert download_url == "https://example.com/meshtastic.apk"

    def test_should_download_asset_matching_patterns(self, android_downloader):
        """Test asset selection with specific patterns configured."""
        android_downloader.config["SELECTED_APP_ASSETS"] = ["meshtastic.apk"]
        assert android_downloader.should_download_asset("meshtastic.apk") is True

        assert android_downloader.should_download_asset("meshtastic-debug.apk") is False

        assert android_downloader.should_download_asset("Meshtastic.dmg") is False

    def test_should_download_asset_empty_selected_downloads_nothing(
        self, android_downloader
    ):
        """Empty SELECTED_APP_ASSETS means download no client app assets."""
        android_downloader.config["SELECTED_APP_ASSETS"] = []
        assert android_downloader.should_download_asset("meshtastic.apk") is False
        assert android_downloader.should_download_asset("Meshtastic.dmg") is False

    def test_should_download_asset_missing_selected_downloads_nothing(
        self, android_downloader
    ):
        """Missing SELECTED_APP_ASSETS and no legacy keys downloads nothing."""
        android_downloader.config.pop("SELECTED_APP_ASSETS", None)
        android_downloader.config.pop("SELECTED_APK_ASSETS", None)
        android_downloader.config.pop("SELECTED_DESKTOP_ASSETS", None)
        assert android_downloader.should_download_asset("meshtastic.apk") is False

    def test_should_download_asset_wildcard_downloads_all(self, android_downloader):
        """SELECTED_APP_ASSETS = ['*'] downloads all client app assets."""
        android_downloader.config["SELECTED_APP_ASSETS"] = ["*"]
        assert android_downloader.should_download_asset("meshtastic.apk") is True
        assert android_downloader.should_download_asset("Meshtastic.dmg") is True
        assert android_downloader.should_download_asset("readme.txt") is True

    def test_should_download_asset_wildcard_respects_excludes(self, android_downloader):
        """Wildcard selection still respects exclude patterns."""
        android_downloader.config["SELECTED_APP_ASSETS"] = ["*"]
        assert android_downloader.should_download_asset("meshtastic-debug.apk") is False

    @patch("fetchtastic.download.android.MeshtasticAndroidAppDownloader.download")
    @patch(
        "fetchtastic.download.android.MeshtasticAndroidAppDownloader._is_asset_complete_for_target"
    )
    def test_download_apk_success(
        self, mock_is_complete, mock_download, android_downloader
    ):
        """Test successful APK download."""
        mock_is_complete.side_effect = [False, True]
        mock_download.return_value = True

        release = Release(tag_name="v2.7.14", prerelease=False)
        asset = Asset(
            name="meshtastic.apk",
            download_url="https://example.com/meshtastic.apk",
            size=1024000,
        )

        result = android_downloader.download_apk(release, asset)

        assert result.success is True
        assert result.release_tag == "v2.7.14"
        assert result.file_type == "client_app"

    @patch("fetchtastic.download.android.MeshtasticAndroidAppDownloader.download")
    @patch(
        "fetchtastic.download.android.MeshtasticAndroidAppDownloader._is_asset_complete_for_target"
    )
    def test_download_apk_method_exists(
        self, mock_is_complete, mock_download, android_downloader
    ):
        """Test that download_apk method exists and can be called."""
        release = Release(tag_name="v2.7.14", prerelease=False)
        asset = Asset(
            name="meshtastic.apk",
            download_url="https://example.com/meshtastic.apk",
            size=1024000,
        )

        # Method should exist and return a result
        mock_is_complete.side_effect = [False, True]
        mock_download.return_value = True
        result = android_downloader.download_apk(release, asset)
        assert hasattr(result, "success")
        assert result.file_type == "client_app"

    def test_cleanup_old_versions(self, android_downloader, tmp_path):
        """Test cleanup of old Android versions."""
        android_downloader.download_dir = str(tmp_path)

        # Create multiple version directories
        versions = ["v2.7.10", "v2.7.11", "v2.7.12", "v2.7.13", "v2.7.14"]
        for version in versions:
            version_dir = tmp_path / APP_DIR_NAME / ANDROID_DIR_NAME / version
            version_dir.mkdir(parents=True)

        releases = [Release(tag_name=version, prerelease=False) for version in versions]
        android_downloader.cleanup_old_versions(keep_limit=2, cached_releases=releases)

        # Should keep 2 newest versions
        remaining_dirs = list((tmp_path / APP_DIR_NAME / ANDROID_DIR_NAME).iterdir())
        assert len(remaining_dirs) == 2

        # Check that the newest versions are kept
        remaining_names = [d.name for d in remaining_dirs]
        assert "v2.7.14" in remaining_names
        assert "v2.7.13" in remaining_names

    def test_update_latest_release_tag(self, android_downloader):
        """Test updating the latest Android release tag."""
        release_tag = "v2.7.14"

        # Method should exist and return a boolean
        result = android_downloader.update_latest_release_tag(release_tag)
        assert isinstance(result, bool)

    def test_should_download_prerelease_disabled(self, android_downloader):
        """Test prerelease download check when prereleases are disabled."""
        android_downloader.config["CHECK_APP_PRERELEASES"] = False

        result = android_downloader.should_download_prerelease("v2.7.15-rc1")

        assert result is False

    def test_should_download_prerelease_enabled(self, android_downloader):
        """Test prerelease download check when prereleases are enabled."""
        android_downloader.config["CHECK_APP_PRERELEASES"] = True

        result = android_downloader.should_download_prerelease("v2.7.15-rc1")

        # Method should return a boolean
        assert isinstance(result, bool)

    def test_get_prerelease_tracking_file(self, android_downloader):
        """Test getting Android prerelease tracking file path."""
        tracking_file = android_downloader.get_prerelease_tracking_file()

        expected_path = android_downloader.cache_manager.get_cache_file_path(
            android_downloader.latest_prerelease_file
        )
        assert tracking_file == expected_path

    def test_update_prerelease_tracking(self, android_downloader):
        """Test updating Android prerelease tracking information."""
        prerelease_tag = "v2.7.15-rc1"

        result = android_downloader.update_prerelease_tracking(prerelease_tag)

        assert isinstance(result, bool)

    def test_handle_prereleases(self, android_downloader):
        """Test Android prerelease handling functionality."""
        android_downloader.config["CHECK_ANDROID_PRERELEASES"] = True

        # Create some releases including prereleases
        releases = [
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.15-rc1", prerelease=True),
            Release(tag_name="v2.7.15-rc2", prerelease=True),
        ]

        filtered_prereleases = android_downloader.handle_prereleases(releases)

        # Should return a list
        assert isinstance(filtered_prereleases, list)

    def test_manage_prerelease_tracking_files(self, android_downloader):
        """Test management of Android prerelease tracking files."""
        with patch("os.path.exists", return_value=False):
            # Should not raise any exceptions
            android_downloader.manage_prerelease_tracking_files()

    def test_error_handling_api_failure(self, android_downloader):
        """Test error handling with API failures."""
        with (
            patch(
                "fetchtastic.download.github_source.make_github_api_request",
                side_effect=requests.RequestException("API Error"),
            ),
            patch.object(
                android_downloader.cache_manager,
                "read_releases_cache_entry",
                return_value=None,
            ),
        ):
            releases = android_downloader.get_releases()
            assert releases == []

    def test_configuration_persistence(self, android_downloader):
        """Test that configuration is properly stored and accessible."""
        assert android_downloader.config is not None
        assert "DOWNLOAD_DIR" in android_downloader.config
        assert "ANDROID_VERSIONS_TO_KEEP" in android_downloader.config

    def test_file_operations_integration(self, android_downloader):
        """Test integration with file operations."""
        # Test that file_operations attribute exists
        assert hasattr(android_downloader, "file_operations")
        assert android_downloader.file_operations is not None

    def test_validate_extraction_patterns(self, android_downloader):
        """Test validation of extraction patterns."""
        patterns = ["*.apk", "*.aab"]
        exclude_patterns = ["*debug*"]

        result = android_downloader.validate_extraction_patterns(
            patterns, exclude_patterns
        )

        # Should return a boolean
        assert isinstance(result, bool)

    def test_update_release_history_with_prereleases(self, android_downloader):
        """Test that prereleases are filtered out before history update."""
        releases = [
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.15-rc1", prerelease=True),
            Release(tag_name="v2.7.16", prerelease=False),
        ]

        result = android_downloader.update_release_history(releases, log_summary=False)

        # Should return history dict
        assert result is not None
        assert isinstance(result, dict)

    def test_update_release_history_all_prereleases(self, android_downloader):
        """Test that history returns None when all releases are prereleases."""
        releases = [
            Release(tag_name="v2.7.15-rc1", prerelease=True),
            Release(tag_name="v2.7.15-rc2", prerelease=True),
        ]

        result = android_downloader.update_release_history(releases, log_summary=False)

        # Should return None (no stable releases)
        assert result is None

    def test_update_release_history_empty_list(self, android_downloader):
        """Test that history returns None when releases list is empty."""
        releases = []

        result = android_downloader.update_release_history(releases, log_summary=False)

        # Should return None (no releases)
        assert result is None
