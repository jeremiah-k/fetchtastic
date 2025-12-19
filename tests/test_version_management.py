"""
Tests for Version Management Module

Comprehensive tests for the version.py module covering:
- Version normalization and parsing
- Version comparison with PEP 440 semantics
- Version tuple extraction
- Prerelease version handling
- Version tracking and JSON operations
"""

import json
from unittest.mock import MagicMock

from fetchtastic.download.version import (
    VersionManager,
    _read_latest_release_tag,
    _read_prerelease_tracking_data,
    _write_latest_release_tag,
    calculate_expected_prerelease_version,
    extract_version,
    is_prerelease_directory,
    normalize_commit_identifier,
)


class TestVersionNormalization:
    """Test version normalization functionality."""

    def test_normalize_version_standard(self):
        """Test normalizing standard version strings."""
        vm = VersionManager()
        result = vm.normalize_version("v1.2.3")
        assert result is not None
        assert str(result) == "1.2.3"

    def test_normalize_version_prerelease(self):
        """Test normalizing prerelease versions."""
        vm = VersionManager()
        result = vm.normalize_version("v1.2.3-alpha1")
        assert result is not None
        assert str(result) == "1.2.3a1"

    def test_normalize_version_with_hash(self):
        """Test normalizing versions with commit hash."""
        vm = VersionManager()
        result = vm.normalize_version("v1.2.3.abc123")
        assert result is not None
        assert str(result) == "1.2.3+abc123"

    def test_normalize_version_invalid(self):
        """Test normalizing invalid version strings."""
        vm = VersionManager()
        result = vm.normalize_version("invalid")
        assert result is None

    def test_normalize_version_none(self):
        """Test normalizing None input."""
        vm = VersionManager()
        result = vm.normalize_version(None)
        assert result is None

    def test_normalize_version_empty(self):
        """Test normalizing empty string."""
        vm = VersionManager()
        result = vm.normalize_version("")
        assert result is None


class TestVersionTuple:
    """Test version tuple extraction."""

    def test_get_release_tuple_standard(self):
        """Test extracting release tuple from standard version."""
        vm = VersionManager()
        result = vm.get_release_tuple("v1.2.3")
        assert result == (1, 2, 3)

    def test_get_release_tuple_with_v(self):
        """Test extracting release tuple with v prefix."""
        vm = VersionManager()
        result = vm.get_release_tuple("v2.0.1")
        assert result == (2, 0, 1)

    def test_get_release_tuple_patch_only(self):
        """Test extracting release tuple from version with only major.minor."""
        vm = VersionManager()
        result = vm.get_release_tuple("v1.5")
        assert result == (1, 5)

    def test_get_release_tuple_invalid(self):
        """Test extracting release tuple from invalid version."""
        vm = VersionManager()
        result = vm.get_release_tuple("invalid")
        assert result is None

    def test_get_release_tuple_none(self):
        """Test extracting release tuple from None."""
        vm = VersionManager()
        result = vm.get_release_tuple(None)
        assert result is None


class TestVersionComparison:
    """Test version comparison functionality."""

    def test_compare_versions_equal(self):
        """Test comparing equal versions."""
        vm = VersionManager()
        result = vm.compare_versions("v1.2.3", "v1.2.3")
        assert result == 0

    def test_compare_versions_greater(self):
        """Test comparing versions where first is greater."""
        vm = VersionManager()
        result = vm.compare_versions("v1.2.4", "v1.2.3")
        assert result == 1


class TestPrereleaseIdentifierFormatting:
    """Test prerelease identifier formatting helpers."""

    def test_create_prerelease_version_with_hash_matches_expected_format(self):
        vm = VersionManager()
        assert (
            vm.create_prerelease_version_with_hash("v1.2.3", "abcdef123456")
            == "1.2.3-rc1+abcdef1"
        )

    def test_compare_versions_lesser(self):
        """Test comparing versions where first is lesser."""
        vm = VersionManager()
        result = vm.compare_versions("v1.2.2", "v1.2.3")
        assert result == -1

    def test_compare_versions_prerelease(self):
        """Test comparing prerelease versions."""
        vm = VersionManager()
        result = vm.compare_versions("v1.2.3-alpha1", "v1.2.3")
        assert result == -1

    def test_compare_versions_natural_fallback(self):
        """Test natural sort fallback for non-standard versions."""
        vm = VersionManager()
        result = vm.compare_versions("v1.10", "v1.2")
        assert result == 1


class TestVersionPrefix:
    """Test version prefix handling."""

    def test_ensure_v_prefix_missing(self):
        """Test adding v prefix when missing."""
        vm = VersionManager()
        result = vm.ensure_v_prefix_if_missing("1.2.3")
        assert result == "v1.2.3"

    def test_ensure_v_prefix_present(self):
        """Test not adding v prefix when already present."""
        vm = VersionManager()
        result = vm.ensure_v_prefix_if_missing("v1.2.3")
        assert result == "v1.2.3"

    def test_ensure_v_prefix_uppercase(self):
        """Test handling uppercase V prefix."""
        vm = VersionManager()
        result = vm.ensure_v_prefix_if_missing("V1.2.3")
        assert result == "V1.2.3"

    def test_ensure_v_prefix_empty(self):
        """Test handling empty string."""
        vm = VersionManager()
        result = vm.ensure_v_prefix_if_missing("")
        assert result == ""


class TestExtractCleanVersion:
    """Test clean version extraction."""

    def test_extract_clean_version_standard(self):
        """Test extracting clean version from standard version."""
        vm = VersionManager()
        result = vm.extract_clean_version("v1.2.3")
        assert result == "v1.2.3"

    def test_extract_clean_version_with_hash(self):
        """Test extracting clean version from version with hash."""
        vm = VersionManager()
        result = vm.extract_clean_version("v1.2.3.abc123")
        assert result == "v1.2.3"

    def test_extract_clean_version_long_hash(self):
        """Test extracting clean version from version with long hash."""
        vm = VersionManager()
        result = vm.extract_clean_version("v1.2.3.abcdef123456")
        assert result == "v1.2.3"

    def test_extract_clean_version_no_hash(self):
        """Test extracting clean version when no hash present."""
        vm = VersionManager()
        result = vm.extract_clean_version("v1.2")
        assert result == "v1.2"

    def test_extract_clean_version_none(self):
        """Test extracting clean version from None."""
        vm = VersionManager()
        result = vm.extract_clean_version(None)
        assert result is None


class TestExpectedPrereleaseVersion:
    """Test expected prerelease version calculation."""

    def test_calculate_expected_prerelease_standard(self):
        """Test calculating expected prerelease from standard version."""
        vm = VersionManager()
        result = vm.calculate_expected_prerelease_version("v1.2.3")
        assert result == "1.2.4"

    def test_calculate_expected_prerelease_hash_suffix_alpha_style(self):
        """Hash-suffixed tags like v2.7.16.a597230 should increment the base patch."""
        vm = VersionManager()
        assert vm.calculate_expected_prerelease_version("v2.7.16.a597230") == "2.7.17"

    def test_calculate_expected_prerelease_hash_suffix_numeric_style(self):
        """Hash-suffixed tags like v2.7.15.567b8ea should increment the base patch."""
        vm = VersionManager()
        assert vm.calculate_expected_prerelease_version("v2.7.15.567b8ea") == "2.7.16"

    def test_calculate_expected_prerelease_module_helper(self):
        assert calculate_expected_prerelease_version("v2.7.16.a597230") == "2.7.17"

    def test_calculate_expected_prerelease_no_patch(self):
        """Test calculating expected prerelease from version without patch."""
        vm = VersionManager()
        result = vm.calculate_expected_prerelease_version("v1.2")
        assert result == "1.2.1"

    def test_calculate_expected_prerelease_invalid(self):
        """Test calculating expected prerelease from invalid version."""
        vm = VersionManager()
        result = vm.calculate_expected_prerelease_version("invalid")
        assert result is None

    def test_calculate_expected_prerelease_empty(self):
        """Test calculating expected prerelease from empty string."""
        vm = VersionManager()
        result = vm.calculate_expected_prerelease_version("")
        assert result is None


class TestPrereleaseVersionParsing:
    """Test prerelease version parsing."""

    def test_parse_commit_history_for_prerelease(self):
        """Test parsing commit history for prerelease version."""
        vm = VersionManager()
        commits = ["2.7.13.abc123 merged", "2.7.13.def456 added"]
        result = vm.parse_commit_history_for_prerelease_version(commits, "2.7.13")
        assert result == "2.7.13.abc123"

    def test_parse_commit_history_no_match(self):
        """Test parsing commit history with no matches."""
        vm = VersionManager()
        commits = ["unrelated commit", "another commit"]
        result = vm.parse_commit_history_for_prerelease_version(commits, "2.7.13")
        assert result == "2.7.14"  # Falls back to incremented version

    def test_parse_commit_history_empty(self):
        """Test parsing empty commit history."""
        vm = VersionManager()
        result = vm.parse_commit_history_for_prerelease_version([], "2.7.13")
        assert result is None


class TestRateLimit:
    """Test rate limit summary functionality."""

    def test_summarize_rate_limit_with_data(self):
        """Test summarizing rate limit with valid data."""
        vm = VersionManager()
        mock_response = MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": "1640995200",
            "X-RateLimit-Limit": "5000",
        }
        result = vm.summarize_rate_limit(mock_response)
        assert result == {"remaining": 4999, "reset": 1640995200, "limit": 5000}

    def test_summarize_rate_limit_missing_headers(self):
        """Test summarizing rate limit with missing headers."""
        vm = VersionManager()
        mock_response = MagicMock()
        mock_response.headers = {}
        result = vm.summarize_rate_limit(mock_response)
        assert result is None

    def test_summarize_rate_limit_invalid_values(self):
        """Test summarizing rate limit with invalid values."""
        vm = VersionManager()
        mock_response = MagicMock()
        mock_response.headers = {
            "X-RateLimit-Remaining": "invalid",
            "X-RateLimit-Reset": "invalid",
        }
        result = vm.summarize_rate_limit(mock_response)
        assert result is None


class TestCommitHash:
    """Test commit hash handling."""

    def test_get_commit_hash_suffix_standard(self):
        """Test extracting commit hash suffix."""
        vm = VersionManager()
        result = vm.get_commit_hash_suffix("abc123def456")
        assert result == "abc123d"

    def test_get_commit_hash_suffix_short(self):
        """Test extracting commit hash suffix from short hash."""
        vm = VersionManager()
        result = vm.get_commit_hash_suffix("abc")
        assert result == "abc"

    def test_get_commit_hash_suffix_empty(self):
        """Test extracting commit hash suffix from empty string."""
        vm = VersionManager()
        result = vm.get_commit_hash_suffix("")
        assert result == ""


class TestPrereleaseVersionCreation:
    """Test prerelease version creation."""

    def test_create_prerelease_version_with_hash(self):
        """Test creating prerelease version with commit hash."""
        vm = VersionManager()
        result = vm.create_prerelease_version_with_hash("1.2.3", "abc123", "rc")
        assert result == "1.2.3-rc1+abc123"

    def test_create_prerelease_version_no_hash(self):
        """Test creating prerelease version without hash."""
        vm = VersionManager()
        result = vm.create_prerelease_version_with_hash("1.2.3", "", "rc")
        assert result == "1.2.3-rc1"

    def test_create_prerelease_version_empty_base(self):
        """Test creating prerelease version with empty base."""
        vm = VersionManager()
        result = vm.create_prerelease_version_with_hash("", "abc123", "rc")
        assert result == ""


class TestPrereleaseDetection:
    """Test prerelease version detection."""

    def test_is_prerelease_version_rc(self):
        """Test detecting RC prerelease."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3-rc1")
        assert result is True

    def test_is_prerelease_version_alpha(self):
        """Test detecting alpha prerelease."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3-alpha1")
        assert result is True

    def test_is_prerelease_version_beta(self):
        """Test detecting beta prerelease."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3-beta2")
        assert result is True

    def test_is_prerelease_version_dev(self):
        """Test detecting dev prerelease."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3-dev")
        assert result is True

    def test_is_prerelease_version_with_hash(self):
        """Test detecting prerelease with commit hash."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3.abc123")
        assert result is True

    def test_is_prerelease_version_release(self):
        """Test that release versions are not detected as prerelease."""
        vm = VersionManager()
        result = vm.is_prerelease_version("v1.2.3")
        assert result is False

    def test_is_prerelease_version_empty(self):
        """Test detecting prerelease for empty string."""
        vm = VersionManager()
        result = vm.is_prerelease_version("")
        assert result is False


class TestPrereleaseMetadata:
    """Test prerelease metadata extraction."""

    def test_get_prerelease_metadata_rc(self):
        """Test extracting metadata from RC prerelease."""
        vm = VersionManager()
        result = vm.get_prerelease_metadata_from_version("v1.2.3-rc1")
        assert result["is_prerelease"] is True
        assert result["base_version"] == "1.2.3"
        assert result["prerelease_type"] == "rc"
        assert result["prerelease_number"] == "1"

    def test_get_prerelease_metadata_with_hash(self):
        """Test extracting metadata from prerelease with hash."""
        vm = VersionManager()
        result = vm.get_prerelease_metadata_from_version("v1.2.3-rc1+abc123")
        assert result["is_prerelease"] is True
        assert result["commit_hash"] == "abc123"

    def test_get_prerelease_metadata_release(self):
        """Test extracting metadata from release version."""
        vm = VersionManager()
        result = vm.get_prerelease_metadata_from_version("v1.2.3")
        assert result["is_prerelease"] is False

    def test_get_prerelease_metadata_empty(self):
        """Test extracting metadata from empty string."""
        vm = VersionManager()
        result = vm.get_prerelease_metadata_from_version("")
        expected = {
            "original_version": "",
            "is_prerelease": False,
            "base_version": "",
            "prerelease_type": "",
            "prerelease_number": "",
            "commit_hash": "",
        }
        assert result == expected


class TestPrereleaseFiltering:
    """Test prerelease filtering functionality."""

    def test_filter_prereleases_include_pattern(self):
        """Test filtering prereleases with include patterns."""
        vm = VersionManager()
        prereleases = ["v1.2.3-rc1", "v1.2.3-alpha1", "v1.2.4-rc1"]
        result = vm.filter_prereleases_by_pattern(prereleases, ["rc"], [])
        assert result == ["v1.2.3-rc1", "v1.2.4-rc1"]

    def test_filter_prereleases_exclude_pattern(self):
        """Test filtering prereleases with exclude patterns."""
        vm = VersionManager()
        prereleases = ["v1.2.3-rc1", "v1.2.3-alpha1", "v1.2.4-rc1"]
        result = vm.filter_prereleases_by_pattern(prereleases, [], ["alpha"])
        assert result == ["v1.2.3-rc1", "v1.2.4-rc1"]

    def test_filter_prereleases_both_patterns(self):
        """Test filtering prereleases with both include and exclude patterns."""
        vm = VersionManager()
        prereleases = ["v1.2.3-rc1", "v1.2.3-alpha1", "v1.2.4-beta1"]
        result = vm.filter_prereleases_by_pattern(
            prereleases, ["rc", "beta"], ["alpha"]
        )
        assert result == ["v1.2.3-rc1", "v1.2.4-beta1"]


class TestVersionTracking:
    """Test version tracking functionality."""

    def test_create_version_tracking_json(self):
        """Test creating version tracking JSON."""
        vm = VersionManager()
        result = vm.create_version_tracking_json("v1.2.3", "firmware")
        assert result["version"] == "v1.2.3"
        assert result["type"] == "firmware"
        assert "timestamp" in result
        assert result["latest_version"] == "v1.2.3"

    def test_create_version_tracking_json_with_data(self):
        """Test creating version tracking JSON with additional data."""
        vm = VersionManager()
        additional_data = {"custom_field": "value"}
        result = vm.create_version_tracking_json(
            "v1.2.3", "firmware", additional_data=additional_data
        )
        assert result["custom_field"] == "value"

    def test_validate_version_tracking_data_valid(self):
        """Test validating valid version tracking data."""
        vm = VersionManager()
        data = {
            "version": "v1.2.3",
            "type": "firmware",
            "timestamp": "2023-01-01T00:00:00Z",
        }
        result = vm.validate_version_tracking_data(data, ["version"])
        assert result is True

    def test_validate_version_tracking_data_invalid(self):
        """Test validating invalid version tracking data."""
        vm = VersionManager()
        data = {"type": "firmware", "timestamp": "2023-01-01T00:00:00Z"}
        result = vm.validate_version_tracking_data(data, ["version"])
        assert result is False


class TestLegacyFunctions:
    """Test legacy compatibility functions."""

    def test_calculate_expected_prerelease_version_function(self):
        """Test the module-level calculate_expected_prerelease_version function."""
        result = calculate_expected_prerelease_version("v1.2.3")
        assert result == "1.2.4"

    def test_extract_version_function(self):
        """Test the extract_version function."""
        result = extract_version("firmware-v1.2.3.abc123")
        assert result == "v1.2.3.abc123"

    def test_is_prerelease_directory_true(self):
        """Test detecting prerelease directory."""
        result = is_prerelease_directory("firmware-v1.2.3.abc123")
        assert result is True

    def test_is_prerelease_directory_false(self):
        """Test detecting non-prerelease directory."""
        result = is_prerelease_directory("firmware-v1.2.3")
        assert result is False

    def test_normalize_commit_identifier_standard(self):
        """Test normalizing standard commit identifier."""
        result = normalize_commit_identifier("abc123", "v1.2.3")
        assert result == "1.2.3.abc123"

    def test_normalize_commit_identifier_already_normalized(self):
        """Test normalizing already normalized identifier."""
        result = normalize_commit_identifier("1.2.3.abc123", "v1.2.3")
        assert result == "1.2.3.abc123"


class TestFileOperations:
    """Test file-based operations."""

    def test_read_latest_release_tag_exists(self, tmp_path):
        """Test reading latest release tag from existing file."""
        json_file = tmp_path / "latest.json"
        data = {"latest_version": "v1.2.3"}
        json_file.write_text(json.dumps(data))

        result = _read_latest_release_tag(str(json_file))
        assert result == "v1.2.3"

    def test_read_latest_release_tag_not_exists(self):
        """Test reading latest release tag from non-existent file."""
        result = _read_latest_release_tag("/non/existent/file.json")
        assert result is None

    def test_write_latest_release_tag(self, tmp_path):
        """Test writing latest release tag."""
        json_file = tmp_path / "latest.json"
        result = _write_latest_release_tag(str(json_file), "v1.2.3", "firmware")
        assert result is True
        assert json_file.exists()

        # Verify content
        data = json.loads(json_file.read_text())
        assert data["latest_version"] == "v1.2.3"
        assert data["file_type"] == "firmware"

    def test_read_prerelease_tracking_data_exists(self, tmp_path):
        """Test reading prerelease tracking data from existing file."""
        json_file = tmp_path / "prerelease.json"
        data = {
            "version": "v1.2.3",
            "commits": ["abc123"],
            "timestamp": "2023-01-01T00:00:00Z",
        }
        json_file.write_text(json.dumps(data))

        commits, version, timestamp = _read_prerelease_tracking_data(str(json_file))
        assert commits == ["1.2.3.abc123"]  # Gets normalized
        assert version == "v1.2.3"
        assert timestamp == "2023-01-01T00:00:00Z"

    def test_read_prerelease_tracking_data_not_exists(self):
        """Test reading prerelease tracking data from non-existent file."""
        commits, version, timestamp = _read_prerelease_tracking_data(
            "/non/existent/file.json"
        )
        assert commits == []
        assert version is None
        assert timestamp is None
