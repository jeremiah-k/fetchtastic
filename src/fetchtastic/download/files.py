"""
File Operations for Fetchtastic Download Subsystem

This module provides file operations utilities including atomic writes,
hash verification, and archive extraction.
"""

import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fetchtastic.constants import (
    EXECUTABLE_PERMISSIONS,
    FIRMWARE_DIR_PREFIX,
    SHELL_SCRIPT_EXTENSION,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    get_hash_file_path,
    matches_selected_patterns,
    verify_file_integrity,
)

NON_ASCII_RX = re.compile(r"[^\x00-\x7F]+")


def strip_unwanted_chars(text: str) -> str:
    """
    Remove non-ASCII characters from a string.

    Parameters:
        text (str): The input string to sanitize.

    Returns:
        str: The input string with all non-ASCII characters removed.
    """
    return NON_ASCII_RX.sub("", text)


def _matches_exclude(name: str, patterns: List[str]) -> bool:
    """
    Shared case-insensitive glob exclude matcher.

    Parameters:
        name (str): The name to test (typically a filename or path component).
        patterns (List[str]): Glob patterns to test against; matching is performed case-insensitively.

    Returns:
        bool: `True` if `name` matches at least one pattern, `False` otherwise.
    """
    if not patterns:
        return False
    name_l = name.lower()
    return any(fnmatch.fnmatch(name_l, p.lower()) for p in patterns)


def _sanitize_path_component(component: Optional[str]) -> Optional[str]:
    """
    Validate and sanitize a single filesystem path component.

    Trims surrounding whitespace and returns the cleaned component if it is a safe, relative path segment. Returns None when the input is None or when the component is unsafe — specifically if it is empty after trimming, equals "." or "..", is an absolute path, contains a null byte, or contains path separator characters.

    Parameters:
        component (Optional[str]): The candidate path component to validate and sanitize.

    Returns:
        Optional[str]: The trimmed, safe component string, or `None` if the component is unsafe or `None`.
    """
    if component is None:
        return None

    sanitized = component.strip()
    if not sanitized or sanitized in {".", ".."}:
        return None

    if os.path.isabs(sanitized):
        return None

    if "\x00" in sanitized:
        return None

    for separator in (os.sep, os.altsep):
        if separator and separator in sanitized:
            return None

    return sanitized


def _get_existing_prerelease_dirs(prerelease_dir: str) -> list[str]:
    """
    Return a list of safe prerelease subdirectory names found in the given directory.

    Scans the provided prerelease directory for immediate subdirectories whose names start with the firmware prefix, validates and sanitizes each name, and collects the safe names. Unsafe names are skipped and a warning is logged; filesystem errors while scanning are logged at debug level.

    Parameters:
        prerelease_dir (str): Path to the prerelease directory to scan.

    Returns:
        list[str]: Sanitized subdirectory names that start with the firmware prefix; empty if the directory does not exist or on scan errors.
    """
    if not os.path.exists(prerelease_dir):
        return []

    entries: list[str] = []
    try:
        with os.scandir(prerelease_dir) as iterator:
            for entry in iterator:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if not entry.name.startswith(FIRMWARE_DIR_PREFIX):
                    continue
                safe_name = _sanitize_path_component(entry.name)
                if safe_name is None:
                    logger.warning(
                        "Ignoring unsafe prerelease directory name: %s", entry.name
                    )
                    continue
                entries.append(safe_name)
    except OSError as e:
        logger.debug("Error scanning prerelease dir %s: %s", prerelease_dir, e)

    return entries


def _find_asset_by_name(
    release_data: Dict[str, Any], asset_name: str
) -> Optional[Dict[str, Any]]:
    """Find an asset dict by name in release data."""
    for asset in release_data.get("assets", []) or []:
        if asset.get("name") == asset_name:
            return asset
    return None


def _is_release_complete(
    release_data: Dict[str, Any],
    release_dir: str,
    selected_patterns: Optional[List[str]],
    exclude_patterns: List[str],
) -> bool:
    """
    Check that a release directory contains every expected asset (filtered by inclusion/exclusion patterns) and that each asset appears intact.

    Parameters:
        release_data (Dict[str, Any]): Release metadata with an "assets" list; each asset object should include at least a "name" and may include a "size".
        release_dir (str): Path to the directory holding downloaded release assets.
        selected_patterns (Optional[List[str]]): Optional glob-style inclusion patterns; when provided, only assets matching these patterns are considered.
        exclude_patterns (List[str]): Glob-style exclusion patterns; assets matching any of these are ignored.

    Returns:
        bool: `True` if every expected asset (after applying inclusion/exclusion patterns) exists in release_dir and passes integrity checks (ZIP files are not corrupted and file sizes match any declared sizes), `False` otherwise.
    """
    if not os.path.exists(release_dir):
        return False

    expected_assets: list[str] = []
    for asset in release_data.get("assets", []) or []:
        file_name = asset.get("name", "")
        if not file_name:
            continue

        if selected_patterns and not matches_selected_patterns(
            file_name, selected_patterns
        ):
            continue

        if _matches_exclude(file_name, exclude_patterns):
            continue

        expected_assets.append(file_name)

    if not expected_assets:
        logger.debug("No assets match selected patterns for release in %s", release_dir)
        return False

    for asset_name in expected_assets:
        asset_path = os.path.join(release_dir, asset_name)
        if not os.path.exists(asset_path):
            logger.debug(
                "Missing asset %s in release directory %s", asset_name, release_dir
            )
            return False

        if asset_name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(asset_path, "r") as zf:
                    if zf.testzip() is not None:
                        logger.debug("Corrupted zip file detected: %s", asset_path)
                        return False
                actual_size = os.path.getsize(asset_path)
                asset_data = _find_asset_by_name(release_data, asset_name)
                if asset_data:
                    expected_size = asset_data.get("size")
                    if expected_size is not None and actual_size != expected_size:
                        logger.debug(
                            "File size mismatch for %s: expected %s, got %s",
                            asset_path,
                            expected_size,
                            actual_size,
                        )
                        return False
            except (zipfile.BadZipFile, OSError, IOError, TypeError):
                return False
        else:
            try:
                actual_size = os.path.getsize(asset_path)
                asset_data = _find_asset_by_name(release_data, asset_name)
                if asset_data:
                    expected_size = asset_data.get("size")
                    if expected_size is not None and actual_size != expected_size:
                        logger.debug(
                            "File size mismatch for %s: expected %s, got %s",
                            asset_path,
                            expected_size,
                            actual_size,
                        )
                        return False
            except (OSError, TypeError):
                return False

    return True


def _prepare_for_redownload(file_path: str) -> bool:
    """
    Prepare a file path for re-download by removing the target file, its hash sidecar, and any orphaned temporary files.

    Parameters:
        file_path (str): Path to the file to remove and clean up related sidecar and temporary files.

    Returns:
        bool: `True` if cleanup completed successfully, `False` if an error occurred.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug("Removed existing file: %s", file_path)

        hash_path = get_hash_file_path(file_path)
        if os.path.exists(hash_path):
            os.remove(hash_path)
            logger.debug("Removed stale hash file: %s", hash_path)

        for tmp_path in glob.glob(f"{glob.escape(file_path)}.tmp.*"):
            os.remove(tmp_path)
            logger.debug("Removed orphaned temp file: %s", tmp_path)
    except OSError as e:
        logger.error("Error preparing for re-download of %s: %s", file_path, e)
        return False
    else:
        return True


def _prerelease_needs_download(file_path: str) -> bool:
    """
    Decides whether a prerelease file should be downloaded.

    Reports that a download is needed when the target file does not exist or when an integrity check fails and preparation for re-download (cleanup) succeeds.

    Returns:
        bool: `True` if the file is missing or failed integrity check and was prepared for re-download, `False` otherwise.
    """
    if not os.path.exists(file_path):
        return True

    if verify_file_integrity(file_path):
        return False

    logger.warning(
        "Existing prerelease file %s failed integrity check; re-downloading",
        os.path.basename(file_path),
    )
    if not _prepare_for_redownload(file_path):
        return False
    return True


def _is_within_base(real_base_dir: str, candidate: str) -> bool:
    """
    Determine whether the candidate path resides within the given base directory.

    Returns:
        True if the candidate path is inside `real_base_dir`, False otherwise.
    """
    try:
        return os.path.commonpath([real_base_dir, candidate]) == real_base_dir
    except ValueError:
        return False


def _safe_rmtree(path_to_remove: str, base_dir: str, item_name: str) -> bool:
    """
    Safely remove a file, directory, or symlink while preventing removal outside a specified base directory.

    If `path_to_remove` is a symlink, only the symlink is removed after verifying the symlink's directory resolves within `base_dir`. For non-symlinks the target's realpath is checked to ensure it is inside `base_dir` before removing a file or recursively removing a directory. `item_name` is used for logging messages. The function logs and returns False on safety checks failure or on OS errors; returns True on successful removal.
    Parameters:
        path_to_remove (str): Filesystem path to remove (file, directory, or symlink).
        base_dir (str): Base directory that removals must be contained within.
        item_name (str): Human-readable name of the item for logging.

    Returns:
        bool: `True` if the item was successfully removed, `False` if removal was skipped for safety or an error occurred.
    """
    try:
        real_base_dir = os.path.realpath(base_dir)

        if os.path.islink(path_to_remove):
            link_dir = os.path.dirname(os.path.abspath(path_to_remove))
            real_link_dir = os.path.realpath(link_dir)

            if not _is_within_base(real_base_dir, real_link_dir):
                logger.warning(
                    "Skipping removal of symlink %s because its location is outside the base directory",
                    path_to_remove,
                )
                return False

            logger.info("Removing symlink: %s", item_name)
            os.unlink(path_to_remove)
            return True

        real_target = os.path.realpath(path_to_remove)
        if not _is_within_base(real_base_dir, real_target):
            logger.warning(
                "Skipping removal of %s because it resolves outside the base directory",
                path_to_remove,
            )
            return False

        if os.path.isdir(path_to_remove):
            shutil.rmtree(path_to_remove)
        else:
            os.remove(path_to_remove)
    except OSError as e:
        logger.error("Error removing %s: %s", path_to_remove, e)
        return False
    else:
        return True


def _atomic_write(
    file_path: str, writer_func: Callable[[Any], None], suffix: str = ".tmp"
) -> bool:
    """
    Write data to a file atomically by writing to a temporary file and atomically replacing the target on success.

    Parameters:
        file_path (str): Destination file path to be written.
        writer_func (Callable[[Any], None]): Callable that receives an open text file-like object and writes the desired content to it.
        suffix (str): Suffix to use for the temporary file name (default ".tmp").

    Returns:
        bool: `True` if the temporary write and atomic replace succeeded, `False` on any error.
    """
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(file_path), prefix="tmp-", suffix=suffix
        )
    except OSError as e:
        logger.error(f"Could not create temporary file for {file_path}: {e}")
        return False

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
            writer_func(temp_f)
        os.replace(temp_path, file_path)
    except (IOError, UnicodeEncodeError, OSError) as e:
        logger.error(f"Could not write to {file_path}: {e}")
        return False
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return True


def _atomic_write_json(file_path: str, data: dict) -> bool:
    """
    Atomically write the given dictionary to the target file as pretty-printed JSON.

    Parameters:
        file_path (str): Destination filesystem path for the JSON file.
        data (dict): JSON-serializable mapping to write to disk.

    Returns:
        bool: `True` if the file was written and moved into place successfully, `False` on error.
    """
    return _atomic_write(
        file_path, lambda f: json.dump(data, f, indent=2), suffix=".json"
    )


class FileOperations:
    """
    Provides file operations utilities for the download subsystem.

    Includes methods for:
    - Atomic file writes
    - File hash verification
    - Archive extraction
    - File cleanup
    - Path validation
    - Extraction pattern validation
    - Extraction need checking
    - Hash generation for extracted files
    """

    def atomic_write(self, file_path: str, content: str) -> bool:
        """
        Atomically write text content to the given file path.

        Parameters:
            file_path (str): Destination filesystem path.
            content (str): Text to write to the file.

        Returns:
            bool: `True` if the write and atomic replacement succeeded, `False` on error.
        """

        def _write_content(f):
            """
            Write the provided string content to the given writable file-like object.

            Parameters:
                f (typing.IO): Writable file-like object to receive the content.
            """
            f.write(content)

        return _atomic_write(file_path, _write_content, suffix=".txt")

    def verify_file_hash(
        self, file_path: str, expected_hash: Optional[str] = None
    ) -> bool:
        """
        Determine whether a file exists and, if provided, whether its SHA-256 hash matches the expected value.

        Parameters:
            file_path (str): Path to the file to verify.
            expected_hash (Optional[str]): Expected SHA-256 hex digest; when omitted, only existence is checked.

        Returns:
            bool: `True` if the file exists and (when `expected_hash` is provided) its SHA-256 hex digest equals `expected_hash`; `False` otherwise.
        """
        if not os.path.exists(file_path):
            logger.warning(f"File does not exist for verification: {file_path}")
            return False

        if expected_hash is None:
            # If no expected hash, just verify file exists
            return True

        actual_hash = self._get_file_hash(file_path)
        if actual_hash is None:
            return False
        return actual_hash == expected_hash

    def extract_archive(
        self,
        zip_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> List[Path]:
        """
        Extract files from a ZIP archive whose basenames match the given inclusion patterns and do not match the exclusion patterns into the target directory.

        Parameters:
            zip_path (str): Path to the ZIP archive.
            extract_dir (str): Destination directory for extracted files.
            patterns (List[str]): Filename glob patterns to include; an empty list results in no extraction (legacy behavior).
            exclude_patterns (List[str]): Filename glob patterns to exclude (case-insensitive).

        Returns:
            List[Path]: Paths of files that were successfully extracted; returns an empty list if no files were extracted or on error.
        """
        if not patterns:
            # Legacy behavior: empty pattern list means do not extract anything
            return []

        extracted_files = []

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for file_info in zip_ref.infolist():
                    # Skip directory entries
                    if file_info.is_dir():
                        continue

                    file_name = file_info.filename
                    if not self._is_safe_archive_member(file_name):
                        logger.warning(
                            "Skipping unsafe archive member %s (possible traversal)",
                            file_name,
                        )
                        continue

                    base_name = os.path.basename(file_name)
                    if self._matches_exclude(base_name, exclude_patterns):
                        continue

                    # Check if file matches any pattern
                    if matches_selected_patterns(base_name, patterns):
                        # Extract the file with safe path resolution
                        try:
                            extract_path = safe_extract_path(extract_dir, file_name)
                        except ValueError as e:
                            logger.warning(f"Skipping unsafe extraction path: {e}")
                            continue

                        # Ensure parent directory exists
                        os.makedirs(os.path.dirname(extract_path), exist_ok=True)

                        with (
                            zip_ref.open(file_info) as source,
                            open(extract_path, "wb") as target,
                        ):
                            shutil.copyfileobj(source, target)

                        if os.name != "nt" and base_name.lower().endswith(
                            SHELL_SCRIPT_EXTENSION
                        ):
                            try:
                                os.chmod(extract_path, EXECUTABLE_PERMISSIONS)
                            except OSError:
                                pass

                        extracted_files.append(Path(extract_path))
                        logger.debug(f"Extracted {file_name} to {extract_path}")

            return extracted_files

        except (zipfile.BadZipFile, IOError, OSError) as e:
            logger.error(f"Error extracting archive {zip_path}: {e}")
            return []

    def _matches_exclude(self, filename: str, patterns: List[str]) -> bool:
        """
        Instance wrapper around module-level exclude matcher.

        Parameters:
            filename (str): The name to test; comparison is performed against the pattern(s).
            patterns (List[str]): Iterable of glob-style patterns; matching is case-insensitive.

        Returns:
            bool: `True` if `filename` matches any pattern in `patterns`, `False` otherwise.
        """
        return _matches_exclude(filename, patterns)

    def _is_safe_archive_member(self, member_name: str) -> bool:
        """
        Determine whether an archive member name is safe to extract.

        Returns:
            `true` if the member name contains no absolute paths, parent-directory references, or null bytes, `false` otherwise.
        """
        if (
            not member_name
            or member_name.startswith("/")
            or member_name.startswith("\\")
        ):
            return False
        normalized = os.path.normpath(member_name)
        # Reject absolute paths (including Windows drive-letter paths)
        if os.path.isabs(normalized):
            return False
        if normalized == "..":
            return False
        if normalized.startswith(f"..{os.sep}"):
            return False
        if os.altsep and normalized.startswith(f"..{os.altsep}"):
            return False
        # Explicit null byte check
        if "\x00" in normalized:
            return False
        return True

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate inclusion and exclusion glob patterns for safe archive extraction.

        Parameters:
            patterns (List[str]): Glob patterns of files to include during extraction.
            exclude_patterns (List[str]): Glob patterns of files to exclude during extraction.

        Returns:
            bool: True if all provided patterns are well-formed and do not pose path-traversal or overly-broad wildcard risks, False otherwise.
        """
        try:
            # Check for empty patterns that might cause issues
            for pattern in patterns + exclude_patterns:
                if not pattern or not pattern.strip():
                    logger.warning("Empty extraction pattern detected")
                    return False

                # Check for patterns that might cause path traversal
                if any(sep in pattern for sep in [os.sep, os.altsep or "\\"]):
                    logger.warning(f"Potential path traversal in pattern: {pattern}")
                    return False

                # Check for patterns with dangerous wildcards
                # Limits increased to allow more specific patterns while preventing overly broad matches
                if pattern.count("*") > 5 or pattern.count("?") > 10:
                    logger.warning(f"Overly broad pattern detected: {pattern}")
                    return False

                # Test the pattern to ensure it compiles correctly
                try:
                    # This will raise an exception if the pattern is invalid
                    re.compile(fnmatch.translate(pattern))
                except re.error as e:
                    logger.warning(f"Invalid pattern {pattern}: {e}")
                    return False

            return True

        except (TypeError, AttributeError) as e:
            logger.error(f"Error validating extraction patterns: {e}")
            return False

    def check_extraction_needed(
        self,
        zip_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Determine whether the ZIP archive requires extraction by comparing candidate members against existing files in extract_dir.

        This checks archive members that match the given filename patterns (applied to the base filename) and are not excluded, verifies each candidate would be safely extracted, and compares existing extracted file sizes to the archive entry sizes. If all matching candidates already exist with matching sizes, extraction is not needed.

        Parameters:
            zip_path (str): Path to the ZIP archive.
            extract_dir (str): Target directory where files would be extracted.
            patterns (List[str]): Filename patterns to select candidates (matched against the base filename).
            exclude_patterns (List[str]): Filename patterns to exclude (matched against the base filename).

        Returns:
            bool: `True` if extraction should be performed, `False` if extraction can be skipped. If the ZIP file is missing, returns `False`. On any error while checking, returns `True` (assumes extraction is needed).
        """
        try:
            # If the ZIP file doesn't exist, extraction is not needed
            if not os.path.exists(zip_path):
                logger.debug(f"ZIP file not found: {zip_path}")
                return False

            # If no patterns are specified, no extraction is needed
            if not patterns:
                logger.debug("No extraction patterns specified")
                return False

            # Check if all files that would be extracted already exist
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                files_to_extract = 0
                files_existing = 0

                for file_info in zip_ref.infolist():
                    if file_info.is_dir():
                        continue

                    file_name = file_info.filename
                    if not self._is_safe_archive_member(file_name):
                        continue

                    base_name = os.path.basename(file_name)
                    if self._matches_exclude(base_name, exclude_patterns):
                        continue

                    if matches_selected_patterns(base_name, patterns):
                        files_to_extract += 1
                        try:
                            extract_path = safe_extract_path(extract_dir, file_name)
                        except ValueError:
                            # Skip unsafe paths for extraction check
                            continue
                        if os.path.exists(extract_path):
                            # Check if file size matches
                            if os.path.getsize(extract_path) == file_info.file_size:
                                files_existing += 1

                # If all files that would be extracted already exist with correct sizes,
                # extraction is not needed
                if files_to_extract > 0 and files_existing == files_to_extract:
                    logger.debug(
                        f"All {files_to_extract} files already extracted - skipping extraction"
                    )
                    return False

                return True

        except (zipfile.BadZipFile, IOError, OSError) as e:
            logger.error(f"Error checking extraction need for {zip_path}: {e}")
            return True  # If we can't check, assume extraction is needed

    def extract_with_validation(
        self,
        zip_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> List[Path]:
        """
        Extract files from a ZIP archive that match the provided include/exclude patterns after validating patterns and confirming extraction is necessary.

        Parameters:
            zip_path (str): Path to the ZIP archive to extract from.
            extract_dir (str): Destination directory for extracted files.
            patterns (List[str]): Glob patterns selecting which file basenames to include.
            exclude_patterns (List[str]): Glob patterns selecting which file basenames to exclude.

        Returns:
            List[Path]: Paths of files that were actually extracted; empty list if extraction was skipped or failed.
        """
        # Validate patterns first
        if not self.validate_extraction_patterns(patterns, exclude_patterns):
            logger.error("Extraction aborted due to invalid patterns")
            return []

        # Check if extraction is actually needed
        if not self.check_extraction_needed(
            zip_path, extract_dir, patterns, exclude_patterns
        ):
            return []

        # Perform the actual extraction
        extracted = self.extract_archive(
            zip_path, extract_dir, patterns, exclude_patterns
        )

        # Generate sidecar hashes for extracted files
        if extracted:
            self.generate_hash_for_extracted_files(extracted)

        return extracted

    def generate_hash_for_extracted_files(
        self, extracted_files: List[Path], algorithm: str = "sha256"
    ) -> Dict[str, str]:
        """
        Generate a hash sidecar file for each existing path in extracted_files using the specified algorithm.

        Creates a file named "<original_path>.<algorithm>" containing the file's hexadecimal digest. If the provided algorithm is unsupported, defaults to "sha256".

        Parameters:
            extracted_files (List[Path]): Files to hash; only existing files are processed.
            algorithm (str): Hash algorithm to use (e.g., "sha256", "md5"); case-insensitive. Defaults to "sha256".

        Returns:
            Dict[str, str]: Mapping from each processed file's path string to its hex digest.
        """
        hash_dict = {}

        try:
            # Validate algorithm is available
            try:

                def hash_func():
                    """
                    Create and return a new hash object for the configured algorithm.

                    Returns:
                        A new hash object suitable for incremental updates and digest computation using the selected algorithm.
                    """
                    return hashlib.new(algorithm.lower())

                hash_func()  # Test that algorithm is valid
            except ValueError:
                logger.warning(
                    f"Unsupported hash algorithm: {algorithm}, using SHA-256"
                )
                algorithm = "sha256"
                hash_func = hashlib.sha256

            for file_path in extracted_files:
                if os.path.exists(file_path):
                    try:
                        # Calculate hash
                        file_hash = hash_func()
                        with open(file_path, "rb") as f:
                            for byte_block in iter(lambda: f.read(4096), b""):
                                file_hash.update(byte_block)

                        hash_value = file_hash.hexdigest()
                        hash_dict[str(file_path)] = hash_value

                        # Create sidecar file
                        hash_file_path = f"{file_path}.{algorithm}"
                        if _atomic_write(
                            hash_file_path,
                            lambda f, hv=hash_value: f.write(str(hv)),  # type: ignore[misc]
                            suffix=f".{algorithm}",
                        ):
                            logger.debug("Created hash file: %s", hash_file_path)
                        else:
                            logger.warning(
                                "Failed to write hash sidecar for %s", file_path
                            )

                    except IOError as e:
                        logger.error(f"Error generating hash for {file_path}: {e}")

            return hash_dict

        except (IOError, OSError) as e:
            logger.error("Error generating hashes for extracted files: %s", e)
            return {}

    def cleanup_file(self, file_path: str) -> bool:
        """
        Remove a file and its temporary sidecar files.

        Removes the file at `file_path`, its exact `.tmp` sibling, and any files matching the `<file_path>.tmp.*` pattern.

        Parameters:
            file_path (str): Path to the target file to remove.

        Returns:
            bool: `True` if all removal operations completed without error, `False` otherwise.
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)

            # Remove exact .tmp file
            tmp_exact = f"{file_path}.tmp"
            if os.path.exists(tmp_exact):
                os.remove(tmp_exact)
            # Remove .tmp.* pattern files using glob
            for tmp_pattern_file in glob.glob(f"{glob.escape(file_path)}.tmp.*"):
                os.remove(tmp_pattern_file)

            return True
        except OSError as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")
            return False

    def ensure_directory_exists(self, directory: str) -> bool:
        """
        Ensure a directory path exists by creating any missing parent directories.

        Returns:
            bool: `True` if the directory exists or was created successfully, `False` otherwise.
        """
        try:
            os.makedirs(directory, exist_ok=True)
            return True
        except OSError as e:
            logger.error(f"Could not create directory {directory}: {e}")
            return False

    def get_file_size(self, file_path: str) -> Optional[int]:
        """
        Retrieve the size of a file in bytes.

        Returns:
            The file size in bytes, or None if the file does not exist or cannot be accessed.
        """
        try:
            return os.path.getsize(file_path)
        except (OSError, FileNotFoundError):
            return None

    def compare_file_hashes(self, file1: str, file2: str) -> bool:
        """
        Determine whether two files have identical SHA-256 hashes.

        Parameters:
            file1 (str): Path to the first file.
            file2 (str): Path to the second file.

        Returns:
            `true` if both files exist and their SHA-256 hex digests are identical, `false` otherwise.
        """
        hash1 = self._get_file_hash(file1)
        hash2 = self._get_file_hash(file2)

        if hash1 is None or hash2 is None:
            return False

        return hash1 == hash2

    def _get_file_hash(self, file_path: str) -> Optional[str]:
        """
        Compute the SHA-256 hash of a file.

        Parameters:
            file_path (str): Path to the file to hash.

        Returns:
            Optional[str]: SHA-256 hex digest string if the file exists and is readable, `None` if the file does not exist or cannot be read.
        """
        if not os.path.exists(file_path):
            return None

        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except IOError:
            return None


def safe_extract_path(extract_dir: str, file_path: str) -> str:
    """
    Resolve a safe absolute extraction path and prevent directory traversal.

    Ensures the absolute path for file_path, when joined to extract_dir, resides within extract_dir.

    Parameters:
        extract_dir (str): Base directory intended for extraction.
        file_path (str): Member path from the archive to be extracted.

    Returns:
        str: Absolute, normalized path inside extract_dir suitable for extraction.

    Raises:
        ValueError: If the resolved path is outside extract_dir.
    """
    real_extract_dir = os.path.realpath(extract_dir)
    prospective_path = os.path.join(real_extract_dir, file_path)
    normalized_path = os.path.realpath(prospective_path)

    if not _is_within_base(real_extract_dir, normalized_path):
        raise ValueError(
            f"Unsafe extraction path '{file_path}' is outside base '{extract_dir}'"
        )

    return normalized_path
