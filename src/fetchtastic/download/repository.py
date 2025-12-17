"""
Meshtastic Repository File Downloader

This module implements the specific downloader for Meshtastic repository files
from the meshtastic.github.io repository.
"""

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

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
        Create a RepositoryDownloader configured for Meshtastic repository downloads.

        Initializes the base downloader with the provided configuration and sets repository-specific attributes:
        `repo_url`, `repo_downloads_dir`, and `shell_script_extension` from module constants.

        Parameters:
            config (Dict[str, Any]): Configuration options forwarded to the base downloader.
        """
        super().__init__(config)
        self.repo_url = MESHTASTIC_REPO_URL
        self.repo_downloads_dir = REPO_DOWNLOADS_DIR
        self.shell_script_extension = SHELL_SCRIPT_EXTENSION
        self._cleanup_summary: Dict[str, Any] = {
            "removed_files": 0,
            "removed_dirs": 0,
            "errors": [],
            "success": False,
        }

    def get_cleanup_summary(self) -> Dict[str, Any]:
        """
        Return the most recent cleanup summary produced by `clean_repository_directory`.

        Returns:
            Dict[str, Any]: Summary containing counts of removed files/directories, any recorded error messages, and a success flag.
        """
        return dict(self._cleanup_summary)

    def get_repository_files(self, subdirectory: str = "") -> List[Dict[str, Any]]:
        """
        Fetches file entries from the Meshtastic repository GitHub contents API for an optional subdirectory.

        Parameters:
            subdirectory (str): Relative subdirectory path within the repository; empty string refers to the repository root.

        Returns:
            List[Dict[str, Any]]: A list of file information dictionaries. Each dictionary contains the keys `name`, `path`, `download_url`, `size`, and `type`. Returns an empty list if the API response is not a file listing or an error occurs.
        """
        try:
            from fetchtastic.constants import (
                GITHUB_API_TIMEOUT,
                MESHTASTIC_GITHUB_IO_CONTENTS_URL,
            )
            from fetchtastic.utils import make_github_api_request

            # Construct API URL
            api_url = MESHTASTIC_GITHUB_IO_CONTENTS_URL
            if subdirectory:
                subdirectory = subdirectory.strip("/")
                api_url = f"{api_url}/{subdirectory}"

            # Make API request
            response = make_github_api_request(
                api_url,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=True,
                timeout=GITHUB_API_TIMEOUT,
            )

            contents = response.json()
            if not isinstance(contents, list):
                logger.warning(f"Expected list from GitHub API, got {type(contents)}")
                return []

            # Filter for files only (directories are handled by menu system)
            files = []
            for item in contents:
                if isinstance(item, dict) and item.get("type") == "file":
                    file_info = {
                        "name": item.get("name", ""),
                        "path": item.get("path", ""),
                        "download_url": item.get("download_url", ""),
                        "size": item.get("size", 0),
                        "type": "file",
                    }
                    files.append(file_info)

            logger.info(
                f"Fetched {len(files)} repository files from {subdirectory or 'root'}"
            )
            return files

        except Exception as e:
            logger.error(f"Error fetching repository files: {e}")
            return []

    def download_repository_file(
        self, file_info: Dict[str, Any], target_subdirectory: str = ""
    ) -> DownloadResult:
        """
        Download a single repository file into the repository downloads directory.

        Parameters:
            file_info (Dict[str, Any]): File metadata dictionary; must include 'name' and 'download_url', may include 'size'.
            target_subdirectory (str): Relative subdirectory (within the repository downloads area) to save the file; path traversal is disallowed.

        Returns:
            DownloadResult: Result object describing the outcome — on success includes the saved file path, download URL, size, and type; on failure includes an error message and retry/error metadata.
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

            file_name = str(file_info["name"])
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

            # Create target path (prevent traversal via filename)
            safe_name = os.path.basename(file_name)
            if not safe_name or safe_name != file_name:
                error_msg = f"Unsafe repository filename: {file_name}"
                logger.error(error_msg)
                return self.create_download_result(
                    success=False,
                    release_tag="repository",
                    file_path="",
                    error_message=error_msg,
                )
            target_path = os.path.join(target_dir, safe_name)

            # Skip if already complete
            size = file_info.get("size")
            if os.path.exists(target_path) and self.verify(target_path):
                if not size or self.file_operations.get_file_size(target_path) == size:
                    logger.info(
                        f"Repository file {file_name} already exists and is valid"
                    )
                    return self.create_download_result(
                        success=True,
                        release_tag="repository",
                        file_path=target_path,
                        download_url=download_url,
                        file_size=size,
                        file_type="repository",
                    )

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
                    download_url=download_url,
                    file_size=file_info.get("size"),
                    file_type="repository",
                )
            else:
                error_msg = f"Failed to download repository file: {file_name}"
                logger.error(error_msg)
                return self.create_download_result(
                    success=False,
                    release_tag="repository",
                    file_path=target_path,
                    error_message=error_msg,
                    download_url=download_url,
                    file_size=file_info.get("size"),
                    file_type="repository",
                    is_retryable=True,
                    error_type="network_error",
                )

        except Exception as e:
            error_msg = f"Error downloading repository file {file_info.get('name', 'unknown')}: {e}"
            logger.error(error_msg)
            return self.create_download_result(
                success=False,
                release_tag="repository",
                file_path="",
                error_message=error_msg,
                download_url=file_info.get("download_url"),
                file_size=file_info.get("size"),
                file_type="repository",
                is_retryable=True,
                error_type="network_error",
            )

    def _get_safe_target_directory(self, subdirectory: str) -> Optional[str]:
        """
        Return a safe target directory path within the repository downloads area, creating directories as needed.

        Parameters:
            subdirectory (str): Relative subdirectory path under the repository downloads directory; if empty, the base repository downloads directory is used.

        Returns:
            str | None: Absolute path to the safe target directory, or None if the directory could not be created.
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
        Determine whether a subdirectory path is safe from path-traversal and absolute-path attacks.

        Parameters:
            subdirectory (str): Candidate subdirectory (relative path segment) to validate.

        Returns:
            bool: `True` if the subdirectory is a relative path that does not contain traversal or disallowed patterns and resolves inside the repository downloads base directory; `False` otherwise.
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

            # Resolve real paths to handle symlinks and '..' components securely.
            real_base_path = os.path.realpath(base_repo_dir)
            candidate_path = os.path.join(real_base_path, subdirectory)
            real_candidate_path = os.path.realpath(candidate_path)

            # Check if the resolved candidate path is within the base directory.
            try:
                return (
                    os.path.commonpath([real_base_path, real_candidate_path])
                    == real_base_path
                )
            except ValueError:
                return False

        except (ValueError, TypeError, OSError):
            return False

    def _set_executable_permissions(self, file_path: str) -> bool:
        """
        Ensure the file has executable permission bits on Unix-like systems.

        Parameters:
            file_path (str): Path to the file to modify.

        Returns:
            bool: `True` if the file is left executable or permissions were modified successfully; `False` if an error occurred while setting permissions.
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
        Remove all contents of the repository downloads directory (<download_dir>/firmware/<repo_downloads_dir>).

        Removes files, symlinks, and subdirectories found in the repository downloads directory. If the directory does not exist the function does nothing and reports success.

        Returns:
            bool: `True` if cleanup succeeded, `False` otherwise.
        """
        self._cleanup_summary = {
            "removed_files": 0,
            "removed_dirs": 0,
            "errors": [],
            "success": False,
        }
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
                        self._cleanup_summary["removed_files"] += 1
                    elif os.path.isdir(item_path):
                        import shutil

                        shutil.rmtree(item_path)
                        logger.info(f"Removed directory: {item_path}")
                        self._cleanup_summary["removed_dirs"] += 1
                except OSError as e:
                    logger.error(f"Error removing {item_path}: {e}")
                    self._cleanup_summary["errors"].append(f"{item_path}: {e}")
                    return False

            logger.info(f"Successfully cleaned repository directory: {repo_dir}")
            self._cleanup_summary["success"] = True
            return True

        except Exception as e:
            logger.error(f"Error cleaning repository directory: {e}")
            self._cleanup_summary["errors"].append(str(e))
            return False

    def get_repository_download_url(self, file_path: str) -> str:
        """
        Builds the full download URL for a repository file given its relative repository path.

        Parameters:
            file_path (str): Relative path within the repository; must not be absolute or contain a URL scheme.

        Returns:
            str: Absolute download URL for the specified repository file.

        Raises:
            ValueError: If `file_path` contains a scheme, netloc, or is an absolute path.
        """
        file_path = str(file_path)
        parsed = urlparse(file_path)
        if parsed.scheme or parsed.netloc or file_path.startswith("/"):
            raise ValueError(f"Repository file_path must be relative: {file_path}")

        # Strip leading "./" for robustness
        normalized = file_path.lstrip("./")
        return urljoin(self.repo_url.rstrip("/") + "/", normalized)

    def download_repository_files_batch(
        self, files_info: List[Dict[str, Any]], subdirectory: str = ""
    ) -> List[DownloadResult]:
        """
        Download multiple repository files into the repository downloads directory.

        Downloads each file described in `files_info` and returns a per-file DownloadResult in the same order.

        Parameters:
            files_info (List[Dict[str, Any]]): List of file info dictionaries. Each dictionary must include at least the `name` and `download_url` keys and may include `path` and `size`.
            subdirectory (str): Optional relative subdirectory under the repository downloads directory where files will be saved.

        Returns:
            List[DownloadResult]: A list of DownloadResult objects corresponding to each input file, in the same order.
        """
        results: List[DownloadResult] = []

        if not files_info:
            logger.info("No files to download")
            return results

        for file_info in files_info:
            result = self.download_repository_file(file_info, subdirectory)
            results.append(result)

        return results

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Remove all downloaded repository files; retention limits are ignored.

        Repository repository files are not versioned like other artifacts, so this method clears the repository downloads directory instead of retaining a limited number of versions.

        Parameters:
            keep_limit (int): Suggested number of versions to keep; ignored for repository downloads.
        """
        # Repository files are stored in a flat structure, so we clean the entire directory
        self.clean_repository_directory()

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Return a fixed identifier representing the latest repository release tag.

        Returns:
            str: The fixed tag "repository-latest" used for repository downloads.
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

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate that extraction include and exclude patterns are well-formed and acceptable.

        Parameters:
            patterns (List[str]): Glob or filename patterns to include when extracting.
            exclude_patterns (List[str]): Glob or filename patterns to exclude when extracting.

        Returns:
            bool: `True` if the provided patterns are valid and usable, `False` otherwise.
        """
        # Repository files are typically not extracted, but validate patterns for safety
        return self.file_operations.validate_extraction_patterns(
            patterns, exclude_patterns
        )

    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Check if extraction is needed for repository files.

        Since repository files are typically not archives, this method
        always returns False to indicate that extraction is not needed.

        Args:
            file_path: Path to the repository file
            extract_dir: Directory where files would be extracted
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: False (extraction not needed for repository files)
        """
        # Repository files are typically not archives, so extraction is never needed
        logger.debug(
            "Extraction need check called for repository file - not applicable"
        )
        return False

    def should_download_release(self, release_tag: str, asset_name: str) -> bool:
        """
        Decide whether a repository release asset should be downloaded.

        Repository downloads do not filter by release tag or asset name; repository assets are always selected for download.

        Returns:
            `True` if the asset should be downloaded (`True` for all repository assets).
        """
        # Repository downloads don't use pattern filtering in the same way
        return True
