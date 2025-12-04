"""
Tests for the constants module.

This module tests that all constants are properly defined and have expected values.
"""

import pytest

from fetchtastic import constants


@pytest.mark.infrastructure
@pytest.mark.unit
class TestGitHubAPIConstants:
    """Test GitHub API URL constants."""

    def test_github_api_base_url(self):
        """Test that GitHub API base URL is correct."""
        assert constants.GITHUB_API_BASE == "https://api.github.com/repos"

    def test_meshtastic_android_releases_url(self):
        """Test Meshtastic Android releases URL."""
        expected = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
        assert constants.MESHTASTIC_ANDROID_RELEASES_URL == expected

    def test_meshtastic_firmware_releases_url(self):
        """Test Meshtastic firmware releases URL."""
        expected = "https://api.github.com/repos/meshtastic/firmware/releases"
        assert constants.MESHTASTIC_FIRMWARE_RELEASES_URL == expected

    def test_meshtastic_github_io_contents_url(self):
        """Test Meshtastic GitHub.io contents URL."""
        expected = (
            "https://api.github.com/repos/meshtastic/meshtastic.github.io/contents"
        )
        assert constants.MESHTASTIC_GITHUB_IO_CONTENTS_URL == expected


@pytest.mark.infrastructure
@pytest.mark.unit
class TestNetworkConstants:
    """Test network-related constants."""

    def test_timeout_values_are_positive(self):
        """Test that all timeout values are positive integers."""
        assert constants.GITHUB_API_TIMEOUT > 0
        assert constants.NTFY_REQUEST_TIMEOUT > 0
        assert constants.PRERELEASE_REQUEST_TIMEOUT > 0
        # Note: DEFAULT_AUTO_EXTRACT was removed as an unused constant.
        # Timeout/retry defaults (DEFAULT_BACKOFF_FACTOR, DEFAULT_CHUNK_SIZE,
        # DEFAULT_CONNECT_RETRIES, DEFAULT_REQUEST_TIMEOUT) are exercised indirectly
        # via download tests rather than asserted here
        assert constants.WINDOWS_MAX_REPLACE_RETRIES > 0
        assert constants.WINDOWS_INITIAL_RETRY_DELAY > 0


@pytest.mark.infrastructure
@pytest.mark.unit
class TestFileConstants:
    """Test file-related constants."""

    def test_file_extensions(self):
        """Test file extension constants."""
        assert constants.APK_EXTENSION == ".apk"
        assert constants.ZIP_EXTENSION == ".zip"
        assert constants.SHELL_SCRIPT_EXTENSION == ".sh"

    def test_executable_permissions(self):
        """Test executable permissions constant."""
        assert constants.EXECUTABLE_PERMISSIONS == 0o755

    def test_directory_names(self):
        """Test directory name constants."""
        assert constants.REPO_DOWNLOADS_DIR == "repo-dls"
        assert constants.FIRMWARE_PRERELEASES_DIR_NAME == "prerelease"
        assert constants.MESHTASTIC_DIR_NAME == "Meshtastic"

    def test_file_names(self):
        """Test file name constants."""
        assert constants.CONFIG_FILE_NAME == "fetchtastic.yaml"


@pytest.mark.infrastructure
@pytest.mark.unit
class TestDefaultValues:
    """Test default configuration values."""

    def test_version_defaults(self):
        """Test default version counts."""
        assert constants.DEFAULT_FIRMWARE_VERSIONS_TO_KEEP >= 1
        assert constants.DEFAULT_ANDROID_VERSIONS_TO_KEEP >= 1

    def test_auto_extract_default(self):
        """Test auto extract default."""
        # Note: DEFAULT_AUTO_EXTRACT was removed as unused constant
        # assert isinstance(constants.DEFAULT_AUTO_EXTRACT, bool)

    def test_release_scan_count(self):
        """Test release scan count."""
        assert constants.RELEASE_SCAN_COUNT > 0


@pytest.mark.infrastructure
@pytest.mark.unit
class TestLoggingConstants:
    """Test logging-related constants."""

    def test_logger_name(self):
        """Test logger name constant."""
        assert constants.LOGGER_NAME == "fetchtastic"

    def test_log_formats_are_strings(self):
        """Test that log format strings are valid."""
        assert isinstance(constants.LOG_DATE_FORMAT, str)
        assert isinstance(constants.INFO_LOG_FORMAT, str)
        assert isinstance(constants.DEBUG_LOG_FORMAT, str)

    def test_log_file_settings(self):
        """Test log file configuration constants."""
        assert constants.LOG_FILE_MAX_BYTES > 0
        assert constants.LOG_FILE_BACKUP_COUNT > 0

    def test_environment_variable_name(self):
        """Test environment variable name."""
        assert constants.LOG_LEVEL_ENV_VAR == "FETCHTASTIC_LOG_LEVEL"


@pytest.mark.infrastructure
@pytest.mark.unit
class TestValidationConstants:
    """Test validation-related constants."""

    def test_version_regex_pattern(self):
        """Assert VERSION_REGEX_PATTERN is a string and a valid regular expression.

        Verifies that constants.VERSION_REGEX_PATTERN is a str and that re.compile can compile it without error.
        """
        assert isinstance(constants.VERSION_REGEX_PATTERN, str)
        # Test that it's a valid regex by compiling it
        import re

        re.compile(constants.VERSION_REGEX_PATTERN)

    def test_default_extraction_patterns(self):
        """Test default extraction patterns."""
        assert isinstance(constants.DEFAULT_EXTRACTION_PATTERNS, list)
        assert len(constants.DEFAULT_EXTRACTION_PATTERNS) > 0
        # All patterns should be strings
        for pattern in constants.DEFAULT_EXTRACTION_PATTERNS:
            assert isinstance(pattern, str)


@pytest.mark.infrastructure
@pytest.mark.unit
class TestConstantsIntegrity:
    """Test overall constants module integrity."""

    def test_no_none_values(self):
        """Test that no constants are None (which might indicate missing values)."""
        import inspect

        for name, value in inspect.getmembers(constants):
            if not name.startswith("_"):  # Skip private attributes
                assert value is not None, f"Constant {name} should not be None"

    def test_constants_are_immutable_types(self):
        """Test that constants use immutable types where appropriate."""
        # These should be immutable types (str, int, float, tuple)
        immutable_constants = [
            "GITHUB_API_BASE",
            "GITHUB_API_TIMEOUT",
            "API_CALL_DELAY",
            "APK_EXTENSION",
            "ZIP_EXTENSION",
            "LOGGER_NAME",
        ]

        for const_name in immutable_constants:
            if hasattr(constants, const_name):
                value = getattr(constants, const_name)
                assert isinstance(
                    value, (str, int, float, tuple)
                ), f"{const_name} should be an immutable type, got {type(value)}"
