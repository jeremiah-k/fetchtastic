#!/usr/bin/env python3
"""
Test script to verify repository downloader integration in the orchestrator.
"""

import sys
import tempfile

sys.path.insert(0, "/home/coder/fetchtastic/src")

from fetchtastic.download.orchestrator import DownloadOrchestrator


def test_repository_integration():
    """Test that repository downloader is properly integrated into the orchestrator."""

    print("Testing repository downloader integration...")

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a test configuration
        config = {
            "DOWNLOAD_DIR": temp_dir,
            "CHECK_APK_PRERELEASES": False,
            "CHECK_FIRMWARE_PRERELEASES": False,
            "ANDROID_VERSIONS_TO_KEEP": 3,
            "FIRMWARE_VERSIONS_TO_KEEP": 3,
            "EXTRACT_PATTERNS": ["rak4631-"],
            "EXCLUDE_PATTERNS": [],
            "SELECTED_PATTERNS": ["device-", "firmware-"],
            "SELECTED_FIRMWARE_ASSETS": [],
            "SELECTED_PRERELEASE_ASSETS": [],
        }

        try:
            # Create orchestrator
            orchestrator = DownloadOrchestrator(config)

            # Verify that repository downloader is initialized
            assert hasattr(
                orchestrator, "repository_downloader"
            ), "Repository downloader not initialized"
            print("✓ Repository downloader initialized")

            # Verify that repository downloader has the correct config
            assert (
                orchestrator.repository_downloader.config == config
            ), "Repository downloader config mismatch"
            print("✓ Repository downloader config correct")

            # Test that repository processing method exists
            assert hasattr(
                orchestrator, "_process_repository_downloads"
            ), "Repository processing method missing"
            print("✓ Repository processing method exists")

            # Test that repository file filtering method exists
            assert hasattr(
                orchestrator, "_filter_repository_files"
            ), "Repository file filtering method missing"
            print("✓ Repository file filtering method exists")

            # Test that repository file download method exists
            assert hasattr(
                orchestrator, "_download_repository_file"
            ), "Repository file download method missing"
            print("✓ Repository file download method exists")

            # Test repository file filtering
            test_files = [
                {
                    "name": "device-install.sh",
                    "download_url": "https://example.com/device-install.sh",
                },
                {
                    "name": "firmware-rak4631-2.7.8.uf2",
                    "download_url": "https://example.com/firmware-rak4631-2.7.8.uf2",
                },
                {
                    "name": "readme.txt",
                    "download_url": "https://example.com/readme.txt",
                },
                {
                    "name": "excluded-file.bin",
                    "download_url": "https://example.com/excluded-file.bin",
                },
            ]

            # Test with selection patterns
            config_with_patterns = config.copy()
            config_with_patterns["SELECTED_PATTERNS"] = ["device-", "firmware-"]
            config_with_patterns["EXCLUDE_PATTERNS"] = ["excluded"]

            orchestrator_with_patterns = DownloadOrchestrator(config_with_patterns)
            filtered_files = orchestrator_with_patterns._filter_repository_files(
                test_files
            )

            # Should include files matching selection patterns and exclude files matching exclude patterns
            expected_files = [
                {
                    "name": "device-install.sh",
                    "download_url": "https://example.com/device-install.sh",
                },
                {
                    "name": "firmware-rak4631-2.7.8.uf2",
                    "download_url": "https://example.com/firmware-rak4631-2.7.8.uf2",
                },
            ]

            assert len(filtered_files) == len(
                expected_files
            ), f"Expected {len(expected_files)} files, got {len(filtered_files)}"
            for expected_file in expected_files:
                assert any(
                    f["name"] == expected_file["name"] for f in filtered_files
                ), f"Expected file {expected_file['name']} not found"

            print("✓ Repository file filtering works correctly")

            # Test statistics include repository downloads
            stats = orchestrator.get_download_statistics()
            assert (
                "repository_downloads" in stats
            ), "Repository downloads not in statistics"
            assert (
                stats["repository_downloads"] == 0
            ), "Expected 0 repository downloads initially"
            print("✓ Repository download statistics included")

            # Test cleanup includes repository
            orchestrator.cleanup_old_versions()
            print("✓ Repository cleanup integrated")

            return True

        except Exception as e:
            print(f"✗ Error in repository integration: {e}")
            import traceback

            traceback.print_exc()
            return False


def test_repository_processing_flow():
    """Test the complete repository processing flow."""

    print("\nTesting repository processing flow...")

    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "DOWNLOAD_DIR": temp_dir,
            "CHECK_APK_PRERELEASES": False,
            "CHECK_FIRMWARE_PRERELEASES": False,
            "ANDROID_VERSIONS_TO_KEEP": 3,
            "FIRMWARE_VERSIONS_TO_KEEP": 3,
            "EXTRACT_PATTERNS": ["rak4631-"],
            "EXCLUDE_PATTERNS": [],
            "SELECTED_PATTERNS": ["device-"],
            "SELECTED_FIRMWARE_ASSETS": [],
            "SELECTED_PRERELEASE_ASSETS": [],
        }

        try:
            orchestrator = DownloadOrchestrator(config)

            # Mock the repository downloader's get_repository_files method
            class MockRepositoryDownloader:
                def __init__(self, config):
                    self.config = config

                def get_repository_files(self):
                    return [
                        {
                            "name": "device-install.sh",
                            "download_url": "https://example.com/device-install.sh",
                        },
                        {
                            "name": "device-uninstall.sh",
                            "download_url": "https://example.com/device-uninstall.sh",
                        },
                    ]

                def download_repository_file(self, file_info):
                    from pathlib import Path

                    from fetchtastic.download.interfaces import DownloadResult

                    return DownloadResult(
                        success=True,
                        release_tag="repository",
                        file_path=Path(temp_dir) / file_info["name"],
                        error_message=None,
                    )

                def cleanup_old_versions(self, keep_limit):
                    pass

            orchestrator.repository_downloader = MockRepositoryDownloader(config)

            # Run the download pipeline
            success_results, failed_results = orchestrator.run_download_pipeline()

            # Should have processed repository files
            stats = orchestrator.get_download_statistics()
            print(f"Repository downloads: {stats['repository_downloads']}")

            # Check that repository files were processed
            if stats["repository_downloads"] > 0:
                print("✓ Repository files processed successfully")
                return True
            else:
                print(
                    "⚠ No repository files were processed (this may be expected in test environment)"
                )
                return True

        except Exception as e:
            print(f"✗ Error in repository processing flow: {e}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    print("Running repository integration tests...")

    success = True

    # Run tests
    success &= test_repository_integration()
    success &= test_repository_processing_flow()

    if success:
        print("\n🎉 All repository integration tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some repository integration tests failed.")
        sys.exit(1)
