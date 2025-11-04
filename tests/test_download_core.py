"""
Core download orchestration tests for the fetchtastic downloader module.

This module contains tests for:
- Main download orchestration logic
- Version tracking and cleanup
- Release detection and processing
- Download retry and error handling
- File integrity verification
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fetchtastic import downloader


@pytest.fixture
def write_dummy_file():
    """Fixture that provides a function to write dummy files for download mocking."""

    def _write(dest, data=b"data"):
        """
        Create parent directories for `dest`, write binary `data` to `dest`, and return True.

        Parameters:
            dest (str or Path): Destination file path to write.
            data (bytes): Binary content to write; defaults to b"data".

        Returns:
            bool: Always returns True on successful write.
        """
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with dest_path.open("wb") as f:
            f.write(data)
        return True

    return _write


@pytest.mark.core_downloads
def test_cleanup_old_versions(tmp_path):
    """Test the logic for cleaning up old version directories."""
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()

    # Create some version directories
    (firmware_dir / "v1.0").mkdir()
    (firmware_dir / "v2.0").mkdir()
    (firmware_dir / "v3.0").mkdir()
    (firmware_dir / "repo-dls").mkdir()  # Should be ignored
    (firmware_dir / "prerelease").mkdir()  # Should be ignored

    releases_to_keep = ["v2.0", "v3.0"]
    downloader.cleanup_old_versions(str(firmware_dir), releases_to_keep)

    assert not (firmware_dir / "v1.0").exists()
    assert (firmware_dir / "v2.0").exists()
    assert (firmware_dir / "v3.0").exists()
    assert (firmware_dir / "repo-dls").exists()
    assert (firmware_dir / "prerelease").exists()


@pytest.mark.core_downloads
def test_check_and_download_logs_when_no_assets_match(tmp_path, caplog):
    """When a release is new but no assets match selection, log a helpful message."""
    # Capture logs from 'fetchtastic' logger used by downloader
    caplog.set_level("INFO", logger="fetchtastic")
    # One release with an asset that won't match selected patterns
    releases = [
        {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": "firmware-heltec-v3-1.0.0.zip",
                    "browser_download_url": "https://example.invalid/heltec-v3.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v0.9.0",
            "assets": [
                {
                    "name": "firmware-heltec-v3-0.9.0.zip",
                    "browser_download_url": "https://example.invalid/heltec-0.9.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path / "firmware")

    # Run with a pattern that won't match provided asset name
    # Ensure logger propagates so caplog can capture records regardless of handlers
    from fetchtastic.log_utils import logger as ft_logger

    old_propagate = ft_logger.propagate
    ft_logger.propagate = True
    try:
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=2,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )
    finally:
        ft_logger.propagate = old_propagate

    # No downloads and no failures expected; should note new version available
    assert downloaded == []
    assert failures == []
    assert _new_versions == []
    # Check for the log message - the exact format may vary slightly
    assert "Release v1.0.0 found, but no assets matched" in caplog.text
    assert "current selection/exclude filters" in caplog.text


@pytest.mark.core_downloads
def test_new_versions_detection_with_saved_tag(tmp_path):
    """
    Verify new-release detection honors a saved latest-tag and that only releases newer than the saved tag (by list position, newest-first) are considered — but only releases with matching asset patterns are reported.

    Detailed behavior:
    - Writes a saved tag of "v2" and provides releases in newest-first order (v3, v2, v1).
    - v3 is technically newer than saved tag, but its asset names do not match the provided selected_patterns, so no new_versions or downloads should be recorded.
    - Asserts that no downloads or failures occurred and that new_versions is empty.
    """
    releases = [
        {
            "tag_name": "v3",
            "published_at": "2024-03-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-3.zip",
                    "browser_download_url": "https://example.invalid/3.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v2",
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-2.zip",
                    "browser_download_url": "https://example.invalid/2.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
        {
            "tag_name": "v1",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-heltec-v3-1.zip",
                    "browser_download_url": "https://example.invalid/1.zip",
                    "size": 10,
                }
            ],
            "body": "",
        },
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path / "firmware")

    # Write saved tag
    Path(latest_release_file).write_text("v2")

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        download_dir,
        versions_to_keep=2,
        extract_patterns=[],
        selected_patterns=["rak4631-"],  # Won't match heltec assets
        auto_extract=False,
        exclude_patterns=[],
    )

    # No downloads or failures expected; new_versions should be empty
    assert downloaded == []
    assert failures == []
    assert new_versions == []


@pytest.mark.core_downloads
def test_check_and_download_happy_path_with_extraction(tmp_path, caplog):
    """Covers successful download path, latest tag save, and auto-extract."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.0.0"
    zip_name = "firmware-rak4631-1.0.0.zip"

    # Release data with a single ZIP asset
    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": 100,  # nominal; not strictly enforced in this path
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Mock downloader to write a real ZIP that contains a file we want to auto-extract
    def _mock_dl(_url, dest):
        """
        Create a simple ZIP file at the given destination for use in tests.

        Creates any missing parent directories for dest and writes a ZIP archive containing a single entry
        "device-install.sh" with contents "echo hi". The _url parameter is ignored (present only to match
        downloader call signature).

        Parameters:
            _url (str): Ignored.
            dest (str): Filesystem path where ZIP file will be created.

        Returns:
            bool: True on success.
        """
        # uses top-level imports: os, zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=["device-install.sh"],
            selected_patterns=["rak4631-"],
            auto_extract=True,
            exclude_patterns=[],
        )

    # The release should be considered downloaded
    assert downloaded == [release_tag]
    assert failures == []
    # latest_release_file written (now in JSON format)
    assert (tmp_path / "latest_firmware_release.json").exists()
    # auto-extracted file exists and is executable
    extracted = tmp_path / release_tag / "device-install.sh"
    assert extracted.exists()

    if os.name != "nt":
        assert os.access(extracted, os.X_OK)


@pytest.mark.core_downloads
def test_auto_extract_with_empty_patterns_does_not_extract(tmp_path, caplog):
    """When AUTO_EXTRACT is True but EXTRACT_PATTERNS is empty, do not extract any files."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.2.3"
    zip_name = "firmware-rak4631-1.2.3.zip"

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-02-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": 100,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Mock downloader to write a real ZIP that contains a file that would normally be extracted
    def _mock_dl(_url, dest):
        # uses top-level imports: os, zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],  # Empty patterns
            selected_patterns=["rak4631-"],
            auto_extract=True,  # But empty patterns should prevent extraction
            exclude_patterns=[],
        )

    # The release should be considered downloaded
    assert downloaded == [release_tag]
    assert failures == []
    # latest_release_file written (now in JSON format)
    assert (tmp_path / "latest_firmware_release.json").exists()
    # No extraction should occur when patterns are empty
    extracted = tmp_path / release_tag / "device-install.sh"
    assert not extracted.exists()


@pytest.mark.core_downloads
def test_check_and_download_release_already_complete_logs_up_to_date(tmp_path, caplog):
    """When a release is already downloaded and up-to-date, log that it's up to date."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.0.0"
    zip_name = "firmware-rak4631-1.0.0.zip"

    # Prepare a valid zip already present in the release directory

    release_dir = tmp_path / release_tag
    release_dir.mkdir()
    zip_path = release_dir / zip_name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("foo.txt", "bar")
    size = os.path.getsize(zip_path)

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": size,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Write the latest release file to indicate this release is current
    # Note: The code now reads from JSON files, so we need to write to JSON format
    json_file = tmp_path / "latest_firmware_release.json"
    json_file.write_text('{"latest_version": "v1.0.0", "file_type": "firmware"}')

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        download_dir,
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    # Already complete, so no downloads expected
    assert downloaded == []  # already complete
    assert failures == []
    assert new_versions == []


@pytest.mark.core_downloads
def test_check_and_download_corrupted_existing_zip_records_failure(tmp_path):
    """When an existing ZIP is corrupted, it should be treated as a failure and redownloaded."""
    release_tag = "v1.0.0"
    zip_name = "firmware-rak4631-1.0.0.zip"

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": zip_name,
                    "browser_download_url": "https://example.invalid/firmware.zip",
                    "size": 100,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Pre-create a corrupted ZIP file
    release_dir = tmp_path / release_tag
    release_dir.mkdir()
    corrupted_zip = release_dir / zip_name
    corrupted_zip.write_text("not a zip file")

    # Write the latest release file to indicate this release is current
    # Note: The code now reads from JSON files, so we need to write to JSON format
    json_file = tmp_path / "latest_firmware_release.json"
    json_file.write_text('{"latest_version": "v1.0.0", "file_type": "firmware"}')

    def mock_download(_url, _path):
        # Mock download failure to test error handling
        """
        Simulates a download operation that always fails.
        
        Used in tests to force a download failure for exercising error handling.
        
        Returns:
            False: Indicates the download did not succeed.
        """
        return False

    with patch(
        "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
    ):
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )

    # Should record failure due to corrupted file and download failure
    assert downloaded == []
    assert len(failures) == 1
    assert failures[0]["release_tag"] == release_tag
    assert failures[0]["reason"] == "download_file_with_retry returned False"
    assert _new_versions == []


@pytest.mark.core_downloads
def test_check_and_download_redownloads_mismatched_non_zip(tmp_path):
    """Non-ZIP files with mismatched sizes should be redownloaded."""
    release_tag = "v1.0.0"
    file_name = "firmware-rak4631-1.0.0.bin"

    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": file_name,
                    "browser_download_url": "https://example.invalid/firmware.bin",
                    "size": 100,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    # Pre-create a file with wrong size
    release_dir = tmp_path / release_tag
    release_dir.mkdir()
    existing_file = release_dir / file_name
    existing_file.write_text("wrong size content")  # Much smaller than expected

    # Write the latest release file to indicate this release is current
    Path(latest_release_file).write_text(release_tag)

    with patch(
        "fetchtastic.downloader.download_file_with_retry",
        return_value=True,
    ) as mock_download:
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=1,
            extract_patterns=[],
            selected_patterns=["rak4631-"],
            auto_extract=False,
            exclude_patterns=[],
        )

        # Should redownload due to size mismatch
        assert downloaded == [release_tag]
        assert failures == []
        mock_download.assert_called_once()


@pytest.mark.core_downloads
def test_check_and_download_missing_download_url(tmp_path):
    """Assets missing download_url should be skipped."""
    releases = [
        {
            "tag_name": "v1.0.0",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "firmware-rak4631-1.0.0.zip",
                    # Missing browser_download_url
                    "size": 100,
                }
            ],
            "body": "Release notes",
        }
    ]

    latest_release_file = str(tmp_path / "latest_firmware_release.txt")
    download_dir = str(tmp_path)

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        latest_release_file,
        "Firmware",
        download_dir,
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    # Should skip asset due to missing URL
    assert downloaded == []
    assert len(failures) == 1
    assert failures[0]["release_tag"] == "v1.0.0"
    assert failures[0]["reason"] == "Missing browser_download_url"
    assert new_versions == []


class TestDownloadCoreIntegration:
    """Integration tests for core download functionality."""

    @pytest.mark.core_downloads
    def test_check_and_download_comprehensive_flow(self, mocker, tmp_path):
        """Test comprehensive download flow with all features."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        mock_download_file = mocker.patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        )
        mock_cleanup = mocker.patch("fetchtastic.downloader.cleanup_old_versions")

        downloaded, new, failed = downloader.check_and_download(
            releases,
            latest_release_file,
            "Firmware",
            download_dir,
            versions_to_keep=2,
            extract_patterns=["device-install.sh"],
            selected_patterns=["rak4631-"],
            auto_extract=True,
            exclude_patterns=["*.md"],
        )

        # Should download the release
        assert downloaded == ["v1.0.0"]
        assert new == ["v1.0.0"]
        assert failed == []

        # Check that download was attempted
        mock_download_file.assert_called_once()

        # Check that cleanup was called with correct versions to keep
        mock_cleanup.assert_called_once()

    @pytest.mark.core_downloads
    def test_download_with_multiple_assets_selection(self, tmp_path):
        """Test download behavior with multiple assets and pattern selection."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/rak4631.zip",
                        "size": 1000,
                    },
                    {
                        "name": "firmware-tbeam-1.0.0.zip",
                        "browser_download_url": "https://example.com/tbeam.zip",
                        "size": 1000,
                    },
                    {
                        "name": "firmware-canary-1.0.0.zip",
                        "browser_download_url": "https://example.com/canary.zip",
                        "size": 1000,
                    },
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            # Only select rak4631 and tbeam patterns
            downloaded, new, failed = downloader.check_and_download(
                releases,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-", "tbeam-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Should download the release (matching assets found)
            assert downloaded == ["v1.0.0"]
            assert new == ["v1.0.0"]
            assert failed == []

    @pytest.mark.core_downloads
    def test_download_with_exclude_patterns(self, tmp_path):
        """Test download behavior with exclude patterns."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/rak4631.zip",
                        "size": 1000,
                    },
                    {
                        "name": "firmware-rak4631_eink-1.0.0.zip",
                        "browser_download_url": "https://example.com/rak4631_eink.zip",
                        "size": 1000,
                    },
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            # Select rak4631 but exclude eink variants
            downloaded, new, failed = downloader.check_and_download(
                releases,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=["*eink*"],
            )

            # Should download the release (non-excluded assets found)
            assert downloaded == ["v1.0.0"]
            assert new == ["v1.0.0"]
            assert failed == []

    @pytest.mark.core_downloads
    def test_download_error_handling_and_recovery(self, tmp_path):
        """Test error handling during download process."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            }
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        def mock_download(_url, _path):
            # Mock download that returns False to test error handling
            return False

        with patch(
            "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Should record failure when download returns False
            assert downloaded == []
            assert new == ["v1.0.0"]  # Notify about new version even if download failed
            assert len(failed) == 1
            assert failed[0]["release_tag"] == "v1.0.0"
            assert failed[0]["reason"] == "download_file_with_retry returned False"

    @pytest.mark.core_downloads
    def test_version_tracking_across_multiple_runs(self, tmp_path):
        """Test version tracking across multiple download runs."""
        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        # First run - download v1.0
        releases_v1 = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            }
        ]

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases_v1,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            assert downloaded == ["v1.0.0"]
            assert new == ["v1.0.0"]
            assert failed == []

        # Second run - v1.0 should be up to date
        def mock_download_second_run(_url, _path):
            # This should not be called if file exists and is complete
            # If called, just return True to allow test to continue
            return True

        with patch(
            "fetchtastic.downloader.download_file_with_retry",
            side_effect=mock_download_second_run,
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases_v1,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Should not download again, but if it does, new versions should still be empty
            # since this is not a new version compared to saved tag
            assert new == []
            assert failed == []

        # Third run - new v2.0 available
        releases_v2 = [
            {
                "tag_name": "v2.0.0",
                "published_at": "2024-02-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-2.0.0.zip",
                        "browser_download_url": "https://example.com/firmware.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            },
            *releases_v1,  # Include v1.0 for completeness
        ]

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases_v2,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Should download new v2.0 (v1.0.0 might also be downloaded if not detected as complete)
            assert "v2.0.0" in downloaded
            assert new == ["v2.0.0"]
            assert failed == []

    @pytest.mark.core_downloads
    def test_partial_download_failure_notifies_all_new_versions(self, tmp_path):
        """Test that when some downloads succeed and some fail, all new versions are reported."""
        releases = [
            {
                "tag_name": "v1.0.0",
                "published_at": "2024-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.0.0.zip",
                        "browser_download_url": "https://example.com/firmware1.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            },
            {
                "tag_name": "v1.1.0",
                "published_at": "2024-01-02T00:00:00Z",
                "assets": [
                    {
                        "name": "firmware-rak4631-1.1.0.zip",
                        "browser_download_url": "https://example.com/firmware2.zip",
                        "size": 1000,
                    }
                ],
                "body": "Release notes",
            },
        ]

        latest_release_file = str(tmp_path / "latest.txt")
        download_dir = str(tmp_path / "downloads")

        # Mock download to succeed for v1.0.0 but fail for v1.1.0
        def mock_download(url, _path):
            if "firmware1.zip" in url:
                return True  # v1.0.0 succeeds
            else:
                return False  # v1.1.0 fails

        with patch(
            "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases,
                latest_release_file,
                "Firmware",
                download_dir,
                versions_to_keep=2,
                extract_patterns=[],
                selected_patterns=["rak4631-"],
                auto_extract=False,
                exclude_patterns=[],
            )

            # Should have downloaded v1.0.0 successfully
            assert downloaded == ["v1.0.0"]
            # Should notify about both new versions (success and failure)
            assert set(new) == {"v1.0.0", "v1.1.0"}
            # Should have one failure
            assert len(failed) == 1
            assert failed[0]["release_tag"] == "v1.1.0"


@pytest.mark.core_downloads
def test_compare_file_hashes(tmp_path):
    """Test file hash comparison functionality."""
    from fetchtastic.downloader import compare_file_hashes

    # Create test files
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file3 = tmp_path / "file3.txt"

    content1 = b"test content 1"
    content2 = b"test content 2"

    file1.write_bytes(content1)
    file2.write_bytes(content1)  # Same content as file1
    file3.write_bytes(content2)  # Different content

    # Same content should return True
    assert compare_file_hashes(str(file1), str(file2)) is True

    # Different content should return False
    assert compare_file_hashes(str(file1), str(file3)) is False

    # Non-existent file should return False
    assert compare_file_hashes(str(file1), str(tmp_path / "nonexistent.txt")) is False
    assert compare_file_hashes(str(tmp_path / "nonexistent.txt"), str(file1)) is False

    # Both non-existent should return False
    assert (
        compare_file_hashes(
            str(tmp_path / "nonexistent1.txt"), str(tmp_path / "nonexistent2.txt")
        )
        is False
    )


@pytest.mark.core_downloads
def test_compare_file_hashes_permission_error(tmp_path):
    """Test compare_file_hashes with permission errors."""
    from fetchtastic.downloader import compare_file_hashes

    # Create a file and make it unreadable
    file1 = tmp_path / "readable.txt"
    file2 = tmp_path / "unreadable.txt"

    file1.write_bytes(b"content")
    file2.write_bytes(b"content")

    # Make file2 unreadable (if possible)
    if os.name != "nt":  # Skip on Windows where chmod might not work
        file2.chmod(0o000)
        try:
            # Should return False due to permission error
            result = compare_file_hashes(str(file1), str(file2))
            assert result is False
        finally:
            file2.chmod(0o644)  # Restore permissions for cleanup


@pytest.mark.core_downloads
def test_atomic_write_error_handling(tmp_path):
    """Test _atomic_write error handling."""
    from fetchtastic.downloader import _atomic_write

    # Test with non-existent directory
    nonexistent_dir = tmp_path / "nonexistent" / "deep" / "path"
    file_path = nonexistent_dir / "test.txt"

    # Should handle OSError when creating temp file in non-existent directory
    def writer(f):
        """
        Write the literal string "test" to the given file-like object.

        Parameters:
            f: A writable file-like object with a `write(str)` method; the string "test" will be written to it.
        """
        f.write("test")

    result = _atomic_write(str(file_path), writer, ".txt")
    assert result is False


@pytest.mark.core_downloads
def test_atomic_write_permission_error(tmp_path):
    """Test _atomic_write with permission errors."""
    from fetchtastic.downloader import _atomic_write

    if os.name == "nt":
        pytest.skip("Permission bits unreliable on Windows")

    # Create a read-only directory
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o444)

    try:
        file_path = readonly_dir / "test.txt"

        def writer(f):
            """
            Write the literal string "test" to the provided writable file-like object.

            Parameters:
                f: A writable file-like object with a `write(str)` method.
            """
            f.write("test")

        result = _atomic_write(str(file_path), writer, ".txt")
        assert result is False
    finally:
        readonly_dir.chmod(0o755)  # Restore for cleanup


@pytest.mark.core_downloads
def test_safe_rmtree_additional_edge_cases(tmp_path):
    """Test _safe_rmtree with additional edge cases."""
    from fetchtastic.downloader import _safe_rmtree

    # Test with file instead of directory
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("content")

    result = _safe_rmtree(str(test_file), str(tmp_path), "test_file.txt")
    assert result is True
    assert not test_file.exists()

    # Test with nested path traversal attempt (should fail safely)
    # Create a safe directory structure
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()

    # Try to remove a path that resolves outside (this should fail)
    # Since we're in a test environment, create a path that would resolve outside if not checked
    outside_attempt = tmp_path / ".." / "outside"
    # This should not exist, but _safe_rmtree should handle it
    result = _safe_rmtree(str(outside_attempt), str(tmp_path), "outside")
    assert result is False  # Should fail because path doesn't exist or resolves outside


@pytest.mark.core_downloads
def test_cleanup_superseded_prereleases_file_removal(tmp_path):
    """Test file removal logic in cleanup_superseded_prereleases with various scenarios."""
    from unittest.mock import patch

    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Test successful removal of both files
    # JSON tracking file is now in cache directory, text file in prerelease dir
    json_tracking_file = cache_dir / "prerelease_tracking.json"
    text_tracking_file = prerelease_dir / "prerelease_commits.txt"

    json_tracking_file.write_text('{"version": "v1.0.0", "commits": ["abc123"]}')
    text_tracking_file.write_text("Release: v1.0.0\nabc123\n")

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)):
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")

    # Both files should be removed
    assert not json_tracking_file.exists()
    assert not text_tracking_file.exists()

    # Test OSError during file removal
    json_tracking_file.write_text('{"version": "v1.0.0", "commits": ["abc123"]}')
    text_tracking_file.write_text("Release: v1.0.0\nabc123\n")

    with patch(
        "fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)
    ), patch("os.remove", side_effect=OSError("Permission denied")), patch(
        "fetchtastic.downloader.logger"
    ) as mock_logger:

        # Should not raise exception, should log warning
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")

        # Should log warnings for file removal failures
        assert mock_logger.warning.call_count >= 1

    # Test with no tracking files
    with patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)):
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")
    # Should not raise any exceptions


@pytest.mark.core_downloads
def test_cleanup_superseded_prereleases_file_removal_edge_cases(tmp_path):
    """Test edge cases for file removal in cleanup_superseded_prereleases."""

    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Test with only JSON file exists
    json_tracking_file = cache_dir / "prerelease_tracking.json"
    json_tracking_file.write_text('{"version": "v1.0.0", "commits": ["abc123"]}')

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)):
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")
    assert not json_tracking_file.exists()

    # Recreate for next test
    json_tracking_file.write_text('{"version": "v1.0.0", "commits": ["abc123"]}')

    # Test with only text file exists
    text_tracking_file = prerelease_dir / "prerelease_commits.txt"
    text_tracking_file.write_text("Release: v1.0.0\nabc123\n")

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)):
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")
    assert not text_tracking_file.exists()
    assert not json_tracking_file.exists()  # Should be removed from cache

    # Test with no tracking files
    with patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)):
        cleanup_superseded_prereleases(str(tmp_path), "v2.0.0")
    # Should not raise any exceptions


@pytest.mark.core_downloads
def test_cleanup_legacy_files(tmp_path):
    """Test _cleanup_legacy_files removes legacy files without migration."""
    from fetchtastic.downloader import _cleanup_legacy_files

    # Create mock config and paths_and_urls
    config = {
        "PRERELEASE_DIR": str(tmp_path / "firmware" / "prerelease"),
        "APK_DIR": str(tmp_path / "apks"),
        "FIRMWARE_DIR": str(tmp_path / "firmware"),
    }

    # Create directory structure
    prerelease_dir = tmp_path / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)
    apks_dir = tmp_path / "apks"
    apks_dir.mkdir(parents=True)
    firmware_dir = tmp_path / "firmware"

    # Create legacy files
    prerelease_text = prerelease_dir / "prerelease_commits.txt"
    prerelease_text.write_text("Release: v1.5.0\nabc123\ndef456\n")

    android_legacy = apks_dir / "latest_android_release.txt"
    android_legacy.write_text("v2.3.0")

    firmware_legacy = firmware_dir / "latest_firmware_release.txt"
    firmware_legacy.write_text("v1.8.0")

    # Create paths_and_urls with actual paths
    paths_and_urls = {
        "latest_firmware_release_file": str(firmware_legacy),
        "latest_android_release_file": str(android_legacy),
        "download_dir": str(tmp_path),
    }

    # Verify files exist before cleanup
    assert prerelease_text.exists()
    assert android_legacy.exists()
    assert firmware_legacy.exists()

    # Run cleanup
    _cleanup_legacy_files(config, paths_and_urls)

    # Verify all legacy files are removed
    assert not prerelease_text.exists()
    assert not android_legacy.exists()
    assert not firmware_legacy.exists()

    # Verify no JSON files were created (since we don't migrate)
    assert not (prerelease_dir / "prerelease_tracking.json").exists()
    assert not (apks_dir / "latest_android_release.json").exists()
    assert not (firmware_dir / "latest_firmware_release.json").exists()


@pytest.mark.core_downloads
def test_parse_json_formats_error_handling():
    """Test JSON parsing functions with error handling."""
    from fetchtastic.downloader import _parse_legacy_json_format, _parse_new_json_format

    # Test _parse_new_json_format with invalid data
    invalid_data = {
        "version": "v1.0.0",
        "commits": "not_a_list",
    }  # commits should be list
    commits, release, _last_updated = _parse_new_json_format(invalid_data)
    assert commits == []  # Should reset to empty list
    assert release == "v1.0.0"

    # Test with non-string commits
    invalid_data2 = {
        "version": "v1.0.0",
        "commits": ["valid", 123, None],
    }  # mixed types
    commits, release, _last_updated = _parse_new_json_format(invalid_data2)
    assert commits == ["valid"]  # Should filter out invalid entries
    assert release == "v1.0.0"

    # Test _parse_legacy_json_format with missing keys
    legacy_data = {}  # Empty dict
    commits, release, _last_updated = _parse_legacy_json_format(legacy_data)
    assert commits == []
    assert release is None

    # Test with invalid commits type
    legacy_data2 = {"release": "v1.0.0", "commits": "not_a_list"}
    commits, release, _last_updated = _parse_legacy_json_format(legacy_data2)
    assert commits == []  # Should handle gracefully
    assert release == "v1.0.0"


@pytest.mark.core_downloads
def test_calculate_expected_prerelease_version_edge_cases():
    """Test calculate_expected_prerelease_version with edge cases."""
    from fetchtastic.downloader import calculate_expected_prerelease_version

    # Test with empty string
    result = calculate_expected_prerelease_version("")
    assert result == ""

    # Test with invalid version
    result = calculate_expected_prerelease_version("invalid")
    assert result == ""

    # Test with version missing minor/patch
    result = calculate_expected_prerelease_version("v1")
    assert result == ""

    # Test with valid version
    result = calculate_expected_prerelease_version("v1.2.3")
    assert result == "1.2.4"

    # Test without v prefix
    result = calculate_expected_prerelease_version("1.2.3")
    assert result == "1.2.4"


@pytest.mark.core_downloads
def test_cache_functions_error_handling(tmp_path):
    """
    Ensure loader functions tolerate corrupted cache files without raising exceptions.

    Writes invalid JSON to the commit and releases cache files and verifies that
    fetchtastic.downloader._load_commit_cache and _load_releases_cache handle the
    malformed data safely (do not propagate exceptions).
    """
    from fetchtastic.downloader import _load_commit_cache, _load_releases_cache

    # Test _load_commit_cache with corrupted file
    cache_file = tmp_path / "commit_timestamps.json"
    cache_file.write_text("invalid json")

    # Mock the global cache file path
    with patch(
        "fetchtastic.downloader._get_commit_cache_file", return_value=str(cache_file)
    ):
        # Should not raise exception
        _load_commit_cache()

    # Test _load_releases_cache with corrupted file
    releases_cache_file = tmp_path / "releases.json"
    releases_cache_file.write_text("invalid json")

    with patch(
        "fetchtastic.downloader._get_releases_cache_file",
        return_value=str(releases_cache_file),
    ):
        # Should not raise exception
        _load_releases_cache()


@pytest.mark.core_downloads
def test_extract_version_edge_cases():
    """Test extract_version with edge cases."""
    from fetchtastic.downloader import extract_version

    # Test normal case
    assert extract_version("firmware-1.2.3") == "1.2.3"

    # Test without firmware prefix
    assert extract_version("1.2.3") == "1.2.3"

    # Test with hash
    assert extract_version("firmware-1.2.3.abc123") == "1.2.3.abc123"

    # Test empty string
    assert extract_version("") == ""


@pytest.mark.core_downloads
def test_get_commit_hash_from_dir_edge_cases():
    """Test _get_commit_hash_from_dir with edge cases."""
    from fetchtastic.downloader import _get_commit_hash_from_dir

    # Test valid cases
    assert _get_commit_hash_from_dir("firmware-1.2.3.abc123") == "abc123"
    assert _get_commit_hash_from_dir("firmware-1.2.3.ABCDEF") == "abcdef"

    # Test too short hash
    assert _get_commit_hash_from_dir("firmware-1.2.3.ab") is None

    # Test too long hash
    long_hash = "a" * 41
    assert _get_commit_hash_from_dir(f"firmware-1.2.3.{long_hash}") is None

    # Test no hash
    assert _get_commit_hash_from_dir("firmware-1.2.3") is None

    # Test invalid format
    assert _get_commit_hash_from_dir("invalid") is None


@pytest.mark.core_downloads
def test_normalize_commit_identifier_edge_cases():
    """Test _normalize_commit_identifier with edge cases."""
    from fetchtastic.downloader import _normalize_commit_identifier

    # Test already normalized
    assert _normalize_commit_identifier("1.2.3.abc123", "v1.2.3") == "1.2.3.abc123"

    # Test bare hash with release version
    assert _normalize_commit_identifier("abc123", "v1.2.3") == "1.2.3.abc123"

    # Test bare hash without release version
    assert _normalize_commit_identifier("abc123", None) == "abc123"

    # Test invalid hash
    assert _normalize_commit_identifier("invalid", "v1.2.3") == "invalid"

    # Test case normalization
    assert _normalize_commit_identifier("ABC123", "v1.2.3") == "1.2.3.abc123"


@pytest.mark.core_downloads
def test_read_latest_release_tag(tmp_path):
    """Test _read_latest_release_tag with various scenarios."""

    from fetchtastic.downloader import _read_latest_release_tag

    tmp_path / "latest_test.txt"
    json_file = tmp_path / "latest_test.json"

    # Test with no files
    result = _read_latest_release_tag(str(json_file))
    assert result is None

    # Test with only JSON file
    json_file.write_text('{"latest_version": "v2.0.0", "file_type": "test"}')
    result = _read_latest_release_tag(str(json_file))
    assert result == "v2.0.0"

    # Test with invalid JSON
    json_file.write_text('{"invalid": json}')
    result = _read_latest_release_tag(str(json_file))
    assert result is None  # Should return None for invalid JSON

    # Test with empty JSON
    json_file.write_text('{"latest_version": ""}')
    result = _read_latest_release_tag(str(json_file))
    assert result is None


@pytest.mark.core_downloads
def test_write_latest_release_tag(tmp_path):
    """Test _write_latest_release_tag functionality."""
    import json

    from fetchtastic.downloader import _write_latest_release_tag

    tmp_path / "latest_test.txt"
    json_file = tmp_path / "latest_test.json"

    # Test successful write
    result = _write_latest_release_tag(str(json_file), "v3.0.0", "Test")
    assert result is True
    assert json_file.exists()

    with open(json_file) as f:
        data = json.load(f)
    assert data["latest_version"] == "v3.0.0"
    assert data["file_type"] == "test"

    # Test write failure
    with patch("fetchtastic.downloader._atomic_write_json", return_value=False):
        result = _write_latest_release_tag(str(json_file), "v3.1.0", "Test")
        assert result is False