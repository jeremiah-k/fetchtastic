"""
Firmware Release Downloader

This module implements the specific downloader for Meshtastic firmware releases.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fetchtastic.constants import (
    FIRMWARE_DIR_PREFIX,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .base import BaseDownloader
from .interfaces import Asset, DownloadResult, Release
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

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the firmware downloader.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.firmware_releases_url = MESHTASTIC_FIRMWARE_RELEASES_URL
        self.latest_release_file = LATEST_FIRMWARE_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_FIRMWARE_PRERELEASE_JSON_FILE

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Get the target path for a firmware asset under the firmware directory.

        The legacy layout keeps firmware files in a firmware-specific subdirectory;
        preserve that structure so cleanup and reporting can detect firmware assets.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, "firmware", safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Get available firmware releases from GitHub.

        Args:
            limit: Maximum number of releases to return

        Returns:
            List[Release]: List of available firmware releases
        """
        try:
            # Use the existing GitHub API request utility
            releases_data = make_github_api_request(
                f"{self.firmware_releases_url}",
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
            logger.error(f"Error fetching firmware releases: {e}")
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Get downloadable assets for a specific firmware release.

        Args:
            release: The release to get assets for

        Returns:
            List[Asset]: List of downloadable assets for the release
        """
        return release.assets or []

    def get_download_url(self, asset: Asset) -> str:
        """
        Get the download URL for a specific firmware asset.

        Args:
            asset: The asset to get download URL for

        Returns:
            str: Direct download URL for the asset
        """
        return asset.download_url

    def download_firmware(self, release: Release, asset: Asset) -> DownloadResult:
        """
        Download a specific firmware file.

        Args:
            release: The release containing the firmware
            asset: The firmware asset to download

        Returns:
            DownloadResult: Result of the download operation
        """
        target_path: Optional[str] = None
        try:
            # Get target path for the firmware ZIP
            target_path = self.get_target_path_for_release(release.tag_name, asset.name)

            # Check if we need to download
            if not self.needs_download(release.tag_name, asset.name, asset.size):
                logger.info(f"Firmware {asset.name} already exists and is valid")
                return self.create_download_result(
                    success=True, release_tag=release.tag_name, file_path=target_path
                )

            # Download the firmware ZIP
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
            logger.error(f"Error downloading firmware {asset.name}: {e}")
            safe_path = target_path or os.path.join(self.download_dir, "firmware")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message=str(e),
            )

    def extract_firmware(
        self, release: Release, asset: Asset, patterns: List[str]
    ) -> DownloadResult:
        """
        Extract firmware files from a downloaded ZIP archive.

        Args:
            release: The release containing the firmware
            asset: The firmware asset that was downloaded
            patterns: List of filename patterns to extract

        Returns:
            DownloadResult: Result of the extraction operation
        """
        try:
            # Get the path to the downloaded ZIP file
            zip_path = self.get_target_path_for_release(release.tag_name, asset.name)
            if not os.path.exists(zip_path):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=Path(zip_path),
                    error_message="ZIP file not found",
                )

            # Extract files matching patterns
            extracted_files = self.extract(zip_path, patterns)

            if extracted_files:
                logger.info(f"Extracted {len(extracted_files)} files from {asset.name}")
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=Path(zip_path),
                    extracted_files=extracted_files,
                )
            else:
                logger.warning(
                    f"No files extracted from {asset.name} - no matches for patterns"
                )
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=Path(zip_path),
                    error_message="No files matched extraction patterns",
                )

        except Exception as e:
            logger.error(f"Error extracting firmware {asset.name}: {e}")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=Path(zip_path),
                error_message=str(e),
            )

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old firmware versions according to retention policy.

        Args:
            keep_limit: Maximum number of versions to keep
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
                    and item.startswith("v")
                    and item not in ["prerelease", "repo-dls"]
                ):
                    version_dirs.append(item)

            # Sort versions and keep only the newest ones
            version_dirs.sort(reverse=True, key=self._get_version_sort_key)

            # Remove old versions
            for old_version in version_dirs[keep_limit:]:
                old_dir = os.path.join(firmware_dir, old_version)
                try:
                    import shutil

                    shutil.rmtree(old_dir)
                    logger.info(f"Removed old firmware version: {old_version}")
                except OSError as e:
                    logger.error(
                        f"Error removing old firmware version {old_version}: {e}"
                    )

        except Exception as e:
            logger.error(f"Error cleaning up old firmware versions: {e}")

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
        Get the latest firmware release tag from the tracking file.

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
        Update the latest firmware release tag in the tracking file.

        Args:
            release_tag: The release tag to record

        Returns:
            bool: True if update succeeded, False otherwise
        """
        latest_file = os.path.join(self.download_dir, self.latest_release_file)
        data = {
            "latest_version": release_tag,
            "file_type": "firmware",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(latest_file, data)

    def _get_current_iso_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def handle_prereleases(self, releases: List[Release]) -> List[Release]:
        """
        Filter and manage firmware prereleases.

        Args:
            releases: List of all releases

        Returns:
            List[Release]: Filtered list of prereleases
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get("CHECK_FIRMWARE_PRERELEASES", False)

        if not check_prereleases:
            return []

        # Filter prereleases
        prereleases = [r for r in releases if r.prerelease]

        # Sort by published date (newest first)
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)

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
        Update the firmware prerelease tracking information.

        Args:
            prerelease_tag: The prerelease tag to record

        Returns:
            bool: True if update succeeded, False otherwise
        """
        tracking_file = self.get_prerelease_tracking_file()
        data = {
            "latest_version": prerelease_tag,
            "file_type": "firmware_prerelease",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(tracking_file, data)

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

    def cleanup_superseded_prereleases(self, latest_release_tag: str) -> bool:
        """
        Remove prerelease firmware directories that are superseded by an official release.

        Args:
            latest_release_tag: Latest official release tag

        Returns:
            bool: True if cleanup was performed, False otherwise
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
                                        import shutil

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

        except Exception as e:
            logger.error(f"Error cleaning up superseded prereleases: {e}")
            return False

    @staticmethod
    def check_and_download(
        releases,
        cache_dir,
        release_type,
        download_dir,
        versions_to_keep=2,
        extract_patterns=None,
        selected_patterns=None,
        auto_extract=False,
        exclude_patterns=None,
    ):
        """
        Static method to check and download releases (for backward compatibility with tests).

        This method creates a temporary downloader instance and performs the download operation.

        Args:
            releases: List of release data
            cache_dir: Directory for caching
            release_type: Type of release (e.g., "Firmware")
            download_dir: Directory to download files to
            versions_to_keep: Number of versions to keep
            extract_patterns: Patterns for files to extract
            selected_patterns: Patterns for selecting assets to download
            auto_extract: Whether to automatically extract files
            exclude_patterns: Patterns for excluding assets

        Returns:
            Tuple of (downloaded, new_versions, failures)
        """
        # Create a mock config for the downloader
        mock_config = {
            "DOWNLOAD_DIR": download_dir,
            "VERSIONS_TO_KEEP": versions_to_keep,
            "FIRMWARE_VERSIONS_TO_KEEP": versions_to_keep,
            "ANDROID_VERSIONS_TO_KEEP": versions_to_keep,
            "SELECTED_PATTERNS": selected_patterns or [],
            "EXCLUDE_PATTERNS": exclude_patterns or [],
            "EXTRACT_PATTERNS": extract_patterns or [],
            "AUTO_EXTRACT": auto_extract,
            "GITHUB_TOKEN": None,
        }

        # Create downloader instance
        downloader = FirmwareReleaseDownloader(mock_config)
        downloader.download_dir = download_dir

        # Convert releases to the expected format
        processed_releases = []
        for release_data in releases:
            release = Release(
                tag_name=release_data["tag_name"],
                prerelease=release_data.get("prerelease", False),
                published_at=release_data.get("published_at"),
                body=release_data.get("body"),
            )

            # Add assets
            for asset_data in release_data.get("assets", []):
                asset = Asset(
                    name=asset_data["name"],
                    download_url=asset_data.get("browser_download_url"),
                    size=asset_data.get("size"),
                    browser_download_url=asset_data.get("browser_download_url"),
                    content_type=asset_data.get("content_type"),
                )
                release.assets.append(asset)

            processed_releases.append(release)

        # Process downloads
        downloaded = []
        new_versions = []
        failures = []

        for release in processed_releases:
            # Check if this release should be downloaded based on patterns
            should_download = False
            for asset in release.assets:
                if downloader.should_download_release(release.tag_name, asset.name):
                    should_download = True
                    break

            if not should_download:
                logger.info(
                    f"Release {release.tag_name} found, but no assets matched current selection/exclude filters"
                )
                continue

            # Check if this is a new version
            latest_tag = downloader.get_latest_release_tag()
            is_new_version = (
                latest_tag is None
                or downloader.version_manager.compare_versions(
                    release.tag_name, latest_tag
                )
                > 0
            )

            if is_new_version:
                new_versions.append(release.tag_name)

            # Download each asset
            release_downloaded = False
            for asset in release.assets:
                if not downloader.should_download_release(release.tag_name, asset.name):
                    continue

                download_result = downloader.download_firmware(release, asset)

                if download_result.success:
                    release_downloaded = True

                    # Handle extraction if needed
                    if auto_extract and extract_patterns:
                        extract_result = downloader.extract_firmware(
                            release, asset, extract_patterns
                        )
                        if not extract_result.success:
                            logger.warning(
                                f"Extraction failed for {asset.name}: {extract_result.error_message}"
                            )

                    # Update latest release tag if this is the newest version
                    if is_new_version:
                        downloader.update_latest_release_tag(release.tag_name)
                else:
                    failures.append(
                        {
                            "release_tag": release.tag_name,
                            "asset": asset.name,
                            "reason": download_result.error_message
                            or "Download failed",
                        }
                    )

            if release_downloaded:
                downloaded.append(release.tag_name)

        # Clean up old versions
        downloader.cleanup_old_versions(versions_to_keep)

        return downloaded, new_versions, failures
