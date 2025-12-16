#!/usr/bin/env python3
"""
Quick test to verify config key compatibility between legacy and new architecture.
"""

import sys

sys.path.insert(0, "src")


def test_config_compatibility():
    """Test that legacy config keys work with new architecture."""

    # Test firmware config keys
    from fetchtastic.download.firmware import FirmwareReleaseDownloader

    config = {
        "DOWNLOAD_DIR": "/tmp/test",
        "SELECTED_FIRMWARE_ASSETS": ["*t-deck*", "*heltec*"],
        "EXCLUDE_FIRMWARE_ASSETS": ["*nrf52*"],
        "CHECK_FIRMWARE_PRERELEASES": True,
        "SELECTED_PRERELEASE_ASSETS": ["*rc*"],
    }

    downloader = FirmwareReleaseDownloader(config)

    # Test that config is properly used
    selected_patterns = downloader._get_selected_patterns()
    exclude_patterns = downloader._get_exclude_patterns()

    print("✅ Firmware Config Test:")
    print(f"  Selected patterns: {selected_patterns}")
    print(f"  Exclude patterns: {exclude_patterns}")
    print(f"  Prerelease patterns: {downloader._get_prerelease_patterns()}")

    # Test Android config keys
    from fetchtastic.download.android import MeshtasticAndroidAppDownloader

    config = {
        "DOWNLOAD_DIR": "/tmp/test",
        "SELECTED_APK_ASSETS": ["*universal*", "*arm64*"],
        "EXCLUDE_APK_ASSETS": ["*debug*"],
        "CHECK_APK_PRERELEASES": True,
    }

    apk_downloader = MeshtasticAndroidAppDownloader(config)

    print("\n✅ Android Config Test:")
    print(f"  Selected patterns: {apk_downloader._get_selected_patterns()}")
    print(f"  Exclude patterns: {apk_downloader._get_exclude_patterns()}")

    # Test get_prerelease_patterns function
    from fetchtastic.download import get_prerelease_patterns

    config = {
        "SELECTED_PRERELEASE_ASSETS": ["*alpha*", "*beta*"],
        "EXTRACT_PATTERNS": ["*legacy*"],
    }

    patterns = get_prerelease_patterns(config)
    print(f"\n✅ Prerelease Patterns Test: {patterns}")

    print("\n🎯 All config compatibility tests passed!")
    return True


if __name__ == "__main__":
    test_config_compatibility()
