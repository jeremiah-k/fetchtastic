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
    DEFAULT_CHECK_FIRMWARE_NIGHTLIES,
    DEFAULT_CREATE_LATEST_SYMLINKS,
    DEFAULT_FILTER_REVOKED_RELEASES,
    DEFAULT_FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP,
    DEFAULT_PRESERVE_LEGACY_FIRMWARE_BASE_DIRS,
    DEVICE_HARDWARE_API_URL,
    DEVICE_HARDWARE_CACHE_HOURS,
    ERROR_TYPE_EXTRACTION,
    ERROR_TYPE_FILESYSTEM,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_REVOKED_RELEASE,
    ERROR_TYPE_VALIDATION,
    EXECUTABLE_PERMISSIONS,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_MANIFEST,
    FILE_TYPE_FIRMWARE_NIGHTLY,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_MANIFEST_EXTENSION,
    FIRMWARE_NIGHTLIES_DIR_NAME,
    FIRMWARE_NIGHTLY_MANIFEST_PATTERN,
    FIRMWARE_NIGHTLY_SOURCE_DIR,
    FIRMWARE_PRERELEASES_DIR_NAME,
    FIRMWARE_RELEASE_HISTORY_JSON_FILE,
    LATEST_FIRMWARE_NIGHTLY_JSON_FILE,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    LATEST_POINTER_NAME,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    RELEASE_SCAN_COUNT,
    REPO_DOWNLOADS_DIR,
    STORAGE_CHANNEL_SUFFIXES,
)
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    coerce_bool,
    download_file_with_retry,
    load_file_hash,
    matches_extract_patterns,
    matches_selected_patterns,
    verify_file_integrity,
)

from .base import BaseDownloader
from .cache import CacheManager, parse_iso_datetime_utc
from .files import (
    _is_within_base,
    _prepare_for_redownload,
    _safe_rmtree,
    build_storage_tag_with_channel,
    get_channel_suffix,
    is_zip_intact,
)
from .github_source import GithubReleaseSource, create_release_from_github_data
from .interfaces import Asset, DownloadResult, FirmwareManifest, Release
from .latest_pointer import remove_latest_pointer, update_latest_pointer
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

# Release-level nightly manifest: firmware-<version>.<hash>.json (build-id capture).
# Device manifests (.mt.json) and zips are rejected.  See FIRMWARE_NIGHTLY_MANIFEST_PATTERN.
_NIGHTLY_MANIFEST_RX = re.compile(FIRMWARE_NIGHTLY_MANIFEST_PATTERN, re.IGNORECASE)
# A nightly build-id on its own (used to validate build directories under nightlies/).
_NIGHTLY_BUILD_ID_RX = re.compile(r"^\d+\.\d+\.\d+\.[a-f0-9]{6,}$", re.IGNORECASE)
# Detects a nightly build token (e.g. ``2.8.0.f52e2ea``) embedded anywhere in
# an asset filename so the selector can reject stale generations.
_NIGHTLY_BUILD_TOKEN_RX = re.compile(r"(\d+\.\d+\.\d+\.[a-f0-9]{6,})", re.IGNORECASE)


def _normalize_repo_directory_listing(raw: Any, *, source: str) -> list[str]:
    """Return string directory names from a repository listing response."""
    if not isinstance(raw, list):
        logger.debug(
            "Expected list of repo directories from %s, got %s",
            source,
            type(raw).__name__,
        )
        return []
    return [directory for directory in raw if isinstance(directory, str)]


def _resolve_extract_patterns(raw: Any) -> Optional[List[str]]:
    """Resolve an extraction-patterns config value, distinguishing
    absent/unsupported/malformed from explicitly empty.

    Returns:
      * ``None``  — value is absent (``None``), an unsupported type
        (e.g. ``int``, ``dict``), or a collection containing any
        non-string member (mixed types). Callers may fall back to
        another key.
      * ``[]``    — value is a valid all-string string / list / tuple /
        set / frozenset that resolves to no nonempty patterns (explicit
        empty). Callers MUST NOT fall back: the user explicitly opted out.
      * ``[...]`` — nonempty deterministic ordered list of stripped,
        deduplicated strings.

    The input is never mutated. All-or-nothing: a collection with any
    non-string member is malformed (``None``) — partial salvage is never
    returned. ``set`` / ``frozenset`` members are validated before sorting
    so mixed types never raise ``TypeError``. Lists / tuples / strings
    preserve first-seen order; sets / frozensets are sorted for determinism.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        return [stripped] if stripped else []
    if not isinstance(raw, (list, tuple, set, frozenset)):
        # Unsupported scalar / mapping → caller may fall back.
        return None
    # All-or-nothing: any non-string member makes the whole collection
    # malformed. Validate BEFORE sorting sets so mixed types never raise.
    for item in raw:
        if not isinstance(item, str):
            return None
    if isinstance(raw, (set, frozenset)):
        items: List[str] = []
        for item in sorted(raw):
            stripped = item.strip()
            if stripped and stripped not in items:
                items.append(stripped)
        return items
    items = []
    for item in raw:
        stripped = item.strip()
        if stripped and stripped not in items:
            items.append(stripped)
    return items


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
        Create a FirmwareReleaseDownloader configured with runtime settings and a cache manager.

        Initializes components required for firmware release discovery and management, including a GitHub release source, release history tracking, and device-hardware metadata handling.

        Parameters:
            config (Dict[str, Any]): Runtime configuration that controls download directories, selected asset patterns, feature flags (for example, prerelease handling), and device-hardware API settings.
            cache_manager (CacheManager): Cache manager used for cached API responses, atomic JSON writes, remote repository listings, and deriving local cache file paths.
        """
        super().__init__(config)
        self.cache_manager = cache_manager
        self.firmware_releases_url = MESHTASTIC_FIRMWARE_RELEASES_URL
        self.github_source = GithubReleaseSource(
            releases_url=MESHTASTIC_FIRMWARE_RELEASES_URL,
            cache_manager=cache_manager,
            config=config,
        )
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

    def update_latest_pointer_for_release(self, release: Release) -> bool:
        """Best-effort update of firmware latest pointer for a completed release."""
        if not coerce_bool(
            self.config.get("CREATE_LATEST_SYMLINKS", DEFAULT_CREATE_LATEST_SYMLINKS),
            DEFAULT_CREATE_LATEST_SYMLINKS,
        ):
            return False
        try:
            parent_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
            target_name = self._get_release_storage_tag(release)
            return update_latest_pointer(parent_dir, target_name, LATEST_POINTER_NAME)
        except (OSError, ValueError, TypeError) as exc:
            logger.debug(
                "Skipping firmware latest pointer for %s: %s",
                release.tag_name,
                exc,
            )
            return False

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

            releases = self.github_source.get_releases(
                params, create_release_from_github_data
            )

            # Respect limit if specified
            if limit and len(releases) > limit:
                releases = releases[:limit]

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
                error_type=ERROR_TYPE_REVOKED_RELEASE,
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

    def _is_release_manifest_name(self, asset_name: str) -> bool:
        """
        Determine whether an asset name is the release-level firmware manifest JSON.

        Examples of accepted names include `firmware-2.7.20.6658ec2.json`.
        Per-device manifests (`*.mt.json`) are excluded by this helper.
        """
        asset_name_lower = asset_name.lower()
        return (
            asset_name_lower.startswith(FIRMWARE_DIR_PREFIX)
            and asset_name_lower.endswith(".json")
            and not asset_name_lower.endswith(FIRMWARE_MANIFEST_EXTENSION)
        )

    def _is_manifest_asset_name(self, asset_name: str) -> bool:
        """
        Return True when an asset is either a per-device (`.mt.json`) or release-level JSON manifest.
        """
        asset_name_lower = asset_name.lower()
        return asset_name_lower.endswith(
            FIRMWARE_MANIFEST_EXTENSION
        ) or self._is_release_manifest_name(asset_name_lower)

    def download_manifests(self, release: Release) -> List[DownloadResult]:
        """
        Download firmware manifest files for a firmware release.

        This includes:
        - Per-device manifests (`*.mt.json`) with hardware/file metadata.
        - Release-level manifests (`firmware-<version>.json`) with target lists.

        Parameters:
            release (Release): Release whose manifest files should be downloaded.

        Returns:
            List[DownloadResult]: List of download results for each manifest file.
        """
        results: List[DownloadResult] = []

        if self._filter_revoked_releases and self.is_release_revoked(release):
            logger.info(
                "Skipping revoked firmware manifests for %s because revoked filtering is enabled.",
                release.tag_name,
            )
            return results

        try:
            storage_tag = self._get_release_storage_tag(release)
        except ValueError:
            logger.warning(
                "Skipping manifests for unsafe firmware tag: %s", release.tag_name
            )
            return results

        for asset in release.assets:
            if not asset.name or not self._is_manifest_asset_name(asset.name):
                continue
            is_device_manifest = asset.name.lower().endswith(
                FIRMWARE_MANIFEST_EXTENSION
            )

            try:
                target_path = self.get_target_path_for_release(storage_tag, asset.name)
            except ValueError as exc:
                logger.warning(
                    "Skipping manifest with unsafe name %s: %s", asset.name, exc
                )
                results.append(
                    self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=os.path.join(
                            self.download_dir, FIRMWARE_DIR_NAME, storage_tag
                        ),
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                        error_message=str(exc),
                        is_retryable=False,
                        error_type=ERROR_TYPE_VALIDATION,
                    )
                )
                continue

            if os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        manifest_data = json.load(f)
                    if (
                        is_device_manifest
                        and self._parse_manifest_data(manifest_data) is None
                    ):
                        raise ValueError("Manifest schema is invalid")
                    size_matches = asset.size is None or (
                        os.path.getsize(target_path) == asset.size
                    )
                    if size_matches and self.verify(target_path):
                        logger.debug(
                            "Manifest %s already exists and is valid", asset.name
                        )
                        results.append(
                            self.create_download_result(
                                success=True,
                                release_tag=release.tag_name,
                                file_path=target_path,
                                download_url=asset.download_url,
                                file_size=asset.size,
                                file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                                was_skipped=True,
                            )
                        )
                        continue
                except (json.JSONDecodeError, IOError, OSError, ValueError):
                    pass

            try:
                success = self.download(asset.download_url, target_path)
                if success:
                    if self.verify(target_path):
                        try:
                            with open(target_path, "r", encoding="utf-8") as f:
                                manifest_data = json.load(f)
                            if (
                                is_device_manifest
                                and self._parse_manifest_data(manifest_data) is None
                            ):
                                raise ValueError("Manifest schema is invalid")
                        except (json.JSONDecodeError, ValueError):
                            logger.error("Malformed manifest %s", asset.name)
                            self.cleanup_file(target_path)
                            results.append(
                                self.create_download_result(
                                    success=False,
                                    release_tag=release.tag_name,
                                    file_path=target_path,
                                    error_message="Manifest JSON is invalid",
                                    download_url=asset.download_url,
                                    file_size=asset.size,
                                    file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                                    is_retryable=True,
                                    error_type=ERROR_TYPE_VALIDATION,
                                )
                            )
                            continue
                        except OSError as exc:
                            logger.exception(
                                "Error reading manifest %s at %s: %s",
                                asset.name,
                                target_path,
                                exc,
                            )
                            self.cleanup_file(target_path)
                            results.append(
                                self.create_download_result(
                                    success=False,
                                    release_tag=release.tag_name,
                                    file_path=target_path,
                                    error_message=str(exc),
                                    download_url=asset.download_url,
                                    file_size=asset.size,
                                    file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                                    is_retryable=False,
                                    error_type=ERROR_TYPE_FILESYSTEM,
                                )
                            )
                            continue
                        logger.info("Downloaded manifest %s", asset.name)
                        results.append(
                            self.create_download_result(
                                success=True,
                                release_tag=release.tag_name,
                                file_path=target_path,
                                download_url=asset.download_url,
                                file_size=asset.size,
                                file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                            )
                        )
                    else:
                        logger.error("Verification failed for manifest %s", asset.name)
                        self.cleanup_file(target_path)
                        results.append(
                            self.create_download_result(
                                success=False,
                                release_tag=release.tag_name,
                                file_path=target_path,
                                error_message="Verification failed",
                                download_url=asset.download_url,
                                file_size=asset.size,
                                file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                                is_retryable=True,
                                error_type=ERROR_TYPE_VALIDATION,
                            )
                        )
                else:
                    logger.error("Download failed for manifest %s", asset.name)
                    results.append(
                        self.create_download_result(
                            success=False,
                            release_tag=release.tag_name,
                            file_path=target_path,
                            error_message="download(...) returned False",
                            download_url=asset.download_url,
                            file_size=asset.size,
                            file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                            is_retryable=True,
                            error_type=ERROR_TYPE_NETWORK,
                        )
                    )
            except requests.RequestException as exc:
                logger.exception("Error downloading manifest %s: %s", asset.name, exc)
                self.cleanup_file(target_path)
                results.append(
                    self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                        error_message=str(exc),
                        is_retryable=True,
                        error_type=ERROR_TYPE_NETWORK,
                    )
                )
            except OSError as exc:
                logger.exception("Error downloading manifest %s: %s", asset.name, exc)
                self.cleanup_file(target_path)
                results.append(
                    self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                        error_message=str(exc),
                        is_retryable=False,
                        error_type=ERROR_TYPE_FILESYSTEM,
                    )
                )
            except ValueError as exc:
                logger.exception(
                    "Validation error for manifest %s: %s", asset.name, exc
                )
                self.cleanup_file(target_path)
                results.append(
                    self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type=FILE_TYPE_FIRMWARE_MANIFEST,
                        error_message=str(exc),
                        is_retryable=False,
                        error_type=ERROR_TYPE_VALIDATION,
                    )
                )

        return results

    def _parse_manifest_data(self, data: Any) -> Optional[FirmwareManifest]:
        """
        Parse raw manifest JSON data into a FirmwareManifest dataclass.

        Parameters:
            data (Any): Raw JSON manifest data.

        Returns:
            Optional[FirmwareManifest]: Parsed manifest, or None if parsing fails.
        """
        if not isinstance(data, dict):
            logger.debug(
                "Manifest data is not a dict, got %s: %s", type(data).__name__, data
            )
            return None

        try:
            # Validate required fields before constructing FirmwareManifest
            hw_model_slug = data.get("hwModelSlug")
            if not isinstance(hw_model_slug, str) or not hw_model_slug:
                logger.debug("Manifest missing valid hwModelSlug: %s", hw_model_slug)
                return None

            files = data.get("files", [])
            if not isinstance(files, list):
                logger.debug(
                    "Manifest files field is not a list: %s", type(files).__name__
                )
                return None

            part = data.get("part", [])
            if not isinstance(part, list):
                logger.debug(
                    "Manifest part field is not a list: %s", type(part).__name__
                )
                return None

            return FirmwareManifest(
                version=data.get("version"),
                hwModel=data.get("hwModel"),
                hwModelSlug=hw_model_slug,
                architecture=data.get("architecture"),
                activelySupported=data.get("activelySupported"),
                displayName=data.get("displayName"),
                supportLevel=data.get("supportLevel"),
                has_mui=data.get("has_mui"),
                has_inkhud=data.get("has_inkhud"),
                files=files,
                part=part,
                raw_data=data,
            )
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to parse manifest data: %s", exc)
            return None

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

            try:
                actual_size = os.path.getsize(asset_path)
                expected_size = asset.size
                if expected_size is not None and actual_size != expected_size:
                    logger.debug(
                        "File size mismatch for %s: expected %s, got %s",
                        asset_path,
                        expected_size,
                        actual_size,
                    )
                    return False
            except (OSError, TypeError):
                logger.debug(f"Error checking file size for {asset_path}")
                return False

            if asset.name.lower().endswith(".zip"):
                # If a trusted hash baseline already exists, hash verification is
                # substantially faster than re-running ZIP member decompression.
                has_hash_baseline = load_file_hash(asset_path) is not None
                if has_hash_baseline:
                    try:
                        if not self.verify(asset_path):
                            logger.debug("Hash verification failed for %s", asset_path)
                            return False
                    except OSError as e:
                        logger.debug(
                            "Error during hash verification for %s: %s", asset_path, e
                        )
                        return False
                    continue

                try:
                    with zipfile.ZipFile(asset_path, "r") as zf:
                        if zf.testzip() is not None:
                            logger.debug(f"Corrupted zip file detected: {asset_path}")
                            return False
                    if not self.verify(asset_path):
                        logger.debug("Hash verification failed for %s", asset_path)
                        return False
                except zipfile.BadZipFile:
                    logger.debug(f"Bad zip file detected: {asset_path}")
                    return False
                except (IOError, OSError):
                    logger.debug(f"Error checking zip file: {asset_path}")
                    return False
            else:
                # For non-zip files, verify the hash
                if not self.verify(asset_path):
                    logger.debug("Hash verification failed for %s", asset_path)
                    return False

        return True

    def get_zips_needing_extraction(self, release: Release) -> List[Asset]:
        """
        Return the list of .zip assets from a release whose extracted contents
        do not match the current EXTRACT_PATTERNS / EXCLUDE_PATTERNS configuration.

        This enables re-extraction when the user changes extraction patterns or
        when previously extracted files are missing or have incorrect sizes.

        Parameters:
            release (Release): Release whose .zip assets to inspect.

        Returns:
            List[Asset]: .zip assets that need extraction. Empty list if
                AUTO_EXTRACT is disabled, no extract patterns are configured,
                the version directory does not exist, or all files are already
                extracted correctly.
        """
        if not self.config.get("AUTO_EXTRACT", False):
            return []

        extract_patterns = self.config.get("EXTRACT_PATTERNS", [])
        if isinstance(extract_patterns, str):
            extract_patterns = [extract_patterns]
        if not extract_patterns:
            return []

        try:
            storage_tag = self._get_release_storage_tag(release)
        except ValueError:
            logger.warning(
                "Skipping extraction check for unsafe firmware tag: %s",
                release.tag_name,
            )
            return []
        version_dir = os.path.join(self.download_dir, FIRMWARE_DIR_NAME, storage_tag)
        if not os.path.isdir(version_dir):
            return []

        exclude_patterns = self._get_exclude_patterns()
        selected_patterns = self.config.get("SELECTED_FIRMWARE_ASSETS", [])
        if isinstance(selected_patterns, str):
            selected_patterns = [selected_patterns]

        result: List[Asset] = []
        for asset in release.assets:
            if not asset.name:
                continue

            if not asset.name.lower().endswith(".zip"):
                continue

            if selected_patterns and not matches_selected_patterns(
                asset.name, selected_patterns
            ):
                continue

            if self._matches_exclude_patterns(asset.name, exclude_patterns):
                continue

            zip_path = os.path.join(version_dir, asset.name)
            if not os.path.exists(zip_path):
                continue

            if self.file_operations.check_extraction_needed(
                zip_path, version_dir, extract_patterns, exclude_patterns
            ):
                result.append(asset)

        logger.debug(
            "Found %d zip(s) needing extraction for release %s",
            len(result),
            release.tag_name,
        )
        return result

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
            bool: `True` if extraction is needed (matching members are missing or size-stale, or the archive could not be inspected). `False` if extraction can be skipped, including when the archive has no members matching the patterns (the normal no-op case).
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
                        FIRMWARE_NIGHTLIES_DIR_NAME,
                        LATEST_POINTER_NAME,
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
                        FIRMWARE_NIGHTLIES_DIR_NAME,
                        REPO_DOWNLOADS_DIR,
                    }:
                        continue
                    if entry.name == LATEST_POINTER_NAME:
                        if entry.is_symlink():
                            continue
                        logger.debug(
                            "Preserving non-symlink latest entry that may block latest pointer creation: %s",
                            entry.path,
                        )
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

    def _matches_prerelease_selection(
        self, filename: str, selected_patterns: List[str]
    ) -> bool:
        """
        Determine whether a prerelease filename should be selected by include patterns.

        Selection rules:
        - If no patterns are configured, all files are eligible.
        - Files matching legacy prerelease extraction patterns are eligible.
        - Release-level manifest JSON (`firmware-<version>.json`) is always eligible
          so target metadata remains available even with narrow pattern filters.
        """
        if not selected_patterns:
            return True

        if matches_extract_patterns(
            filename, selected_patterns, device_manager=self.device_manager
        ):
            return True

        return self._is_release_manifest_name(filename)

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
            if not self._matches_prerelease_selection(name, selected_patterns):
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
                        has_hash_baseline = load_file_hash(target_path) is not None
                        if not has_hash_baseline:
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
            latest_release_tag (str): Tag of the latest firmware release selected for the current run; may be hash-suffixed (e.g. v2.7.22.96dd647).
            force_refresh (bool): When True, bypass cached directory listings and force remote refresh.

        Returns:
            tuple[list[DownloadResult], list[DownloadResult], Optional[str], Optional[Dict[str, Any]]]:
            A 4-tuple containing:
                - successes: list of DownloadResult for assets that were successfully downloaded or skipped.
                - failures: list of DownloadResult for assets that failed to download.
                - active_dir: remote prerelease directory identifier used for the download, or `None` if no prerelease was found.
                - prerelease_summary: a dict with prerelease history and/or repo-discovered details (keys: `history_entries`, `clean_latest_release`, `expected_version`, `available_history_entries`) for later reporting, or `None` when no prerelease processing was performed.
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
        # History APIs now admit any prerelease base strictly newer than the
        # latest stable release, so pass the cleaned stable tag (e.g. "v2.7.26")
        # rather than the legacy next-patch derivation.  The next-patch value is
        # retained only as a display label in the summary/log and as a parseability
        # gate: if it cannot be derived the input tag is too malformed to admit.
        expected_version = version_manager.calculate_expected_prerelease_version(
            clean_latest_release
        )
        if not expected_version:
            return [], [], None, None

        logger.debug(
            "Prerelease admission floor: stable=%s (next-patch label=%s)",
            clean_latest_release,
            expected_version,
        )

        latest_active_dir, history_entries = (
            prerelease_manager.get_latest_active_prerelease_from_history(
                clean_latest_release,
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

        active_dirs = self._get_active_prerelease_dirs_from_history(history_entries)
        if latest_active_dir and latest_active_dir not in active_dirs:
            active_dirs.append(latest_active_dir)

        fallback_repo_dirs = None
        if active_dirs:
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
                dirs = _normalize_repo_directory_listing(dirs, source="cache manager")
                fallback_repo_dirs = dirs
                matches = prerelease_manager.scan_prerelease_directories(
                    dirs, clean_latest_release
                )
                if matches:
                    # Choose newest by tuple then string
                    matches.sort(
                        key=lambda ident: (
                            version_manager.get_release_tuple(ident) or (),
                            ident,
                        ),
                    )
                    active_dirs = [
                        f"{FIRMWARE_DIR_PREFIX}{identifier}" for identifier in matches
                    ]
            except (requests.RequestException, OSError, ValueError, TypeError) as exc:
                logger.debug(
                    "Fallback prerelease directory scan failed; skipping prerelease detection: %s",
                    exc,
                )

        if active_dirs:
            repo_availability_verified = False
            if fallback_repo_dirs is not None:
                repo_dirs = fallback_repo_dirs
            else:
                try:
                    repo_dirs = self.cache_manager.get_repo_directories(
                        "",
                        force_refresh=force_refresh,
                        github_token=self.config.get("GITHUB_TOKEN"),
                        allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                    )
                except (
                    requests.RequestException,
                    OSError,
                    ValueError,
                    TypeError,
                ) as exc:
                    logger.debug(
                        "Repo availability scan failed; continuing with best history-derived active dirs: %s",
                        exc,
                    )
                    repo_dirs = []
            repo_dirs = _normalize_repo_directory_listing(
                repo_dirs, source="cache manager"
            )
            if repo_dirs:
                repo_availability_verified = True
            else:
                logger.debug(
                    "Repo availability scan returned no usable directories; "
                    "cannot verify availability of history-derived prereleases"
                )
            deleted_dirs = self._get_deleted_prerelease_dirs_from_history(
                history_entries
            )
            if repo_availability_verified and not force_refresh:
                history_active_dirs = [
                    directory
                    for directory in active_dirs
                    if directory not in deleted_dirs
                ]
                cached_repo_dir_set = set(repo_dirs)
                missing_history_dirs = [
                    directory
                    for directory in history_active_dirs
                    if directory not in cached_repo_dir_set
                ]
                if missing_history_dirs:
                    logger.debug(
                        "History-derived prereleases are missing from the cached "
                        "repository directory listing; refreshing availability before "
                        "treating them as deleted: %s",
                        ", ".join(missing_history_dirs),
                    )
                    try:
                        refreshed_repo_dirs = self.cache_manager.get_repo_directories(
                            "",
                            force_refresh=True,
                            github_token=self.config.get("GITHUB_TOKEN"),
                            allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                        )
                    except (
                        requests.RequestException,
                        OSError,
                        ValueError,
                        TypeError,
                    ) as exc:
                        logger.debug(
                            "Fresh repo availability scan failed; preserving "
                            "history-derived active prereleases: %s",
                            exc,
                        )
                        refreshed_repo_dirs = []
                    refreshed_repo_dirs = _normalize_repo_directory_listing(
                        refreshed_repo_dirs, source="fresh availability scan"
                    )
                    if refreshed_repo_dirs:
                        repo_dirs = refreshed_repo_dirs
                    else:
                        logger.debug(
                            "Fresh repo availability scan returned no usable directories; "
                            "preserving history-derived active prereleases"
                        )
                        repo_availability_verified = False

            if repo_availability_verified:
                repo_dir_set = set(repo_dirs)
                matching_repo_dirs = [
                    f"{FIRMWARE_DIR_PREFIX}{identifier}"
                    for identifier in prerelease_manager.scan_prerelease_directories(
                        repo_dirs, clean_latest_release
                    )
                ]
                active_dirs_set = set(active_dirs)
                for repo_dir in matching_repo_dirs:
                    if repo_dir not in deleted_dirs and repo_dir not in active_dirs_set:
                        active_dirs.append(repo_dir)
                        active_dirs_set.add(repo_dir)

                missing_dirs = [
                    directory
                    for directory in active_dirs
                    if directory not in repo_dir_set
                ]
                active_dirs = [
                    directory
                    for directory in active_dirs
                    if directory in repo_dir_set and directory not in deleted_dirs
                ]
                for missing_dir in missing_dirs:
                    if active_dirs:
                        logger.info(
                            "Prerelease directory %s no longer exists; continuing with remaining active prereleases",
                            missing_dir,
                        )
                    else:
                        logger.info(
                            "Prerelease directory %s no longer exists; skipping prerelease download",
                            missing_dir,
                        )
            else:
                active_dirs = [
                    directory
                    for directory in active_dirs
                    if directory not in deleted_dirs
                ]

            if prerelease_summary is None and active_dirs:
                prerelease_summary = {
                    "history_entries": history_entries or [],
                    "clean_latest_release": clean_latest_release,
                    "expected_version": expected_version,
                }

            if prerelease_summary is not None:
                available_dirs = set(active_dirs)
                available_history_entries = [
                    entry
                    for entry in history_entries
                    if entry.get("status") == "deleted"
                    or bool(entry.get("removed_at"))
                    or entry.get("directory") in available_dirs
                ]
                available_dirs_in_history = {
                    entry.get("directory")
                    for entry in available_history_entries
                    if entry.get("directory")
                }
                for active_dir in active_dirs:
                    if active_dir not in available_dirs_in_history:
                        identifier = active_dir.removeprefix(FIRMWARE_DIR_PREFIX)
                        base_version = (
                            ".".join(identifier.split(".")[:3]) if identifier else ""
                        )
                        available_history_entries.append(
                            {
                                "directory": active_dir,
                                "identifier": identifier,
                                "base_version": base_version,
                                "status": "active",
                                "active": True,
                                "source": "repo_scan",
                            }
                        )
                prerelease_summary["available_history_entries"] = (
                    available_history_entries
                )

        if not active_dirs:
            logger.info("No pre-release firmware available")
            return [], [], None, prerelease_summary

        active_dirs = self._sort_prerelease_dirs(active_dirs)

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

        successes: list[DownloadResult] = []
        failures: list[DownloadResult] = []
        any_downloaded = False
        downloaded_dirs: list[str] = []
        successful_dirs: list[str] = []
        for active_dir in active_dirs:
            (
                prerelease_successes,
                prerelease_failures,
                prerelease_downloaded,
            ) = self._download_prerelease_assets(
                active_dir,
                selected_patterns=selected_patterns,
                exclude_patterns=exclude_patterns,
                force_refresh=force_refresh,
            )
            successes.extend(prerelease_successes)
            failures.extend(prerelease_failures)
            if (
                prerelease_successes
                and not prerelease_failures
                and all(result.success for result in prerelease_successes)
            ):
                successful_dirs.append(active_dir)
            if prerelease_downloaded:
                any_downloaded = True
                downloaded_dirs.append(active_dir)
            if (
                not prerelease_downloaded
                and active_dir in existing_dirs
                and not prerelease_failures
            ):
                logger.info(
                    "Found existing pre-release %s, but no new files to download.",
                    active_dir,
                )

        dirs_to_track: list[str] = []
        if force_refresh or (active_dirs and not failures):
            dirs_to_track = active_dirs
        elif any_downloaded:
            dirs_to_track = downloaded_dirs
        for active_dir in dirs_to_track:
            prerelease_manager.update_prerelease_tracking(
                latest_release_tag, active_dir, cache_manager=self.cache_manager
            )
        latest_successful_dir = self._select_latest_prerelease_dir(
            successful_dirs, history_entries
        ) or (
            self._sort_prerelease_dirs(successful_dirs)[-1] if successful_dirs else None
        )
        if latest_successful_dir and coerce_bool(
            self.config.get("CREATE_LATEST_SYMLINKS", DEFAULT_CREATE_LATEST_SYMLINKS),
            DEFAULT_CREATE_LATEST_SYMLINKS,
        ):
            if not update_latest_pointer(
                prerelease_base_dir,
                latest_successful_dir,
                LATEST_POINTER_NAME,
            ):
                logger.debug(
                    "Skipping firmware prerelease latest pointer for %s",
                    latest_successful_dir,
                )

        # Consolidate skipped messages
        skipped_count = sum(1 for result in successes if result.was_skipped)
        if skipped_count > 0:
            logger.debug(f"Skipped {skipped_count} existing pre-release files.")

        return (
            successes,
            failures,
            (
                self._select_latest_prerelease_dir(active_dirs, history_entries)
                or (
                    self._sort_prerelease_dirs(active_dirs)[-1] if active_dirs else None
                )
            ),
            prerelease_summary,
        )

    @staticmethod
    def _sort_prerelease_dirs(dirs: List[str]) -> List[str]:
        """Deduplicate and sort prerelease directory names by parsed version tuple, then directory string as tie breaker."""
        version_manager = VersionManager()

        def sort_key(directory: str) -> Tuple:
            identifier = directory.removeprefix(FIRMWARE_DIR_PREFIX)
            version_tuple = version_manager.get_release_tuple(identifier) or ()
            return (version_tuple, directory)

        seen: set[str] = set()
        unique_dirs: list[str] = []
        for d in dirs:
            if d not in seen:
                seen.add(d)
                unique_dirs.append(d)
        unique_dirs.sort(key=sort_key)
        return unique_dirs

    def _select_latest_prerelease_dir(
        self,
        candidate_dirs: list[str],
        history_entries: list[dict[str, Any]],
    ) -> Optional[str]:
        """Select the latest prerelease directory using commit-history chronology.

        Firmware repo prereleases are distinguished by a commit-hash suffix, not
        by semver prerelease components.  Therefore the "latest" prerelease must
        be chosen by repository commit chronology (added_at), not by hash-string
        ordering or VersionManager.get_release_tuple.

        Ranking (higher wins):
          1. has_history – real history entry exists (source != "repo_scan")
          2. has_timestamp – entry carries an added_at value
          3. timestamp – parsed added_at datetime
          4. history_index – position in history_entries, or a repo-only sentinel
          5. fallback_key – deterministic sort via _sort_prerelease_dirs

        Synthetic source="repo_scan" entries are fallback-only and do not count
        as real history-backed entries.
        Deleted/removed entries and entries whose directory is not in
        candidate_dirs are excluded.

        Returns:
            The directory string of the newest active prerelease, or None when
            no candidate qualifies.
        """
        if not candidate_dirs:
            return None

        deleted_dirs = self._get_deleted_prerelease_dirs_from_history(history_entries)
        history_rank_by_dir: dict[str, dict[str, Any]] = {}
        for idx, entry in enumerate(history_entries):
            directory = entry.get("directory")
            if not isinstance(directory, str):
                continue
            if directory in deleted_dirs:
                continue
            is_deleted = entry.get("status") == "deleted" or bool(
                entry.get("removed_at")
            )
            if is_deleted:
                continue
            is_active = entry.get("status") == "active" or entry.get("active") is True
            if not is_active:
                continue
            if entry.get("source") == "repo_scan":
                continue

            added_at_raw = entry.get("added_at")
            timestamp = parse_iso_datetime_utc(added_at_raw) if added_at_raw else None

            history_rank_by_dir[directory] = {
                "has_timestamp": timestamp is not None,
                "timestamp": timestamp,
                "history_index": idx,
            }

        sorted_dirs = self._sort_prerelease_dirs(candidate_dirs)
        fallback_index = {d: i for i, d in enumerate(sorted_dirs)}

        best_dir: Optional[str] = None
        best_key: Optional[tuple] = None
        repo_only_history_index = len(history_entries) + len(candidate_dirs) + 1

        for candidate in candidate_dirs:
            if candidate in deleted_dirs:
                continue
            info = history_rank_by_dir.get(candidate)
            has_history = info is not None
            timestamp = info.get("timestamp") if info is not None else None
            history_index = (
                info["history_index"] if info is not None else repo_only_history_index
            )
            sort_key = (
                1 if has_history else 0,
                1 if timestamp is not None else 0,
                timestamp or datetime.min.replace(tzinfo=timezone.utc),
                history_index,
                fallback_index.get(candidate, 0),
            )

            if best_key is None or sort_key > best_key:
                best_key = sort_key
                best_dir = candidate

        return best_dir

    def _get_active_prerelease_dirs_from_history(
        self, history_entries: List[Dict[str, Any]]
    ) -> list[str]:
        """Return explicitly active prerelease directories from history in history order."""
        active_dirs: list[str] = []
        seen: set[str] = set()
        for entry in history_entries:
            directory = entry.get("directory")
            is_active = entry.get("status") == "active" or entry.get("active") is True
            is_deleted = entry.get("status") == "deleted" or bool(
                entry.get("removed_at")
            )
            if (
                is_active
                and not is_deleted
                and isinstance(directory, str)
                and directory not in seen
            ):
                active_dirs.append(directory)
                seen.add(directory)
        return active_dirs

    def _get_deleted_prerelease_dirs_from_history(
        self, history_entries: List[Dict[str, Any]]
    ) -> set[str]:
        """Return prerelease directories explicitly marked deleted in history."""
        deleted_dirs: set[str] = set()
        for entry in history_entries:
            directory = entry.get("directory")
            if not isinstance(directory, str):
                continue
            if entry.get("status") == "deleted" or bool(entry.get("removed_at")):
                deleted_dirs.add(directory)
        return deleted_dirs

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

        deleted_dirs = self._get_deleted_prerelease_dirs_from_history(history_entries)
        active_candidate_dirs: List[str] = []
        for entry in history_entries:
            directory_value: Any = entry.get("directory")
            if not isinstance(directory_value, str):
                continue
            if directory_value in deleted_dirs:
                continue
            if not (entry.get("status") == "active" or entry.get("active") is True):
                continue
            if entry.get("status") == "deleted":
                continue
            if entry.get("removed_at"):
                continue
            active_candidate_dirs.append(directory_value)
        latest_active_dir = self._select_latest_prerelease_dir(
            active_candidate_dirs, history_entries
        )
        latest_active_identifier = None
        if latest_active_dir:
            for entry in history_entries:
                if entry.get("directory") == latest_active_dir:
                    latest_active_identifier = entry.get("identifier")
                    break
            if latest_active_identifier is None:
                latest_active_identifier = latest_active_dir.removeprefix(
                    FIRMWARE_DIR_PREFIX
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
        Remove prerelease firmware directories whose semantic version is less than or equal to the given release tag.

        Uses the latest release by version (stable) as the baseline for determining which prerelease
        directories are superseded, regardless of prerelease flag.

        Parameters:
            latest_release_tag (str): Release tag used for comparison. May include:
                - a leading "v" (e.g., "v2.7.15")
                - a hash/commit suffix (e.g., "v2.7.15.abc1234")

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

            # Names left on disk after cleanup are the only valid targets for the
            # managed prerelease/latest pointer. Removal failures remain valid targets.
            valid_latest_target_names: set[str] = set()
            try:
                # Check for matching pre-release directories
                with os.scandir(prerelease_dir) as it:
                    for entry in it:
                        if entry.is_symlink():
                            if entry.name == LATEST_POINTER_NAME:
                                logger.debug(
                                    "Validating expected symlink in prerelease folder: %s",
                                    entry.name,
                                )
                            else:
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
                                                valid_latest_target_names.add(
                                                    entry.name
                                                )
                                        else:
                                            valid_latest_target_names.add(entry.name)

                                    except ValueError:
                                        continue
                self._cleanup_invalid_prerelease_latest_pointer(
                    prerelease_dir, valid_latest_target_names
                )
            except FileNotFoundError:
                return False

            return cleaned_up

        except (OSError, ValueError) as e:
            logger.error(f"Error cleaning up superseded prereleases: {e}")
            return False

    def _cleanup_invalid_prerelease_latest_pointer(
        self, base_dir: str, retained_names: set[str]
    ) -> None:
        """Remove a managed prerelease ``latest`` symlink when it is dangling or stale.

        Non-symlink entries named ``latest`` are preserved. The symlink target must be
        a retained prerelease directory name and must still exist as a directory.
        """
        link_path = os.path.join(base_dir, LATEST_POINTER_NAME)
        if not os.path.islink(link_path):
            return
        try:
            target = os.readlink(link_path)
        except OSError:
            remove_latest_pointer(base_dir)
            return
        target_name = os.path.basename(target.rstrip(os.sep))
        if target != target_name or target_name not in retained_names:
            remove_latest_pointer(base_dir)
            return
        target_path = os.path.join(base_dir, target_name)
        if not os.path.isdir(target_path):
            remove_latest_pointer(base_dir)

    # ==================================================================
    # Firmware-nightly: rolling build published at firmware-nightly/
    # ==================================================================

    def _nightlies_enabled(self) -> bool:
        """Return True when CHECK_FIRMWARE_NIGHTLIES is enabled (opt-in, default off)."""
        return coerce_bool(
            self.config.get(
                "CHECK_FIRMWARE_NIGHTLIES", DEFAULT_CHECK_FIRMWARE_NIGHTLIES
            ),
            DEFAULT_CHECK_FIRMWARE_NIGHTLIES,
        )

    def _nightly_tracking_path(self) -> str:
        """Cache path to the latest firmware-nightly tracking JSON."""
        return self.cache_manager.get_cache_file_path(LATEST_FIRMWARE_NIGHTLY_JSON_FILE)

    def fetch_firmware_nightlies(self) -> List[Dict[str, Any]]:
        """
        Fetch the flat GitHub Contents listing of the rolling firmware-nightly directory.

        Returns an empty list when the feature is disabled (no API call is made)
        or when the listing is genuinely empty (``None`` or ``[]``). Each entry
        preserves the live GitHub Contents API shape (``name``, ``download_url``,
        ``size``, ``type``).

        **Fail-closed policy for malformed source responses:** a non-list
        response or a nonempty list containing any malformed entry (non-dict
        or dict with a non-string ``name``) raises ``ValueError`` rather than
        silently filtering. The orchestrator catches this and marks the run
        ``CHECK_FAILED`` so a corrupt listing is never mistaken for "no
        candidate published yet" (which an empty list represents). This is
        a strict mixed-list fail-closed: one bad entry rejects the entire
        listing, because silently dropping entries could hide a missing
        release manifest and produce an incoherent download set.
        """
        if not self._nightlies_enabled():
            return []
        # firmware-nightly/ is a rolling directory that upstream replaces in place.
        # A cached Contents response can therefore describe a superseded generation,
        # so enabled nightly checks must read the live listing each run.
        contents = self.cache_manager.get_repo_contents(
            FIRMWARE_NIGHTLY_SOURCE_DIR,
            force_refresh=True,
            github_token=self.config.get("GITHUB_TOKEN"),
            allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
        )
        # Validate type before emptiness: a falsy non-list response ("", {},
        # 0, False, (), set()) must NOT be collapsed into a successful empty
        # listing. Only ``None`` and ``[]`` are valid empty source results.
        if contents is None:
            return []
        if not isinstance(contents, list):
            raise ValueError("firmware-nightly source response is not a list")
        if not contents:
            return []
        validated: List[Dict[str, Any]] = []
        for entry in contents:
            if not isinstance(entry, dict):
                raise ValueError(
                    f"firmware-nightly source response has non-dict entry: {entry!r}"
                )
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    "firmware-nightly source response has entry with invalid name"
                )
            validated.append(entry)
        return validated

    @staticmethod
    def parse_nightly_build_id(name: str) -> Optional[str]:
        """
        Extract the immutable build-id (e.g. ``2.8.0.f52e2ea``) from a release-level
        nightly manifest filename (``firmware-<build-id>.json``).

        Returns ``None`` for per-device manifests (``*.mt.json``), firmware zips,
        helper scripts, and any other non-manifest filename.  Matching is
        case-insensitive.
        """
        if not isinstance(name, str) or not name:
            return None
        m = _NIGHTLY_MANIFEST_RX.match(name)
        return m.group(1).lower() if m else None

    @staticmethod
    def validate_nightly_build_id(build_id: Any) -> bool:
        """Return True only when ``build_id`` matches the strict nightly build-id regex.

        Non-writing check used by the retry path to confirm a failed result's
        release tag is a genuine build-id before re-deriving its canonical path.
        """
        return (
            isinstance(build_id, str)
            and _NIGHTLY_BUILD_ID_RX.match(build_id) is not None
        )

    def get_nightly_build_id(self, entries: List[Dict[str, Any]]) -> Optional[str]:
        """
        Scan a nightly listing and return the build-id parsed from the single
        release-level manifest.

        Only entries whose GitHub Contents ``type`` is exactly ``"file"`` can
        supply identity — directories, symlinks, submodules, and unknown types
        are ignored even when their name matches the manifest pattern. A
        non-file manifest-name entry cannot establish build identity.

        Returns ``None`` when no manifest is present **and the listing is
        empty** (no candidate published yet). For a nonempty listing with zero
        valid file manifests, raises ``ValueError`` (malformed generation). For
        multiple unique build-ids, raises ``ValueError`` (ambiguous generation).
        Exact duplicate manifest entries (same build-id) are deduplicated and
        treated as one.
        """
        listing = entries or []
        build_ids: list[str] = []
        seen: set[str] = set()
        for entry in listing:
            if not isinstance(entry, dict):
                continue
            # Only a regular GitHub Contents "file" entry can supply identity.
            # A dir/symlink/submodule/unknown manifest-name entry is ignored.
            if entry.get("type") != "file":
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            build_id = self.parse_nightly_build_id(name)
            if build_id and build_id not in seen:
                seen.add(build_id)
                build_ids.append(build_id)

        if not build_ids:
            if not listing:
                return None
            raise ValueError("firmware-nightly listing has no release-level manifest")
        if len(build_ids) > 1:
            raise ValueError(
                f"firmware-nightly listing has multiple unique build-ids: {build_ids}"
            )
        return build_ids[0]

    def _ensure_nightly_base_dir(self) -> str:
        """Create (if absent) and return ``firmware/nightlies/`` after safety checks."""
        base = self._nightly_root_for_safety()
        firmware_parent = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        for ancestor in (base, firmware_parent, self.download_dir):
            if os.path.islink(ancestor):
                raise ValueError(
                    f"Refusing firmware-nightly path through symlink ancestor: {ancestor}"
                )
        os.makedirs(base, exist_ok=True)
        return base

    def _nightly_root_for_safety(self) -> str:
        """Return the nightly root path without creating it (for safety checks)."""
        return os.path.join(
            self.download_dir, FIRMWARE_DIR_NAME, FIRMWARE_NIGHTLIES_DIR_NAME
        )

    def _resolve_nightly_target(self, build_id: str, name: str, *, create: bool) -> str:
        """
        Unified managed-path resolver for nightly assets.

        Returns the canonical target path
        ``firmware/nightlies/<build_id>/<safe_name>`` after validating every
        managed ancestor. In write mode (``create=True``) the nightly root and
        build directory are created; in non-write mode (``create=False``)
        nothing is created and missing ancestors are allowed (the caller will
        detect the missing file separately), but any **unsafe** condition
        still raises ``ValueError`` so non-write callers never trust a path
        through a symlink or an escaped build directory.

        Safety rules (enforced in both modes):
          - ``build_id`` must match the strict nightly build-id regex;
          - ``DOWNLOAD_DIR``, ``firmware/``, ``firmware/nightlies/`` must not
            be symlinks;
          - the build directory must not be a symlink;
          - the resolved build path must remain under the resolved nightly root
            (containment);
          - ``name`` must be a single safe path component (no separators,
            no ``..``).

        Raises ``ValueError`` on any violation.
        """
        if not isinstance(build_id, str) or not _NIGHTLY_BUILD_ID_RX.match(build_id):
            raise ValueError(f"Unsafe firmware-nightly build_id: {build_id!r}")

        safe_name = self._sanitize_required(name, "nightly asset name")
        # Reject multi-component names (path traversal).
        if safe_name != os.path.basename(safe_name) or safe_name in ("", ".", ".."):
            raise ValueError(
                f"Unsafe firmware-nightly asset name (multi-component): {name!r}"
            )

        root = self._nightly_root_for_safety()
        firmware_parent = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        # Reject any symlink in the managed ancestor chain (download dir,
        # firmware parent, nightly root). Do not follow — that would allow
        # escapes.
        for ancestor in (root, firmware_parent, self.download_dir):
            if os.path.islink(ancestor):
                raise ValueError(
                    f"Refusing firmware-nightly path through symlink ancestor: {ancestor}"
                )

        build_dir = os.path.join(root, build_id)
        if os.path.islink(build_dir):
            raise ValueError(
                f"Refusing firmware-nightly build path that is a symlink: {build_dir}"
            )

        # Containment: the realpath of the build dir must stay under the
        # realpath of the nightly root. In non-write mode the root may not
        # exist yet — compute realpath of the parent chain that does exist.
        if create:
            os.makedirs(root, exist_ok=True)
            real_root = os.path.realpath(root)
            os.makedirs(build_dir, exist_ok=True)
            real_build = os.path.realpath(build_dir)
        else:
            # Non-write: validate containment without creating anything.
            # If the root doesn't exist yet, the build is "missing" (not
            # unsafe) — return the path so the caller can check existence.
            if not os.path.isdir(root):
                return os.path.join(build_dir, safe_name)
            real_root = os.path.realpath(root)
            real_build = os.path.realpath(build_dir)
        if not _is_within_base(real_root, real_build):
            raise ValueError(
                f"Refusing firmware-nightly build path outside managed tree: {build_dir}"
            )

        target_path = os.path.join(build_dir, safe_name)
        # Reject a symlink at the target itself in non-write mode. In write
        # mode the caller (download_nightly_asset / _retry_nightly_failure)
        # is responsible for detecting and removing the symlink before
        # downloading — checking here would prevent that cleanup.
        if not create and os.path.islink(target_path):
            raise ValueError(
                f"Refusing firmware-nightly target that is a symlink: {target_path}"
            )

        return target_path

    def _resolve_nightly_dir(self, build_id: str) -> str:
        """Create (if absent) and safely return ``firmware/nightlies/<build_id>/``."""
        if not isinstance(build_id, str) or not _NIGHTLY_BUILD_ID_RX.match(build_id):
            raise ValueError(f"Unsafe firmware-nightly build_id: {build_id!r}")
        root = self._nightly_root_for_safety()
        firmware_parent = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        for ancestor in (root, firmware_parent, self.download_dir):
            if os.path.islink(ancestor):
                raise ValueError(
                    f"Refusing firmware-nightly path through symlink ancestor: {ancestor}"
                )
        os.makedirs(root, exist_ok=True)
        real_root = os.path.realpath(root)
        build_dir = os.path.join(root, build_id)
        if os.path.islink(build_dir):
            raise ValueError(
                f"Refusing firmware-nightly build path that is a symlink: {build_dir}"
            )
        real_build = os.path.realpath(build_dir)
        if not _is_within_base(real_root, real_build):
            raise ValueError(
                f"Refusing firmware-nightly build path outside managed tree: {build_dir}"
            )
        os.makedirs(build_dir, exist_ok=True)
        return build_dir

    def get_nightly_target_path(
        self, build_id: str, name: str, *, create: bool = False
    ) -> str:
        """
        Resolve the storage path for a nightly asset under
        ``firmware/nightlies/<build_id>/<name>``.  When ``create`` is True the
        build directory is created after passing path-safety validation;
        otherwise the path is computed via the shared resolver without
        creating anything — but symlink/containment/name violations still
        raise ``ValueError`` so non-write callers never trust an unsafe path.
        """
        return self._resolve_nightly_target(build_id, name, create=create)

    def _get_nightly_selection_patterns(self) -> List[str]:
        """Extraction patterns for nightly direct-file selection.

        Resolution distinguishes absent/malformed from explicitly empty:

          - ``EXTRACT_PATTERNS`` is the primary source. When the key is
            missing or its value is ``None`` / an unsupported scalar
            (e.g. ``int``, ``dict``), resolution falls back to
            ``SELECTED_PRERELEASE_ASSETS``.
          - When ``EXTRACT_PATTERNS`` is a valid string / list / tuple /
            set / frozenset that resolves to no nonempty patterns
            (explicit empty), the result is ``[]`` and fallback is
            suppressed — the user explicitly opted out.
          - A nonempty value always overrides the fallback.

        ``SELECTED_FIRMWARE_ASSETS`` (the stable archive key) is NEVER
        used for nightly direct files — it follows a different
        (ZIP-centric) matcher.

        The configured value is never mutated.
        """
        for key in ("EXTRACT_PATTERNS", "SELECTED_PRERELEASE_ASSETS"):
            if key not in self.config:
                continue
            resolved = _resolve_extract_patterns(self.config.get(key))
            if resolved is None:
                logger.warning(
                    "Config key %s has an unsupported type or malformed value "
                    "(type %s); falling back to the next selection key",
                    key,
                    type(self.config.get(key)).__name__,
                )
                continue
            return resolved
        return []

    def get_selected_nightly_assets(
        self, entries: List[Dict[str, Any]], build_id: str
    ) -> List[Dict[str, Any]]:
        """
        Select nightly entries for a single build generation.

        The firmware-nightly directory is a flat repo-prerelease-like direct-file
        listing (per-device ``.uf2``/``.bin``/``-ota.zip``/``.elf``/``.mt.json``
        plus build-agnostic helpers), not a stable architecture-ZIP release.
        Selection therefore uses the shared extraction-pattern matcher
        (:func:`matches_extract_patterns`) with DeviceManager aliases/families,
        the same matcher used by repository prereleases.

        Selection rules (build-aware, deterministic, fail-closed):
          - ``build_id`` must match the strict nightly build-id regex;
            otherwise the result is empty.
          - The release manifest for *this* build (``firmware-<build_id>.json``)
            is included exactly once — but **only when it is a regular
            ``type == "file"`` entry AND at least one eligible non-release
            entry also survives selection**. A non-file manifest (dir,
            symlink, submodule) is never selected and never establishes the
            build companion. A manifest-only OR payload-only result is never
            produced (fail-closed against no-match / excluded-all /
            manifest-as-dir).
          - Only entries whose GitHub Contents ``type`` is exactly ``"file"``
            are eligible. Directories, symlinks, submodules, and unknown
            types are ignored before manifest/pattern/exclude evaluation.
          - Per-device manifests (``*.mt.json``) for the current build are
            included when their filename matches an extraction pattern and is
            not excluded — they carry the per-device file inventory required to
            validate payloads.
          - Any versioned asset whose embedded build token differs from
            ``build_id`` is excluded (stale generation), even if it matches a
            selection pattern.
          - ``EXCLUDE_PATTERNS`` (case-insensitive fnmatch) are applied to every
            non-manifest entry. Typical excludes: ``*.hex`` and ``*rak4631_*``
            (underscore device variants).
          - Build-agnostic helpers (``device-install.sh``, ``mt-esp32-ota.bin``,
            ``littlefs-*``) are included only when an extraction pattern matches.
            ``index.html`` / ``release_notes.md`` / unrelated files are never
            auto-included.
          - Malformed entries (non-dict, non-string name, empty name) are
            ignored.
          - Empty extraction patterns (no ``EXTRACT_PATTERNS`` and no
            ``SELECTED_PRERELEASE_ASSETS``) → empty selection (fail-closed).
            The orchestrator surfaces this as ``CHECK_FAILED``.
          - Results are deduplicated by name and sorted alphabetically.
        """
        if not isinstance(build_id, str) or not _NIGHTLY_BUILD_ID_RX.match(build_id):
            return []

        patterns = self._get_nightly_selection_patterns()
        # Fail-closed: empty patterns never produce a manifest-only selection.
        if not patterns:
            return []

        exclude_patterns = self._get_exclude_patterns()

        # Collect the release manifest (this build) separately from non-release
        # matches so we can fail-closed when no eligible device file survives.
        manifest_entry: Optional[Dict[str, Any]] = None
        non_release: List[Dict[str, Any]] = []
        seen_names: set[str] = set()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name or name in seen_names:
                continue

            # Only regular GitHub Contents "file" entries are eligible — dirs,
            # symlinks, submodules, and unknown types are never manifest/assets.
            if entry.get("type") != "file":
                continue

            # Always include exactly the release manifest for this build
            # (deferred until we know at least one non-release entry also
            # survives — see the manifest-only fail-closed below).
            if self.parse_nightly_build_id(name) == build_id:
                manifest_entry = entry
                seen_names.add(name)
                continue

            # Reject stale versioned assets from a different build generation.
            # This applies to per-device manifests, payloads, and zips alike.
            token_match = _NIGHTLY_BUILD_TOKEN_RX.search(name)
            if token_match and token_match.group(1).lower() != build_id:
                continue

            if exclude_patterns and self._matches_exclude_patterns(
                name, exclude_patterns
            ):
                continue

            if matches_extract_patterns(
                name, patterns, device_manager=self.device_manager
            ):
                non_release.append(entry)
                seen_names.add(name)

        # Fail-closed: selection requires BOTH exactly one file manifest for
        # this build AND ≥1 eligible non-release file. Never manifest-only
        # (no companion payload) and never payload-only (no file manifest —
        # e.g. the manifest is a dir/symlink/submodule or absent entirely).
        if not non_release or manifest_entry is None:
            return []
        selected: List[Dict[str, Any]] = [manifest_entry] + non_release
        selected.sort(key=lambda e: str(e.get("name", "")))
        return selected

    def is_nightly_complete(self, build_id: str) -> bool:
        """
        Lightweight on-disk completeness probe: returns True iff the build
        directory exists and contains at least one non-empty regular file.

        Uses the shared non-writing root/build policy: rejects any symlink
        in the managed ancestor chain (``DOWNLOAD_DIR``, ``firmware/``,
        ``firmware/nightlies/``, build dir), rejects containment escapes,
        and never creates directories. File entries are checked with
        ``follow_symlinks=False`` so a symlink inside the build dir is
        never counted as a real asset.

        The strict selected-set check is performed by
        :meth:`should_process_nightly`, which has the listing in hand.
        """
        if not isinstance(build_id, str) or not _NIGHTLY_BUILD_ID_RX.match(build_id):
            return False
        root = self._nightly_root_for_safety()
        firmware_parent = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        for ancestor in (self.download_dir, firmware_parent, root):
            if os.path.islink(ancestor):
                return False
        build_dir = os.path.join(root, build_id)
        if os.path.islink(build_dir):
            return False
        if not os.path.isdir(build_dir):
            return False
        try:
            real_root = os.path.realpath(root)
            real_build = os.path.realpath(build_dir)
            if not _is_within_base(real_root, real_build):
                return False
        except (OSError, ValueError):
            return False
        try:
            with os.scandir(build_dir) as it:
                for entry in it:
                    if entry.name == LATEST_POINTER_NAME:
                        continue
                    if entry.is_symlink():
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        if entry.stat(follow_symlinks=False).st_size > 0:
                            return True
                    except OSError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError as exc:
            logger.debug("Error scanning nightly build dir %s: %s", build_dir, exc)
            return False
        return False

    def should_download_nightly(self, build_id: str) -> bool:
        """
        Return True when nightlies are enabled AND the supplied build-id differs
        from the tracked one.  A missing or unreadable tracking file is treated
        as "download" (identity unknown).
        """
        if not self._nightlies_enabled() or not build_id:
            return False
        tracking_file = self._nightly_tracking_path()
        try:
            data = self.cache_manager.read_json(tracking_file)
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        if not isinstance(data, dict):
            return True
        tracked = data.get("build_id")
        return tracked != build_id

    def should_process_nightly(
        self,
        entries: List[Dict[str, Any]],
        build_id: str,
        selected: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Decide whether a nightly build should be processed.

        Short-circuits to False when nightlies are disabled or build_id is
        empty.  Otherwise:
          - identity changed (build_id differs from tracked)  -> True
          - identity same, all selected assets fully valid     -> False (skip)
          - identity same, any selected asset missing/unsafe/
            corrupt/hash-mismatched/invalid ZIP or manifest    -> True (backfill)

        Same-identity skip uses the shared :meth:`_validate_nightly_asset`
        against the non-writing managed target path so a tracked build is only
        skipped when every selected asset passes path/content validation.

        ``selected`` — optional precomputed selection list (the exact list
        returned by :meth:`get_selected_nightly_assets` for this build). When
        supplied, the selector is not re-invoked and this list is validated
        as-is, so the orchestrator can compute the set once and reuse it for
        processing, maintenance, retry, and finalization without a config
        reread or reselection in the same run. Omit (``None``) for the
        backwards-compatible recompute-from-entries behavior.
        """
        if not self._nightlies_enabled() or not build_id:
            return False
        tracking_file = self._nightly_tracking_path()
        tracked: Optional[str] = None
        try:
            data = self.cache_manager.read_json(tracking_file)
            if isinstance(data, dict):
                tracked = data.get("build_id")
        except (OSError, ValueError, json.JSONDecodeError):
            tracked = None

        if tracked != build_id:
            return True

        # Same identity: backfill if any selected asset is not fully valid.
        # Reuse the caller-supplied precomputed selection when available so a
        # single run never recomputes/rereads the pattern configuration.
        if selected is None:
            selected = self.get_selected_nightly_assets(entries, build_id)
        for entry in selected:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            try:
                target = self.get_nightly_target_path(build_id, name, create=False)
            except ValueError:
                return True
            ok, _reason = self._validate_nightly_asset(target, name, entry.get("size"))
            if not ok:
                return True
        return False

    def update_nightly_tracking(self, build_id: str) -> bool:
        """
        Persist the supplied build-id to the nightly tracking JSON.  Callers must
        only invoke this after all selected assets have downloaded successfully.
        """
        if not build_id:
            return False
        data = {
            "build_id": build_id,
            "file_type": FILE_TYPE_FIRMWARE_NIGHTLY,
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(self._nightly_tracking_path(), data)

    def _remove_nightly_target_and_hash(self, target_path: str) -> None:
        """Remove a nightly asset target and its hash metadata without following symlinks.

        A symlink at ``target_path`` (dangling or pointing outside the build
        directory) is unlinked directly via ``os.unlink`` — the link itself is
        removed, the external target is never touched or followed. After the
        symlink (if any) is gone, the shared ``_prepare_for_redownload`` helper
        removes any remaining regular file plus the current and legacy hash
        sidecars and orphaned temp files so the next download starts clean.

        This is the nightly-scoped cleanup seam: it does not alter the behavior
        of ``_prepare_for_redownload`` for non-nightly callers, and it closes
        the dangling-symlink gap (``_prepare_for_redownload`` alone uses
        ``os.path.exists`` which follows symlinks and reports False for a
        dangling link, leaving the link in place).
        """
        if os.path.islink(target_path):
            try:
                os.unlink(target_path)
                logger.debug(
                    "Removed nightly symlink target (link only): %s", target_path
                )
            except OSError as exc:
                logger.warning(
                    "Could not remove nightly symlink target %s: %s",
                    target_path,
                    exc,
                )
        _prepare_for_redownload(target_path)

    def _validate_nightly_asset(
        self, target_path: str, name: str, expected_size: Any
    ) -> Tuple[bool, str]:
        """
        Validate a nightly asset on disk. Shared by the skip, fresh-download,
        and retry paths so they apply identical rules.

        Rules (all must hold):
          - target must be a regular file and not a symlink;
          - when ``expected_size`` is a positive int, the on-disk size must
            match exactly;
          - existing integrity / hash verification must pass;
          - ZIP archives must pass ZIP integrity;
          - release-level manifests (``firmware-<build>.json``) must be valid
            JSON objects;
          - per-device manifests (``*.mt.json``) must be valid JSON objects with
            ``version`` equal to the build-id embedded in the filename, a
            nonempty string ``platformioTarget``, and a ``files`` list whose
            entries are records with nonempty string ``name``.

        Returns ``(True, "")`` on success, otherwise ``(False, reason)``. The
        caller is responsible for removing the target and any stored hash when
        validation fails.
        """
        if os.path.islink(target_path) or not os.path.isfile(target_path):
            return False, "target is not a regular file (symlink or missing)"

        try:
            actual_size = os.path.getsize(target_path)
        except OSError as exc:
            return False, f"could not stat target: {exc}"

        if isinstance(expected_size, int) and expected_size > 0:
            if actual_size != expected_size:
                return False, (
                    f"size mismatch: expected {expected_size}, got {actual_size}"
                )

        lower = name.lower()

        if lower.endswith(".zip"):
            if not is_zip_intact(target_path):
                return False, "ZIP integrity check failed"

        # Release-level manifest: firmware-<build>.json (no .mt.json suffix).
        if self.parse_nightly_build_id(name) is not None:
            try:
                with open(target_path, "r", encoding="utf-8") as manifest_file:
                    data = json.load(manifest_file)
                if not isinstance(data, dict):
                    return False, "release manifest is not a JSON object"
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                return False, f"release manifest is not valid JSON: {exc}"
        elif lower.endswith(FIRMWARE_MANIFEST_EXTENSION):
            # Per-device manifest (*.mt.json): validate structure + identity.
            ok, reason = self._validate_nightly_device_manifest(target_path, name)
            if not ok:
                return False, reason

        if not verify_file_integrity(target_path):
            return False, "hash/integrity verification failed"

        return True, ""

    def _validate_nightly_device_manifest(
        self, target_path: str, name: str
    ) -> Tuple[bool, str]:
        """Validate a per-device nightly ``*.mt.json`` manifest on disk.

        Rules (all must hold):
          - parses as a JSON object;
          - the filename embeds exactly one distinct nightly build token
            (``<maj>.<min>.<patch>.<hex6+>``). Zero tokens, two or more
            distinct tokens, or any malformed token is rejected — the
            manifest's identity is ambiguous otherwise;
          - ``version`` is a nonempty string equal to that build token
            (case-insensitive);
          - ``platformioTarget`` is a nonempty string;
          - ``files`` is a list whose every entry is a record (dict) with a
            nonempty string ``name``. An empty list is allowed (a device may
            publish a manifest before its payloads).

        Returns ``(True, "")`` on success, otherwise ``(False, reason)``.
        """
        try:
            with open(target_path, "r", encoding="utf-8") as manifest_file:
                data = json.load(manifest_file)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            return False, f"device manifest is not valid JSON: {exc}"
        if not isinstance(data, dict):
            return False, "device manifest is not a JSON object"

        # Exactly one distinct build token in the filename. Zero / two-or-more
        # distinct tokens mean the manifest cannot be reliably identified.
        tokens = {m.group(1).lower() for m in _NIGHTLY_BUILD_TOKEN_RX.finditer(name)}
        if len(tokens) != 1:
            return False, (
                f"device manifest filename must embed exactly one nightly "
                f"build token; found {sorted(tokens)}"
            )
        expected_version = next(iter(tokens))

        version = data.get("version")
        if not isinstance(version, str) or not version:
            return False, "device manifest version is missing or not a nonempty string"
        if version.lower() != expected_version:
            return False, (
                f"device manifest version {version!r} != build-id {expected_version!r}"
            )

        platformio_target = data.get("platformioTarget")
        if not isinstance(platformio_target, str) or not platformio_target:
            return False, (
                "device manifest platformioTarget is missing or not a nonempty string"
            )

        files = data.get("files")
        if not isinstance(files, list):
            return False, "device manifest files is not a list"
        for idx, record in enumerate(files):
            if not isinstance(record, dict):
                return False, f"device manifest files[{idx}] is not a record"
            record_name = record.get("name")
            if not isinstance(record_name, str) or not record_name:
                return False, (
                    f"device manifest files[{idx}] name is missing or not a nonempty string"
                )

        return True, ""

    def download_nightly_asset(
        self, entry: Dict[str, Any], build_id: str
    ) -> DownloadResult:
        """
        Download a single firmware-nightly entry into the build's directory and
        return a structured DownloadResult.

        Path-safety, size, ZIP, manifest-JSON, and hash integrity are all
        validated through :meth:`_validate_nightly_asset`. On any validation
        failure the target and its stored hash are removed and the result is
        marked non-retryable (``ERROR_TYPE_VALIDATION``). Network/download
        failures remain retryable. The executable bit for ``*.sh`` payloads is
        applied only after validation succeeds.
        """
        name = str(entry.get("name") or "")
        url = entry.get("download_url") or entry.get("browser_download_url")
        size = entry.get("size")
        nightly_root = os.path.join(
            self.download_dir, FIRMWARE_DIR_NAME, FIRMWARE_NIGHTLIES_DIR_NAME
        )
        if not name or not url:
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=nightly_root,
                error_message="nightly entry missing name or download_url",
                download_url=str(url) if url else None,
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=False,
                error_type=ERROR_TYPE_VALIDATION,
            )

        try:
            target_path = self.get_nightly_target_path(build_id, name, create=True)
        except ValueError as exc:
            logger.error("Unsafe firmware-nightly path for %s: %s", name, exc)
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=nightly_root,
                error_message=f"unsafe nightly path: {exc}",
                download_url=str(url),
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=False,
                error_type=ERROR_TYPE_VALIDATION,
            )

        # Reject any pre-existing symlink target before touching it. Unlink the
        # link directly (never following it) so a dangling or escaping symlink is
        # gone before any download could write through it.
        if os.path.islink(target_path):
            self._remove_nightly_target_and_hash(target_path)
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=target_path,
                error_message="nightly target is a symlink; refused and removed",
                download_url=str(url),
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=False,
                error_type=ERROR_TYPE_VALIDATION,
            )

        # Skip if already present and fully valid.
        if os.path.exists(target_path):
            ok, reason = self._validate_nightly_asset(target_path, name, size)
            if ok:
                logger.debug("Nightly asset already present and valid: %s", name)
                return self.create_download_result(
                    success=True,
                    release_tag=build_id,
                    file_path=target_path,
                    download_url=str(url),
                    file_size=size,
                    file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                    was_skipped=True,
                )
            logger.warning(
                "Existing nightly asset %s failed validation (%s); re-downloading",
                name,
                reason,
            )
            self._remove_nightly_target_and_hash(target_path)

        try:
            downloaded = download_file_with_retry(str(url), target_path)
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
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=target_path,
                error_message=str(exc),
                download_url=str(url),
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=is_retryable,
                error_type=error_type,
            )

        if not downloaded:
            logger.error("Download failed for nightly asset %s", name)
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=target_path,
                error_message="download_file_with_retry returned False",
                download_url=str(url),
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=True,
                error_type=ERROR_TYPE_NETWORK,
            )

        # Deterministic post-download validation. A failure here means the
        # response itself was wrong; remove the bad file + stored hash and
        # treat it as non-retryable so a retry cannot silently replace it.
        ok, reason = self._validate_nightly_asset(target_path, name, size)
        if not ok:
            self._remove_nightly_target_and_hash(target_path)
            logger.error(
                "Nightly asset %s failed post-download validation: %s", name, reason
            )
            return self.create_download_result(
                success=False,
                release_tag=build_id,
                file_path=target_path,
                error_message=f"nightly validation failed: {reason}",
                download_url=str(url),
                file_size=size,
                file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
                is_retryable=False,
                error_type=ERROR_TYPE_VALIDATION,
            )

        # Executable bit only after the file has been validated.
        if name.lower().endswith(".sh") and os.name != "nt":
            try:
                os.chmod(target_path, EXECUTABLE_PERMISSIONS)
            except OSError:
                pass
        logger.info("Downloaded nightly asset %s", name)
        return self.create_download_result(
            success=True,
            release_tag=build_id,
            file_path=target_path,
            download_url=str(url),
            file_size=size,
            file_type=FILE_TYPE_FIRMWARE_NIGHTLY,
        )

    def update_latest_pointer_for_nightly(self, build_id: str) -> bool:
        """Best-effort update of the ``latest`` pointer inside ``nightlies/``."""
        if not coerce_bool(
            self.config.get("CREATE_LATEST_SYMLINKS", DEFAULT_CREATE_LATEST_SYMLINKS),
            DEFAULT_CREATE_LATEST_SYMLINKS,
        ):
            return False
        try:
            parent = self._ensure_nightly_base_dir()
            return update_latest_pointer(parent, build_id, LATEST_POINTER_NAME)
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Skipping firmware-nightly latest pointer: %s", exc)
            return False

    def cleanup_superseded_nightlies(
        self, current_build_id: Optional[str] = None
    ) -> int:
        """
        Remove old nightly build directories under ``firmware/nightlies/``,
        keeping an exact maximum of ``FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP``
        (floor 1). The current build always occupies one slot.

        Selection: at most ``keep_limit`` managed build directories survive,
        selected by deterministic directory mtime (newest first) with a stable
        name tiebreak — never hash chronology. When ``current_build_id`` is
        supplied it reserves one slot; at most ``keep_limit - 1`` other
        directories are retained alongside it.

        Safety rules:
          - never follow the ``nightlies/`` root if it is a symlink;
          - never delete the ``latest`` pointer or any non-build-id directory;
          - never follow per-entry symlinks (uses ``_safe_rmtree``);
          - never delete ``current_build_id``;
          - remove an invalid managed ``latest`` symlink via
            ``remove_latest_pointer`` when its target is gone.

        Returns the number of build directories removed.
        """
        base = os.path.join(
            self.download_dir, FIRMWARE_DIR_NAME, FIRMWARE_NIGHTLIES_DIR_NAME
        )
        firmware_parent = os.path.join(self.download_dir, FIRMWARE_DIR_NAME)
        # Validate the full non-writing managed ancestor chain before any
        # scan/delete/latest mutation. Reject any symlink, containment
        # ambiguity, or missing unsafe root. Create nothing; return 0 and
        # do not alter latest.
        for ancestor in (self.download_dir, firmware_parent, base):
            if os.path.islink(ancestor):
                return 0
        if not os.path.isdir(base):
            return 0
        try:
            real_download = os.path.realpath(self.download_dir)
            real_base = os.path.realpath(base)
            if not _is_within_base(real_download, real_base):
                return 0
        except (OSError, ValueError):
            return 0

        keep_limit = self.config.get(
            "FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP",
            DEFAULT_FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP,
        )
        try:
            keep_limit = int(keep_limit)
        except (TypeError, ValueError):
            keep_limit = DEFAULT_FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP
        if keep_limit < 1:
            keep_limit = 1

        # Collect managed build directories with deterministic (mtime, name)
        # ordering metadata. Non-build directories and symlinks are preserved.
        candidates: List[Tuple[float, str]] = []
        try:
            with os.scandir(base) as it:
                for entry in it:
                    if entry.is_symlink():
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if not _NIGHTLY_BUILD_ID_RX.match(entry.name):
                        continue
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    candidates.append((mtime, entry.name))
        except (FileNotFoundError, NotADirectoryError):
            return 0
        except OSError as exc:
            logger.debug("Error scanning nightlies dir %s: %s", base, exc)
            return 0

        # Sort newest mtime first; stable name tiebreak (descending) so the
        # selection is deterministic regardless of hash chronology.
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

        current = (
            current_build_id
            if isinstance(current_build_id, str)
            and _NIGHTLY_BUILD_ID_RX.match(current_build_id)
            else None
        )

        other_budget = keep_limit - 1 if current else keep_limit
        other_budget = max(0, other_budget)

        keep_names: set[str] = set()
        if current:
            keep_names.add(current)
        kept_others = 0
        for _mtime, name in candidates:
            if name == current:
                continue
            if kept_others < other_budget:
                keep_names.add(name)
                kept_others += 1

        removed = 0
        for _mtime, name in candidates:
            if name in keep_names:
                continue
            path = os.path.join(base, name)
            if _safe_rmtree(path, base, name):
                removed += 1
                logger.info("Removed old nightly build: %s", name)

        # Always validate the managed latest pointer against the retained set,
        # even when zero directories were removed or no candidates existed.
        # Removes unsafe/dangling/unretained symlinks; preserves a valid
        # retained target and any non-symlink latest entry. Never followed.
        retained = {name for _mtime, name in candidates if name in keep_names}
        self._cleanup_invalid_nightly_latest_pointer(base, retained)

        return removed

    def _cleanup_invalid_nightly_latest_pointer(
        self, base_dir: str, retained_names: set[str]
    ) -> None:
        """Remove the managed ``latest`` symlink when its target is missing or unsafe.

        A non-symlink ``latest`` entry is always preserved. A symlink is removed
        when its target is unreadable, dangling (target directory absent), or not
        in the retained set. The link itself is removed; external targets are
        never followed.
        """
        link_path = os.path.join(base_dir, LATEST_POINTER_NAME)
        if not os.path.islink(link_path):
            return
        try:
            target = os.readlink(link_path)
        except OSError:
            remove_latest_pointer(base_dir)
            return
        if target not in retained_names:
            remove_latest_pointer(base_dir)
            return
        # Target is retained — still confirm the directory actually exists so a
        # dangling symlink to a deleted-but-retained name is repaired.
        if not os.path.isdir(os.path.join(base_dir, target)):
            remove_latest_pointer(base_dir)

    def repair_nightly_executable_metadata(
        self,
        build_id: str,
        entries: List[Dict[str, Any]],
        selected: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        Best-effort repair of the executable bit on ``*.sh`` nightly assets
        that are already present and fully valid. Does NOT redownload — the
        file is only chmodded when it passes content validation. Invalid or
        missing assets are left untouched. Windows is unchanged.

        Only assets in the build-aware selected set (same generation as
        ``build_id``) are examined, so stale or unmanaged files are never
        touched.

        ``selected`` — optional precomputed selection list (the exact list
        returned by :meth:`get_selected_nightly_assets`). When supplied, the
        selector is not re-invoked, so the orchestrator reuses one
        precomputed set through process/maintenance/retry without a config
        reread. Omit (``None``) for the backwards-compatible
        recompute-from-entries behavior.

        Returns the number of assets repaired.
        """
        if os.name == "nt":
            return 0
        if selected is None:
            selected = self.get_selected_nightly_assets(entries, build_id)
        repaired = 0
        for entry in selected:
            name = entry.get("name")
            if not isinstance(name, str) or not name.lower().endswith(".sh"):
                continue
            try:
                target = self.get_nightly_target_path(build_id, name, create=False)
            except ValueError:
                continue
            ok, _reason = self._validate_nightly_asset(target, name, entry.get("size"))
            if not ok:
                continue
            try:
                current_mode = os.stat(target).st_mode
                if not (current_mode & 0o111):
                    os.chmod(target, EXECUTABLE_PERMISSIONS)
                    repaired += 1
            except OSError:
                continue
        return repaired

    def recreate_latest_pointer_for_nightly(self, build_id: str) -> bool:
        """
        Recreate the ``latest`` pointer for a build when it is missing or
        points to a different build. Preserves a non-symlink ``latest`` entry.

        Returns True when the pointer was created or already correct.
        """
        if not coerce_bool(
            self.config.get("CREATE_LATEST_SYMLINKS", DEFAULT_CREATE_LATEST_SYMLINKS),
            DEFAULT_CREATE_LATEST_SYMLINKS,
        ):
            return False
        try:
            parent = self._ensure_nightly_base_dir()
        except (OSError, ValueError) as exc:
            logger.debug("Skipping firmware-nightly latest pointer repair: %s", exc)
            return False
        link_path = os.path.join(parent, LATEST_POINTER_NAME)
        if os.path.islink(link_path):
            try:
                target = os.readlink(link_path)
            except OSError:
                remove_latest_pointer(parent)
                target = None
            if target == build_id:
                return True
            remove_latest_pointer(parent)
        elif os.path.exists(link_path):
            # Non-symlink latest: preserve, do not overwrite.
            return False
        try:
            return update_latest_pointer(parent, build_id, LATEST_POINTER_NAME)
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Failed to recreate firmware-nightly latest pointer: %s", exc)
            return False
