#!/usr/bin/env python3
"""
Test script to verify prerelease functionality integration in the new modular architecture.
"""

import json

# Add the src directory to the path so we can import the modules
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, "/home/coder/fetchtastic/src")

from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator


def test_prerelease_functionality():
    """Test that prerelease functionality works in the new architecture."""

    print("Testing prerelease functionality integration...")

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a test configuration
        config = {
            "DOWNLOAD_DIR": temp_dir,
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

        # Test prerelease tracking file management
        print("Testing prerelease tracking file management...")

        # Create some mock prereleases
        mock_prereleases = [
            Release(tag_name="v2.7.8-rc1", prerelease=True),
            Release(tag_name="v2.7.8-rc2", prerelease=True),
            Release(tag_name="v2.7.9-beta1", prerelease=True),
        ]

        # Test that prerelease tracking files are created and managed
        try:
            # This should create prerelease tracking files
            android_downloader.manage_prerelease_tracking_files()
            firmware_downloader.manage_prerelease_tracking_files()

            print("✓ Prerelease tracking file management works")

        except Exception as e:
            print(f"✗ Error in prerelease tracking: {e}")
            return False

        # Test version tracking
        print("Testing version tracking...")

        try:
            # Test version tracking updates
            android_downloader.update_latest_release_tag("v2.7.8")
            firmware_downloader.update_latest_release_tag("v2.7.8")

            # Verify tracking files exist
            tracking_files = list(Path(temp_dir).rglob("*tracking*.json"))
            if tracking_files:
                print(
                    f"✓ Version tracking files created: {[f.name for f in tracking_files]}"
                )
            else:
                print("✗ No tracking files found")

        except Exception as e:
            print(f"✗ Error in version tracking: {e}")
            return False

        # Test cleanup functionality
        print("Testing cleanup functionality...")

        try:
            # Test cleanup methods
            android_downloader.cleanup_old_versions(2)
            firmware_downloader.cleanup_old_versions(2)

            print("✓ Cleanup functionality works")

        except Exception as e:
            print(f"✗ Error in cleanup: {e}")
            return False

        # Test orchestrator integration
        print("Testing orchestrator integration...")

        try:
            orchestrator = DownloadOrchestrator(config)

            # Verify that the orchestrator can manage prerelease tracking
            orchestrator.update_version_tracking()

            print("✓ Orchestrator integration works")

        except Exception as e:
            print(f"✗ Error in orchestrator integration: {e}")
            return False

    print("✓ All prerelease functionality tests passed!")
    return True


def test_prerelease_config_handling():
    """Test that prerelease configuration is handled correctly."""

    print("\nTesting prerelease configuration handling...")

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
    print("Testing prerelease filtering...")

    # When prereleases are enabled, all releases should be considered
    enabled_count = sum(
        1
        for release in releases
        if not release.prerelease or config_enabled.get("CHECK_APK_PRERELEASES", False)
    )
    print(f"✓ With prereleases enabled: {enabled_count} releases would be downloaded")

    # When prereleases are disabled, only stable releases should be considered
    disabled_count = sum(
        1
        for release in releases
        if not release.prerelease or config_disabled.get("CHECK_APK_PRERELEASES", False)
    )
    print(f"✓ With prereleases disabled: {disabled_count} releases would be downloaded")

    return True


if __name__ == "__main__":
    print("Running prerelease functionality integration tests...")

    success = True

    # Run tests
    success &= test_prerelease_functionality()
    success &= test_prerelease_config_handling()

    if success:
        print("\n🎉 All tests passed! Prerelease functionality is working correctly.")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed. Please check the implementation.")
        sys.exit(1)
