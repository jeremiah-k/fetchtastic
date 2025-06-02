#!/usr/bin/env python3
"""
Test script to debug fetchtastic's release detection logic
"""

import sys
import os

# Add src to path so we can import fetchtastic modules
sys.path.insert(0, 'src')

from fetchtastic.downloader import _get_latest_releases_data

def test_fetchtastic_release_detection():
    """Test fetchtastic's actual release detection logic."""
    
    print("=== TESTING FETCHTASTIC'S RELEASE DETECTION ===")
    
    # Test firmware releases
    print("\n--- Firmware Releases ---")
    firmware_url = "https://api.github.com/repos/meshtastic/firmware/releases"
    firmware_releases = _get_latest_releases_data(firmware_url, 10)
    
    print(f"Fetchtastic returned {len(firmware_releases)} firmware releases:")
    for i, release in enumerate(firmware_releases[:5]):
        tag = release.get("tag_name", "Unknown")
        published = release.get("published_at", "Unknown")
        prerelease = release.get("prerelease", False)
        print(f"{i+1}. {tag} - Published: {published} - Pre-release: {prerelease}")
    
    if firmware_releases:
        latest_firmware = firmware_releases[0]
        print(f"\nFetchtastic would consider LATEST FIRMWARE: {latest_firmware.get('tag_name')}")
    
    # Test Android releases  
    print("\n--- Android APK Releases ---")
    android_url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    android_releases = _get_latest_releases_data(android_url, 10)
    
    print(f"Fetchtastic returned {len(android_releases)} Android releases:")
    for i, release in enumerate(android_releases[:5]):
        tag = release.get("tag_name", "Unknown")
        published = release.get("published_at", "Unknown")
        prerelease = release.get("prerelease", False)
        print(f"{i+1}. {tag} - Published: {published} - Pre-release: {prerelease}")
    
    if android_releases:
        latest_android = android_releases[0]
        print(f"\nFetchtastic would consider LATEST ANDROID: {latest_android.get('tag_name')}")
    
    # Test what gets written to latest_release_file
    print("\n--- Testing check_and_download logic ---")
    
    # Simulate what check_and_download does
    if firmware_releases:
        releases_to_download = firmware_releases[:2]  # Keep 2 versions
        if releases_to_download:
            latest_release_tag_val = releases_to_download[0]["tag_name"]
            print(f"check_and_download would write to latest_firmware_release.txt: {latest_release_tag_val}")
    
    if android_releases:
        releases_to_download = android_releases[:2]  # Keep 2 versions  
        if releases_to_download:
            latest_release_tag_val = releases_to_download[0]["tag_name"]
            print(f"check_and_download would write to latest_android_release.txt: {latest_release_tag_val}")

if __name__ == "__main__":
    test_fetchtastic_release_detection()
