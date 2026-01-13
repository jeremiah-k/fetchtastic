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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, cast

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    DEFAULT_ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES,
    DEFAULT_FILTER_REVOKED_RELEASES,
    DEFAULT_PRESERVE_LEGACY_FIRMWARE_BASE_DIRS,
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
    ERROR_TYPE_EXTRACTION,
    ERROR_TYPE_FILESYSTEM,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_VALIDATION,
    EXECUTABLE_PERMISSIONS,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_PRERELEASES_DIR_NAME,
    FIRMWARE_RELEASE_HISTORY_JSON_FILE,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    RELEASE_SCAN_COUNT,
    RELEASES_CACHE_EXPIRY_HOURS,
    REPO_DOWNLOADS_DIR,
    STORAGE_CHANNEL_SUFFIXES,
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
from .files import build_storage_tag_with_channel, get_channel_suffix
from .interfaces import Asset, DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .release_history import ReleaseHistoryManager
from .version import VersionManager

_FIRMWARE_SUFFIX_PARTS = [
    "revoked",
    *sorted(STORAGE_CHANNEL_SUFFIXES, key=len, reverse=True),
]
_FIRMWARE_SUFFIX_PATTERN = re.compile(
    rf"(?:{'|'.join(re.escape(f'-{suffix}') for suffix in _FIRMWARE_SUFFIX_PARTS)})+$"
)


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
        Initialize the firmware downloader with runtime configuration and a cache manager.

        Parameters:
            config: Runtime configuration dict used to determine download directories, selected asset patterns, and feature flags (e.g., prerelease checks).
            cache_manager: CacheManager used for cached API responses, tracking files, and remote directory listings.
        """
        super().__init__(config)
        self.cache_manager = cache_manager
        self.firmware_releases_url = MESHTASTIC_FIRMWARE_RELEASES_URL
        self.latest_release_file = LATEST_FIRMWARE_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_FIRMWARE_PRERELEASE_JSON_FILE
        self.latest_release_path = self.cache_manager.get_cache_file_path(
            self.latest_release_file
        )
        self.release_history_path = self.cache_manager.get_cache_file_path(
            FIRMWARE_RELEASE_HISTORY_JSON_FILE
        )
        self.release_history_manager = ReleaseHistoryManager(
            self.cache_manager, self.release_history_path
        )

        device_api_config = self.config.get("DEVICE_HARDWARE_API", {})
        self.device_manager = DeviceHardwareManager(
            enabled=device_api_config.get("enabled", True),
            cache_hours=device_api_config.get(
                "cache_hours", DEVICE_HARDWARE_CACHE_HOURS
            ),
            api_url=device_api_config.get("api_url", DEVICE_HARDWARE_API_URL),
        )

    @property
    def _filter_revoked_releases(self) -> bool:
        """
        Return whether revoked firmware releases should be filtered.

        Reads the "FILTER_REVOKED_RELEASES" configuration option and falls back to the module default when unset.

        Returns:
            bool: True if revoked firmware releases should be filtered, False otherwise.
        """
        value = self.config.get(
            "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
        )
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off", ""}:
                return False
        return bool(value)

    def collect_non_revoked_releases(
        self,
        initial_releases: List[Release],
        target_count: int,
        current_fetch_limit: int,
    ) -> Tuple[List[Release], List[Release], int]:
        """
        Select non-revoked releases from an initial list and expand the fetched set until a target count of non-revoked releases is met or a hard cap is reached.

        Parameters:
            initial_releases (List[Release]): Initial list of releases to filter.
            target_count (int): Desired number of non-revoked releases to obtain; if 0, no additional fetching is performed.
            current_fetch_limit (int): Current GitHub fetch limit used to retrieve releases; may be increased to find more non-revoked releases.

        Returns:
            Tuple[List[Release], List[Release], int]: A tuple containing:
                - non_revoked_releases: list of releases that are not revoked (may be shorter than target_count if no more are available),
                - all_releases: the most recently fetched full release list used to derive non_revoked_releases,
                - fetch_limit: the fetch limit used to obtain all_releases (may be increased up to 100).
        """
        all_releases = initial_releases
        fetch_limit = current_fetch_limit
        if not self._filter_revoked_releases:
            return all_releases, all_releases, fetch_limit

        def _filter(releases: List[Release]) -> List[Release]:
            """
            Filter a list of releases to exclude revoked entries.

            Parameters:
                releases (List[Release]): Release objects to be filtered.

            Returns:
                List[Release]: Subset of `releases` containing only releases that are not revoked.
            """
            return [
                release for release in releases if not self.is_release_revoked(release)
            ]

        non_revoked_releases = _filter(all_releases)
        if target_count == 0:
            return non_revoked_releases, all_releases, fetch_limit
        while len(non_revoked_releases) < target_count and fetch_limit < 100:
            next_limit = min(100, fetch_limit + RELEASE_SCAN_COUNT)
            logger.debug(
                "Need %d non-revoked releases but have %d; increasing fetch limit to %d",
                target_count,
                len(non_revoked_releases),
                next_limit,
            )
            all_releases = self.get_releases(limit=next_limit)
            if not all_releases:
                break
            fetch_limit = next_limit
            non_revoked_releases = _filter(all_releases)

        return non_revoked_releases, all_releases, fetch_limit

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Compute the filesystem path for a firmware asset and ensure its release version directory exists.

        Sanitizes `release_tag` and `file_name`, and creates the version subdirectory under the downloader's firmware directory if it does not exist.

        Parameters:
            release_tag (str): Release tag to use for the version subdirectory; will be sanitized.
            file_name (str): Asset file name; will be sanitized.

        Returns:
            str: Absolute path to the target location for the firmware asset.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME, safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Fetch firmware releases from GitHub and produce Release objects with their associated assets.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return; when omitted defaults to 8. Pass 0 to return an empty list. Values above 100 are capped at 100 (GitHub API limit).

        Returns:
            List[Release]: Parsed Release objects (each includes its Asset entries); returns an empty list if no valid releases are found or an error occurs.
        """
        try:
            if limit == 0:
                return []
            if limit is not None:
                if limit < 0:
                    logger.warning("Invalid limit value %d; using default", limit)
                    limit = None
                elif limit > 100:
                    logger.warning(
                        "Limit %d exceeds GitHub API max of 100; capping at 100.", limit
                    )
                    limit = 100
            params = {"per_page": limit if limit else 8}
            url_key = self.cache_manager.build_url_cache_key(
                self.firmware_releases_url, params
            )
            releases_data = self.cache_manager.read_releases_cache_entry(
                url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
            )

            if releases_data is not None:
                logger.debug(
                    "Using cached releases for %s (%d releases)",
                    self.firmware_releases_url,
                    len(releases_data),
                )

            if releases_data is None:
                response = make_github_api_request(
                    self.firmware_releases_url,
                    self.config.get("GITHUB_TOKEN"),
                    allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
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
                    name=release_data.get("name"),
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
        Get the direct download URL for an asset.

        Returns:
            The asset's direct download URL.
        """
        return asset.download_url

    def update_release_history(
        self, releases: List[Release], *, log_summary: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Update the on-disk release history cache and optionally log status summaries.

        Parameters:
            releases (List[Release]): Releases to record in history.
            log_summary (bool): When True, emit summary logs for revoked/removed releases
                and duplicated base versions.

        Returns:
            Optional[Dict[str, Any]]: The updated history data, or None when no releases
                were supplied.
        """
        if not releases:
            return None
        history = self.release_history_manager.update_release_history(releases)
        if log_summary:
            self.release_history_manager.log_release_channel_summary(
                releases, label="Firmware"
            )
            self.release_history_manager.log_release_status_summary(
                history, label="Firmware"
            )
            self.release_history_manager.log_duplicate_base_versions(
                releases, label="Firmware"
            )
        return history

    def format_release_log_suffix(self, release: Release) -> str:
        """
        Return a log suffix that annotates the release with its channel and revoked status when available.

        Returns:
            suffix (str): A string suitable for appending to log messages describing the release's channel (e.g., "-beta") and/or a revoked indicator; empty string if no annotation is necessary.
        """
        return self.release_history_manager.format_release_log_suffix(release)

    def ensure_release_notes(self, release: Release) -> Optional[str]:
        """
        Store the given release's release notes alongside its firmware assets and return the notes file path.

        Parameters:
            release (Release): Release object whose release notes should be stored.

        Returns:
            str or None: Path to the release notes file if written or already present; `None` if the release tag is unsafe or the notes were not stored.
        """
        try:
            storage_tag = self._get_release_storage_tag(release)
        except ValueError:
            logger.warning(
                "Skipping release notes for unsafe firmware tag: %s", release.tag_name
            )
            return None

        release_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME, storage_tag)
        base_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        return self._write_release_notes(
            release_dir=release_dir,
            release_tag=release.tag_name,
            body=release.body,
            base_dir=base_dir,
        )

    def _get_release_storage_tag(self, release: Release) -> str:
        """
        Compute the filesystem storage tag for a release by combining a sanitized tag with any channel and revoked suffixes.

        If an existing on-disk directory matches a different valid storage tag for the same release, the method will attempt to rename that directory to the computed target tag; if the rename fails it will return the existing directory tag. If multiple candidate directories are present, the first candidate found is returned.

        Returns:
            storage_tag (str): The storage tag that should be used for the release's directory.
        """
        safe_tag = self._sanitize_required(release.tag_name, "release tag")
        is_revoked = self.is_release_revoked(release)
        target_tag = build_storage_tag_with_channel(
            sanitized_release_tag=safe_tag,
            release=release,
            release_history_manager=self.release_history_manager,
            config=self.config,
            is_revoked=is_revoked,
        )

        firmware_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        add_channel_suffixes = self.config.get(
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES",
            DEFAULT_ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES,
        )
        if add_channel_suffixes and not is_revoked and target_tag == safe_tag:
            existing_channel_dirs = [
                f"{safe_tag}-{channel}"
                for channel in sorted(STORAGE_CHANNEL_SUFFIXES)
                if os.path.isdir(os.path.join(firmware_dir, f"{safe_tag}-{channel}"))
            ]
            if existing_channel_dirs:
                if len(existing_channel_dirs) > 1:
                    logger.warning(
                        "Multiple channel-suffixed firmware directories found for %s: %s",
                        release.tag_name,
                        ", ".join(existing_channel_dirs),
                    )
                logger.debug(
                    "Using existing channel-suffixed firmware directory for %s: %s",
                    release.tag_name,
                    existing_channel_dirs[0],
                )
                target_tag = existing_channel_dirs[0]

        target_path = os.path.join(firmware_dir, target_tag)
        if os.path.isdir(target_path):
            return target_tag

        candidates = self._get_storage_tag_candidates(release, target_tag)
        existing = [
            tag for tag in candidates if os.path.isdir(os.path.join(firmware_dir, tag))
        ]
        if len(existing) == 1:
            alternate_tag = existing[0]
            alternate_path = os.path.join(firmware_dir, alternate_tag)
            try:
                os.rename(alternate_path, target_path)
                logger.info(
                    "Renamed firmware release directory %s -> %s",
                    alternate_tag,
                    target_tag,
                )
                return target_tag
            except OSError as exc:
                logger.warning(
                    "Unable to rename firmware release directory %s -> %s: %s",
                    alternate_tag,
                    target_tag,
                    exc,
                )
                return alternate_tag
        if len(existing) > 1:
            logger.warning(
                "Multiple firmware release directories found for %s: %s",
                release.tag_name,
                ", ".join(existing),
            )
            return existing[0]

        return target_tag

    def _build_storage_tag(self, safe_tag: str, channel: str, revoked: bool) -> str:
        """
        Builds a storage tag by appending an optional channel suffix to a sanitized base tag, or replacing with -revoked suffix.

        Parameters:
            safe_tag (str): Sanitized release tag to use as the base (no channel or revoked suffixes).
            channel (str): Channel suffix to append (e.g., "beta"); if empty, no channel suffix is added.
            revoked (bool): If True, replaces any channel suffix with "-revoked".

        Returns:
            str: The resulting storage tag.
        """
        if revoked:
            return f"{safe_tag}-revoked"
        tag = safe_tag
        if channel:
            tag = f"{tag}-{channel}"
        return tag

    def _get_storage_tag_candidates(
        self, release: Release, target_tag: str
    ) -> List[str]:
        """
        Builds an ordered list of alternative storage-tag candidates for a release by combining channel suffixes and revoked variants.

        Includes channel-suffixed and unsuffixed variants (and a revoked variant) to aid discovery of existing directories; excludes the supplied target_tag from the result.

        Parameters:
            release (Release): Release to derive the base tag and channel from.
            target_tag (str): Storage tag to omit from the returned candidates.

        Returns:
            List[str]: Ordered, distinct storage-tag strings (each a filesystem-safe tag) excluding `target_tag`.
        """
        safe_tag = self._sanitize_required(release.tag_name, "release tag")
        is_revoked = self.is_release_revoked(release)

        # Determine current channel from release using shared helper
        # Always detect channel for candidate generation (regardless of feature flag)
        # so we can find existing directories created with different suffixes
        current_channel_suffix = get_channel_suffix(
            release=release,
            release_history_manager=self.release_history_manager,
            add_channel_suffixes=True,
        )
        current_channel = (
            current_channel_suffix.lstrip("-") if current_channel_suffix else ""
        )

        # Build list of channel names to try for discovery even when suffixes are disabled.
        channels_to_try = [current_channel, ""]
        if not release.prerelease:
            channels_to_try.extend(sorted(STORAGE_CHANNEL_SUFFIXES))
        channels = list(dict.fromkeys(channels_to_try))

        # Build all possible non-revoked and revoked tags
        non_revoked_tags = [
            self._build_storage_tag(safe_tag, c, False) for c in channels
        ]
        revoked_tag = self._build_storage_tag(safe_tag, "", True)

        # Order candidates based on whether the release is revoked
        if is_revoked:
            ordered_candidates = [revoked_tag] + non_revoked_tags
        else:
            ordered_candidates = non_revoked_tags + [revoked_tag]

        # Remove duplicates while preserving order and filter out the target_tag
        unique_candidates = list(dict.fromkeys(ordered_candidates))
        return [tag for tag in unique_candidates if tag != target_tag]

    def download_firmware(self, release: Release, asset: Asset) -> DownloadResult:
        """
        Download and verify a single firmware asset for a release and produce a structured DownloadResult.

        Parameters:
            release (Release): Release that contains the asset being downloaded.
            asset (Asset): Metadata for the firmware asset to download.

        Returns:
            DownloadResult: Result describing the outcome. On success includes `file_path`, `download_url`, `file_size`, and `file_type`; when the download was skipped includes `was_skipped`; on failure includes `error_message`, `error_type` (e.g., `"network_error"`, `"validation_error"`, `"filesystem_error"`) and `is_retryable`.
        """
        if self._filter_revoked_releases and self.is_release_revoked(release):
            logger.info(
                "Skipping revoked firmware release %s because revoked filtering is enabled.",
                release.tag_name,
            )
            firmware_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
            return self.create_download_result(
                success=True,
                release_tag=release.tag_name,
                file_path=firmware_dir,
                download_url=asset.download_url,
                file_size=asset.size,
                file_type=FILE_TYPE_FIRMWARE,
                was_skipped=True,
                error_type="revoked_release",
                error_details={
                    "revoked": True,
                    "filter_revoked_releases": True,
                },
            )

        target_path: Optional[str] = None
        try:
            storage_tag = self._get_release_storage_tag(release)
            # Get target path for the firmware ZIP
            target_path = self.get_target_path_for_release(storage_tag, asset.name)

            # Check if we need to download
            if self.is_asset_complete(storage_tag, asset):
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
                    file_type=FILE_TYPE_FIRMWARE,
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
                        file_type=FILE_TYPE_FIRMWARE,
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
                        file_type=FILE_TYPE_FIRMWARE,
                        is_retryable=True,
                        error_type=ERROR_TYPE_VALIDATION,
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
                    file_type=FILE_TYPE_FIRMWARE,
                    is_retryable=True,
                    error_type=ERROR_TYPE_NETWORK,
                )

        except (requests.RequestException, OSError, ValueError) as exc:
            logger.exception("Error downloading firmware %s: %s", asset.name, exc)
            safe_path = target_path or os.path.join(
                self.download_dir, FIRMWARE_DIR_NAME
            )
            if isinstance(exc, requests.RequestException):
                error_type = ERROR_TYPE_NETWORK
                is_retryable = True
            elif isinstance(exc, OSError):
                error_type = ERROR_TYPE_FILESYSTEM
                is_retryable = False
            else:
                error_type = ERROR_TYPE_VALIDATION
                is_retryable = False
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=safe_path,
                error_message=str(exc),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type=FILE_TYPE_FIRMWARE,
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
        try:
            storage_tag = self._get_release_storage_tag(release)
        except ValueError:
            logger.warning(
                "Skipping completeness check for unsafe firmware tag: %s",
                release.tag_name,
            )
            return False
        version_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME, storage_tag)
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
        Validate that the provided include and exclude glob patterns for extraction are well-formed and safe.

        Parameters:
            patterns (List[str]): Filename glob patterns to include during extraction.
            exclude_patterns (List[str]): Filename glob patterns to exclude during extraction.

        Returns:
            `True` if the patterns are valid, `False` otherwise.
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
        Determines whether files should be extracted from an archive into a target directory based on include/exclude patterns and current extracted contents.

        Parameters:
            file_path (str): Path to the archive file.
            extract_dir (str): Directory where files would be extracted.
            patterns (List[str]): Glob patterns of files to include.
            exclude_patterns (List[str]): Glob patterns of files to exclude.

        Returns:
            bool: `True` if extraction is needed (files are missing, outdated, or do not match the patterns), `False` otherwise.
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
        Extract specified files from a firmware ZIP into the release's version directory.

        Validates the provided include/exclude patterns, skips extraction when matching files are already present, performs extraction when needed, and returns a DownloadResult summarizing success, skipped status, extracted file list, or error details.

        Parameters:
                release (Release): Release that owns the firmware asset.
                asset (Asset): The firmware ZIP asset to extract.
                patterns (List[str]): Glob patterns of files to include from the archive.
                exclude_patterns (Optional[List[str]]): Glob patterns of files to exclude from extraction.

        Returns:
                DownloadResult: Contains `extracted_files` and `file_path` on success (or empty list with `was_skipped=True` when no files matched); on failure contains `error_message` and `error_type`.
        """
        zip_path: str = ""
        try:
            exclude_patterns = exclude_patterns or []

            # Get the path to the downloaded ZIP file
            storage_tag = self._get_release_storage_tag(release)
            zip_path = self.get_target_path_for_release(storage_tag, asset.name)
            if not os.path.exists(zip_path):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="ZIP file not found",
                    file_type=FILE_TYPE_FIRMWARE,
                    error_type=ERROR_TYPE_VALIDATION,
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
                    file_type=FILE_TYPE_FIRMWARE,
                    error_type=ERROR_TYPE_VALIDATION,
                )

            if not self.file_operations.check_extraction_needed(
                zip_path, extract_dir, patterns, exclude_patterns
            ):
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    extracted_files=[],
                    file_type=FILE_TYPE_FIRMWARE,
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
                    file_type=FILE_TYPE_FIRMWARE,
                )
            else:
                logger.warning(
                    f"No files extracted from {asset.name} - no matches for patterns"
                )
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    file_type=FILE_TYPE_FIRMWARE,
                    extracted_files=[],
                    was_skipped=True,
                )

        except (zipfile.BadZipFile, OSError, ValueError) as e:
            logger.error(f"Error extracting firmware {asset.name}: {e}")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=zip_path
                or os.path.join(self.download_dir, FIRMWARE_DIR_NAME),
                error_message=str(e),
                file_type=FILE_TYPE_FIRMWARE,
                error_type=ERROR_TYPE_EXTRACTION,
            )

    def cleanup_old_versions(
        self,
        keep_limit: int,
        cached_releases: Optional[List[Release]] = None,
        keep_last_beta: bool = False,
    ) -> None:
        """
        Remove firmware version directories not present in the latest `keep_limit` releases
        (full releases only).

        This mirrors legacy behavior by keeping only the newest release tags (alpha/beta)
        returned by GitHub API (bounded by `keep_limit`). Any local version
        directories not in that set are removed. Special directories "prerelease" and
        "repo-dls" are always preserved.

        Parameters:
            keep_limit (int): Maximum number of most-recent version directories to retain;
                older directories will be deleted. Pass 0 to delete all version directories.
            cached_releases (Optional[List[Release]]): Optional release list to avoid redundant API calls.
            keep_last_beta (bool): If True, always keep the most recent beta release
                in addition to keep_limit releases. Default is False.
        """
        try:
            if keep_limit < 0:
                logger.warning(
                    "Invalid keep_limit value %d; skipping cleanup", keep_limit
                )
                return

            # Get all firmware version directories
            firmware_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
            if not os.path.exists(firmware_dir):
                return

            logger.debug(
                "Firmware cleanup start: keep_limit=%s, keep_last_beta=%s, firmware_dir=%s",
                keep_limit,
                keep_last_beta,
                firmware_dir,
            )

            # Fetch releases once, using a small scan window to locate the latest beta
            # This avoids a redundant second API call when keep_last_beta is enabled
            filter_revoked = self._filter_revoked_releases
            fetch_limit = (
                max(keep_limit, RELEASE_SCAN_COUNT) if keep_last_beta else keep_limit
            )
            if filter_revoked:
                # Add a buffer of releases to compensate for skipped revoked entries
                # without increasing the API loop complexity.
                fetch_limit += RELEASE_SCAN_COUNT
            fetch_limit = min(100, fetch_limit if fetch_limit >= 0 else 0)

            if cached_releases is not None and len(cached_releases) >= fetch_limit:
                all_releases = cached_releases
            else:
                cached_len = len(cached_releases) if cached_releases is not None else 0
                reason_parts = []
                if keep_last_beta:
                    reason_parts.append("keep_last_beta")
                if filter_revoked:
                    reason_parts.append("filter_revoked")
                reason_text = (
                    " and ".join(reason_parts) if reason_parts else "fetch requirements"
                )
                logger.debug(
                    "cached_releases contains %d releases but %d are needed to honor %s; refetching",
                    cached_len,
                    fetch_limit,
                    reason_text,
                )
                all_releases = self.get_releases(limit=fetch_limit)
            if not all_releases and (keep_limit > 0 or keep_last_beta):
                logger.warning(
                    "Skipping firmware cleanup: no releases available to determine keep set."
                )
                return

            preserve_legacy_base_dirs = self.config.get(
                "PRESERVE_LEGACY_FIRMWARE_BASE_DIRS",
                DEFAULT_PRESERVE_LEGACY_FIRMWARE_BASE_DIRS,
            )

            add_channel_suffixes = self.config.get(
                "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES",
                DEFAULT_ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES,
            )

            non_revoked_releases, all_releases, fetch_limit = (
                self.collect_non_revoked_releases(
                    initial_releases=all_releases,
                    target_count=keep_limit,
                    current_fetch_limit=fetch_limit,
                )
            )
            latest_releases = non_revoked_releases[:keep_limit] if keep_limit else []

            release_tags_to_keep = set()
            keep_base_names = set()
            for release in latest_releases:
                try:
                    safe_tag = self._sanitize_required(release.tag_name, "release tag")
                except ValueError:
                    logger.warning(
                        "Skipping unsafe firmware release tag during cleanup: %s",
                        release.tag_name,
                    )
                    continue
                base_tag = self._get_comparable_base_tag(safe_tag)
                keep_base_names.add(base_tag)

                # Always keep the unsuffixed tag so legacy directories (created
                # before channel suffixing existed) are never deleted during
                # cleanup. This keeps the transition from older versions safe.
                release_tags_to_keep.add(safe_tag)

                # Build the current channel-aware tag and keep it too; this
                # preserves the preferred directory name without renaming
                # anything during cleanup.
                release_tags_to_keep.add(
                    build_storage_tag_with_channel(
                        sanitized_release_tag=base_tag,
                        release=release,
                        release_history_manager=self.release_history_manager,
                        config=self.config,
                        is_revoked=self.is_release_revoked(release),
                    )
                )

            # If keep_last_beta is enabled, ensure most recent beta is kept
            if keep_last_beta:
                beta_source = non_revoked_releases if filter_revoked else all_releases
                most_recent_beta = self.release_history_manager.find_most_recent_beta(
                    beta_source
                )
                if most_recent_beta:
                    try:
                        safe_beta_tag = self._sanitize_required(
                            most_recent_beta.tag_name, "beta release tag"
                        )
                        beta_base_tag = self._get_comparable_base_tag(safe_beta_tag)
                        keep_base_names.add(beta_base_tag)
                        release_tags_to_keep.add(safe_beta_tag)
                        release_tags_to_keep.add(
                            build_storage_tag_with_channel(
                                sanitized_release_tag=beta_base_tag,
                                release=most_recent_beta,
                                release_history_manager=self.release_history_manager,
                                config=self.config,
                                is_revoked=self.is_release_revoked(most_recent_beta),
                            )
                        )
                    except ValueError:
                        logger.warning(
                            "Skipping unsafe beta release tag during cleanup: %s",
                            most_recent_beta.tag_name,
                        )

            if not release_tags_to_keep and keep_limit > 0:
                logger.warning(
                    "Skipping firmware cleanup: no safe release tags found to keep."
                )
                return

            # Remove local versions not in the keep set
            try:
                with os.scandir(firmware_dir) as it:
                    entries = list(it)

                existing_versions = {
                    entry.name
                    for entry in entries
                    if entry.is_dir()
                    and not entry.is_symlink()
                    and entry.name
                    not in {
                        FIRMWARE_PRERELEASES_DIR_NAME,
                        REPO_DOWNLOADS_DIR,
                    }
                }

                existing_base_names = {
                    self._get_comparable_base_tag(name) for name in existing_versions
                }
                unmatched_channel_dirs = []
                if not add_channel_suffixes:
                    unmatched_channel_dirs = [
                        name
                        for name in existing_versions
                        if name not in release_tags_to_keep
                        and name != self._get_comparable_base_tag(name)
                    ]

                if (
                    keep_limit > 0
                    and existing_versions
                    and (
                        keep_base_names.isdisjoint(existing_base_names)
                        or bool(unmatched_channel_dirs)
                    )
                ):
                    logger.warning(
                        "Skipping firmware cleanup: keep set does not match existing directories."
                    )
                    return
                for entry in entries:
                    if entry.name in {
                        FIRMWARE_PRERELEASES_DIR_NAME,
                        REPO_DOWNLOADS_DIR,
                    }:
                        continue
                    if entry.is_symlink():
                        logger.warning(
                            "Skipping symlink in firmware directory during cleanup: %s",
                            entry.name,
                        )
                        continue
                    if entry.is_dir():
                        if entry.name in release_tags_to_keep:
                            continue
                        if preserve_legacy_base_dirs and entry.name in keep_base_names:
                            continue
                        try:
                            logger.debug(
                                "Removing firmware directory: %s",
                                entry.path,
                            )
                            shutil.rmtree(entry.path)
                            logger.info("Removed old firmware version: %s", entry.name)
                        except OSError as e:
                            logger.error(
                                "Error removing old firmware version %s: %s",
                                entry.name,
                                e,
                            )
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.error("Error cleaning up old firmware versions: %s", e)
        except OSError as e:
            logger.error("Error during firmware cleanup: %s", e)

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Read the tracked latest firmware release tag from the local tracking JSON file.

        Returns:
            latest_version (Optional[str]): The stored latest release tag, or `None` if the tracking file does not exist or contains invalid JSON.
        """
        latest_file = self.latest_release_path
        if os.path.exists(latest_file):
            try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return cast(str | None, data.get("latest_version"))
            except (IOError, json.JSONDecodeError):
                pass
        return None

    def update_latest_release_tag(self, release_tag: str) -> bool:
        """
        Record the provided firmware release tag as the latest tracked release.

        Parameters:
            release_tag (str): The release tag to persist (for example, "v1.2.3").

        Returns:
            `true` if the tracking file was written successfully, `false` otherwise.
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
        Get the current UTC timestamp as an ISO 8601 string including the UTC timezone offset.

        Returns:
            str: ISO 8601 formatted UTC timestamp including the UTC timezone offset.
        """

        return datetime.now(timezone.utc).isoformat()

    def _get_expiry_timestamp(self) -> str:
        """
        Produce an ISO 8601 UTC timestamp 24 hours from now.

        Returns:
            iso_timestamp (str): ISO 8601-formatted UTC timestamp representing the current time plus 24 hours.
        """
        return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    def _get_prerelease_base_dir(self) -> str:
        """
        Ensure and return the base directory for prerelease firmware downloads.

        Returns:
            str: Absolute path to the prerelease base directory under the downloader's download directory; the directory is created if it does not already exist.
        """
        prerelease_dir = os.path.join(
            self.download_dir, FIRMWARE_DIR_NAME, FIRMWARE_PRERELEASES_DIR_NAME
        )
        os.makedirs(prerelease_dir, exist_ok=True)
        return prerelease_dir

    def _get_prerelease_patterns(self) -> List[str]:
        """
        Normalize and return the prerelease asset selection patterns from the configuration.

        If the configuration key "SELECTED_PRERELEASE_ASSETS" is missing or falsy, returns an empty list. If the configured value is already a list, it is returned unchanged; if it is a single non-list value, it is converted to a single-item list containing its string representation.

        Returns:
            List[str]: Patterns used to select prerelease assets; empty list if none configured.
        """
        patterns = self.config.get("SELECTED_PRERELEASE_ASSETS") or []
        return patterns if isinstance(patterns, list) else [str(patterns)]

    def _get_comparable_base_tag(self, name: str) -> str:
        """
        Remove channel/revoked suffixes and the firmware- prefix to get a comparable base version tag.

        Parameters:
            name (str): Directory or tag name that may include channel suffixes (e.g., "-beta", "-rc") or "-revoked", and may start with the firmware- prefix.

        Returns:
            str: Normalized base version tag suitable for comparison.
        """
        stripped_name = _FIRMWARE_SUFFIX_PATTERN.sub("", name)
        return stripped_name.removeprefix(FIRMWARE_DIR_PREFIX)

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
            allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
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
                name, selected_patterns, device_manager=self.device_manager
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
                                file_type=FILE_TYPE_FIRMWARE_PRERELEASE,
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
                            file_type=FILE_TYPE_FIRMWARE_PRERELEASE,
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
                            file_type=FILE_TYPE_FIRMWARE_PRERELEASE,
                            is_retryable=True,
                            error_type=ERROR_TYPE_NETWORK,
                        )
                    )
            except (requests.RequestException, OSError, ValueError) as exc:
                if isinstance(exc, requests.RequestException):
                    error_type = ERROR_TYPE_NETWORK
                    is_retryable = True
                elif isinstance(exc, OSError):
                    error_type = ERROR_TYPE_FILESYSTEM
                    is_retryable = False
                else:
                    error_type = ERROR_TYPE_VALIDATION
                    is_retryable = False
                failures.append(
                    self.create_download_result(
                        success=False,
                        release_tag=remote_dir,
                        file_path=target_path,
                        error_message=str(exc),
                        download_url=str(url),
                        file_size=item.get("size"),
                        file_type=FILE_TYPE_FIRMWARE_PRERELEASE,
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
        Download prerelease assets from a remote prerelease directory into the local prerelease store using include and exclude patterns.

        Parameters:
            remote_dir (str): Remote prerelease directory identifier or path to fetch files from.
            selected_patterns (Optional[List[str]]): Glob patterns to include; empty list means include all.
            exclude_patterns (Optional[List[str]]): Glob patterns to exclude; empty list means exclude none.
            force_refresh (bool): If true, refresh remote listing/cache before deciding which files to download.

        Returns:
            tuple[list[DownloadResult], list[DownloadResult], bool]: A 3-tuple containing:
                - successes: list of successful DownloadResult entries,
                - failures: list of failed DownloadResult entries,
                - any_downloaded: `True` if any file was downloaded during this call, `False` otherwise.
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
    ) -> tuple[
        list[DownloadResult],
        list[DownloadResult],
        Optional[str],
        Optional[Dict[str, Any]],
    ]:
        """
        Check for and download firmware prerelease assets from the legacy repo-based workflow and update prerelease tracking.

        Parameters:
            latest_release_tag (str): Tag of the latest official release used to derive the expected prerelease base version.
            force_refresh (bool): When True, bypass cached directory listings and force remote refresh.

        Returns:
            tuple[list[DownloadResult], list[DownloadResult], Optional[str], Optional[Dict[str, Any]]]:
            A 4-tuple containing:
                - successes: list of DownloadResult for assets that were successfully downloaded or skipped.
                - failures: list of DownloadResult for assets that failed to download.
                - active_dir: remote prerelease directory identifier used for the download, or `None` if no prerelease was found.
                - prerelease_summary: a dict with prerelease history details (keys: `history_entries`, `clean_latest_release`, `expected_version`) for later reporting, or `None` when no history is available.
        """
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return [], [], None, None

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
            return [], [], None, None

        logger.debug("Expected prerelease version: %s", expected_version)

        active_dir, history_entries = (
            prerelease_manager.get_latest_active_prerelease_from_history(
                expected_version,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                force_refresh=force_refresh,
            )
        )
        prerelease_summary = None
        if history_entries:
            prerelease_summary = {
                "history_entries": history_entries,
                "clean_latest_release": clean_latest_release,
                "expected_version": expected_version,
            }

        if active_dir:
            logger.info("Using commit history for prerelease detection")
        else:
            # Fallback: scan repo root for prerelease directories
            try:
                dirs = self.cache_manager.get_repo_directories(
                    "",
                    force_refresh=force_refresh,
                    github_token=self.config.get("GITHUB_TOKEN"),
                    allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
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

        if active_dir:
            repo_dirs = self.cache_manager.get_repo_directories(
                "",
                force_refresh=True,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
            )
            if active_dir not in repo_dirs:
                logger.info(
                    "Prerelease directory %s no longer exists; skipping prerelease download",
                    active_dir,
                )
                return [], [], None, prerelease_summary

        if not active_dir:
            logger.info("No pre-release firmware available")
            return [], [], None, prerelease_summary

        selected_patterns = self._get_prerelease_patterns()
        exclude_patterns = self._get_exclude_patterns()
        if selected_patterns:
            logger.debug(
                "Using your extraction patterns for pre-release selection: %s",
                " ".join(selected_patterns),
            )

        prerelease_base_dir = self._get_prerelease_base_dir()
        existing_dirs = []
        try:
            with os.scandir(prerelease_base_dir) as it:
                for entry in it:
                    if entry.is_dir():
                        existing_dirs.append(entry.name)
        except FileNotFoundError:
            pass

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

        # Consolidate skipped messages
        skipped_count = sum(1 for result in successes if result.was_skipped)
        if skipped_count > 0:
            logger.debug(f"Skipped {skipped_count} existing pre-release files.")

        return successes, failures, active_dir, prerelease_summary

    def log_prerelease_summary(
        self,
        history_entries: List[Dict[str, Any]],
        clean_latest_release: str,
        expected_version: str,
    ) -> None:
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
        Filter prerelease Release objects according to configuration, the expected base version derived from the latest stable release, and optional recent commit hashes.

        This function:
        - Returns an empty list when prerelease checking is disabled via configuration.
        - Returns an empty list for firmware GitHub releases because their prerelease
          flag represents alpha/beta tracks that are treated as stable in Fetchtastic.
          Firmware prereleases are instead handled via the meshtastic.github.io workflow.
        - Excludes prereleases whose tag appears to be a hash-suffixed version.
        - Sorts remaining prereleases by published date (newest first).
        - Applies include/exclude pattern filtering using configuration keys "FIRMWARE_PRERELEASE_INCLUDE_PATTERNS" and "FIRMWARE_PRERELEASE_EXCLUDE_PATTERNS" when provided.
        - Derives an expected prerelease base version from the latest stable release and keeps only prereleases whose cleaned version starts with that base.
        - If recent_commits is provided, further prefers prereleases whose tag contains any 7-character commit SHA present in that list.

        Parameters:
            releases (List[Release]): All releases to consider.
            recent_commits (Optional[List[Dict[str, Any]]]): Optional list of recent commit objects; each commit dict is expected to contain a "sha" key used to derive 7-character hashes for tag matching.

        Returns:
            List[Release]: Filtered list of prerelease Release objects that satisfy the configured and derived constraints.
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )

        if not check_prereleases:
            return []

        logger.debug(
            "Firmware GitHub prerelease flags are treated as stable; "
            "firmware prereleases are handled via the repo-based workflow."
        )
        return []

    def get_prerelease_tracking_file(self) -> str:
        """
        Return the path to the firmware prerelease tracking JSON file.

        Returns:
            str: Absolute path to the prerelease tracking file used for firmware prerelease state.
        """
        return self.cache_manager.get_cache_file_path(self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        """
        Record a prerelease tag and its metadata to the prerelease tracking file.

        The stored metadata includes base version, prerelease type and number, commit hash, file type, and last updated timestamp.

        Parameters:
            prerelease_tag (str): Prerelease tag to record.

        Returns:
            bool: True if the tracking file was written successfully, False otherwise.
        """
        tracking_file = self.get_prerelease_tracking_file()

        # Extract metadata from prerelease tag
        version_manager = VersionManager()
        metadata = version_manager.get_prerelease_metadata_from_version(prerelease_tag)

        # Create tracking data with enhanced metadata
        data = {
            "latest_version": prerelease_tag,
            "file_type": FILE_TYPE_FIRMWARE_PRERELEASE,
            "last_updated": self._get_current_iso_timestamp(),
            "base_version": metadata.get("base_version", ""),
            "prerelease_type": metadata.get("prerelease_type", ""),
            "prerelease_number": metadata.get("prerelease_number", ""),
            "commit_hash": metadata.get("commit_hash", ""),
        }

        return self.cache_manager.atomic_write_json(tracking_file, data)

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        """
        Decides whether a prerelease tag is newer than the currently tracked prerelease and should be downloaded.

        Parameters:
                prerelease_tag (str): The prerelease tag to evaluate.

        Returns:
                True if prerelease checks are enabled and `prerelease_tag` is newer than the tracked prerelease, or if no valid tracking data exists; `False` otherwise.
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

    def manage_prerelease_tracking_files(
        self, cached_releases: Optional[List[Release]] = None
    ) -> None:
        """
        Remove or expire local prerelease tracking files that are superseded by current repository prereleases.

        Compare stored prerelease tracking data with the set of current prereleases and delegate removal of outdated or expired tracking files to the PrereleaseHistoryManager.

        Parameters:
            cached_releases (Optional[List[Release]]): Optional list of Release objects to use instead of fetching releases from the remote API.
        """
        tracking_dir = os.path.dirname(self.get_prerelease_tracking_file())

        # Get all prerelease tracking files
        tracking_files = []
        try:
            with os.scandir(tracking_dir) as it:
                for entry in it:
                    if entry.name.startswith("prerelease_") and entry.name.endswith(
                        ".json"
                    ):
                        tracking_files.append(entry.path)
        except FileNotFoundError:
            return

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
        # Use cached releases if provided to avoid redundant API calls
        current_releases = cached_releases or self.get_releases(limit=10)
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
        Remove prerelease firmware directories whose semantic version is less than or equal to a given official release.

        Parameters:
            latest_release_tag (str): Official release tag used for comparison; may include a leading "v".

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
            prerelease_dir = os.path.join(
                self.download_dir, FIRMWARE_DIR_NAME, FIRMWARE_PRERELEASES_DIR_NAME
            )

            cleaned_up = False

            try:
                # Check for matching pre-release directories
                with os.scandir(prerelease_dir) as it:
                    for entry in it:
                        if entry.is_symlink():
                            logger.warning(
                                "Skipping symlink in prerelease folder: %s",
                                entry.name,
                            )
                            continue
                        if not entry.is_dir():
                            continue
                        if entry.name.startswith(FIRMWARE_DIR_PREFIX):
                            dir_name = entry.name[len(FIRMWARE_DIR_PREFIX) :]

                            # Extract version from directory name
                            if "." in dir_name:
                                parts = dir_name.split(".")
                                if len(parts) >= 3:
                                    try:
                                        dir_major, dir_minor, dir_patch = map(
                                            int, parts[:3]
                                        )
                                        dir_tuple = (dir_major, dir_minor, dir_patch)

                                        # Check if this prerelease is superseded
                                        if dir_tuple <= release_tuple:
                                            prerelease_path = entry.path
                                            try:
                                                shutil.rmtree(prerelease_path)
                                                logger.info(
                                                    f"Removed superseded prerelease: {entry.name}"
                                                )
                                                cleaned_up = True
                                            except OSError as e:
                                                logger.error(
                                                    f"Error removing superseded prerelease {entry.name}: {e}"
                                                )

                                    except ValueError:
                                        continue
            except FileNotFoundError:
                return False

            return cleaned_up

        except (OSError, ValueError) as e:
            logger.error(f"Error cleaning up superseded prereleases: {e}")
            return False
