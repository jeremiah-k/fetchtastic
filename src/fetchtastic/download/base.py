"""
Base Downloader Implementation

This module provides the base implementation of the Downloader interface
that can be extended by specific artifact downloaders.
"""

import fnmatch
import os
import zipfile
from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from requests.exceptions import RequestException  # type: ignore[import-untyped]

from fetchtastic import utils
from fetchtastic.log_utils import logger
from fetchtastic.utils import matches_selected_patterns

from .cache import CacheManager
from .files import FileOperations, _sanitize_path_component, strip_unwanted_chars
from .interfaces import Asset, Downloader, DownloadResult, Pathish
from .version import VersionManager

if TYPE_CHECKING:
    from .interfaces import Release


_MISSING_HISTORY_MANAGER_MSG = (
    "{cls} does not have a release_history_manager and cannot check revoked status"
)


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
        self.download_dir = str(Path(self.get_download_dir()))
        self.versions_to_keep = self._get_versions_to_keep()

    def get_download_dir(self) -> str:
        """
        Return the configured download directory path.

        If the configuration does not provide "DOWNLOAD_DIR", defaults to the user's home "meshtastic" directory.

        Returns:
            download_dir (str): The resolved download directory path (e.g. '~/meshtastic' when not configured).
        """
        return cast(
            str, self.config.get("DOWNLOAD_DIR", os.path.expanduser("~/meshtastic"))
        )

    def _get_versions_to_keep(self) -> int:
        """
        Number of release versions to retain.

        Reads the `VERSIONS_TO_KEEP` configuration value and returns it as an integer; defaults to 5 when unset.

        Returns:
            int: Number of versions to keep.
        """
        return int(self.config.get("VERSIONS_TO_KEEP", 5))

    def download(self, url: str, target_path: Pathish) -> bool:
        """
        Download a file to the specified target path.

        Ensures the target's parent directory exists before attempting the download.

        Returns:
            `True` if the file was downloaded successfully, `False` otherwise.
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

    def cleanup_old_versions(
        self,
        keep_limit: int,
        cached_releases: Optional[List["Release"]] = None,
        keep_last_beta: bool = False,
    ) -> None:
        """
        Remove older downloaded versions so that at most `keep_limit` version directories remain.

        Parameters:
            keep_limit (int): Maximum number of version entries to retain; older versions beyond this limit should be removed.
            cached_releases (Optional[List[Release]]): Optional list of releases to consult instead of performing fresh lookups.
            keep_last_beta (bool): If true and supported by the downloader, preserve the most recent beta release in addition to the retained releases.
        """
        # This will be implemented by specific downloaders
        pass

    def get_version_manager(self) -> VersionManager:
        """
        Get the VersionManager associated with this downloader.

        Returns:
            VersionManager: The VersionManager instance associated with this downloader.
        """
        return self.version_manager

    def get_cache_manager(self) -> CacheManager:
        """
        Retrieve the cache manager used by this downloader.

        Returns:
            CacheManager: The cache manager instance.
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
            asset_name (str): Name of the asset (filename) to evaluate.

        Returns:
            True if the asset should be downloaded, False otherwise.
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
        Create a DownloadResult that encapsulates the outcome and metadata of a release asset download attempt.

        Parameters:
            success (bool): Whether the download succeeded.
            release_tag (str): Release identifier associated with the asset.
            file_path (str): File system path to the asset; converted to a Path on the result.
            error_message (Optional[str]): Human-readable error message, if any.
            extracted_files (Optional[List[Pathish]]): Paths of files extracted from an archive, if applicable.
            download_url (Optional[str]): URL used to fetch the asset.
            file_size (Optional[int]): Size of the asset in bytes, when known.
            file_type (Optional[str]): Asset type hint (for example, "android", "firmware", or "repository").
            is_retryable (bool): Whether a failed download is safe to retry.
            error_type (Optional[str]): Machine-readable classification of the error.
            error_details (Optional[Dict[str, Any]]): Additional structured information about the error.
            http_status_code (Optional[int]): HTTP status code returned by the request, if applicable.
            was_skipped (bool): Whether the asset was intentionally skipped (not attempted).

        Returns:
            DownloadResult: Object containing the result fields and normalized Path for the file.
        """
        return DownloadResult(
            success=success,
            release_tag=release_tag,
            file_path=Path(file_path),
            error_message=error_message,
            extracted_files=extracted_files,
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
        Get the filesystem path of an existing asset for the given release.

        Parameters:
            release_tag (str): Release tag used to locate the version directory.
            file_name (str): Asset filename within the release directory.

        Returns:
            Optional[str]: Path to the existing file as a string if present, `None` otherwise.
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

    def _write_release_notes(
        self,
        *,
        release_dir: str,
        release_tag: str,
        body: Optional[str],
        base_dir: str,
    ) -> Optional[str]:
        """
        Write sanitized release notes to a markdown file within the given release directory if not already present.

        Validates and sanitizes the provided release_tag; skips writing if the tag is unsafe. Ensures the target notes path is located inside base_dir (prevents path escape), creates release_dir as needed, strips unwanted characters from body, and writes the file atomically via the downloader's cache_manager. If the notes file already exists, returns its path without modifying it.

        Parameters:
                release_dir (str): Directory where the release notes file should be placed.
                release_tag (str): Tag used to derive a safe filename component; will be sanitized.
                body (Optional[str]): Release notes content; nothing is written if empty or whitespace after sanitization.
                base_dir (str): Base download directory used to verify the notes path does not escape the allowed location.

        Returns:
                notes_path (Optional[str]): Path to the release notes file if written or already present, `None` otherwise.
        """
        if not body:
            return None

        safe_tag = _sanitize_path_component(release_tag)
        if safe_tag is None:
            logger.warning("Skipping release notes for unsafe tag: %s", release_tag)
            return None

        notes_path = os.path.join(release_dir, f"release_notes-{safe_tag}.md")

        real_base = os.path.realpath(base_dir)
        try:
            real_release_dir = os.path.realpath(release_dir)
            release_dir_common = os.path.commonpath([real_base, real_release_dir])
        except ValueError:
            release_dir_common = None

        if release_dir_common != real_base:
            logger.warning(
                "Skipping write of release notes for %s: release directory path escapes download base",
                release_tag,
            )
            return None

        if os.path.lexists(notes_path):
            if os.path.islink(notes_path):
                logger.warning(
                    "Refusing to use existing symlink for release notes: %s",
                    notes_path,
                )
                return None
            return notes_path

        os.makedirs(release_dir, exist_ok=True)

        try:
            real_notes = os.path.realpath(notes_path)
            notes_common = os.path.commonpath([real_base, real_notes])
        except ValueError:
            notes_common = None

        if notes_common != real_base:
            logger.warning(
                "Skipping write of release notes for %s: path escapes download base",
                release_tag,
            )
            return None

        notes_content = strip_unwanted_chars(body)
        if not notes_content.strip():
            return None

        if self.cache_manager.atomic_write_text(notes_path, notes_content):
            logger.debug("Saved release notes to %s", notes_path)
            return notes_path

        logger.warning("Could not atomically write release notes to %s", notes_path)
        return None

    def _is_zip_intact(self, file_path: str) -> bool:
        """
        Perform a quick integrity check of a ZIP archive.

        Parameters:
            file_path (str): Path to the ZIP file to inspect.

        Returns:
            bool: `True` if the archive contains no corrupt members, `False` otherwise.
        """
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                return zf.testzip() is None
        except (IOError, zipfile.BadZipFile):
            return False

    def is_asset_complete(self, release_tag: str, asset: Asset) -> bool:
        """
        Check whether the downloaded asset for a release exists and is valid.

        Performs an existence check, verifies the file size when provided by the asset, verifies integrity using stored hash records, and performs a ZIP integrity check for .zip files.

        Returns:
            True if the file exists and passes all applicable checks, False otherwise.
        """
        target_path = self.get_target_path_for_release(release_tag, asset.name)
        if not os.path.exists(target_path):
            return False

        # Size check
        if asset.size and self.file_operations.get_file_size(target_path) != asset.size:
            return False

        # Hash/verify (uses cached hash records)
        if not self.verify(target_path):
            return False

        # Zip integrity check
        if target_path.lower().endswith(".zip") and not self._is_zip_intact(
            target_path
        ):
            return False

        return True

    def is_release_revoked(self, release: "Release") -> bool:
        """
        Determine whether the given release is recorded as revoked in release history.

        This method is only available in downloaders that have a release_history_manager
        (e.g., FirmwareReleaseDownloader, MeshtasticAndroidAppDownloader).

        Parameters:
            release (Release): The release to check.

        Returns:
            bool: `True` if the release is revoked, `False` otherwise.

        Raises:
            AttributeError: If the downloader does not have a release_history_manager.
        """
        if not hasattr(self, "release_history_manager"):
            raise AttributeError(
                _MISSING_HISTORY_MANAGER_MSG.format(cls=self.__class__.__name__)
            )
        return self.release_history_manager.is_release_revoked(release)  # type: ignore[attr-defined]

    def needs_download(
        self, release_tag: str, file_name: str, expected_size: int
    ) -> bool:
        """
        Determine whether the specified release asset must be downloaded.

        Parameters:
            release_tag (str): Release tag used to locate the existing file.
            file_name (str): Name of the asset file.
            expected_size (int): Expected file size in bytes; used to detect incomplete or mismatched files.

        Returns:
            True if the file should be downloaded, False otherwise.
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
