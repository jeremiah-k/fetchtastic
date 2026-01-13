"""
Download Pipeline Orchestrator

This module implements the orchestration layer that coordinates multiple
downloaders in a single fetchtastic download run.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    APKS_DIR_NAME,
    DEFAULT_ANDROID_VERSIONS_TO_KEEP,
    DEFAULT_FILTER_REVOKED_RELEASES,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    DEFAULT_KEEP_LAST_BETA,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    ERROR_TYPE_RETRY_FAILURE,
    ERROR_TYPE_UNKNOWN,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
    FILE_TYPE_REPOSITORY,
    FILE_TYPE_UNKNOWN,
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_PRERELEASES_DIR_NAME,
    MAX_RETRY_DELAY,
    RELEASE_SCAN_COUNT,
    REPO_DOWNLOADS_DIR,
)
from fetchtastic.log_utils import logger
from fetchtastic.setup_config import is_termux
from fetchtastic.utils import cleanup_legacy_hash_sidecars

from .android import MeshtasticAndroidAppDownloader
from .base import BaseDownloader
from .cache import CacheManager
from .files import _safe_rmtree
from .firmware import FirmwareReleaseDownloader
from .interfaces import DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .version import VersionManager, is_prerelease_directory


def is_connected_to_wifi() -> bool:
    """
    Check if device is connected to Wi-Fi.

    For Termux, it uses 'termux-wifi-connectioninfo'.
    For other platforms, it currently assumes connected.

    Returns:
        bool: True if connected to Wi-Fi (or assumed to be), False otherwise.
    """
    if not is_termux():
        return True

    try:
        process = subprocess.run(
            ["termux-wifi-connectioninfo"],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            error_message = process.stderr.strip()
            logger.warning(
                f"termux-wifi-connectioninfo command failed with exit code {process.returncode}: {error_message}"
            )
            return False

        output = process.stdout.strip()
        if not output:
            return False

        data = json.loads(output)
        supplicant_state = data.get("supplicant_state", "")
        ip_address = data.get("ip", "")
        return supplicant_state == "COMPLETED" and ip_address != ""
    except json.JSONDecodeError as e:
        logger.warning(f"Error decoding JSON from termux-wifi-connectioninfo: {e}")
        return False
    except FileNotFoundError:
        logger.warning(
            "termux-wifi-connectioninfo command not found. Is Termux:API installed and configured?"
        )
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(
            f"Unexpected error checking Wi-Fi connection: {e}", exc_info=True
        )
        return False


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
        Create a DownloadOrchestrator configured to run the download pipeline.

        Initializes version, prerelease history, and cache managers; instantiates Android and firmware downloaders with the provided cache manager; and prepares result lists and in-run release caches.

        Parameters:
            config (Dict[str, Any]): Configuration mapping used by the orchestrator and its downloaders (controls behavior such as keep counts, prerelease handling, retry settings, extraction/exclude patterns, etc.).
        """
        self.config = config
        self.version_manager = VersionManager()
        self.prerelease_manager = PrereleaseHistoryManager()
        self.cache_manager = CacheManager()

        # Initialize downloaders
        self.android_downloader: MeshtasticAndroidAppDownloader = (
            MeshtasticAndroidAppDownloader(config, self.cache_manager)
        )
        self.firmware_downloader: FirmwareReleaseDownloader = FirmwareReleaseDownloader(
            config, self.cache_manager
        )

        # Track results
        self.download_results: List[DownloadResult] = []
        self.failed_downloads: List[DownloadResult] = []

        # Cache releases to avoid redundant API calls within a single run
        # None => complete/unbounded fetch; int => fetched with a limit (partial cache)
        self._android_releases_fetch_limit: Optional[int] = None
        self.android_releases: Optional[List[Release]] = None
        self._firmware_releases_fetch_limit: Optional[int] = None
        self.firmware_releases: Optional[List[Release]] = None
        self.firmware_release_history: Optional[Dict[str, Any]] = None
        # Single-run only: cleared by log_firmware_release_history_summary()
        self.firmware_prerelease_summary: Optional[Dict[str, Any]] = None

    def run_download_pipeline(
        self,
    ) -> Tuple[List[DownloadResult], List[DownloadResult]]:
        """
        Orchestrates discovery, downloading, retrying, and summary reporting for all configured artifact types.

        Returns:
            Tuple[List[DownloadResult], List[DownloadResult]]: A tuple (successful_results, failed_results) where `successful_results` is the list of completed DownloadResult entries and `failed_results` is the list of DownloadResult entries that remain failed after retry attempts.
        """
        start_time = time.time()
        logger.info("Starting download pipeline...")
        logger.debug(
            "Execution context: cwd=%s, python=%s, fetchtastic=%s",
            os.getcwd(),
            sys.executable,
            shutil.which("fetchtastic"),
        )

        if is_termux() and self.config.get("WIFI_ONLY", False):
            if not is_connected_to_wifi():
                logger.warning("Not connected to Wi-Fi. Skipping all downloads.")
                return [], []

        cleanup_legacy_hash_sidecars(self.config.get("DOWNLOAD_DIR", ""))

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
        """
        Coordinate discovery and retrieval of Android APK releases and prerelease APK assets.

        Checks configuration to determine whether APKs should be saved; if enabled, ensures release metadata is available, considers the configured number of recent stable releases, skips releases already marked complete, downloads missing release assets, processes eligible prerelease APKs, and records each asset's outcome in the orchestrator's result lists.
        """
        try:
            if not self.config.get("SAVE_APKS", False):
                logger.info("Android APK downloads are disabled in configuration")
                return

            logger.info("Scanning Android APK releases")
            android_releases = self._ensure_android_releases()
            if not android_releases:
                logger.info("No Android releases found")
                return

            self.android_downloader.update_release_history(android_releases)
            keep_count = self.config.get(
                "ANDROID_VERSIONS_TO_KEEP", DEFAULT_ANDROID_VERSIONS_TO_KEEP
            )
            stable_releases = [r for r in android_releases if not r.prerelease]
            releases_to_process = stable_releases[:keep_count]

            releases_to_download = []
            for release in releases_to_process:
                self.android_downloader.ensure_release_notes(release)
                suffix = self.android_downloader.format_release_log_suffix(release)
                logger.info(f"Checking {release.tag_name}{suffix}…")
                if self.android_downloader.is_release_complete(release):
                    logger.debug(
                        f"Release {release.tag_name} already exists and is complete"
                    )
                else:
                    releases_to_download.append(release)

            any_android_downloaded = False
            if releases_to_download:
                for release in releases_to_download:
                    logger.info(f"Downloading Android release {release.tag_name}")
                    if self._download_android_release(release):
                        any_android_downloaded = True

            logger.info("Checking for pre-release APK...")
            prereleases = self.android_downloader.handle_prereleases(android_releases)
            for prerelease in prereleases:
                self.android_downloader.ensure_release_notes(prerelease)
                for asset in prerelease.assets:
                    if not self.android_downloader.should_download_asset(asset.name):
                        continue
                    result = self.android_downloader.download_apk(prerelease, asset)
                    if result.success and not result.was_skipped:
                        any_android_downloaded = True
                    self._handle_download_result(result, FILE_TYPE_ANDROID_PRERELEASE)

            if (
                self.config.get(
                    "CHECK_APK_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
                )
                and android_releases
                and not prereleases
            ):
                logger.info("No pre-release APKs available")

            if not any_android_downloaded and not releases_to_download:
                logger.info("All Android APK assets are up to date.")

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error processing Android downloads: {e}", exc_info=True)

    def _ensure_android_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Return cached Android releases, fetching them once from the downloader if not already cached.

        Parameters:
            limit (Optional[int]): Maximum number of releases to fetch on the initial request; if releases are already cached with a smaller limit and the requested limit is larger or None (unbounded), refetches to ensure a complete result set.

        Returns:
            List[Release]: The cached list of Android releases.
        """
        return self._ensure_releases(
            downloader=self.android_downloader,
            releases_attr="android_releases",
            fetch_limit_attr="_android_releases_fetch_limit",
            limit=limit,
        )

    def _ensure_releases(
        self,
        downloader: Union[MeshtasticAndroidAppDownloader, FirmwareReleaseDownloader],
        releases_attr: str,
        fetch_limit_attr: str,
        limit: Optional[int] = None,
    ) -> List[Release]:
        """
        Generic helper to fetch and cache releases with partial cache detection.

        Parameters:
            downloader: The downloader instance to use for fetching releases.
            releases_attr (str): Attribute name on self that holds the cached releases list.
            fetch_limit_attr (str): Attribute name on self that holds the fetch limit used for caching.
            limit (Optional[int]): Maximum number of releases to fetch; if releases are already cached with a smaller limit and the requested limit is larger or None (unbounded), refetches to ensure a complete result set.

        Returns:
            List[Release]: The cached or newly fetched list of releases.
        """
        if limit == 0:
            return []

        current_releases = getattr(self, releases_attr)
        current_fetch_limit = getattr(self, fetch_limit_attr)

        should_fetch = current_releases is None or (
            current_fetch_limit is not None
            and (limit is None or limit > current_fetch_limit)
        )

        if should_fetch:
            new_releases = downloader.get_releases(limit=limit) or []
            setattr(self, releases_attr, new_releases)
            setattr(self, fetch_limit_attr, limit)
            return new_releases

        cached = current_releases or []
        if limit is None:
            return cached
        return cached[: max(0, limit)]

    def _ensure_firmware_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Return cached firmware releases, fetching them once from the downloader if not already cached.

        Parameters:
            limit (Optional[int]): Maximum number of releases to fetch on the initial request; if releases are already cached with a smaller limit and the requested limit is larger or None (unbounded), refetches to ensure a complete result set.

        Returns:
            List[Release]: The cached list of firmware releases.
        """
        return self._ensure_releases(
            downloader=self.firmware_downloader,
            releases_attr="firmware_releases",
            fetch_limit_attr="_firmware_releases_fetch_limit",
            limit=limit,
        )

    def _process_firmware_downloads(self) -> None:
        """
        Ensure configured firmware releases and repository prereleases are present locally and remove unmanaged prerelease directories.

        Scans firmware releases according to retention and filtering settings, downloads missing release assets and repository prerelease firmware for the selected latest release, records each outcome in the orchestrator's result lists, and safely removes unexpected or unmanaged directories from the firmware prereleases folder. Network, filesystem, and parsing errors encountered during processing are caught and logged.
        """
        try:
            if not self.config.get("SAVE_FIRMWARE", False):
                logger.info("Firmware downloads are disabled in configuration")
                return

            logger.info("Scanning Firmware releases")
            keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
            keep_limit = self._get_firmware_keep_limit()
            filter_revoked = self.config.get(
                "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
            )
            fetch_limit = (
                max(keep_limit, RELEASE_SCAN_COUNT) if keep_last_beta else keep_limit
            )
            if filter_revoked and fetch_limit > 0:
                fetch_limit += RELEASE_SCAN_COUNT
            fetch_limit = min(100, fetch_limit if fetch_limit >= 0 else 0)
            firmware_releases = self._ensure_firmware_releases(limit=fetch_limit)
            if not firmware_releases:
                logger.info("No firmware releases found")
                return

            self.firmware_release_history = (
                self.firmware_downloader.update_release_history(
                    firmware_releases, log_summary=False
                )
            )
            latest_release = self._select_latest_release_by_version(firmware_releases)
            (
                releases_for_processing,
                firmware_releases,
                fetch_limit,
            ) = self.firmware_downloader.collect_non_revoked_releases(
                initial_releases=firmware_releases,
                target_count=keep_limit,
                current_fetch_limit=fetch_limit,
            )
            self.firmware_releases = firmware_releases

            releases_to_process = releases_for_processing[:keep_limit]
            if keep_last_beta:
                most_recent_beta = self.firmware_downloader.release_history_manager.find_most_recent_beta(
                    releases_for_processing
                )
                if most_recent_beta and most_recent_beta not in releases_to_process:
                    releases_to_process.append(most_recent_beta)

            releases_to_download = []
            for release in releases_to_process:
                suffix = self.firmware_downloader.format_release_log_suffix(release)
                logger.info(f"Checking {release.tag_name}{suffix}…")
                if self.firmware_downloader.is_release_complete(release):
                    self.firmware_downloader.ensure_release_notes(release)
                    logger.debug(
                        f"Release {release.tag_name} already exists and is complete"
                    )
                else:
                    releases_to_download.append(release)

            any_firmware_downloaded = False
            if releases_to_download:
                for release in releases_to_download:
                    logger.info(f"Downloading firmware release {release.tag_name}")
                    self.firmware_downloader.ensure_release_notes(release)
                    if self._download_firmware_release(release):
                        any_firmware_downloaded = True

            if latest_release:
                (
                    successes,
                    failures,
                    _active_dir,
                    prerelease_summary,
                ) = self.firmware_downloader.download_repo_prerelease_firmware(
                    latest_release.tag_name, force_refresh=False
                )
                if prerelease_summary:
                    self.firmware_prerelease_summary = prerelease_summary
                for result in successes:
                    if not result.was_skipped:
                        any_firmware_downloaded = True
                    self._handle_download_result(
                        result, FILE_TYPE_FIRMWARE_PRERELEASE_REPO
                    )
                for result in failures:
                    self._handle_download_result(
                        result, FILE_TYPE_FIRMWARE_PRERELEASE_REPO
                    )

            if not any_firmware_downloaded and not releases_to_download:
                logger.info("All Firmware assets are up to date.")

            # Clean up prerelease directory
            prerelease_dir = (
                Path(self.firmware_downloader.download_dir)
                / FIRMWARE_DIR_NAME
                / FIRMWARE_PRERELEASES_DIR_NAME
            )
            if prerelease_dir.exists():
                for item in prerelease_dir.iterdir():
                    # Skip symlinks to prevent path traversal attacks
                    if item.is_symlink():
                        logger.warning(
                            f"Skipping symlink in prerelease folder: {item.name}"
                        )
                        continue
                    if not item.is_dir():
                        continue

                    # Only remove directories that are clearly Fetchtastic-managed prerelease
                    # directories (firmware prefix + parseable version). This prevents accidental
                    # deletion of user-created directories under the prerelease folder.
                    if not item.name.startswith(FIRMWARE_DIR_PREFIX):
                        continue
                    suffix = item.name[len(FIRMWARE_DIR_PREFIX) :]
                    if self.version_manager.get_release_tuple(suffix) is None:
                        logger.warning(
                            "Skipping unexpected directory in prerelease folder: %s",
                            item.name,
                        )
                        continue

                    # A prerelease directory should contain a hash; if it doesn't, it's likely a
                    # stable release directory misplaced into the prerelease folder.
                    if not is_prerelease_directory(item.name):
                        logger.warning(
                            "Removing unexpected directory from prerelease folder: %s",
                            item.name,
                        )
                        if not _safe_rmtree(str(item), str(prerelease_dir), item.name):
                            logger.warning(
                                "Failed to safely remove directory: %s", item.name
                            )

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error processing firmware downloads: {e}", exc_info=True)

    def _select_latest_release_by_version(
        self, releases: List[Release]
    ) -> Optional[Release]:
        """
        Choose the release with the highest semantic version parsed from its tag name, preferring non-revoked releases when possible.

        If one or more tag names parse as semantic versions, returns the release whose parsed version is greatest. If no tag parses successfully, returns the first release in the provided list. Returns None when the input list is empty.

        Returns:
            The selected Release, the first release if no tags parse, or None if no releases were provided.
        """
        best_release: Optional[Release] = None
        best_tuple: Optional[Tuple[int, ...]] = None
        best_revoked_release: Optional[Release] = None
        best_revoked_tuple: Optional[Tuple[int, ...]] = None

        for release in releases:
            release_tuple = self.version_manager.get_release_tuple(release.tag_name)
            if release_tuple is None:
                continue
            is_revoked = self.firmware_downloader.is_release_revoked(release)
            if is_revoked:
                if best_revoked_tuple is None or release_tuple > best_revoked_tuple:
                    best_revoked_tuple = release_tuple
                    best_revoked_release = release
            else:
                if best_tuple is None or release_tuple > best_tuple:
                    best_tuple = release_tuple
                    best_release = release

        return (
            best_release or best_revoked_release or (releases[0] if releases else None)
        )

    def _download_android_release(self, release: Release) -> bool:
        """
        Download all eligible assets for a given Android release and record each asset's result.

        Parameters:
            release (Release): The Android release whose assets should be downloaded.

        Returns:
            `True` if any asset was downloaded, `False` otherwise.
        """
        any_downloaded = False
        try:
            # Download each asset in the release
            for asset in release.assets:
                if not self.android_downloader.should_download_asset(asset.name):
                    continue
                result = self.android_downloader.download_apk(release, asset)
                if result.success and not result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(result, FILE_TYPE_ANDROID)
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error downloading Android release {release.tag_name}: {e}")
            return False
        else:
            return any_downloaded

    def _download_firmware_release(self, release: Release) -> bool:
        """
        Download firmware assets from a release and optionally extract them based on configuration.

        If matching assets are found they are downloaded. Extraction is performed only when the
        `AUTO_EXTRACT` configuration flag is true and the release was not skipped due to being revoked.

        Parameters:
            release (Release): Firmware release whose matching assets will be downloaded and (optionally) extracted.

        Returns:
            bool: `True` if at least one asset was downloaded, `False` otherwise.
        """
        any_downloaded = False
        try:
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
                return False

            # Download each asset in the release
            for asset in assets_to_download:
                # Download the firmware ZIP
                download_result = self.firmware_downloader.download_firmware(
                    release, asset
                )
                if download_result.success and not download_result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(download_result, FILE_TYPE_FIRMWARE)

                # If download succeeded, extract files if AUTO_EXTRACT is enabled.
                # Skip extraction when a release is intentionally skipped (e.g., revoked).
                if (
                    download_result.success
                    and self.config.get("AUTO_EXTRACT", False)
                    and not (
                        download_result.was_skipped
                        and download_result.error_type == "revoked_release"
                    )
                ):
                    extract_result = self.firmware_downloader.extract_firmware(
                        release, asset, extract_patterns, exclude_patterns
                    )
                    self._handle_download_result(extract_result, "firmware_extraction")
            return any_downloaded
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error downloading firmware release {release.tag_name}: {e}")
            return False

    def _get_extraction_patterns(self) -> List[str]:
        """
        Retrieve extraction filename patterns from the orchestrator configuration.

        Returns:
            List[str]: Filename patterns to extract. If the configured value is a single string, it is returned as a one-element list.
        """
        patterns = self.config.get("EXTRACT_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _get_exclude_patterns(self) -> List[str]:
        """
        Normalize and return filename patterns to exclude from processing.

        If the configuration key "EXCLUDE_PATTERNS" is missing, returns an empty list. If the configured value is a string, it is wrapped in a single-element list; if it is already a list, it is returned unchanged.

        Returns:
            List[str]: Filename patterns to exclude.
        """
        patterns = self.config.get("EXCLUDE_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _handle_download_result(
        self, result: DownloadResult, operation_type: str
    ) -> None:
        """
        Record a download result by adding it to the orchestrator's success or failure lists and logging the outcome.

        Parameters:
            result (DownloadResult): The result of a download attempt. If `result.success` is True the result is appended to `download_results`; if `result.success` is False it is appended to `failed_downloads`. A `was_skipped` attribute on `result` (when present and True) is treated as a skipped success.
            operation_type (str): Human-readable operation/category used in logs (for example 'android', 'firmware', or include 'prerelease' to indicate prerelease handling).
        """
        if result.success:
            self.download_results.append(result)
            if getattr(result, "was_skipped", False) is True:
                if "prerelease" not in operation_type:
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
        """
        Retry failed downloads using per-result metadata and exponential backoff.

        Reads MAX_RETRIES, RETRY_DELAY_SECONDS, and RETRY_BACKOFF_FACTOR from configuration, separates failures into retryable and non-retryable groups, and attempts retries for eligible failures. Each retry increments the result's retry count, stamps a retry timestamp, updates the error message with retry context, waits using exponential backoff, and records the retry outcome (successful retries are moved to completed results; persistent failures are marked non-retryable and retained). After processing, replaces the stored failed downloads with the remaining non-retryable failures and generates a summary report of retry activity.
        """
        if not self.failed_downloads:
            return

        # Get retry configuration
        max_retries = self.config.get("MAX_RETRIES", 3)
        retry_delay = self.config.get("RETRY_DELAY_SECONDS", 0)
        retry_backoff_factor = self.config.get("RETRY_BACKOFF_FACTOR", 2.0)

        logger.info(
            f"Retrying {len(self.failed_downloads)} failed downloads with enhanced retry logic..."
        )

        retryable_failures: List[DownloadResult] = []
        non_retryable_failures: List[DownloadResult] = []

        # Separate retryable and non-retryable failures
        original_failures = list(self.failed_downloads)
        for failed_result in original_failures:
            if failed_result.is_retryable and failed_result.retry_count < max_retries:
                retryable_failures.append(failed_result)
            else:
                non_retryable_failures.append(failed_result)

        logger.info(
            f"Found {len(retryable_failures)} retryable failures and {len(non_retryable_failures)} non-retryable failures"
        )

        remaining_failures: List[DownloadResult] = list(non_retryable_failures)

        # Process retryable failures with exponential backoff
        for i, failed_result in enumerate(retryable_failures):
            try:
                # Calculate delay with exponential backoff
                current_delay = retry_delay * (
                    retry_backoff_factor**failed_result.retry_count
                )
                current_delay = min(current_delay, MAX_RETRY_DELAY)
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
                if retry_result.success:
                    failed_result.success = True
                    failed_result.file_path = retry_result.file_path
                    failed_result.extracted_files = retry_result.extracted_files
                    failed_result.error_message = None
                    failed_result.error_type = None
                    failed_result.error_details = None
                    failed_result.http_status_code = None
                    failed_result.is_retryable = False
                    failed_result.was_skipped = retry_result.was_skipped

                    self.download_results.append(failed_result)
                    logger.debug(
                        "Completed %s: %s", operation, failed_result.release_tag
                    )
                else:
                    failed_result.success = False
                    failed_result.file_path = (
                        retry_result.file_path or failed_result.file_path
                    )
                    failed_result.extracted_files = retry_result.extracted_files
                    failed_result.error_message = (
                        retry_result.error_message or failed_result.error_message
                    )
                    failed_result.error_type = retry_result.error_type
                    failed_result.error_details = retry_result.error_details
                    failed_result.http_status_code = retry_result.http_status_code
                    failed_result.is_retryable = retry_result.is_retryable
                    failed_result.was_skipped = False

                    remaining_failures.append(failed_result)
                    error_msg = failed_result.error_message or "Unknown error"
                    logger.error(
                        "Failed %s for %s: %s",
                        operation,
                        failed_result.release_tag,
                        error_msg,
                    )
                    if failed_result.download_url:
                        logger.error("URL: %s", failed_result.download_url)

            except (requests.RequestException, OSError, ValueError, TypeError) as e:
                logger.error(f"Retry failed for {failed_result.release_tag}: {e}")
                # Mark as non-retryable after max attempts
                failed_result.is_retryable = False
                failed_result.error_message = f"Max retries exceeded: {e!s}"
                remaining_failures.append(failed_result)

        # Update the failed downloads list with remaining failures (including failed retries)
        self.failed_downloads = remaining_failures

        # Generate detailed retry report
        self._generate_retry_report(retryable_failures, non_retryable_failures)

    def _create_failure_result(
        self,
        failed_result: DownloadResult,
        file_path: Path,
        download_url: str,
        file_type: str,
        error_message: str,
        exception_message: Optional[str] = None,
        is_retryable_override: Optional[bool] = None,
    ) -> DownloadResult:
        """
        Create a standardized failure DownloadResult populated for retry handling.

        Parameters:
            failed_result (DownloadResult): Original failed result whose metadata (release_tag, file_size, retry_count, retry_timestamp) will be carried forward.
            file_path (Path): Target filesystem path for the attempted download.
            download_url (str): URL that was being downloaded.
            file_type (str): Logical file type to record (e.g., "android", "firmware").
            error_message (str): Human-readable description of the failure.
            exception_message (Optional[str]): Optional low-level exception text to prefer over error_message when present.
            is_retryable_override (Optional[bool]): If provided, explicitly sets the result's retryability; otherwise retryability is derived from retry_count and MAX_RETRIES config.

        Returns:
            DownloadResult: A failure result with success=False, populated error fields, preserved retry metadata, and an `is_retryable` flag.
        """
        if is_retryable_override is not None:
            is_retryable = is_retryable_override
        else:
            is_retryable = failed_result.retry_count < self.config.get("MAX_RETRIES", 3)

        final_message = exception_message or error_message

        return DownloadResult(
            success=False,
            release_tag=failed_result.release_tag,
            file_path=file_path,
            download_url=download_url,
            file_size=failed_result.file_size,
            file_type=file_type,
            retry_count=failed_result.retry_count,
            retry_timestamp=failed_result.retry_timestamp,
            error_message=final_message,
            error_type=ERROR_TYPE_RETRY_FAILURE,
            is_retryable=is_retryable,
        )

    def _retry_single_failure(self, failed_result: DownloadResult) -> DownloadResult:
        """
        Attempt a single retry of a previously failed download using metadata from the provided DownloadResult.

        Parameters:
            failed_result (DownloadResult): The original failed download result containing the URL, target path, retry counters, and file type used to perform the retry.

        Returns:
            DownloadResult: A result representing the outcome of the retry. On success the returned result has `success=True` and contains the validated file path; on failure the returned result has `success=False` and includes an error message and updated `is_retryable`/retry metadata.
        """
        url = failed_result.download_url
        target_path = str(failed_result.file_path) if failed_result.file_path else None
        file_type = failed_result.file_type or FILE_TYPE_UNKNOWN

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
                error_type=ERROR_TYPE_RETRY_FAILURE,
                is_retryable=False,
            )

        try:
            downloader: Optional[BaseDownloader] = None
            if file_type == "android":
                downloader = self.android_downloader
            elif file_type == "firmware":
                downloader = self.firmware_downloader

            if downloader:
                ok = downloader.download(url, target_path)
                if ok and downloader.verify(target_path):
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
            return self._create_failure_result(
                failed_result, Path(target_path), url, file_type, "Retry attempt failed"
            )

        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.error(f"Retry exception for {failed_result.release_tag}: {exc}")
            return self._create_failure_result(
                failed_result,
                Path(target_path),
                url,
                file_type,
                "",
                str(exc),
                is_retryable_override=False,
            )

    def _generate_retry_report(
        self,
        retryable_failures: List[DownloadResult],
        non_retryable_failures: List[DownloadResult],
    ) -> None:
        """
        Log a structured report summarizing retry outcomes for failed downloads.

        Produces aggregate statistics and breakdowns including total failures, count of retryable
        and non-retryable failures, retry success rate, per-file-type counts, distribution by
        retry attempt, and counts by error reason. Also logs the effective retry configuration
        (max retries, base delay, backoff factor).

        Parameters:
            retryable_failures (List[DownloadResult]): Failures that were subject to retry attempts.
            non_retryable_failures (List[DownloadResult]): Failures that were not eligible for retries.
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
                failure_type = failure.file_type or FILE_TYPE_UNKNOWN
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
                reason = failure.error_type or ERROR_TYPE_UNKNOWN
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
        Populate missing metadata fields on aggregated download results after a pipeline run.

        For each DownloadResult in `download_results` and `failed_downloads` this infers a missing `file_type` from the result's `file_path` (mapping to "android", "firmware", "repository", or "unknown") and, for failed results lacking retry data, sets `is_retryable` using `_is_download_retryable(result)` and initializes `retry_count` to 0.
        """
        for result in self.download_results + self.failed_downloads:
            # Set file type based on file path if not already set
            if not result.file_type and result.file_path:
                # Convert to Path for reliable path component checking
                file_path = (
                    Path(result.file_path)
                    if not isinstance(result.file_path, Path)
                    else result.file_path
                )
                file_path_str = str(result.file_path)
                path_parts = file_path.parts

                # Check repository first since repo paths contain both firmware and repo directories
                if REPO_DOWNLOADS_DIR in path_parts:
                    result.file_type = FILE_TYPE_REPOSITORY
                elif APKS_DIR_NAME in path_parts or file_path_str.endswith(".apk"):
                    result.file_type = FILE_TYPE_ANDROID
                elif FIRMWARE_DIR_NAME in path_parts or file_path_str.endswith(
                    (".zip", ".bin", ".elf")
                ):
                    result.file_type = FILE_TYPE_FIRMWARE
                else:
                    result.file_type = FILE_TYPE_UNKNOWN

            # Set retry metadata for failed downloads
            if not result.success and result.retry_count is None:
                result.is_retryable = self._is_download_retryable(result)
                result.retry_count = 0

    def _is_download_retryable(self, result: DownloadResult) -> bool:
        """
        Decide if a failed download should be retried based on the result's error_type.

        Parameters:
            result (DownloadResult): The failed download result to evaluate.

        Returns:
            true if the failure is considered retryable, false otherwise.

        Notes:
            - Treats `network_error`, `connection_error`, `timeout`, `http_error`, `rate_limit`, and `temporary_failure` as retryable.
            - Treats `permission_error`, `validation_error`, `corrupted_file`, `disk_full`, `invalid_url`, and `authentication_error` as non-retryable.
            - Unknown or missing `error_type` defaults to retryable.
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
        """
        Log a concise summary of the download pipeline results.

        Logs the elapsed time since `start_time`, counts of successfully downloaded assets (excluding skipped),
        counts of skipped successful downloads, and the number of failed downloads. Emits a warning if any downloads failed.

        Parameters:
            start_time (float): Epoch timestamp (seconds) marking when the pipeline started (as returned by time.time()).
        """
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
        if not downloaded and total_failures == 0:
            logger.info("All assets are up to date.")
        else:
            logger.info(
                "Downloads: %d downloaded, %d failed",
                len(downloaded),
                total_failures,
            )
        if skipped:
            logger.debug("Skipped %d existing assets", len(skipped))

        if total_failures > 0:
            logger.warning(
                f"{total_failures} downloads failed - check logs for details"
            )

    def log_firmware_release_history_summary(self) -> None:
        """
        Emit firmware release summaries when firmware release history and releases are available.

        Logs three reports via the firmware release history manager: a release channel summary, a release status summary, and a duplicate base-version summary. If the `FILTER_REVOKED_RELEASES` config is enabled, revoked firmware releases are excluded from the channel and status summaries. If the `KEEP_LAST_BETA` config is enabled, the channel summary's retention window may be expanded to include the most recent beta release according to the configured firmware keep limit.
        """
        if not self.firmware_release_history or not self.firmware_releases:
            return

        manager = self.firmware_downloader.release_history_manager
        keep_limit_for_summary = self._get_firmware_keep_limit()
        keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
        filter_revoked = self.config.get(
            "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
        )

        releases_for_summary = self.firmware_releases
        if filter_revoked:
            releases_for_summary = [
                release
                for release in self.firmware_releases
                if not self.firmware_downloader.is_release_revoked(release)
            ]

        if keep_last_beta:
            keep_limit_for_summary = manager.expand_keep_limit_to_include_beta(
                releases_for_summary, keep_limit_for_summary
            )

        manager.log_release_channel_summary(
            releases_for_summary, label="Firmware", keep_limit=keep_limit_for_summary
        )

        kept_releases = manager.get_releases_for_summary(
            releases_for_summary, keep_limit=keep_limit_for_summary
        )
        kept_tags = {release.tag_name for release in kept_releases}
        entries = self.firmware_release_history.get("entries")
        entries_dict = entries if isinstance(entries, dict) else {}
        filtered_history: Dict[str, Any] = {
            "entries": {
                tag: entry for tag, entry in entries_dict.items() if tag in kept_tags
            }
        }

        manager.log_release_status_summary(filtered_history, label="Firmware")
        manager.log_duplicate_base_versions(kept_releases, label="Firmware")
        self._log_prerelease_summary()

    def _log_prerelease_summary(self) -> None:
        """
        Log prerelease history details that were captured during firmware downloads.

        The summary is emitted near other release history reports so prerelease commit
        information appears with the final summaries instead of during the download loop.
        """
        summary = self.firmware_prerelease_summary
        if not summary:
            return

        self.firmware_prerelease_summary = None

        history_entries = summary.get("history_entries") or []
        clean_latest_release = summary.get("clean_latest_release")
        expected_version = summary.get("expected_version")

        if not history_entries:
            logger.debug("Skipping prerelease summary: missing history_entries")
            return
        if not isinstance(clean_latest_release, str):
            logger.debug(
                "Skipping prerelease summary: clean_latest_release is not a string (got %s)",
                type(clean_latest_release).__name__,
            )
            return
        if not isinstance(expected_version, str):
            logger.debug(
                "Skipping prerelease summary: expected_version is not a string (got %s)",
                type(expected_version).__name__,
            )
            return

        self.firmware_downloader.log_prerelease_summary(
            history_entries, clean_latest_release, expected_version
        )

    def _get_firmware_keep_limit(self) -> int:
        """
        Get the configured firmware versions-to-keep limit as a non-negative integer.

        If the configuration value is missing or cannot be converted to an integer, the default
        DEFAULT_FIRMWARE_VERSIONS_TO_KEEP is returned.

        Returns:
            int: The configured limit coerced to an int and clamped to zero or greater.
        """
        raw_keep_limit = self.config.get(
            "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
        )
        try:
            return max(0, int(raw_keep_limit))
        except (TypeError, ValueError):
            return int(DEFAULT_FIRMWARE_VERSIONS_TO_KEEP)

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Summarizes download attempts and outcomes for the current run.

        Returns:
            dict: Mapping with the following keys:
                - "total_downloads": number of attempted downloads (excludes skipped results).
                - "successful_downloads": number of completed, non-skipped downloads.
                - "skipped_downloads": number of downloads marked as skipped.
                - "failed_downloads": number of failed downloads.
                - "success_rate": overall success percentage as a float (0-100).
                - "android_downloads": count of successful Android artifact downloads.
                - "firmware_downloads": count of successful firmware artifact downloads.
                - "repository_downloads": count of repository downloads (always 0 for automatic pipeline).
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
            "android_downloads": self._count_artifact_downloads(FILE_TYPE_ANDROID),
            "firmware_downloads": self._count_artifact_downloads(FILE_TYPE_FIRMWARE),
            # Repository downloads are not part of the automatic download pipeline.
            "repository_downloads": 0,
        }

    def _calculate_success_rate(self) -> float:
        """
        Compute the percentage of attempted downloads that completed successfully.

        Returns:
            float: Percentage (0.0-100.0) of successful downloads. Returns 100.0 when there were no attempted downloads.
        """
        downloaded_count = sum(
            1
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        )
        attempted = downloaded_count + len(self.failed_downloads)
        return (downloaded_count / attempted) * 100 if attempted > 0 else 100.0

    def _count_artifact_downloads(self, artifact_type: str) -> int:
        """
        Count successful (non-skipped) downloads that correspond to the given artifact type.

        Parameters:
            artifact_type (str): Artifact identifier to match against a result's `file_type` or as a substring in `file_path` (e.g., "android", "firmware").

        Returns:
            int: Number of matching downloads that were not skipped.
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
        """
        Prune locally stored Android and firmware artifacts according to configured retention settings and remove prerelease directories marked as deleted.

        This routine reads retention settings (e.g., `ANDROID_VERSIONS_TO_KEEP`, `FIRMWARE_VERSIONS_TO_KEEP`) and instructs the Android and firmware downloaders to remove older releases. When firmware retention is applied, the `KEEP_LAST_BETA` setting is honored if present. After pruning releases, it removes any prerelease directories that have been recorded as deleted. On filesystem or configuration-related errors (`OSError`, `ValueError`, `TypeError`) it logs an error.
        """
        try:
            logger.info("Cleaning up old versions...")

            # Clean up Android versions
            android_keep = self.config.get("ANDROID_VERSIONS_TO_KEEP", 5)
            self.android_downloader.cleanup_old_versions(
                android_keep, cached_releases=self.android_releases
            )

            # Clean up firmware versions
            firmware_keep = self._get_firmware_keep_limit()
            keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
            self.firmware_downloader.cleanup_old_versions(
                firmware_keep,
                cached_releases=self.firmware_releases,
                keep_last_beta=keep_last_beta,
            )
            self._cleanup_deleted_prereleases()

            logger.info("Old version cleanup completed")

        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Error cleaning up old versions: {e}")

    def _cleanup_deleted_prereleases(self) -> None:
        """
        Remove local firmware prerelease directories that are recorded as deleted in prerelease history.

        Queries the prerelease commit history for the expected firmware prerelease version derived from the latest firmware release. For each history entry with status "deleted", verifies the directory name is safe and removes the corresponding directory under the firmware prereleases folder if it exists. Uses the configured cache and the optional GitHub token when fetching history. Network and filesystem errors are caught and logged; the function does not raise on those errors.
        """
        try:
            # This logic is specific to firmware prereleases from meshtastic.github.io
            latest_firmware_release = self.firmware_downloader.get_latest_release_tag()
            if not latest_firmware_release:
                return

            expected_version = (
                self.version_manager.calculate_expected_prerelease_version(
                    latest_firmware_release
                )
            )
            if not expected_version:
                return

            history = self.prerelease_manager.get_prerelease_commit_history(
                expected_version,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                force_refresh=False,
            )

            deleted_entries = [e for e in history if e.get("status") == "deleted"]
            if not deleted_entries:
                return

            prerelease_base_dir = (
                Path(self.firmware_downloader.download_dir)
                / FIRMWARE_DIR_NAME
                / FIRMWARE_PRERELEASES_DIR_NAME
            )
            if not prerelease_base_dir.exists():
                return

            for entry in deleted_entries:
                directory_name = entry.get("directory")
                if not directory_name:
                    continue

                safe_name = os.path.basename(directory_name)
                if not safe_name or safe_name != directory_name:
                    logger.warning(
                        "Skipping unsafe prerelease directory name: %s", directory_name
                    )
                    continue
                dir_to_delete = prerelease_base_dir / safe_name
                if dir_to_delete.exists() and dir_to_delete.is_dir():
                    logger.info(
                        f"Removing deleted prerelease directory: {directory_name}"
                    )
                    if not _safe_rmtree(
                        str(dir_to_delete), str(prerelease_base_dir), directory_name
                    ):
                        logger.warning(
                            f"Failed to safely remove directory: {directory_name}"
                        )

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error during deleted prerelease cleanup: {e}", exc_info=True)

    def get_latest_versions(self) -> Dict[str, Optional[str]]:
        """
        Retrieve the latest known version tags for Android and firmware artifacts, including active prerelease identifiers when available.

        Returns:
            Dict[str, Optional[str]]: Mapping with keys:
                - "android": latest Android release tag or None
                - "firmware": latest firmware release tag or None
                - "firmware_prerelease": active firmware prerelease identifier (without "firmware-" prefix when applicable) or None
                - "android_prerelease": latest Android prerelease tag or None
        """
        firmware_prerelease = None
        latest_firmware_release = self.firmware_downloader.get_latest_release_tag()

        if latest_firmware_release:
            clean_latest_release = (
                self.version_manager.extract_clean_version(latest_firmware_release)
                or latest_firmware_release
            )
            expected_version = (
                self.version_manager.calculate_expected_prerelease_version(
                    clean_latest_release
                )
            )
            if expected_version:
                # Do not force refresh here to avoid API calls just for status display
                active_dir, _ = (
                    self.prerelease_manager.get_latest_active_prerelease_from_history(
                        expected_version,
                        cache_manager=self.cache_manager,
                        github_token=self.config.get("GITHUB_TOKEN"),
                        allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                        force_refresh=False,
                    )
                )
                if active_dir and active_dir.startswith(FIRMWARE_DIR_PREFIX):
                    firmware_prerelease = active_dir[len(FIRMWARE_DIR_PREFIX) :]
                else:
                    firmware_prerelease = active_dir

        android_releases = self._ensure_android_releases()
        latest_android_release = next(
            (
                release.tag_name
                for release in android_releases
                if not release.prerelease
            ),
            None,
        )
        latest_android_prerelease = self.android_downloader.get_latest_prerelease_tag(
            android_releases
        )

        return {
            "android": latest_android_release,
            "firmware": latest_firmware_release,
            "firmware_prerelease": firmware_prerelease,
            "android_prerelease": latest_android_prerelease,
        }

    def update_version_tracking(self) -> None:
        """
        Refresh the recorded latest release tags for Android and firmware and update prerelease tracking.

        If per-run release caches are present, uses them; otherwise fetches the most recent release for each artifact and updates the corresponding downloader's latest release tag. Invokes prerelease tracking refresh and logs an error if the update fails.
        """
        try:
            # Use cached releases if available
            android_releases = self._ensure_android_releases(limit=1)
            firmware_releases = self._ensure_firmware_releases(limit=1)

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

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error updating version tracking: {e}")

    def _refresh_commit_history_cache(self) -> None:
        """
        Refresh the commit history cache for prerelease filtering.

        Uses the prerelease manager to fetch recent repository commits with the configured GitHub token.
        This is used to determine which prereleases should be kept or filtered out.
        """
        try:
            logger.debug("Refreshing commit history cache...")
            self.prerelease_manager.fetch_recent_repo_commits(
                DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
            )
            logger.debug("Commit history cache refreshed")
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error refreshing commit history cache: {e}")

    def _manage_prerelease_tracking(self) -> None:
        """
        Manage prerelease tracking files for Android and firmware.

        Cleans up superseded prerelease directories and ensures prerelease tracking files remain consistent for each artifact type.
        """
        try:
            logger.info("Managing prerelease tracking files...")

            # Share recent commits with downloaders for prerelease filtering
            self._refresh_commit_history_cache()

            # Manage Android prerelease tracking - pass cached releases to avoid redundant API calls
            self.android_downloader.manage_prerelease_tracking_files(
                cached_releases=self.android_releases
            )

            # Manage firmware prerelease tracking - pass cached releases to avoid redundant API calls
            self.firmware_downloader.manage_prerelease_tracking_files(
                cached_releases=self.firmware_releases
            )

            logger.info("Prerelease tracking management completed")

        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Error managing prerelease tracking: {e}")
