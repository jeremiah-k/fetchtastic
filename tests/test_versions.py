"""
Version comparison and parsing tests for the Fetchtastic downloader module.

This module contains tests for version comparison logic, version parsing,
and related utility functions.
"""

import pytest

from fetchtastic import downloader
from tests.test_constants import (
    TEST_VERSION_NEW,
    TEST_VERSION_NEWER,
    TEST_VERSION_OLD,
)


# Test cases for compare_versions
@pytest.mark.parametrize(
    "version1, version2, expected",
    [
        (TEST_VERSION_OLD, "2.0.0", 1),
        ("2.0.1", "2.0.0", 1),
        ("2.0.0", "2.0.1", -1),
        ("1.9.0", "2.0.0", -1),
        ("2.0.0", "2.0.0", 0),
        (TEST_VERSION_NEWER, TEST_VERSION_NEW, 1),
        (TEST_VERSION_NEW, TEST_VERSION_NEWER, -1),
        ("2.3.0", "2.3.0.b123456", 1),  # 2.3.0 > 2.3.0.b123456 (release > pre-release)
        ("v1.2.3", "1.2.3", 0),  # Should handle 'v' prefix
        ("1.2", "1.2.3", -1),  # Handle different number of parts
    ],
)
def test_compare_versions(version1, version2, expected):
    """Test the version comparison logic."""
    assert downloader.compare_versions(version1, version2) == expected
    # Antisymmetry: reversing operands should flip the sign
    assert downloader.compare_versions(version2, version1) == -expected


def test_compare_versions_prerelease_parsing():
    """Test new prerelease version parsing logic."""
    # Test dot-separated prerelease versions
    assert downloader.compare_versions("2.3.0.rc1", "2.3.0") == -1  # rc1 < final
    assert downloader.compare_versions("2.3.0.dev1", "2.3.0") == -1  # dev1 < final
    assert downloader.compare_versions("2.3.0.alpha1", "2.3.0") == -1  # alpha1 < final
    assert downloader.compare_versions("2.3.0.beta2", "2.3.0") == -1  # beta2 < final

    # Test dash-separated prerelease versions
    assert downloader.compare_versions("2.3.0-rc1", "2.3.0") == -1  # rc1 < final
    assert downloader.compare_versions("2.3.0-dev1", "2.3.0") == -1  # dev1 < final
    assert downloader.compare_versions("2.3.0-alpha1", "2.3.0") == -1  # alpha1 < final
    assert downloader.compare_versions("2.3.0-beta2", "2.3.0") == -1  # beta2 < final

    # rc ordering
    assert downloader.compare_versions("2.3.0.rc0", "2.3.0.rc1") == -1

    # Test prerelease ordering
    assert (
        downloader.compare_versions("2.3.0.alpha1", "2.3.0.beta1") == -1
    )  # alpha < beta
    assert downloader.compare_versions("2.3.0.beta1", "2.3.0.rc1") == -1  # beta < rc
    assert downloader.compare_versions("2.3.0.rc1", "2.3.0.dev1") == 1  # rc > dev


def test_compare_versions_invalid_version_exception():
    """Test InvalidVersion exception handling in version parsing."""
    # Test with a version that will trigger the hash coercion and InvalidVersion exception
    # This should exercise the InvalidVersion exception handling in the _try_parse function
    result = downloader.compare_versions("1.0.0.invalid+hash", "1.0.0")
    # The function should handle the exception gracefully and return a comparison result
    # Natural sort fallback should determine "1.0.0.invalid+hash" > "1.0.0"
    assert result == 1  # Should be greater due to natural sort fallback


def test_compare_versions_hash_coercion():
    """Test hash coercion in version parsing."""
    # Test versions with hash patterns that get coerced to local versions
    assert downloader.compare_versions("1.0.0.abc123", "1.0.0") == 1  # local > base
    assert (
        downloader.compare_versions("2.1.0.def456", "2.1.0.abc123") == 1
    )  # lexical comparison

    # Test edge cases that might trigger InvalidVersion in hash coercion
    result = downloader.compare_versions("1.0.0.invalid-hash+more", "1.0.0")
    assert isinstance(result, int)  # Should handle gracefully


def test_compare_versions_prerelease_edge_cases():
    """Test edge cases in prerelease version parsing."""
    # Test prerelease versions that might trigger InvalidVersion during coercion
    assert downloader.compare_versions("2.3.0.rc", "2.3.0") == -1  # rc without number
    assert downloader.compare_versions("2.3.0-dev", "2.3.0") == -1  # dev without number

    # Test mixed separators and edge cases
    result = downloader.compare_versions("2.3.0.invalid-pre", "2.3.0")
    assert isinstance(result, int)  # Should handle gracefully


# Tests for _normalize_version function
def test_normalize_version_basic():
    """Test basic version normalization."""
    from packaging.version import Version

    # Valid versions
    result = downloader._normalize_version("1.2.3")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3"

    result = downloader._normalize_version("v1.2.3")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3"

    # Invalid versions
    assert downloader._normalize_version(None) is None
    assert downloader._normalize_version("") is None
    assert downloader._normalize_version("   ") is None


def test_normalize_version_prerelease():
    """Test prerelease version normalization."""
    from packaging.version import Version

    result = downloader._normalize_version("1.2.3-alpha1")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3a1"

    result = downloader._normalize_version("1.2.3.beta2")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3b2"

    result = downloader._normalize_version("1.2.3rc1")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3rc1"


def test_normalize_version_hash_suffix():
    """Test version normalization with hash suffixes."""
    from packaging.version import Version

    result = downloader._normalize_version("1.2.3.abc123")
    assert isinstance(result, Version)
    assert str(result) == "1.2.3+abc123"


# Tests for _get_release_tuple function
def test_get_release_tuple():
    """Test extracting release tuple from version strings."""
    assert downloader._get_release_tuple("1.2.3") == (1, 2, 3)
    assert downloader._get_release_tuple("v1.2.3") == (1, 2, 3)
    assert downloader._get_release_tuple("1.2") == (1, 2)
    assert downloader._get_release_tuple("1.2.3.4") == (1, 2, 3, 4)

    # Invalid inputs
    assert downloader._get_release_tuple(None) is None
    assert downloader._get_release_tuple("") is None
    assert downloader._get_release_tuple("invalid") is None


# Tests for _ensure_v_prefix_if_missing function
def test_ensure_v_prefix_if_missing():
    """Test adding 'v' prefix to versions when missing."""
    assert downloader._ensure_v_prefix_if_missing("1.2.3") == "v1.2.3"
    assert downloader._ensure_v_prefix_if_missing("v1.2.3") == "v1.2.3"
    assert downloader._ensure_v_prefix_if_missing("V1.2.3") == "V1.2.3"
    assert downloader._ensure_v_prefix_if_missing("") == ""
    assert downloader._ensure_v_prefix_if_missing(None) is None


# Tests for _normalize_commit_identifier function
def test_normalize_commit_identifier():
    """Test normalizing commit identifiers."""
    # Already normalized
    assert (
        downloader._normalize_commit_identifier("1.2.3.abc123", "v1.2.3")
        == "1.2.3.abc123"
    )

    # Hash only - should combine with version
    assert downloader._normalize_commit_identifier("abc123", "v1.2.3") == "1.2.3.abc123"
    assert (
        downloader._normalize_commit_identifier("abcdef123456", "2.7.13")
        == "2.7.13.abcdef123456"
    )

    # Hash only without version - should return hash as-is
    assert downloader._normalize_commit_identifier("abc123", None) == "abc123"

    # Invalid inputs
    assert downloader._normalize_commit_identifier("invalid", "v1.2.3") == "invalid"


# Tests for _extract_clean_version function
def test_extract_clean_version():
    """Test extracting clean version from version+hash strings."""
    assert downloader._extract_clean_version("v1.2.3") == "v1.2.3"
    assert downloader._extract_clean_version("v1.2.3.abc123") == "v1.2.3"
    assert downloader._extract_clean_version("1.2.3.def456") == "v1.2.3"
    assert downloader._extract_clean_version("2.7.13.abcdef") == "v2.7.13"

    # Invalid inputs
    assert downloader._extract_clean_version(None) is None
    assert downloader._extract_clean_version("") is None
    assert downloader._extract_clean_version("invalid") == "vinvalid"


# Tests for extract_version function
def test_extract_version():
    """Test extracting version from firmware directory names."""
    assert downloader.extract_version("firmware-1.2.3") == "1.2.3"
    assert downloader.extract_version("firmware-v1.2.3") == "v1.2.3"
    assert downloader.extract_version("firmware-2.7.13.abc123") == "2.7.13.abc123"

    # Edge cases
    assert downloader.extract_version("firmware-") == ""
    assert downloader.extract_version("other-prefix-1.2.3") == "other-prefix-1.2.3"


# Tests for calculate_expected_prerelease_version function
def test_calculate_expected_prerelease_version():
    """Test calculating expected prerelease version."""
    assert downloader.calculate_expected_prerelease_version("1.2.3") == "1.2.4"
    assert downloader.calculate_expected_prerelease_version("v1.2.3") == "1.2.4"
    assert downloader.calculate_expected_prerelease_version("2.7.6") == "2.7.7"
    assert (
        downloader.calculate_expected_prerelease_version("1.0") == "1.0.1"
    )  # Missing patch

    # Invalid inputs
    assert downloader.calculate_expected_prerelease_version("invalid") == ""
    assert downloader.calculate_expected_prerelease_version("") == ""


# Tests for cleanup_old_versions function
def test_cleanup_old_versions(tmp_path):
    """Test cleanup of old version directories."""
    from unittest.mock import patch

    # Create test directory structure
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    # Create version directories
    (versions_dir / "1.0.0").mkdir()
    (versions_dir / "1.1.0").mkdir()
    (versions_dir / "1.2.0").mkdir()
    (versions_dir / "repo-dls").mkdir()  # Should be excluded
    (versions_dir / "prerelease").mkdir()  # Should be excluded

    # Mock _safe_rmtree to track calls
    removed = []

    def mock_safe_rmtree(_path, _base_dir, version):
        """
        Record a version as removed (test mock).

        Parameters:
            version (str): Version identifier to mark as removed; appended to the `removed` list.

        Returns:
            bool: `True` indicating the removal was simulated successfully.
        """
        removed.append(version)
        return True

    with patch("fetchtastic.downloader._safe_rmtree", side_effect=mock_safe_rmtree):
        downloader.cleanup_old_versions(str(versions_dir), ["1.1.0", "1.2.0"])

    # Should remove 1.0.0 but keep others
    assert "1.0.0" in removed
    assert "1.1.0" not in removed
    assert "1.2.0" not in removed
    assert "repo-dls" not in removed
    assert "prerelease" not in removed
