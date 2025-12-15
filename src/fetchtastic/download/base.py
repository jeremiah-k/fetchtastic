"""
Base Downloader Implementation

This module provides the base implementation of the Downloader interface
that can be extended by specific artifact downloaders.
"""

import fnmatch
import os
from abc import ABC
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from requests.exceptions import RequestException
except ImportError:
    RequestException = Exception

from fetchtastic import utils
from fetchtastic.log_utils import logger
from fetchtastic.utils import matches_selected_patterns

from .cache import CacheManager
from .files import FileOperations, _sanitize_path_component
from .interfaces import Asset, Downloader, DownloadResult, Pathish
from .version import VersionManager


class BaseDownloader(Downloader, ABC):
    """
    Base implementation of the Downloader interface.

    This class provides common functionality that can be used by all
    specific downloaders (Android, Firmware, etc.).
    """

    def __init__(
        self, config: Dict[str, Any], cache_manager: Optional[CacheManager] = None
    ):
        """
        Initialize the base downloader.

        Args:
            config: Configuration dictionary
            cache_manager: Optional cache manager instance
        """
        self.config = config
        self.version_manager = VersionManager()
        self.cache_manager = cache_manager or CacheManager()
        self.file_operations = FileOperations()

        # Initialize common configuration with normalized paths
        self.download_dir = str(Path(self._get_download_dir()))
        self.versions_to_keep = self._get_versions_to_keep()

    def _get_download_dir(self) -> str:
        """Get the download directory from configuration."""
        return self.config.get("DOWNLOAD_DIR", os.path.expanduser("~/meshtastic"))

    def _get_versions_to_keep(self) -> int:
        """Get the number of versions to keep from configuration."""
        return int(self.config.get("VERSIONS_TO_KEEP", 5))

    def download(self, url: str, target_path: Pathish) -> bool:
        """
        Download a file from a URL to a target path.

        Uses the existing download_file_with_retry utility for robustness.

        Args:
            url: The URL to download from
            target_path: Where to save the downloaded file

        Returns:
            bool: True if download succeeded, False otherwise
        """
        try:
            # Ensure target directory exists using pathlib
            target = Path(target_path)
            target.parent.mkdir(parents=True, exist_ok=True)

            # Use the existing robust download utility
            success = utils.download_file_with_retry(url, str(target))

            if success:
                logger.info(f"Successfully downloaded {target.name}")
            else:
                logger.error(f"Failed to download {url}")

            return success
        except (OSError, RequestException, ValueError) as e:
            logger.exception("Error downloading %s: %s", url, e)
            return False

    def verify(self, file_path: Pathish, expected_hash: Optional[str] = None) -> bool:
        """
        Verify the integrity of a downloaded file.

        Args:
            file_path: Path to the file to verify
            expected_hash: Optional expected hash for verification

        Returns:
            bool: True if verification succeeded, False otherwise
        """
        if expected_hash:
            return self.file_operations.verify_file_hash(str(file_path), expected_hash)
        return utils.verify_file_integrity(str(file_path))

    def extract(
        self,
        file_path: Pathish,
        patterns: List[str],
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Pathish]:
        """
        Extract files from an archive matching specific patterns.

        Args:
            file_path: Path to the archive file
            patterns: List of filename patterns to extract
            exclude_patterns: Optional list of filename patterns to skip

        Returns:
            List[Path]: List of paths to extracted files
        """
        # Get the directory where the archive is located using pathlib
        archive_path = Path(file_path)
        archive_dir = str(archive_path.parent)
        extracted = self.file_operations.extract_archive(
            str(archive_path), archive_dir, patterns, exclude_patterns or []
        )
        return [Path(p) for p in extracted]  # Convert back to Path objects

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old versions according to retention policy.

        Args:
            keep_limit: Maximum number of versions to keep
        """
        # This will be implemented by specific downloaders
        pass

    def get_version_manager(self) -> VersionManager:
        """
        Get the version manager for this downloader.

        Returns:
            VersionManager: The version manager instance
        """
        return self.version_manager

    def get_cache_manager(self) -> CacheManager:
        """
        Get the cache manager for this downloader.

        Returns:
            CacheManager: The cache manager instance
        """
        return self.cache_manager

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Get the target path for a release file.

        Args:
            release_tag: The release tag/version
            file_name: The filename of the asset

        Returns:
            str: Full path where the file should be saved
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        # Create version-specific directory
        version_dir = os.path.join(self.download_dir, safe_release)
        os.makedirs(version_dir, exist_ok=True)

        return os.path.join(version_dir, safe_name)

    def should_download_release(self, _release_tag: str, asset_name: str) -> bool:
        """
        Determine if a release should be downloaded based on selection patterns.

        Args:
            _release_tag: The release tag (unused, prefixed to silence linter)
            asset_name: The asset name to check

        Returns:
            bool: True if the release should be downloaded, False otherwise
        """
        # Get selection patterns from config
        selected_patterns = self._get_selected_patterns()
        exclude_patterns = self._get_exclude_patterns()

        # Check if asset matches selected patterns
        if selected_patterns and not self._matches_selected_patterns(
            asset_name, selected_patterns
        ):
            logger.debug(f"Skipping {asset_name} - doesn't match selected patterns")
            return False

        # Check if asset matches exclude patterns
        if exclude_patterns and self._matches_exclude_patterns(
            asset_name, exclude_patterns
        ):
            logger.debug(f"Skipping {asset_name} - matches exclude patterns")
            return False

        return True

    def _get_selected_patterns(self) -> List[str]:
        """Get the selected patterns from configuration."""
        patterns = self.config.get("SELECTED_PATTERNS")

        # Backward compatibility with existing config keys
        if not patterns:
            patterns = self.config.get("SELECTED_FIRMWARE_ASSETS")
        if not patterns:
            patterns = self.config.get("SELECTED_PRERELEASE_ASSETS")
        if not patterns:
            patterns = self.config.get("SELECTED_APK_ASSETS")

        patterns = patterns or []
        return patterns if isinstance(patterns, list) else [patterns]

    def _get_exclude_patterns(self) -> List[str]:
        """Get the exclude patterns from configuration."""
        patterns = self.config.get("EXCLUDE_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _matches_selected_patterns(self, filename: str, patterns: List[str]) -> bool:
        """
        Check if a filename matches any of the selected patterns.

        Args:
            filename: The filename to check
            patterns: List of patterns to match against

        Returns:
            bool: True if filename matches any pattern, False otherwise
        """
        return matches_selected_patterns(filename, patterns)

    def _matches_exclude_patterns(self, filename: str, patterns: List[str]) -> bool:
        """
        Check if a filename matches any of the exclude patterns.

        Args:
            filename: The filename to check
            patterns: List of patterns to match against

        Returns:
            bool: True if filename matches any exclude pattern, False otherwise
        """
        if not patterns:
            return False  # No exclude patterns means don't exclude anything

        filename_lower = filename.lower()
        return any(
            fnmatch.fnmatch(filename_lower, pattern.lower()) for pattern in patterns
        )

    def _sanitize_required(self, component: str, label: str) -> str:
        """Sanitize a required component or raise ValueError with a helpful message."""
        safe = _sanitize_path_component(component)
        if safe is None:
            raise ValueError(
                f"Unsafe {label} provided; aborting to avoid path traversal"
            )
        return safe

    def create_download_result(
        self,
        success: bool,
        release_tag: str,
        file_path: str,
        error_message: Optional[str] = None,
        *,
        extracted_files: Optional[List[Pathish]] = None,
        download_url: Optional[str] = None,
        file_size: Optional[int] = None,
        file_type: Optional[str] = None,
        is_retryable: bool = False,
        error_type: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        http_status_code: Optional[int] = None,
        was_skipped: bool = False,
    ) -> DownloadResult:
        """
        Create a DownloadResult object.

        Args:
            success: Whether the download succeeded
            release_tag: The release tag
            file_path: Path to the downloaded file
            error_message: Optional error message
            download_url: URL used to fetch the asset
            file_size: Size of the asset in bytes
            file_type: Asset type hint (android/firmware/repository)
            is_retryable: Whether this failure can be retried
            error_type: Optional error classification

        Returns:
            DownloadResult: The download result object
        """
        return DownloadResult(
            success=success,
            release_tag=release_tag,
            file_path=Path(file_path),
            error_message=error_message,
            extracted_files=extracted_files,  # type: ignore[arg-type]
            download_url=download_url,
            file_size=file_size,
            file_type=file_type,
            is_retryable=is_retryable,
            error_type=error_type,
            error_details=error_details,
            http_status_code=http_status_code,
            was_skipped=was_skipped,
        )

    def get_existing_file_path(self, release_tag: str, file_name: str) -> Optional[str]:
        """
        Get the path to an existing file for a release.

        Args:
            release_tag: The release tag
            file_name: The filename

        Returns:
            Optional[str]: Path to existing file, or None if it doesn't exist
        """
        target_path = self.get_target_path_for_release(release_tag, file_name)
        return target_path if os.path.exists(target_path) else None

    def cleanup_file(self, file_path: str) -> bool:
        """Remove a file from disk via the shared file operations helper."""
        return self.file_operations.cleanup_file(file_path)

    def _is_zip_intact(self, file_path: str) -> bool:
        """Quick integrity check for zip archives."""
        import zipfile

        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                return zf.testzip() is None
        except (IOError, zipfile.BadZipFile):
            return False

    def is_asset_complete(self, release_tag: str, asset: Asset) -> bool:
        """
        Determine if an asset is already downloaded and valid.

        Checks existence, size match, hash/verify, and zip integrity.
        """
        target_path = self.get_target_path_for_release(release_tag, asset.name)
        if not os.path.exists(target_path):
            return False

        # Size check
        if asset.size and self.file_operations.get_file_size(target_path) != asset.size:
            return False

        # Hash/verify (legacy: verify/write sidecar and validate)
        if not self.verify(target_path):
            return False

        # Zip integrity check
        if target_path.lower().endswith(".zip") and not self._is_zip_intact(
            target_path
        ):
            return False

        return True

    def needs_download(
        self, release_tag: str, file_name: str, expected_size: int
    ) -> bool:
        """
        Determine if a file needs to be downloaded.

        Args:
            release_tag: The release tag
            file_name: The filename
            expected_size: Expected file size

        Returns:
            bool: True if file needs download, False if it exists and is valid
        """
        existing_path = self.get_existing_file_path(release_tag, file_name)
        if not existing_path:
            return True

        # Check file size
        actual_size = self.file_operations.get_file_size(existing_path)
        if actual_size is None or actual_size != expected_size:
            logger.debug(f"File {file_name} size mismatch - will redownload")
            return True

        return False
