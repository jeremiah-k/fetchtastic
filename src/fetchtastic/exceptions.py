"""
Custom exceptions for the Fetchtastic application.

This module defines domain-specific exceptions that provide better error
categorization and more informative error messages for users and developers.
"""


class FetchtasticError(Exception):
    """
    Base exception for all Fetchtastic errors.

    All custom exceptions in Fetchtastic should inherit from this class
    to allow for easy catching of all application-specific errors.
    """

    def __init__(self, message: str, details: str | None = None) -> None:
        """
        Initialize the exception.

        Args:
            message: The primary error message.
            details: Optional additional context about the error.
        """
        self.message = message
        self.details = details
        super().__init__(message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} - {self.details}"
        return self.message


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigurationError(FetchtasticError):
    """
    Exception raised when configuration is invalid or missing.

    This includes:
    - Missing required configuration keys
    - Invalid configuration values
    - Configuration file parsing errors
    """

    pass


class ConfigFileError(ConfigurationError):
    """Exception raised when configuration file cannot be read or written."""

    pass


class ConfigValidationError(ConfigurationError):
    """Exception raised when configuration validation fails."""

    pass


# =============================================================================
# Download Errors
# =============================================================================


class DownloadError(FetchtasticError):
    """
    Base exception for download-related errors.

    Attributes:
        url: The URL that was being downloaded when the error occurred.
        retry_count: Number of retry attempts made before failure.
        is_retryable: Whether the error could be retried.
    """

    def __init__(
        self,
        message: str,
        url: str | None = None,
        retry_count: int = 0,
        is_retryable: bool = False,
        details: str | None = None,
    ) -> None:
        """
        Initialize the download exception.

        Args:
            message: The primary error message.
            url: The URL that was being downloaded.
            retry_count: Number of retry attempts made.
            is_retryable: Whether this error could be retried.
            details: Optional additional context.
        """
        super().__init__(message, details)
        self.url = url
        self.retry_count = retry_count
        self.is_retryable = is_retryable


class NetworkError(DownloadError):
    """
    Exception raised for network-related download failures.

    This includes:
    - Connection timeouts
    - DNS resolution failures
    - Connection refused errors
    - SSL/TLS errors
    """

    pass


class HTTPError(DownloadError):
    """
    Exception raised for HTTP-related download failures.

    Attributes:
        status_code: The HTTP status code returned by the server.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
        retry_count: int = 0,
        is_retryable: bool = False,
        details: str | None = None,
    ) -> None:
        """
        Initialize the HTTP exception.

        Args:
            message: The primary error message.
            status_code: The HTTP status code.
            url: The URL that was being downloaded.
            retry_count: Number of retry attempts made.
            is_retryable: Whether this error could be retried.
            details: Optional additional context.
        """
        super().__init__(message, url, retry_count, is_retryable, details)
        self.status_code = status_code


class RateLimitError(HTTPError):
    """
    Exception raised when GitHub API rate limit is exceeded.

    Attributes:
        reset_time: When the rate limit will reset (Unix timestamp).
        remaining: Number of requests remaining.
    """

    def __init__(
        self,
        message: str = "GitHub API rate limit exceeded",
        reset_time: int | None = None,
        remaining: int = 0,
        url: str | None = None,
    ) -> None:
        """
        Initialize the rate limit exception.

        Args:
            message: The primary error message.
            reset_time: When the rate limit will reset (Unix timestamp).
            remaining: Number of requests remaining.
            url: The URL that was being accessed.
        """
        super().__init__(
            message,
            status_code=403,
            url=url,
            is_retryable=True,
            details=f"Resets at: {reset_time}, Remaining: {remaining}",
        )
        self.reset_time = reset_time
        self.remaining = remaining


# =============================================================================
# File System Errors
# =============================================================================


class FileSystemError(FetchtasticError):
    """
    Exception raised for file system-related errors.

    This includes:
    - Permission denied errors
    - Disk full errors
    - File not found errors
    - Path validation errors
    """

    def __init__(
        self,
        message: str,
        path: str | None = None,
        details: str | None = None,
    ) -> None:
        """
        Initialize the file system exception.

        Args:
            message: The primary error message.
            path: The file path that caused the error.
            details: Optional additional context.
        """
        super().__init__(message, details)
        self.path = path


class PermissionError(FileSystemError):
    """Exception raised when file system permissions prevent an operation."""

    pass


class DiskSpaceError(FileSystemError):
    """Exception raised when there is insufficient disk space."""

    pass


class PathValidationError(FileSystemError):
    """Exception raised when a path fails security validation."""

    pass


# =============================================================================
# Validation Errors
# =============================================================================


class ValidationError(FetchtasticError):
    """
    Exception raised when validation fails.

    This includes:
    - Invalid version strings
    - Invalid asset names
    - Invalid extraction patterns
    - Invalid user input
    """

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: str | None = None,
        details: str | None = None,
    ) -> None:
        """
        Initialize the validation exception.

        Args:
            message: The primary error message.
            field: The name of the field that failed validation.
            value: The value that failed validation.
            details: Optional additional context.
        """
        super().__init__(message, details)
        self.field = field
        self.value = value


class VersionError(ValidationError):
    """Exception raised when version parsing or comparison fails."""

    pass


class PatternError(ValidationError):
    """Exception raised when pattern compilation or matching fails."""

    pass


# =============================================================================
# Archive Errors
# =============================================================================


class ArchiveError(FetchtasticError):
    """
    Exception raised for archive-related errors.

    This includes:
    - Corrupted ZIP files
    - Extraction failures
    - Invalid archive members
    """

    def __init__(
        self,
        message: str,
        archive_path: str | None = None,
        details: str | None = None,
    ) -> None:
        """
        Initialize the archive exception.

        Args:
            message: The primary error message.
            archive_path: Path to the problematic archive.
            details: Optional additional context.
        """
        super().__init__(message, details)
        self.archive_path = archive_path


class CorruptedArchiveError(ArchiveError):
    """Exception raised when an archive is corrupted or invalid."""

    pass


class ExtractionError(ArchiveError):
    """Exception raised when archive extraction fails."""

    pass


# =============================================================================
# API Errors
# =============================================================================


class APIError(FetchtasticError):
    """
    Exception raised for API-related errors.

    This includes:
    - Invalid API responses
    - Authentication failures
    - Resource not found errors
    """

    def __init__(
        self,
        message: str,
        endpoint: str | None = None,
        status_code: int | None = None,
        details: str | None = None,
    ) -> None:
        """
        Initialize the API exception.

        Args:
            message: The primary error message.
            endpoint: The API endpoint that was accessed.
            status_code: The HTTP status code returned.
            details: Optional additional context.
        """
        super().__init__(message, details)
        self.endpoint = endpoint
        self.status_code = status_code


class AuthenticationError(APIError):
    """Exception raised when API authentication fails."""

    pass


class ResourceNotFoundError(APIError):
    """Exception raised when an API resource is not found."""

    pass


# =============================================================================
# Setup/Installation Errors
# =============================================================================


class SetupError(FetchtasticError):
    """
    Exception raised for setup and installation errors.

    This includes:
    - Cron job setup failures
    - Windows shortcut creation failures
    - Migration failures
    """

    pass


class CronError(SetupError):
    """Exception raised when cron job setup or removal fails."""

    pass


class ShortcutError(SetupError):
    """Exception raised when Windows shortcut creation fails."""

    pass


class MigrationError(SetupError):
    """Exception raised when installation migration fails."""

    pass
