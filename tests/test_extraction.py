"""
File extraction and pattern matching tests for the fetchtastic downloader module.

This module contains tests for:
- File extraction from archives
- Pattern matching logic
- Include/exclude filter functionality
- Permission setting for extracted files
- Subdirectory preservation
"""

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from fetchtastic import downloader
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.downloader import matches_extract_patterns


@pytest.fixture
def dummy_zip_file(tmp_path):
    """Create a dummy ZIP file containing sample firmware and support files used by extraction tests."""
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("littlefs-rak11200-2.7.4.c1f4f79.bin", "rak11200_littlefs")
        zf.writestr("firmware-tbeam-2.7.4.c1f4f79.uf2", "tbeam_data")
        zf.writestr("device-update.sh", "#!/bin/bash\necho 'Updating device...'")
        zf.writestr("notes.txt", "Release notes")
    return zip_path


def test_extract_files(dummy_zip_file, tmp_path):
    """Test file extraction with patterns."""
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    patterns = ["rak11200", "device-update.sh"]
    exclude_patterns = []

    downloader.extract_files(
        str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
    )

    assert (extract_dir / "firmware-rak11200-2.7.4.c1f4f79.bin").exists()
    assert (extract_dir / "littlefs-rak11200-2.7.4.c1f4f79.bin").exists()
    assert (extract_dir / "device-update.sh").exists()
    assert not (extract_dir / "firmware-tbeam-2.7.4.c1f4f79.uf2").exists()
    assert not (extract_dir / "notes.txt").exists()

    # Check that shell script was made executable
    if os.name != "nt":
        assert os.access(extract_dir / "device-update.sh", os.X_OK)


def test_extract_files_preserves_subdirectories(tmp_path):
    """Extraction should preserve archive subdirectories when writing to disk."""
    zip_path = tmp_path / "nested.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("sub/dir/firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("sub/dir/device-install.sh", "echo hi")
        zf.writestr("sub/notes.txt", "n")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Include rak11200 and script; exclude notes
    downloader.extract_files(
        str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], ["notes*"]
    )

    # Files extracted under their original subdirectories
    bin_path = out_dir / "sub/dir/firmware-rak11200-2.7.4.c1f4f79.bin"
    sh_path = out_dir / "sub/dir/device-install.sh"

    assert bin_path.exists()
    assert sh_path.exists()
    if os.name != "nt":
        assert os.access(sh_path, os.X_OK)
    assert not (out_dir / "sub/notes.txt").exists()


def test_check_extraction_needed_with_nested_paths(tmp_path):
    """check_extraction_needed should consider nested archive paths and base-name filters."""
    zip_path = tmp_path / "nested2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dir/inner/firmware-rak11200-2.7.4.c1f4f79.bin", "rak11200_data")
        zf.writestr("dir/inner/device-install.sh", "echo hi")

    out_dir = tmp_path / "out2"
    out_dir.mkdir()

    # 1) Empty patterns -> never needed
    assert (
        downloader.check_extraction_needed(str(zip_path), str(out_dir), [], []) is False
    )

    # 2) Specific patterns: both files missing -> needed
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is True
    )

    # Create one of the expected files, still needed for the other
    (out_dir / "dir" / "inner").mkdir(parents=True, exist_ok=True)
    (out_dir / "dir/inner/firmware-rak11200-2.7.4.c1f4f79.bin").write_text(
        "rak11200_data"
    )
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is True
    )

    # Create the second expected file -> no extraction needed
    (out_dir / "dir/inner/device-install.sh").write_text("echo hi")
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(out_dir), ["rak11200", "device-install.sh"], []
        )
        is False
    )


def test_check_extraction_needed(dummy_zip_file, tmp_path):
    """Test logic for checking if extraction is needed."""
    extract_dir = tmp_path / "extract_check"
    extract_dir.mkdir()
    patterns = ["rak4631", "rak11200", "tbeam"]
    exclude_patterns = []

    # 1. No files extracted yet, should be needed
    assert (
        downloader.check_extraction_needed(
            str(dummy_zip_file), str(extract_dir), patterns, exclude_patterns
        )
        is True
    )


def test_check_extraction_needed_with_dash_patterns(tmp_path):
    """Ensure dash-suffixed patterns are honored in extraction-needed check."""
    zip_path = tmp_path / "dash.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("firmware-rak4631-2.7.4.c1f4f79.bin", "rak_data")

    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    # Missing -> extraction needed
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(extract_dir), ["rak4631-"], []
        )
        is True
    )
    # Create expected file -> not needed
    (extract_dir / "firmware-rak4631-2.7.4.c1f4f79.bin").write_text("rak_data")
    assert (
        downloader.check_extraction_needed(
            str(zip_path), str(extract_dir), ["rak4631-"], []
        )
        is False
    )


def test_extract_files_matching_and_exclude(tmp_path):
    """Test extraction honors legacy-style matching and exclude patterns."""
    zip_path = tmp_path / "mix.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("firmware-rak4631-2.7.6.aaa.bin", "a")
        zf.writestr("firmware-rak4631_eink-2.7.6.aaa.uf2", "b")
        zf.writestr("device-install.sh", "echo x")
        zf.writestr("notes.txt", "n")

    out_dir = tmp_path / "ext"
    out_dir.mkdir()

    downloader.extract_files(
        str(zip_path), str(out_dir), ["rak4631-", "device-install.sh"], ["*eink*"]
    )

    assert (out_dir / "firmware-rak4631-2.7.6.aaa.bin").exists()
    assert not (out_dir / "firmware-rak4631_eink-2.7.6.aaa.uf2").exists()
    # script extracted and made executable
    sh_path = out_dir / "device-install.sh"
    assert sh_path.exists()
    if os.name != "nt":
        assert os.access(sh_path, os.X_OK)

    # No further changes; validates include/exclude and executable bit behavior


def test_matches_extract_patterns_with_device_manager():
    """Test matches_extract_patterns with DeviceHardwareManager."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)

        # Create manager with fallback patterns
        manager = DeviceHardwareManager(
            cache_dir=cache_dir,
            enabled=False,  # Use fallback patterns
        )

        extract_patterns = ["rak4631-", "tbeam-", "device-", "bleota"]

        # Test device pattern matching
        assert matches_extract_patterns(
            "firmware-rak4631-2.7.9.bin", extract_patterns, manager
        )
        assert matches_extract_patterns(
            "littlefs-rak4631-2.7.9.bin", extract_patterns, manager
        )
        assert matches_extract_patterns(
            "firmware-tbeam-2.7.9.bin", extract_patterns, manager
        )
        assert matches_extract_patterns(
            "littlefs-tbeam-2.7.9.bin", extract_patterns, manager
        )

        # Test file type pattern matching
        assert matches_extract_patterns("device-install.sh", extract_patterns, manager)
        assert matches_extract_patterns("bleota.bin", extract_patterns, manager)
        assert matches_extract_patterns("bleota-c3.bin", extract_patterns, manager)

        # Test non-matching files
        assert not matches_extract_patterns(
            "firmware-canaryone-2.7.9.bin", extract_patterns, manager
        )
        assert not matches_extract_patterns(
            "some-random-file.txt", extract_patterns, manager
        )

        # Test littlefs- special case
        extract_patterns_with_littlefs = ["rak4631-", "littlefs-"]
        assert matches_extract_patterns(
            "littlefs-canaryone-2.7.9.bin", extract_patterns_with_littlefs, manager
        )
        assert matches_extract_patterns(
            "littlefs-any-device-2.7.9.bin", extract_patterns_with_littlefs, manager
        )


def test_matches_extract_patterns_backwards_compatibility():
    """Test that matches_extract_patterns works without device_manager (backwards compatibility)."""
    extract_patterns = ["rak4631-", "tbeam-", "device-", "bleota"]

    # Test without device_manager (should use fallback logic)
    assert matches_extract_patterns("firmware-rak4631-2.7.9.bin", extract_patterns)
    assert matches_extract_patterns("device-install.sh", extract_patterns)
    assert matches_extract_patterns("bleota.bin", extract_patterns)

    # Test patterns ending with dash (fallback device detection)
    assert matches_extract_patterns(
        "firmware-custom-device-2.7.9.bin", ["custom-device-"]
    )
    assert matches_extract_patterns(
        "littlefs-custom-device-2.7.9.bin", ["custom-device-"]
    )


def test_device_hardware_manager_api_failure():
    """Test DeviceHardwareManager behavior when API fails."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)

        # Test with API enabled but mocked to fail (should fallback)
        with pytest.MonkeyPatch().context() as m:
            import requests

            m.setattr(
                "requests.get",
                Mock(side_effect=requests.exceptions.RequestException("Network error")),
            )

            manager = DeviceHardwareManager(
                cache_dir=cache_dir,
                enabled=True,
                api_url="https://api.example.com/device-hardware",
                timeout_seconds=1,
            )

            patterns = manager.get_device_patterns()
            assert isinstance(patterns, set)
            assert len(patterns) > 0  # Should get fallback patterns

            # Should still be able to detect device patterns
            extract_patterns = ["rak4631-", "tbeam-"]
            assert matches_extract_patterns(
                "firmware-rak4631-2.7.9.bin", extract_patterns, manager
            )


def test_device_hardware_manager_cache_expiration():
    """Test DeviceHardwareManager cache expiration logic."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)
        cache_file = cache_dir / "device_hardware.json"

        manager = DeviceHardwareManager(cache_dir=cache_dir, cache_hours=1)

        # Create valid cache with timestamp
        import json
        import time

        current_time = time.time()
        cache_data = {
            "device_patterns": ["device1", "device2"],
            "timestamp": current_time,
            "api_url": manager.api_url,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Set the last fetch time to match cache timestamp
        manager._last_fetch_time = current_time

        # Fresh cache should not be expired
        assert manager._is_cache_expired() is False

        # Old cache should be expired
        old_time = current_time - (manager.cache_hours * 3600 + 100)
        cache_data["timestamp"] = old_time
        cache_file.write_text(str(cache_data))
        manager._last_fetch_time = old_time
        assert manager._is_cache_expired() is True


def test_device_hardware_manager_user_facing_messages():
    """Test DeviceHardwareManager user-facing messages and logging."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)

        # Test with API disabled (should show fallback message)
        manager = DeviceHardwareManager(
            cache_dir=cache_dir,
            enabled=False,
        )

        patterns = manager.get_device_patterns()
        assert isinstance(patterns, set)
        assert len(patterns) > 0

        # Test with API enabled but no cache (should try to fetch)
        manager2 = DeviceHardwareManager(
            cache_dir=cache_dir,
            enabled=True,
            api_url="https://invalid.example.com",
            timeout_seconds=1,
        )

        # Should fall back gracefully when API fails
        patterns2 = manager2.get_device_patterns()
        assert isinstance(patterns2, set)
        assert len(patterns2) > 0


def test_device_hardware_manager_corrupted_cache():
    """Test DeviceHardwareManager handling of corrupted cache files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)
        cache_file = cache_dir / "device_hardware.json"

        # Create corrupted cache file
        cache_file.write_text("invalid json content")

        manager = DeviceHardwareManager(cache_dir=cache_dir, enabled=False)

        # Should handle corruption gracefully and fall back
        patterns = manager.get_device_patterns()
        assert isinstance(patterns, set)
        assert len(patterns) > 0


class TestExtractionAndPermissionSetting:
    """Test extraction and permission setting functionality."""

    def test_extraction_and_permission_setting_integration(self):
        """Test that extraction preserves file permissions correctly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a zip with executable scripts
            zip_path = Path(tmp_dir) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("script.sh", "#!/bin/bash\necho 'test'")
                zf.writestr("firmware.bin", "firmware data")

            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            # Extract with script pattern
            downloader.extract_files(str(zip_path), str(extract_dir), ["script"], [])

            script_path = extract_dir / "script.sh"
            assert script_path.exists()

            # Script should be executable on Unix systems
            if os.name != "nt":  # Not on Windows
                assert os.access(script_path, os.X_OK)

    def test_extraction_with_complex_patterns(self):
        """Test extraction with complex include/exclude patterns."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "complex.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("firmware-rak4631-2.7.9.bin", "data1")
                zf.writestr("firmware-rak4631_eink-2.7.9.uf2", "data2")
                zf.writestr("littlefs-rak4631-2.7.9.bin", "data3")
                zf.writestr("device-update.sh", "script")
                zf.writestr("README.md", "docs")

            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            # Complex pattern matching
            downloader.extract_files(
                str(zip_path),
                str(extract_dir),
                ["rak4631-", "device-"],
                ["*eink*", "*.md"],
            )

            # Should extract rak4631 firmware and littlefs, but not eink variant
            assert (extract_dir / "firmware-rak4631-2.7.9.bin").exists()
            assert (extract_dir / "littlefs-rak4631-2.7.9.bin").exists()
            assert not (extract_dir / "firmware-rak4631_eink-2.7.9.uf2").exists()
            assert not (extract_dir / "README.md").exists()

            # Script should be extracted and executable
            script_path = extract_dir / "device-update.sh"
            assert script_path.exists()
            if os.name != "nt":
                assert os.access(script_path, os.X_OK)

    def test_extraction_preserves_directory_structure(self):
        """Test that extraction preserves the original directory structure."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "nested.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("firmware/rak4631/rak4631-firmware.bin", "firmware")
                zf.writestr("scripts/install.sh", "install script")
                zf.writestr("docs/README.md", "documentation")

            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            downloader.extract_files(
                str(zip_path), str(extract_dir), ["rak4631", "install"], []
            )

            # Directory structure should be preserved
            assert (extract_dir / "firmware/rak4631/rak4631-firmware.bin").exists()
            assert (extract_dir / "scripts/install.sh").exists()
            assert not (extract_dir / "docs/README.md").exists()

            # Script should be executable
            if os.name != "nt":
                assert os.access(extract_dir / "scripts/install.sh", os.X_OK)

    def test_extraction_with_empty_patterns(self):
        """Test extraction behavior with empty patterns."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("firmware.bin", "data")
                zf.writestr("script.sh", "script")

            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            # Empty patterns should extract nothing
            downloader.extract_files(str(zip_path), str(extract_dir), [], [])

            assert not (extract_dir / "firmware.bin").exists()
            assert not (extract_dir / "script.sh").exists()

    def test_extraction_with_wildcard_patterns(self):
        """Test extraction with substring patterns (simulating wildcard behavior)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "wildcard.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("firmware-rak4631.bin", "data1")
                zf.writestr("firmware-tbeam.bin", "data2")
                zf.writestr("firmware-canary.bin", "data3")
                zf.writestr("script.sh", "script")

            extract_dir = Path(tmp_dir) / "extracted"
            extract_dir.mkdir()

            # Substring pattern for all firmware files (simulating wildcard behavior)
            downloader.extract_files(str(zip_path), str(extract_dir), ["firmware-"], [])

            assert (extract_dir / "firmware-rak4631.bin").exists()
            assert (extract_dir / "firmware-tbeam.bin").exists()
            assert (extract_dir / "firmware-canary.bin").exists()
            assert not (extract_dir / "script.sh").exists()


def test_validate_extraction_patterns(tmp_path, capsys):
    """Test the _validate_extraction_patterns function."""
    # Create a test ZIP file with various firmware files
    zip_file = tmp_path / "test_firmware.zip"

    with zipfile.ZipFile(str(zip_file), "w") as zf:
        # Add files that should match patterns
        zf.writestr("firmware-rak4631-2.7.15.567b8ea.uf2", "firmware data")
        zf.writestr("littlefs-rak4631-2.7.15.567b8ea.bin", "littlefs data")
        zf.writestr("device-install.sh", "script data")
        zf.writestr("firmware-tbeam-2.7.15.bin", "tbeam firmware")
        # Add file that shouldn't match
        zf.writestr("random-file.txt", "random data")

    # Test with patterns that should match - should not raise any exceptions
    patterns = ["rak4631-", "device-"]
    downloader._validate_extraction_patterns(str(zip_file), patterns, [], "v2.7.15")

    # Test with patterns that won't match - should log a warning
    patterns_no_match = ["nonexistent-", "invalid-"]
    captured_before = capsys.readouterr()
    _ = captured_before  # avoid lint about unused
    # Capture stdout/stderr since logging handler writes to console
    downloader._validate_extraction_patterns(
        str(zip_file), patterns_no_match, [], "v2.7.15"
    )
    output = capsys.readouterr().out + capsys.readouterr().err
    assert "No patterns matched" in output
    assert "files in ZIP archive" in output

    # Test with empty patterns - should not raise any exceptions
    downloader._validate_extraction_patterns(str(zip_file), [], [], "v2.7.15")

    # Test with corrupted ZIP - should handle gracefully
    corrupted_zip = tmp_path / "corrupted.zip"
    corrupted_zip.write_text("not a zip file")

    # Should not raise exception, just log error
    downloader._validate_extraction_patterns(
        str(corrupted_zip), ["test-"], [], "v2.7.15"
    )
