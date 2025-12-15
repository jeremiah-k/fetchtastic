"""
CLI Integration for New Download Subsystem

This module provides integration between the new download subsystem and the existing CLI.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from fetchtastic.log_utils import logger

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
        Run the download process using the new architecture.

        This method provides the same interface as the legacy downloader.main() method
        to ensure backward compatibility with the CLI.

        Args:
            config: Configuration dictionary
            force_refresh: Whether to force refresh caches

        Returns:
            Tuple containing:
            - downloaded_firmwares: List of downloaded firmware versions
            - new_firmware_versions: List of new firmware versions
            - downloaded_apks: List of downloaded APK versions
            - new_apk_versions: List of new APK versions
            - failed_downloads: List of failed downloads with details
            - latest_firmware_version: Latest firmware version
            - latest_apk_version: Latest APK version
        """
        try:
            # Initialize components with the provided config
            self.config = config
            self.orchestrator = DownloadOrchestrator(config)
            self.android_downloader = MeshtasticAndroidAppDownloader(config)
            self.firmware_downloader = FirmwareReleaseDownloader(config)

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
            latest_versions = self.orchestrator.get_latest_versions()
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

        except Exception as e:
            logger.error(f"Error in CLI integration: {e}")
            # Return empty results and error information
            return [], [], [], [], [], "", ""

    def _clear_caches(self) -> None:
        """Clear all caches as requested by force refresh."""
        try:
            # Clear cache manager caches
            if self.android_downloader:
                self.android_downloader.cache_manager.clear_all_caches()

            logger.info("All caches cleared")

        except Exception as e:
            logger.error(f"Error clearing caches: {e}")

    def _convert_results_to_legacy_format(
        self, success_results: List[Any]
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        Convert new download results to legacy format.

        Args:
            success_results: List of successful download results

        Returns:
            Tuple containing lists in legacy format
        """
        downloaded_firmwares = []
        new_firmware_versions = []
        downloaded_apks = []
        new_apk_versions = []

        # Get current versions before processing results
        current_android = self.android_downloader.get_latest_release_tag()
        current_firmware = self.firmware_downloader.get_latest_release_tag()

        for result in success_results:
            # Legacy parity: "already complete" skips should not be reported as
            # downloaded versions in the CLI summary.
            if getattr(result, "was_skipped", False):
                continue
            if result.release_tag:
                # Determine if this is firmware or Android based on file path
                if result.file_path and "firmware" in str(result.file_path):
                    if result.release_tag not in downloaded_firmwares:
                        downloaded_firmwares.append(result.release_tag)
                        # Check if this is a new version
                        if not current_firmware or self._is_newer_version(
                            result.release_tag, current_firmware
                        ):
                            new_firmware_versions.append(result.release_tag)
                elif result.file_path and "android" in str(result.file_path):
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
        Check if version1 is newer than version2.

        Args:
            version1: First version to compare
            version2: Second version to compare

        Returns:
            bool: True if version1 is newer than version2
        """
        version_manager = self.android_downloader.get_version_manager()
        comparison = version_manager.compare_versions(version1, version2)
        return comparison > 0

    def get_failed_downloads(self) -> List[Dict[str, Any]]:
        """
        Get failed downloads in legacy format.

        Returns:
            List[Dict[str, str]]: List of failed downloads with details
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
        List[str], List[str], List[str], List[str], List[Dict[str, str]], str, str
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

            results = self.run_download(config, force_refresh)
            self.log_integration_summary()
            return results

        except Exception as error:
            self.handle_cli_error(error)
            return [], [], [], [], [], "", ""

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Get download statistics for reporting.

        Returns:
            Dict[str, Any]: Dictionary containing download statistics
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
        Get the latest versions of all artifact types.

        Returns:
            Dict[str, str]: Dictionary mapping artifact types to latest versions
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
        Validate that the CLI integration is working properly.

        Returns:
            bool: True if validation passed
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

        except Exception as e:
            logger.error(f"Integration validation failed: {e}")
            return False

    def get_migration_report(self) -> Dict[str, Any]:
        """
        Get a report on the integration status.

        Returns:
            Dict[str, Any]: Integration status report
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
        Fallback to legacy downloader if integration fails.

        Returns:
            bool: True if fallback was attempted
        """
        # Fallback is no longer needed since we're using the new architecture directly
        logger.warning(
            "Fallback to legacy downloader requested but new architecture is active"
        )
        return False

    def _validate_configuration(self) -> bool:
        """Validate the configuration."""
        if not self.config:
            return False
        required_keys = ["DOWNLOAD_DIR"]
        return all(key in self.config for key in required_keys)

    def _check_download_directory(self) -> bool:
        """Check if download directory exists."""
        if not self.android_downloader:
            return False
        download_dir = self.android_downloader._get_download_dir()
        return os.path.exists(download_dir)

    def get_legacy_compatibility_report(self) -> Dict[str, Any]:
        """
        Get a report on legacy compatibility.

        Returns:
            Dict[str, Any]: Legacy compatibility report
        """
        return {
            "cli_integration_ready": True,
            "expected_interface_compatibility": True,
            "return_format_compatibility": True,
            "error_handling_compatibility": True,
            "configuration_compatibility": True,
            "repository_reporting": True,
            "statistics": self.get_download_statistics(),
        }

    def log_integration_summary(self) -> None:
        """Log a summary of the integration process."""
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
        Handle CLI errors and provide user-friendly messages.

        Args:
            error: The exception that occurred
        """
        logger.error(f"CLI Error: {str(error)}")

        # Provide specific guidance based on error type
        if isinstance(error, ImportError):
            logger.error(
                "Import error - please check your Python environment and dependencies"
            )
        elif isinstance(error, FileNotFoundError):
            logger.error("File not found - please check your configuration and paths")
        elif isinstance(error, PermissionError):
            logger.error("Permission error - please check file system permissions")
        elif isinstance(error, ConnectionError):
            logger.error(
                "Network connection error - please check your internet connection"
            )
        else:
            logger.error("An unexpected error occurred - please check logs for details")

    def get_cli_help_integration(self) -> Dict[str, str]:
        """
        Get CLI help information for the new download subsystem.

        Returns:
            Dict[str, str]: Dictionary containing help information
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
        Update CLI progress information.

        Args:
            message: Progress message
            progress: Progress percentage (0.0 to 1.0)
        """
        if progress > 0:
            logger.info(f"Progress: {progress * 100:.1f}% - {message}")
        else:
            logger.info(f"Status: {message}")

    def get_environment_info(self) -> Dict[str, Any]:
        """
        Get environment information for debugging.

        Returns:
            Dict[str, Any]: Environment information
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

    def _get_existing_prerelease_dirs(self, prerelease_dir: str) -> List[str]:
        """List existing prerelease directory names."""
        if not os.path.exists(prerelease_dir):
            return []

        entries = []
        try:
            for entry in os.listdir(prerelease_dir):
                full_path = os.path.join(prerelease_dir, entry)
                if os.path.isdir(full_path) and not os.path.islink(full_path):
                    if entry.startswith("firmware-"):
                        entries.append(entry)
        except OSError:
            pass
        return entries

    def check_for_prereleases(
        self,
        download_dir: str,
        latest_release_tag: str,
        selected_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        device_manager=None,
        github_token: Optional[str] = None,
        force_refresh: bool = False,
        allow_env_token: bool = True,
    ) -> Tuple[bool, List[str]]:
        """
        Detect and download matching prerelease firmware assets for the expected prerelease version.

        This is a compatibility method that provides the same interface as the legacy
        check_for_prereleases function, implemented using the new modular architecture.

        Args:
            download_dir: Base download directory containing firmware/prerelease subdirectory
            latest_release_tag: Official release tag used to compute expected prerelease version
            selected_patterns: Asset selection patterns
            exclude_patterns: Patterns to exclude from matching assets
            device_manager: Optional device pattern resolver
            github_token: GitHub API token
            force_refresh: Force remote checks and update tracking
            allow_env_token: Allow using token from environment

        Returns:
            Tuple of (downloaded: bool, versions: List[str])
        """
        if not self.orchestrator or not self.firmware_downloader:
            logger.error("Orchestrator not initialized")
            return False, []

        # Use the orchestrator's components
        version_manager = self.orchestrator.version_manager
        prerelease_manager = self.orchestrator.prerelease_manager
        firmware_downloader = self.firmware_downloader

        # Calculate expected prerelease version
        expected_version = version_manager.calculate_expected_prerelease_version(
            latest_release_tag
        )
        if not expected_version:
            logger.warning(
                f"Could not calculate expected prerelease version from {latest_release_tag}"
            )
            return False, []

        logger.debug(f"Expected prerelease version: {expected_version}")

        # Set up prerelease directory
        prerelease_base_dir = os.path.join(download_dir, "firmware", "prerelease")
        os.makedirs(prerelease_base_dir, exist_ok=True)

        # Check for existing prereleases locally
        existing_dirs = self._get_existing_prerelease_dirs(prerelease_base_dir)

        # Try to get latest active prerelease from history
        try:
            latest_active_dir = (
                prerelease_manager.get_latest_active_prerelease_from_history(
                    expected_version,
                    github_token=github_token,
                    force_refresh=force_refresh,
                    allow_env_token=allow_env_token,
                )
            )

            if latest_active_dir and latest_active_dir in existing_dirs:
                # Latest active prerelease already exists locally
                remote_dir = latest_active_dir
                logger.debug(f"Using existing active prerelease: {remote_dir}")
                return False, [remote_dir]
            elif latest_active_dir:
                # Latest active prerelease found remotely but not local
                remote_dir = latest_active_dir
                logger.debug(f"Found remote active prerelease: {remote_dir}")
            else:
                # No active prerelease found, fall back to directory scanning
                remote_dir = None
                logger.debug("No active prerelease found in commit history")

        except Exception as exc:
            logger.debug(f"Failed to get prerelease commit history: {exc}")
            remote_dir = None

        # Fallback to directory scanning
        if not remote_dir:
            logger.debug("Falling back to directory scanning approach")

            # Find newest matching prerelease directory
            matching_dirs = [
                d
                for d in existing_dirs
                if version_manager.extract_clean_version(d).startswith(expected_version)
            ]

            if matching_dirs:
                # Sort by version
                matching_dirs.sort(
                    key=lambda d: version_manager.get_release_tuple(d) or (),
                    reverse=True,
                )
                newest_dir = matching_dirs[0]
                logger.debug(f"Found existing prerelease: {newest_dir}")
                return False, [newest_dir]

            # Find latest remote prerelease directory
            remote_dir = prerelease_manager.find_latest_remote_prerelease_dir(
                expected_version,
                github_token=github_token,
                force_refresh=force_refresh,
                allow_env_token=allow_env_token,
            )

            if not remote_dir:
                return False, []

        # Download assets for the selected prerelease
        files_downloaded = firmware_downloader.download_prerelease_assets(
            remote_dir,
            prerelease_base_dir,
            selected_patterns or [],
            exclude_patterns or [],
            device_manager,
            force_refresh,
            github_token=github_token,
            allow_env_token=allow_env_token,
        )

        # Update tracking information
        if files_downloaded or force_refresh:
            prerelease_manager.update_prerelease_tracking(
                latest_release_tag, remote_dir
            )

        # Clean up old prerelease directories if appropriate
        should_cleanup = files_downloaded or remote_dir in existing_dirs
        if should_cleanup:
            firmware_downloader._cleanup_old_prerelease_dirs(
                prerelease_base_dir, remote_dir, existing_dirs
            )

        return files_downloaded, [remote_dir] if files_downloaded else []
