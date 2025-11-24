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

    cache_dir = str(tmp_path)
    download_dir = str(tmp_path / "firmware")

    # Run with a pattern that won't match provided asset name
    # Ensure logger propagates so caplog can capture records regardless of handlers
    from fetchtastic.log_utils import logger as ft_logger

    old_propagate = ft_logger.propagate
    ft_logger.propagate = True
    try:
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            cache_dir,
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

    cache_dir = str(tmp_path)
    download_dir = str(tmp_path / "firmware")

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        cache_dir,
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

    cache_dir = str(tmp_path)
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
            cache_dir,
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

    cache_dir = str(tmp_path)
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
            cache_dir,
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

    cache_dir = str(tmp_path)
    download_dir = str(tmp_path)

    # Write the latest release file to indicate this release is current
    # Note: The code now reads from JSON files, so we need to write to JSON format
    json_file = tmp_path / "latest_firmware_release.json"
    json_file.write_text('{"latest_version": "v1.0.0", "file_type": "firmware"}')

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        cache_dir,
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

    cache_dir = str(tmp_path)
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
        Simulates a download failure for tests.

        Returns:
            `False` indicating the download did not succeed.
        """
        return False

    with patch(
        "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
    ):
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            cache_dir,
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

    cache_dir = str(tmp_path)
    download_dir = str(tmp_path)

    # Pre-create a file with wrong size
    release_dir = tmp_path / release_tag
    release_dir.mkdir()
    existing_file = release_dir / file_name
    existing_file.write_text("wrong size content")  # Much smaller than expected

    # Write the latest release file to indicate this release is current
    json_file = tmp_path / "latest_firmware_release.json"
    json_file.write_text(
        f'{{"latest_version": "{release_tag}", "file_type": "firmware"}}'
    )

    with patch(
        "fetchtastic.downloader.download_file_with_retry",
        return_value=True,
    ) as mock_download:
        downloaded, _new_versions, failures = downloader.check_and_download(
            releases,
            cache_dir,
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

    cache_dir = str(tmp_path)
    download_dir = str(tmp_path)

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        cache_dir,
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

        cache_dir = str(tmp_path)
        download_dir = str(tmp_path / "downloads")

        mock_download_file = mocker.patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        )
        mock_cleanup = mocker.patch("fetchtastic.downloader.cleanup_old_versions")

        downloaded, new, failed = downloader.check_and_download(
            releases,
            cache_dir,
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

        cache_dir = str(tmp_path)
        download_dir = str(tmp_path / "downloads")

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            # Only select rak4631 and tbeam patterns
            downloaded, new, failed = downloader.check_and_download(
                releases,
                cache_dir,
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

        cache_dir = str(tmp_path)
        download_dir = str(tmp_path / "downloads")

        with patch(
            "fetchtastic.downloader.download_file_with_retry", return_value=True
        ):
            # Select rak4631 but exclude eink variants
            downloaded, new, failed = downloader.check_and_download(
                releases,
                cache_dir,
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

        cache_dir = str(tmp_path)
        download_dir = str(tmp_path / "downloads")

        def mock_download(_url, _path):
            # Mock download that returns False to test error handling
            """
            Simulates a download that always fails, used for testing error handling.

            Parameters:
                _url (str): URL argument (ignored).
                _path (str | Path): Destination path argument (ignored).

            Returns:
                bool: `False` indicating the download failed.
            """
            return False

        with patch(
            "fetchtastic.downloader.download_file_with_retry", side_effect=mock_download
        ):
            downloaded, new, failed = downloader.check_and_download(
                releases,
                cache_dir,
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
        cache_dir = str(tmp_path)
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
                cache_dir,
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
                cache_dir,
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
                cache_dir,
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

        cache_dir = str(tmp_path)
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
                cache_dir,
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

    with (
        patch("fetchtastic.downloader._ensure_cache_dir", return_value=str(cache_dir)),
        patch("os.remove", side_effect=OSError("Permission denied")),
        patch("fetchtastic.downloader.logger") as mock_logger,
    ):
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


def test_is_apk_prerelease():
    """Test _is_apk_prerelease function correctly identifies prereleases."""
    from fetchtastic.downloader import _is_apk_prerelease, _is_apk_prerelease_by_name

    # Test legacy prerelease tags with string-based function
    assert _is_apk_prerelease_by_name("v2.7.7-open.1") is True
    assert _is_apk_prerelease_by_name("v2.7.7-open.4") is True
    assert _is_apk_prerelease_by_name("v2.7.7-closed.1") is True
    assert _is_apk_prerelease_by_name("v2.7.7-OPEN.1") is True  # Case insensitive
    assert _is_apk_prerelease_by_name("v2.7.7-CLOSED.1") is True  # Case insensitive

    # Test regular releases with string-based function
    assert _is_apk_prerelease_by_name("v2.7.7") is False
    assert _is_apk_prerelease_by_name("v2.7.6") is False
    assert _is_apk_prerelease_by_name("v2.7.7-beta") is False  # Different suffix
    assert _is_apk_prerelease_by_name("v2.7.7-rc1") is False  # Different suffix

    # Test with release objects (new functionality)
    legacy_prerelease = {"tag_name": "v2.7.7-open.1", "prerelease": False}
    github_prerelease = {"tag_name": "v2.7.8-beta1", "prerelease": True}
    regular_release = {"tag_name": "v2.7.7", "prerelease": False}

    assert _is_apk_prerelease(legacy_prerelease) is True  # Legacy pattern
    assert _is_apk_prerelease(github_prerelease) is True  # GitHub prerelease flag
    assert _is_apk_prerelease(regular_release) is False  # Regular release


def test_cleanup_apk_prereleases(tmp_path):
    """Test _cleanup_apk_prereleases removes obsolete prerelease directories."""
    from fetchtastic.downloader import _cleanup_apk_prereleases

    prerelease_dir = tmp_path / "prereleases"
    prerelease_dir.mkdir()

    # Create prerelease directories
    (prerelease_dir / "v2.7.7-open.1").mkdir()
    (prerelease_dir / "v2.7.7-open.2").mkdir()
    (prerelease_dir / "v2.7.6-open.1").mkdir()  # Older version
    (
        prerelease_dir / "v2.7.7-pr1"
    ).mkdir()  # GitHub-flagged prerelease name without -open/-closed
    (prerelease_dir / "v2.8.0-open.1").mkdir()  # Newer version, should remain

    # Call cleanup with full release v2.7.7
    _cleanup_apk_prereleases(str(prerelease_dir), "v2.7.7")

    # Check that prereleases up to and including 2.7.7 are removed
    assert not (prerelease_dir / "v2.7.7-open.1").exists()
    assert not (prerelease_dir / "v2.7.7-open.2").exists()
    assert not (prerelease_dir / "v2.7.6-open.1").exists()
    assert not (prerelease_dir / "v2.7.7-pr1").exists()
    # Newer prerelease should still exist
    assert (prerelease_dir / "v2.8.0-open.1").exists()


@pytest.mark.core_downloads
def test_process_apk_downloads_enhanced_with_prereleases_enabled(tmp_path):
    """Prerel enabled with mixed releases should process stable releases and skip superseded prereleases."""
    from unittest.mock import patch

    from fetchtastic.downloader import _process_apk_downloads

    # Mock release data with both regular and prerelease
    mock_releases = [
        {"tag_name": "v2.7.7", "assets": [{"name": "app-release.apk"}]},
        {"tag_name": "v2.7.7-open.1", "assets": [{"name": "app-open.apk"}]},
        {"tag_name": "v2.7.6", "assets": [{"name": "app-older.apk"}]},
        {"tag_name": "v2.7.7-open.2", "assets": [{"name": "app-open2.apk"}]},
    ]

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["app-release.apk", "app-open.apk"],
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "CHECK_APK_PRERELEASES": True,
    }

    paths_and_urls = {
        "cache_dir": str(tmp_path / "cache"),
        "apks_dir": str(tmp_path / "apks"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/meshtastic-android/releases",
    }

    with (
        patch(
            "fetchtastic.downloader._get_latest_releases_data",
            return_value=mock_releases,
        ),
        patch("fetchtastic.downloader.check_and_download") as mock_download,
        patch("fetchtastic.downloader._summarise_release_scan") as mock_summarise,
    ):
        mock_download.return_value = (["v2.7.7"], ["v2.7.7"], [])

        result = _process_apk_downloads(config, paths_and_urls, force_refresh=False)

        # Verify regular releases and prereleases were separated correctly
        mock_summarise.assert_called_once_with("Android APK", 2, 2)

        # Prereleases are superseded by the latest stable release, so only regular releases are processed
        assert mock_download.call_count == 1

        regular_call = mock_download.call_args_list[0]

        # Regular releases call should only include non-prerelease tags
        regular_releases_arg = regular_call[0][0]
        assert len(regular_releases_arg) == 2  # v2.7.7 and v2.7.6
        assert all(
            not r["tag_name"].endswith("-open.1")
            and not r["tag_name"].endswith("-open.2")
            for r in regular_releases_arg
        )

        # Verify result includes no latest prerelease version because they were filtered out
        (
            _downloaded_apks,
            _new_versions,
            _failed,
            _latest_version,
            latest_prerelease,
        ) = result
        assert latest_prerelease is None


@pytest.mark.core_downloads
def test_process_apk_downloads_skips_legacy_prerelease_tags(tmp_path):
    """Ensure legacy (<2.7.0) tags are ignored entirely and cannot surface as prereleases."""
    from unittest.mock import patch

    from fetchtastic.downloader import _process_apk_downloads

    mock_releases = [
        {
            "tag_name": "v2.7.7",
            "prerelease": False,
            "assets": [{"name": "app-google-release.apk"}],
        },
        {
            # Real legacy sample: no leading "v", beta naming, and different asset names
            "tag_name": "2.6.30",
            "name": "Meshtastic Android 2.6.30 beta",
            "prerelease": True,  # even if flagged, we should ignore due to version cutoff
            "assets": [
                {"name": "fdroidRelease-2.6.30.apk"},
                {"name": "googleRelease-2.6.30.aab"},
                {"name": "googleRelease-2.6.30.apk"},
                {"name": "version_info.txt"},
            ],
        },
    ]

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["app-release.apk"],
        "ANDROID_VERSIONS_TO_KEEP": 1,
        "CHECK_APK_PRERELEASES": True,
    }

    paths_and_urls = {
        "cache_dir": str(tmp_path / "cache"),
        "apks_dir": str(tmp_path / "apks"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/meshtastic-android/releases",
    }

    with (
        patch(
            "fetchtastic.downloader._get_latest_releases_data",
            return_value=mock_releases,
        ),
        patch(
            "fetchtastic.downloader.check_and_download",
            return_value=(["v2.7.7"], ["v2.7.7"], []),
        ) as mock_download,
        patch("fetchtastic.downloader._summarise_release_scan") as mock_summarise,
    ):
        result = _process_apk_downloads(config, paths_and_urls, force_refresh=False)

        mock_summarise.assert_called_once_with("Android APK", 1, 1)
        mock_download.assert_called_once()

        regular_releases_arg = mock_download.call_args_list[0][0][0]
        assert len(regular_releases_arg) == 1
        assert regular_releases_arg[0]["tag_name"] == "v2.7.7"

        (
            _downloaded,
            _new_versions,
            _failed,
            _latest_version,
            latest_prerelease,
        ) = result
        assert latest_prerelease is None


@pytest.mark.core_downloads
def test_process_apk_downloads_enhanced_with_prereleases_disabled(tmp_path):
    """Test enhanced _process_apk_downloads with prereleases disabled."""
    from unittest.mock import patch

    from fetchtastic.downloader import _process_apk_downloads

    # Mock release data with both regular and prerelease
    mock_releases = [
        {"tag_name": "v2.7.7", "assets": [{"name": "app-release.apk"}]},
        {"tag_name": "v2.7.7-open.1", "assets": [{"name": "app-open.apk"}]},
        {"tag_name": "v2.7.6", "assets": [{"name": "app-older.apk"}]},
    ]

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["app-release.apk"],
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "CHECK_APK_PRERELEASES": False,  # Disabled
    }

    paths_and_urls = {
        "cache_dir": str(tmp_path / "cache"),
        "apks_dir": str(tmp_path / "apks"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/meshtastic-android/releases",
    }

    with (
        patch(
            "fetchtastic.downloader._get_latest_releases_data",
            return_value=mock_releases,
        ),
        patch("fetchtastic.downloader.check_and_download") as mock_download,
        patch("fetchtastic.downloader._summarise_release_scan") as mock_summarise,
    ):
        mock_download.return_value = (["v2.7.7"], ["v2.7.7"], [])

        result = _process_apk_downloads(config, paths_and_urls, force_refresh=False)

        # Verify regular releases were processed
        mock_summarise.assert_called_once_with("Android APK", 2, 2)

        # Verify check_and_download was called only once (for regular releases only)
        assert mock_download.call_count == 1

        # Verify call was made with only regular releases
        regular_releases_arg = mock_download.call_args_list[0][0][0]
        assert len(regular_releases_arg) == 2  # v2.7.7 and v2.7.6
        assert all(not r["tag_name"].endswith("-open.1") for r in regular_releases_arg)

        # Verify result includes no latest prerelease version
        (
            _downloaded_apks,
            _new_versions,
            _failed,
            _latest_version,
            latest_prerelease,
        ) = result
        assert latest_prerelease is None


@pytest.mark.core_downloads
def test_process_apk_downloads_enhanced_no_regular_releases(tmp_path):
    """Test enhanced _process_apk_downloads with only prereleases available."""
    from unittest.mock import patch

    from fetchtastic.downloader import _process_apk_downloads

    # Mock release data with only prereleases
    mock_releases = [
        {"tag_name": "v2.7.7-open.1", "assets": [{"name": "app-open.apk"}]},
        {"tag_name": "v2.7.7-open.2", "assets": [{"name": "app-open2.apk"}]},
    ]

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["app-open.apk"],
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "CHECK_APK_PRERELEASES": True,
    }

    paths_and_urls = {
        "cache_dir": str(tmp_path / "cache"),
        "apks_dir": str(tmp_path / "apks"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/meshtastic-android/releases",
    }

    with (
        patch(
            "fetchtastic.downloader._get_latest_releases_data",
            return_value=mock_releases,
        ),
        patch("fetchtastic.downloader.check_and_download") as mock_download,
        patch("fetchtastic.downloader._summarise_release_scan") as mock_summarise,
    ):
        mock_download.return_value = (["v2.7.7-open.1"], ["v2.7.7-open.1"], [])

        result = _process_apk_downloads(config, paths_and_urls, force_refresh=False)

        # Verify no regular releases summary was called
        mock_summarise.assert_not_called()

        # Verify check_and_download was called only once (for prereleases only)
        assert mock_download.call_count == 1

        # Verify call was made with only prereleases
        prerelease_releases_arg = mock_download.call_args_list[0][0][0]
        assert len(prerelease_releases_arg) == 2  # Both prereleases
        assert all(
            r["tag_name"].endswith("-open.1") or r["tag_name"].endswith("-open.2")
            for r in prerelease_releases_arg
        )

        # Verify result includes latest prerelease version but no regular version
        (
            _downloaded_apks,
            _new_versions,
            _failed,
            latest_version,
            latest_prerelease,
        ) = result
        assert latest_version is None  # No regular releases
        assert latest_prerelease == "v2.7.7-open.1"


@pytest.mark.core_downloads
def test_process_apk_downloads_enhanced_prerelease_cleanup(tmp_path):
    """Test enhanced _process_apk_downloads calls cleanup when full release available."""
    from unittest.mock import patch

    from fetchtastic.downloader import _process_apk_downloads

    # Mock release data with both regular and prerelease
    mock_releases = [
        {"tag_name": "v2.7.7", "assets": [{"name": "app-release.apk"}]},
        {"tag_name": "v2.7.7-open.1", "assets": [{"name": "app-open.apk"}]},
        {"tag_name": "v2.7.6", "assets": [{"name": "app-older.apk"}]},
    ]

    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": ["app-release.apk"],
        "ANDROID_VERSIONS_TO_KEEP": 2,
        "CHECK_APK_PRERELEASES": True,
    }

    paths_and_urls = {
        "cache_dir": str(tmp_path / "cache"),
        "apks_dir": str(tmp_path / "apks"),
        "android_releases_url": "https://api.github.com/repos/meshtastic/meshtastic-android/releases",
    }

    with (
        patch(
            "fetchtastic.downloader._get_latest_releases_data",
            return_value=mock_releases,
        ),
        patch(
            "fetchtastic.downloader.check_and_download",
            return_value=(["v2.7.7"], ["v2.7.7"], []),
        ),
        patch("fetchtastic.downloader._summarise_release_scan"),
        patch("fetchtastic.downloader._cleanup_apk_prereleases") as mock_cleanup,
    ):
        _process_apk_downloads(config, paths_and_urls, force_refresh=False)

        # Verify cleanup was called because we have full releases
        mock_cleanup.assert_called_once()

        # Verify cleanup was called with correct parameters
        cleanup_call_args = mock_cleanup.call_args[0]
        expected_prerelease_dir = str(tmp_path / "apks" / "prerelease")
        expected_full_release_tag = "v2.7.7"

        assert cleanup_call_args[0] == expected_prerelease_dir
        assert cleanup_call_args[1] == expected_full_release_tag


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


@pytest.mark.core_downloads
def test_ensure_cache_dir(tmp_path):
    """Test _ensure_cache_dir creates and returns cache directory path."""
    from unittest.mock import patch

    from fetchtastic.downloader import _ensure_cache_dir

    with patch("platformdirs.user_cache_dir") as mock_user_cache_dir:
        mock_user_cache_dir.return_value = str(tmp_path / "test_cache")

        cache_dir = _ensure_cache_dir()

        assert cache_dir == str(tmp_path / "test_cache")
        assert os.path.exists(cache_dir)
        mock_user_cache_dir.assert_called_once_with("fetchtastic")


@pytest.mark.core_downloads
def test_atomic_write_json_success(tmp_path):
    """Test _atomic_write_json successful write."""
    import json

    from fetchtastic.downloader import _atomic_write_json

    test_file = tmp_path / "test_output.json"
    test_data = {"key": "value", "number": 42}

    result = _atomic_write_json(str(test_file), test_data)

    assert result is True
    assert test_file.exists()

    with open(test_file, "r") as f:
        loaded_data = json.load(f)

    assert loaded_data == test_data


@pytest.mark.core_downloads
def test_atomic_write_json_failure(tmp_path):
    """Test _atomic_write_json handles write failures."""
    from unittest.mock import patch

    from fetchtastic.downloader import _atomic_write_json

    test_file = tmp_path / "test_output.json"
    test_data = {"key": "value"}

    with patch("fetchtastic.downloader._atomic_write", return_value=False):
        result = _atomic_write_json(str(test_file), test_data)
        assert result is False


@pytest.mark.core_downloads
def test_load_json_cache_with_expiry_no_file(tmp_path):
    """Test _load_json_cache_with_expiry with non-existent file."""
    from fetchtastic.downloader import _load_json_cache_with_expiry

    non_existent_file = str(tmp_path / "non_existent.json")

    def dummy_validator(entry):
        """
        Always treats the provided entry as valid.

        Returns:
            `True` for any input.
        """
        return True

    def dummy_processor(entry, cached_at):
        """
        Return the entry unchanged.

        Parameters:
            entry: Object to return unchanged.
            cached_at: Timestamp or metadata when the entry was cached; accepted but ignored.

        Returns:
            The same `entry` object that was passed in.
        """
        return entry

    result = _load_json_cache_with_expiry(
        cache_file_path=non_existent_file,
        expiry_hours=1.0,
        cache_entry_validator=dummy_validator,
        entry_processor=dummy_processor,
        cache_name="test cache",
    )

    assert result == {}


@pytest.mark.core_downloads
def test_load_json_cache_with_expiry_invalid_json(tmp_path):
    """Test _load_json_cache_with_expiry with invalid JSON."""
    from fetchtastic.downloader import _load_json_cache_with_expiry

    invalid_json_file = tmp_path / "invalid.json"
    invalid_json_file.write_text("{ invalid json content")

    def dummy_validator(entry):
        """
        Always treats the provided entry as valid.

        Returns:
            `True` for any input.
        """
        return True

    def dummy_processor(entry, cached_at):
        """
        Return the entry unchanged.

        Parameters:
            entry: Object to return unchanged.
            cached_at: Timestamp or metadata when the entry was cached; accepted but ignored.

        Returns:
            The same `entry` object that was passed in.
        """
        return entry

    result = _load_json_cache_with_expiry(
        cache_file_path=str(invalid_json_file),
        expiry_hours=1.0,
        cache_entry_validator=dummy_validator,
        entry_processor=dummy_processor,
        cache_name="test cache",
    )

    assert result == {}


@pytest.mark.core_downloads
def test_load_json_cache_with_expiry_wrong_type(tmp_path):
    """Test _load_json_cache_with_expiry with non-dict JSON."""
    from fetchtastic.downloader import _load_json_cache_with_expiry

    wrong_type_file = tmp_path / "wrong_type.json"
    wrong_type_file.write_text('["not", "a", "dict"]')

    def dummy_validator(entry):
        """
        Always treats the provided entry as valid.

        Returns:
            `True` for any input.
        """
        return True

    def dummy_processor(entry, cached_at):
        """
        Return the entry unchanged.

        Parameters:
            entry: Object to return unchanged.
            cached_at: Timestamp or metadata when the entry was cached; accepted but ignored.

        Returns:
            The same `entry` object that was passed in.
        """
        return entry

    result = _load_json_cache_with_expiry(
        cache_file_path=str(wrong_type_file),
        expiry_hours=1.0,
        cache_entry_validator=dummy_validator,
        entry_processor=dummy_processor,
        cache_name="test cache",
    )

    assert result == {}


@pytest.mark.core_downloads
def test_load_json_cache_with_expiry_expired_entries(tmp_path):
    """Test _load_json_cache_with_expiry filters out expired entries."""
    import json
    from datetime import datetime, timedelta, timezone

    from fetchtastic.downloader import _load_json_cache_with_expiry

    cache_file = tmp_path / "test_cache.json"
    past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    cache_data = {
        "valid_entry": {
            "data": "valid",
            "cached_at": datetime.now(timezone.utc).isoformat(),
        },
        "expired_entry": {"data": "expired", "cached_at": past_time},
    }

    cache_file.write_text(json.dumps(cache_data))

    def dummy_validator(entry):
        """
        Check whether a cache entry contains the required metadata keys.

        Parameters:
            entry (Mapping): The mapping to validate.

        Returns:
            bool: `True` if `entry` contains both the `cached_at` and `data` keys, `False` otherwise.
        """
        return "cached_at" in entry and "data" in entry

    def dummy_processor(entry, cached_at):
        """
        Return the value of the "data" key from a cache entry.

        Parameters:
            entry (dict): Cache entry mapping that must contain a "data" key.
            cached_at: Ignored; timestamp or metadata for when the entry was cached.

        Returns:
            The value stored under the "data" key in `entry`.
        """
        return entry["data"]

    result = _load_json_cache_with_expiry(
        cache_file_path=str(cache_file),
        expiry_hours=1.0,
        cache_entry_validator=dummy_validator,
        entry_processor=dummy_processor,
        cache_name="test cache",
    )

    assert result == {"valid_entry": "valid"}
    assert "expired_entry" not in result


@pytest.mark.core_downloads
def test_sanitize_path_component_valid_inputs():
    """Test _sanitize_path_component with valid inputs."""
    from fetchtastic.downloader import _sanitize_path_component

    # Test normal strings
    assert _sanitize_path_component("valid-name") == "valid-name"
    assert _sanitize_path_component("valid_name") == "valid_name"
    assert _sanitize_path_component("name123") == "name123"

    # Test with whitespace
    assert _sanitize_path_component("  trimmed  ") == "trimmed"

    # Test None
    assert _sanitize_path_component(None) is None


@pytest.mark.core_downloads
def test_sanitize_path_component_invalid_inputs():
    """Test _sanitize_path_component with invalid inputs."""
    from fetchtastic.downloader import _sanitize_path_component

    # Test empty string
    assert _sanitize_path_component("") is None
    assert _sanitize_path_component("   ") is None

    # Test dangerous paths
    assert _sanitize_path_component(".") is None
    assert _sanitize_path_component("..") is None
    assert _sanitize_path_component("../dangerous") is None
    assert _sanitize_path_component("path/with/slashes") is None


@pytest.mark.core_downloads
def test_normalize_version_prerelease_parsing():
    """Test _normalize_version handles prerelease versions correctly."""
    from fetchtastic.downloader import _normalize_version

    # Test alpha prereleases
    result = _normalize_version("v1.0.0-alpha")
    assert result is not None
    assert hasattr(result, "is_prerelease")
    assert result.is_prerelease is True

    # Test beta prereleases
    result = _normalize_version("v2.1.0-beta")
    assert result is not None
    assert result.is_prerelease is True

    # Test with numbers
    result = _normalize_version("v1.0.0-alpha2")
    assert result is not None
    assert result.is_prerelease is True


@pytest.mark.core_downloads
def test_normalize_version_hash_suffix():
    """Test _normalize_version handles hash suffix versions."""
    from fetchtastic.downloader import _normalize_version

    # Test hash suffix
    result = _normalize_version("v1.0.0+abc123")
    assert result is not None
    assert hasattr(result, "local")
    assert str(result.local) == "abc123"


@pytest.mark.core_downloads
def test_normalize_version_invalid_prerelease():
    """Test _normalize_version handles invalid prerelease versions."""
    from fetchtastic.downloader import _normalize_version

    # Test invalid prerelease that should fall back to natural sort
    result = _normalize_version("v1.0.0-invalid")
    # Should not crash and should return some version object
    assert result is not None


@pytest.mark.core_downloads
def test_cleanup_superseded_prereleases_unsafe_tag(tmp_path):
    """Test cleanup_superseded_prereleases with unsafe tag."""
    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir()

    # Test with unsafe tag (contains path traversal)
    result = cleanup_superseded_prereleases(str(tmp_path), "../../../unsafe")

    # Should return False and not crash
    assert result is False


@pytest.mark.core_downloads
def test_cleanup_superseded_prereleases_prerelease_as_latest(tmp_path):
    """Test cleanup when latest release is itself a prerelease."""
    from fetchtastic.downloader import cleanup_superseded_prereleases

    # Create directory structure
    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    prerelease_dir = firmware_dir / "prerelease"
    prerelease_dir.mkdir()

    # Test with prerelease as latest - should not clean up
    result = cleanup_superseded_prereleases(str(tmp_path), "v1.0.0-alpha")

    # Should return False since latest is prerelease
    assert result is False


@pytest.mark.core_downloads
def test_get_commit_cache_file():
    """Test _get_commit_cache_file returns correct path."""
    from unittest.mock import patch

    from fetchtastic.downloader import _get_commit_cache_file

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        result = _get_commit_cache_file()
        assert result == "/test/cache/commit_timestamps.json"


@pytest.mark.core_downloads
def test_get_prerelease_dir_cache_file():
    """Test _get_prerelease_dir_cache_file returns correct path."""
    from unittest.mock import patch

    from fetchtastic.downloader import _get_prerelease_dir_cache_file

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        result = _get_prerelease_dir_cache_file()
        assert result == "/test/cache/prerelease_dirs.json"


@pytest.mark.core_downloads
def test_get_release_tuple_valid_versions():
    """Test _get_release_tuple with valid version strings."""
    from fetchtastic.downloader import _get_release_tuple

    # Test normal version
    result = _get_release_tuple("v1.2.3")
    assert result == (1, 2, 3)

    # Test without v prefix
    result = _get_release_tuple("2.0.0")
    assert result == (2, 0, 0)


@pytest.mark.core_downloads
def test_get_release_tuple_invalid_versions():
    """Test _get_release_tuple with invalid version strings."""
    from fetchtastic.downloader import _get_release_tuple

    # Test non-numeric version
    result = _get_release_tuple("not.a.version")
    assert result is None

    # Test empty string
    result = _get_release_tuple("")
    assert result is None


@pytest.mark.core_downloads
def test_ensure_v_prefix_if_missing():
    """Test _ensure_v_prefix_if_missing adds v prefix correctly."""
    from fetchtastic.downloader import _ensure_v_prefix_if_missing

    # Test without prefix
    assert _ensure_v_prefix_if_missing("1.0.0") == "v1.0.0"
    assert _ensure_v_prefix_if_missing("2.1.3") == "v2.1.3"

    # Test with prefix already present
    assert _ensure_v_prefix_if_missing("v1.0.0") == "v1.0.0"
    assert _ensure_v_prefix_if_missing("V2.0.0") == "V2.0.0"  # Should preserve case

    # Test None
    assert _ensure_v_prefix_if_missing(None) is None

    # Test empty string
    assert _ensure_v_prefix_if_missing("") == ""
    assert _ensure_v_prefix_if_missing("   ") == ""


@pytest.mark.core_downloads
def test_load_prerelease_dir_cache_double_checked_locking():
    """Test _load_prerelease_dir_cache uses double-checked locking."""
    from unittest.mock import patch

    from fetchtastic.downloader import _load_prerelease_dir_cache

    # Mock cache as already loaded
    with patch("fetchtastic.downloader._prerelease_dir_cache_loaded", True):
        with patch("fetchtastic.downloader._load_json_cache_with_expiry") as mock_load:
            _load_prerelease_dir_cache()

            # Should not call load since cache is already loaded
            mock_load.assert_not_called()


@pytest.mark.core_downloads
def test_save_prerelease_dir_cache():
    """Test _save_prerelease_dir_cache saves cache correctly."""
    from datetime import datetime
    from unittest.mock import patch

    from fetchtastic.downloader import _save_prerelease_dir_cache

    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        with patch(
            "fetchtastic.downloader._atomic_write_json", return_value=True
        ) as mock_write:
            # Mock the cache as a dict with correct structure: Dict[str, Tuple[List[str], datetime]]
            with patch(
                "fetchtastic.downloader._prerelease_dir_cache",
                {"test": (["dir1"], datetime(2023, 1, 1))},
            ):
                _save_prerelease_dir_cache()

                # Should call atomic write with correct data
                mock_write.assert_called_once()
                call_args = mock_write.call_args[0]
                assert call_args[0] == "/test/cache/prerelease_dirs.json"
                assert isinstance(call_args[1], dict)
                assert "test" in call_args[1]
                assert call_args[1]["test"]["directories"] == ["dir1"]
                assert call_args[1]["test"]["cached_at"] == "2023-01-01T00:00:00"


@pytest.mark.core_downloads
def test_get_json_release_basename():
    """Test _get_json_release_basename returns correct filenames."""
    from fetchtastic.downloader import _get_json_release_basename

    # Test firmware
    result = _get_json_release_basename("Firmware")
    assert result == "latest_firmware_release.json"

    # Test Android APK
    result = _get_json_release_basename("Android APK")
    assert result == "latest_android_release.json"


@pytest.mark.core_downloads
def test_get_existing_prerelease_dirs(tmp_path):
    """Test _get_existing_prerelease_dirs finds valid directories."""
    from fetchtastic.downloader import _get_existing_prerelease_dirs

    # Create directory structure
    prerelease_dir = tmp_path / "prerelease"
    prerelease_dir.mkdir()

    # Create valid firmware directories
    (prerelease_dir / "firmware-v1.0.0").mkdir()
    (prerelease_dir / "firmware-v2.0.0").mkdir()
    (prerelease_dir / "firmware-v1.0.0+abc123").mkdir()  # hash suffix is also valid

    # Create invalid directories (should be ignored)
    (prerelease_dir / "invalid").mkdir()

    result = _get_existing_prerelease_dirs(str(prerelease_dir))

    # Should return all firmware directories (those starting with "firmware-")
    assert len(result) == 3
    assert any("firmware-v1.0.0" in path for path in result)
    assert any("firmware-v2.0.0" in path for path in result)
    assert any("firmware-v1.0.0+abc123" in path for path in result)
    assert not any("invalid" in path for path in result)


@pytest.mark.core_downloads
def test_get_commit_hash_from_dir():
    """Test _get_commit_hash_from_dir extracts hash correctly."""
    from fetchtastic.downloader import _get_commit_hash_from_dir

    # Test with hash suffix after dash
    result = _get_commit_hash_from_dir("firmware-v1.0.0-abc123def")
    assert result == "abc123def"

    # Test with hash suffix after dot
    result = _get_commit_hash_from_dir("firmware-v1.0.0.abc123def")
    assert result == "abc123def"

    # Test with no hash
    result = _get_commit_hash_from_dir("firmware-v1.0.0")
    assert result is None

    # Test with plus sign (should not match based on regex)
    result = _get_commit_hash_from_dir("firmware-v1.0.0+abc123def")
    assert result is None

    # Test without hash suffix
    result = _get_commit_hash_from_dir("firmware-v1.0.0")
    assert result is None

    # Test with empty hash after plus (should not match)
    result = _get_commit_hash_from_dir("firmware-v1.0.0+")
    assert result is None


@pytest.mark.core_downloads
def test_atomic_write_operations(tmp_path):
    """Test _atomic_write function handles file operations correctly."""
    from fetchtastic.downloader import _atomic_write

    test_file = tmp_path / "test_atomic.txt"

    # Test successful write
    def write_content(f):
        """
        Write the literal string "test content" to the provided writable file-like object.

        Parameters:
            f (io.TextIOBase): A writable file-like object opened in text mode.
        """
        f.write("test content")

    result = _atomic_write(str(test_file), write_content, suffix=".tmp")

    assert result is True
    assert test_file.exists()
    assert test_file.read_text() == "test content"


@pytest.mark.core_downloads
def test_read_latest_release_tag_file_not_found(tmp_path):
    """Test _read_latest_release_tag with non-existent file."""
    from fetchtastic.downloader import _read_latest_release_tag

    non_existent_file = tmp_path / "non_existent.json"
    result = _read_latest_release_tag(str(non_existent_file))
    assert result is None


@pytest.mark.core_downloads
def test_clear_cache_generic():
    """Test _clear_cache_generic function."""
    from unittest.mock import MagicMock, patch

    from fetchtastic.downloader import _clear_cache_generic

    # Mock cache dict and file getter
    mock_cache = {"key1": "value1", "key2": "value2"}
    mock_file_getter = MagicMock(return_value="/test/cache.json")

    with patch("os.path.exists", return_value=True), patch("os.remove") as mock_remove:
        _clear_cache_generic(mock_cache, mock_file_getter, "test cache")

        # Should clear the cache dict
        assert len(mock_cache) == 0

        # Should call os.remove to delete the cache file
        mock_remove.assert_called_once_with("/test/cache.json")


@pytest.mark.core_downloads
def test_extract_version():
    """Test extract_version function."""
    from fetchtastic.downloader import extract_version

    # Test with firmware prefix
    assert extract_version("firmware-v1.0.0") == "v1.0.0"
    assert extract_version("firmware-v2.1.0-beta") == "v2.1.0-beta"

    # Test without prefix
    assert extract_version("v1.0.0") == "v1.0.0"
    assert extract_version("some-other-text") == "some-other-text"


@pytest.mark.core_downloads
def test_remove_firmware_prefix_logic():
    """Test the logic of removing firmware prefix using extract_version."""
    from fetchtastic.downloader import FIRMWARE_DIR_PREFIX, extract_version

    # Test that extract_version removes the firmware prefix
    assert extract_version("firmware-v1.0.0") == "v1.0.0"
    assert extract_version(f"{FIRMWARE_DIR_PREFIX}something") == "something"
    assert extract_version("v1.0.0") == "v1.0.0"  # No prefix to remove


@pytest.mark.core_downloads
def test_calculate_expected_prerelease_version():
    """Test calculate_expected_prerelease_version function."""
    from fetchtastic.downloader import calculate_expected_prerelease_version

    # Test normal version increment
    assert calculate_expected_prerelease_version("v1.0.0") == "1.0.1"
    assert calculate_expected_prerelease_version("v2.5.3") == "2.5.4"
    assert calculate_expected_prerelease_version("1.0.0") == "1.0.1"

    # Test with missing patch version
    assert calculate_expected_prerelease_version("v1.0") == "1.0.1"

    # Test with invalid versions
    assert calculate_expected_prerelease_version("invalid") == ""
    assert calculate_expected_prerelease_version("") == ""


@pytest.mark.core_downloads
def test_read_latest_release_tag_invalid_json(tmp_path):
    """Test _read_latest_release_tag with invalid JSON."""
    from fetchtastic.downloader import _read_latest_release_tag

    invalid_json_file = tmp_path / "invalid.json"
    invalid_json_file.write_text("{ invalid json content")

    result = _read_latest_release_tag(str(invalid_json_file))

    assert result is None


@pytest.mark.core_downloads
def test_read_latest_release_tag_missing_version_key(tmp_path):
    """Test _read_latest_release_tag with missing latest_version key."""
    from fetchtastic.downloader import _read_latest_release_tag

    incomplete_json_file = tmp_path / "incomplete.json"
    incomplete_json_file.write_text('{"other_key": "value"}')

    result = _read_latest_release_tag(str(incomplete_json_file))

    assert result is None


@pytest.mark.core_downloads
def test_normalize_commit_identifier():
    """Test _normalize_commit_identifier function."""
    from fetchtastic.downloader import _normalize_commit_identifier

    # Test with version and commit (hash only)
    result = _normalize_commit_identifier("abc123", "v1.0.0")
    assert result == "1.0.0.abc123"

    # Test with None version
    result = _normalize_commit_identifier("abc123", None)
    assert result == "abc123"

    # Test with empty version
    result = _normalize_commit_identifier("abc123", "")
    assert result == "abc123"

    # Test with already normalized version+hash
    result = _normalize_commit_identifier("1.0.0.abc123", "v1.0.0")
    assert result == "1.0.0.abc123"


@pytest.mark.core_downloads
def test_extract_clean_version():
    """Test _extract_clean_version function."""
    from fetchtastic.downloader import _extract_clean_version

    # Test with version and dot hash
    result = _extract_clean_version("v1.0.0.abc123")
    assert result == "v1.0.0"

    # Test with version and more parts
    result = _extract_clean_version("v1.0.0.abc123.extra")
    assert result == "v1.0.0"

    # Test with clean version
    result = _extract_clean_version("v1.0.0")
    assert result == "v1.0.0"

    # Test with None
    result = _extract_clean_version(None)
    assert result is None


@pytest.mark.core_downloads
def test_matches_exclude():
    """Test _matches_exclude function."""
    from fetchtastic.downloader import _matches_exclude

    # Test with matching patterns
    assert _matches_exclude("test.txt", ["*.txt"]) is True
    assert _matches_exclude("file.md", ["*.md", "*.txt"]) is True

    # Test with no matching patterns
    assert _matches_exclude("test.txt", ["*.md", "*.pdf"]) is False

    # Test with empty patterns
    assert _matches_exclude("test.txt", []) is False


@pytest.mark.core_downloads
def test_strip_unwanted_chars():
    """Test strip_unwanted_chars function."""
    from fetchtastic.downloader import strip_unwanted_chars

    # Test with non-ASCII characters (should be removed)
    assert strip_unwanted_chars("hello🌟world") == "helloworld"
    assert strip_unwanted_chars("café") == "caf"  # codespell:ignore
    assert strip_unwanted_chars("text with émojis 🚀") == "text with mojis "

    # Test with clean ASCII text (should remain unchanged)
    assert strip_unwanted_chars("clean text") == "clean text"
    assert strip_unwanted_chars("") == ""


@pytest.mark.core_downloads
def test_get_string_list_from_config():
    """Test _get_string_list_from_config function."""
    from fetchtastic.downloader import _get_string_list_from_config

    # Test with list of strings
    config = {"patterns": ["*.bin", "*.hex"]}
    result = _get_string_list_from_config(config, "patterns")
    assert result == ["*.bin", "*.hex"]

    # Test with single string (should convert to list)
    config = {"patterns": "*.bin"}
    result = _get_string_list_from_config(config, "patterns")
    assert result == ["*.bin"]

    # Test with mixed types (should filter to strings only)
    config = {"patterns": ["*.bin", 123, "*.hex", None, b"bytes"]}
    result = _get_string_list_from_config(config, "patterns")
    assert result == ["*.bin", "*.hex", "b'bytes'"]

    # Test with non-list, non-string (should return empty list)
    config = {"patterns": 123}
    result = _get_string_list_from_config(config, "patterns")
    assert result == []

    # Test with missing key (should return empty list)
    config = {}
    result = _get_string_list_from_config(config, "patterns")
    assert result == []


@pytest.mark.core_downloads
def test_is_release_complete(tmp_path):
    """Test _is_release_complete function."""
    from fetchtastic.downloader import _is_release_complete

    # Create a test directory with files
    test_dir = tmp_path / "release"
    test_dir.mkdir()
    (test_dir / "file1.txt").write_text("content1")
    (test_dir / "file2.txt").write_text("content2")

    # Test complete release
    complete_release = {
        "name": "Test Release",
        "tag_name": "v1.0.0",
        "assets": [{"name": "file1.txt"}, {"name": "file2.txt"}],
    }
    assert _is_release_complete(complete_release, str(test_dir), None, []) is True

    # Test incomplete release (missing assets)
    incomplete_release = {
        "name": "Test Release",
        "tag_name": "v1.0.0",
        "assets": [{"name": "file1.txt"}, {"name": "missing.txt"}],
    }
    assert _is_release_complete(incomplete_release, str(test_dir), None, []) is False

    # Test incomplete release (no assets)
    incomplete_release2 = {"name": "Test Release", "tag_name": "v1.0.0", "assets": []}
    assert _is_release_complete(incomplete_release2, str(test_dir), None, []) is False

    # Test incomplete release (missing assets key)
    incomplete_release3 = {"name": "Test Release", "tag_name": "v1.0.0"}
    assert _is_release_complete(incomplete_release3, str(test_dir), None, []) is False


@pytest.mark.core_downloads
def test_prepare_for_redownload(tmp_path):
    """Test _prepare_for_redownload function."""
    from fetchtastic.downloader import _prepare_for_redownload

    # Create test files
    test_file = tmp_path / "test.bin"
    test_file.write_text("test content")

    hash_file = tmp_path / "test.bin.sha256"
    hash_file.write_text("hash content")

    temp_file1 = tmp_path / "test.bin.tmp.123"
    temp_file1.write_text("temp1")

    temp_file2 = tmp_path / "test.bin.tmp.456"
    temp_file2.write_text("temp2")

    # Test successful cleanup
    result = _prepare_for_redownload(str(test_file))
    assert result is True
    assert not test_file.exists()
    assert not hash_file.exists()
    assert not temp_file1.exists()
    assert not temp_file2.exists()

    # Test with non-existent file (should still succeed)
    result = _prepare_for_redownload(str(tmp_path / "nonexistent.bin"))
    assert result is True

    # Test with only some files existing
    test_file.write_text("content")
    temp_file1.write_text("temp")
    result = _prepare_for_redownload(str(test_file))
    assert result is True
    assert not test_file.exists()
    assert not temp_file1.exists()


@pytest.mark.core_downloads
def test_prerelease_needs_download(tmp_path):
    """Test _prerelease_needs_download function."""
    from unittest.mock import patch

    from fetchtastic.downloader import _prerelease_needs_download

    test_file = tmp_path / "test.bin"

    # Test file doesn't exist - should return True
    result = _prerelease_needs_download(str(test_file))
    assert result is True

    # Test file exists and integrity check passes - should return False
    test_file.write_text("content")
    with patch("fetchtastic.downloader.verify_file_integrity", return_value=True):
        result = _prerelease_needs_download(str(test_file))
        assert result is False

    # Test file exists but integrity check fails and cleanup succeeds - should return True
    with (
        patch("fetchtastic.downloader.verify_file_integrity", return_value=False),
        patch("fetchtastic.downloader._prepare_for_redownload", return_value=True),
    ):
        result = _prerelease_needs_download(str(test_file))
        assert result is True

    # Test file exists but integrity check fails and cleanup fails - should return False
    with (
        patch("fetchtastic.downloader.verify_file_integrity", return_value=False),
        patch("fetchtastic.downloader._prepare_for_redownload", return_value=False),
    ):
        result = _prerelease_needs_download(str(test_file))
        assert result is False


@pytest.mark.core_downloads
def test_is_within_base():
    """Test _is_within_base function."""
    from fetchtastic.downloader import _is_within_base

    # Test candidate within base directory
    assert _is_within_base("/base", "/base/file.txt") is True
    assert _is_within_base("/base", "/base/subdir/file.txt") is True

    # Test candidate outside base directory
    assert _is_within_base("/base", "/other/file.txt") is False

    # Test candidate is the base directory itself
    assert _is_within_base("/base", "/base") is True

    # Test with different drives (should return False on error)
    assert _is_within_base("C:/base", "D:/file.txt") is False


@pytest.mark.core_downloads
def test_safe_rmtree():
    """Test _safe_rmtree function."""
    from unittest.mock import patch

    from fetchtastic.downloader import _safe_rmtree

    with (
        patch("os.path.realpath") as mock_realpath,
        patch("os.path.commonpath", return_value="/base"),
        patch("os.path.isdir", return_value=True),
        patch("shutil.rmtree") as mock_rmtree,
    ):
        # Setup mock to return same path for both calls
        mock_realpath.side_effect = lambda x: x

        # Test successful removal
        result = _safe_rmtree("/test/path", "/base", "test_item")
        assert result is True
        mock_rmtree.assert_called_once_with("/test/path")

    with (
        patch("os.path.realpath") as mock_realpath,
        patch(
            "os.path.commonpath",
            side_effect=ValueError("Paths don't have the same drive"),
        ),
        patch("os.path.isdir", return_value=True),
        patch("shutil.rmtree") as mock_rmtree,
    ):
        # Setup mock to return different paths (security check failure)
        mock_realpath.side_effect = ["/base", "/malicious/path"]

        # Test security check failure
        result = _safe_rmtree("/test/path", "/base", "test_item")
        assert result is False
        mock_rmtree.assert_not_called()


@pytest.mark.core_downloads
def test_cache_thread_safety():
    """Test that cache operations are thread-safe."""
    import threading
    import time
    from unittest.mock import patch

    from fetchtastic.downloader import (
        _load_commit_cache,
        _load_prerelease_dir_cache,
        _load_releases_cache,
        clear_all_caches,
        clear_commit_timestamp_cache,
    )

    def simulate_cache_operation(_cache_type, operation_func, results_list, thread_id):
        """
        Run a cache-related operation in a thread and record its timing and outcome.

        Appends a tuple to `results_list` containing (thread_id, start_timestamp, end_timestamp, error_message_or_None). If the operation raises, the exception string is recorded as `error_message_or_None`; otherwise that field is `None`.

        Parameters:
            _cache_type: Identifier for the cache being exercised (unused by this helper; provided for context).
            operation_func: Callable that performs the cache operation to measure.
            results_list: Mutable sequence to which the timing/result tuple will be appended.
            thread_id: Identifier for the calling thread, included in the recorded tuple.
        """
        start_time = time.time()
        try:
            operation_func()
            end_time = time.time()
            results_list.append((thread_id, start_time, end_time, None))
        except Exception as e:
            end_time = time.time()
            results_list.append((thread_id, start_time, end_time, str(e)))

    # Test commit cache thread safety
    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        with patch("fetchtastic.downloader._atomic_write_json", return_value=True):
            # Reset cache state
            clear_commit_timestamp_cache()

            # Test concurrent loads
            commit_results = []
            commit_threads = []

            for i in range(3):
                thread = threading.Thread(
                    target=simulate_cache_operation,
                    args=("commit", _load_commit_cache, commit_results, i),
                )
                commit_threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in commit_threads:
                thread.join(timeout=5.0)

            # Verify all threads completed without errors
            assert len(commit_results) == 3
            for thread_id, start, end, error in commit_results:
                assert error is None, f"Thread {thread_id} failed with error: {error}"
                assert (
                    end - start < 2.0
                ), f"Thread {thread_id} took too long: {end - start}s"

    # Test releases cache thread safety
    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        with patch("fetchtastic.downloader._atomic_write_json", return_value=True):
            # Reset cache state using clear_all_caches
            clear_all_caches()

            # Test concurrent loads
            release_results = []
            release_threads = []

            for i in range(3):
                thread = threading.Thread(
                    target=simulate_cache_operation,
                    args=("releases", _load_releases_cache, release_results, i),
                )
                release_threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in release_threads:
                thread.join(timeout=5.0)

            # Verify all threads completed without errors
            assert len(release_results) == 3
            for thread_id, _, _, error in release_results:
                assert (
                    error is None
                ), f"Release thread {thread_id} failed with error: {error}"

    # Test prerelease cache thread safety
    with patch("fetchtastic.downloader._ensure_cache_dir", return_value="/test/cache"):
        with patch("fetchtastic.downloader._atomic_write_json", return_value=True):
            # Reset cache state
            clear_all_caches()

            # Test concurrent loads
            prerelease_results = []
            prerelease_threads = []

            for i in range(3):
                thread = threading.Thread(
                    target=simulate_cache_operation,
                    args=(
                        "prerelease",
                        _load_prerelease_dir_cache,
                        prerelease_results,
                        i,
                    ),
                )
                prerelease_threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in prerelease_threads:
                thread.join(timeout=5.0)

            # Verify all threads completed without errors
            assert len(prerelease_results) == 3
            for thread_id, _, _, error in prerelease_results:
                assert (
                    error is None
                ), f"Prerelease thread {thread_id} failed with error: {error}"


@pytest.mark.core_downloads
def test_read_latest_release_tag_non_dict_json(tmp_path):
    """Test _read_latest_release_tag handles non-dict JSON correctly."""
    from fetchtastic.downloader import _read_latest_release_tag

    # Test with JSON array instead of object
    json_file = tmp_path / "invalid.json"
    json_file.write_text('["not", "an", "object"]')

    result = _read_latest_release_tag(str(json_file))
    assert result is None  # Should return None for invalid JSON structure


@pytest.mark.core_downloads
def test_read_prerelease_tracking_data_non_dict_json(tmp_path):
    """Test _read_prerelease_tracking_data handles non-dict JSON correctly."""
    from fetchtastic.downloader import _read_prerelease_tracking_data

    # Test with JSON string instead of object
    tracking_file = tmp_path / "invalid.json"
    tracking_file.write_text('"not an object"')

    commits, current_release, last_updated = _read_prerelease_tracking_data(
        str(tracking_file)
    )

    # Should return default values when JSON structure is invalid
    assert commits == []
    assert current_release is None
    assert last_updated is None


@pytest.mark.core_downloads
def test_get_commit_hash_from_dir_length_validation():
    """Test _get_commit_hash_from_dir enforces 6-40 character hash length."""
    from fetchtastic.downloader import _get_commit_hash_from_dir

    # Test with 4-character hash (should be rejected)
    assert (
        _get_commit_hash_from_dir("firmware-v1.0.0-abc123") is not None
    )  # 6 chars, should work
    assert (
        _get_commit_hash_from_dir("firmware-v1.0.0-abcd") is None
    )  # 4 chars, should fail

    # Test with 6-character hash (should work)
    assert _get_commit_hash_from_dir("firmware-v1.0.0-abcdef") == "abcdef"

    # Test with 40-character hash (should work)
    long_hash = "a" * 40
    assert _get_commit_hash_from_dir(f"firmware-v1.0.0-{long_hash}") == long_hash

    # Test with 41-character hash (should fail)
    too_long_hash = "a" * 41
    assert _get_commit_hash_from_dir(f"firmware-v1.0.0-{too_long_hash}") is None


@pytest.mark.core_downloads
def test_main_downloads_skipped_reset():
    """Test main function resets downloads_skipped flag."""
    import fetchtastic.downloader as downloader_module

    # Set the flag to True first
    downloader_module.downloads_skipped = True

    # Mock the initial setup to avoid actual configuration loading
    with patch("fetchtastic.downloader._initial_setup_and_config") as mock_setup:
        mock_setup.return_value = (
            None,
            None,
            None,
            False,
            None,
        )  # config, current_version, latest_version, update_available, paths_and_urls

        try:
            # Call main function - it should fail early but still reset the flag
            downloader_module.main(force_refresh=False)
        except SystemExit:
            pass  # Expected when setup fails

        # Flag should be reset to False even when setup fails
        assert downloader_module.downloads_skipped is False


@pytest.mark.core_downloads
def test_download_repo_files_path_traversal(tmp_path):
    """Test that download_repo_files prevents path traversal attacks."""
    import os
    from unittest.mock import patch

    from fetchtastic.repo_downloader import download_repo_files

    download_dir = str(tmp_path)
    repo_dir = tmp_path / "firmware" / "repo-dls"

    # Test malicious directory path
    malicious_dir = "../../../etc"
    selected_files = {
        "directory": malicious_dir,
        "files": [{"name": "passwd", "download_url": "http://example.com/passwd"}],
    }

    def mock_download_file_with_retry(url, file_path):
        """Mock that creates the file to simulate successful download."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write("mock content")
        return True

    # Test the actual behavior of download_repo_files
    with patch(
        "fetchtastic.repo_downloader.download_file_with_retry",
        side_effect=mock_download_file_with_retry,
    ):
        downloaded_files = download_repo_files(selected_files, download_dir)

    # CRITICAL: Assert that the file was NOT written outside the intended repo directory
    assert not (
        tmp_path / "etc" / "passwd"
    ).exists(), "SECURITY: File was written outside repo directory!"

    # Assert that the file was written to the base repo directory as a fallback
    assert (repo_dir / "passwd").exists(), "File was not written to fallback directory!"

    # Verify the returned path points to the safe location
    expected_safe_path = str(repo_dir / "passwd")
    assert (
        expected_safe_path in downloaded_files
    ), "Returned path does not point to safe location!"

    # Additional test: verify safe directory works normally
    safe_selected_files = {
        "directory": "safe_subdir",
        "files": [{"name": "safe_file.txt", "download_url": "http://example.com/safe"}],
    }

    with patch(
        "fetchtastic.repo_downloader.download_file_with_retry",
        side_effect=mock_download_file_with_retry,
    ):
        safe_downloaded_files = download_repo_files(safe_selected_files, download_dir)

    # Safe directory should work normally
    safe_dir = repo_dir / "safe_subdir"
    assert (safe_dir / "safe_file.txt").exists(), "Safe directory file was not created!"
    assert (
        str(safe_dir / "safe_file.txt") in safe_downloaded_files
    ), "Safe file not in returned paths!"
