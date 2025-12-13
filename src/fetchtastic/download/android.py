"""
Meshtastic Android App Downloader

This module implements the specific downloader for Meshtastic Android APK files.
"""

import fnmatch
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fetchtastic.constants import (
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    MESHTASTIC_ANDROID_RELEASES_URL,
    RELEASES_CACHE_EXPIRY_HOURS,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request, matches_selected_patterns

from .base import BaseDownloader
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

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Android app downloader.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.android_releases_url = MESHTASTIC_ANDROID_RELEASES_URL
        self.latest_release_file = LATEST_ANDROID_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_ANDROID_PRERELEASE_JSON_FILE

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Get the target path for an Android asset under the android directory.

        Keeping platform-specific subdirectories matches the legacy layout and
        allows result reporting to correctly classify download types.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, "android", safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Get available Android APK releases from GitHub.

        Args:
            limit: Maximum number of releases to return

        Returns:
            List[Release]: List of available Android releases
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

        except Exception:
            logger.exception("Error fetching Android releases")
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Get downloadable assets for a specific Android release.

        Args:
            release: The release to get assets for

        Returns:
            List[Asset]: List of downloadable assets for the release
        """
        return release.assets or []

    def get_download_url(self, asset: Asset) -> str:
        """
        Get the download URL for a specific Android asset.

        Args:
            asset: The asset to get download URL for

        Returns:
            str: Direct download URL for the asset
        """
        return asset.download_url

    def should_download_asset(self, asset_name: str) -> bool:
        """
        Determine if an Android asset should be downloaded based on config selections.
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
        Download a specific Android APK file.

        Args:
            release: The release containing the APK
            asset: The APK asset to download

        Returns:
            DownloadResult: Result of the download operation
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

        except Exception:
            logger.exception("Error downloading APK %s", asset.name)
            safe_path = target_path or os.path.join(self.download_dir, "android")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message="Error downloading APK",
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type="android",
                is_retryable=True,
                error_type="network_error",
            )

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old Android APK versions according to retention policy.

        Args:
            keep_limit: Maximum number of versions to keep
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
                    import shutil

                    shutil.rmtree(old_dir)
                    logger.info(f"Removed old Android version: {old_version}")
                except OSError as e:
                    logger.error(
                        f"Error removing old Android version {old_version}: {e}"
                    )

        except Exception:
            logger.exception("Error cleaning up old Android versions")

    def _is_version_directory(self, dir_name: str) -> bool:
        """Check if a directory name represents a version directory."""
        return bool(re.match(r"^(v)?\d+\.\d+\.\d+$", dir_name))

    def _get_version_sort_key(self, version_dir: str) -> tuple:
        """Get a sort key for version directories."""
        # Extract version numbers for sorting
        version = version_dir.lstrip("v")
        try:
            parts = list(map(int, version.split(".")))
            return tuple(parts)
        except ValueError:
            return (0, 0, 0)

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Get the latest Android release tag from the tracking file.

        Returns:
            Optional[str]: Latest release tag, or None if not found
        """
        latest_file = os.path.join(self.download_dir, self.latest_release_file)
        if os.path.exists(latest_file):
            try:
                import json

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
        latest_file = os.path.join(self.download_dir, self.latest_release_file)
        data = {
            "latest_version": release_tag,
            "file_type": "android",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(latest_file, data)

    def _get_current_iso_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def handle_prereleases(
        self,
        releases: List[Release],
        recent_commits: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Release]:
        """
        Filter and manage Android prereleases with enhanced functionality.

        Args:
            releases: List of all releases
            recent_commits: Optional list of recent commits for filtering

        Returns:
            List[Release]: Filtered list of prereleases
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_APK_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )

        if not check_prereleases:
            return []

        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()

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

        # Repo directory scan: ensure prerelease tag exists in repo listing (for apk none, but keep parity)
        try:
            from fetchtastic import menu_repo

            directories = menu_repo.fetch_repo_directories()
            repo_matches = prerelease_manager.scan_prerelease_directories(
                directories, expected_base or ""
            )
            if repo_matches:
                prereleases = [
                    pr
                    for pr in prereleases
                    if any(match in pr.tag_name for match in repo_matches)
                ] or prereleases
        except Exception:
            logger.debug(
                "Repo directory scan for APK prereleases failed", exc_info=True
            )

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
        Update the Android prerelease tracking information with enhanced metadata.

        Args:
            prerelease_tag: The prerelease tag to record

        Returns:
            bool: True if update succeeded, False otherwise
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
        Check if extraction is needed for Android APK files.

        Since APK files are not extracted in this downloader, this method
        always returns False to indicate that extraction is not needed.

        Args:
            file_path: Path to the APK file
            extract_dir: Directory where files would be extracted
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: False (extraction not needed for APK files)
        """
        # APK files are not extracted, so extraction is never needed
        logger.debug("Extraction need check called for Android APK - not applicable")
        return False

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        """
        Determine if a prerelease should be downloaded.

        Args:
            prerelease_tag: The prerelease tag to check

        Returns:
            bool: True if prerelease should be downloaded, False otherwise
        """
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
            except Exception:
                return True

        # No tracking file or unreadable; default to download
        return True

    def manage_prerelease_tracking_files(self) -> None:
        """
        Manage Android prerelease tracking files including cleanup of superseded prereleases.

        This method scans the prerelease tracking directory and cleans up any superseded
        or expired prerelease tracking files.
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
            try:
                tracking_data = self.cache_manager.read_json(file_path)
                if (
                    tracking_data
                    and "latest_version" in tracking_data
                    and "base_version" in tracking_data
                ):
                    existing_prereleases.append(tracking_data)
            except Exception:
                continue

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
    return "-open" in (tag_name or "").lower() or "-closed" in (tag_name or "").lower()


def _is_apk_prerelease(release: Dict[str, Any]) -> bool:
    tag_name = (release or {}).get("tag_name", "")
    is_legacy_prerelease = _is_apk_prerelease_by_name(tag_name)
    is_github_prerelease = (release or {}).get("prerelease", False)
    return is_legacy_prerelease or is_github_prerelease
