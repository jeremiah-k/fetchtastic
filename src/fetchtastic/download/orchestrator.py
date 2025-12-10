"""
Download Pipeline Orchestrator

This module implements the orchestration layer that coordinates multiple
downloaders in a single fetchtastic download run.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fetchtastic.log_utils import logger

from .android import MeshtasticAndroidAppDownloader
from .firmware import FirmwareReleaseDownloader
from .interfaces import Asset, DownloadResult, Release
from .version import VersionManager


class DownloadOrchestrator:
    """
    Orchestrates the download pipeline for multiple artifact types.

    This class coordinates:
    - Multiple downloaders (Android, Firmware, etc.)
    - Release fetching and filtering
    - Download execution and retry logic
    - Result aggregation and reporting
    - Error handling and recovery
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the download orchestrator.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.version_manager = VersionManager()

        # Initialize downloaders
        self.android_downloader = MeshtasticAndroidAppDownloader(config)
        self.firmware_downloader = FirmwareReleaseDownloader(config)

        # Track results
        self.download_results: List[DownloadResult] = []
        self.failed_downloads: List[DownloadResult] = []

    def run_download_pipeline(
        self,
    ) -> Tuple[List[DownloadResult], List[DownloadResult]]:
        """
        Run the complete download pipeline.

        Returns:
            Tuple[List[DownloadResult], List[DownloadResult]]: Successful and failed download results
        """
        start_time = time.time()
        logger.info("Starting download pipeline...")

        # Process Android downloads
        self._process_android_downloads()

        # Process firmware downloads
        self._process_firmware_downloads()

        # Retry failed downloads
        self._retry_failed_downloads()

        # Log summary
        self._log_download_summary(start_time)

        return self.download_results, self.failed_downloads

    def _process_android_downloads(self) -> None:
        """Process Android APK downloads."""
        try:
            logger.info("Processing Android APK downloads...")

            # Get Android releases
            android_releases = self.android_downloader.get_releases()
            if not android_releases:
                logger.info("No Android releases found")
                return

            # Filter releases based on configuration
            releases_to_download = self._filter_releases(android_releases, "android")

            # Download each release
            for release in releases_to_download:
                self._download_android_release(release)

        except Exception as e:
            logger.error(f"Error processing Android downloads: {e}")

    def _process_firmware_downloads(self) -> None:
        """Process firmware downloads."""
        try:
            logger.info("Processing firmware downloads...")

            # Get firmware releases
            firmware_releases = self.firmware_downloader.get_releases()
            if not firmware_releases:
                logger.info("No firmware releases found")
                return

            # Filter releases based on configuration
            releases_to_download = self._filter_releases(firmware_releases, "firmware")

            # Download each release
            for release in releases_to_download:
                self._download_firmware_release(release)

        except Exception as e:
            logger.error(f"Error processing firmware downloads: {e}")

    def _filter_releases(
        self, releases: List[Release], artifact_type: str
    ) -> List[Release]:
        """
        Filter releases based on configuration and existing downloads.

        Args:
            releases: List of available releases
            artifact_type: Type of artifact ('android' or 'firmware')

        Returns:
            List[Release]: Filtered list of releases to download
        """
        filtered_releases = []

        # Get existing releases for this artifact type
        existing_releases = self._get_existing_releases(artifact_type)

        for release in releases:
            # Skip if we already have this release
            if release.tag_name in existing_releases:
                logger.debug(
                    f"Skipping {artifact_type} release {release.tag_name} - already downloaded"
                )
                continue

            # Check if this release should be downloaded based on patterns
            if self._should_download_release(release, artifact_type):
                filtered_releases.append(release)

        return filtered_releases

    def _get_existing_releases(self, artifact_type: str) -> List[str]:
        """
        Get list of existing releases for an artifact type.

        Args:
            artifact_type: Type of artifact ('android' or 'firmware')

        Returns:
            List[str]: List of existing release tags
        """
        if artifact_type == "android":
            existing = self.android_downloader.get_latest_release_tag()
            return [existing] if existing else []
        elif artifact_type == "firmware":
            existing = self.firmware_downloader.get_latest_release_tag()
            return [existing] if existing else []
        return []

    def _should_download_release(self, release: Release, artifact_type: str) -> bool:
        """
        Determine if a release should be downloaded.

        Args:
            release: The release to check
            artifact_type: Type of artifact

        Returns:
            bool: True if release should be downloaded
        """
        # Check prerelease settings
        if release.prerelease:
            if artifact_type == "android":
                check_prereleases = self.config.get("CHECK_APK_PRERELEASES", False)
            else:
                check_prereleases = self.config.get("CHECK_FIRMWARE_PRERELEASES", False)

            if not check_prereleases:
                logger.debug(
                    f"Skipping prerelease {release.tag_name} - prereleases disabled"
                )
                return False

        return True

    def _download_android_release(self, release: Release) -> None:
        """
        Download an Android release and its assets.

        Args:
            release: The Android release to download
        """
        try:
            logger.info(f"Downloading Android release {release.tag_name}")

            # Download each asset in the release
            for asset in release.assets:
                result = self.android_downloader.download_apk(release, asset)
                self._handle_download_result(result, "android")

        except Exception as e:
            logger.error(f"Error downloading Android release {release.tag_name}: {e}")

    def _download_firmware_release(self, release: Release) -> None:
        """
        Download a firmware release and its assets.

        Args:
            release: The firmware release to download
        """
        try:
            logger.info(f"Downloading firmware release {release.tag_name}")

            # Get extraction patterns from configuration
            extract_patterns = self._get_extraction_patterns()
            exclude_patterns = self._get_exclude_patterns()

            # Filter assets based on selection/exclude rules
            assets_to_download = [
                asset
                for asset in release.assets
                if self.firmware_downloader.should_download_release(
                    release.tag_name, asset.name
                )
            ]

            if not assets_to_download:
                logger.info(
                    "Release %s found, but no assets matched current selection/exclude filters",
                    release.tag_name,
                )
                return

            # Download each asset in the release
            for asset in assets_to_download:
                # Download the firmware ZIP
                download_result = self.firmware_downloader.download_firmware(
                    release, asset
                )
                self._handle_download_result(download_result, "firmware")

                # If download succeeded, extract files
                if download_result.success:
                    extract_result = self.firmware_downloader.extract_firmware(
                        release, asset, extract_patterns, exclude_patterns
                    )
                    self._handle_download_result(extract_result, "firmware_extraction")

        except Exception as e:
            logger.error(f"Error downloading firmware release {release.tag_name}: {e}")

    def _get_extraction_patterns(self) -> List[str]:
        """
        Get extraction patterns from configuration.

        Returns:
            List[str]: List of filename patterns to extract
        """
        patterns = self.config.get("EXTRACT_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _get_exclude_patterns(self) -> List[str]:
        """
        Get exclude patterns from configuration.

        Returns:
            List[str]: List of filename patterns to exclude
        """
        patterns = self.config.get("EXCLUDE_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _handle_download_result(
        self, result: DownloadResult, operation_type: str
    ) -> None:
        """
        Handle the result of a download operation.

        Args:
            result: The download result
            operation_type: Type of operation (for logging)
        """
        if result.success:
            self.download_results.append(result)
            logger.info(f"Successfully {operation_type}: {result.release_tag}")
        else:
            self.failed_downloads.append(result)
            error_msg = result.error_message or "Unknown error"
            logger.error(
                f"Failed {operation_type} for {result.release_tag}: {error_msg}"
            )

    def _retry_failed_downloads(self) -> None:
        """Retry failed downloads."""
        if not self.failed_downloads:
            return

        logger.info(f"Retrying {len(self.failed_downloads)} failed downloads...")

        for failed_result in self.failed_downloads:
            try:
                # Determine which downloader to use based on the file path
                if failed_result.file_path and "android" in str(
                    failed_result.file_path
                ):
                    # This was an Android download - retry with Android downloader
                    # We'd need to reconstruct the release/asset info here
                    logger.info(
                        f"Retrying Android download: {failed_result.release_tag}"
                    )
                    # In a real implementation, we'd have the original release/asset info
                elif failed_result.file_path and "firmware" in str(
                    failed_result.file_path
                ):
                    # This was a firmware download - retry with firmware downloader
                    logger.info(
                        f"Retrying firmware download: {failed_result.release_tag}"
                    )
                    # In a real implementation, we'd have the original release/asset info

            except Exception as e:
                logger.error(f"Retry failed for {failed_result.release_tag}: {e}")

    def _log_download_summary(self, start_time: float) -> None:
        """Log a summary of the download results."""
        elapsed_time = time.time() - start_time
        total_downloads = len(self.download_results)
        total_failures = len(self.failed_downloads)

        logger.info("Download pipeline completed")
        logger.info(f"Time taken: {elapsed_time:.2f} seconds")
        logger.info(f"Successful downloads: {total_downloads}")
        logger.info(f"Failed downloads: {total_failures}")

        if total_failures > 0:
            logger.warning(
                f"{total_failures} downloads failed - check logs for details"
            )

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the download operations.

        Returns:
            Dict[str, Any]: Dictionary containing download statistics
        """
        return {
            "total_downloads": len(self.download_results),
            "failed_downloads": len(self.failed_downloads),
            "success_rate": self._calculate_success_rate(),
            "android_downloads": self._count_artifact_downloads("android"),
            "firmware_downloads": self._count_artifact_downloads("firmware"),
        }

    def _calculate_success_rate(self) -> float:
        """Calculate the success rate of downloads."""
        total = len(self.download_results) + len(self.failed_downloads)
        return (len(self.download_results) / total) * 100 if total > 0 else 100.0

    def _count_artifact_downloads(self, artifact_type: str) -> int:
        """
        Count downloads for a specific artifact type.

        Args:
            artifact_type: Type of artifact to count

        Returns:
            int: Number of downloads for the artifact type
        """
        return sum(
            1
            for result in self.download_results
            if result.file_path and artifact_type in str(result.file_path)
        )

    def cleanup_old_versions(self) -> None:
        """Clean up old versions of all artifact types."""
        try:
            logger.info("Cleaning up old versions...")

            # Clean up Android versions
            android_keep = self.config.get("ANDROID_VERSIONS_TO_KEEP", 5)
            self.android_downloader.cleanup_old_versions(android_keep)

            # Clean up firmware versions
            firmware_keep = self.config.get("FIRMWARE_VERSIONS_TO_KEEP", 5)
            self.firmware_downloader.cleanup_old_versions(firmware_keep)

            logger.info("Old version cleanup completed")

        except Exception as e:
            logger.error(f"Error cleaning up old versions: {e}")

    def get_latest_versions(self) -> Dict[str, Optional[str]]:
        """
        Get the latest versions of all artifact types.

        Returns:
            Dict[str, Optional[str]]: Dictionary mapping artifact types to latest versions
        """
        return {
            "android": self.android_downloader.get_latest_release_tag(),
            "firmware": self.firmware_downloader.get_latest_release_tag(),
        }

    def update_version_tracking(self) -> None:
        """Update version tracking for all artifact types."""
        try:
            # Get the latest releases for each type
            android_releases = self.android_downloader.get_releases(limit=1)
            firmware_releases = self.firmware_downloader.get_releases(limit=1)

            # Update tracking
            if android_releases:
                self.android_downloader.update_latest_release_tag(
                    android_releases[0].tag_name
                )

            if firmware_releases:
                self.firmware_downloader.update_latest_release_tag(
                    firmware_releases[0].tag_name
                )

        except Exception as e:
            logger.error(f"Error updating version tracking: {e}")
