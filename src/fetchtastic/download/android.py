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
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    MESHTASTIC_ANDROID_RELEASES_URL,
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
        Compute the filesystem path for an Android release asset under the android/<release> directory.

        Sanitizes the release tag and file name and ensures the version directory exists before returning the path.

        Returns:
            path (str): Filesystem path to the asset file.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, "android", safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Retrieve Android APK releases from GitHub, using cached API results when available.

        Parameters:
            limit (Optional[int]): Maximum number of releases to return.

        Returns:
            List[Release]: List of Release objects; empty list on error or if no valid releases are found.
        """
        try:
            params = {"per_page": 10}
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
                    url_key, releases_data if isinstance(releases_data, list) else []
                )

            if releases_data is None or not isinstance(releases_data, list):
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
            TypeError,
        ) as exc:
            logger.exception("Error fetching Android releases: %s", exc)
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Return APK assets included in the given release.

        Parameters:
            release (Release): Release object whose assets will be filtered.

        Returns:
            List[Asset]: Assets from the release whose names end with ".apk" (case-insensitive).
        """
        # Filter for APK files only
        assets = release.assets or []
        return [asset for asset in assets if asset.name.lower().endswith(".apk")]

    def get_download_url(self, asset: Asset) -> str:
        """
        Return the direct download URL for an asset.

        Returns:
            The asset's direct download URL.
        """
        return asset.download_url

    def should_download_asset(self, asset_name: str) -> bool:
        """
        Decides whether an APK asset name matches configured selection and exclusion patterns.

        Exclude patterns, if present, take precedence over selection patterns configured in SELECTED_APK_ASSETS.

        Returns:
            True if the asset should be downloaded, False otherwise.
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
        Download an APK asset for a given release, verify the saved file, and return a result describing the outcome.

        Parameters:
            release (Release): The release that contains the APK asset.
            asset (Asset): The APK asset to download.

        Returns:
            DownloadResult: A result object describing success or failure. On success the result contains the saved `file_path`, `download_url`, `file_size`, and `file_type`; if the file was already present and complete `was_skipped` will be true. On failure the result includes `error_message`, and may set `is_retryable` and `error_type` (for example `"network_error"` or `"validation_error"`).
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
                    file_type="android",
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
                        file_type="android",
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
                        file_type="android",
                        is_retryable=True,
                        error_type="validation_error",
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
                    file_type="android",
                    is_retryable=True,
                    error_type="network_error",
                )

        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.exception("Error downloading APK %s: %s", asset.name, exc)
            safe_path = target_path or os.path.join(self.download_dir, "android")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message=str(exc),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type="android",
                is_retryable=True,
                error_type="network_error",
            )

    def is_release_complete(self, release: Release) -> bool:
        """
        Determine whether all selected APK assets for the given release exist on disk and match their expected sizes.

        Parameters:
            release (Release): Release object whose tag and assets are used to locate expected APK files under the downloader's android/<release_tag> directory. Only assets that match configured SELECTED_APK_ASSETS patterns are considered.

        Returns:
            bool: `true` if every selected asset file exists and its file size equals the asset's expected size, `false` otherwise.
        """
        safe_tag = self._sanitize_required(release.tag_name, "release tag")
        version_dir = os.path.join(self.download_dir, "android", safe_tag)
        if not os.path.isdir(version_dir):
            return False

        selected_patterns = self.config.get("SELECTED_APK_ASSETS", [])
        expected_assets = [
            asset
            for asset in release.assets
            if matches_selected_patterns(asset.name, selected_patterns)
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
        Remove Android version directories older than the most recent `keep_limit` versions.

        Scans the downloader's android subdirectory for directories whose names match version patterns, sorts them from newest to oldest, and permanently deletes directories beyond the `keep_limit` newest entries. Non-version directories are ignored. Deletion failures are logged; exceptions are caught and logged without raising.

        Parameters:
            keep_limit (int): Number of most-recent version directories to retain; directories older than this are removed.
        """
        try:
            # Get all Android version directories
            android_dir = os.path.join(self.download_dir, "android")
            if not os.path.exists(android_dir):
                return

            # Get all version directories
            version_dirs = []
            for item in os.listdir(android_dir):
                item_path = os.path.join(android_dir, item)
                if os.path.isdir(item_path) and self._is_version_directory(item):
                    version_dirs.append(item)

            # Sort versions and keep only the newest ones
            version_dirs.sort(reverse=True, key=self._get_version_sort_key)

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
        Determine whether a directory name matches the expected version-directory pattern (e.g., "1.2", "v1.2.3").

        Parameters:
            dir_name (str): Directory name to test.

        Returns:
            `true` if the name matches a version pattern optionally prefixed with "v" and containing one to two dot-separated numeric components (major.minor or major.minor.patch), `false` otherwise.
        """
        return bool(re.match(r"^(v)?\d+(\.\d+){1,2}$", dir_name))

    def _get_version_sort_key(self, version_dir: str) -> tuple:
        """
        Generate a sort key based on the semantic version components of a version directory name.

        Strips a leading "v" if present, parses up to three dot-separated numeric components, and pads missing components with zeros. If the name cannot be parsed as numeric version components, returns (0, 0, 0).

        Parameters:
            version_dir (str): Directory name containing the version (may start with 'v' and use dot-separated numeric segments).

        Returns:
            tuple: A 3-tuple of integers (major, minor, patch) to use as a sort key; returns (0, 0, 0) for unparsable names.
        """
        # Extract version numbers for sorting
        version = version_dir.lstrip("v")
        try:
            parts = list(map(int, version.split(".")))
            # Pad to 3 parts for consistent sorting
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts[:3])  # Ensure exactly 3 parts
        except ValueError:
            return (0, 0, 0)

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Return the latest Android release tag recorded in the tracking file.

        Reads the tracking JSON located at download_dir/latest_release_file and returns the value stored under `latest_version`.

        Returns:
            The latest release tag string if present, otherwise None.
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
        Update the latest Android release tag in the tracking file.

        Args:
            release_tag: The release tag to record

        Returns:
            bool: True if update succeeded, False otherwise
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
        Return the current UTC timestamp as an ISO 8601 formatted string.

        Returns:
            iso_timestamp (str): ISO 8601-formatted UTC timestamp (e.g., "2025-12-16T12:34:56+00:00").
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

        version_manager = VersionManager()

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
        Get the path to the Android prerelease tracking file.

        Returns:
            str: Path to the prerelease tracking file
        """
        return os.path.join(self.download_dir, self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        """
        Write the given prerelease tag and extracted prerelease metadata to the Android prerelease tracking JSON file.

        Parameters:
            prerelease_tag (str): The prerelease tag to record (e.g., "v1.2.3-open-1").

        Returns:
            bool: `true` if the tracking file was written successfully, `false` otherwise.
        """
        tracking_file = self.get_prerelease_tracking_file()

        # Extract metadata from prerelease tag
        version_manager = VersionManager()
        metadata = version_manager.get_prerelease_metadata_from_version(prerelease_tag)

        # Create tracking data with enhanced metadata
        data = {
            "latest_version": prerelease_tag,
            "file_type": "android_prerelease",
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
        Determine whether extraction should be performed for the given file.

        Always returns `False` because APK files handled by this downloader are not extracted.

        Returns:
            `False` always (APK files are not extracted).
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
                    comparison = VersionManager().compare_versions(
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
        Clean up Android prerelease tracking files by removing entries that are superseded or expired.

        When prerelease handling is enabled in configuration, this method collects existing prerelease
        tracking records, determines currently relevant prereleases from remote releases, and delegates
        removal of superseded or expired tracking files to the PrereleaseHistoryManager. No value is returned.
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
        for filename in os.listdir(tracking_dir):
            if filename.startswith("prerelease_") and filename.endswith(".json"):
                tracking_files.append(os.path.join(tracking_dir, filename))

        # Also include the main prerelease tracking file if it exists
        main_tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(main_tracking_file):
            tracking_files.append(main_tracking_file)

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
    Determine whether a tag name indicates an APK prerelease by containing the markers "-open" or "-closed".

    Parameters:
        tag_name (str): The release tag name to inspect.

    Returns:
        bool: `True` if `tag_name` contains "-open" or "-closed" (case-insensitive), `False` otherwise.
    """
    return "-open" in (tag_name or "").lower() or "-closed" in (tag_name or "").lower()


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
