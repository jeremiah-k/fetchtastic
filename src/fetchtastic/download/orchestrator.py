"""
Download Pipeline Orchestrator

This module implements the orchestration layer that coordinates multiple
downloaders in a single fetchtastic download run.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fetchtastic.constants import (
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    PRERELEASE_TRACKING_JSON_FILE,
)
from fetchtastic.log_utils import logger

from .android import MeshtasticAndroidAppDownloader
from .cache import CacheManager
from .firmware import FirmwareReleaseDownloader
from .interfaces import DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
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
        self.prerelease_manager = PrereleaseHistoryManager()
        self.cache_manager = CacheManager()

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

        # Process firmware downloads
        self._process_firmware_downloads()

        # Process Android downloads
        self._process_android_downloads()

        # Legacy parity: Repository downloads are handled separately through the interactive
        # "repo browse" command and are not part of the automatic download pipeline.

        # Enhance results with metadata before retry
        self._enhance_download_results_with_metadata()

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

            # Limit releases to process to match legacy behavior
            keep_count = self.config.get(
                "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
            )
            android_releases = android_releases[:keep_count]

            # Filter releases based on configuration
            releases_to_download = self._filter_releases(android_releases, "android")

            # Download each release
            for release in releases_to_download:
                self._download_android_release(release)

            # Handle Android prereleases
            if any(r.prerelease for r in android_releases):
                self._refresh_commit_history_cache()
            prereleases = self.android_downloader.handle_prereleases(
                android_releases, recent_commits=getattr(self, "_recent_commits", None)
            )
            for prerelease in prereleases:
                for asset in prerelease.assets:
                    if not self.android_downloader.should_download_asset(asset.name):
                        continue
                    result = self.android_downloader.download_apk(prerelease, asset)
                    self._handle_download_result(result, "android_prerelease")

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

            latest_release = firmware_releases[0] if firmware_releases else None

            # Limit releases to process to match legacy behavior
            keep_count = self.config.get(
                "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
            )
            releases_to_check = firmware_releases[:keep_count]

            # Filter releases based on configuration
            releases_to_download = self._filter_releases(releases_to_check, "firmware")

            # Download each release
            for release in releases_to_download:
                self._download_firmware_release(release)

            # Handle prerelease selection based on commit history + expected version
            if any(r.prerelease for r in firmware_releases):
                self._refresh_commit_history_cache()
            prereleases = self.firmware_downloader.handle_prereleases(
                firmware_releases, recent_commits=getattr(self, "_recent_commits", None)
            )
            for prerelease in prereleases:
                for asset in prerelease.assets:
                    if not self.firmware_downloader.should_download_release(
                        prerelease.tag_name, asset.name
                    ):
                        continue
                    download_result = self.firmware_downloader.download_firmware(
                        prerelease, asset
                    )
                    self._handle_download_result(download_result, "firmware_prerelease")
                    if download_result.success:
                        extract_patterns = self._get_extraction_patterns()
                        exclude_patterns = self._get_exclude_patterns()
                        extract_result = self.firmware_downloader.extract_firmware(
                            prerelease, asset, extract_patterns, exclude_patterns
                        )
                        self._handle_download_result(
                            extract_result, "firmware_prerelease_extraction"
                        )

            # Legacy: firmware prereleases from meshtastic.github.io directories
            if latest_release:
                successes, failures, _active_dir = (
                    self.firmware_downloader.download_repo_prerelease_firmware(
                        latest_release.tag_name, force_refresh=False
                    )
                )
                for result in successes:
                    self._handle_download_result(result, "firmware_prerelease_repo")
                for result in failures:
                    self._handle_download_result(result, "firmware_prerelease_repo")

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
        existing: List[str] = []
        if artifact_type == "android":
            latest = self.android_downloader.get_latest_release_tag()
            if latest:
                existing.append(latest)
            android_dir = Path(self.android_downloader.download_dir) / "android"
            if android_dir.exists():
                existing.extend([p.name for p in android_dir.iterdir() if p.is_dir()])
        elif artifact_type == "firmware":
            latest = self.firmware_downloader.get_latest_release_tag()
            if latest:
                existing.append(latest)
            fw_dir = Path(self.firmware_downloader.download_dir) / "firmware"
            if fw_dir.exists():
                existing.extend([p.name for p in fw_dir.iterdir() if p.is_dir()])

        # Deduplicate
        return list(dict.fromkeys(existing))

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
                check_prereleases = self.config.get(
                    "CHECK_APK_PRERELEASES",
                    self.config.get("CHECK_PRERELEASES", False),
                )
            else:
                check_prereleases = self.config.get(
                    "CHECK_FIRMWARE_PRERELEASES",
                    self.config.get("CHECK_PRERELEASES", False),
                )

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
                if not self.android_downloader.should_download_asset(asset.name):
                    continue
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
            if getattr(result, "was_skipped", False) is True:
                logger.debug("Skipped %s: %s", operation_type, result.release_tag)
            else:
                logger.debug("Completed %s: %s", operation_type, result.release_tag)
        else:
            self.failed_downloads.append(result)
            error_msg = result.error_message or "Unknown error"
            logger.error(
                f"Failed {operation_type} for {result.release_tag}: {error_msg}"
            )
            if result.download_url:
                logger.error(f"URL: {result.download_url}")

    def _retry_failed_downloads(self) -> None:
        """Retry failed downloads with enhanced metadata and retry logic."""
        if not self.failed_downloads:
            return

        # Get retry configuration
        max_retries = self.config.get("MAX_RETRIES", 3)
        retry_delay = self.config.get("RETRY_DELAY_SECONDS", 0)
        retry_backoff_factor = self.config.get("RETRY_BACKOFF_FACTOR", 2.0)

        logger.info(
            f"Retrying {len(self.failed_downloads)} failed downloads with enhanced retry logic..."
        )

        retryable_failures = []
        non_retryable_failures = []

        # Separate retryable and non-retryable failures
        for failed_result in self.failed_downloads:
            if failed_result.is_retryable and failed_result.retry_count < max_retries:
                retryable_failures.append(failed_result)
            else:
                non_retryable_failures.append(failed_result)

        logger.info(
            f"Found {len(retryable_failures)} retryable failures and {len(non_retryable_failures)} non-retryable failures"
        )

        # Process retryable failures with exponential backoff
        for i, failed_result in enumerate(retryable_failures):
            try:
                # Calculate delay with exponential backoff
                current_delay = retry_delay * (
                    retry_backoff_factor**failed_result.retry_count
                )
                logger.info(
                    f"Waiting {current_delay:.1f} seconds before retry attempt {failed_result.retry_count + 1}/{max_retries}..."
                )

                if current_delay > 0:
                    time.sleep(current_delay)

                # Update retry metadata
                failed_result.retry_count += 1
                failed_result.retry_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                original_error = failed_result.error_message or "Unknown error"
                failed_result.error_message = f"Retry attempt {failed_result.retry_count}/{max_retries} - Original: {original_error}"

                # Log detailed retry information
                logger.info(f"Retrying download {i + 1}/{len(retryable_failures)}:")
                logger.info(f"  - Release: {failed_result.release_tag}")
                logger.info(f"  - URL: {failed_result.download_url}")
                logger.info(f"  - File: {failed_result.file_path}")
                logger.info(f"  - Error: {failed_result.error_type}")
                logger.info(f"  - Attempt: {failed_result.retry_count}/{max_retries}")

                retry_result = self._retry_single_failure(failed_result)
                operation = f"{retry_result.file_type or 'unknown'}_retry"
                self._handle_download_result(retry_result, operation)

            except Exception as e:
                logger.error(f"Retry failed for {failed_result.release_tag}: {e}")
                # Mark as non-retryable after max attempts
                failed_result.is_retryable = False
                failed_result.error_message = f"Max retries exceeded: {str(e)}"
                non_retryable_failures.append(failed_result)

        # Update the failed downloads list with only non-retryable failures
        self.failed_downloads = non_retryable_failures

        # Generate detailed retry report
        self._generate_retry_report(retryable_failures, non_retryable_failures)

    def _retry_single_failure(self, failed_result: DownloadResult) -> DownloadResult:
        """
        Retry a single failed download using stored metadata.

        Returns:
            DownloadResult: Updated result after retry attempt.
        """
        url = failed_result.download_url
        target_path = str(failed_result.file_path) if failed_result.file_path else None
        file_type = failed_result.file_type or "unknown"

        if not url or not target_path:
            return DownloadResult(
                success=False,
                release_tag=failed_result.release_tag,
                file_path=failed_result.file_path,
                download_url=url,
                file_size=failed_result.file_size,
                file_type=file_type,
                retry_count=failed_result.retry_count,
                retry_timestamp=failed_result.retry_timestamp,
                error_message="Retry skipped: missing URL or target path",
                error_type="retry_failure",
                is_retryable=False,
            )

        try:
            if file_type == "android":
                ok = self.android_downloader.download(url, target_path)
                if ok and self.android_downloader.verify(target_path):
                    return DownloadResult(
                        success=True,
                        release_tag=failed_result.release_tag,
                        file_path=Path(target_path),
                        download_url=url,
                        file_size=failed_result.file_size,
                        file_type=file_type,
                        retry_count=failed_result.retry_count,
                        retry_timestamp=failed_result.retry_timestamp,
                        error_message=None,
                        is_retryable=False,
                    )

            elif file_type == "firmware":
                ok = self.firmware_downloader.download(url, target_path)
                if ok and self.firmware_downloader.verify(target_path):
                    return DownloadResult(
                        success=True,
                        release_tag=failed_result.release_tag,
                        file_path=Path(target_path),
                        download_url=url,
                        file_size=failed_result.file_size,
                        file_type=file_type,
                        retry_count=failed_result.retry_count,
                        retry_timestamp=failed_result.retry_timestamp,
                        error_message=None,
                        is_retryable=False,
                    )
            else:
                logger.debug("Retry not supported for file type: %s", file_type)

            # If we reach here, retry failed verification or download
            return DownloadResult(
                success=False,
                release_tag=failed_result.release_tag,
                file_path=Path(target_path),
                download_url=url,
                file_size=failed_result.file_size,
                file_type=file_type,
                retry_count=failed_result.retry_count,
                retry_timestamp=failed_result.retry_timestamp,
                error_message="Retry attempt failed",
                error_type="retry_failure",
                is_retryable=failed_result.retry_count
                < self.config.get("MAX_RETRIES", 3),
            )

        except Exception as exc:
            logger.error(f"Retry exception for {failed_result.release_tag}: {exc}")
            return DownloadResult(
                success=False,
                release_tag=failed_result.release_tag,
                file_path=Path(target_path),
                download_url=url,
                file_size=failed_result.file_size,
                file_type=file_type,
                retry_count=failed_result.retry_count,
                retry_timestamp=failed_result.retry_timestamp,
                error_message=str(exc),
                error_type="retry_failure",
                is_retryable=False,
            )

    def _generate_retry_report(
        self,
        retryable_failures: List[DownloadResult],
        non_retryable_failures: List[DownloadResult],
    ) -> None:
        """
        Generate a detailed retry report with statistics and metadata.

        Args:
            retryable_failures: List of failures that were retryable
            non_retryable_failures: List of failures that were not retryable
        """
        total_failures = len(retryable_failures) + len(non_retryable_failures)
        retry_success_rate = 0.0

        if retryable_failures:
            # Count how many retries succeeded (by checking if they're no longer in failed_downloads)
            successful_retries = len(retryable_failures) - len(
                [f for f in retryable_failures if f in self.failed_downloads]
            )
            retry_success_rate = (successful_retries / len(retryable_failures)) * 100

        logger.info("\n" + "=" * 60)
        logger.info("📊 DETAILED RETRY REPORT")
        logger.info("=" * 60)

        logger.info("📈 Overall Statistics:")
        logger.info(f"  - Total failures processed: {total_failures}")
        logger.info(f"  - Retryable failures: {len(retryable_failures)}")
        logger.info(f"  - Non-retryable failures: {len(non_retryable_failures)}")
        logger.info(f"  - Retry success rate: {retry_success_rate:.1f}%")

        if retryable_failures:
            logger.info("\n🔄 Retryable Failures Summary:")
            by_type: Dict[str, int] = {}
            for failure in retryable_failures:
                failure_type = failure.file_type or "unknown"
                by_type[failure_type] = by_type.get(failure_type, 0) + 1

            for file_type, count in by_type.items():
                logger.info(f"  - {file_type}: {count} failures")

            # Show retry distribution
            by_attempt: Dict[int, int] = {}
            for failure in retryable_failures:
                attempt = failure.retry_count
                by_attempt[attempt] = by_attempt.get(attempt, 0) + 1

            logger.info("\n📊 Retry Attempt Distribution:")
            for attempt, count in sorted(by_attempt.items()):
                logger.info(f"  - Attempt {attempt}: {count} failures")

        if non_retryable_failures:
            logger.info("\n❌ Non-Retryable Failures Summary:")
            by_reason: Dict[str, int] = {}
            for failure in non_retryable_failures:
                reason = failure.error_type or "unknown_error"
                by_reason[reason] = by_reason.get(reason, 0) + 1

            for reason, count in by_reason.items():
                logger.info(f"  - {reason}: {count} failures")

        logger.info("\n💡 Retry Configuration:")
        logger.info(f"  - Max retries: {self.config.get('MAX_RETRIES', 3)}")
        logger.info(
            f"  - Base delay: {self.config.get('RETRY_DELAY_SECONDS', 0)} seconds"
        )
        logger.info(
            f"  - Backoff factor: {self.config.get('RETRY_BACKOFF_FACTOR', 2.0)}"
        )

        logger.info("=" * 60 + "\n")

    def _enhance_download_results_with_metadata(self) -> None:
        """
        Enhance download results with additional metadata for better reporting and retry handling.

        This method should be called after all downloads are complete to ensure all results
        have proper metadata populated.
        """
        for result in self.download_results + self.failed_downloads:
            # Set file type based on file path if not already set
            if not result.file_type and result.file_path:
                file_path_str = str(result.file_path)
                if "android" in file_path_str:
                    result.file_type = "android"
                elif "firmware" in file_path_str:
                    result.file_type = "firmware"
                elif "repository" in file_path_str or "repo-dls" in file_path_str:
                    result.file_type = "repository"
                else:
                    result.file_type = "unknown"

            # Set retry metadata for failed downloads
            if not result.success and result.retry_count == 0:
                result.is_retryable = self._is_download_retryable(result)
                result.retry_count = 0

    def _is_download_retryable(self, result: DownloadResult) -> bool:
        """
        Determine if a failed download is retryable based on error type and configuration.

        Args:
            result: The download result to check

        Returns:
            bool: True if the download should be retried
        """
        if not result.error_type:
            return True  # Unknown errors are retryable by default

        # These error types are generally retryable
        retryable_errors = {
            "network_error",
            "connection_error",
            "timeout",
            "http_error",
            "rate_limit",
            "temporary_failure",
        }

        # These error types are generally not retryable
        non_retryable_errors = {
            "permission_error",
            "validation_error",
            "corrupted_file",
            "disk_full",
            "invalid_url",
            "authentication_error",
        }

        if result.error_type in retryable_errors:
            return True
        elif result.error_type in non_retryable_errors:
            return False
        else:
            # Default to retryable for unknown error types
            return True

    def _log_download_summary(self, start_time: float) -> None:
        """Log a summary of the download results."""
        elapsed_time = time.time() - start_time
        downloaded = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        ]
        skipped = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is True
        ]
        total_failures = len(self.failed_downloads)

        logger.info("Download pipeline completed")
        logger.info(f"Time taken: {elapsed_time:.2f} seconds")
        logger.info(
            "Downloads: %d downloaded, %d skipped, %d failed",
            len(downloaded),
            len(skipped),
            total_failures,
        )

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
        downloaded = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        ]
        skipped = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is True
        ]
        attempted = len(downloaded) + len(self.failed_downloads)
        return {
            # "Downloads" excludes skipped results for legacy-parity reporting.
            "total_downloads": attempted,
            "successful_downloads": len(downloaded),
            "skipped_downloads": len(skipped),
            "failed_downloads": len(self.failed_downloads),
            "success_rate": self._calculate_success_rate(),
            "android_downloads": self._count_artifact_downloads("android"),
            "firmware_downloads": self._count_artifact_downloads("firmware"),
            # Repository downloads are not part of the automatic download pipeline.
            "repository_downloads": 0,
        }

    def _calculate_success_rate(self) -> float:
        """Calculate the success rate of downloads."""
        downloaded_count = sum(
            1
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        )
        attempted = downloaded_count + len(self.failed_downloads)
        return (downloaded_count / attempted) * 100 if attempted > 0 else 100.0

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
            if (
                getattr(result, "was_skipped", False) is not True
                and (
                    result.file_type == artifact_type
                    or (result.file_path and artifact_type in str(result.file_path))
                )
            )
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
        firmware_prerelease = None
        tracking_path = (
            Path(self.cache_manager.cache_dir) / PRERELEASE_TRACKING_JSON_FILE
        )
        if tracking_path.exists():
            try:
                data = self.cache_manager.read_json(str(tracking_path))
                commits = data.get("commits") if isinstance(data, dict) else None
                if isinstance(commits, list) and commits:
                    firmware_prerelease = str(commits[-1])
            except Exception:
                firmware_prerelease = None

        return {
            "android": self.android_downloader.get_latest_release_tag(),
            "firmware": self.firmware_downloader.get_latest_release_tag(),
            "firmware_prerelease": firmware_prerelease,
            "android_prerelease": None,
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

            # Manage prerelease tracking files
            self._manage_prerelease_tracking()

        except Exception as e:
            logger.error(f"Error updating version tracking: {e}")

    def _manage_prerelease_tracking(self) -> None:
        """
        Manage prerelease tracking files for all artifact types.

        This method calls the prerelease management functions for both
        Android and firmware downloaders to clean up superseded prereleases
        and maintain tracking file consistency.
        """
        try:
            logger.info("Managing prerelease tracking files...")

            # Share recent commits with downloaders for prerelease filtering

            # Manage Android prerelease tracking
            self.android_downloader.manage_prerelease_tracking_files()

            # Manage firmware prerelease tracking
            self.firmware_downloader.manage_prerelease_tracking_files()

            logger.info("Prerelease tracking management completed")

        except Exception as e:
            logger.error(f"Error managing prerelease tracking: {e}")

    def _refresh_commit_history_cache(self) -> None:
        """Refresh commit history cache used for prerelease expected-version selection."""
        try:
            self._recent_commits = self.prerelease_manager.fetch_recent_repo_commits(
                limit=10,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=True,
                force_refresh=False,
            )
        except Exception as e:
            logger.debug(f"Skipping commit history refresh: {e}")
