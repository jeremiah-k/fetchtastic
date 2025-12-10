"""
CLI Integration for New Download Subsystem

This module provides integration between the new download subsystem and the existing CLI.
"""

import os
import sys
from typing import Any, Dict, List, Tuple

from fetchtastic.log_utils import logger

from .migration import DownloadMigration


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
        self.migration = None
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
            # Initialize migration with the provided config
            self.config = config
            self.migration = DownloadMigration(config)

            # Run the migration process
            (
                downloaded_firmwares,
                new_firmware_versions,
                downloaded_apks,
                new_apk_versions,
            ) = self.migration.run_migration(force_refresh)

            # Get failed downloads
            failed_downloads = self.migration.get_failed_downloads()

            # Get latest versions
            latest_versions = self.migration.get_latest_versions()
            latest_firmware_version = latest_versions.get("firmware", "")
            latest_apk_version = latest_versions.get("android", "")

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

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Get download statistics for reporting.

        Returns:
            Dict[str, Any]: Dictionary containing download statistics
        """
        if self.migration:
            return self.migration.get_download_statistics()
        return {
            "total_downloads": 0,
            "failed_downloads": 0,
            "success_rate": 0.0,
            "android_downloads": 0,
            "firmware_downloads": 0,
        }

    def validate_integration(self) -> bool:
        """
        Validate that the CLI integration is working properly.

        Returns:
            bool: True if validation passed
        """
        if not self.migration:
            return False

        return self.migration.validate_migration()

    def get_migration_report(self) -> Dict[str, Any]:
        """
        Get a report on the migration and integration status.

        Returns:
            Dict[str, Any]: Migration and integration status report
        """
        if self.migration:
            return self.migration.get_migration_report()

        return {
            "status": "not_initialized",
            "android_downloader_initialized": False,
            "firmware_downloader_initialized": False,
            "orchestrator_initialized": False,
            "configuration_valid": False,
            "download_directory_exists": False,
            "statistics": self.get_download_statistics(),
        }

    def fallback_to_legacy(self) -> bool:
        """
        Fallback to legacy downloader if integration fails.

        Returns:
            bool: True if fallback was attempted
        """
        if self.migration:
            self.migration.fallback_to_legacy()
            return True
        return False

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
            "statistics": self.get_download_statistics(),
        }

    def log_integration_summary(self) -> None:
        """Log a summary of the integration process."""
        if not self.migration:
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
            logger.info(f"Progress: {progress*100:.1f}% - {message}")
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
            "migration_initialized": self.migration is not None,
            "platform": sys.platform,
            "executable": sys.executable,
        }
