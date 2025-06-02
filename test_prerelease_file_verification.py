#!/usr/bin/env python3
"""
Test to verify that pre-release file verification works correctly.
This test simulates the scenario where a pre-release directory exists but is missing files.
"""

import sys
import os
import tempfile
import shutil
from unittest.mock import Mock, patch

# Add src to path so we can import fetchtastic modules
sys.path.insert(0, 'src')

def test_prerelease_file_verification():
    """Test that pre-release directories with missing files are re-processed."""
    
    print("=== TESTING PRE-RELEASE FILE VERIFICATION ===")
    print("=" * 50)
    
    try:
        from fetchtastic.downloader import check_for_prereleases
        
        # Create a temporary directory structure
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"Using temporary directory: {temp_dir}")
            
            # Create the prerelease directory structure
            prerelease_dir = os.path.join(temp_dir, "firmware", "prerelease")
            test_prerelease_dir = os.path.join(prerelease_dir, "firmware-2.6.10.9ce4455")
            os.makedirs(test_prerelease_dir, exist_ok=True)
            
            # Create only some of the expected files (simulating incomplete download)
            incomplete_files = [
                "firmware-rak4631-2.6.10.9ce4455.uf2",
                "firmware-tbeam-2.6.10.9ce4455.bin",
                # Missing: littlefs-rak4631-2.6.10.9ce4455.bin, bleota.bin, etc.
            ]
            
            for file_name in incomplete_files:
                file_path = os.path.join(test_prerelease_dir, file_name)
                with open(file_path, 'w') as f:
                    f.write("dummy content")
                print(f"  Created incomplete file: {file_name}")
            
            # Mock the menu_repo functions
            mock_directories = ["firmware-2.6.10.9ce4455"]
            
            mock_files = [
                {"name": "firmware-rak4631-2.6.10.9ce4455.uf2", "download_url": "http://example.com/file1"},
                {"name": "firmware-tbeam-2.6.10.9ce4455.bin", "download_url": "http://example.com/file2"},
                {"name": "littlefs-rak4631-2.6.10.9ce4455.bin", "download_url": "http://example.com/file3"},
                {"name": "bleota.bin", "download_url": "http://example.com/file4"},
                {"name": "firmware-tcxo-rak4631-2.6.10.9ce4455.elf", "download_url": "http://example.com/file5"},  # Should be excluded
            ]
            
            extract_patterns = ["rak4631-", "tbeam-", "littlefs-", "bleota"]
            exclude_patterns = ["tcxo"]
            
            with patch('fetchtastic.menu_repo.fetch_repo_directories') as mock_fetch_dirs, \
                 patch('fetchtastic.menu_repo.fetch_directory_contents') as mock_fetch_contents, \
                 patch('requests.get') as mock_requests:
                
                mock_fetch_dirs.return_value = mock_directories
                mock_fetch_contents.return_value = mock_files
                
                # Mock successful download
                mock_response = Mock()
                mock_response.raise_for_status.return_value = None
                mock_response.iter_content.return_value = [b"dummy content"]
                mock_requests.return_value = mock_response
                
                print("\nTesting pre-release verification with incomplete directory...")
                
                # Call check_for_prereleases
                found, versions = check_for_prereleases(
                    temp_dir,
                    "v2.6.9.f223b8a",  # Latest release tag
                    extract_patterns,
                    exclude_patterns
                )
                
                print(f"Pre-release found: {found}")
                print(f"Versions processed: {versions}")
                
                # Verify that the function detected missing files and attempted to download
                if found and versions:
                    print("✅ Pre-release verification correctly detected missing files and re-processed")
                    
                    # Check that the missing files would have been downloaded
                    expected_files = [
                        "littlefs-rak4631-2.6.10.9ce4455.bin",  # Was missing
                        "bleota.bin",  # Was missing
                    ]
                    
                    print(f"Expected missing files that should be downloaded: {expected_files}")
                    
                    # Verify mock was called for downloads
                    download_calls = mock_requests.call_count
                    print(f"Number of download attempts: {download_calls}")
                    
                    if download_calls >= len(expected_files):
                        print("✅ Correct number of download attempts made for missing files")
                        return True
                    else:
                        print(f"❌ Expected at least {len(expected_files)} downloads, got {download_calls}")
                        return False
                else:
                    print("❌ Pre-release verification failed to detect missing files")
                    return False
                    
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_complete_prerelease_directory():
    """Test that complete pre-release directories are not re-processed."""
    
    print("\n=== TESTING COMPLETE PRE-RELEASE DIRECTORY ===")
    print("=" * 50)
    
    try:
        from fetchtastic.downloader import check_for_prereleases
        
        # Create a temporary directory structure
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"Using temporary directory: {temp_dir}")
            
            # Create the prerelease directory structure
            prerelease_dir = os.path.join(temp_dir, "firmware", "prerelease")
            test_prerelease_dir = os.path.join(prerelease_dir, "firmware-2.6.10.9ce4455")
            os.makedirs(test_prerelease_dir, exist_ok=True)
            
            # Create ALL expected files (simulating complete download)
            complete_files = [
                "firmware-rak4631-2.6.10.9ce4455.uf2",
                "firmware-tbeam-2.6.10.9ce4455.bin",
                "littlefs-rak4631-2.6.10.9ce4455.bin",
                "bleota.bin",
            ]
            
            for file_name in complete_files:
                file_path = os.path.join(test_prerelease_dir, file_name)
                with open(file_path, 'w') as f:
                    f.write("dummy content")
                print(f"  Created complete file: {file_name}")
            
            # Mock the menu_repo functions
            mock_directories = ["firmware-2.6.10.9ce4455"]
            
            mock_files = [
                {"name": "firmware-rak4631-2.6.10.9ce4455.uf2", "download_url": "http://example.com/file1"},
                {"name": "firmware-tbeam-2.6.10.9ce4455.bin", "download_url": "http://example.com/file2"},
                {"name": "littlefs-rak4631-2.6.10.9ce4455.bin", "download_url": "http://example.com/file3"},
                {"name": "bleota.bin", "download_url": "http://example.com/file4"},
                {"name": "firmware-tcxo-rak4631-2.6.10.9ce4455.elf", "download_url": "http://example.com/file5"},  # Should be excluded
            ]
            
            extract_patterns = ["rak4631-", "tbeam-", "littlefs-", "bleota"]
            exclude_patterns = ["tcxo"]
            
            with patch('fetchtastic.menu_repo.fetch_repo_directories') as mock_fetch_dirs, \
                 patch('fetchtastic.menu_repo.fetch_directory_contents') as mock_fetch_contents, \
                 patch('requests.get') as mock_requests:
                
                mock_fetch_dirs.return_value = mock_directories
                mock_fetch_contents.return_value = mock_files
                
                print("\nTesting pre-release verification with complete directory...")
                
                # Call check_for_prereleases
                found, versions = check_for_prereleases(
                    temp_dir,
                    "v2.6.9.f223b8a",  # Latest release tag
                    extract_patterns,
                    exclude_patterns
                )
                
                print(f"Pre-release found: {found}")
                print(f"Versions processed: {versions}")
                
                # Verify that no downloads were attempted since all files are present
                download_calls = mock_requests.call_count
                print(f"Number of download attempts: {download_calls}")
                
                if not found and not versions and download_calls == 0:
                    print("✅ Complete pre-release directory correctly skipped (no re-download)")
                    return True
                else:
                    print("❌ Complete pre-release directory was unnecessarily re-processed")
                    return False
                    
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🧪 PRE-RELEASE FILE VERIFICATION TEST")
    print("=" * 60)
    
    incomplete_test = test_prerelease_file_verification()
    complete_test = test_complete_prerelease_directory()
    
    overall_success = incomplete_test and complete_test
    
    print("\n" + "=" * 60)
    print("FINAL RESULTS:")
    print(f"✅ Incomplete directory test: {'PASS' if incomplete_test else 'FAIL'}")
    print(f"✅ Complete directory test: {'PASS' if complete_test else 'FAIL'}")
    
    if overall_success:
        print("\n🎉 ALL TESTS PASSED! Pre-release file verification is working correctly.")
        print("   - Incomplete pre-release directories will be re-processed")
        print("   - Complete pre-release directories will be skipped")
        print("   - This fixes the issue where empty directories prevented re-downloads")
    else:
        print("\n💥 SOME TESTS FAILED! Check the output above for details.")
    
    sys.exit(0 if overall_success else 1)
