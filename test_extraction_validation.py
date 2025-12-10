#!/usr/bin/env python3

"""
Test script to verify the extraction validation functionality works correctly.
"""

import os

# Add the src directory to the Python path
import sys
import tempfile
import zipfile

sys.path.insert(0, "/home/coder/fetchtastic/src")

from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.files import FileOperations
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.repository import RepositoryDownloader


def test_extraction_validation():
    """Test the extraction validation methods."""
    print("Testing extraction validation functionality...")

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test FileOperations directly
        file_ops = FileOperations()

        # Test FileOperations directly
        file_ops = FileOperations()

        # Test 1: Valid patterns
        valid_patterns = ["*.bin", "*.uf2", "firmware-*"]
        valid_excludes = ["*debug*", "*_test*"]
        result = file_ops.validate_extraction_patterns(valid_patterns, valid_excludes)
        print(f"Valid patterns test: {'PASS' if result else 'FAIL'}")
        assert result, "Valid patterns should be accepted"

        # Test 2: Invalid patterns (path traversal)
        invalid_patterns = ["../*.bin", "*.bin/../", "/absolute/*.uf2"]
        result = file_ops.validate_extraction_patterns(invalid_patterns, [])
        print(f"Invalid patterns test: {'PASS' if not result else 'FAIL'}")
        assert not result, "Invalid patterns should be rejected"

        # Test 3: Empty patterns
        empty_patterns = ["", "   "]
        result = file_ops.validate_extraction_patterns(empty_patterns, [])
        print(f"Empty patterns test: {'PASS' if not result else 'FAIL'}")
        assert not result, "Empty patterns should be rejected"

        # Test 4: Create a test ZIP file and test extraction need checking
        zip_path = os.path.join(temp_dir, "test_firmware.zip")
        extract_dir = os.path.join(temp_dir, "extracted")

        # Create a simple ZIP file
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("firmware.bin", "test firmware content")
            zipf.writestr("readme.txt", "readme content")
            zipf.writestr("debug.bin", "debug firmware content")

        # Test extraction need - should need extraction since files don't exist
        result = file_ops.check_extraction_needed(
            zip_path, extract_dir, ["*.bin"], ["*debug*"]
        )
        print(
            f"Extraction needed test (files don't exist): {'PASS' if result else 'FAIL'}"
        )
        assert result, "Extraction should be needed when files don't exist"

        # Create the expected extracted files to test "not needed" scenario
        os.makedirs(extract_dir, exist_ok=True)
        with open(os.path.join(extract_dir, "firmware.bin"), "w") as f:
            f.write("test firmware content")

        # Test extraction need - should NOT need extraction since files exist
        result = file_ops.check_extraction_needed(
            zip_path, extract_dir, ["*.bin"], ["*debug*"]
        )
        print(
            f"Extraction not needed test (files exist): {'PASS' if not result else 'FAIL'}"
        )
        assert not result, "Extraction should not be needed when files exist"

        print("All extraction validation tests passed!")


def test_downloader_implementations():
    """Test that all downloaders implement the new interface methods."""
    print("\nTesting downloader implementations...")

    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "DOWNLOAD_DIR": temp_dir,
            "VERSIONS_TO_KEEP": 5,
            "EXTRACT_PATTERNS": ["*.bin"],
            "EXCLUDE_PATTERNS": ["*debug*"],
        }

        # Test FirmwareReleaseDownloader
        firmware_downloader = FirmwareReleaseDownloader(config)
        assert hasattr(
            firmware_downloader, "validate_extraction_patterns"
        ), "Firmware downloader missing validate_extraction_patterns"
        assert hasattr(
            firmware_downloader, "check_extraction_needed"
        ), "Firmware downloader missing check_extraction_needed"

        result = firmware_downloader.validate_extraction_patterns(
            ["*.bin"], ["*debug*"]
        )
        print(f"Firmware downloader validation: {'PASS' if result else 'FAIL'}")
        assert result, "Firmware downloader pattern validation should work"

        # Test Android downloader (should return False for extraction)
        android_downloader = MeshtasticAndroidAppDownloader(config)
        assert hasattr(
            android_downloader, "validate_extraction_patterns"
        ), "Android downloader missing validate_extraction_patterns"
        assert hasattr(
            android_downloader, "check_extraction_needed"
        ), "Android downloader missing check_extraction_needed"

        result = android_downloader.validate_extraction_patterns(["*.apk"], [])
        print(f"Android downloader validation: {'PASS' if not result else 'FAIL'}")
        assert not result, "Android downloader should return False for extraction"

        # Test Repository downloader
        repo_downloader = RepositoryDownloader(config)
        assert hasattr(
            repo_downloader, "validate_extraction_patterns"
        ), "Repository downloader missing validate_extraction_patterns"
        assert hasattr(
            repo_downloader, "check_extraction_needed"
        ), "Repository downloader missing check_extraction_needed"

        result = repo_downloader.validate_extraction_patterns(["*.sh"], [])
        print(f"Repository downloader validation: {'PASS' if result else 'FAIL'}")
        assert result, "Repository downloader pattern validation should work"

        print("All downloader implementation tests passed!")


def test_extraction_with_hash_generation():
    """Test the extraction with hash generation functionality."""
    print("\nTesting extraction with hash generation...")

    with tempfile.TemporaryDirectory() as temp_dir:
        file_ops = FileOperations()

        # Create a test ZIP file
        zip_path = os.path.join(temp_dir, "test_firmware.zip")
        extract_dir = os.path.join(temp_dir, "extracted")

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("firmware.bin", "test firmware content")
            zipf.writestr("bootloader.uf2", "bootloader content")

        # Test extraction with validation
        extracted_files = file_ops.extract_with_validation(
            zip_path, extract_dir, ["*.bin", "*.uf2"], []
        )

        print(f"Extracted {len(extracted_files)} files")
        assert len(extracted_files) == 2, "Should extract 2 files"

        # Test hash generation
        hash_results = file_ops.generate_hash_for_extracted_files(extracted_files)
        print(f"Generated hashes for {len(hash_results)} files")
        assert len(hash_results) == 2, "Should generate hashes for 2 files"

        # Verify hash files were created
        hash_files = [f for f in os.listdir(extract_dir) if f.endswith(".sha256")]
        print(f"Created {len(hash_files)} hash files")
        assert len(hash_files) == 2, "Should create 2 hash files"

        print("Extraction with hash generation test passed!")


if __name__ == "__main__":
    try:
        test_extraction_validation()
        test_downloader_implementations()
        test_extraction_with_hash_generation()
        print(
            "\n🎉 All tests passed! Extraction validation functionality is working correctly."
        )
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
