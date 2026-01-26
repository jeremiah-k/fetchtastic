"""
Meshtastic Android App Downloader

This module implements the specific downloader for Meshtastic Android APK files.
"""

import fnmatch
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    ANDROID_RELEASE_HISTORY_JSON_FILE,
    APK_PRERELEASES_DIR_NAME,
    APKS_DIR_NAME,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    ERROR_TYPE_FILESYSTEM,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_VALIDATION,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    GITHUB_MAX_PER_PAGE,
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    MESHTASTIC_ANDROID_RELEASES_URL,
    RELEASE_SCAN_COUNT,
    RELEASES_CACHE_EXPIRY_HOURS,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request, matches_selected_patterns

from .base import BaseDownloader
from .cache import CacheManager
from .files import (
    _safe_rmtree,
    _sanitize_path_component,
)
from .interfaces import Asset, DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .release_history import ReleaseHistoryManager
from .version import VersionManager


class MeshtasticAndroidAppDownloader(BaseDownloader):
    """
    Downloader for Meshtastic Android APK files.

    This class handles:
    - Fetching Android APK releases from GitHub
    - Downloading APK files
    - Managing Android-specific version tracking
    - Handling Android prereleases
    - Cleaning up old Android versions
    """

    def __init__(self, config: Dict[str, Any], cache_manager: "CacheManager"):
        """
        Create and configure the Meshtastic Android APK downloader.

        Parameters:
            config (dict): Downloader configuration dictionary used to set behavior and paths.
            cache_manager (CacheManager): Cache manager used to read/write cached API responses and tracking/metadata files.
        """
        super().__init__(config)
        self.cache_manager = cache_manager
        self.android_releases_url = MESHTASTIC_ANDROID_RELEASES_URL
        self.latest_release_file = LATEST_ANDROID_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_ANDROID_PRERELEASE_JSON_FILE
        self.latest_release_path = self.cache_manager.get_cache_file_path(
            self.latest_release_file
        )
        self.release_history_path = self.cache_manager.get_cache_file_path(
            ANDROID_RELEASE_HISTORY_JSON_FILE
        )
        self.release_history_manager = ReleaseHistoryManager(
            self.cache_manager, self.release_history_path
        )

    def get_target_path_for_release(
        self,
        release_tag: str,
        file_name: str,
        is_prerelease: Optional[bool] = None,
        release: Optional[Release] = None,
    ) -> str:
        """
        Compute the filesystem path for an APK asset and ensure the corresponding release directory exists.

        Sanitizes inputs and places prerelease APKs under the prerelease APKs subdirectory when `is_prerelease` is True or inferred; creates the release version directory if it does not exist.

        Parameters:
            is_prerelease (Optional[bool]): If provided, override inference and use the specified prerelease status to choose the base directory. Note: This parameter is only used when `release` is None; when a Release object is provided, prerelease status is determined from the Release object itself.
            release (Optional[Release]): Should be provided when ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES is enabled for full releases to correctly detect channel suffixes.

        Returns:
            str: Filesystem path to the asset file within the (possibly created) release directory.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        # Use Release object for comprehensive prerelease detection when available
        if release is not None:
            safe_release = self._get_storage_tag_for_release(release)
            is_prerelease = self._is_android_prerelease(release)
        else:
            # Infer prerelease status from tag name when Release object not available
            if is_prerelease is None:
                is_prerelease = _is_apk_prerelease_by_name(
                    release_tag
                ) or self.version_manager.is_prerelease_version(release_tag)

        base_dir = (
            self._get_prerelease_base_dir()
            if is_prerelease
            else os.path.join(self.download_dir, APKS_DIR_NAME)
        )
        version_dir = os.path.join(base_dir, safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def _get_prerelease_base_dir(self) -> str:
        """
        Return the absolute path to the prerelease APKs directory, creating the directory if it does not exist.

        Returns:
            str: Absolute filesystem path to the prerelease APKs directory under the APK downloads directory.
        """
        prerelease_dir = os.path.join(
            self.download_dir, APKS_DIR_NAME, APK_PRERELEASES_DIR_NAME
        )
        os.makedirs(prerelease_dir, exist_ok=True)
        return prerelease_dir

    def _is_android_prerelease(self, release: Release) -> bool:
        """
        Determine if an Android release is a prerelease.

        Parameters:
            release (Release): Release object to check.

        Returns:
            bool: True if the release is a prerelease, False otherwise.
        """
        return (
            release.prerelease
            or _is_apk_prerelease_by_name(release.tag_name)
            or self.version_manager.is_prerelease_version(release.tag_name)
        )

    def _get_storage_tag_for_release(self, release: Release) -> str:
        """
        Compute storage tag for an APK release.

        APK releases do not use channel suffixes; returns only the sanitized tag name.

        Parameters:
            release (Release): Release object containing tag_name and other metadata.

        Returns:
            str: Sanitized storage tag without any channel or revoked suffixes.
        """
        return self._sanitize_required(release.tag_name, "release tag")

    def update_release_history(
        self, releases: List[Release], *, log_summary: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Update the on-disk release history cache and optionally log status summaries.

        Parameters:
            releases (List[Release]): Releases to record in history.
            log_summary (bool): When True, emit summary logs for revoked/removed releases.

        Returns:
            Optional[Dict[str, Any]]: The updated history data, or None when no releases
                were supplied.
        """
        if not releases:
            return None
        stable_releases = [r for r in releases if not self._is_android_prerelease(r)]
        if not stable_releases:
            return None
        history = self.release_history_manager.update_release_history(stable_releases)
        if log_summary:
            self.release_history_manager.log_release_status_summary(
                history, label="Android"
            )
        return history

    def format_release_log_suffix(self, release: Release) -> str:
        """
        Create a log suffix describing the release channel and revoked status when available.

        Returns:
            suffix (str): Formatted log suffix containing channel and revoked information, or an empty string if no contextual info is available.
        """
        label = self.release_history_manager.format_release_label(
            release, include_channel=False, include_status=True
        )
        return label[len(release.tag_name) :]

    def ensure_release_notes(self, release: Release) -> Optional[str]:
        """
        Write the release notes for the given release into the appropriate APK directory and return the notes file path.

        Parameters:
            release (Release): Release metadata containing tag_name and body used to determine the notes filename and content.

        Returns:
            Optional[str]: Path to the release notes file if written or already present, `None` if the tag is unsafe or notes cannot be determined.
        """
        safe_release = _sanitize_path_component(release.tag_name)
        if safe_release is None:
            logger.warning(
                "Skipping release notes for unsafe Android tag: %s", release.tag_name
            )
            return None

        is_prerelease = self._is_android_prerelease(release)

        storage_tag = self._get_storage_tag_for_release(release)

        base_dir = (
            self._get_prerelease_base_dir()
            if is_prerelease
            else os.path.join(self.download_dir, APKS_DIR_NAME)
        )
        release_dir = os.path.join(base_dir, storage_tag)
        return self._write_release_notes(
            release_dir=release_dir,
            release_tag=release.tag_name,
            body=release.body,
            base_dir=base_dir,
        )

    def _is_asset_complete_for_target(self, target_path: str, asset: Asset) -> bool:
        """
        Determine whether the asset file at target_path exists and is valid for the provided Asset.

        Performs the applicable checks: file existence, file size equals Asset.size (when provided), verifier integrity check, and ZIP integrity validation for files ending with `.zip`.

        Returns:
            True if all applicable checks pass, False otherwise.
        """
        if not os.path.exists(target_path):
            return False

        if asset.size and self.file_operations.get_file_size(target_path) != asset.size:
            return False

        if not self.verify(target_path):
            return False

        if target_path.lower().endswith(".zip") and not self._is_zip_intact(
            target_path
        ):
            return False

        return True

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Retrieve Android APK releases from GitHub and construct Release objects populated with their APK assets.

        Respects cached responses and the configured scan window; when no `limit` is provided the function expands its scan to collect a configured minimum number of stable releases.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return. If `None`, the function uses configured scan parameters to determine how many releases to fetch.

        Returns:
            List[Release]: Release objects populated with their APK Asset entries; returns an empty list on error or if no valid releases are found.
        """
        try:
            max_scan = GITHUB_MAX_PER_PAGE
            min_stable_releases = int(
                self.config.get("ANDROID_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT)
            )
            scan_count = min(max_scan, max(min_stable_releases * 2, RELEASE_SCAN_COUNT))
            if limit is not None:
                if limit <= 0:
                    return []
                scan_count = min(max_scan, limit)

            while True:
                params = {"per_page": scan_count}
                url_key = self.cache_manager.build_url_cache_key(
                    self.android_releases_url, params
                )
                releases_data = self.cache_manager.read_releases_cache_entry(
                    url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
                )

                if releases_data is None:
                    response = make_github_api_request(
                        self.android_releases_url,
                        self.config.get("GITHUB_TOKEN"),
                        allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                        params=params,
                    )
                    releases_data = response.json() if hasattr(response, "json") else []
                    if isinstance(releases_data, list):
                        logger.debug(
                            "Cached %d releases for %s (fetched from API)",
                            len(releases_data),
                            self.android_releases_url,
                        )
                    self.cache_manager.write_releases_cache_entry(
                        url_key,
                        releases_data if isinstance(releases_data, list) else [],
                    )

                if releases_data is None or not isinstance(releases_data, list):
                    logger.error("Invalid releases data received from GitHub API")
                    return []

                releases: List[Release] = []
                stable_count = 0
                for release_data in releases_data:
                    # Filter out releases without assets
                    if not release_data.get("assets"):
                        continue

                    tag_name = release_data.get("tag_name", "")
                    if not _is_supported_android_release(
                        tag_name, version_manager=self.version_manager
                    ):
                        logger.debug(
                            "Skipping legacy Android release %s (pre-2.7.0 tagging scheme)",
                            tag_name or "<unknown>",
                        )
                        continue

                    release = Release(
                        tag_name=tag_name,
                        prerelease=_is_apk_prerelease(release_data),
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
                    if not release.prerelease:
                        stable_count += 1

                    # Respect limit if specified
                    if limit is not None and len(releases) >= limit:
                        break

                if limit is not None:
                    return releases

                if (
                    stable_count >= min_stable_releases
                    or len(releases_data) < scan_count
                ):
                    return releases

                if scan_count >= max_scan:
                    logger.debug(
                        "Reached maximum APK scan window (%d) without finding %d stable releases; proceeding with %d stable release(s).",
                        max_scan,
                        min_stable_releases,
                        stable_count,
                    )
                    return releases

                scan_count = min(max_scan, scan_count * 2)

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
        ) as exc:
            logger.exception("Error fetching Android releases: %s", exc)
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Get APK assets included in the given release.

        Returns:
            List[Asset]: Assets from the release whose names end with ".apk" (case-insensitive).
        """
        # Filter for APK files only
        assets = release.assets or []
        return [asset for asset in assets if asset.name.lower().endswith(".apk")]

    def get_download_url(self, asset: Asset) -> str:
        """
        Get the direct download URL for the given asset.

        Returns:
            str: The asset's direct download URL.
        """
        return asset.download_url

    def should_download_asset(self, asset_name: str) -> bool:
        """
        Determine if an APK asset should be downloaded based on configured include and exclude patterns.

        Exclude patterns take precedence over include (selected) patterns. If no selected patterns are configured, the asset is allowed.

        Returns:
            `True` if the asset should be downloaded, `False` otherwise.
        """
        selected = self.config.get("SELECTED_APK_ASSETS") or []
        exclude = self._get_exclude_patterns()

        if exclude and any(
            fnmatch.fnmatch(asset_name.lower(), pat.lower()) for pat in exclude
        ):
            return False

        if not selected:
            return True

        return matches_selected_patterns(asset_name, selected)

    def download_apk(self, release: Release, asset: Asset) -> DownloadResult:
        """
        Download and verify the APK asset for the given release.

        Attempts to reuse an existing, validated file when present; otherwise downloads the asset, verifies the saved file, and removes it on verification failure.

        Parameters:
            release (Release): Release metadata (used for tag, prerelease flag, and publish data).
            asset (Asset): Asset metadata including name, download_url, and expected size.

        Returns:
            DownloadResult: Success entries include `file_path`, `download_url`, `file_size`, `file_type`, and `was_skipped` when applicable; failure entries include `error_message`, `error_type`, and `is_retryable`.
        """
        target_path: Optional[str] = None
        file_type = (
            FILE_TYPE_ANDROID_PRERELEASE if release.prerelease else FILE_TYPE_ANDROID
        )
        try:
            # Get target path for the APK
            target_path = self.get_target_path_for_release(
                release.tag_name,
                asset.name,
                is_prerelease=release.prerelease,
                release=release,
            )

            # Check if we need to download
            if self._is_asset_complete_for_target(target_path, asset):
                logger.debug(f"APK {asset.name} already exists and is complete")
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type=file_type,
                    was_skipped=True,
                )

            # Download the APK
            success = self.download(asset.download_url, target_path)

            if success:
                # Verify the download
                if self.verify(target_path):
                    logger.info(f"Successfully downloaded and verified {asset.name}")
                    return self.create_download_result(
                        success=True,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type=file_type,
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
                        file_type=file_type,
                        is_retryable=True,
                        error_type=ERROR_TYPE_VALIDATION,
                    )
            else:
                logger.error(f"Download failed for {asset.name}")
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    error_message="download_file_with_retry returned False",
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type=file_type,
                    is_retryable=True,
                    error_type=ERROR_TYPE_NETWORK,
                )

        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.exception("Error downloading APK %s: %s", asset.name, exc)
            safe_path = target_path or os.path.join(self.download_dir, APKS_DIR_NAME)
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
                file_path=str(Path(safe_path)),
                error_message=str(exc),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type=file_type,
                is_retryable=is_retryable,
                error_type=error_type,
            )

    def is_release_complete(self, release: Release) -> bool:
        """
        Check whether all APK assets selected for the given release exist on disk and match their expected sizes.

        Parameters:
            release (Release): Release whose APK assets are checked. Only assets that pass the downloader's selection rules are considered.

        Returns:
            `true` if all selected assets are present and their file sizes equal the assets' expected sizes, `false` otherwise.
        """
        safe_tag = self._get_storage_tag_for_release(release)

        version_dir = os.path.join(self.download_dir, APKS_DIR_NAME, safe_tag)
        if not os.path.isdir(version_dir):
            return False

        expected_assets = [
            asset for asset in release.assets if self.should_download_asset(asset.name)
        ]

        if not expected_assets:
            return False

        for asset in expected_assets:
            asset_path = os.path.join(version_dir, asset.name)
            if not os.path.exists(asset_path):
                return False
            try:
                if os.path.getsize(asset_path) != asset.size:
                    return False
            except (OSError, TypeError):
                return False
        return True

    def cleanup_old_versions(
        self,
        keep_limit: int,
        cached_releases: Optional[List[Release]] = None,
        keep_last_beta: bool = False,
    ) -> None:
        """
        Remove older Android APK version directories while preserving a configured number of recent versions.

        Parameters:
            keep_limit (int): Number of most-recent version directories to retain.
            cached_releases (Optional[List[Release]]): Optional list of releases to use instead of fetching current releases.
            keep_last_beta (bool): Ignored for APK cleanup; present only for signature compatibility.
        """
        try:
            del keep_last_beta  # intentionally unused (signature compatibility)
            releases = cached_releases or self.get_releases()
            if not releases:
                return
            self.cleanup_prerelease_directories(
                cached_releases=releases, keep_limit_override=keep_limit
            )
        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.error("Error cleaning up old Android versions: %s", exc)

    def cleanup_prerelease_directories(
        self,
        cached_releases: Optional[List[Release]] = None,
        keep_limit_override: Optional[int] = None,
    ) -> None:
        """
        Ensure APK version directories are organized and remove filesystem entries that are not part of the expected stable or prerelease sets.

        Scans the APK root and the prerelease subdirectory, preserving symlinks and any entries whose sanitized tag names match the expected stable or prerelease sets derived from `cached_releases`. No filesystem changes are made if `cached_releases` is None/empty, the APK root is missing, or there are no stable releases. The number of stable versions retained is determined by `keep_limit_override` when provided, otherwise by the `ANDROID_VERSIONS_TO_KEEP` configuration value.

        Parameters:
            cached_releases (Optional[List[Release]]): Releases used to compute which stable and prerelease directories should be retained; if None or empty, the method returns without modifying the filesystem.
            keep_limit_override (Optional[int]): If provided, overrides the configured number of stable versions to keep (must be >= 0); non-integer or invalid values fall back to the default keep value.
        """
        try:
            if not cached_releases:
                return

            android_dir = os.path.join(self.download_dir, APKS_DIR_NAME)
            if not os.path.exists(android_dir):
                return

            prerelease_dir = os.path.join(android_dir, APK_PRERELEASES_DIR_NAME)
            raw_keep_limit = (
                keep_limit_override
                if keep_limit_override is not None
                else self.config.get(
                    "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
                )
            )
            try:
                keep_limit = max(0, int(raw_keep_limit))
            except (TypeError, ValueError):
                keep_limit = int(DEFAULT_ANDROID_VERSIONS_TO_KEEP)
            stable_releases = sorted(
                [release for release in cached_releases if not release.prerelease],
                key=lambda release: self.version_manager.get_release_tuple(
                    release.tag_name
                )
                or (),
                reverse=True,
            )
            if not stable_releases:
                logger.debug(
                    "Skipping APK cleanup because no stable releases are available."
                )
                return
            prerelease_releases = self.handle_prereleases(cached_releases)

            def _build_expected_set(
                releases: List[Release], release_label: str
            ) -> set[str]:
                """
                Builds the set of filesystem-safe release directory names from a list of Release objects.

                Parameters:
                    releases (List[Release]): Releases whose tag_name values will be sanitized and included.
                    release_label (str): Human-readable label used in warning messages when a tag_name is unsafe.

                Returns:
                    set[str]: A set of sanitized tag strings suitable as directory names; releases with unsafe tags are skipped and logged.
                """
                expected: set[str] = set()
                for release in releases:
                    safe_tag = _sanitize_path_component(release.tag_name)
                    if safe_tag is None:
                        logger.warning(
                            "Skipping unsafe %s tag during cleanup: %s",
                            release_label,
                            release.tag_name,
                        )
                        continue
                    expected.add(safe_tag)
                return expected

            expected_stable = _build_expected_set(
                stable_releases[:keep_limit], "release"
            )
            expected_prerelease = _build_expected_set(prerelease_releases, "prerelease")

            if not expected_stable and keep_limit > 0:
                logger.warning(
                    "Skipping APK cleanup: no safe release tags found to keep."
                )
                return

            def _remove_unexpected_entries(
                base_dir: str,
                allowed: set[str],
                entries: Optional[List[os.DirEntry[str]]] = None,
            ) -> None:
                """
                Remove filesystem entries in base_dir whose names are not in `allowed`.

                If `entries` is provided, it will be used instead of scanning `base_dir`. Symlinks are skipped. If `base_dir` does not exist the function returns quietly.

                Parameters:
                    base_dir (str): Path of the directory to inspect and prune.
                    allowed (set[str]): Names of entries (files or directories) that must be preserved.
                    entries (Optional[List[os.DirEntry[str]]]): Optional list of directory entries to use instead of scanning `base_dir`.
                """
                try:
                    if entries is None:
                        with os.scandir(base_dir) as it:
                            scan_entries = list(it)
                    else:
                        scan_entries = entries
                except FileNotFoundError:
                    return

                for entry in scan_entries:
                    if entry.is_symlink():
                        logger.warning(
                            "Skipping symlink in APK cleanup: %s", entry.name
                        )
                        continue
                    if entry.name in allowed:
                        continue
                    logger.info("Removing unexpected APK entry: %s", entry.name)
                    _safe_rmtree(entry.path, base_dir, entry.name)

            try:
                with os.scandir(android_dir) as it:
                    android_entries = list(it)
            except FileNotFoundError:
                return

            existing_entries = {
                entry.name for entry in android_entries if not entry.is_symlink()
            }
            if (
                keep_limit > 0
                and expected_stable
                and existing_entries
                and expected_stable.isdisjoint(existing_entries)
            ):
                logger.warning(
                    "Skipping APK cleanup: keep set does not match existing directories."
                )
                return

            _remove_unexpected_entries(
                android_dir,
                expected_stable | {APK_PRERELEASES_DIR_NAME},
                entries=android_entries,
            )

            if not os.path.exists(prerelease_dir):
                return

            _remove_unexpected_entries(prerelease_dir, expected_prerelease)
        except (OSError, ValueError) as exc:
            logger.error("Error cleaning up APK prerelease directories: %s", exc)

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Get the latest Android release tag recorded in the downloader's tracking file.

        Returns:
            The tracked release tag string (value of "latest_version") if present, `None` otherwise.
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
        Record the given release tag as the latest Android release in the tracking file.

        Returns:
            `True` if the tracking file was written successfully, `False` otherwise.
        """
        latest_file = self.latest_release_path
        data = {
            "latest_version": release_tag,
            "file_type": "android",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(latest_file, data)

    def _get_current_iso_timestamp(self) -> str:
        """
        Get the current UTC time as an ISO 8601 formatted timestamp.

        Returns:
            iso_timestamp (str): ISO 8601 formatted UTC timestamp (e.g., "2025-12-16T12:34:56+00:00").
        """
        return datetime.now(timezone.utc).isoformat()

    def handle_prereleases(
        self,
        releases: List[Release],
        recent_commits: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Release]:
        """
        Selects and returns Android prerelease releases that should be considered for download.

        Filters the provided releases according to the downloader's prerelease configuration: honors the CHECK_APK_PRERELEASES/CHECK_PRERELEASES flag, applies include/exclude tag patterns, restricts to prereleases that match the expected base version derived from the latest stable release, and optionally narrows results to tags containing short commit SHAs from recent_commits.

        Parameters:
            releases (List[Release]): All releases to evaluate; prerelease candidates are selected from this list.
            recent_commits (Optional[List[Dict[str, Any]]]): Optional list of recent commits (each a dict with a "sha" key). When provided, prereleases containing any short (7-character) commit SHA from this list in their tag will be preferred.

        Returns:
            List[Release]: Prerelease Release objects that match configured patterns, expected base version, and (when applicable) recent commit hashes.
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_APK_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )

        if not check_prereleases:
            return []

        version_manager = self.version_manager

        # Filter prereleases
        prereleases = [r for r in releases if r.prerelease]

        # Sort by published date (newest first)
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)

        # Apply pattern filtering if configured
        include_patterns = self.config.get("APK_PRERELEASE_INCLUDE_PATTERNS", [])
        exclude_patterns = self.config.get("APK_PRERELEASE_EXCLUDE_PATTERNS", [])

        if include_patterns or exclude_patterns:
            prerelease_tags = [r.tag_name for r in prereleases]
            filtered_tags = version_manager.filter_prereleases_by_pattern(
                prerelease_tags, include_patterns, exclude_patterns
            )
            prereleases = [r for r in prereleases if r.tag_name in filtered_tags]

        # Restrict to prereleases matching expected base version of latest stable
        expected_base = None
        latest_release = next((r for r in releases if not r.prerelease), None)
        if latest_release:
            expected_base = version_manager.calculate_expected_prerelease_version(
                latest_release.tag_name
            )

        if expected_base:
            filtered_prereleases = []
            for pr in prereleases:
                clean_version = version_manager.extract_clean_version(pr.tag_name)
                if not clean_version:
                    # Preserve unparsable tags to avoid discarding future formats.
                    filtered_prereleases.append(pr)
                    continue
                if clean_version.lstrip("vV").startswith(expected_base):
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

    def get_latest_prerelease_tag(
        self, releases: Optional[List[Release]] = None
    ) -> Optional[str]:
        """
        Return the newest APK prerelease tag, filtering out prereleases that are obsolete compared to the latest stable release.

        Parameters:
            releases (Optional[List[Release]]): Optional release list to inspect; when omitted, releases are fetched from GitHub.

        Returns:
            Optional[str]: The tag name for the newest relevant prerelease, or None if none are found.
        """
        available_releases = releases or self.get_releases()
        if not available_releases:
            return None

        sorted_releases = sorted(
            available_releases,
            key=lambda release: release.published_at or "",
            reverse=True,
        )
        latest_stable = next(
            (release for release in sorted_releases if not release.prerelease), None
        )
        latest_stable_tuple = (
            self.version_manager.get_release_tuple(latest_stable.tag_name)
            if latest_stable
            else None
        )

        for release in sorted_releases:
            if not release.prerelease:
                continue
            prerelease_tuple = self.version_manager.get_release_tuple(release.tag_name)
            if (
                latest_stable_tuple is None
                or prerelease_tuple is None
                or prerelease_tuple > latest_stable_tuple
            ):
                return release.tag_name

        return None

    def get_prerelease_tracking_file(self) -> str:
        """
        Get the filesystem path to the Android prerelease tracking JSON file.

        Returns:
            str: Path to prerelease tracking JSON file within the cache manager's directory.
        """
        return self.cache_manager.get_cache_file_path(self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        """
        Record the prerelease tag and extracted prerelease metadata to the Android prerelease tracking JSON file.

        Parameters:
            prerelease_tag (str): Prerelease tag to record (e.g., "v1.2.3-open-1"); used to extract base version, prerelease type/number, and commit hash.

        Returns:
            bool: `True` if the tracking file was written successfully, `False` otherwise.
        """
        tracking_file = self.get_prerelease_tracking_file()

        # Extract metadata from prerelease tag
        metadata = self.version_manager.get_prerelease_metadata_from_version(
            prerelease_tag
        )

        # Create tracking data with enhanced metadata
        data = {
            "latest_version": prerelease_tag,
            "file_type": FILE_TYPE_ANDROID_PRERELEASE,
            "last_updated": self._get_current_iso_timestamp(),
            "base_version": metadata.get("base_version", ""),
            "prerelease_type": metadata.get("prerelease_type", ""),
            "prerelease_number": metadata.get("prerelease_number", ""),
            "commit_hash": metadata.get("commit_hash", ""),
        }

        return self.cache_manager.atomic_write_json(tracking_file, data)

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate extraction patterns for Android APK files.

        Since APK files are not extracted in this downloader, this method
        always returns False to indicate that extraction is not supported.

        Args:
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: False (extraction not supported for APK files)
        """
        # APK files are not extracted, so patterns are not applicable
        logger.debug("Extraction validation called for Android APK - not applicable")
        return False

    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Indicates whether the given APK file requires extraction (always false for APKs).

        This downloader does not perform APK extraction; extraction is never needed or performed.

        Returns:
            `False` always — APK files are not extracted.
        """
        # APK files are not extracted, so extraction is never needed
        logger.debug("Extraction need check called for Android APK - not applicable")
        return False

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        """
        Determine whether the provided prerelease tag should be downloaded based on configuration and existing prerelease tracking.

        Checks the CHECK_APK_PRERELEASES / CHECK_PRERELEASES configuration and, if a valid prerelease tracking file exists, compares the given prerelease tag to the tracked prerelease to decide if it is newer.

        Parameters:
            prerelease_tag (str): The prerelease tag or identifier to evaluate.

        Returns:
            `True` if prereleases are enabled and either no valid tracking entry exists or `prerelease_tag` is newer than the tracked prerelease, `False` otherwise.
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_APK_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return False

        # Check if we have a tracking file
        tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(tracking_file):
            try:
                data = self.cache_manager.read_json(tracking_file) or {}
                current_prerelease = data.get("latest_version")
                if current_prerelease:
                    comparison = self.version_manager.compare_versions(
                        prerelease_tag, current_prerelease
                    )
                    return comparison > 0
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.debug(
                    "Error reading Android prerelease tracking file %s: %s; "
                    "defaulting to download",
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
        Remove Android prerelease tracking files that are superseded or expired when prerelease handling is enabled.

        Scans for prerelease tracking directory for existing tracking JSON files, determines the currently relevant prereleases from remote releases, builds corresponding tracking entries, and delegates deletion of superseded or expired tracking files to the PrereleaseHistoryManager. No value is returned.

        Parameters:
            cached_releases (Optional[List[Release]]): Optional cached releases to avoid redundant API calls.
        """
        check_prereleases = self.config.get(
            "CHECK_APK_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return

        tracking_dir = os.path.dirname(self.get_prerelease_tracking_file())
        if not os.path.exists(tracking_dir):
            return

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
            pass

        # Also include the main prerelease tracking file if it exists
        main_tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(main_tracking_file):
            tracking_files.append(main_tracking_file)

        # Read all existing prerelease tracking data
        existing_prereleases = []
        version_manager = self.version_manager
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

        # Create tracking data for current prereleases using shared helper
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

        # Clean up superseded/expired prereleases
        prerelease_manager.manage_prerelease_tracking_files(
            tracking_dir, current_tracking_data, self.cache_manager
        )


def _is_apk_prerelease_by_name(tag_name: str) -> bool:
    """
    Check if a tag name indicates an APK prerelease.

    Returns:
        True if the tag contains "-open" or "-closed" (case-insensitive), False otherwise.
    """
    return "-open" in (tag_name or "").lower() or "-closed" in (tag_name or "").lower()


MIN_ANDROID_TRACKED_VERSION = (2, 7, 0)


def _is_supported_android_release(
    tag_name: str, version_manager: Optional[VersionManager] = None
) -> bool:
    """
    Return True when the tag_name represents an Android release at or beyond the
    version where the new tagging scheme began (2.7.0+).

    Older prerelease tags (e.g., 2.6.x-open) should be ignored so they are not
    treated as current prereleases. Unparsable tags are allowed through to
    avoid blocking future formats.
    """
    manager = version_manager or VersionManager()
    version_tuple = manager.get_release_tuple(tag_name)
    if not version_tuple:
        return True

    max_len = max(len(version_tuple), len(MIN_ANDROID_TRACKED_VERSION))
    padded_version = version_tuple + (0,) * (max_len - len(version_tuple))
    padded_minimum = MIN_ANDROID_TRACKED_VERSION + (0,) * (
        max_len - len(MIN_ANDROID_TRACKED_VERSION)
    )

    return padded_version >= padded_minimum


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    """
    Determine whether a GitHub release represents an Android APK prerelease.

    Parameters:
        release (dict): GitHub release payload (or partial dict) expected to include at least `tag_name` and/or `prerelease` keys.

    Returns:
        bool: `True` if the release is identified as an APK prerelease (by legacy tag name patterns or the GitHub `prerelease` flag), `False` otherwise.
    """
    tag_name = (release or {}).get("tag_name", "")
    is_legacy_prerelease = _is_apk_prerelease_by_name(tag_name)
    is_github_prerelease = (release or {}).get("prerelease", False)
    return is_legacy_prerelease or is_github_prerelease
