"""
Core Interfaces for Fetchtastic Download Subsystem

This module defines the fundamental interfaces and data structures that form
the foundation of the modular download architecture.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union

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

    name: Optional[str] = None
    """Human-readable release name/title"""

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

    error_details: Optional[dict[str, Any]] = None
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
            true if the task is ready to execute, false otherwise.
        """

    @abstractmethod
    def execute(self) -> DownloadResult:
        """
        Perform the download operation and report its outcome.

        Returns:
            DownloadResult: Object containing the success flag, optional release tag and target file path, extracted file paths when applicable, error message and details, HTTP status code if available, retry count and timestamp, retryability flag, skip indicator, and other related metadata.
        """

    @abstractmethod
    def get_target_path(self) -> Pathish:
        """
        Return the filesystem path where the download should be saved.

        Returns:
            The intended filesystem path for the downloaded artifact.
        """

    @abstractmethod
    def cleanup(self) -> None:
        """
        Release temporary files and other resources used by the task.

        Intended to be invoked after a download operation to ensure resources are released even if the download failed.
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
        Retrieve downloadable assets for the given release.

        Parameters:
            release (Release): Release whose assets should be returned.

        Returns:
            List[Asset]: Assets belonging to the provided release.
        """

    @abstractmethod
    def get_download_url(self, asset: Asset) -> str:
        """
        Return the direct download URL for the given asset.

        Parameters:
            asset (Asset): The asset whose direct download URL is requested.

        Returns:
            str: Direct download URL for the asset.
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
        Download the resource at url and save it to target_path.

        Returns:
            True if the resource was successfully downloaded and written to target_path, False otherwise.
        """

    @abstractmethod
    def verify(self, file_path: Pathish, expected_hash: Optional[str] = None) -> bool:
        """
        Validate that the file at file_path matches an expected integrity check.

        If an expected_hash is provided, the file's digest is compared against that hex-encoded value; otherwise the downloader's default verification is applied.

        Parameters:
            file_path (Pathish): Path to the file to verify.
            expected_hash (Optional[str]): Optional hex-encoded digest to check against.

        Returns:
            `true` if the file's contents match the expected hash or pass verification, `false` otherwise.
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
        Determine whether include and exclude extraction patterns are well-formed and safe for archive extraction.

        Parameters:
            patterns (List[str]): File-glob patterns to include when extracting archive contents.
            exclude_patterns (List[str]): File-glob patterns to exclude from extraction.

        Returns:
            `true` if all patterns are well-formed and considered safe for extraction, `false` otherwise.
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
        Determine whether the archive at `file_path` needs extraction by verifying that files matching `patterns` (and not matching `exclude_patterns`) are present and complete in `extract_dir`.

        Parameters:
            file_path (str): Path to the archive file intended for extraction.
            extract_dir (str): Directory where extracted files are expected to be found; patterns are matched relative to this directory.
            patterns (List[str]): Glob or filename patterns that identify required files within the extraction directory.
            exclude_patterns (List[str]): Glob or filename patterns to ignore when checking for required files.

        Returns:
            `true` if extraction should be performed because required files are missing or incomplete, `false` otherwise.
        """

    @abstractmethod
    def cleanup_old_versions(
        self,
        keep_limit: int,
        cached_releases: Optional[List["Release"]] = None,
        keep_last_beta: bool = False,
    ) -> None:
        """
        Prune cached or stored releases, retaining only the most recent releases defined by keep_limit.

        Parameters:
            keep_limit (int): Number of most recent releases to keep; older releases beyond this count will be removed.
            cached_releases (Optional[List[Release]]): Optional list of releases to use instead of querying the source; when provided, order should match the newest-first ordering returned by get_releases.
            keep_last_beta (bool): If True and supported, also retain the most recent beta release in addition to the kept releases.
        """

    @abstractmethod
    def get_version_manager(self) -> "VersionManager":
        """
        Get the VersionManager associated with this downloader.

        Returns:
            VersionManager: The associated VersionManager instance.
        """

    @abstractmethod
    def get_cache_manager(self) -> "CacheManager":
        """
        Get the cache manager associated with this downloader.

        Returns:
            CacheManager: The cache manager used by this downloader.
        """
