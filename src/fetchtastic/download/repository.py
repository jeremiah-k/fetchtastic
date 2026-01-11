"""
Meshtastic Repository File Downloader

This module implements the specific downloader for Meshtastic repository files
from the meshtastic.github.io repository.
"""

import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import (
    ERROR_TYPE_NETWORK,
    FILE_TYPE_REPOSITORY,
    FIRMWARE_DIR_NAME,
    MESHTASTIC_REPO_URL,
    REPO_DOWNLOADS_DIR,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.log_utils import logger

from .base import BaseDownloader
from .interfaces import DownloadResult, Release


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
        Get a copy of the most recent repository cleanup summary.

        The summary contains counts and status produced by clean_repository_directory.

        Returns:
            dict: A copy of the cleanup summary with keys:
                - "removed_files" (int): number of files removed
                - "removed_dirs" (int): number of directories removed
                - "errors" (List[str]): recorded error messages, if any
                - "success" (bool): True if cleanup completed without errors, False otherwise
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
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
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

        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
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
                        file_type=FILE_TYPE_REPOSITORY,
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
                    file_type=FILE_TYPE_REPOSITORY,
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
                    file_type=FILE_TYPE_REPOSITORY,
                    is_retryable=True,
                    error_type=ERROR_TYPE_NETWORK,
                )

        except (requests.RequestException, OSError, ValueError) as e:
            error_msg = f"Error downloading repository file {file_info.get('name', 'unknown')}: {e}"
            logger.error(error_msg)
            return self.create_download_result(
                success=False,
                release_tag="repository",
                file_path="",
                error_message=error_msg,
                download_url=file_info.get("download_url"),
                file_size=file_info.get("size"),
                file_type=FILE_TYPE_REPOSITORY,
                is_retryable=True,
                error_type=ERROR_TYPE_NETWORK,
            )

    def _get_safe_target_directory(self, subdirectory: str) -> Optional[str]:
        """
        Resolve a safe absolute target directory under the repository downloads area, creating it if necessary.

        Parameters:
            subdirectory (str): Relative subpath inside the repository downloads area. If empty the base repository downloads directory is used; subpaths that are unsafe or appear to perform path traversal are treated as if empty and the base directory will be returned.

        Returns:
            str | None: Absolute filesystem path to the resolved target directory, or `None` if the directory could not be created.
        """
        try:
            # Create base repo downloads directory
            base_repo_dir = os.path.join(
                self.download_dir, FIRMWARE_DIR_NAME, self.repo_downloads_dir
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
                self.download_dir, FIRMWARE_DIR_NAME, self.repo_downloads_dir
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
        Set the file's executable permission bits on Unix-like systems.

        On Unix-like systems this adds user/group/other execute bits; on Windows this is a no-op and returns True.

        Parameters:
            file_path (str): Path to the target file.

        Returns:
            bool: `True` if the file is executable after the call, `False` if an OSError occurred while setting permissions.
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
        Remove all contents of the repository downloads directory under the configured downloads area.

        Removes files, symbolic links, and subdirectories found in <download_dir>/<firmware-dir>/<repo_downloads_dir>. If the directory does not exist the function does nothing and reports success. Updates the instance's cleanup summary with counts of removed files and directories, any errors encountered, and an overall success flag.

        Returns:
            bool: `True` if cleanup completed without errors, `False` otherwise.
        """
        self._cleanup_summary = {
            "removed_files": 0,
            "removed_dirs": 0,
            "errors": [],
            "success": False,
        }
        try:
            repo_dir = os.path.join(
                self.download_dir, FIRMWARE_DIR_NAME, self.repo_downloads_dir
            )

            if not os.path.exists(repo_dir):
                logger.info(
                    "Repository downloads directory does not exist - nothing to clean: %s",
                    repo_dir,
                )
                self._cleanup_summary["success"] = True
                return True

            logger.info("Cleaning repository downloads directory: %s", repo_dir)

            def _format_entry_path(path: str) -> str:
                try:
                    rel_path = os.path.relpath(path, repo_dir)
                except ValueError:
                    return path
                if rel_path.startswith(os.pardir + os.sep) or rel_path == os.pardir:
                    return path
                return rel_path

            # Remove all contents of the repository directory
            had_errors = False
            with os.scandir(repo_dir) as it:
                for entry in it:
                    try:
                        entry_display = _format_entry_path(entry.path)
                        if entry.is_file() or entry.is_symlink():
                            os.remove(entry.path)
                            logger.info("Removed file: %s", entry_display)
                            self._cleanup_summary["removed_files"] += 1
                        elif entry.is_dir():
                            shutil.rmtree(entry.path)
                            logger.info("Removed directory: %s", entry_display)
                            self._cleanup_summary["removed_dirs"] += 1
                    except OSError as e:
                        logger.error("Error removing %s: %s", entry_display, e)
                        self._cleanup_summary["errors"].append(f"{entry_display}: {e}")
                        had_errors = True

            if not had_errors:
                logger.info("Successfully cleaned repository directory: %s", repo_dir)
            self._cleanup_summary["success"] = not had_errors
            return not had_errors

        except OSError as e:
            logger.error(f"Error cleaning repository directory: {e}")
            self._cleanup_summary["errors"].append(str(e))
            return False

    def get_repository_download_url(self, file_path: str) -> str:
        """
        Constructs an absolute download URL for a repository-relative file path.

        Parameters:
            file_path (str): Relative repository path (must not be an absolute path or contain a URL scheme/host).

        Returns:
            str: Absolute download URL for the specified repository file.

        Raises:
            ValueError: If `file_path` contains a URL scheme, host, or is an absolute path.
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

    def cleanup_old_versions(
        self,
        _keep_limit: int,
        cached_releases: Optional[List[Release]] = None,
        keep_last_beta: bool = False,
    ) -> None:
        """
        Clear the repository downloads directory, ignoring any retention limit.

        The _keep_limit parameter is ignored because repository files are not versioned; this method removes all files under the repository downloads area.

        Parameters:
            _keep_limit (int): Suggested number of versions to keep; ignored for repository downloads.
            cached_releases (Optional[List[Release]]): Unused for repository downloads; retained for signature compatibility.
            keep_last_beta (bool): Unused for repository downloads; retained for signature compatibility.
        """
        del (
            cached_releases,
            keep_last_beta,
        )  # intentionally unused (signature compatibility)
        # Repository files are stored in a flat structure, so we clean the entire directory
        self.clean_repository_directory()

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Provide the fixed tag that identifies the latest repository release.

        Returns:
            The string "repository-latest".
        """
        return "repository-latest"

    def update_latest_release_tag(self, _release_tag: str) -> bool:
        """
        No-op updater for the repository's latest release tag.

        This method ignores the provided release tag because repository downloads are not versioned and always succeeds.

        Parameters:
            _release_tag (str): Release tag value (ignored).

        Returns:
            bool: `True` always, indicating success.
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
        _file_path: str,
        _extract_dir: str,
        _patterns: List[str],
        _exclude_patterns: List[str],
    ) -> bool:
        """
        Indicates whether the given repository file requires extraction.

        Repository assets are not treated as archives, so extraction is not applicable.

        Returns:
            `False` indicating extraction is not required for repository files.
        """
        # Repository files are typically not archives, so extraction is never needed
        logger.debug(
            "Extraction need check called for repository file - not applicable"
        )
        return False

    def should_download_release(self, _release_tag: str, _asset_name: str) -> bool:
        """
        Indicates whether a repository release asset should be downloaded.

        Repository assets are always selected for download.

        Returns:
            True for all repository assets, False otherwise.
        """
        # Repository downloads don't use pattern filtering in the same way
        return True
