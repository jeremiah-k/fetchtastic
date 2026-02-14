"""
Comprehensive tests for the Fetchtastic exceptions module.

Tests the custom exception hierarchy including:
- Base FetchtasticError and error message formatting
- Configuration errors (ConfigurationError, ConfigFileError, ConfigValidationError)
- Download errors (DownloadError, NetworkError, HTTPError, RateLimitError)
- File system errors (FileSystemError, FilePermissionError, DiskSpaceError, PathValidationError)
- Validation errors (ValidationError, VersionError, PatternError)
- Archive errors (ArchiveError, CorruptedArchiveError, ExtractionError)
- API errors (APIError, AuthenticationError, ResourceNotFoundError)
- Setup/Installation errors (SetupError, CronError, ShortcutError, MigrationError)
"""

import pytest

from fetchtastic.exceptions import (
    APIError,
    ArchiveError,
    AuthenticationError,
    ConfigFileError,
    ConfigurationError,
    ConfigValidationError,
    CorruptedArchiveError,
    CronError,
    DiskSpaceError,
    DownloadError,
    ExtractionError,
    FetchtasticError,
    FilePermissionError,
    FileSystemError,
    HTTPError,
    MigrationError,
    NetworkError,
    PathValidationError,
    PatternError,
    RateLimitError,
    ResourceNotFoundError,
    SetupError,
    ShortcutError,
    ValidationError,
    VersionError,
)


class TestFetchtasticError:
    """Test base FetchtasticError exception."""

    def test_basic_message(self):
        """Test basic error message."""
        error = FetchtasticError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.details is None

    def test_message_with_details(self):
        """Test error message with additional details."""
        error = FetchtasticError("Operation failed", details="Connection timeout")
        assert str(error) == "Operation failed - Connection timeout"
        assert error.message == "Operation failed"
        assert error.details == "Connection timeout"

    def test_inheritance(self):
        """Test that FetchtasticError inherits from Exception."""
        error = FetchtasticError("Test error")
        assert isinstance(error, Exception)

    def test_empty_message(self):
        """Test error with empty message."""
        error = FetchtasticError("")
        assert str(error) == ""
        assert error.message == ""

    def test_none_details(self):
        """Test that None details are handled correctly."""
        error = FetchtasticError("Test", details=None)
        assert str(error) == "Test"
        assert error.details is None


class TestConfigurationErrors:
    """Test configuration-related exceptions."""

    def test_configuration_error(self):
        """Test ConfigurationError inherits from FetchtasticError."""
        error = ConfigurationError("Invalid configuration")
        assert isinstance(error, FetchtasticError)
        assert str(error) == "Invalid configuration"

    def test_config_file_error(self):
        """Test ConfigFileError inherits from ConfigurationError."""
        error = ConfigFileError("Cannot read config file", details="File not found")
        assert isinstance(error, ConfigurationError)
        assert isinstance(error, FetchtasticError)
        assert str(error) == "Cannot read config file - File not found"

    def test_config_validation_error(self):
        """Test ConfigValidationError inherits from ConfigurationError."""
        error = ConfigValidationError("Validation failed")
        assert isinstance(error, ConfigurationError)
        assert isinstance(error, FetchtasticError)

    def test_configuration_error_with_details(self):
        """Test configuration errors with details."""
        error = ConfigurationError(
            "Missing required key", details="DOWNLOAD_DIR is required"
        )
        assert error.message == "Missing required key"
        assert error.details == "DOWNLOAD_DIR is required"
        assert "DOWNLOAD_DIR is required" in str(error)


class TestDownloadErrors:
    """Test download-related exceptions."""

    def test_download_error_basic(self):
        """Test basic DownloadError."""
        error = DownloadError("Download failed")
        assert isinstance(error, FetchtasticError)
        assert error.url is None
        assert error.retry_count == 0
        assert error.is_retryable is False

    def test_download_error_with_url(self):
        """Test DownloadError with URL."""
        error = DownloadError(
            "Download failed",
            url="https://example.com/file.zip",
            retry_count=3,
            is_retryable=True,
        )
        assert error.url == "https://example.com/file.zip"
        assert error.retry_count == 3
        assert error.is_retryable is True

    def test_download_error_with_details(self):
        """Test DownloadError with details."""
        error = DownloadError(
            "Download failed",
            url="https://example.com/file.zip",
            details="Connection reset by peer",
        )
        assert error.details == "Connection reset by peer"
        assert "Connection reset by peer" in str(error)

    def test_network_error(self):
        """Test NetworkError inherits from DownloadError."""
        error = NetworkError("Connection timeout", url="https://example.com")
        assert isinstance(error, DownloadError)
        assert isinstance(error, FetchtasticError)
        assert error.url == "https://example.com"

    def test_http_error_basic(self):
        """Test basic HTTPError."""
        error = HTTPError("HTTP error occurred")
        assert isinstance(error, DownloadError)
        assert error.status_code is None

    def test_http_error_with_status(self):
        """Test HTTPError with status code."""
        error = HTTPError(
            "Not found",
            status_code=404,
            url="https://example.com/missing",
            is_retryable=False,
        )
        assert error.status_code == 404
        assert error.url == "https://example.com/missing"
        assert error.is_retryable is False

    def test_rate_limit_error_default(self):
        """Test RateLimitError with default message."""
        error = RateLimitError()
        assert isinstance(error, HTTPError)
        assert isinstance(error, DownloadError)
        assert error.status_code == 403
        assert error.reset_time is None
        assert error.remaining == 0
        assert error.is_retryable is True
        assert "rate limit exceeded" in str(error).lower()

    def test_rate_limit_error_with_details(self):
        """Test RateLimitError with reset time and remaining."""
        error = RateLimitError(
            "Rate limit exceeded",
            reset_time=1234567890,
            remaining=0,
            url="https://api.github.com/repos",
        )
        assert error.reset_time == 1234567890
        assert error.remaining == 0
        assert error.url == "https://api.github.com/repos"
        # Reset time is now formatted as human-readable date
        assert "2009-02-13" in str(error)
        assert "Remaining: 0" in str(error)

    def test_rate_limit_error_with_remaining(self):
        """Test RateLimitError with non-zero remaining."""
        error = RateLimitError(remaining=10, reset_time=1234567890)
        assert error.remaining == 10
        assert "Remaining: 10" in str(error)


class TestFileSystemErrors:
    """Test file system-related exceptions."""

    def test_file_system_error_basic(self):
        """Test basic FileSystemError."""
        error = FileSystemError("File operation failed")
        assert isinstance(error, FetchtasticError)
        assert error.path is None

    def test_file_system_error_with_path(self):
        """Test FileSystemError with path."""
        error = FileSystemError(
            "Cannot write file", path="/path/to/file.txt", details="Disk full"
        )
        assert error.path == "/path/to/file.txt"
        assert error.details == "Disk full"

    def test_file_permission_error(self):
        """Test FilePermissionError inherits from FileSystemError."""
        error = FilePermissionError("Permission denied", details="/protected/file")
        assert isinstance(error, FileSystemError)
        assert isinstance(error, FetchtasticError)
        assert error.path is None

    def test_disk_space_error(self):
        """Test DiskSpaceError inherits from FileSystemError."""
        error = DiskSpaceError("Insufficient disk space", path="/downloads")
        assert isinstance(error, FileSystemError)
        assert isinstance(error, FetchtasticError)

    def test_path_validation_error(self):
        """Test PathValidationError inherits from FileSystemError."""
        error = PathValidationError(
            "Invalid path", path="../../../etc/passwd", details="Path traversal attempt"
        )
        assert isinstance(error, FileSystemError)
        assert error.path == "../../../etc/passwd"
        assert error.details is not None
        assert "Path traversal" in error.details


class TestValidationErrors:
    """Test validation-related exceptions."""

    def test_validation_error_basic(self):
        """Test basic ValidationError."""
        error = ValidationError("Validation failed")
        assert isinstance(error, FetchtasticError)
        assert error.field is None
        assert error.value is None

    def test_validation_error_with_field(self):
        """Test ValidationError with field name."""
        error = ValidationError("Invalid value", field="version")
        assert error.field == "version"

    def test_validation_error_with_value(self):
        """Test ValidationError with invalid value."""
        error = ValidationError("Invalid version", field="version", value="invalid")
        assert error.field == "version"
        assert error.value == "invalid"

    def test_validation_error_with_details(self):
        """Test ValidationError with details."""
        error = ValidationError(
            "Pattern invalid",
            field="pattern",
            value="[invalid",
            details="Unclosed bracket",
        )
        assert error.field == "pattern"
        assert error.value == "[invalid"
        assert error.details == "Unclosed bracket"

    def test_version_error(self):
        """Test VersionError inherits from ValidationError."""
        error = VersionError("Invalid version format", field="version", value="1.x.3")
        assert isinstance(error, ValidationError)
        assert isinstance(error, FetchtasticError)
        assert error.field == "version"
        assert error.value == "1.x.3"

    def test_pattern_error(self):
        """Test PatternError inherits from ValidationError."""
        error = PatternError("Pattern compilation failed", field="regex", value="[")
        assert isinstance(error, ValidationError)
        assert isinstance(error, FetchtasticError)


class TestArchiveErrors:
    """Test archive-related exceptions."""

    def test_archive_error_basic(self):
        """Test basic ArchiveError."""
        error = ArchiveError("Archive operation failed")
        assert isinstance(error, FetchtasticError)
        assert error.archive_path is None

    def test_archive_error_with_path(self):
        """Test ArchiveError with archive path."""
        error = ArchiveError(
            "Cannot extract archive",
            archive_path="/downloads/file.zip",
            details="Corrupted header",
        )
        assert error.archive_path == "/downloads/file.zip"
        assert error.details == "Corrupted header"

    def test_corrupted_archive_error(self):
        """Test CorruptedArchiveError inherits from ArchiveError."""
        error = CorruptedArchiveError(
            "Archive is corrupted", archive_path="/downloads/bad.zip"
        )
        assert isinstance(error, ArchiveError)
        assert isinstance(error, FetchtasticError)
        assert error.archive_path == "/downloads/bad.zip"

    def test_extraction_error(self):
        """Test ExtractionError inherits from ArchiveError."""
        error = ExtractionError(
            "Extraction failed",
            archive_path="/downloads/file.zip",
            details="Insufficient permissions",
        )
        assert isinstance(error, ArchiveError)
        assert isinstance(error, FetchtasticError)
        assert error.details == "Insufficient permissions"


class TestAPIErrors:
    """Test API-related exceptions."""

    def test_api_error_basic(self):
        """Test basic APIError."""
        error = APIError("API request failed")
        assert isinstance(error, FetchtasticError)
        assert error.endpoint is None
        assert error.status_code is None

    def test_api_error_with_endpoint(self):
        """Test APIError with endpoint."""
        error = APIError("API error", endpoint="/api/v1/users")
        assert error.endpoint == "/api/v1/users"

    def test_api_error_with_status_code(self):
        """Test APIError with status code."""
        error = APIError(
            "API error",
            endpoint="/api/v1/users",
            status_code=500,
            details="Internal server error",
        )
        assert error.status_code == 500
        assert error.details == "Internal server error"

    def test_authentication_error(self):
        """Test AuthenticationError inherits from APIError."""
        error = AuthenticationError(
            "Authentication failed", endpoint="/api/auth", status_code=401
        )
        assert isinstance(error, APIError)
        assert isinstance(error, FetchtasticError)
        assert error.status_code == 401

    def test_resource_not_found_error(self):
        """Test ResourceNotFoundError inherits from APIError."""
        error = ResourceNotFoundError(
            "Resource not found", endpoint="/api/resource/123", status_code=404
        )
        assert isinstance(error, APIError)
        assert isinstance(error, FetchtasticError)
        assert error.endpoint == "/api/resource/123"
        assert error.status_code == 404


class TestSetupErrors:
    """Test setup/installation-related exceptions."""

    def test_setup_error(self):
        """Test SetupError inherits from FetchtasticError."""
        error = SetupError("Setup failed")
        assert isinstance(error, FetchtasticError)
        assert str(error) == "Setup failed"

    def test_setup_error_with_details(self):
        """Test SetupError with details."""
        error = SetupError("Installation failed", details="Missing dependencies")
        assert error.details == "Missing dependencies"
        assert "Missing dependencies" in str(error)

    def test_cron_error(self):
        """Test CronError inherits from SetupError."""
        error = CronError("Cron job setup failed", details="crontab not found")
        assert isinstance(error, SetupError)
        assert isinstance(error, FetchtasticError)
        assert "crontab not found" in str(error)

    def test_shortcut_error(self):
        """Test ShortcutError inherits from SetupError."""
        error = ShortcutError(
            "Shortcut creation failed", details="Windows modules not available"
        )
        assert isinstance(error, SetupError)
        assert isinstance(error, FetchtasticError)

    def test_migration_error(self):
        """Test MigrationError inherits from SetupError."""
        error = MigrationError(
            "Migration failed", details="Cannot backup configuration"
        )
        assert isinstance(error, SetupError)
        assert isinstance(error, FetchtasticError)


class TestExceptionHierarchy:
    """Test exception hierarchy and relationships."""

    def test_all_inherit_from_fetchtastic_error(self):
        """Test that all custom exceptions inherit from FetchtasticError."""
        exceptions_to_test = [
            ConfigurationError("test"),
            ConfigFileError("test"),
            ConfigValidationError("test"),
            DownloadError("test"),
            NetworkError("test"),
            HTTPError("test"),
            RateLimitError(),
            FileSystemError("test"),
            FilePermissionError("test"),
            DiskSpaceError("test"),
            PathValidationError("test"),
            ValidationError("test"),
            VersionError("test"),
            PatternError("test"),
            ArchiveError("test"),
            CorruptedArchiveError("test"),
            ExtractionError("test"),
            APIError("test"),
            AuthenticationError("test"),
            ResourceNotFoundError("test"),
            SetupError("test"),
            CronError("test"),
            ShortcutError("test"),
            MigrationError("test"),
        ]

        for exc in exceptions_to_test:
            assert isinstance(exc, FetchtasticError)
            assert isinstance(exc, Exception)

    def test_exception_catching_by_category(self):
        """Test that exceptions can be caught by their parent category."""
        # Configuration errors
        try:
            raise ConfigFileError("test")
        except ConfigurationError:
            pass  # Should catch ConfigFileError

        # Download errors
        try:
            raise NetworkError("test")
        except DownloadError:
            pass  # Should catch NetworkError

        try:
            raise HTTPError("test")
        except DownloadError:
            pass  # Should catch HTTPError

        # File system errors
        try:
            raise FilePermissionError("test")
        except FileSystemError:
            pass  # Should catch FilePermissionError

        # Validation errors
        try:
            raise VersionError("test")
        except ValidationError:
            pass  # Should catch VersionError

        # Archive errors
        try:
            raise CorruptedArchiveError("test")
        except ArchiveError:
            pass  # Should catch CorruptedArchiveError

        # API errors
        try:
            raise AuthenticationError("test")
        except APIError:
            pass  # Should catch AuthenticationError

        # Setup errors
        try:
            raise CronError("test")
        except SetupError:
            pass  # Should catch CronError

    def test_catch_all_fetchtastic_errors(self):
        """Test that all exceptions can be caught with FetchtasticError."""
        test_exceptions = [
            ConfigFileError("test"),
            NetworkError("test"),
            HTTPError("test"),
            FilePermissionError("test"),
            VersionError("test"),
            CorruptedArchiveError("test"),
            AuthenticationError("test"),
            CronError("test"),
        ]

        for exc in test_exceptions:
            try:
                raise exc
            except FetchtasticError as e:
                assert isinstance(e, FetchtasticError)


class TestExceptionEdgeCases:
    """Test edge cases and special scenarios."""

    def test_long_messages(self):
        """Test exceptions with very long messages."""
        long_message = "x" * 10000
        error = FetchtasticError(long_message)
        assert len(str(error)) == 10000

    def test_unicode_messages(self):
        """Test exceptions with unicode characters."""
        error = FetchtasticError("错误信息 🚫", details="Подробности ⚠️")
        assert "错误信息" in str(error)
        assert "Подробности" in str(error)

    def test_special_characters_in_paths(self):
        """Test file system errors with special characters in paths."""
        error = FileSystemError("Error", path="/path/with spaces/and-special_chars")
        assert error.path == "/path/with spaces/and-special_chars"

    def test_empty_string_fields(self):
        """Test validation error with empty string fields."""
        error = ValidationError("test", field="", value="")
        assert error.field == ""
        assert error.value == ""

    def test_none_vs_empty_string(self):
        """Test that None and empty string are handled differently."""
        error1 = FetchtasticError("test", details=None)
        error2 = FetchtasticError("test", details="")

        assert str(error1) == "test"
        # Empty string details still results in just "test" due to conditional check
        assert str(error2) == "test"

    def test_exception_repr(self):
        """Test string representation of exceptions."""
        error = DownloadError("Download failed", url="https://example.com")
        repr_str = repr(error)
        assert "DownloadError" in repr_str

    def test_multiple_inheritance_levels(self):
        """Test exceptions with multiple inheritance levels."""
        error = RateLimitError()
        assert isinstance(error, HTTPError)
        assert isinstance(error, DownloadError)
        assert isinstance(error, FetchtasticError)
        assert isinstance(error, Exception)


class TestExceptionUsageScenarios:
    """Test realistic usage scenarios for exceptions."""

    def test_download_retry_scenario(self):
        """Test exception used in download retry scenario."""
        error = NetworkError(
            "Connection timeout",
            url="https://example.com/file.zip",
            retry_count=3,
            is_retryable=True,
            details="Timed out after 30 seconds",
        )

        assert error.is_retryable
        assert error.retry_count == 3
        assert "Connection timeout" in str(error)

    def test_rate_limit_scenario(self):
        """Test rate limit exception with reset information."""
        import time

        current_time = int(time.time())
        reset_time = current_time + 3600  # 1 hour from now

        error = RateLimitError(
            reset_time=reset_time, remaining=0, url="https://api.github.com"
        )

        assert error.status_code == 403
        assert error.is_retryable
        assert error.reset_time == reset_time

    def test_configuration_validation_scenario(self):
        """Test configuration validation error scenario."""
        error = ConfigValidationError(
            "Invalid configuration value",
            details="VERSIONS_TO_KEEP must be a positive integer",
        )

        assert isinstance(error, ConfigurationError)
        assert "VERSIONS_TO_KEEP" in str(error)

    def test_archive_extraction_scenario(self):
        """Test archive extraction failure scenario."""
        error = ExtractionError(
            "Failed to extract files",
            archive_path="/downloads/firmware.zip",
            details="Archive is password protected",
        )

        assert error.archive_path == "/downloads/firmware.zip"
        assert "password protected" in str(error)

    def test_api_authentication_scenario(self):
        """Test API authentication failure scenario."""
        error = AuthenticationError(
            "Invalid GitHub token",
            endpoint="/api/repos/meshtastic/firmware",
            status_code=401,
            details="Token has expired",
        )

        assert error.status_code == 401
        assert "Token has expired" in str(error)

    def test_disk_full_scenario(self):
        """Test disk full error scenario."""
        error = DiskSpaceError(
            "Cannot save file",
            path="/downloads/large-file.zip",
            details="Insufficient disk space: 0 bytes available",
        )

        assert error.path == "/downloads/large-file.zip"
        assert "0 bytes available" in str(error)
