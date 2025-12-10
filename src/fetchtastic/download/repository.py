"""
Meshtastic Repository File Downloader

This module implements the specific downloader for Meshtastic repository files
from the meshtastic.github.io repository.
"""

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from fetchtastic.constants import (
    MESHTASTIC_REPO_URL,
    REPO_DOWNLOADS_DIR,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.log_utils import logger

from .base import BaseDownloader
from .interfaces import DownloadResult


class RepositoryDownloader(BaseDownloader):
    """
    Downloader for Meshtastic repository files from meshtastic.github.io.

    This class handles:
    - Fetching repository file listings
    - Downloading repository files with proper directory structure
    - Managing repository-specific file organization
    - Setting executable permissions for shell scripts
    - Path traversal protection and security
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the repository downloader.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.repo_url = MESHTASTIC_REPO_URL
        self.repo_downloads_dir = REPO_DOWNLOADS_DIR
        self.shell_script_extension = SHELL_SCRIPT_EXTENSION

    def get_repository_files(self, subdirectory: str = "") -> List[Dict[str, Any]]:
        """
        Get available files from the Meshtastic repository.

        Args:
            subdirectory: Optional subdirectory path within the repository

        Returns:
            List[Dict[str, Any]]: List of file information dictionaries
        """
        # This would typically make an HTTP request to fetch the repository listing
        # For now, we'll return an empty list as this would require actual API integration
        logger.info(f"Fetching repository files from {self.repo_url}/{subdirectory}")
        return []

    def download_repository_file(
        self, file_info: Dict[str, Any], target_subdirectory: str = ""
    ) -> DownloadResult:
        """
        Download a specific repository file.

        Args:
            file_info: Dictionary containing file information (name, download_url)
            target_subdirectory: Optional subdirectory within repo-dls to save to

        Returns:
            DownloadResult: Result of the download operation
        """
        try:
            # Validate file info
            if (
                not file_info
                or "name" not in file_info
                or "download_url" not in file_info
            ):
                error_msg = "Invalid file info - missing required fields"
                logger.error(error_msg)
                return self.create_download_result(
                    success=False,
                    release_tag="repository",
                    file_path="",
                    error_message=error_msg,
                )

            file_name = file_info["name"]
            download_url = file_info["download_url"]

            # Create safe target directory path
            target_dir = self._get_safe_target_directory(target_subdirectory)
            if not target_dir:
                error_msg = f"Invalid target subdirectory: {target_subdirectory}"
                logger.error(error_msg)
                return self.create_download_result(
                    success=False,
                    release_tag="repository",
                    file_path="",
                    error_message=error_msg,
                )

            # Create target path
            target_path = os.path.join(target_dir, file_name)

            # Download the file
            success = self.download(download_url, target_path)

            if success:
                # Set executable permissions for shell scripts
                if file_name.lower().endswith(self.shell_script_extension):
                    self._set_executable_permissions(target_path)

                logger.info(f"Successfully downloaded repository file: {file_name}")
                return self.create_download_result(
                    success=True,
                    release_tag="repository",
                    file_path=target_path,
                )
            else:
                error_msg = f"Failed to download repository file: {file_name}"
                logger.error(error_msg)
                return self.create_download_result(
                    success=False,
                    release_tag="repository",
                    file_path=target_path,
                    error_message=error_msg,
                )

        except Exception as e:
            error_msg = f"Error downloading repository file {file_info.get('name', 'unknown')}: {e}"
            logger.error(error_msg)
            return self.create_download_result(
                success=False,
                release_tag="repository",
                file_path="",
                error_message=error_msg,
            )

    def _get_safe_target_directory(self, subdirectory: str) -> Optional[str]:
        """
        Get a safe target directory path with path traversal protection.

        Args:
            subdirectory: The subdirectory path to validate and use

        Returns:
            Optional[str]: Safe target directory path, or None if invalid
        """
        try:
            # Create base repo downloads directory
            base_repo_dir = os.path.join(
                self.download_dir, "firmware", self.repo_downloads_dir
            )
            os.makedirs(base_repo_dir, exist_ok=True)

            # If no subdirectory specified, use base repo directory
            if not subdirectory:
                return base_repo_dir

            # Validate and sanitize subdirectory path
            if not self._is_safe_subdirectory(subdirectory):
                logger.warning(
                    f"Sanitized unsafe repository subdirectory '{subdirectory}'; "
                    f"using base repo directory"
                )
                return base_repo_dir

            # Create full target directory path
            target_dir = os.path.join(base_repo_dir, subdirectory)
            os.makedirs(target_dir, exist_ok=True)

            return target_dir

        except OSError as e:
            logger.error(f"Error creating repository download directory: {e}")
            return None

    def _is_safe_subdirectory(self, subdirectory: str) -> bool:
        """
        Check if a subdirectory path is safe (no path traversal attempts).

        Args:
            subdirectory: The subdirectory path to validate

        Returns:
            bool: True if the subdirectory is safe, False otherwise
        """
        # Check for path traversal patterns
        if re.search(r"(\.\./|\.\.\\|~|\\|\.\.)", subdirectory):
            return False

        # Check for absolute paths
        if os.path.isabs(subdirectory):
            return False

        # Check that the normalized path doesn't escape the base directory
        try:
            base_repo_dir = os.path.join(
                self.download_dir, "firmware", self.repo_downloads_dir
            )
            test_path = os.path.join(base_repo_dir, subdirectory)
            normalized_path = os.path.normpath(test_path)

            # Ensure the normalized path still starts with the base repo directory
            if not normalized_path.startswith(os.path.normpath(base_repo_dir)):
                return False

            return True
        except (ValueError, TypeError):
            return False

    def _set_executable_permissions(self, file_path: str) -> bool:
        """
        Set executable permissions for shell script files.

        Args:
            file_path: Path to the file to set permissions for

        Returns:
            bool: True if permissions were set successfully, False otherwise
        """
        try:
            if os.name != "nt":  # Only set permissions on Unix-like systems
                import stat

                current_permissions = os.stat(file_path).st_mode
                new_permissions = (
                    current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
                os.chmod(file_path, new_permissions)
                logger.debug(
                    f"Set executable permissions for {os.path.basename(file_path)}"
                )
                return True
            return True  # On Windows, files are always "executable" if they have the right extension
        except OSError as e:
            logger.warning(f"Error setting permissions for {file_path}: {e}")
            return False

    def clean_repository_directory(self) -> bool:
        """
        Clean the repository downloads directory by removing all contents.

        Returns:
            bool: True if cleanup succeeded, False otherwise
        """
        try:
            repo_dir = os.path.join(
                self.download_dir, "firmware", self.repo_downloads_dir
            )

            if not os.path.exists(repo_dir):
                logger.info(
                    "Repository downloads directory does not exist - nothing to clean"
                )
                return True

            # Remove all contents of the repository directory
            for item in os.listdir(repo_dir):
                item_path = os.path.join(repo_dir, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.remove(item_path)
                        logger.info(f"Removed file: {item_path}")
                    elif os.path.isdir(item_path):
                        import shutil

                        shutil.rmtree(item_path)
                        logger.info(f"Removed directory: {item_path}")
                except OSError as e:
                    logger.error(f"Error removing {item_path}: {e}")
                    return False

            logger.info(f"Successfully cleaned repository directory: {repo_dir}")
            return True

        except Exception as e:
            logger.error(f"Error cleaning repository directory: {e}")
            return False

    def get_repository_download_url(self, file_path: str) -> str:
        """
        Get the full download URL for a repository file.

        Args:
            file_path: The file path within the repository

        Returns:
            str: Full download URL
        """
        return urljoin(self.repo_url, file_path)

    def download_repository_files_batch(
        self, files_info: List[Dict[str, Any]], subdirectory: str = ""
    ) -> List[DownloadResult]:
        """
        Download multiple repository files in a batch.

        Args:
            files_info: List of file information dictionaries
            subdirectory: Optional subdirectory within repo-dls to save to

        Returns:
            List[DownloadResult]: List of download results for each file
        """
        results = []

        if not files_info:
            logger.info("No files to download")
            return results

        for file_info in files_info:
            result = self.download_repository_file(file_info, subdirectory)
            results.append(result)

        return results

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old repository versions according to retention policy.

        For repository downloads, this cleans the entire repo-dls directory
        since repository files are not versioned like firmware/Android releases.

        Args:
            keep_limit: Maximum number of versions to keep (not used for repository)
        """
        # Repository files are stored in a flat structure, so we clean the entire directory
        self.clean_repository_directory()

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Get the latest repository release tag.

        For repository downloads, this returns a fixed identifier since
        repository files are not versioned like other artifacts.

        Returns:
            Optional[str]: Latest release tag identifier
        """
        return "repository-latest"

    def update_latest_release_tag(self, release_tag: str) -> bool:
        """
        Update the latest repository release tag.

        For repository downloads, this is a no-op since repository files
        are not versioned like other artifacts.

        Args:
            release_tag: The release tag to record

        Returns:
            bool: Always returns True for repository downloads
        """
        # Repository downloads don't use version tracking
        return True

    def should_download_release(self, release_tag: str, asset_name: str) -> bool:
        """
        Determine if a repository release should be downloaded.

        For repository downloads, this always returns True since we want
        to download all selected files.

        Args:
            release_tag: The release tag to check
            asset_name: The asset name to check

        Returns:
            bool: Always True for repository downloads
        """
        # Repository downloads don't use pattern filtering in the same way
        return True
