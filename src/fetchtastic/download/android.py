"""
Meshtastic Android App Downloader

This module implements the specific downloader for Meshtastic Android APK files.
"""

import fnmatch
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from fetchtastic.constants import (
    APKS_DIR_NAME,
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
from .interfaces import Asset, DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
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

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Return filesystem path for an APK asset inside Android downloads directory, creating the release directory if it does not exist.

        Input values are sanitized before use; function ensures directory {APKS_DIR_NAME}/<release_tag> exists under the configured download directory.

        Returns:
            str: Filesystem path to the asset file.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, APKS_DIR_NAME, safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Fetches Android APK releases from GitHub, using a cached response when available.

        Builds and returns Release objects populated with their Asset entries, filters out releases that have no assets, and respects the optional limit on the number of releases returned.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return.

        Returns:
            List[Release]: List of Release objects; empty list on error or if no valid releases are found.
        """
        try:
            max_scan = GITHUB_MAX_PER_PAGE
            min_stable_releases = int(
                self.config.get("ANDROID_VERSIONS_TO_KEEP", RELEASE_SCAN_COUNT)
            )
            scan_count = (
                int(limit)
                if limit
                else min(max_scan, max(min_stable_releases * 2, RELEASE_SCAN_COUNT))
            )

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
                        allow_env_token=True,
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
                    if limit and len(releases) >= limit:
                        break

                if limit:
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
        Download and verify an APK asset for a specific release and return a DownloadResult describing the outcome.

        If the asset already exists and matches expectations the download is skipped; on a successful download the saved file is verified and returned; on verification or transfer failures a result is returned that includes an error message, an error_type (e.g., "network_error", "validation_error", or "filesystem_error"), and whether the failure is retryable.

        Parameters:
            release (Release): Release object containing the APK asset (used for tag and metadata).
            asset (Asset): Asset object describing the APK to download (includes name, size, and download_url).

        Returns:
            DownloadResult: An object describing success or failure. On success contains the saved `file_path`, `download_url`, `file_size`, and `file_type`; if the download was skipped `was_skipped` will be true. On failure contains `error_message` and may set `is_retryable` and `error_type`.
        """
        target_path: Optional[str] = None
        try:
            # Get target path for the APK
            target_path = self.get_target_path_for_release(release.tag_name, asset.name)

            # Check if we need to download
            if self.is_asset_complete(release.tag_name, asset):
                logger.info(f"APK {asset.name} already exists and is complete")
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type=FILE_TYPE_ANDROID,
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
                        file_type=FILE_TYPE_ANDROID,
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
                        file_type=FILE_TYPE_ANDROID,
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
                    file_type=FILE_TYPE_ANDROID,
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
                file_type=FILE_TYPE_ANDROID,
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
        safe_tag = self._sanitize_required(release.tag_name, "release tag")
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

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Delete Android version directories older than most recent specified number to keep.

        Ignores directories that do not match version-style names. Deletion failures are logged and exceptions are suppressed.
        Operates on the {APKS_DIR_NAME} subdirectory within the configured download directory.

        Parameters:
            keep_limit (int): Number of most-recent version directories to retain; directories older than this are removed.
        """
        try:
            # Get all Android version directories
            android_dir = os.path.join(self.download_dir, APKS_DIR_NAME)
            if not os.path.exists(android_dir):
                return

            # Get all version directories
            version_dirs = []
            try:
                with os.scandir(android_dir) as it:
                    for entry in it:
                        if entry.is_dir() and self._is_version_directory(entry.name):
                            version_dirs.append(entry.name)
            except FileNotFoundError:
                pass

            # Sort versions (newest first) using VersionManager tuples
            version_dirs.sort(
                reverse=True,
                key=lambda version: self.version_manager.get_release_tuple(version)
                or (),
            )

            # Remove old versions
            for old_version in version_dirs[keep_limit:]:
                old_dir = os.path.join(android_dir, old_version)
                try:
                    shutil.rmtree(old_dir)
                    logger.info(f"Removed old Android version: {old_version}")
                except OSError as e:
                    logger.error(
                        f"Error removing old Android version {old_version}: {e}"
                    )

        except OSError:
            logger.exception("Error cleaning up old Android versions")

    def _is_version_directory(self, dir_name: str) -> bool:
        """
        Determine whether a directory name matches a semantic version-like pattern.

        Parameters:
            dir_name (str): Directory name to test.

        Returns:
            `true` if the name matches a version pattern optionally prefixed with 'v' and containing one to two dot-separated numeric components (e.g., '1.2', 'v1.2.3'), `false` otherwise.
        """
        return bool(re.match(r"^(v)?\d+(\.\d+){1,2}$", dir_name))

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
                    return data.get("latest_version")
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
        Decides whether the given prerelease tag should be downloaded based on configuration and existing prerelease tracking.

        Parameters:
            prerelease_tag (str): The prerelease tag to evaluate.

        Returns:
            bool: `True` if prereleases are enabled and either no valid tracking entry exists or `prerelease_tag` is newer than the tracked prerelease; `False` if prereleases are disabled or `prerelease_tag` is not newer than the tracked prerelease.
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

    def manage_prerelease_tracking_files(self) -> None:
        """
        Remove Android prerelease tracking files that are superseded or expired when prerelease handling is enabled.

        Scans the prerelease tracking directory for existing tracking JSON files, determines the currently relevant prereleases from remote releases, builds corresponding tracking entries, and delegates deletion of superseded or expired tracking files to the PrereleaseHistoryManager. No value is returned.
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
        current_releases = self.get_releases(limit=10)
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
