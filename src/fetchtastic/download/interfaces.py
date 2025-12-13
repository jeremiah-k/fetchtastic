"""
Core Interfaces for Fetchtastic Download Subsystem

This module defines the fundamental interfaces and data structures that form
the foundation of the modular download architecture.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .cache import CacheManager
    from .version import VersionManager


@dataclass
class Release:
    """Represents a software release from a repository."""

    tag_name: str
    """The release tag/version identifier (e.g., 'v2.7.8')"""

    prerelease: bool = False
    """Whether this is a prerelease version"""

    published_at: Optional[str] = None
    """ISO 8601 timestamp when the release was published"""

    body: Optional[str] = None
    """Release notes/markdown content"""

    assets: Optional[List["Asset"]] = None
    """List of downloadable assets for this release"""

    def __post_init__(self):
        """Ensure assets list is initialized."""
        if self.assets is None:
            self.assets = []


@dataclass
class Asset:
    """Represents a downloadable asset from a release."""

    name: str
    """The filename of the asset"""

    download_url: str
    """Direct URL to download the asset"""

    size: int
    """File size in bytes"""

    browser_download_url: Optional[str] = None
    """Alternative download URL (may be same as download_url)"""

    content_type: Optional[str] = None
    """MIME type of the asset"""


@dataclass
class DownloadResult:
    """Result of a download operation."""

    success: bool
    """Whether the download operation succeeded"""

    release_tag: Optional[str] = None
    """The release tag that was downloaded"""

    file_path: Optional[Path] = None
    """Path to the downloaded file (if successful)"""

    error_message: Optional[str] = None
    """Error message (if failed)"""

    extracted_files: Optional[List[Path]] = None
    """List of files extracted from archives"""

    # Enhanced retry and failure metadata for P2.1
    download_url: Optional[str] = None
    """URL of the downloaded asset (for retry and reporting)"""

    file_size: Optional[int] = None
    """Size of the file in bytes (for retry and reporting)"""

    file_type: Optional[str] = None
    """Type of the file (APK, firmware, repository, etc.)"""

    retry_count: int = 0
    """Number of retry attempts made"""

    retry_timestamp: Optional[str] = None
    """Timestamp of the last retry attempt"""

    error_type: Optional[str] = None
    """Type/category of error (network, permission, validation, etc.)"""

    error_details: Optional[dict] = None
    """Detailed error information for debugging"""

    http_status_code: Optional[int] = None
    """HTTP status code if download failed due to HTTP error"""

    is_retryable: bool = False
    """Whether this failure is retryable"""

    was_skipped: bool = False
    """Whether this result represents a skip (already complete) rather than a new download."""


class DownloadTask(ABC):
    """
    Abstract base class for download tasks.

    A DownloadTask represents a single download operation with a complete
    lifecycle: validation, execution, and cleanup.
    """

    @abstractmethod
    def validate(self) -> bool:
        """
        Validate that the task can be executed.

        Returns:
            bool: True if the task is valid and can be executed, False otherwise
        """

    @abstractmethod
    def execute(self) -> DownloadResult:
        """
        Execute the download task.

        Returns:
            DownloadResult: The result of the download operation
        """

    @abstractmethod
    def get_target_path(self) -> Path:
        """
        Get the target path where the download will be saved.

        Returns:
            Path: The target file path
        """

    @abstractmethod
    def cleanup(self) -> None:
        """
        Clean up any temporary files or resources.

        This method should be called after a download operation to clean up
        any temporary files, even if the download failed.
        """


class DownloadSource(ABC):
    """
    Abstract base class for download sources.

    A DownloadSource provides access to releases and assets from various
    repositories or sources (GitHub, static repos, etc.).
    """

    @abstractmethod
    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Get available releases from the source.

        Args:
            limit: Maximum number of releases to return (None for all)

        Returns:
            List[Release]: List of available releases, sorted newest first
        """

    @abstractmethod
    def get_assets(self, release: Release) -> List[Asset]:
        """
        Get downloadable assets for a specific release.

        Args:
            release: The release to get assets for

        Returns:
            List[Asset]: List of downloadable assets for the release
        """

    @abstractmethod
    def get_download_url(self, asset: Asset) -> str:
        """
        Get the download URL for a specific asset.

        Args:
            asset: The asset to get download URL for

        Returns:
            str: Direct download URL for the asset
        """


class Downloader(ABC):
    """
    Abstract base class for artifact-specific downloaders.

    A Downloader handles the actual download, verification, and extraction
    of specific artifact types (APKs, firmware, etc.).
    """

    @abstractmethod
    def download(self, url: str, target_path: Path) -> bool:
        """
        Download a file from a URL to a target path.

        Args:
            url: The URL to download from
            target_path: Where to save the downloaded file

        Returns:
            bool: True if download succeeded, False otherwise
        """

    @abstractmethod
    def verify(self, file_path: Path, expected_hash: Optional[str] = None) -> bool:
        """
        Verify the integrity of a downloaded file.

        Args:
            file_path: Path to the file to verify
            expected_hash: Optional expected hash for verification

        Returns:
            bool: True if verification succeeded, False otherwise
        """

    @abstractmethod
    def extract(
        self,
        file_path: Path,
        patterns: List[str],
        exclude_patterns: Optional[List[str]],
    ) -> List[Path]:
        """
        Extract files from an archive matching specific patterns.

        Args:
            file_path: Path to the archive file
            patterns: List of filename patterns to extract
            exclude_patterns: List of filename patterns to exclude during extraction

        Returns:
            List[Path]: List of paths to extracted files
        """

    @abstractmethod
    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate extraction patterns to ensure they are safe and well-formed.

        Args:
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if patterns are valid, False otherwise
        """

    @abstractmethod
    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Check if extraction is needed by examining existing files.

        Args:
            file_path: Path to the archive file
            extract_dir: Directory where files would be extracted
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if extraction is needed, False if files already exist
        """

    @abstractmethod
    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old versions according to retention policy.

        Args:
            keep_limit: Maximum number of versions to keep
        """

    @abstractmethod
    def get_version_manager(self) -> "VersionManager":
        """
        Get the version manager for this downloader.

        Returns:
            VersionManager: The version manager instance
        """

    @abstractmethod
    def get_cache_manager(self) -> "CacheManager":
        """
        Get the cache manager for this downloader.

        Returns:
            CacheManager: The cache manager instance
        """
