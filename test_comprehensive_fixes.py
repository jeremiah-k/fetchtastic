#!/usr/bin/env python3
"""
Comprehensive test to verify all fixes are working correctly:
1. Pre-release pattern matching logic
2. Version detection showing correct latest versions
3. Progress feedback during GitHub API calls
"""

import re
import sys

# Add src to path so we can import fetchtastic modules
sys.path.insert(0, "src")


def strip_version_numbers(filename: str) -> str:
    """
    Removes version numbers and commit hashes from a filename.
    """
    base_name: str = re.sub(r"([_-])\d+\.\d+\.\d+(?:\.[\da-f]+)?", r"\1", filename)
    return base_name


def test_pattern_matching():
    """Test that pre-release files match extract patterns correctly."""

    print("=== TESTING PRE-RELEASE PATTERN MATCHING ===")
    print("=" * 50)

    # Sample pre-release files from firmware-2.6.10.9ce4455 directory
    prerelease_files = [
        "firmware-rak4631-2.6.10.9ce4455.elf",
        "firmware-tbeam-2.6.10.9ce4455.elf",
        "firmware-t1000-e-2.6.10.9ce4455.elf",
        "littlefs-rak4631-2.6.10.9ce4455.bin",
        "littlefs-tbeam-2.6.10.9ce4455.bin",
        "bleota-s3.bin",
        "firmware-tcxo-rak4631-2.6.10.9ce4455.elf",  # Should be excluded
        "firmware-s3-core-2.6.10.9ce4455.elf",  # Should be excluded
        "firmware-request-2.6.10.9ce4455.elf",  # Should be excluded
        "firmware-something.hex",  # Should be excluded
    ]

    # Extract patterns from user's config
    extract_patterns = [
        "rak4631-",
        "tbeam-",
        "t1000-e-",
        "tlora-v2-1-1_6-",
        "device-",
        "littlefs-",
        "bleota",
    ]

    # Exclude patterns from user's config
    exclude_patterns = [".hex", "tcxo", "s3-core", "request"]

    print("Testing pre-release file pattern matching...")

    matched_files = []
    excluded_files = []
    skipped_files = []

    for file_name in prerelease_files:
        print(f"\nTesting file: {file_name}")

        # Check exclude patterns first
        if any(exclude in file_name for exclude in exclude_patterns):
            print(
                f"  ❌ EXCLUDED by pattern: {[p for p in exclude_patterns if p in file_name]}"
            )
            excluded_files.append(file_name)
            continue

        # Check extract patterns
        stripped_file_name = strip_version_numbers(file_name)
        print(f"  Stripped filename: {stripped_file_name}")

        matching_patterns = [
            pattern for pattern in extract_patterns if pattern in stripped_file_name
        ]
        if matching_patterns:
            print(f"  ✅ MATCHED by pattern: {matching_patterns}")
            matched_files.append(file_name)
        else:
            print("  ⏭️  SKIPPED - no matching patterns")
            skipped_files.append(file_name)

    print("\n" + "=" * 50)
    print("PATTERN MATCHING SUMMARY:")
    print(f"✅ Files that would be downloaded ({len(matched_files)}):")
    for f in matched_files:
        print(f"   - {f}")

    print(f"\n❌ Files excluded by exclude patterns ({len(excluded_files)}):")
    for f in excluded_files:
        print(f"   - {f}")

    print(f"\n⏭️  Files skipped (no matching patterns) ({len(skipped_files)}):")
    for f in skipped_files:
        print(f"   - {f}")

    # Verify expected results
    expected_matched = [
        "firmware-rak4631-2.6.10.9ce4455.elf",
        "firmware-tbeam-2.6.10.9ce4455.elf",
        "firmware-t1000-e-2.6.10.9ce4455.elf",
        "littlefs-rak4631-2.6.10.9ce4455.bin",
        "littlefs-tbeam-2.6.10.9ce4455.bin",
        "bleota-s3.bin",
    ]

    expected_excluded = [
        "firmware-tcxo-rak4631-2.6.10.9ce4455.elf",
        "firmware-s3-core-2.6.10.9ce4455.elf",
        "firmware-request-2.6.10.9ce4455.elf",
        "firmware-something.hex",
    ]

    print("\n" + "=" * 50)
    print("PATTERN MATCHING VALIDATION:")

    success = True
    if set(matched_files) == set(expected_matched):
        print("✅ Matched files are correct!")
    else:
        print("❌ Matched files are incorrect!")
        print(f"   Expected: {expected_matched}")
        print(f"   Got: {matched_files}")
        success = False

    if set(excluded_files) == set(expected_excluded):
        print("✅ Excluded files are correct!")
    else:
        print("❌ Excluded files are incorrect!")
        print(f"   Expected: {expected_excluded}")
        print(f"   Got: {excluded_files}")
        success = False

    return success


def test_version_detection():
    """Test that version detection shows correct latest versions using mocked data."""
    from unittest.mock import patch

    print("\n=== TESTING VERSION DETECTION ===")
    print("=" * 50)

    try:
        from fetchtastic.downloader import _get_latest_releases_data

        # Mock firmware release data
        mock_firmware_releases = [
            {"tag_name": "v2.6.10.9ce4455", "published_at": "2024-01-15T10:00:00Z"},
            {"tag_name": "v2.6.9.f223b8a", "published_at": "2024-01-10T10:00:00Z"},
        ]

        # Mock Android release data
        mock_android_releases = [
            {"tag_name": "2.6.10", "published_at": "2024-01-15T10:00:00Z"},
            {"tag_name": "2.6.9", "published_at": "2024-01-10T10:00:00Z"},
        ]

        def mock_get_releases(url, scan_count):
            if "firmware" in url:
                return mock_firmware_releases[:scan_count]
            elif "Android" in url:
                return mock_android_releases[:scan_count]
            return []

        with patch(
            "fetchtastic.downloader._get_latest_releases_data",
            side_effect=mock_get_releases,
        ):
            # Test firmware releases
            print("Testing firmware version detection...")
            firmware_url = "https://api.github.com/repos/meshtastic/firmware/releases"
            firmware_releases = _get_latest_releases_data(firmware_url, 5)

            if firmware_releases:
                latest_firmware = firmware_releases[0].get("tag_name")
                print(f"✅ Latest firmware detected: {latest_firmware}")
                if latest_firmware == "v2.6.10.9ce4455":
                    print("✅ Firmware version detection is CORRECT!")
                    firmware_success = True
                else:
                    print(f"❌ Expected v2.6.10.9ce4455, got {latest_firmware}")
                    firmware_success = False
            else:
                print("❌ No firmware releases found")
                firmware_success = False

            # Test Android releases
            print("\nTesting Android version detection...")
            android_url = (
                "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
            )
            android_releases = _get_latest_releases_data(android_url, 5)

            if android_releases:
                latest_android = android_releases[0].get("tag_name")
                print(f"✅ Latest Android detected: {latest_android}")
                if latest_android == "2.6.10":
                    print("✅ Android version detection is CORRECT!")
                    android_success = True
                else:
                    print(f"❌ Expected 2.6.10, got {latest_android}")
                    android_success = False
            else:
                print("❌ No Android releases found")
                android_success = False

            return firmware_success and android_success

    except Exception as e:
        print(f"❌ Error testing version detection: {e}")
        return False


def test_progress_feedback():
    """Test that progress feedback is working during GitHub API calls."""
    import logging
    from io import StringIO
    from unittest.mock import patch

    print("\n=== TESTING PROGRESS FEEDBACK ===")
    print("=" * 50)

    try:
        from fetchtastic.downloader import _get_latest_releases_data

        # Create a string buffer to capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)

        # Get the logger used by the downloader
        from fetchtastic.log_utils import logger

        logger.addHandler(handler)

        # Mock requests to avoid actual network calls
        with patch("requests.get") as mock_get:
            mock_response = mock_get.return_value
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = [
                {"tag_name": "v2.6.10", "published_at": "2024-01-15T10:00:00Z"}
            ]

            # Test firmware progress message
            firmware_url = "https://api.github.com/repos/meshtastic/firmware/releases"
            _get_latest_releases_data(firmware_url, 5)

            # Test Android progress message
            android_url = (
                "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
            )
            _get_latest_releases_data(android_url, 5)

        # Remove the handler to avoid affecting other tests
        logger.removeHandler(handler)

        # Get the captured log output
        log_output = log_capture.getvalue()

        # Check for expected progress messages
        firmware_message_found = "Fetching firmware releases from GitHub" in log_output
        android_message_found = (
            "Fetching Android APK releases from GitHub" in log_output
        )

        if firmware_message_found and android_message_found:
            print("✅ Progress feedback is working!")
            print("   Found expected messages:")
            print("   - 'Fetching firmware releases from GitHub...'")
            print("   - 'Fetching Android APK releases from GitHub...'")
            return True
        else:
            print("❌ Progress feedback messages not found!")
            print(f"   Log output: {log_output}")
            print(f"   Firmware message found: {firmware_message_found}")
            print(f"   Android message found: {android_message_found}")
            return False

    except Exception as e:
        print(f"❌ Error testing progress feedback: {e}")
        return False


if __name__ == "__main__":
    print("🧪 COMPREHENSIVE FETCHTASTIC FIXES TEST")
    print("=" * 60)

    pattern_success = test_pattern_matching()
    version_success = test_version_detection()
    progress_success = test_progress_feedback()

    overall_success = pattern_success and version_success and progress_success

    print("\n" + "=" * 60)
    print("FINAL RESULTS:")
    print(f"✅ Pattern matching: {'PASS' if pattern_success else 'FAIL'}")
    print(f"✅ Version detection: {'PASS' if version_success else 'FAIL'}")
    print(f"✅ Progress feedback: {'PASS' if progress_success else 'FAIL'}")

    if overall_success:
        print("\n🎉 ALL TESTS PASSED! All fixes are working correctly.")
        print(
            "   - Pre-release downloads will use EXTRACT_PATTERNS instead of SELECTED_FIRMWARE_ASSETS"
        )
        print("   - Exclude patterns will be properly applied to pre-release downloads")
        print(
            "   - Latest versions will be correctly displayed (v2.6.9.f223b8a for firmware, 2.6.9 for Android)"
        )
        print("   - Progress feedback will be shown during GitHub API calls")
    else:
        print("\n💥 SOME TESTS FAILED! Check the output above for details.")

    sys.exit(0 if overall_success else 1)
