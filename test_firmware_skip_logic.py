#!/usr/bin/env python3
"""
Test to verify that firmware re-download fix is working correctly.
This test ensures that firmware is not re-downloaded when it already exists and is complete.
"""

import os
import sys
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Add src to path so we can import fetchtastic modules
sys.path.insert(0, "src")

def test_firmware_skip_logic():
    """Test that firmware is not re-downloaded when it already exists and is complete."""
    
    print("=== TESTING FIRMWARE SKIP LOGIC ===")
    print("=" * 50)
    
    try:
        from fetchtastic.downloader import _is_release_complete, check_and_download
        
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"Using temporary directory: {temp_dir}")
            
            # Create a mock release directory with files
            release_tag = "v2.6.8.ef9d0d7"
            release_dir = os.path.join(temp_dir, release_tag)
            os.makedirs(release_dir, exist_ok=True)
            
            # Create mock release data
            mock_release_data = {
                "tag_name": release_tag,
                "assets": [
                    {"name": "firmware-rak4631-2.6.8.ef9d0d7.zip", "browser_download_url": "http://example.com/file1.zip"},
                    {"name": "firmware-tbeam-2.6.8.ef9d0d7.zip", "browser_download_url": "http://example.com/file2.zip"},
                    {"name": "firmware-tcxo-rak4631-2.6.8.ef9d0d7.zip", "browser_download_url": "http://example.com/file3.zip"},  # Should be excluded
                ]
            }
            
            selected_patterns = ["rak4631-", "tbeam-"]
            exclude_patterns = ["*tcxo*"]
            
            # Test 1: Empty release directory should not be complete
            print("\nTest 1: Empty release directory")
            is_complete = _is_release_complete(mock_release_data, release_dir, selected_patterns, exclude_patterns)
            print(f"Is complete (empty dir): {is_complete}")
            assert not is_complete, "Empty directory should not be complete"
            print("✅ Empty directory correctly identified as incomplete")
            
            # Test 2: Create expected files and test completeness
            print("\nTest 2: Complete release directory")
            expected_files = ["firmware-rak4631-2.6.8.ef9d0d7.zip", "firmware-tbeam-2.6.8.ef9d0d7.zip"]
            
            for file_name in expected_files:
                file_path = os.path.join(release_dir, file_name)
                # Create a valid zip file
                import zipfile
                with zipfile.ZipFile(file_path, 'w') as zf:
                    zf.writestr("test.txt", "test content")
                print(f"Created mock file: {file_name}")
            
            is_complete = _is_release_complete(mock_release_data, release_dir, selected_patterns, exclude_patterns)
            print(f"Is complete (with files): {is_complete}")
            assert is_complete, "Directory with all expected files should be complete"
            print("✅ Complete directory correctly identified as complete")
            
            # Test 3: Missing one file should not be complete
            print("\nTest 3: Incomplete release directory")
            os.remove(os.path.join(release_dir, expected_files[0]))
            print(f"Removed file: {expected_files[0]}")
            
            is_complete = _is_release_complete(mock_release_data, release_dir, selected_patterns, exclude_patterns)
            print(f"Is complete (missing file): {is_complete}")
            assert not is_complete, "Directory missing files should not be complete"
            print("✅ Incomplete directory correctly identified as incomplete")
            
            # Test 4: Test with corrupted zip file
            print("\nTest 4: Corrupted zip file")
            # Recreate the missing file
            file_path = os.path.join(release_dir, expected_files[0])
            with open(file_path, 'w') as f:
                f.write("This is not a valid zip file")
            print(f"Created corrupted zip file: {expected_files[0]}")
            
            is_complete = _is_release_complete(mock_release_data, release_dir, selected_patterns, exclude_patterns)
            print(f"Is complete (corrupted zip): {is_complete}")
            assert not is_complete, "Directory with corrupted zip should not be complete"
            print("✅ Corrupted zip correctly identified as incomplete")
            
            print("\n" + "=" * 50)
            print("✅ ALL FIRMWARE SKIP LOGIC TESTS PASSED!")
            print("   - Empty directories are correctly identified as incomplete")
            print("   - Complete directories are correctly identified as complete")
            print("   - Missing files are correctly detected")
            print("   - Corrupted zip files are correctly detected")
            return True
            
    except Exception as e:
        print(f"❌ Error testing firmware skip logic: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_check_and_download_skip():
    """Test that check_and_download skips complete releases."""
    
    print("\n=== TESTING CHECK_AND_DOWNLOAD SKIP LOGIC ===")
    print("=" * 50)
    
    try:
        from fetchtastic.downloader import check_and_download
        
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"Using temporary directory: {temp_dir}")
            
            # Create a mock release directory with complete files
            release_tag = "v2.6.8.ef9d0d7"
            release_dir = os.path.join(temp_dir, release_tag)
            os.makedirs(release_dir, exist_ok=True)
            
            # Create latest release file
            latest_release_file = os.path.join(temp_dir, "latest_firmware_release.txt")
            with open(latest_release_file, 'w') as f:
                f.write("v2.6.7.abc1234")  # Different from current release
            
            # Create complete files in release directory
            expected_files = ["firmware-rak4631-2.6.8.ef9d0d7.zip"]
            for file_name in expected_files:
                file_path = os.path.join(release_dir, file_name)
                import zipfile
                with zipfile.ZipFile(file_path, 'w') as zf:
                    zf.writestr("test.txt", "test content")
                print(f"Created complete file: {file_name}")
            
            # Mock release data
            mock_releases = [{
                "tag_name": release_tag,
                "body": "Test release notes",
                "assets": [
                    {"name": "firmware-rak4631-2.6.8.ef9d0d7.zip", "browser_download_url": "http://example.com/file1.zip"},
                ]
            }]
            
            selected_patterns = ["rak4631-"]
            exclude_patterns = []
            
            # Mock the download function to track if it was called
            download_called = False
            def mock_download(url, path):
                nonlocal download_called
                download_called = True
                return True
            
            with patch('fetchtastic.utils.download_file_with_retry', side_effect=mock_download):
                downloaded_versions, new_versions, failed_downloads = check_and_download(
                    releases=mock_releases,
                    latest_release_file=latest_release_file,
                    release_type="Firmware",
                    download_dir_path=temp_dir,
                    versions_to_keep=2,
                    extract_patterns=[],
                    selected_patterns=selected_patterns,
                    auto_extract=False,
                    exclude_patterns=exclude_patterns
                )
            
            print(f"Downloaded versions: {downloaded_versions}")
            print(f"New versions: {new_versions}")
            print(f"Failed downloads: {failed_downloads}")
            print(f"Download function called: {download_called}")
            
            # Verify that download was skipped
            assert not download_called, "Download should have been skipped for complete release"
            assert len(downloaded_versions) == 0, "No versions should have been downloaded"
            assert release_tag in new_versions, "Release should be in new_versions since it's different from saved tag"
            
            print("✅ Complete release was correctly skipped!")
            print("   - Download function was not called")
            print("   - No versions were marked as downloaded")
            print("   - Release was correctly identified as new but complete")
            
            return True
            
    except Exception as e:
        print(f"❌ Error testing check_and_download skip logic: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🧪 FIRMWARE SKIP LOGIC TEST")
    print("=" * 60)
    
    test1_success = test_firmware_skip_logic()
    test2_success = test_check_and_download_skip()
    
    overall_success = test1_success and test2_success
    
    print("\n" + "=" * 60)
    print("FINAL RESULTS:")
    print(f"✅ Release completeness check: {'PASS' if test1_success else 'FAIL'}")
    print(f"✅ Download skip logic: {'PASS' if test2_success else 'FAIL'}")
    
    if overall_success:
        print("\n🎉 ALL TESTS PASSED! Firmware re-download fix is working correctly.")
        print("   - Firmware will not be re-downloaded when already complete")
        print("   - Corrupted or missing files will trigger re-download")
        print("   - Latest release tracking works properly")
    else:
        print("\n💥 SOME TESTS FAILED! Check the output above for details.")
    
    sys.exit(0 if overall_success else 1)
