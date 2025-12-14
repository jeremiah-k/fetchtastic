"""
Comprehensive tests for FirmwareReleaseDownloader functionality.

This module tests the core firmware download behaviors that were
previously handled by the legacy downloader module, ensuring they work
correctly with the new modular architecture.
"""

import json
import os
from pathlib import Path
from unittest import mock
from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release


@pytest.fixture
def test_config():
    """Test configuration for firmware downloader."""
    return {
        "DOWNLOAD_DIR": "/tmp/test_firmware",
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "SELECTED_PATTERNS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "GITHUB_TOKEN": "test_token",
        "CHECK_FIRMWARE_PRERELEASES": True,
    }


@pytest.fixture
def firmware_downloader(test_config):
    """Firmware release downloader instance."""
    return FirmwareReleaseDownloader(test_config)


class TestFirmwareReleaseDownloader:
    """Test suite for FirmwareReleaseDownloader functionality."""

    def test_initialization(self, test_config):
        """Test firmware downloader initialization."""
        downloader = FirmwareReleaseDownloader(test_config)
        assert downloader.download_dir == test_config["DOWNLOAD_DIR"]
        assert downloader.config == test_config

    def test_get_target_path_for_release(self, firmware_downloader):
        """Test getting target path for firmware release."""
        release_tag = "v2.7.14"
        file_name = "firmware-rak4631-2.7.14.bin"

        target_path = firmware_downloader.get_target_path_for_release(
            release_tag, file_name
        )

        expected_path = os.path.join(
            firmware_downloader.download_dir, "firmware", release_tag, file_name
        )
        assert target_path == expected_path

    def test_get_assets(self, firmware_downloader):
        """Test getting assets from a release."""
        release = Release(
            tag_name="v2.7.14",
            prerelease=False,
            published_at="2025-01-20T12:00:00Z",
        )
        asset = Asset(
            name="firmware-rak4631-2.7.14.bin",
            download_url="https://example.com/firmware.bin",
            size=1024000,
        )
        release.assets.append(asset)

        assets = firmware_downloader.get_assets(release)

        assert len(assets) == 1
        assert assets[0].name == "firmware-rak4631-2.7.14.bin"

    def test_get_download_url(self, firmware_downloader):
        """Test getting download URL for an asset."""
        asset = Asset(
            name="firmware-rak4631-2.7.14.bin",
            download_url="https://example.com/firmware.bin",
            size=1024000,
        )

        download_url = firmware_downloader.get_download_url(asset)

        assert download_url == "https://example.com/firmware.bin"

    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.download")
    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.verify")
    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.is_asset_complete")
    def test_download_firmware_success(
        self, mock_is_complete, mock_verify, mock_download, firmware_downloader
    ):
        """Test successful firmware download."""
        mock_is_complete.return_value = False
        mock_download.return_value = True
        mock_verify.return_value = True

        release = Release(tag_name="v2.7.14", prerelease=False)
        asset = Asset(
            name="firmware-rak4631-2.7.14.bin",
            download_url="https://example.com/firmware.bin",
            size=1024000,
        )

        result = firmware_downloader.download_firmware(release, asset)

        assert result.success is True
        assert result.release_tag == "v2.7.14"
        assert result.file_type == "firmware"

    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.download")
    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.verify")
    @patch("fetchtastic.download.firmware.FirmwareReleaseDownloader.is_asset_complete")
    def test_download_firmware_already_exists(
        self, mock_is_complete, mock_verify, mock_download, firmware_downloader
    ):
        """Test firmware download when file already exists."""
        mock_is_complete.return_value = True

        release = Release(tag_name="v2.7.14", prerelease=False)
        asset = Asset(
            name="firmware-rak4631-2.7.14.bin",
            download_url="https://example.com/firmware.bin",
            size=1024000,
        )

        result = firmware_downloader.download_firmware(release, asset)

        assert result.success is True
        assert result.was_skipped is True
        assert result.file_type == "firmware"

    def test_cleanup_old_versions(self, firmware_downloader, tmp_path):
        """Test cleanup of old firmware versions."""
        firmware_downloader.download_dir = str(tmp_path)

        # Create multiple version directories
        for version in ["v2.7.10", "v2.7.11", "v2.7.12", "v2.7.13", "v2.7.14"]:
            version_dir = tmp_path / "firmware" / version
            version_dir.mkdir(parents=True)

        firmware_downloader.cleanup_old_versions(keep_limit=2)

        # Should keep 2 newest versions
        remaining_dirs = list((tmp_path / "firmware").iterdir())
        assert len(remaining_dirs) == 2

        # Check that the newest versions are kept
        remaining_names = [d.name for d in remaining_dirs]
        assert "v2.7.14" in remaining_names
        assert "v2.7.13" in remaining_names

    def test_get_latest_release_tag(self, firmware_downloader):
        """Test getting the latest release tag."""
        mock_data = {"latest_version": "v2.7.14"}

        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock.mock_open(read_data=json.dumps(mock_data))),
        ):
            latest_tag = firmware_downloader.get_latest_release_tag()

            assert latest_tag == "v2.7.14"

    def test_get_latest_release_tag_no_file(self, firmware_downloader):
        """Test getting latest release tag when file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            latest_tag = firmware_downloader.get_latest_release_tag()

            assert latest_tag is None

    def test_should_download_prerelease_disabled(self, firmware_downloader):
        """Test prerelease download check when prereleases are disabled."""
        firmware_downloader.config["CHECK_FIRMWARE_PRERELEASES"] = False

        result = firmware_downloader.should_download_prerelease("v2.7.15-rc1")

        assert result is False

    def test_should_download_prerelease_enabled(self, firmware_downloader):
        """Test prerelease download check when prereleases are enabled."""
        firmware_downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True

        with patch("os.path.exists", return_value=False):
            result = firmware_downloader.should_download_prerelease("v2.7.15-rc1")

            assert result is True

    def test_cleanup_superseded_prereleases(self, firmware_downloader, tmp_path):
        """Test cleanup of superseded prereleases."""
        firmware_downloader.download_dir = str(tmp_path)
        latest_release_tag = "v2.7.14"

        # Create prerelease directory structure
        prerelease_dir = tmp_path / "firmware" / "prerelease"
        prerelease_dir.mkdir(parents=True)

        # Create some old prerelease directories
        old_prerelease = prerelease_dir / "firmware-2.7.12.abcdef"
        old_prerelease.mkdir()

        result = firmware_downloader.cleanup_superseded_prereleases(latest_release_tag)

        # Should return boolean indicating if cleanup was performed
        assert isinstance(result, bool)

    def test_get_prerelease_tracking_file(self, firmware_downloader):
        """Test getting prerelease tracking file path."""
        tracking_file = firmware_downloader.get_prerelease_tracking_file()

        expected_path = os.path.join(
            firmware_downloader.download_dir, firmware_downloader.latest_prerelease_file
        )
        assert tracking_file == expected_path

    def test_handle_prereleases(self, firmware_downloader):
        """Test prerelease handling functionality."""
        firmware_downloader.config["CHECK_FIRMWARE_PRERELEASES"] = True

        # Create some releases including prereleases
        releases = [
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.15-rc1", prerelease=True),
            Release(tag_name="v2.7.15-rc2", prerelease=True),
        ]

        filtered_prereleases = firmware_downloader.handle_prereleases(releases)

        # Should return prereleases
        assert len(filtered_prereleases) == 2
        assert all(r.prerelease for r in filtered_prereleases)

    def test_static_check_and_download(self, tmp_path):
        """Test backward compatibility static method."""
        mock_releases = [
            {
                "tag_name": "v2.7.14",
                "prerelease": False,
                "assets": [
                    {
                        "name": "test.bin",
                        "browser_download_url": "https://example.com/test.bin",
                        "size": 1000,
                    }
                ],
            }
        ]

        # Test static method exists and works
        result = FirmwareReleaseDownloader.check_and_download(
            releases=mock_releases,
            cache_dir=str(tmp_path / "cache"),
            release_type="Firmware",
            download_dir=str(tmp_path),
            versions_to_keep=2,
            selected_patterns=["test"],
        )

        # Should return tuple of (downloaded, new_versions, failures)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_error_handling_api_failure(self, firmware_downloader):
        """Test error handling with API failures."""
        with patch(
            "fetchtastic.download.firmware.make_github_api_request",
            side_effect=Exception("API Error"),
        ):
            releases = firmware_downloader.get_releases()
            assert releases == []

    def test_configuration_persistence(self, firmware_downloader):
        """Test that configuration is properly stored and accessible."""
        assert firmware_downloader.config is not None
        assert "DOWNLOAD_DIR" in firmware_downloader.config
        assert "FIRMWARE_VERSIONS_TO_KEEP" in firmware_downloader.config

    def test_file_operations_integration(self, firmware_downloader):
        """Test integration with file operations."""
        # Test that file_operations attribute exists
        assert hasattr(firmware_downloader, "file_operations")
        assert firmware_downloader.file_operations is not None
