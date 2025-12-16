"""
Base Downloader Implementation

This module provides the base implementation of the Downloader interface
that can be extended by specific artifact downloaders.
"""

import fnmatch
import os
from abc import ABC
from pathlib import Path
from typing import Any, Dict, List, Optional

from requests.exceptions import RequestException

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
        Initialize BaseDownloader and its common helpers.

        Parameters:
            config (Dict[str, Any]): Configuration containing downloader settings. Recognized keys include
                "DOWNLOAD_DIR" (defaults to "~/meshtastic") and "VERSIONS_TO_KEEP" (defaults to 5).
            cache_manager (Optional[CacheManager]): Cache manager to use; a new CacheManager is created if omitted.

        Notes:
            Creates a VersionManager and FileOperations instance, stores a CacheManager, and normalizes
            download_dir (string path) and versions_to_keep from the provided configuration.
        """
        self.config = config
        self.version_manager = VersionManager()
        if cache_manager is None:
            cache_manager = CacheManager()
        self.cache_manager: CacheManager = cache_manager
        self.file_operations = FileOperations()

        # Initialize common configuration with normalized paths
        self.download_dir = str(Path(self._get_download_dir()))
        self.versions_to_keep = self._get_versions_to_keep()

    def _get_download_dir(self) -> str:
        """
        Determine the directory used for downloads from configuration.

        Returns:
            download_dir (str): Configured download directory path. If not set, returns the user's home 'meshtastic' directory (e.g. '~/meshtastic').
        """
        return self.config.get("DOWNLOAD_DIR", os.path.expanduser("~/meshtastic"))

    def _get_versions_to_keep(self) -> int:
        """
        Return the number of release versions to retain.

        Reads "VERSIONS_TO_KEEP" from the downloader configuration and returns its value cast to int; defaults to 5 if the setting is not present.

        Returns:
            int: Number of versions to keep (defaults to 5).
        """
        return int(self.config.get("VERSIONS_TO_KEEP", 5))

    def download(self, url: str, target_path: Pathish) -> bool:
        """
        Download a file from the given URL into the specified target path.

        Returns:
            bool: True if the file was downloaded successfully, False otherwise.
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

        If `expected_hash` is provided, verifies the file's hash against it; otherwise performs a general integrity check.

        Parameters:
            file_path (Pathish): Path to the file to verify.
            expected_hash (Optional[str]): Expected hash to validate against; if omitted, a broader integrity check is used.

        Returns:
            bool: `True` if the file passes verification, `False` otherwise.
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
        Extracts files from the given archive that match any of the provided include patterns and do not match the optional exclude patterns.

        Parameters:
            file_path (PathLike | str): Path to the archive file.
            patterns (List[str]): Filename include patterns (e.g., glob or fnmatch) used to select files to extract.
            exclude_patterns (Optional[List[str]]): Optional filename patterns to exclude from extraction.

        Returns:
            List[pathlib.Path]: Paths to the extracted files.
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
        Decide whether an asset should be downloaded based on configured selection and exclusion patterns.

        The asset is eligible only if it matches at least one configured selected pattern (when any are defined)
        and does not match any configured exclude pattern.

        Parameters:
            asset_name (str): The name of the asset to evaluate.

        Returns:
            bool: `true` if the asset should be downloaded, `false` otherwise.
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
        """
        Retrieve asset selection patterns from configuration, falling back to legacy keys if necessary.

        Checks "SELECTED_PATTERNS" and, if not present, attempts "SELECTED_FIRMWARE_ASSETS", "SELECTED_PRERELEASE_ASSETS", and "SELECTED_APK_ASSETS". Normalizes a single string into a one-element list.

        Returns:
            List[str]: Selection patterns from configuration, or an empty list if none are configured.
        """
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
        """
        Retrieve exclude filename patterns from the configuration.

        Returns:
            patterns (List[str]): A list of exclude pattern strings. If the config value is a single string it is wrapped into a one-element list; missing value yields an empty list.
        """
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
        Check whether the filename matches any exclude pattern, using case-insensitive shell-style wildcard matching.

        Parameters:
            filename: The filename to test.
            patterns: Iterable of shell-style patterns (e.g., '*.zip'); matching is case-insensitive.

        Returns:
            True if the filename matches any exclude pattern, False otherwise.
        """
        if not patterns:
            return False  # No exclude patterns means don't exclude anything

        filename_lower = filename.lower()
        return any(
            fnmatch.fnmatch(filename_lower, pattern.lower()) for pattern in patterns
        )

    def _sanitize_required(self, component: str, label: str) -> str:
        """
        Ensure a path component is safe for use and return the sanitized value.

        Parameters:
            component (str): The path component to sanitize.
            label (str): Human-readable label used in the error message if sanitization fails.

        Returns:
            sanitized (str): The sanitized path component.

        Raises:
            ValueError: If the component cannot be safely sanitized (to prevent path traversal).
        """
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
        Builds a standardized DownloadResult representing a download attempt for a release asset.

        Parameters:
            success (bool): Whether the download succeeded.
            release_tag (str): Release identifier associated with the asset.
            file_path (str): Path to the downloaded file; converted to a Path on the result.
            error_message (Optional[str]): Human-readable error message, if any.
            extracted_files (Optional[List[Pathish]]): Files extracted from an archive, if applicable.
            download_url (Optional[str]): URL used to fetch the asset.
            file_size (Optional[int]): Size of the asset in bytes, when known.
            file_type (Optional[str]): Asset type hint (e.g., "android", "firmware", "repository").
            is_retryable (bool): Whether a failed download can be retried.
            error_type (Optional[str]): Machine-readable classification of the error.
            error_details (Optional[Dict[str, Any]]): Additional structured error information.
            http_status_code (Optional[int]): HTTP status code returned by the request, if applicable.
            was_skipped (bool): Whether the asset was intentionally skipped (not attempted).

        Returns:
            DownloadResult: An object encapsulating the outcome and metadata of the download attempt.
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
        Return the filesystem path for an asset of the given release if the file exists.

        Parameters:
            release_tag (str): Release tag used to locate the version directory.
            file_name (str): Asset filename within the release directory.

        Returns:
            Optional[str]: Path to the existing file as a string, or `None` if the file does not exist.
        """
        target_path = self.get_target_path_for_release(release_tag, file_name)
        return target_path if os.path.exists(target_path) else None

    def cleanup_file(self, file_path: str) -> bool:
        """
        Delete the file at the given path if present.

        Parameters:
            file_path (str): Path to the file to remove.

        Returns:
            bool: `True` if the file was removed, `False` otherwise.
        """
        return self.file_operations.cleanup_file(file_path)

    def _is_zip_intact(self, file_path: str) -> bool:
        """
        Perform a quick integrity check of a ZIP archive.

        Parameters:
            file_path (str): Path to the ZIP file to inspect.

        Returns:
            bool: `True` if the archive contains no corrupt members, `False` otherwise.
        """
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
        Determine whether a release asset file should be downloaded.

        Parameters:
            release_tag (str): Release tag used to locate the existing file.
            file_name (str): Name of the asset file.
            expected_size (int): Expected file size in bytes.

        Returns:
            `true` if the file should be downloaded, `false` otherwise.
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
