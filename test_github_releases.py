#!/usr/bin/env python3
"""
Test script to check what GitHub API returns for firmware releases
and how they are being sorted.
"""

import requests
import json
from datetime import datetime

def test_github_releases():
    """Test GitHub releases API and sorting logic."""

    print("=== TESTING FIRMWARE RELEASES ===")
    url = "https://api.github.com/repos/meshtastic/firmware/releases"
    
    print("Fetching releases from GitHub API...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        releases = response.json()
        
        print(f"Fetched {len(releases)} releases")
        print("\nFirst 5 releases (as returned by GitHub):")
        print("=" * 80)
        
        for i, release in enumerate(releases[:5]):
            tag = release.get("tag_name", "Unknown")
            published = release.get("published_at", "Unknown")
            prerelease = release.get("prerelease", False)
            draft = release.get("draft", False)
            
            # Parse and format the published date
            try:
                pub_date = datetime.fromisoformat(published.replace('Z', '+00:00'))
                formatted_date = pub_date.strftime("%Y-%m-%d %H:%M:%S UTC")
            except:
                formatted_date = published
                
            print(f"{i+1}. {tag}")
            print(f"   Published: {formatted_date}")
            print(f"   Pre-release: {prerelease}")
            print(f"   Draft: {draft}")
            print()
        
        # Test the sorting logic from fetchtastic
        print("Testing fetchtastic sorting logic:")
        print("=" * 80)
        
        try:
            sorted_releases = sorted(
                releases, key=lambda r: r["published_at"], reverse=True
            )
            
            print("After sorting by published_at (descending):")
            for i, release in enumerate(sorted_releases[:5]):
                tag = release.get("tag_name", "Unknown")
                published = release.get("published_at", "Unknown")
                prerelease = release.get("prerelease", False)
                
                try:
                    pub_date = datetime.fromisoformat(published.replace('Z', '+00:00'))
                    formatted_date = pub_date.strftime("%Y-%m-%d %H:%M:%S UTC")
                except:
                    formatted_date = published
                    
                print(f"{i+1}. {tag} - {formatted_date} - Pre-release: {prerelease}")
                
        except Exception as e:
            print(f"Error sorting releases: {e}")
        
        # Check for version comparison
        print("\nVersion comparison test:")
        print("=" * 80)
        
        # Find v2.6.9 and v2.6.8 releases
        v269_release = None
        v268_release = None
        
        for release in releases:
            tag = release.get("tag_name", "")
            if "v2.6.9" in tag:
                v269_release = release
            elif "v2.6.8" in tag:
                v268_release = release
                
        if v269_release and v268_release:
            print(f"v2.6.9 release: {v269_release['tag_name']}")
            print(f"  Published: {v269_release['published_at']}")
            print(f"  Pre-release: {v269_release['prerelease']}")
            print()
            print(f"v2.6.8 release: {v268_release['tag_name']}")
            print(f"  Published: {v268_release['published_at']}")
            print(f"  Pre-release: {v268_release['prerelease']}")
            print()
            
            # Check which one would be considered "latest" by current logic
            if v269_release['published_at'] > v268_release['published_at']:
                print("v2.6.9 would be considered latest (newer published_at)")
            elif v269_release['published_at'] < v268_release['published_at']:
                print("v2.6.8 would be considered latest (newer published_at)")
            else:
                print("Both have same published_at - sorting is non-deterministic!")
                print("This explains why the wrong version might be detected as latest.")
        else:
            print("Could not find both v2.6.9 and v2.6.8 releases")
            
    except Exception as e:
        print(f"Error fetching releases: {e}")

def test_android_releases():
    """Test Android APK releases API and sorting logic."""

    print("\n=== TESTING ANDROID APK RELEASES ===")
    url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"

    print("Fetching Android releases from GitHub API...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        releases = response.json()

        print(f"Fetched {len(releases)} Android releases")
        print("\nFirst 5 Android releases (as returned by GitHub):")
        print("=" * 80)

        for i, release in enumerate(releases[:5]):
            tag = release.get("tag_name", "Unknown")
            published = release.get("published_at", "Unknown")
            prerelease = release.get("prerelease", False)
            draft = release.get("draft", False)

            # Parse and format the published date
            try:
                pub_date = datetime.fromisoformat(published.replace('Z', '+00:00'))
                formatted_date = pub_date.strftime("%Y-%m-%d %H:%M:%S UTC")
            except:
                formatted_date = published

            print(f"{i+1}. {tag}")
            print(f"   Published: {formatted_date}")
            print(f"   Pre-release: {prerelease}")
            print(f"   Draft: {draft}")
            print()

    except Exception as e:
        print(f"Error fetching Android releases: {e}")

if __name__ == "__main__":
    test_github_releases()
    test_android_releases()
