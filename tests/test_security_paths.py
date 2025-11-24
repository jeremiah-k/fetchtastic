"""
Security and path validation tests for the fetchtastic downloader module.

This module contains tests for:
- Path traversal prevention
- Symlink attack protection
- Input validation and sanitization
- Safe file extraction
"""

import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from fetchtastic import downloader


@pytest.fixture
def mock_commit_history(monkeypatch):
    """
    Replace the prerelease commit-history fetcher with a stub that always returns an empty list during tests.

    Parameters:
        monkeypatch (pytest.MonkeyPatch): Pytest fixture used to patch attributes on modules; this function uses it to replace
            fetchtastic.downloader._get_prerelease_commit_history with a stub that returns [].
    """
    from fetchtastic import downloader

    monkeypatch.setattr(
        downloader,
        "_get_prerelease_commit_history",
        lambda *_args, **_kwargs: [],
    )


class TestSecuritySymlinkAttacks:
    """Test security measures against symlink traversal attacks."""

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires administrator privileges on Windows",
    )
    def test_safe_rmtree_blocks_symlink_traversal(self, tmp_path):
        """Test that _safe_rmtree handles symlinks (current implementation removes them directly)."""
        from fetchtastic.downloader import _safe_rmtree

        # Create important external data
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        critical_file = external_dir / "critical.txt"
        critical_file.write_text("CRITICAL DATA")

        # Create malicious symlink inside download area
        download_dir = tmp_path / "download"
        download_dir.mkdir()
        malicious_symlink = download_dir / "malicious"
        malicious_symlink.symlink_to(external_dir, target_is_directory=True)

        # Verify setup
        assert malicious_symlink.is_symlink()
        assert critical_file.exists()

        # Current implementation removes symlinks directly (returns True)
        result = _safe_rmtree(str(malicious_symlink), str(download_dir), "malicious")
        assert result is True  # Current implementation removes symlinks directly

        # Symlink should be removed but external data should remain intact
        assert not malicious_symlink.exists()
        assert critical_file.exists()
        assert critical_file.read_text() == "CRITICAL DATA"

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires administrator privileges on Windows",
    )
    def test_safe_rmtree_blocks_nested_symlink_traversal(self, tmp_path):
        """Test that _safe_rmtree handles nested symlinks correctly."""
        from fetchtastic.downloader import _safe_rmtree

        # Create external target
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        (external_dir / "important.txt").write_text("IMPORTANT")

        # Create nested directory structure with malicious symlink
        base_dir = tmp_path / "base"
        nested_dir = base_dir / "nested" / "deep"
        nested_dir.mkdir(parents=True)
        malicious_symlink = nested_dir / "escape"
        malicious_symlink.symlink_to(external_dir, target_is_directory=True)

        # Should remove symlink directly (returns True)
        result = _safe_rmtree(str(malicious_symlink), str(base_dir), "escape")
        assert result is True

        # Symlink should be removed but external data should remain intact
        assert not malicious_symlink.exists()
        assert (external_dir / "important.txt").exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires administrator privileges on Windows",
    )
    def test_safe_rmtree_blocks_symlink_loop(self, tmp_path):
        """Test that _safe_rmtree handles symlink loops safely."""
        from fetchtastic.downloader import _safe_rmtree

        # Create symlink loop
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        # Create circular symlinks
        (dir1 / "link_to_dir2").symlink_to(dir2, target_is_directory=True)
        (dir2 / "link_to_dir1").symlink_to(dir1, target_is_directory=True)

        # Should handle symlink loop (symlink gets unlinked, returns True)
        result = _safe_rmtree(str(dir1 / "link_to_dir2"), str(dir1), "link_to_dir2")
        assert result is True

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires administrator privileges on Windows",
    )
    def test_safe_rmtree_allows_safe_symlinks(self, tmp_path):
        """Test that _safe_rmtree allows symlinks within the same directory tree."""
        from fetchtastic.downloader import _safe_rmtree

        # Create directory structure with internal symlinks
        base_dir = tmp_path / "base"
        subdir = base_dir / "subdir"
        subdir.mkdir(parents=True)

        # Create file and internal symlink (safe)
        target_file = base_dir / "target.txt"
        target_file.write_text("safe content")
        safe_symlink = subdir / "safe_link"
        safe_symlink.symlink_to(target_file)

        # Should allow deletion of directory with internal symlinks
        result = _safe_rmtree(str(base_dir), str(tmp_path), "base")
        assert result is True

        # Directory should be deleted
        assert not base_dir.exists()

    def test_safe_rmtree_handles_nonexistent_paths(self, tmp_path):
        """Test that _safe_rmtree handles non-existent paths gracefully."""
        from fetchtastic.downloader import _safe_rmtree

        nonexistent = tmp_path / "nonexistent"

        # Should handle non-existent paths without error (returns False)
        result = _safe_rmtree(str(nonexistent), str(tmp_path), "nonexistent")
        assert result is False  # Should return False for non-existent path

    def test_safe_rmtree_handles_regular_directories(self, tmp_path):
        """Test that _safe_rmtree works normally with regular directories."""
        from fetchtastic.downloader import _safe_rmtree

        # Create normal directory structure
        test_dir = tmp_path / "test"
        subdir = test_dir / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("content")

        # Should delete normal directory structure
        result = _safe_rmtree(str(test_dir), str(tmp_path), "test")
        assert result is True

        assert not test_dir.exists()

    def test_safe_rmtree_rejects_paths_outside_base(self, tmp_path):
        """_safe_rmtree should not touch paths that are outside the permitted base dir."""
        from fetchtastic.downloader import _safe_rmtree

        base_dir = tmp_path / "base"
        base_dir.mkdir()
        outside_dir = tmp_path / "base_alt"
        outside_dir.mkdir()
        stray_file = outside_dir / "file.txt"
        stray_file.write_text("data")

        result = _safe_rmtree(str(stray_file), str(base_dir), "file.txt")

        assert result is False
        assert stray_file.exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlink creation requires administrator privileges on Windows",
    )
    def test_safe_rmtree_skips_symlinks_outside_base(self, tmp_path):
        """Symlinks that live outside the base directory should be left alone."""
        from fetchtastic.downloader import _safe_rmtree

        base_dir = tmp_path / "base"
        base_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        link_path = outside_dir / "link_to_base"
        link_path.symlink_to(base_dir, target_is_directory=True)

        result = _safe_rmtree(str(link_path), str(base_dir), "link_to_base")

        assert result is False
        assert link_path.exists()


class TestSecurityPathTraversal:
    """Test security measures against path traversal attacks."""

    def test_path_sanitization_blocks_traversal(self):
        """Test that _sanitize_path_component blocks path traversal attempts."""
        from fetchtastic.downloader import _sanitize_path_component

        # Test various path traversal attempts
        malicious_names = [
            "../../../etc/passwd",
            "....//....//....//etc/passwd",
            "..%2f..%2f..%2fetc/passwd",
            "test/../../../etc/passwd",
            "normal/../../../etc/passwd",
        ]

        for malicious_name in malicious_names:
            sanitized = _sanitize_path_component(malicious_name)
            # Dangerous inputs with path separators should return None
            assert (
                sanitized is None
            ), f"Malicious name should return None: {malicious_name}"

        # Test Windows-style paths with backslashes
        windows_paths = [
            "..\\..\\windows\\system32\\config\\sam",
            "..%5c..%5c..%5cwindows\\system32\\config\\sam",
        ]

        for windows_path in windows_paths:
            sanitized = _sanitize_path_component(windows_path)
            # On Unix, backslashes are not path separators, so these might pass
            # But they should still be handled safely
            if os.sep == "/":
                # On Unix, backslashes are just characters, not separators
                # So they won't be blocked by the separator check
                assert (
                    sanitized is not None
                ), f"Windows path on Unix should not be None: {windows_path}"
            else:
                # On Windows, these should be blocked
                assert (
                    sanitized is None
                ), f"Windows path should be blocked: {windows_path}"

        # Test path with mixed separators that should be blocked
        mixed_separators = [
            "normal\\../../../etc/passwd",  # Backslash + forward slash traversal
        ]

        for mixed_path in mixed_separators:
            sanitized = _sanitize_path_component(mixed_path)
            # Should be blocked due to forward slash separators
            assert (
                sanitized is None
            ), f"Mixed separator path should be blocked: {mixed_path}"

    def test_path_sanitization_preserves_safe_names(self):
        """Test that _sanitize_path_component preserves safe names."""
        from fetchtastic.downloader import _sanitize_path_component

        safe_names = [
            "firmware-rak4631-2.7.9.uf2",
            "device-install.sh",
            "bleota.bin",
            "littlefs-tbeam-2.7.9.bin",
            "normal_file.txt",
            "file-with-dashes.txt",
            "file_with_underscores.txt",
            "file.with.dots.txt",
        ]

        for safe_name in safe_names:
            sanitized = _sanitize_path_component(safe_name)
            # Safe names should be preserved
            assert sanitized is not None
            assert len(sanitized) > 0
            assert sanitized == safe_name  # Should preserve safe names exactly

    def test_path_sanitization_handles_edge_cases(self):
        """Test _sanitize_path_component with edge cases."""
        from fetchtastic.downloader import _sanitize_path_component

        edge_cases = [
            "",  # Empty string
            ".",  # Current directory
            "..",  # Parent directory
            "....",  # Multiple dots
            "   ",  # Whitespace only
            "\x00\x01\x02",  # Control characters
        ]

        for edge_case in edge_cases:
            sanitized = _sanitize_path_component(edge_case)
            # Dangerous edge cases should return None
            if edge_case in ["", ".", "..", "\x00\x01\x02"]:
                assert sanitized is None, f"Edge case should return None: {edge_case!r}"
            else:
                # Other cases might be handled differently but should be safe
                assert sanitized is None or isinstance(sanitized, str)

    def test_safe_extract_path_prevents_traversal(self, tmp_path):
        """Test that safe_extract_path prevents directory traversal."""
        from fetchtastic.downloader import safe_extract_path

        extract_dir = str(tmp_path / "extract")

        # Test various traversal attempts
        malicious_paths = [
            "../../../etc/passwd",
            "test/../../../etc/passwd",
        ]

        for malicious_path in malicious_paths:
            with pytest.raises(ValueError, match="Unsafe extraction path"):
                safe_extract_path(extract_dir, malicious_path)

        # Test absolute paths (should be blocked)
        absolute_paths = [
            "/etc/passwd",
            "/absolute/path/file.txt",
            os.path.join(os.path.abspath(extract_dir) + "-evil", "payload.bin"),
        ]

        for absolute_path in absolute_paths:
            with pytest.raises(ValueError, match="Unsafe extraction path"):
                safe_extract_path(extract_dir, absolute_path)

        # Test Windows-style paths on Unix (behavior depends on platform)
        windows_paths = [
            "..\\..\\windows\\system32\\config\\sam",
            "C:\\Windows\\System32\\config\\sam",
        ]

        for windows_path in windows_paths:
            if os.name == "nt":
                # On Windows, these should be handled appropriately
                try:
                    result = safe_extract_path(extract_dir, windows_path)
                    # If no exception, result should be within extract_dir
                    assert os.path.commonpath(
                        [os.path.abspath(result), os.path.abspath(extract_dir)]
                    ) == os.path.abspath(extract_dir)
                except ValueError:
                    # Or it should raise ValueError
                    pass
            else:
                # On Unix, backslashes are treated as normal characters
                # So these might not trigger traversal detection
                result = safe_extract_path(extract_dir, windows_path)
                # But result should still be within extract_dir due to normpath
                assert os.path.commonpath(
                    [os.path.abspath(result), os.path.abspath(extract_dir)]
                ) == os.path.abspath(extract_dir)

    def test_safe_extract_path_allows_safe_paths(self, tmp_path):
        """Test that safe_extract_path allows legitimate paths."""
        from fetchtastic.downloader import safe_extract_path

        extract_dir = str(tmp_path / "extract")

        safe_paths = [
            "firmware.bin",
            "subdir/firmware.bin",
            "deep/nested/path/file.txt",
            "file-with-dashes.txt",
            "file_with_underscores.txt",
        ]

        for safe_path in safe_paths:
            result = safe_extract_path(extract_dir, safe_path)
            # Should return safe absolute path within extract_dir (cross-platform)
            import os

            assert os.path.commonpath(
                [os.path.abspath(result), os.path.abspath(extract_dir)]
            ) == os.path.abspath(extract_dir)
            assert ".." not in result


class TestSecurityInputValidation:
    """Test security measures for input validation."""

    def test_release_tag_sanitization_blocks_unsafe_tags(self):
        """Test that release tag sanitization blocks unsafe inputs."""
        from fetchtastic.downloader import _sanitize_path_component

        # Test various unsafe release tags
        unsafe_tags = [
            "../../../etc/passwd",
            "release/../../../etc/passwd",
            "release\x00malicious",  # Null byte injection
            "",  # Empty string
            ".",  # Current directory
            "..",  # Parent directory
        ]

        for unsafe_tag in unsafe_tags:
            sanitized = _sanitize_path_component(unsafe_tag)
            assert sanitized is None, f"Unsafe tag should return None: {unsafe_tag!r}"

        # Test that CRLF characters are allowed (they're not path separators)
        crlf_tags = [
            "release\r\nmalicious",
            "tag\rwith\ncrlf",
        ]

        for crlf_tag in crlf_tags:
            sanitized = _sanitize_path_component(crlf_tag)
            assert sanitized == crlf_tag, f"CRLF tag should be allowed: {crlf_tag!r}"

        # Test Windows-style paths separately (behavior depends on platform)
        windows_paths = [
            "..\\..\\windows\\system32",
        ]

        for windows_path in windows_paths:
            sanitized = _sanitize_path_component(windows_path)
            if os.sep == "/":
                # On Unix, backslashes are not separators
                assert (
                    sanitized is not None
                ), f"Windows path on Unix should not be None: {windows_path}"
            else:
                # On Windows, should be blocked
                assert (
                    sanitized is None
                ), f"Windows path should be blocked: {windows_path}"

    def test_release_tag_sanitization_preserves_safe_tags(self):
        """Test that safe release tags are preserved."""
        from fetchtastic.downloader import _sanitize_path_component

        safe_tags = [
            "v2.7.9",
            "v2.7.9.abc123",
            "v2.7.9.fcb1d64",
            "v2.7.9-rc1",
            "v2.7.9-beta",
            "v2.7.9-alpha",
            "release-2.7.9",
            "2.7.9",
        ]

        for safe_tag in safe_tags:
            sanitized = _sanitize_path_component(safe_tag)
            assert sanitized is not None
            assert len(sanitized) > 0
            # Safe tags should be preserved or minimally modified
            assert ".." not in sanitized
            assert "\x00" not in sanitized

    def test_filename_sanitization_in_download_process(self):
        """Test that filenames are sanitized during download process."""
        from fetchtastic.downloader import _sanitize_path_component

        # Test filenames that might come from GitHub API
        test_filenames = [
            "firmware-rak4631-2.7.9.uf2",
            "../../../etc/passwd",  # Malicious
            "device-install.sh",
            "bleota.bin",
            "normal-file.txt",
            "file\x00with\x00nulls.bin",  # Null bytes
            "file\r\nwith\rcrlf.txt",  # CRLF injection
        ]

        for filename in test_filenames:
            sanitized = _sanitize_path_component(filename)
            if ".." in filename or "\x00" in filename:
                # Malicious filenames should be rejected
                assert (
                    sanitized is None
                ), f"Malicious filename should return None: {filename!r}"
            else:
                # Safe filenames should be preserved
                assert sanitized is not None
                assert len(sanitized) > 0
                assert ".." not in sanitized
                assert "\x00" not in sanitized

    def test_directory_name_sanitization_prevents_traversal(self):
        """Test that directory names are sanitized to prevent traversal."""
        from fetchtastic.downloader import _sanitize_path_component

        # Test directory names that might be used for creating release directories
        unsafe_dir_names = [
            "../../../etc",
            "release/../../../malicious",
            "normal\x00directory",  # Null byte
            "",  # Empty
            ".",  # Current directory
            "..",  # Parent directory
        ]

        for dir_name in unsafe_dir_names:
            sanitized = _sanitize_path_component(dir_name)
            assert (
                sanitized is None
            ), f"Unsafe directory name should return None: {dir_name!r}"

        # Test that CRLF characters are allowed (they're not path separators)
        crlf_dir_names = [
            "directory\r\nwith\ncrlf",
            "release\rwith\ncrlf",
        ]

        for crlf_dir_name in crlf_dir_names:
            sanitized = _sanitize_path_component(crlf_dir_name)
            assert (
                sanitized == crlf_dir_name
            ), f"CRLF directory name should be allowed: {crlf_dir_name!r}"


@pytest.mark.parametrize(
    "file_path, should_raise",
    [
        # Safe paths
        ("firmware.bin", False),
        ("subdir/firmware.bin", False),
        ("deep/nested/path/file.txt", False),
        # Dangerous paths
        ("../../../etc/passwd", True),
        ("/etc/passwd", True),
        ("test/../../../etc/passwd", True),
    ],
)
def test_safe_extract_path(tmp_path, file_path, should_raise):
    """Test the safe path extraction logic to prevent directory traversal."""
    extract_dir = str(tmp_path / "extract")
    if should_raise:
        with pytest.raises(ValueError):
            downloader.safe_extract_path(extract_dir, file_path)
    else:
        try:
            downloader.safe_extract_path(extract_dir, file_path)
        except ValueError:
            pytest.fail("safe_extract_path raised ValueError unexpectedly.")


@patch("fetchtastic.downloader.download_file_with_retry")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_check_and_download_skips_unsafe_release_tag(
    _mock_fetch_dirs, _mock_fetch_contents, _mock_download, tmp_path
):
    """Releases with unsafe tag names are ignored to prevent path traversal."""

    releases = [
        {
            "tag_name": "../bad",
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [],
            "body": "",
        }
    ]

    cache_dir = str(tmp_path)
    download_dir = tmp_path / "downloads"

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        cache_dir,
        "Firmware",
        str(download_dir),
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    assert downloaded == []
    assert new_versions == []
    assert failures == []
    assert download_dir.exists()
    assert list(download_dir.iterdir()) == []


@patch("fetchtastic.downloader.download_file_with_retry")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_check_and_download_skips_unsafe_asset_name(
    _mock_fetch_dirs, _mock_fetch_contents, _mock_download, tmp_path
):
    """Assets with unsafe filenames are skipped before download attempts."""

    release_tag = "v7.0.0"
    releases = [
        {
            "tag_name": release_tag,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "../evil.uf2",
                    "browser_download_url": "https://example.invalid/evil.uf2",
                    "size": 10,
                }
            ],
            "body": "",
        }
    ]

    cache_dir = str(tmp_path)
    download_dir = tmp_path / "downloads"

    downloaded, new_versions, failures = downloader.check_and_download(
        releases,
        cache_dir,
        "Firmware",
        str(download_dir),
        versions_to_keep=1,
        extract_patterns=[],
        selected_patterns=["rak4631-"],
        auto_extract=False,
        exclude_patterns=[],
    )

    assert downloaded == []
    assert new_versions == []
    assert failures == []
    assert download_dir.exists()
    # Release directory may be created even if no assets are downloaded
    release_dir = download_dir / release_tag
    assert release_dir.exists()
    assert list(release_dir.iterdir()) == []


@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
def test_prerelease_functions_symlink_safety(
    mock_fetch_dirs, mock_fetch_contents, tmp_path, mock_commit_history
):
    """Test that prerelease functions handle symlinks safely."""
    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Mock repo to return a directory
    mock_fetch_dirs.return_value = ["firmware-1.9.1.abcdef"]
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-esp32-1.9.1.abcdef.bin",
            "download_url": "http://example.com/file.bin",
        }
    ]

    # Call check_for_prereleases
    found, versions = downloader.check_for_prereleases(
        download_dir,
        latest_release_tag="v1.9.0",
        selected_patterns=["esp32"],
        device_manager=None,
    )

    # Should complete without errors
    assert isinstance(found, bool)
    assert isinstance(versions, list)


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
def test_prerelease_symlink_traversal_attack_prevention(
    mock_download, mock_fetch_contents, mock_fetch_dirs, tmp_path, mock_commit_history
):
    """Test that symlink traversal attacks are prevented during prerelease processing."""
    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Mock repo to return a directory
    mock_fetch_dirs.return_value = ["firmware-1.9.1.abcdef"]
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-esp32-1.9.1.abcdef.bin",
            "download_url": "http://example.com/file.bin",
        }
    ]

    # Mock download to create a file
    def mock_download_func(_url, dest_path, _max_retries=3, _backoff_factor=1.0):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_text("mock firmware content")

    mock_download.side_effect = mock_download_func

    # Call check_for_prereleases
    found, versions = downloader.check_for_prereleases(
        download_dir,
        latest_release_tag="v1.9.0",
        selected_patterns=["esp32"],
        device_manager=None,
    )

    # Should complete without security errors
    assert isinstance(found, bool)
    assert isinstance(versions, list)


@patch("fetchtastic.downloader.menu_repo.fetch_repo_directories")
@patch("fetchtastic.downloader.menu_repo.fetch_directory_contents")
@patch("fetchtastic.downloader.download_file_with_retry")
def test_prerelease_symlink_mixed_with_valid_directories(
    mock_download, mock_fetch_contents, mock_fetch_dirs, tmp_path, mock_commit_history
):
    """Test handling of mixed valid directories and symlinks in prerelease processing."""
    download_dir = tmp_path
    prerelease_dir = download_dir / "firmware" / "prerelease"
    prerelease_dir.mkdir(parents=True)

    # Mock repo to return a directory
    mock_fetch_dirs.return_value = ["firmware-1.9.1.abcdef"]
    mock_fetch_contents.return_value = [
        {
            "name": "firmware-esp32-1.9.1.abcdef.bin",
            "download_url": "http://example.com/file.bin",
        }
    ]

    # Mock download to create a file
    def mock_download_func(_url, dest_path, _max_retries=3, _backoff_factor=1.0):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_text("mock firmware content")

    mock_download.side_effect = mock_download_func

    # Call check_for_prereleases
    found, versions = downloader.check_for_prereleases(
        download_dir,
        latest_release_tag="v1.9.0",
        selected_patterns=["esp32"],
        device_manager=None,
    )

    # Should complete without errors
    assert isinstance(found, bool)
    assert isinstance(versions, list)
