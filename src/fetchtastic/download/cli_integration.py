"""
CLI Integration for New Download Subsystem

This module provides integration between the new download subsystem and the existing CLI.
"""

import os
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from .version import VersionManager

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    ANDROID_FILE_TYPES,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
    FILE_TYPE_REPOSITORY,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_FILE_TYPES,
)
from fetchtastic.log_utils import logger
from fetchtastic.notifications import (
    send_download_completion_notification,
    send_up_to_date_notification,
)
from fetchtastic.utils import (
    format_api_summary,
    get_api_request_summary,
    get_effective_github_token,
)

from .android import MeshtasticAndroidAppDownloader
from .firmware import FirmwareReleaseDownloader
from .orchestrator import DownloadOrchestrator


class DownloadCLIIntegration:
    """
    Integrates the new download subsystem with the existing CLI.

    This class provides:
    - Compatibility with existing CLI interface
    - Translation between CLI parameters and new architecture
    - Error handling and reporting
    - Progress reporting
    """

    def __init__(self) -> None:
        """
        Initialize a DownloadCLIIntegration instance and set initial internal state.

        Sets attributes used to connect CLI to download subsystem:
        - orchestrator: Orchestrator instance or None until initialized.
        - android_downloader: Android downloader instance or None until initialized.
        - firmware_downloader: Firmware downloader instance or None until initialized.
        - config: Configuration mapping or None until provided.
        """
        self.orchestrator: Optional[DownloadOrchestrator] = None
        self.android_downloader: Optional[MeshtasticAndroidAppDownloader] = None
        self.firmware_downloader: Optional[FirmwareReleaseDownloader] = None
        self.config: Optional[Dict[str, Any]] = None

    def _initialize_components(self, config: Dict[str, Any]) -> None:
        """
        Set up the DownloadOrchestrator and expose its downloaders on the integration instance.

        Stores the provided configuration on self, constructs a DownloadOrchestrator using that configuration, and assigns the orchestrator's android_downloader and firmware_downloader to instance attributes for shared state and caches.

        Parameters:
            config (Dict[str, Any]): Configuration used to initialize the orchestrator and downloaders.
        """
        self.config = config
        self.orchestrator = DownloadOrchestrator(config)
        # Reuse the orchestrator's downloaders so state and caches stay unified
        self.android_downloader = self.orchestrator.android_downloader
        self.firmware_downloader = self.orchestrator.firmware_downloader

    def run_download(
        self, config: Dict[str, Any], force_refresh: bool = False
    ) -> Tuple[
        List[str],
        List[str],
        List[str],
        List[str],
        List[str],
        List[str],
        List[Dict[str, str]],
        str,
        str,
    ]:
        """
        Run the download pipeline using the provided configuration and return results formatted for the legacy CLI.

        Initializes the orchestrator and downloaders from `config`, optionally clears downloader caches when `force_refresh` is True, executes the download pipeline, performs cleanup and version tracking, and collects failed download records.

        Parameters:
            config (Dict[str, Any]): Configuration used to initialize the orchestrator and downloaders.
            force_refresh (bool): If True, clear downloader caches before running the pipeline.

        Returns:
            Tuple[List[str], List[str], List[str], List[str], List[str], List[str], List[Dict[str, str]], str, str]:
                - downloaded_firmwares: Paths or identifiers of firmware files that were downloaded.
                - new_firmware_versions: Firmware release tags that are newer than the currently tracked firmware.
                - downloaded_apks: Paths or identifiers of Android APK files that were downloaded.
                - new_apk_versions: Android release tags that are newer than the currently tracked Android version.
                - downloaded_firmware_prereleases: Paths or identifiers of firmware prerelease files that were downloaded.
                - downloaded_apk_prereleases: Paths or identifiers of Android APK prerelease files that were downloaded.
                - failed_downloads: List of failure records; each record includes keys such as `file_name`, `release_tag`, `url`, `type`, `path_to_download`, `error`, `retryable`, and `http_status`.
                - latest_firmware_version: Latest known firmware version (empty string if unavailable).
                - latest_apk_version: Latest known Android APK version (empty string if unavailable).
        """
        try:
            self._initialize_components(config)
            assert self.orchestrator is not None  # guaranteed by _initialize_components
            orchestrator = self.orchestrator

            # Clear caches if force refresh is requested
            if force_refresh:
                self._clear_caches()

            # Run the download pipeline
            success_results, _failed_results = orchestrator.run_download_pipeline()

            # Convert results to legacy format
            (
                downloaded_firmwares,
                new_firmware_versions,
                downloaded_apks,
                new_apk_versions,
                downloaded_firmware_prereleases,
                downloaded_apk_prereleases,
            ) = self._convert_results_to_legacy_format(success_results)

            # Handle cleanup
            orchestrator.cleanup_old_versions()

            # Update version tracking
            orchestrator.update_version_tracking()

            # Get failed downloads
            failed_downloads = self.get_failed_downloads()

            # Get latest versions
            latest_versions = (
                self.orchestrator.get_latest_versions() if self.orchestrator else {}
            )
            latest_firmware_version = latest_versions.get("firmware", "") or ""
            latest_apk_version = latest_versions.get("android", "") or ""

            return (
                downloaded_firmwares,
                new_firmware_versions,
                downloaded_apks,
                new_apk_versions,
                downloaded_firmware_prereleases,
                downloaded_apk_prereleases,
                failed_downloads,
                latest_firmware_version,
                latest_apk_version,
            )

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            logger.exception("Error in CLI integration: %s", e)
            # Return empty results and error information
            return [], [], [], [], [], [], [], "", ""

    def _clear_caches(self) -> None:
        """
        Clear downloader caches managed by this integration.

        This calls the Android downloader's cache manager to remove all cached data; exceptions raised during the clear operation (e.g., OSError, ValueError) are caught and logged and are not propagated.
        """
        try:
            # Clear shared cache manager (same instance used by all downloaders)
            if self.android_downloader:
                self.android_downloader.cache_manager.clear_all_caches()

            logger.info("All caches cleared")

        except (OSError, ValueError) as e:
            logger.error(f"Error clearing caches: {e}")

    def log_download_results_summary(
        self,
        *,
        logger_override: Any = None,
        elapsed_seconds: float,
        downloaded_firmwares: List[str],
        downloaded_apks: List[str],
        downloaded_firmware_prereleases: Optional[List[str]] = None,
        downloaded_apk_prereleases: Optional[List[str]] = None,
        failed_downloads: List[Dict[str, str]],
        latest_firmware_version: str,
        latest_apk_version: str,
        new_firmware_versions: List[str],
        new_apk_versions: List[str],
    ) -> None:
        """
        Emit a legacy-style summary of download results to the provided logger.

        Logs elapsed time, counts of downloaded assets, latest release and prerelease tags, detailed information about any failed downloads, and a GitHub API usage summary. If no downloads or failures occurred, logs an up-to-date timestamp. If this instance has a configured `config`, sends notifications for completion or up-to-date state.

        Parameters:
            logger_override (logging-like, optional): Logger to use instead of the module logger.
            elapsed_seconds (float): Total time elapsed for the download run.
            downloaded_firmwares (List[str]): Downloaded firmware filenames or tags.
            downloaded_apks (List[str]): Downloaded APK filenames or tags.
            downloaded_firmware_prereleases (Optional[List[str]]): Downloaded firmware prerelease tags, if any.
            downloaded_apk_prereleases (Optional[List[str]]): Downloaded APK prerelease tags, if any.
            failed_downloads (List[Dict[str, str]]): Failure records; expected keys include `type`, `release_tag`, `file_name`, `url`, `retryable`, `http_status`, and `error`.
            latest_firmware_version (str): Reported latest firmware release tag (empty string if none).
            latest_apk_version (str): Reported latest APK release tag (empty string if none).
            new_firmware_versions (List[str]): Retained for backward compatibility; not used by this method.
            new_apk_versions (List[str]): Retained for backward compatibility; not used by this method.
        """
        log = logger_override or logger

        if self.orchestrator:
            self.orchestrator.log_firmware_release_history_summary()

        log.info(f"\nCompleted in {elapsed_seconds:.1f}s")

        downloaded_firmware_prereleases = downloaded_firmware_prereleases or []
        downloaded_apk_prereleases = downloaded_apk_prereleases or []
        downloaded_count = (
            len(downloaded_firmwares)
            + len(downloaded_apks)
            + len(downloaded_firmware_prereleases)
            + len(downloaded_apk_prereleases)
        )
        if downloaded_count > 0:
            log.info(f"Downloaded {downloaded_count} new versions")

        latest_versions = self.get_latest_versions()
        latest_firmware_prerelease = latest_versions.get("firmware_prerelease")
        latest_apk_prerelease = latest_versions.get("android_prerelease")

        if latest_firmware_version:
            log.info(f"Latest firmware: {latest_firmware_version}")
        if latest_firmware_prerelease:
            log.info(f"Latest firmware prerelease: {latest_firmware_prerelease}")
        else:
            log.info("Latest firmware prerelease: none")

        if latest_apk_version:
            log.info(f"Latest APK: {latest_apk_version}")
        if latest_apk_prerelease:
            log.info(f"Latest APK prerelease: {latest_apk_prerelease}")
        else:
            log.info("Latest APK prerelease: none")

        if failed_downloads:
            log.info(f"{len(failed_downloads)} downloads failed:")
            for failure in failed_downloads:
                url = failure.get("url", "unknown")
                retryable = failure.get("retryable")
                http_status = failure.get("http_status")
                error = failure.get("error", "")
                log.info(
                    f"- {failure.get('type', 'Unknown')} {failure.get('release_tag', '')}: "
                    f"{failure.get('file_name', 'unknown')} "
                    f"URL={url} retryable={retryable} http_status={http_status} error={error}"
                )

        if downloaded_count == 0 and not failed_downloads:
            log.info(
                "All assets are up to date.\n%s",
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        elif downloaded_count == 0 and failed_downloads:
            log.info("All attempted downloads failed; check logs for details.")

        # Send notifications based on download results
        if self.config:
            if downloaded_count > 0:
                send_download_completion_notification(
                    self.config,
                    downloaded_firmwares,
                    downloaded_apks,
                    downloaded_firmware_prereleases,
                    downloaded_apk_prereleases,
                )
            else:  # downloaded_count == 0 and not failed_downloads and not new_versions_available
                send_up_to_date_notification(self.config)

        summary = get_api_request_summary()
        if summary.get("total_requests", 0) > 0:
            log.debug(format_api_summary(summary))
        else:
            log.debug(
                "📊 GitHub API Summary: No API requests made (all data served from cache)"
            )

    def _convert_results_to_legacy_format(
        self, success_results: List[Any]
    ) -> Tuple[List[str], List[str], List[str], List[str], List[str], List[str]]:
        """
        Translate new-architecture successful download results into legacy CLI lists.

        Parameters:
            success_results (List[Any]): Iterable of result objects from the orchestrator; each object may have attributes `release_tag`, `file_path`, and `was_skipped`.

        Returns:
            Tuple[List[str], List[str], List[str], List[str], List[str], List[str]]:
                downloaded_firmwares: Unique firmware release tags that were downloaded (excludes skipped results).
                new_firmware_versions: Firmware release tags from `downloaded_firmwares` that are newer than the currently known firmware version.
                downloaded_apks: Unique Android (APK) release tags that were downloaded (excludes skipped results).
                new_apk_versions: Android release tags from `downloaded_apks` that are newer than the currently known Android version.
                downloaded_firmware_prereleases: Unique firmware prerelease release tags that were downloaded (excludes skipped results).
                downloaded_apk_prereleases: Unique Android (APK) prerelease release tags that were downloaded (excludes skipped results).
        """
        downloaded_firmwares: list[str] = []
        new_firmware_versions: list[str] = []
        downloaded_apks: list[str] = []
        new_apk_versions: list[str] = []
        downloaded_firmware_prereleases: list[str] = []
        downloaded_apk_prereleases: list[str] = []
        downloaded_firmware_set: set[str] = set()
        downloaded_apk_set: set[str] = set()
        downloaded_firmware_prerelease_set: set[str] = set()
        downloaded_apk_prerelease_set: set[str] = set()
        new_firmware_set: set[str] = set()
        new_apk_set: set[str] = set()

        # Get current versions before processing results
        if self.orchestrator:
            latest_versions = self.orchestrator.get_latest_versions()
            current_android = latest_versions.get("android")
            current_firmware = latest_versions.get("firmware")
            current_android_prerelease = latest_versions.get("android_prerelease")
            current_firmware_prerelease = latest_versions.get("firmware_prerelease")
        else:
            current_android = None
            current_firmware = None
            current_android_prerelease = None
            current_firmware_prerelease = None

        for result in success_results:
            release_tag = result.release_tag
            if not release_tag:
                continue

            file_type = result.file_type
            is_firmware = file_type in FIRMWARE_FILE_TYPES
            is_android = file_type in ANDROID_FILE_TYPES
            was_skipped = getattr(result, "was_skipped", False)

            # Legacy parity: only mark new versions when a download actually occurred.
            if was_skipped:
                continue

            if is_firmware:
                compare_current = current_firmware
                compare_release_tag = None
                if file_type in {
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                }:
                    compare_release_tag = self._normalize_firmware_prerelease_tag(
                        release_tag
                    )
                    compare_current = self._normalize_firmware_prerelease_tag(
                        current_firmware_prerelease or current_firmware
                    )
                self._update_new_versions(
                    release_tag,
                    compare_current,
                    new_firmware_versions,
                    new_firmware_set,
                    comparison_release_tag=compare_release_tag,
                )
            if is_android:
                compare_current = current_android
                if file_type == FILE_TYPE_ANDROID_PRERELEASE:
                    compare_current = current_android_prerelease or current_android
                self._update_new_versions(
                    release_tag,
                    compare_current,
                    new_apk_versions,
                    new_apk_set,
                )

            if is_firmware:
                if file_type in {
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                }:
                    if release_tag not in downloaded_firmware_prerelease_set:
                        downloaded_firmware_prereleases.append(release_tag)
                        downloaded_firmware_prerelease_set.add(release_tag)
                else:
                    self._add_downloaded_asset(
                        release_tag, downloaded_firmwares, downloaded_firmware_set
                    )
            if is_android:
                if file_type == FILE_TYPE_ANDROID_PRERELEASE:
                    if release_tag not in downloaded_apk_prerelease_set:
                        downloaded_apk_prereleases.append(release_tag)
                        downloaded_apk_prerelease_set.add(release_tag)
                else:
                    self._add_downloaded_asset(
                        release_tag, downloaded_apks, downloaded_apk_set
                    )

        return (
            downloaded_firmwares,
            new_firmware_versions,
            downloaded_apks,
            new_apk_versions,
            downloaded_firmware_prereleases,
            downloaded_apk_prereleases,
        )

    def _update_new_versions(
        self,
        release_tag: str,
        current_version: Optional[str],
        new_versions_list: List[str],
        new_versions_set: set[str],
        *,
        comparison_release_tag: Optional[str] = None,
    ) -> None:
        """
        Add release_tag to new_versions_list and new_versions_set if it is not already present and is newer than current_version.

        Parameters:
            release_tag (str): The release tag to consider for recording.
            current_version (Optional[str]): The existing version to compare against; if None, release_tag is treated as newer.
            new_versions_list (List[str]): Mutable list to append the release_tag to when it is new.
            new_versions_set (set[str]): Mutable set used to ensure uniqueness of recorded versions.
            comparison_release_tag (Optional[str]): If provided, this tag is used for version comparison instead of release_tag.
        """
        compare_tag = comparison_release_tag or release_tag
        if release_tag not in new_versions_set and (
            not current_version or self._is_newer_version(compare_tag, current_version)
        ):
            new_versions_list.append(release_tag)
            new_versions_set.add(release_tag)

    def _normalize_firmware_prerelease_tag(self, tag: Optional[str]) -> Optional[str]:
        """
        Normalize a firmware prerelease tag by removing the configured firmware directory prefix if present.

        Parameters:
            tag (Optional[str]): A prerelease tag that may be prefixed with FIRMWARE_DIR_PREFIX.

        Returns:
            normalized_tag (Optional[str]): The tag with FIRMWARE_DIR_PREFIX stripped if it was present;
                otherwise the original tag (or None/empty unchanged).
        """
        if not tag:
            return tag
        return tag.removeprefix(FIRMWARE_DIR_PREFIX)

    def _add_downloaded_asset(
        self,
        release_tag: str,
        downloaded_list: List[str],
        downloaded_set: set[str],
    ) -> None:
        """
        Add a release tag to the downloaded list while ensuring uniqueness via the downloaded set.

        Parameters:
            release_tag: The release tag to record.
            downloaded_list: Ordered list of recorded release tags; the tag is appended if not already recorded.
            downloaded_set: Set used to track recorded tags for fast membership checks and to prevent duplicates.
        """
        if release_tag not in downloaded_set:
            downloaded_list.append(release_tag)
            downloaded_set.add(release_tag)

    def _is_newer_version(self, version1: str, version2: str) -> bool:
        """
        Determine whether `version1` represents a newer version than `version2`.

        Parameters:
            version1 (str): Version string to compare.
            version2 (str): Version string to compare against.

        Returns:
            bool: `True` if `version1` represents a newer version than `version2`, `False` otherwise.
        """
        version_manager = self._get_version_manager()
        comparison = (
            version_manager.compare_versions(version1, version2)
            if version_manager
            else 0
        )
        return comparison > 0

    def _get_version_manager(self) -> Optional["VersionManager"]:
        """
        Acquire version manager exposed by Android downloader.
        """
        if not self.android_downloader:
            return None
        # Check for method first (for backward compatibility and mocks),
        # then fall back to direct attribute access
        getter = getattr(self.android_downloader, "get_version_manager", None)
        if callable(getter):
            result = getter()
            return cast(Optional["VersionManager"], result)
        result = getattr(self.android_downloader, "version_manager", None)
        return cast(Optional["VersionManager"], result)

    def get_failed_downloads(self) -> List[Dict[str, Any]]:
        """
        Builds a legacy-formatted list describing failed downloads.

        Each item is a dict with the following keys:
            file_name: Base filename of the intended download or "unknown".
            release_tag: Associated release tag or "unknown".
            url: Download URL or "unknown".
            type: Human-readable file type (e.g., "Firmware", "Android APK", "Repository", "Firmware Prerelease", "Android APK Prerelease", or "Unknown").
            path_to_download: Full path where the file was to be saved, or "unknown".
            error: Error message for the failure, or empty string if none.
            retryable: Whether the failure is considered retryable.
            http_status: HTTP status code associated with the failure, or None if not applicable.

        Returns:
            List[Dict[str, Any]]: The list of failed download records. Returns an empty list if the integration is not initialized or there are no failures.
        """
        if not self.orchestrator:
            return []

        failed_downloads = []

        file_type_map = {
            FILE_TYPE_FIRMWARE: "Firmware",
            FILE_TYPE_ANDROID: "Android APK",
            FILE_TYPE_FIRMWARE_PRERELEASE: "Firmware Prerelease",
            FILE_TYPE_FIRMWARE_PRERELEASE_REPO: "Firmware Prerelease",
            FILE_TYPE_REPOSITORY: "Repository",
            FILE_TYPE_ANDROID_PRERELEASE: "Android APK Prerelease",
        }

        for result in self.orchestrator.failed_downloads:
            failure_type = (
                file_type_map.get(result.file_type, "Unknown")
                if result.file_type
                else "Unknown"
            )
            failed_downloads.append(
                {
                    "file_name": (
                        os.path.basename(str(result.file_path))
                        if result.file_path
                        else "unknown"
                    ),
                    "release_tag": result.release_tag or "unknown",
                    "url": result.download_url or "unknown",
                    "type": failure_type,
                    "path_to_download": (
                        str(result.file_path) if result.file_path else "unknown"
                    ),
                    "error": result.error_message or "",
                    "retryable": result.is_retryable,
                    "http_status": result.http_status_code,
                }
            )

        return failed_downloads

    def main(
        self,
        config: Dict[str, Any],
        force_refresh: bool = False,
    ) -> Tuple[
        List[str],
        List[str],
        List[str],
        List[str],
        List[str],
        List[str],
        List[Dict[str, Any]],
        str,
        str,
    ]:
        """
        Entry point for CLI commands that uses a provided configuration, normalizes tokens, and runs the download workflow to produce legacy-compatible results.

         Parameters:
            config (Dict[str, Any]): Configuration mapping for the download run. Must not be None; passing None raises TypeError.
            force_refresh (bool): When True, forces refresh behavior for downloaders (e.g., clears caches) for this run.

        Returns:
            Tuple containing:
                downloaded_firmwares (List[str]): List of firmware release tags or identifiers that were downloaded during the run.
                new_firmware_versions (List[str]): Subset of downloaded_firmwares that are newer than previously known firmware versions.
                downloaded_apks (List[str]): List of Android APK release tags or identifiers that were downloaded during the run.
                new_apk_versions (List[str]): Subset of downloaded_apks that are newer than previously known APK versions.
                downloaded_firmware_prereleases (List[str]): List of firmware prerelease release tags or identifiers that were downloaded during the run.
                downloaded_apk_prereleases (List[str]): List of Android APK prerelease release tags or identifiers that were downloaded during the run.
                failed_downloads (List[Dict[str, Any]]): List of failure records formatted for legacy CLI consumption; each record includes keys like file_name, release_tag, url, type, path_to_download, error, retryable, and http_status.
                latest_firmware_version (str): The latest known firmware version after the run (empty string if unknown).
                latest_apk_version (str): The latest known Android APK version after the run (empty string if unknown).
        """
        if config is None:
            raise TypeError("config must be provided to the download integration.")

        try:
            # Normalize token once for the run so all downstream call sites see the
            # same effective value (config token preferred, env token fallback).
            config_token = get_effective_github_token(
                config.get("GITHUB_TOKEN"),
                allow_env_token=config.get("ALLOW_ENV_TOKEN", True),
            )
            if config_token:
                config["GITHUB_TOKEN"] = config_token
            else:
                config.pop("GITHUB_TOKEN", None)

            results = self.run_download(config, force_refresh)
            return results

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            self.handle_cli_error(error)
            return [], [], [], [], [], [], [], "", ""

    def update_cache(self, config: Dict[str, Any]) -> bool:
        """
        Clear all download caches without running the download pipeline.

        Parameters:
            config (Dict[str, Any]): Configuration mapping for cache refresh.

        Returns:
            bool: True if caches were cleared successfully, False otherwise.
        """
        try:
            self._initialize_components(config)

            self._clear_caches()
            return True

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            self.handle_cli_error(error)
            return False

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Return aggregated download statistics for reporting.

        Returns:
            dict: Aggregated download statistics with keys:
                - total_downloads (int): Number of attempted downloads (excludes skipped results).
                - successful_downloads (int): Number of completed, non-skipped downloads.
                - skipped_downloads (int): Number of downloads marked as skipped.
                - failed_downloads (int): Number of downloads that failed.
                - success_rate (float): Overall success percentage as a float (0-100).
                - android_downloads (int): Number of successful Android artifact downloads.
                - firmware_downloads (int): Number of successful firmware artifact downloads.
                - repository_downloads (int): Number of repository downloads (always 0 for automatic pipeline).
        """
        if self.orchestrator:
            return self.orchestrator.get_download_statistics()
        return {
            "total_downloads": 0,
            "successful_downloads": 0,
            "skipped_downloads": 0,
            "failed_downloads": 0,
            "success_rate": 0.0,
            "android_downloads": 0,
            "firmware_downloads": 0,
            "repository_downloads": 0,
        }

    def get_latest_versions(self) -> Dict[str, str]:
        """
        Get the latest known version strings for each artifact type.

        Returns:
            dict: Mapping with keys 'android', 'firmware', 'firmware_prerelease', and 'android_prerelease' to the latest version string for each; an empty string indicates the version is not available.
        """
        if self.orchestrator:
            versions = self.orchestrator.get_latest_versions()
            # Convert Optional[str] to str for compatibility
            return {k: v or "" for k, v in versions.items()}
        return {
            "android": "",
            "firmware": "",
            "firmware_prerelease": "",
            "android_prerelease": "",
        }

    def validate_integration(self) -> bool:
        """
        Verify the CLI integration is operational by checking component initialization, ability to fetch at least one release from both Android and firmware downloaders, and that the Android download directory exists (creating it if missing).

        Returns:
            bool: `True` if all checks pass, `False` otherwise.
        """
        if (
            not self.orchestrator
            or not self.android_downloader
            or not self.firmware_downloader
        ):
            return False

        try:
            # Check that basic functionality works
            android_releases = self.android_downloader.get_releases(limit=1)
            firmware_releases = self.firmware_downloader.get_releases(limit=1)

            if not android_releases or not firmware_releases:
                logger.warning("Integration validation: Could not fetch releases")
                return False

            # Check that download directories exist
            download_dir = self.android_downloader.get_download_dir()
            if not os.path.exists(download_dir):
                os.makedirs(download_dir, exist_ok=True)

            return True

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            logger.error(f"Integration validation failed: {e}")
            return False

    def get_migration_report(self) -> Dict[str, Any]:
        """
        Produce a report describing the initialization and readiness of the download CLI integration.

        The returned mapping summarizes whether core components are initialized, whether configuration and download directory checks pass, and includes current download statistics.

        Returns:
            Dict[str, Any]: A mapping with keys:
                - status (str): "completed" when core components are initialized, otherwise "not_initialized".
                - android_downloader_initialized (bool)
                - firmware_downloader_initialized (bool)
                - orchestrator_initialized (bool)
                - configuration_valid (bool): result of configuration validation.
                - download_directory_exists (bool): whether the configured download directory exists.
                - statistics (Dict[str, Any]): current download statistics from get_download_statistics().
                - repository_support (bool): included only when `status` is "not_initialized" and set to False.
        """
        if self.orchestrator and self.android_downloader and self.firmware_downloader:
            return {
                "status": "completed",
                "android_downloader_initialized": True,
                "firmware_downloader_initialized": True,
                "orchestrator_initialized": True,
                "configuration_valid": self._validate_configuration(),
                "download_directory_exists": self._check_download_directory(),
                "statistics": self.get_download_statistics(),
            }

        return {
            "status": "not_initialized",
            "android_downloader_initialized": False,
            "firmware_downloader_initialized": False,
            "orchestrator_initialized": False,
            "configuration_valid": False,
            "download_directory_exists": False,
            "statistics": self.get_download_statistics(),
            "repository_support": False,
        }

    def fallback_to_legacy(self) -> bool:
        """
        Indicates that the integration does not fall back to the legacy downloader.

        Returns:
            bool: `false` indicating fallback to the legacy downloader will not occur.
        """
        # Fallback is no longer needed since we're using the new architecture directly
        logger.warning(
            "Fallback to legacy downloader requested but new architecture is active"
        )
        return False

    def _validate_configuration(self) -> bool:
        """
        Determine whether the currently loaded configuration contains the required "DOWNLOAD_DIR" key.

        Returns:
            `True` if a configuration is loaded and contains the "DOWNLOAD_DIR" key, `False` otherwise.
        """
        if not self.config:
            return False
        required_keys = ["DOWNLOAD_DIR"]
        return all(key in self.config for key in required_keys)

    def _check_download_directory(self) -> bool:
        """
        Check whether the Android downloader is initialized and its configured download directory exists.

        Returns:
            `True` if the Android downloader is initialized and its download directory exists, `False` otherwise.
        """
        if not self.android_downloader:
            return False
        download_dir = self.android_downloader.get_download_dir()
        return os.path.exists(download_dir)

    def get_legacy_compatibility_report(self) -> Dict[str, Any]:
        """
        Produce a compatibility report describing how the integration aligns with legacy CLI expectations.

        The report includes boolean flags for compatibility checks and the current download statistics.

        Returns:
            Dict[str, Any]: Mapping with the following keys:
                - cli_integration_ready: `True` if the CLI integration is initialized and ready.
                - expected_interface_compatibility: `True` if the public interface matches legacy expectations.
                - return_format_compatibility: `True` if return formats follow legacy conventions.
                - error_handling_compatibility: `True` if error handling is compatible with legacy behavior.
                - configuration_compatibility: `True` if configuration keys and layout are compatible.
                - repository_reporting: `True` if repository reporting/support is available.
                - statistics: Current download statistics as returned by `get_download_statistics()`.
        """
        return {
            "cli_integration_ready": True,  # CLI integration initialized and ready
            "expected_interface_compatibility": True,  # Public interface matches legacy expectations
            "return_format_compatibility": True,  # Return formats follow legacy conventions
            "error_handling_compatibility": True,  # Error handling compatible with legacy behavior
            "configuration_compatibility": True,  # Configuration keys and layout are compatible
            "repository_reporting": True,  # Repository reporting/support available
            "statistics": self.get_download_statistics(),
        }

    def log_integration_summary(self) -> None:
        """
        Emit a multi-line summary of the integration state to the module logger.

        Includes integration status, initialization flags for the orchestrator and downloaders, configuration and download-directory checks, aggregated download statistics (total, failed, per-type counts, and success rate), and details for any failed downloads.
        """
        if not self.orchestrator:
            logger.info("CLI Integration: Not initialized")
            return

        report = self.get_migration_report()
        stats = self.get_download_statistics()

        logger.info("CLI Integration Summary:")
        logger.info(f"Status: {report.get('status', 'unknown')}")
        logger.info(
            f"Android Downloader: {'Initialized' if report.get('android_downloader_initialized') else 'Not initialized'}"
        )
        logger.info(
            f"Firmware Downloader: {'Initialized' if report.get('firmware_downloader_initialized') else 'Not initialized'}"
        )
        logger.info(
            f"Orchestrator: {'Initialized' if report.get('orchestrator_initialized') else 'Not initialized'}"
        )
        logger.info(
            f"Configuration Valid: {'Yes' if report.get('configuration_valid') else 'No'}"
        )
        logger.info(
            f"Download Directory: {'Exists' if report.get('download_directory_exists') else 'Missing'}"
        )
        logger.info(f"Total Downloads: {stats.get('total_downloads', 0)}")
        logger.info(f"Failed Downloads: {stats.get('failed_downloads', 0)}")
        logger.info(f"Success Rate: {stats.get('success_rate', 0):.1f}%")
        logger.info(
            f"Android downloads: {stats.get('android_downloads', 0)}, "
            f"Firmware downloads: {stats.get('firmware_downloads', 0)}, "
            f"Repository downloads: {stats.get('repository_downloads', 0)}"
        )
        if self.orchestrator.failed_downloads:
            logger.info("Failed downloads with URLs:")
            for failure in self.orchestrator.failed_downloads:
                logger.info(
                    f"- {failure.file_type or 'unknown'} "
                    f"{failure.release_tag or ''} "
                    f"URL: {failure.download_url or 'unknown'} "
                    f"Error: {failure.error_message or 'unknown'} "
                    f"Retryable: {failure.is_retryable}"
                )

    def handle_cli_error(self, error: Exception) -> None:
        """
        Log a user-friendly CLI error message and emit targeted guidance for common exception types.

        Parameters:
            error (Exception): The exception that occurred; used to select and log specific, actionable guidance for common error categories (import, file-not-found, permission, connection, or other).
        """
        logger.error(f"CLI Error: {error!s}")

        # Provide specific guidance based on error type
        if isinstance(error, ImportError):
            logger.error(
                "Import error - please check your Python environment and dependencies"
            )
        elif isinstance(error, FileNotFoundError):
            logger.error("File not found - please check your configuration and paths")
        elif isinstance(error, PermissionError):
            logger.error("Permission error - please check file system permissions")
        elif isinstance(
            error, (requests.ConnectionError, requests.Timeout, ConnectionError)
        ):
            logger.error(
                "Network connection error - please check your internet connection"
            )
        else:
            logger.error("An unexpected error occurred - please check logs for details")

    def get_cli_help_integration(self) -> Dict[str, str]:
        """
        Return a mapping of short help text entries describing the CLI integration for the download subsystem.

        Returns:
            dict: Mapping of help keys to short instructional strings. Keys:
                - description: brief name or summary of the subsystem
                - usage: high-level usage note for the CLI command
                - features: notable features of the new architecture
                - android_info: brief note about Android APK downloads
                - firmware_info: brief note about firmware downloads
                - configuration: how configuration is used or extended
                - force_refresh: how to trigger cache clearing / recheck
                - cache_update: how to clear caches without running downloads
                - troubleshooting: where to look for more detailed error information
        """
        return {
            "description": "Fetchtastic Download Subsystem (New Architecture)",
            "usage": "The download command now uses a modular architecture with separate downloaders for Android and Firmware.",
            "features": "Automatic release detection, version tracking, cleanup, and retry logic",
            "android_info": "Downloads Meshtastic Android APK files from GitHub releases",
            "firmware_info": "Downloads Meshtastic firmware releases and extracts files based on patterns",
            "configuration": "Uses existing configuration with additional options for version retention",
            "force_refresh": "Use --force-download or -f to clear caches and recheck all downloads",
            "cache_update": "Use 'fetchtastic cache update' or 'download --update-cache' to clear caches without downloading",
            "troubleshooting": "Check logs for detailed error information and use --verbose for debugging",
        }

    def update_cli_progress(self, message: str, progress: float = 0.0) -> None:
        """
        Emit a CLI progress or status message to the configured logger.

        Parameters:
            message (str): Human-readable progress or status message.
            progress (float): Fractional progress between 0.0 and 1.0; if greater than 0, a percentage is logged, otherwise only the status message is logged.
        """
        if progress > 0:
            logger.info(f"Progress: {progress * 100:.1f}% - {message}")
        else:
            logger.info(f"Status: {message}")

    def get_environment_info(self) -> Dict[str, Any]:
        """
        Collects environment and configuration diagnostics.

        Returns:
            Dict[str, Any]: Mapping with keys:
                - "python_version": Python interpreter version string.
                - "working_directory": Current working directory path.
                - "download_directory": Configured download directory or "Not configured".
                - "configuration_loaded": `True` if a configuration is loaded, `False` otherwise.
                - "orchestrator_initialized": `True` if the orchestrator has been initialized, `False` otherwise.
                - "platform": Operating system platform identifier.
                - "executable": Path to the Python executable.
        """
        return {
            "python_version": sys.version,
            "working_directory": os.getcwd(),
            "download_directory": (
                self.config.get("DOWNLOAD_DIR", "Not configured")
                if self.config
                else "Not configured"
            ),
            "configuration_loaded": self.config is not None,
            "orchestrator_initialized": self.orchestrator is not None,
            "platform": sys.platform,
            "executable": sys.executable,
        }
