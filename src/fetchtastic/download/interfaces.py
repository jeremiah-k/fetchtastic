"""
Core Interfaces for Fetchtastic Download Subsystem

This module defines the fundamental interfaces and data structures that form
the foundation of the modular download architecture.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

Pathish = Union[str, Path]

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

    assets: List["Asset"] = field(default_factory=list)
    """List of downloadable assets for this release"""


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

    file_path: Optional[Pathish] = None
    """Path to the downloaded file (if successful)"""

    error_message: Optional[str] = None
    """Error message (if failed)"""

    extracted_files: Optional[List[Pathish]] = None
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
        Determine whether the download task is ready to be executed.

        Returns:
            bool: `True` if the task is valid and ready to execute, `False` otherwise.
        """

    @abstractmethod
    def execute(self) -> DownloadResult:
        """
        Perform the download operation and return a structured result describing its outcome.

        Returns:
            DownloadResult: Result containing success flag, file and extraction details, error information, and retry metadata.
        """

    @abstractmethod
    def get_target_path(self) -> Pathish:
        """
        Provide the intended filesystem path where the downloaded artifact should be saved.

        Returns:
            Pathish: The intended file path for the download.
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
        Retrieve available releases from the source, newest first.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return; None for all.

        Returns:
            List[Release]: Releases ordered newest first.
        """

    @abstractmethod
    def get_assets(self, release: Release) -> List[Asset]:
        """
        Retrieve the downloadable assets associated with a release.

        Returns:
            A list of assets belonging to the provided release.
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
    def download(self, url: str, target_path: Pathish) -> bool:
        """
        Download the resource at the given URL and save it to the specified path.

        Parameters:
            url (str): URL of the resource to download.
            target_path (Pathish): Destination filesystem path for the downloaded file.

        Returns:
            True if the download and write completed successfully, False otherwise.
        """

    @abstractmethod
    def verify(self, file_path: Pathish, expected_hash: Optional[str] = None) -> bool:
        """
        Verify that a downloaded file's integrity matches expectations.

        Parameters:
            file_path (Pathish): Path to the downloaded file to verify.
            expected_hash (Optional[str]): Optional expected hash (hex string) to validate the file against.

        Returns:
            bool: `true` if the file passes verification, `false` otherwise.
        """

    @abstractmethod
    def extract(
        self,
        file_path: Pathish,
        patterns: List[str],
        exclude_patterns: Optional[List[str]],
    ) -> List[Pathish]:
        """
        Extracts files from an archive that match the given include patterns and do not match the optional exclude patterns.

        Parameters:
            file_path (Pathish): Path to the archive file to extract.
            patterns (List[str]): Filename patterns to include; matched against archive member names.
            exclude_patterns (Optional[List[str]]): Filename patterns to exclude from extraction.

        Returns:
            List[Pathish]: Paths to the files extracted from the archive.
        """

    @abstractmethod
    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate that extraction include and exclude patterns are syntactically valid and safe for use during archive extraction.

        Parameters:
            patterns (List[str]): File-matching patterns to include during extraction.
            exclude_patterns (List[str]): File-matching patterns to exclude during extraction.

        Returns:
            bool: `True` if all patterns are well-formed and considered safe, `False` otherwise.
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
        Determine whether extraction should run by checking for presence of files that match the given extraction patterns under the target extraction directory, excluding any exclude patterns.

        Parameters:
            file_path (str): Path to the archive file that would be extracted.
            extract_dir (str): Directory where extraction output should reside.
            patterns (List[str]): Filename or glob patterns that specify which files to extract.
            exclude_patterns (List[str]): Filename or glob patterns to exclude from extraction.

        Returns:
            `true` if extraction is needed (expected files are missing or incomplete), `false` otherwise.
        """

    @abstractmethod
    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Remove older cached or stored versions, retaining only the most recent keep_limit versions.

        Parameters:
            keep_limit (int): Number of most recent versions to retain; versions older than this will be removed.
        """

    @abstractmethod
    def get_version_manager(self) -> "VersionManager":
        """
        Return the downloader's associated VersionManager.

        Returns:
            VersionManager: The associated version manager instance.
        """

    @abstractmethod
    def get_cache_manager(self) -> "CacheManager":
        """
        Retrieve the cache manager associated with this downloader.

        Returns:
            CacheManager: The cache manager instance used by this downloader.
        """
