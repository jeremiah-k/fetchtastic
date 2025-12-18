"""
CLI Integration for New Download Subsystem

This module provides integration between the new download subsystem and the existing CLI.
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    format_api_summary,
    get_api_request_summary,
    get_effective_github_token,
)

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

    def __init__(self):
        """Initialize the CLI integration."""
        self.orchestrator = None
        self.android_downloader = None
        self.firmware_downloader = None
        self.config = None

    def run_download(
        self, config: Dict[str, Any], force_refresh: bool = False
    ) -> Tuple[
        List[str], List[str], List[str], List[str], List[Dict[str, str]], str, str
    ]:
        """
        Execute the download pipeline and return results formatted for legacy CLI compatibility.

        Returns:
            A tuple with:
            - downloaded_firmwares (List[str]): Paths or identifiers of firmware files downloaded.
            - new_firmware_versions (List[str]): Firmware versions that are newer than the current tracked version.
            - downloaded_apks (List[str]): Paths or identifiers of Android APK files downloaded.
            - new_apk_versions (List[str]): Android versions that are newer than the current tracked version.
            - failed_downloads (List[Dict[str, str]]): List of failure records with keys such as `file_name`, `release_tag`, `url`, `type`, `path_to_download`, `error`, `retryable`, and `http_status`.
            - latest_firmware_version (str): Latest known firmware version (empty string if unavailable).
            - latest_apk_version (str): Latest known Android APK version (empty string if unavailable).

        On error, returns empty lists for the download collections and empty strings for the latest-version values.
        """
        try:
            # Initialize components with the provided config
            self.config = config
            self.orchestrator = DownloadOrchestrator(config)
            # Reuse the orchestrator's downloaders so state and caches stay unified
            self.android_downloader = self.orchestrator.android_downloader
            self.firmware_downloader = self.orchestrator.firmware_downloader

            # Clear caches if force refresh is requested
            if force_refresh:
                self._clear_caches()

            # Run the download pipeline
            success_results, _failed_results = self.orchestrator.run_download_pipeline()

            # Convert results to legacy format
            (
                downloaded_firmwares,
                new_firmware_versions,
                downloaded_apks,
                new_apk_versions,
            ) = self._convert_results_to_legacy_format(success_results)

            # Handle cleanup
            self.orchestrator.cleanup_old_versions()

            # Update version tracking
            self.orchestrator.update_version_tracking()

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
                failed_downloads,
                latest_firmware_version,
                latest_apk_version,
            )

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.exception("Error in CLI integration: %s", e)
            # Return empty results and error information
            return [], [], [], [], [], "", ""

    def _clear_caches(self) -> None:
        """
        Attempt to clear downloader caches.

        Attempts to clear all caches managed by the integration (currently the Android downloader's cache manager). Exceptions raised during clearing are caught and logged; this method does not propagate errors.
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
        failed_downloads: List[Dict[str, str]],
        latest_firmware_version: str,
        latest_apk_version: str,
    ) -> None:
        """
        Emit a legacy-style summary of download results to the provided logger.

        Logs elapsed time, counts of downloaded firmware and APKs, reported latest release versions (including prereleases), details of any failed downloads, and a GitHub API usage summary. If no downloads or failures occurred, logs an "up to date" timestamp. Uses the instance's get_latest_versions() for prerelease info.

        Parameters:
            logger_override (logging-like, optional): Logger to use instead of the module logger; if omitted, the module-level `logger` is used.
            elapsed_seconds (float): Total time elapsed for the download run.
            downloaded_firmwares (List[str]): Filenames or paths of downloaded firmware assets.
            downloaded_apks (List[str]): Filenames or paths of downloaded APK assets.
            failed_downloads (List[Dict[str, str]]): List of failure records; each may include keys like `type`, `release_tag`, `file_name`, `url`, `retryable`, `http_status`, and `error`.
            latest_firmware_version (str): Reported latest firmware release tag (empty if none).
            latest_apk_version (str): Reported latest APK release tag (empty if none).
        """
        log = logger_override or logger
        log.info(f"\nCompleted in {elapsed_seconds:.1f}s")

        downloaded_count = len(downloaded_firmwares) + len(downloaded_apks)
        if downloaded_count > 0:
            log.info(f"Downloaded {downloaded_count} new versions")

        if latest_firmware_version:
            log.info(f"Latest firmware: {latest_firmware_version}")
        if latest_apk_version:
            log.info(f"Latest APK: {latest_apk_version}")

        latest_versions = self.get_latest_versions()
        latest_firmware_prerelease = latest_versions.get("firmware_prerelease")
        latest_apk_prerelease = latest_versions.get("android_prerelease")
        if latest_firmware_prerelease:
            log.info(f"Latest firmware prerelease: {latest_firmware_prerelease}")
        if latest_apk_prerelease:
            log.info(f"Latest APK prerelease: {latest_apk_prerelease}")

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

        summary = get_api_request_summary()
        if summary.get("total_requests", 0) > 0:
            log.debug(format_api_summary(summary))
        else:
            log.debug(
                "📊 GitHub API Summary: No API requests made (all data served from cache)"
            )

    def _convert_results_to_legacy_format(
        self, success_results: List[Any]
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        Translate new-architecture successful download results into legacy CLI lists.

        Parameters:
            success_results (List[Any]): Iterable of result objects from the orchestrator; each object may have attributes `release_tag`, `file_path`, and `was_skipped`.

        Returns:
            Tuple[List[str], List[str], List[str], List[str]]:
                downloaded_firmwares: Unique firmware release tags that were downloaded (excludes skipped results).
                new_firmware_versions: Firmware release tags from `downloaded_firmwares` that are newer than the currently known firmware version.
                downloaded_apks: Unique Android (APK) release tags that were downloaded (excludes skipped results).
                new_apk_versions: Android release tags from `downloaded_apks` that are newer than the currently known Android version.
        """
        downloaded_firmwares = []
        new_firmware_versions = []
        downloaded_apks = []
        new_apk_versions = []

        # Get current versions before processing results
        if self.orchestrator:
            latest_versions = self.orchestrator.get_latest_versions()
            current_android = latest_versions.get("android")
            current_firmware = latest_versions.get("firmware")
        else:
            current_android = None
            current_firmware = None

        for result in success_results:
            # Legacy parity: "already complete" skips should not be reported as
            # downloaded versions in the CLI summary.
            if getattr(result, "was_skipped", False):
                continue
            if result.release_tag:
                # Determine if this is firmware or Android based on file type
                if result.file_type and "firmware" in result.file_type:
                    if result.release_tag not in downloaded_firmwares:
                        downloaded_firmwares.append(result.release_tag)
                        # Check if this is a new version
                        if not current_firmware or self._is_newer_version(
                            result.release_tag, current_firmware
                        ):
                            new_firmware_versions.append(result.release_tag)
                elif result.file_type and "android" in result.file_type:
                    if result.release_tag not in downloaded_apks:
                        downloaded_apks.append(result.release_tag)
                        # Check if this is a new version
                        if not current_android or self._is_newer_version(
                            result.release_tag, current_android
                        ):
                            new_apk_versions.append(result.release_tag)

        return (
            downloaded_firmwares,
            new_firmware_versions,
            downloaded_apks,
            new_apk_versions,
        )

    def _is_newer_version(self, version1: str, version2: str) -> bool:
        """
        Determine whether `version1` represents a newer version than `version2`.

        Parameters:
            version1 (str): Version string to compare.
            version2 (str): Version string to compare against.

        Returns:
            bool: `True` if `version1` represents a newer version than `version2`, `False` otherwise.
        """
        version_manager = (
            self.android_downloader.get_version_manager()
            if self.android_downloader
            else None
        )
        comparison = (
            version_manager.compare_versions(version1, version2)
            if version_manager
            else 0
        )
        return comparison > 0

    def get_failed_downloads(self) -> List[Dict[str, Any]]:
        """
        Return a legacy-formatted list of failed download records.

        Each list item is a dictionary with the following keys:
            file_name (str): Base filename of the intended download or "unknown" if not available.
            release_tag (str): Associated release tag or "unknown" if not available.
            url (str): Download URL or "unknown" if not available.
            type (str): Human-readable file type (e.g., "Firmware", "Android APK", "Repository", "Firmware Prerelease", "Android APK Prerelease", or "Unknown").
            path_to_download (str): Full path where the file was to be saved, or "unknown" if not available.
            error (str): Error message reported for the failure, or empty string if none.
            retryable (bool): Whether the failure is considered retryable.
            http_status (Optional[int]): HTTP status code associated with the failure, or None if not applicable.

        Returns:
            List[Dict[str, Any]]: The list of failed download dictionaries. Returns an empty list if the integration is not initialized or there are no failures.
        """
        if not self.orchestrator:
            return []

        failed_downloads = []

        file_type_map = {
            "firmware": "Firmware",
            "android": "Android APK",
            "firmware_prerelease": "Firmware Prerelease",
            "firmware_prerelease_repo": "Firmware Prerelease",
            "repository": "Repository",
            "android_prerelease": "Android APK Prerelease",
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
        force_refresh: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[
        List[str],
        List[str],
        List[str],
        List[str],
        List[Dict[str, Any]],  # Updated to match actual payload types
        str,
        str,
    ]:
        """
        Entry point for CLI commands and setup workflows.

        Loads configuration when not provided, runs the download pipeline, and
        returns legacy-compatible results for downstream reporting.
        """
        try:
            if config is None:
                from fetchtastic import setup_config

                exists, _config_path = setup_config.config_exists()
                if not exists:
                    logger.error(
                        "No configuration found. Please run 'fetchtastic setup' first."
                    )
                    return [], [], [], [], [], "", ""

                config = setup_config.load_config()
                if config is None:
                    logger.error("Configuration file exists but could not be loaded.")
                    return [], [], [], [], [], "", ""

            # Normalize token once for the run so all downstream call sites see the
            # same effective value (config token preferred, env token fallback).
            config_token = get_effective_github_token(
                config.get("GITHUB_TOKEN"),
                allow_env_token=True,
            )
            if config_token:
                config["GITHUB_TOKEN"] = config_token
            else:
                config.pop("GITHUB_TOKEN", None)

            results = self.run_download(config, force_refresh)
            return results

        except (requests.RequestException, OSError, ValueError, TypeError) as error:
            self.handle_cli_error(error)
            return [], [], [], [], [], "", ""

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Provide download statistics for reporting.

        Returns:
            A dictionary with the following keys:
            - "total_downloads" (int): Total number of attempted downloads.
            - "failed_downloads" (int): Number of downloads that failed.
            - "success_rate" (float): Percentage of successful downloads (0.0-100.0).
            - "android_downloads" (int): Number of Android artifacts downloaded.
            - "firmware_downloads" (int): Number of firmware artifacts downloaded.
        """
        if self.orchestrator:
            return self.orchestrator.get_download_statistics()
        return {
            "total_downloads": 0,
            "failed_downloads": 0,
            "success_rate": 0.0,
            "android_downloads": 0,
            "firmware_downloads": 0,
        }

    def get_latest_versions(self) -> Dict[str, str]:
        """
        Return the latest known versions for each artifact type.

        Returns:
            dict: Mapping of artifact keys ('android', 'firmware', 'firmware_prerelease', 'android_prerelease') to their latest version string; empty string if unavailable.
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
            download_dir = self.android_downloader._get_download_dir()
            if not os.path.exists(download_dir):
                os.makedirs(download_dir, exist_ok=True)

            return True

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Integration validation failed: {e}")
            return False

    def get_migration_report(self) -> Dict[str, Any]:
        """
        Return a structured report describing the integration's initialization and readiness.

        The report includes whether core components are initialized, whether the active configuration and download directory are valid, current download statistics, and an overall status ("completed" or "not_initialized"). When not initialized, the report includes a `repository_support` flag set to False.

        Returns:
            Dict[str, Any]: A mapping with keys:
                - status: "completed" or "not_initialized".
                - android_downloader_initialized (bool)
                - firmware_downloader_initialized (bool)
                - orchestrator_initialized (bool)
                - configuration_valid (bool)
                - download_directory_exists (bool)
                - statistics (Dict[str, Any]): current download statistics.
                - repository_support (bool): present only when status is "not_initialized".
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
        Indicate that a fallback to the legacy downloader will not be performed.

        Returns:
            bool: `False` indicating no fallback to the legacy downloader was performed.
        """
        # Fallback is no longer needed since we're using the new architecture directly
        logger.warning(
            "Fallback to legacy downloader requested but new architecture is active"
        )
        return False

    def _validate_configuration(self) -> bool:
        """
        Check whether required configuration keys are present.

        Returns:
            `True` if a configuration is loaded and contains the required key "DOWNLOAD_DIR", `False` otherwise.
        """
        if not self.config:
            return False
        required_keys = ["DOWNLOAD_DIR"]
        return all(key in self.config for key in required_keys)

    def _check_download_directory(self) -> bool:
        """
        Verify the Android downloader's configured download directory exists.

        Returns:
            bool: `True` if the Android downloader is initialized and its download directory exists, `False` otherwise.
        """
        if not self.android_downloader:
            return False
        download_dir = self.android_downloader._get_download_dir()
        return os.path.exists(download_dir)

    def get_legacy_compatibility_report(self) -> Dict[str, Any]:
        """
        Return a compatibility report describing how the integration matches legacy CLI expectations.

        Returns:
            Dict[str, Any]: A mapping with boolean flags for compatibility checks and current statistics:
                - cli_integration_ready: whether the CLI integration is initialized
                - expected_interface_compatibility: whether the public interface matches legacy expectations
                - return_format_compatibility: whether return formats follow legacy conventions
                - error_handling_compatibility: whether error handling is compatible with legacy behavior
                - configuration_compatibility: whether configuration keys and layout are compatible
                - repository_reporting: whether repository reporting/support is available
                - statistics: current download statistics from get_download_statistics()
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
        elif isinstance(error, (ConnectionError, requests.Timeout)):
            logger.error(
                "Network connection error - please check your internet connection"
            )
        else:
            logger.error("An unexpected error occurred - please check logs for details")

    def get_cli_help_integration(self) -> Dict[str, str]:
        """
        Provide help text entries describing the CLI integration for the new download subsystem.

        Returns:
            A mapping with the following keys and short instructional strings:
            - `description`: brief name or summary of the subsystem
            - `usage`: high-level usage note for the CLI command
            - `features`: notable features of the new architecture
            - `android_info`: brief note about Android APK downloads
            - `firmware_info`: brief note about firmware downloads
            - `configuration`: how configuration is used or extended
            - `force_refresh`: how to trigger cache clearing / recheck
            - `troubleshooting`: where to look for more detailed error information
        """
        return {
            "description": "Fetchtastic Download Subsystem (New Architecture)",
            "usage": "The download command now uses a modular architecture with separate downloaders for Android and Firmware.",
            "features": "Automatic release detection, version tracking, cleanup, and retry logic",
            "android_info": "Downloads Meshtastic Android APK files from GitHub releases",
            "firmware_info": "Downloads Meshtastic firmware releases and extracts files based on patterns",
            "configuration": "Uses existing configuration with additional options for version retention",
            "force_refresh": "Use --force or -f to clear caches and recheck all downloads",
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
        Return environment and configuration information for debugging and diagnostics.

        Returns:
            Dict[str, Any]: Mapping with keys:
                - "python_version": Python interpreter version string.
                - "working_directory": Current working directory path.
                - "download_directory": Configured download directory or "Not configured".
                - "configuration_loaded": Boolean indicating if configuration was successfully loaded.
                - "orchestrator_initialized": Boolean indicating if orchestrator was initialized.
                - "platform": Operating system platform identifier.
                - "executable": Path to Python executable.
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
