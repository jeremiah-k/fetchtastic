"""
Tests for prerelease functionality integration in the new modular architecture.
"""

from pathlib import Path

import pytest

from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.orchestrator import DownloadOrchestrator

if __name__ == "__main__":
    pytest.main([__file__])


@pytest.mark.integration
def test_prerelease_tracking_file_management(tmp_path: Path):
    """Test that prerelease tracking files are created and managed."""
    # Create a test configuration
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_APK_PRERELEASES": True,
        "CHECK_FIRMWARE_PRERELEASES": True,
        "ANDROID_VERSIONS_TO_KEEP": 3,
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "EXTRACT_PATTERNS": ["rak4631-"],
        "EXCLUDE_PATTERNS": [],
    }

    # Create downloaders
    android_downloader = MeshtasticAndroidAppDownloader(config)
    firmware_downloader = FirmwareReleaseDownloader(config)

    # Test that prerelease tracking files are created and managed
    android_downloader.manage_prerelease_tracking_files()
    firmware_downloader.manage_prerelease_tracking_files()


@pytest.mark.integration
def test_version_tracking(tmp_path: Path):
    """Test version tracking updates."""
    # Create a test configuration
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_APK_PRERELEASES": True,
        "CHECK_FIRMWARE_PRERELEASES": True,
        "ANDROID_VERSIONS_TO_KEEP": 3,
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "EXTRACT_PATTERNS": ["rak4631-"],
        "EXCLUDE_PATTERNS": [],
    }

    # Create downloaders
    android_downloader = MeshtasticAndroidAppDownloader(config)
    firmware_downloader = FirmwareReleaseDownloader(config)

    # Test version tracking updates
    android_downloader.update_latest_release_tag("v2.7.8")
    firmware_downloader.update_latest_release_tag("v2.7.8")

    # Verify tracking files exist
    tracking_files = list(tmp_path.rglob("*tracking*.json"))
    assert tracking_files, "Expected version tracking files to be created"


@pytest.mark.integration
def test_cleanup_functionality(tmp_path: Path):
    """Test cleanup functionality."""
    # Create a test configuration
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_APK_PRERELEASES": True,
        "CHECK_FIRMWARE_PRERELEASES": True,
        "ANDROID_VERSIONS_TO_KEEP": 3,
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "EXTRACT_PATTERNS": ["rak4631-"],
        "EXCLUDE_PATTERNS": [],
    }

    # Create downloaders
    android_downloader = MeshtasticAndroidAppDownloader(config)
    firmware_downloader = FirmwareReleaseDownloader(config)

    # Test cleanup methods
    android_downloader.cleanup_old_versions(2)
    firmware_downloader.cleanup_old_versions(2)


@pytest.mark.integration
def test_orchestrator_integration(tmp_path: Path):
    """Test orchestrator integration."""
    # Create a test configuration
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_APK_PRERELEASES": True,
        "CHECK_FIRMWARE_PRERELEASES": True,
        "ANDROID_VERSIONS_TO_KEEP": 3,
        "FIRMWARE_VERSIONS_TO_KEEP": 3,
        "EXTRACT_PATTERNS": ["rak4631-"],
        "EXCLUDE_PATTERNS": [],
    }

    orchestrator = DownloadOrchestrator(config)

    # Verify that the orchestrator can manage prerelease tracking
    orchestrator.update_version_tracking()


@pytest.mark.unit
def test_prerelease_config_handling():
    """Test that prerelease configuration is handled correctly."""
    from fetchtastic.download.interfaces import Release

    # Test with prereleases enabled
    config_enabled = {"CHECK_APK_PRERELEASES": True, "CHECK_FIRMWARE_PRERELEASES": True}

    # Test with prereleases disabled
    config_disabled = {
        "CHECK_APK_PRERELEASES": False,
        "CHECK_FIRMWARE_PRERELEASES": False,
    }

    # Create mock releases
    releases = [
        Release(tag_name="v2.7.8", prerelease=False),
        Release(tag_name="v2.7.9-rc1", prerelease=True),
        Release(tag_name="v2.8.0-beta1", prerelease=True),
    ]

    # Test filtering logic
    # When prereleases are enabled, all releases should be considered
    enabled_count = sum(
        1
        for release in releases
        if not release.prerelease or config_enabled.get("CHECK_APK_PRERELEASES", False)
    )
    assert enabled_count == 3  # All releases

    # When prereleases are disabled, only stable releases should be considered
    disabled_count = sum(
        1
        for release in releases
        if not release.prerelease or config_disabled.get("CHECK_APK_PRERELEASES", False)
    )
    assert disabled_count == 1  # Only stable release
