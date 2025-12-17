"""
Firmware Release Downloader

This module implements the specific downloader for Meshtastic firmware releases.
"""

import fnmatch
import json
import os
import re
import shutil
import zipfile
from typing import Any, Dict, List, Optional

import requests

from fetchtastic.constants import (
    EXECUTABLE_PERMISSIONS,
    FIRMWARE_DIR_PREFIX,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    RELEASES_CACHE_EXPIRY_HOURS,
)
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    download_file_with_retry,
    make_github_api_request,
    matches_extract_patterns,
    matches_selected_patterns,
    verify_file_integrity,
)

from .base import BaseDownloader
from .cache import CacheManager
from .interfaces import Asset, DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .version import VersionManager


class FirmwareReleaseDownloader(BaseDownloader):
    """
    Downloader for Meshtastic firmware releases.

    This class handles:
    - Fetching firmware releases from GitHub
    - Downloading firmware ZIP files
    - Extracting firmware files with pattern matching
    - Managing firmware-specific version tracking
    - Handling firmware prereleases
    - Cleaning up old firmware versions
    """

    def __init__(self, config: Dict[str, Any], cache_manager: "CacheManager"):
        """
        Initialize the firmware downloader with configuration and a cache manager.

        Parameters:
            config (Dict[str, Any]): Runtime configuration used to locate download directories, selected assets, and feature flags.
            cache_manager (CacheManager): Cache manager used for API responses and remote directory listings.
        """
        super().__init__(config)
        self.cache_manager = cache_manager
        self.firmware_releases_url = MESHTASTIC_FIRMWARE_RELEASES_URL
        self.latest_release_file = LATEST_FIRMWARE_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_FIRMWARE_PRERELEASE_JSON_FILE
        self.latest_release_path = self.cache_manager.get_cache_file_path(
            self.latest_release_file
        )

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Compute a legacy-preserving filesystem path for a firmware asset under the downloader's firmware directory.

        The function ensures the release and file name are sanitized and that the target version directory exists.

        Parameters:
            release_tag (str): Release tag used to create the version subdirectory.
            file_name (str): Name of the firmware asset file.

        Returns:
            target_path (str): Absolute path to where the firmware asset should be stored.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, "firmware", safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Fetch and parse firmware releases from GitHub into Release objects.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return; if omitted returns all available.

        Returns:
            List[Release]: Parsed releases (each with associated Asset entries); returns an empty list on error or if GitHub data is invalid.
        """
        try:
            params = {"per_page": 8}
            url_key = self.cache_manager.build_url_cache_key(
                self.firmware_releases_url, params
            )
            releases_data = self.cache_manager.read_releases_cache_entry(
                url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
            )

            if releases_data is None:
                response = make_github_api_request(
                    self.firmware_releases_url,
                    self.config.get("GITHUB_TOKEN"),
                    allow_env_token=True,
                    params=params,
                )
                releases_data = response.json() if hasattr(response, "json") else []
                if isinstance(releases_data, list):
                    logger.debug(
                        "Cached %d releases for %s (fetched from API)",
                        len(releases_data),
                        self.firmware_releases_url,
                    )
                self.cache_manager.write_releases_cache_entry(
                    url_key, releases_data if isinstance(releases_data, list) else []
                )

            if not releases_data or not isinstance(releases_data, list):
                logger.error("Invalid releases data received from GitHub API")
                return []

            releases = []
            for release_data in releases_data:
                # Filter out releases without assets
                if not release_data.get("assets"):
                    continue

                release = Release(
                    tag_name=release_data["tag_name"],
                    prerelease=release_data.get("prerelease", False),
                    published_at=release_data.get("published_at"),
                    body=release_data.get("body"),
                )

                # Add assets to the release
                for asset_data in release_data["assets"]:
                    asset = Asset(
                        name=asset_data["name"],
                        download_url=asset_data["browser_download_url"],
                        size=asset_data["size"],
                        browser_download_url=asset_data.get("browser_download_url"),
                        content_type=asset_data.get("content_type"),
                    )
                    release.assets.append(asset)

                releases.append(release)

                # Respect limit if specified
                if limit and len(releases) >= limit:
                    break

            return releases

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            logger.exception("Error fetching firmware releases: %s", exc)
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Retrieve the downloadable assets for a firmware release.

        Parameters:
            release (Release): The release object to inspect.

        Returns:
            List[Asset]: The release's assets, or an empty list if none are present.
        """
        return release.assets or []

    def get_download_url(self, asset: Asset) -> str:
        """
        Get the direct download URL for the given firmware asset.

        Returns:
            download_url (str): Direct download URL for the asset.
        """
        return asset.download_url

    def download_firmware(self, release: Release, asset: Asset) -> DownloadResult:
        """
        Download and verify a firmware asset for a given release and report the outcome.

        Parameters:
            release (Release): Release metadata containing the asset's release tag.
            asset (Asset): Asset metadata describing the firmware file (e.g., name, size, download_url).

        Returns:
            DownloadResult: Result object describing success or failure, including file_path, download_url,
            file_size, file_type and additional flags such as `was_skipped` or `is_retryable`.
        """
        target_path: Optional[str] = None
        try:
            # Get target path for the firmware ZIP
            target_path = self.get_target_path_for_release(release.tag_name, asset.name)

            # Check if we need to download
            if self.is_asset_complete(release.tag_name, asset):
                logger.debug(
                    "Firmware %s already exists and is complete",
                    asset.name,
                )
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type="firmware",
                    was_skipped=True,
                )

            # Download the firmware ZIP
            success = self.download(asset.download_url, target_path)

            if success:
                # Verify the download
                if self.verify(target_path):
                    logger.info("Downloaded and verified %s", asset.name)
                    return self.create_download_result(
                        success=True,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type="firmware",
                    )
                else:
                    logger.error(f"Verification failed for {asset.name}")
                    self.cleanup_file(target_path)
                    return self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        error_message="Verification failed",
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type="firmware",
                        is_retryable=True,
                        error_type="validation_error",
                    )
            else:
                logger.error(f"Download failed for {asset.name}")
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    error_message="download(...) returned False",
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type="firmware",
                    is_retryable=True,
                    error_type="network_error",
                )

        except (requests.RequestException, OSError, ValueError) as exc:
            logger.exception("Error downloading firmware %s: %s", asset.name, exc)
            safe_path = target_path or os.path.join(self.download_dir, "firmware")
            if isinstance(exc, requests.RequestException):
                error_type = "network_error"
                is_retryable = True
            elif isinstance(exc, OSError):
                error_type = "filesystem_error"
                is_retryable = False
            else:
                error_type = "validation_error"
                is_retryable = False
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=safe_path,
                error_message=str(exc),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type="firmware",
                is_retryable=is_retryable,
                error_type=error_type,
            )

    def is_release_complete(self, release: Release) -> bool:
        """
        Determine whether all selected assets for a release are present and valid on disk.

        Checks the release's assets filtered by configured include/exclude patterns and verifies each matched file exists in the release version directory, has an expected size when available, and (for ZIP files) passes zip integrity checks.

        Parameters:
            release (Release): Release whose assets will be checked.

        Returns:
            True if all selected assets exist and pass integrity and size checks, False otherwise.
        """
        version_dir = os.path.join(self.download_dir, "firmware", release.tag_name)
        if not os.path.isdir(version_dir):
            return False

        selected_patterns = self.config.get("SELECTED_FIRMWARE_ASSETS", [])
        exclude_patterns = self._get_exclude_patterns()

        expected_assets = []
        for asset in release.assets:
            if not asset.name:
                continue

            if selected_patterns and not matches_selected_patterns(
                asset.name, selected_patterns
            ):
                continue

            if self._matches_exclude_patterns(asset.name, exclude_patterns):
                continue

            expected_assets.append(asset)

        if not expected_assets:
            logger.debug(
                f"No assets match selected patterns for release in {version_dir}"
            )
            return False

        for asset in expected_assets:
            asset_path = os.path.join(version_dir, asset.name)
            if not os.path.exists(asset_path):
                logger.debug(
                    f"Missing asset {asset.name} in release directory {version_dir}"
                )
                return False

            if asset.name.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(asset_path, "r") as zf:
                        if zf.testzip() is not None:
                            logger.debug(f"Corrupted zip file detected: {asset_path}")
                            return False
                    try:
                        actual_size = os.path.getsize(asset_path)
                        expected_size = asset.size
                        if expected_size is not None:
                            if actual_size != expected_size:
                                logger.debug(
                                    f"File size mismatch for {asset_path}: expected {expected_size}, got {actual_size}"
                                )
                                return False
                    except (OSError, TypeError):
                        logger.debug(f"Error checking file size for {asset_path}")
                        return False
                except zipfile.BadZipFile:
                    logger.debug(f"Bad zip file detected: {asset_path}")
                    return False
                except (IOError, OSError):
                    logger.debug(f"Error checking zip file: {asset_path}")
                    return False
            else:
                try:
                    actual_size = os.path.getsize(asset_path)
                    expected_size = asset.size
                    if expected_size is not None and actual_size != expected_size:
                        logger.debug(
                            f"File size mismatch for {asset_path}: expected {expected_size}, got {actual_size}"
                        )
                        return False
                except (OSError, TypeError):
                    logger.debug(f"Error checking file size for {asset_path}")
                    return False

        return True

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Check whether the provided extraction include and exclude patterns are safe and well-formed.

        Parameters:
            patterns (List[str]): Filename glob patterns to include during extraction.
            exclude_patterns (List[str]): Filename glob patterns to exclude during extraction.

        Returns:
            bool: `True` if the patterns are valid, `False` otherwise.
        """
        return self.file_operations.validate_extraction_patterns(
            patterns, exclude_patterns
        )

    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Determine whether files should be extracted from the archive.

        Returns:
            True if extraction is needed, False otherwise.
        """
        return self.file_operations.check_extraction_needed(
            file_path, extract_dir, patterns, exclude_patterns
        )

    def extract_firmware(
        self,
        release: Release,
        asset: Asset,
        patterns: List[str],
        exclude_patterns: Optional[List[str]] = None,
    ) -> DownloadResult:
        """
        Extract files from a downloaded firmware ZIP according to include and exclude patterns.

        Parameters:
            release (Release): Release that owns the firmware asset.
            asset (Asset): The downloaded firmware asset (ZIP) to extract.
            patterns (List[str]): Glob patterns of files to extract from the archive.
            exclude_patterns (Optional[List[str]]): Glob patterns to exclude from extraction.

        Returns:
            DownloadResult: Result describing success or failure, extracted file list when successful,
            and error details when extraction did not occur or failed.
        """
        zip_path: str = ""
        try:
            exclude_patterns = exclude_patterns or []

            # Get the path to the downloaded ZIP file
            zip_path = self.get_target_path_for_release(release.tag_name, asset.name)
            if not os.path.exists(zip_path):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="ZIP file not found",
                    file_type="firmware",
                    error_type="validation_error",
                )

            # Get the directory where files will be extracted
            extract_dir = os.path.dirname(zip_path)

            # Legacy parity: extraction is a no-op success when all matching files
            # already exist with expected sizes (skip instead of treating as failure).
            if not self.file_operations.validate_extraction_patterns(
                patterns, exclude_patterns
            ):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="Invalid extraction patterns",
                    file_type="firmware",
                    error_type="validation_error",
                )

            if not self.file_operations.check_extraction_needed(
                zip_path, extract_dir, patterns, exclude_patterns
            ):
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    extracted_files=[],
                    file_type="firmware",
                    was_skipped=True,
                )

            extracted_files = self.file_operations.extract_archive(
                zip_path, extract_dir, patterns, exclude_patterns
            )
            if extracted_files:
                self.file_operations.generate_hash_for_extracted_files(extracted_files)

            if extracted_files:
                logger.info(f"Extracted {len(extracted_files)} files from {asset.name}")

                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    extracted_files=extracted_files,  # type: ignore[arg-type]
                    file_type="firmware",
                )
            else:
                logger.warning(
                    f"No files extracted from {asset.name} - no matches for patterns"
                )
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="No files matched extraction patterns",
                    file_type="firmware",
                    error_type="validation_error",
                    is_retryable=False,
                )

        except (zipfile.BadZipFile, OSError, ValueError) as e:
            logger.error(f"Error extracting firmware {asset.name}: {e}")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=zip_path or os.path.join(self.download_dir, "firmware"),
                error_message=str(e),
                file_type="firmware",
                error_type="extraction_error",
            )

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Remove firmware version directories older than the most recent `keep_limit` versions.

        Only directories under <download_dir>/firmware that match a semantic-version-like pattern
        (optional leading "v", e.g. "v1.2.3" or "2.3") are considered. Special directories
        "prerelease" and "repo-dls" are ignored. Matching directories are sorted by version
        and any beyond the newest `keep_limit` entries are removed.

        Parameters:
            keep_limit (int): Maximum number of most-recent version directories to retain;
                older matching directories will be deleted.
        """
        try:
            # Get all firmware version directories
            firmware_dir = os.path.join(self.download_dir, "firmware")
            if not os.path.exists(firmware_dir):
                return

            # Get all version directories (excluding special directories)
            version_dirs = []
            for item in os.listdir(firmware_dir):
                item_path = os.path.join(firmware_dir, item)
                if (
                    os.path.isdir(item_path)
                    and re.match(r"^(v)?\d+\.\d+(?:\.\d+)?", item)
                    and item not in ["prerelease", "repo-dls"]
                ):
                    version_dirs.append(item)

            # Sort versions and keep only the newest ones
            version_dirs.sort(
                reverse=True,
                key=lambda version: self.version_manager.get_release_tuple(version)
                or (),
            )

            # Remove old versions
            for old_version in version_dirs[keep_limit:]:
                old_dir = os.path.join(firmware_dir, old_version)
                try:
                    shutil.rmtree(old_dir)
                    logger.info(f"Removed old firmware version: {old_version}")
                except OSError as e:
                    logger.error(
                        f"Error removing old firmware version {old_version}: {e}"
                    )

        except OSError as e:
            logger.error(f"Error cleaning up old firmware versions: {e}")

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Get the latest firmware release tag from the tracking file.

        Returns:
            Optional[str]: Latest release tag, or None if not found
        """
        latest_file = self.latest_release_path
        if os.path.exists(latest_file):
            try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("latest_version")
            except (IOError, json.JSONDecodeError):
                pass
        return None

    def update_latest_release_tag(self, release_tag: str) -> bool:
        """
        Update the tracked latest firmware release tag stored in the downloader's tracking file.

        Parameters:
            release_tag: The release tag to record.

        Returns:
            `True` if the tracking file was written successfully, `False` otherwise.
        """
        latest_file = self.latest_release_path
        data = {
            "latest_version": release_tag,
            "file_type": "firmware",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(latest_file, data)

    def _get_current_iso_timestamp(self) -> str:
        """
        Get the current UTC timestamp in ISO 8601 format.

        Returns:
            iso_timestamp (str): ISO 8601 formatted UTC timestamp (UTC timezone).
        """
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _get_expiry_timestamp(self) -> str:
        """
        Produce an ISO 8601 UTC timestamp 24 hours from now.

        Returns:
            iso_timestamp (str): ISO 8601-formatted UTC timestamp representing the current time plus 24 hours.
        """
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    def _get_prerelease_base_dir(self) -> str:
        """
        Ensure and return the base directory for prerelease firmware downloads.

        Returns:
            str: Absolute path to the prerelease base directory under the downloader's download directory; the directory is created if it does not already exist.
        """
        prerelease_dir = os.path.join(self.download_dir, "firmware", "prerelease")
        os.makedirs(prerelease_dir, exist_ok=True)
        return prerelease_dir

    def _get_prerelease_patterns(self) -> List[str]:
        """
        Return the normalized list of prerelease asset selection patterns from the configuration.

        Returns:
            A list of patterns used to select prerelease assets. If the config value is a single non-list value, it is converted to a single-item list containing its string representation.
        """
        patterns = self.config.get("SELECTED_PRERELEASE_ASSETS") or []
        return patterns if isinstance(patterns, list) else [str(patterns)]

    def _matches_exclude_patterns(self, filename: str, patterns: List[str]) -> bool:
        """
        Determine whether a filename matches any of the provided glob exclude patterns (case-insensitive).

        Parameters:
            filename (str): The file name to test.
            patterns (List[str]): Iterable of glob patterns to check against; matching is case-insensitive.

        Returns:
            bool: `True` if `filename` matches any pattern, `False` otherwise.
        """
        filename_lower = filename.lower()
        return any(
            fnmatch.fnmatch(filename_lower, str(pattern).lower())
            for pattern in patterns or []
        )

    def _fetch_prerelease_directory_listing(
        self,
        prerelease_dir: str,
        *,
        force_refresh: bool,
    ) -> List[Dict[str, Any]]:
        """
        Fetches the repository listing for the given prerelease directory, optionally bypassing cache.

        Parameters:
            prerelease_dir (str): Repository path to the prerelease directory to list.
            force_refresh (bool): If true, bypass cached data and fetch fresh results from the remote.

        Returns:
            List[Dict[str, Any]]: A list of metadata dictionaries for entries (files/directories) in the specified directory.
        """
        contents = self.cache_manager.get_repo_contents(
            prerelease_dir,
            force_refresh=force_refresh,
            github_token=self.config.get("GITHUB_TOKEN"),
            allow_env_token=True,
        )
        logger.debug("Fetched %d items from repository", len(contents))
        return contents

    def _download_prerelease_assets(
        self,
        remote_dir: str,
        *,
        selected_patterns: List[str],
        exclude_patterns: List[str],
        force_refresh: bool,
    ) -> tuple[list[DownloadResult], list[DownloadResult], bool]:
        """
        Download prerelease assets from a remote prerelease directory into the local prerelease store, filtering by include and exclude patterns.

        Parameters:
            remote_dir (str): Remote directory name (repository prerelease path) used to locate and store assets under the local prerelease base directory.
            selected_patterns (List[str]): Patterns that assets must match to be downloaded; empty list means all files are eligible.
            exclude_patterns (List[str]): Case-insensitive glob patterns; any matching filename will be skipped even if it matches `selected_patterns`.
            force_refresh (bool): If True, re-download files even when a valid local copy exists.

        Returns:
            tuple[list[DownloadResult], list[DownloadResult], bool]: A 3-tuple containing:
                - successes: list of successful DownloadResult entries for files present or downloaded.
                - failures: list of failed DownloadResult entries for files that could not be downloaded or verified.
                - any_downloaded: True if at least one file was freshly downloaded during this call, False otherwise.
        """
        prerelease_base_dir = self._get_prerelease_base_dir()
        safe_dir = os.path.basename(str(remote_dir))
        if not safe_dir or safe_dir != remote_dir:
            logger.warning("Skipping unsafe prerelease directory name: %s", remote_dir)
            return [], [], False
        target_dir = os.path.join(prerelease_base_dir, safe_dir)
        os.makedirs(target_dir, exist_ok=True)

        device_manager = DeviceHardwareManager()

        contents = self._fetch_prerelease_directory_listing(
            remote_dir, force_refresh=force_refresh
        )
        file_items = [
            item
            for item in contents
            if isinstance(item, dict) and item.get("type") == "file"
        ]

        matching: list[Dict[str, Any]] = []
        for item in file_items:
            name = str(item.get("name") or "")
            if not name:
                continue
            if exclude_patterns and self._matches_exclude_patterns(
                name, exclude_patterns
            ):
                logger.debug(
                    "Skipping pre-release file %s (matched exclude pattern)", name
                )
                continue
            if selected_patterns and not matches_extract_patterns(
                name, selected_patterns, device_manager=device_manager
            ):
                continue
            matching.append(item)

        logger.debug("Found %d matching prerelease files", len(matching))

        successes: list[DownloadResult] = []
        failures: list[DownloadResult] = []
        any_downloaded = False

        for item in matching:
            name = str(item.get("name") or "")
            url = item.get("download_url") or item.get("browser_download_url")
            if not name or not url:
                continue

            target_path = os.path.join(target_dir, name)
            try:
                if not force_refresh and os.path.exists(target_path):
                    zip_ok = True
                    if name.lower().endswith(".zip"):
                        try:
                            with zipfile.ZipFile(target_path, "r") as zf:
                                zip_ok = zf.testzip() is None
                        except (zipfile.BadZipFile, IOError):
                            zip_ok = False

                    if zip_ok and verify_file_integrity(target_path):
                        logger.debug(
                            "Prerelease file already exists and is valid: %s", name
                        )
                        successes.append(
                            self.create_download_result(
                                success=True,
                                release_tag=remote_dir,
                                file_path=target_path,
                                download_url=str(url),
                                file_size=item.get("size"),
                                file_type="firmware_prerelease",
                                was_skipped=True,
                            )
                        )
                        continue

                ok = download_file_with_retry(str(url), target_path)
                if ok:
                    any_downloaded = True
                    if name.lower().endswith(".sh") and os.name != "nt":
                        try:
                            os.chmod(target_path, EXECUTABLE_PERMISSIONS)
                        except OSError:
                            pass
                    successes.append(
                        self.create_download_result(
                            success=True,
                            release_tag=remote_dir,
                            file_path=target_path,
                            download_url=str(url),
                            file_size=item.get("size"),
                            file_type="firmware_prerelease",
                        )
                    )
                else:
                    failures.append(
                        self.create_download_result(
                            success=False,
                            release_tag=remote_dir,
                            file_path=target_path,
                            error_message="download(...) returned False",
                            download_url=str(url),
                            file_size=item.get("size"),
                            file_type="firmware_prerelease",
                            is_retryable=True,
                            error_type="network_error",
                        )
                    )
            except (requests.RequestException, OSError, ValueError) as exc:
                if isinstance(exc, requests.RequestException):
                    error_type = "network_error"
                    is_retryable = True
                elif isinstance(exc, OSError):
                    error_type = "filesystem_error"
                    is_retryable = False
                else:
                    error_type = "validation_error"
                    is_retryable = False
                failures.append(
                    self.create_download_result(
                        success=False,
                        release_tag=remote_dir,
                        file_path=target_path,
                        error_message=str(exc),
                        download_url=str(url),
                        file_size=item.get("size"),
                        file_type="firmware_prerelease",
                        is_retryable=is_retryable,
                        error_type=error_type,
                    )
                )

        return successes, failures, any_downloaded

    def download_prerelease_assets(
        self,
        remote_dir: str,
        selected_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[DownloadResult], list[DownloadResult], bool]:
        """
        Public entry-point for downloading prerelease assets with optional pattern filters.

        Delegates to `_download_prerelease_assets` while exposing a stable signature
        for consumers like the CLI integration.
        """
        return self._download_prerelease_assets(
            remote_dir,
            selected_patterns=selected_patterns or [],
            exclude_patterns=exclude_patterns or [],
            force_refresh=force_refresh,
        )

    def download_repo_prerelease_firmware(
        self,
        latest_release_tag: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[DownloadResult], list[DownloadResult], Optional[str]]:
        """
        Check for and download firmware prerelease assets from the Meshtastic site (legacy repo-based workflow), update prerelease tracking, and return results.

        Parameters:
            latest_release_tag (str): The tag of the latest official release used to determine the expected prerelease base version.
            force_refresh (bool): When True, bypass cached directory listings and force remote refresh.

        Returns:
            tuple[list[DownloadResult], list[DownloadResult], Optional[str]]: A three-item tuple containing:
                - successes: list of DownloadResult for assets that were successfully downloaded or skipped,
                - failures: list of DownloadResult for assets that failed to download,
                - active_dir: the remote prerelease directory identifier used for the download, or None if no prerelease was found.
        """
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return [], [], None

        logger.info("Checking for pre-release firmware...")

        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()
        clean_latest_release = (
            version_manager.extract_clean_version(latest_release_tag)
            or latest_release_tag
        )
        expected_version = version_manager.calculate_expected_prerelease_version(
            clean_latest_release
        )
        if not expected_version:
            return [], [], None

        logger.debug("Expected prerelease version: %s", expected_version)

        active_dir, history_entries = (
            prerelease_manager.get_latest_active_prerelease_from_history(
                expected_version,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=True,
                force_refresh=force_refresh,
            )
        )
        if active_dir:
            logger.info("Using commit history for prerelease detection")
        else:
            # Fallback: scan repo root for prerelease directories
            try:
                dirs = self.cache_manager.get_repo_directories(
                    "",
                    force_refresh=force_refresh,
                    github_token=self.config.get("GITHUB_TOKEN"),
                    allow_env_token=True,
                )
                if not isinstance(dirs, list):
                    logger.debug(
                        "Expected list of repo directories from cache manager, got %s",
                        type(dirs),
                    )
                    dirs = []
                matches = prerelease_manager.scan_prerelease_directories(
                    [d for d in dirs if isinstance(d, str)], expected_version
                )
                if matches:
                    # Choose newest by tuple then string
                    matches.sort(
                        key=lambda ident: (
                            version_manager.get_release_tuple(ident) or (),
                            ident,
                        ),
                        reverse=True,
                    )
                    active_dir = f"{FIRMWARE_DIR_PREFIX}{matches[0]}"
            except (requests.RequestException, OSError, ValueError, TypeError) as exc:
                logger.debug(
                    "Fallback prerelease directory scan failed; skipping prerelease detection: %s",
                    exc,
                )
                active_dir = None

        if not active_dir:
            return [], [], None

        selected_patterns = self._get_prerelease_patterns()
        exclude_patterns = self._get_exclude_patterns()
        if selected_patterns:
            logger.debug(
                "Using your extraction patterns for pre-release selection: %s",
                " ".join(selected_patterns),
            )

        prerelease_base_dir = self._get_prerelease_base_dir()
        existing_dirs = [
            d
            for d in os.listdir(prerelease_base_dir)
            if os.path.isdir(os.path.join(prerelease_base_dir, d))
        ]

        successes, failures, any_downloaded = self._download_prerelease_assets(
            active_dir,
            selected_patterns=selected_patterns,
            exclude_patterns=exclude_patterns,
            force_refresh=force_refresh,
        )

        if not any_downloaded and active_dir in existing_dirs and not failures:
            logger.info("Found an existing pre-release, but no new files to download.")

        if any_downloaded or force_refresh:
            prerelease_manager.update_prerelease_tracking(
                latest_release_tag, active_dir, cache_manager=self.cache_manager
            )

        # Emit legacy-style history summary when available
        if history_entries:
            self.log_prerelease_summary(
                history_entries, clean_latest_release, expected_version
            )

        # Consolidate skipped messages
        skipped_count = sum(1 for result in successes if result.was_skipped)
        if skipped_count > 0:
            logger.debug(f"Skipped {skipped_count} existing pre-release files.")

        return successes, failures, active_dir

    def log_prerelease_summary(
        self,
        history_entries: List[Dict[str, Any]],
        clean_latest_release: str,
        expected_version: str,
    ):
        """
        Log counts and a formatted list of prerelease commits for a given version.

        Logs the number of prereleases created, deleted, and currently active since
        the provided baseline, then emits a formatted list of prerelease commit
        identifiers with their status (active, latest, or deleted). Identifiers are
        annotated with color/strike formatting for readability.

        Parameters:
            history_entries (List[Dict[str, Any]]): Sequence of prerelease history
                entries. Each entry is expected to include at least an "identifier"
                (commit id or tag) and a "status" key with value "active" or "deleted".
            clean_latest_release (str): Baseline release tag/version used to report
                the range of prereleases considered.
            expected_version (str): Base version string for which the prerelease
                commits are being reported.
        """
        prerelease_manager = PrereleaseHistoryManager()
        summary = prerelease_manager.summarize_prerelease_history(history_entries)
        logger.info(
            "Prereleases since %s: %d created, %d deleted, %d active",
            clean_latest_release,
            summary["created"],
            summary["deleted"],
            summary["active"],
        )

        active_commits = []
        deleted_commits = []
        for entry in history_entries:
            identifier = entry.get("identifier")
            if not identifier:
                continue
            if entry.get("status") == "active":
                active_commits.append(f"[green]{identifier}[/green]")
            else:
                deleted_commits.append(f"[red][strike]{identifier}[/strike][/red]")

        active_entries = [e for e in history_entries if e.get("status") == "active"]
        latest_active_identifier = (
            active_entries[-1].get("identifier") if active_entries else None
        )

        if history_entries:
            logger.info("Prerelease commits for %s:", expected_version)
            for entry in history_entries:
                identifier = entry.get("identifier")
                if not identifier:
                    continue

                is_latest_active = identifier == latest_active_identifier
                is_deleted = entry.get("status") == "deleted"

                if is_deleted:
                    label = f"[red][strike]{identifier}[/strike][/red]"
                    status = "deleted"
                elif is_latest_active:
                    label = f"[bold green]{identifier}[/bold green]"
                    status = "latest"
                else:
                    label = f"[green]{identifier}[/green]"
                    status = "active"

                logger.info(f"  - {label} ({status})")

    def handle_prereleases(
        self,
        releases: List[Release],
        recent_commits: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Release]:
        """
        Filter and return prerelease Release objects that match configured patterns, the expected base version derived from the latest stable release, and (optionally) recent commit hashes.

        This function:
        - Returns an empty list when prerelease checking is disabled via configuration.
        - Excludes prereleases whose tag appears to be a hash-suffixed version.
        - Sorts remaining prereleases by published date (newest first).
        - Applies include/exclude pattern filtering using configuration values "FIRMWARE_PRERELEASE_INCLUDE_PATTERNS" and "FIRMWARE_PRERELEASE_EXCLUDE_PATTERNS" when provided.
        - Derives an expected prerelease base version from the latest stable release and keeps only prereleases whose cleaned version starts with that base.
        - If recent_commits is provided, further prefers prereleases whose tag contains any 7-character commit SHA present in that list.

        Parameters:
            releases (List[Release]): All releases to consider.
            recent_commits (Optional[List[Dict[str, Any]]]): Optional list of recent commit objects; commit dicts are expected to contain a "sha" key used to derive 7-character hashes for tag matching.

        Returns:
            List[Release]: Filtered list of prerelease Release objects satisfying the configured and derived constraints.
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )

        if not check_prereleases:
            return []

        version_manager = VersionManager()

        # Filter prereleases (GitHub's prerelease flag can be noisy for hash-suffixed tags)
        prereleases = [
            r
            for r in releases
            if r.prerelease
            and not version_manager.HASH_SUFFIX_VERSION_RX.match(
                r.tag_name.lstrip("vV")
            )
        ]

        # Sort by published date (newest first)
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)

        # Apply pattern filtering if configured
        include_patterns = self.config.get("FIRMWARE_PRERELEASE_INCLUDE_PATTERNS", [])
        exclude_patterns = self.config.get("FIRMWARE_PRERELEASE_EXCLUDE_PATTERNS", [])

        if include_patterns or exclude_patterns:
            prerelease_tags = [r.tag_name for r in prereleases]
            filtered_tags = version_manager.filter_prereleases_by_pattern(
                prerelease_tags, include_patterns, exclude_patterns
            )
            prereleases = [r for r in prereleases if r.tag_name in filtered_tags]

        # Further restrict to prereleases that match expected base version
        expected_base = None
        latest_tuple = None
        latest_tag = None
        for candidate in releases:
            candidate_is_hash_suffix = bool(
                version_manager.HASH_SUFFIX_VERSION_RX.match(
                    candidate.tag_name.lstrip("vV")
                )
            )
            candidate_is_stable = (not candidate.prerelease) or candidate_is_hash_suffix
            if not candidate_is_stable:
                continue
            candidate_tuple = version_manager.get_release_tuple(candidate.tag_name)
            if candidate_tuple is None:
                continue
            if latest_tuple is None or candidate_tuple > latest_tuple:
                latest_tuple = candidate_tuple
                latest_tag = candidate.tag_name

        if latest_tag:
            expected_base = version_manager.calculate_expected_prerelease_version(
                latest_tag
            )

        if expected_base:
            filtered_prereleases = []
            for pr in prereleases:
                clean_version = version_manager.extract_clean_version(pr.tag_name)
                if clean_version and clean_version.lstrip("vV").startswith(
                    expected_base
                ):
                    filtered_prereleases.append(pr)
            prereleases = filtered_prereleases

        # Further restrict using commit history cache if available
        if recent_commits and expected_base:
            commit_hashes = []
            for commit in recent_commits:
                sha = commit.get("sha")
                if sha:
                    commit_hashes.append(sha[:7])
            filtered_by_commits = [
                pr
                for pr in prereleases
                if any(hash_part in pr.tag_name for hash_part in commit_hashes)
            ]
            if filtered_by_commits:
                prereleases = filtered_by_commits

        return prereleases

    def get_prerelease_tracking_file(self) -> str:
        """
        Get the path to the firmware prerelease tracking file.

        Returns:
            str: Path to the prerelease tracking file
        """
        return os.path.join(self.download_dir, self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        """
        Record the given prerelease tag and its enhanced metadata to the prerelease tracking file.

        Parameters:
            prerelease_tag (str): Prerelease tag to record.

        Returns:
            `true` if the tracking file was written successfully, `false` otherwise.

        Description:
            The tracking entry includes the prerelease tag, file type, timestamp, base version,
            prerelease type and number, and commit hash.
        """
        tracking_file = self.get_prerelease_tracking_file()

        # Extract metadata from prerelease tag
        version_manager = VersionManager()
        metadata = version_manager.get_prerelease_metadata_from_version(prerelease_tag)

        # Create tracking data with enhanced metadata
        data = {
            "latest_version": prerelease_tag,
            "file_type": "firmware_prerelease",
            "last_updated": self._get_current_iso_timestamp(),
            "base_version": metadata.get("base_version", ""),
            "prerelease_type": metadata.get("prerelease_type", ""),
            "prerelease_number": metadata.get("prerelease_number", ""),
            "commit_hash": metadata.get("commit_hash", ""),
        }

        return self.cache_manager.atomic_write_json(tracking_file, data)

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        """
        Decides whether the given prerelease tag should be downloaded based on configuration and prerelease tracking.

        Parameters:
            prerelease_tag (str): The prerelease tag to evaluate.

        Returns:
            bool: `true` if the tag represents a newer prerelease and should be downloaded; `false` if prerelease checks are disabled or the tag is not newer. If no tracking file exists or the tracking data is unreadable, returns `true`.
        """
        # Check if prereleases are enabled in config
        if not self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        ):
            return False

        # Check if we have a tracking file
        tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(tracking_file):
            try:
                data = self.cache_manager.read_json(tracking_file) or {}
                current_prerelease = data.get("latest_version")

                if current_prerelease:
                    version_manager = VersionManager()
                    comparison = version_manager.compare_versions(
                        prerelease_tag, current_prerelease
                    )
                    return comparison > 0  # Download if newer
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.debug(
                    "Error reading firmware prerelease tracking file %s: %s; defaulting to download",
                    tracking_file,
                    exc,
                )
                return True

        # No tracking file or unreadable; default to download
        return True

    def manage_prerelease_tracking_files(self) -> None:
        """
        Scan stored prerelease tracking files and remove entries that are superseded or expired.

        This updates the prerelease tracking directory by comparing stored tracking data with the current prereleases discovered from the remote repository and delegating cleanup of outdated or expired tracking files to the prerelease history manager.
        """
        tracking_dir = os.path.dirname(self.get_prerelease_tracking_file())
        if not os.path.exists(tracking_dir):
            return

        # Get all prerelease tracking files
        tracking_files = []
        for filename in os.listdir(tracking_dir):
            if filename.startswith("prerelease_") and filename.endswith(".json"):
                tracking_files.append(os.path.join(tracking_dir, filename))

        # Read all existing prerelease tracking data
        existing_prereleases = []
        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()

        for file_path in tracking_files:
            tracking_data = None
            try:
                tracking_data = self.cache_manager.read_json(file_path)
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Skipping prerelease tracking file %s due to read error: %s",
                    file_path,
                    exc,
                )
            if (
                tracking_data
                and "latest_version" in tracking_data
                and "base_version" in tracking_data
            ):
                existing_prereleases.append(tracking_data)

        # Get current prereleases from GitHub (if available)
        current_releases = self.get_releases(limit=10)
        current_prereleases = self.handle_prereleases(current_releases)

        # Create tracking data for current prereleases
        current_tracking_data = [
            prerelease_manager.create_prerelease_tracking_data(
                prerelease_version=prerelease.tag_name,
                base_version=version_manager.extract_clean_version(prerelease.tag_name)
                or "",
                expiry_hours=24,
                commit_hash=version_manager.get_prerelease_metadata_from_version(
                    prerelease.tag_name
                ).get("commit_hash", ""),
            )
            for prerelease in current_prereleases
        ]

        # Clean up superseded/expired prereleases using shared helper
        prerelease_manager.manage_prerelease_tracking_files(
            tracking_dir, current_tracking_data, self.cache_manager
        )

    def cleanup_superseded_prereleases(self, latest_release_tag: str) -> bool:
        """
        Remove prerelease firmware directories that are superseded by a given official release.

        Parameters:
            latest_release_tag (str): Official release tag (may include a leading "v") used to determine which prerelease versions are older or equal.

        Returns:
            bool: `True` if any prerelease directories were removed, `False` otherwise.
        """
        try:
            # Strip the 'v' prefix if present
            clean_release_tag = latest_release_tag.lstrip("vV")
            if not clean_release_tag:
                return False

            # Get version tuple for comparison
            version_manager = VersionManager()
            release_tuple = version_manager.get_release_tuple(clean_release_tag)
            if not release_tuple:
                return False

            # Path to prerelease directory
            prerelease_dir = os.path.join(self.download_dir, "firmware", "prerelease")
            if not os.path.exists(prerelease_dir):
                return False

            cleaned_up = False

            # Check for matching pre-release directories
            for raw_dir_name in os.listdir(prerelease_dir):
                if raw_dir_name.startswith(FIRMWARE_DIR_PREFIX):
                    dir_name = raw_dir_name[len(FIRMWARE_DIR_PREFIX) :]

                    # Extract version from directory name
                    if "." in dir_name:
                        parts = dir_name.split(".")
                        if len(parts) >= 3:
                            try:
                                dir_major, dir_minor, dir_patch = map(int, parts[:3])
                                dir_tuple = (dir_major, dir_minor, dir_patch)

                                # Check if this prerelease is superseded
                                if dir_tuple <= release_tuple:
                                    prerelease_path = os.path.join(
                                        prerelease_dir, raw_dir_name
                                    )
                                    try:
                                        shutil.rmtree(prerelease_path)
                                        logger.info(
                                            f"Removed superseded prerelease: {raw_dir_name}"
                                        )
                                        cleaned_up = True
                                    except OSError as e:
                                        logger.error(
                                            f"Error removing superseded prerelease {raw_dir_name}: {e}"
                                        )

                            except ValueError:
                                continue

            return cleaned_up

        except (OSError, ValueError) as e:
            logger.error(f"Error cleaning up superseded prereleases: {e}")
            return False
