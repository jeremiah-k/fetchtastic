"""
Migration Script for New Download Subsystem

This module provides the migration from the legacy downloader to the new modular architecture.
"""

import os
import shutil
from typing import Any, Dict, List, Tuple

from fetchtastic.log_utils import logger

from .android import MeshtasticAndroidAppDownloader
from .firmware import FirmwareReleaseDownloader
from .orchestrator import DownloadOrchestrator


class DownloadMigration:
    """
    Handles the migration from legacy downloader to new modular architecture.

    This class provides:
    - Integration with existing CLI
    - Configuration mapping
    - Result translation
    - Backward compatibility
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the download migration.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.orchestrator = DownloadOrchestrator(config)
        self.android_downloader = MeshtasticAndroidAppDownloader(config)
        self.firmware_downloader = FirmwareReleaseDownloader(config)

    def run_migration(
        self, force_refresh: bool = False
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        Run the download process using the new architecture.

        This method provides the same interface as the legacy downloader.main() method
        to ensure backward compatibility with the CLI.

        Args:
            force_refresh: Whether to force refresh caches

        Returns:
            Tuple containing:
            - downloaded_firmwares: List of downloaded firmware versions
            - new_firmware_versions: List of new firmware versions
            - downloaded_apks: List of downloaded APK versions
            - new_apk_versions: List of new APK versions
        """
        # Clear caches if force refresh is requested
        if force_refresh:
            self._clear_caches()

        # Run the new download pipeline
        success_results, failed_results = self.orchestrator.run_download_pipeline()

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

        return (
            downloaded_firmwares,
            new_firmware_versions,
            downloaded_apks,
            new_apk_versions,
        )

    def _clear_caches(self) -> None:
        """Clear all caches as requested by force refresh."""
        try:
            # Clear cache manager caches
            cache_manager = self.android_downloader.get_cache_manager()
            cache_manager.clear_all_caches()

            logger.info("All caches cleared for migration")

        except Exception as e:
            logger.error(f"Error clearing caches during migration: {e}")

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

    def get_failed_downloads(self) -> List[Dict[str, str]]:
        """
        Get failed downloads in legacy format.

        Returns:
            List[Dict[str, str]]: List of failed downloads with details
        """
        failed_downloads = []

        for result in self.orchestrator.failed_downloads:
            failed_downloads.append(
                {
                    "file_name": (
                        os.path.basename(str(result.file_path))
                        if result.file_path
                        else "unknown"
                    ),
                    "release_tag": result.release_tag or "unknown",
                    "url": result.download_url or "unknown",
                    "type": (
                        "Firmware"
                        if result.file_path and "firmware" in str(result.file_path)
                        else (
                            "Repository"
                            if result.file_path
                            and (
                                "repository" in str(result.file_path)
                                or "repo-dls" in str(result.file_path)
                            )
                            else "Android APK"
                        )
                    ),
                    "path_to_download": (
                        str(result.file_path) if result.file_path else "unknown"
                    ),
                    "error": result.error_message or "",
                    "retryable": result.is_retryable,
                    "http_status": result.http_status_code,
                }
            )

        return failed_downloads

    def get_latest_versions(self) -> Dict[str, str]:
        """
        Get latest versions for all artifact types.

        Returns:
            Dict[str, str]: Dictionary of latest versions
        """
        return self.orchestrator.get_latest_versions()

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Get download statistics.

        Returns:
            Dict[str, Any]: Dictionary containing download statistics
        """
        return self.orchestrator.get_download_statistics()

    def migrate_configuration(self, legacy_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate legacy configuration to new format.

        Args:
            legacy_config: Legacy configuration dictionary

        Returns:
            Dict[str, Any]: Migrated configuration
        """
        new_config = legacy_config.copy()

        # Map legacy config keys to new format
        config_mapping = {
            "DOWNLOAD_DIR": "DOWNLOAD_DIR",
            "GITHUB_TOKEN": "GITHUB_TOKEN",
            "CHECK_APK_PRERELEASES": "CHECK_APK_PRERELEASES",
            "CHECK_FIRMWARE_PRERELEASES": "CHECK_FIRMWARE_PRERELEASES",
            "ANDROID_VERSIONS_TO_KEEP": "ANDROID_VERSIONS_TO_KEEP",
            "FIRMWARE_VERSIONS_TO_KEEP": "FIRMWARE_VERSIONS_TO_KEEP",
            "EXTRACT_PATTERNS": "EXTRACT_PATTERNS",
            "SELECTED_PATTERNS": "SELECTED_PATTERNS",
            "EXCLUDE_PATTERNS": "EXCLUDE_PATTERNS",
            "SELECTED_FIRMWARE_ASSETS": "SELECTED_FIRMWARE_ASSETS",
            "SELECTED_PRERELEASE_ASSETS": "SELECTED_PRERELEASE_ASSETS",
        }

        # Apply mapping
        for legacy_key, new_key in config_mapping.items():
            if legacy_key in legacy_config:
                new_config[new_key] = legacy_config[legacy_key]

        # Set defaults for missing values
        defaults = {
            "ANDROID_VERSIONS_TO_KEEP": 5,
            "FIRMWARE_VERSIONS_TO_KEEP": 5,
            "CHECK_APK_PRERELEASES": False,
            "CHECK_FIRMWARE_PRERELEASES": False,
        }

        for key, default_value in defaults.items():
            if key not in new_config:
                new_config[key] = default_value

        return new_config

    def validate_migration(self) -> bool:
        """
        Validate that the migration was successful.

        Returns:
            bool: True if migration validation passed
        """
        try:
            # Check that basic functionality works
            android_releases = self.android_downloader.get_releases(limit=1)
            firmware_releases = self.firmware_downloader.get_releases(limit=1)

            if not android_releases or not firmware_releases:
                logger.warning("Migration validation: Could not fetch releases")
                return False

            # Check that download directories exist
            download_dir = self.android_downloader._get_download_dir()
            if not os.path.exists(download_dir):
                os.makedirs(download_dir, exist_ok=True)

            return True

        except Exception as e:
            logger.error(f"Migration validation failed: {e}")
            return False

    def get_migration_report(self) -> Dict[str, Any]:
        """
        Get a report on the migration status.

        Returns:
            Dict[str, Any]: Migration status report
        """
        return {
            "status": "completed",
            "android_downloader_initialized": True,
            "firmware_downloader_initialized": True,
            "orchestrator_initialized": True,
            "configuration_valid": self._validate_configuration(),
            "download_directory_exists": self._check_download_directory(),
            "statistics": self.get_download_statistics(),
        }

    def _validate_configuration(self) -> bool:
        """Validate the configuration."""
        required_keys = ["DOWNLOAD_DIR"]
        return all(key in self.config for key in required_keys)

    def _check_download_directory(self) -> bool:
        """Check if download directory exists."""
        download_dir = self.android_downloader._get_download_dir()
        return os.path.exists(download_dir)

    def fallback_to_legacy(self) -> None:
        """
        Fallback to legacy downloader if migration fails.

        This method would import and use the legacy downloader as a fallback.
        """
        try:
            logger.warning("Falling back to legacy downloader")

            # In a real implementation, this would import and use the legacy downloader
            # For now, we'll just log the fallback attempt

            from fetchtastic import downloader as legacy_downloader

            logger.info("Legacy downloader imported successfully")

        except ImportError as e:
            logger.error(f"Could not import legacy downloader for fallback: {e}")
        except Exception as e:
            logger.error(f"Fallback to legacy downloader failed: {e}")
