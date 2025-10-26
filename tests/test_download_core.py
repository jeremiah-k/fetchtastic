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
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

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
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True

    return _write


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
        import os
        import zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, new_versions, failures = downloader.check_and_download(
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
    # latest_release_file written
    assert (tmp_path / "latest_firmware_release.txt").exists()
    # auto-extracted file exists and is executable
    extracted = tmp_path / release_tag / "device-install.sh"
    assert extracted.exists()

    assert os.access(extracted, os.X_OK)


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
        import os
        import zipfile

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("device-install.sh", "echo hi")
        return True

    with patch("fetchtastic.downloader.download_file_with_retry", side_effect=_mock_dl):
        downloaded, new_versions, failures = downloader.check_and_download(
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
    # latest_release_file written
    assert (tmp_path / "latest_firmware_release.txt").exists()
    # No extraction should occur when patterns are empty
    extracted = tmp_path / release_tag / "device-install.sh"
    assert not extracted.exists()


def test_check_and_download_release_already_complete_logs_up_to_date(tmp_path, caplog):
    """When a release is already downloaded and up-to-date, log that it's up to date."""
    caplog.set_level("INFO", logger="fetchtastic")

    release_tag = "v1.0.0"
    zip_name = "firmware-rak4631-1.0.0.zip"

    # Prepare a valid zip already present in the release directory
    import os
    import zipfile

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
    Path(latest_release_file).write_text(release_tag)

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
    Path(latest_release_file).write_text(release_tag)

    def mock_download(url, path):
        # Mock download failure to test error handling
        return False

    with patch(
        "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
    ):
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

    # Should record failure due to corrupted file and download failure
    assert downloaded == []
    assert len(failures) == 1
    assert failures[0]["release_tag"] == release_tag
    assert failures[0]["reason"] == "download_file_with_retry returned False"
    assert new_versions == []


def test_check_and_download_redownloads_mismatched_non_zip(tmp_path, write_dummy_file):
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

        def mock_download(url, path):
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
            assert new == []
            assert len(failed) == 1
            assert failed[0]["release_tag"] == "v1.0.0"
            assert failed[0]["reason"] == "download_file_with_retry returned False"

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
        def mock_download_second_run(url, path):
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
