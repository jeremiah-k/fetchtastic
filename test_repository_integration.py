"""
Tests for repository downloader integration in the orchestrator.
"""

from pathlib import Path

import pytest

from fetchtastic.download.orchestrator import DownloadOrchestrator


@pytest.mark.integration
def test_repository_downloader_initialization(tmp_path: Path):
    """Test that repository downloader is properly initialized in orchestrator."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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

    # Create orchestrator
    orchestrator = DownloadOrchestrator(config)

    # Verify that repository downloader is initialized
    assert hasattr(
        orchestrator, "repository_downloader"
    ), "Repository downloader not initialized"

    # Verify that repository downloader has the correct config
    assert (
        orchestrator.repository_downloader.config == config
    ), "Repository downloader config mismatch"


@pytest.mark.unit
def test_repository_processing_methods_exist(tmp_path: Path):
    """Test that repository processing methods exist in orchestrator."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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

    orchestrator = DownloadOrchestrator(config)

    # Test that repository processing method exists
    assert hasattr(
        orchestrator, "_process_repository_downloads"
    ), "Repository processing method missing"

    # Test that repository file filtering method exists
    assert hasattr(
        orchestrator, "_filter_repository_files"
    ), "Repository file filtering method missing"

    # Test that repository file download method exists
    assert hasattr(
        orchestrator, "_download_repository_file"
    ), "Repository file download method missing"


@pytest.mark.unit
def test_repository_file_filtering(tmp_path: Path):
    """Test repository file filtering with selection and exclude patterns."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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
    filtered_files = orchestrator_with_patterns._filter_repository_files(test_files)

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


@pytest.mark.unit
def test_repository_download_statistics(tmp_path: Path):
    """Test that repository download statistics are included."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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

    orchestrator = DownloadOrchestrator(config)

    # Test statistics include repository downloads
    stats = orchestrator.get_download_statistics()
    assert "repository_downloads" in stats, "Repository downloads not in statistics"
    assert (
        stats["repository_downloads"] == 0
    ), "Expected 0 repository downloads initially"


@pytest.mark.unit
def test_repository_cleanup_integration(tmp_path: Path):
    """Test that repository cleanup is integrated."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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

    orchestrator = DownloadOrchestrator(config)

    # Test cleanup includes repository
    orchestrator.cleanup_old_versions()


@pytest.mark.integration
def test_repository_processing_flow(tmp_path: Path):
    """Test the complete repository processing flow."""
    from unittest.mock import patch

    from fetchtastic.download.interfaces import DownloadResult

    config = {
        "DOWNLOAD_DIR": str(tmp_path),
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
            # Actually create the file to make the test deterministic
            file_path = tmp_path / file_info["name"]
            file_path.write_text("#!/bin/bash\necho 'test'")
            return DownloadResult(
                success=True,
                release_tag="repository",
                file_path=file_path,
                error_message=None,
            )

        def cleanup_old_versions(self, keep_limit):
            pass

    # Mock the repository downloader
    with patch.object(
        orchestrator, "repository_downloader", MockRepositoryDownloader(config)
    ):
        # Run the download pipeline
        _success_results, _failed_results = orchestrator.run_download_pipeline()

        # Should have processed repository files
        stats = orchestrator.get_download_statistics()
        assert (
            stats["repository_downloads"] == 2
        ), f"Expected 2 repository downloads, got {stats['repository_downloads']}"

        # Verify files were actually created
        assert (tmp_path / "device-install.sh").exists()
        assert (tmp_path / "device-uninstall.sh").exists()
