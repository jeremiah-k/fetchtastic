"""
Meshtastic Android App Downloader

This module implements the specific downloader for Meshtastic Android APK files.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fetchtastic.constants import (
    LATEST_ANDROID_PRERELEASE_JSON_FILE,
    LATEST_ANDROID_RELEASE_JSON_FILE,
    MESHTASTIC_ANDROID_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .base import BaseDownloader
from .interfaces import Asset, DownloadResult, Release
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
            # Use the existing GitHub API request utility
            releases_data = make_github_api_request(
                f"{self.android_releases_url}",
                self.config.get("GITHUB_TOKEN"),
                allow_env_token=True,
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

        except Exception as e:
            logger.error(f"Error fetching Android releases: {e}")
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
            if not self.needs_download(release.tag_name, asset.name, asset.size):
                logger.info(f"APK {asset.name} already exists and is valid")
                return self.create_download_result(
                    success=True, release_tag=release.tag_name, file_path=target_path
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
                    )
                else:
                    logger.error(f"Verification failed for {asset.name}")
                    self.cleanup_file(target_path)
                    return self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        error_message="Verification failed",
                    )
            else:
                logger.error(f"Download failed for {asset.name}")
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    error_message="Download failed",
                )

        except Exception as e:
            logger.error(f"Error downloading APK {asset.name}: {e}")
            safe_path = target_path or os.path.join(self.download_dir, "android")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message=str(e),
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

        except Exception as e:
            logger.error(f"Error cleaning up old Android versions: {e}")

    def _is_version_directory(self, dir_name: str) -> bool:
        """Check if a directory name represents a version directory."""
        return dir_name.startswith("v") and re.match(r"^v\d+\.\d+\.\d+$", dir_name)

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
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def handle_prereleases(self, releases: List[Release]) -> List[Release]:
        """
        Filter and manage Android prereleases.

        Args:
            releases: List of all releases

        Returns:
            List[Release]: Filtered list of prereleases
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get("CHECK_APK_PRERELEASES", False)

        if not check_prereleases:
            return []

        # Filter prereleases
        prereleases = [r for r in releases if r.prerelease]

        # Sort by published date (newest first)
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)

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
        Update the Android prerelease tracking information.

        Args:
            prerelease_tag: The prerelease tag to record

        Returns:
            bool: True if update succeeded, False otherwise
        """
        tracking_file = self.get_prerelease_tracking_file()
        data = {
            "latest_version": prerelease_tag,
            "file_type": "android_prerelease",
            "last_updated": self._get_current_iso_timestamp(),
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
        if not os.path.exists(tracking_file):
            return True

        # Check if this is a newer prerelease than what we have
        try:
            import json

            with open(tracking_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                current_prerelease = data.get("latest_version")

                if not current_prerelease:
                    return True

                # Compare versions
                version_manager = VersionManager()
                comparison = version_manager.compare_versions(
                    prerelease_tag, current_prerelease
                )
                return comparison > 0  # Download if newer

        except (IOError, json.JSONDecodeError):
            return True
