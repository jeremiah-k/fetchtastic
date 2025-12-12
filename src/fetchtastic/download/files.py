"""
File Operations for Fetchtastic Download Subsystem

This module provides file operations utilities including atomic writes,
hash verification, and archive extraction.
"""

import fnmatch
import hashlib
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from fetchtastic.log_utils import logger
from fetchtastic.utils import matches_selected_patterns


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
        Atomically write text content to a file.

        Args:
            file_path: Destination path for the file
            content: Text content to write

        Returns:
            bool: True on successful write, False on error
        """
        try:
            # Write to temporary file first
            temp_path = f"{file_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Atomic rename to final destination
            os.replace(temp_path, file_path)
            return True
        except (IOError, OSError) as e:
            logger.error(f"Could not write to {file_path}: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

    def verify_file_hash(
        self, file_path: str, expected_hash: Optional[str] = None
    ) -> bool:
        """
        Verify the SHA-256 hash of a file.

        Args:
            file_path: Path to the file to verify
            expected_hash: Optional expected SHA-256 hash

        Returns:
            bool: True if file exists and hash matches (or no expected hash provided),
                 False otherwise
        """
        if not os.path.exists(file_path):
            logger.warning(f"File does not exist for verification: {file_path}")
            return False

        if expected_hash is None:
            # If no expected hash, just verify file exists
            return True

        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                # Read and update hash in chunks of 4K
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)

            actual_hash = sha256_hash.hexdigest()
            return actual_hash == expected_hash
        except IOError as e:
            logger.error(f"Error reading file {file_path} for hash verification: {e}")
            return False

    def extract_archive(
        self,
        zip_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> List[Path]:
        """
        Extract files from a ZIP archive matching specific patterns.

        Args:
            zip_path: Path to the ZIP archive
            extract_dir: Directory to extract files to
            patterns: List of filename patterns to extract (empty list extracts all)
            exclude_patterns: List of filename patterns to skip (case-insensitive glob)

        Returns:
            List[Path]: List of paths to extracted files
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
                        # Extract the file
                        extract_path = os.path.join(extract_dir, file_name)

                        # Ensure parent directory exists
                        os.makedirs(os.path.dirname(extract_path), exist_ok=True)

                        with zip_ref.open(file_info) as source, open(
                            extract_path, "wb"
                        ) as target:
                            shutil.copyfileobj(source, target)

                        extracted_files.append(Path(extract_path))
                        logger.debug(f"Extracted {file_name} to {extract_path}")

            return extracted_files

        except (zipfile.BadZipFile, IOError, OSError) as e:
            logger.error(f"Error extracting archive {zip_path}: {e}")
            return []

    def _matches_exclude(self, filename: str, patterns: List[str]) -> bool:
        """Case-insensitive glob match for exclusion."""
        if not patterns:
            return False
        name_lower = filename.lower()
        return any(fnmatch.fnmatch(name_lower, pat.lower()) for pat in patterns)

    def _is_safe_archive_member(self, member_name: str) -> bool:
        """
        Validate archive member names to prevent path traversal during extraction.
        """
        if (
            not member_name
            or member_name.startswith("/")
            or member_name.startswith("\\")
        ):
            return False
        normalized = os.path.normpath(member_name)
        if normalized.startswith("..") or normalized.startswith(f"..{os.sep}"):
            return False
        # Explicit null byte check
        if "\x00" in normalized:
            return False
        return True

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate extraction patterns to ensure they are safe and well-formed.

        This method implements the equivalent of the legacy _validate_extraction_patterns
        functionality to ensure pattern safety and prevent potential security issues.

        Args:
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if patterns are valid, False otherwise
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
                if pattern.count("*") > 3 or pattern.count("?") > 5:
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

        except Exception as e:
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
        Check if extraction is needed by examining existing files.

        This method implements the equivalent of the legacy check_extraction_needed
        functionality to avoid unnecessary extraction operations.

        Args:
            zip_path: Path to the ZIP archive
            extract_dir: Directory where files would be extracted
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if extraction is needed, False if files already exist
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
                        extract_path = os.path.join(extract_dir, file_name)
                        if os.path.exists(extract_path):
                            # Check if file size matches
                            if os.path.getsize(extract_path) == file_info.file_size:
                                files_existing += 1

                # If all files that would be extracted already exist with correct sizes,
                # extraction is not needed
                if files_to_extract > 0 and files_existing == files_to_extract:
                    logger.info(
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
        Extract files from archive with comprehensive validation and safety checks.

        This method combines pattern validation, extraction need checking, and
        the actual extraction process with enhanced safety features.

        Args:
            zip_path: Path to the ZIP archive
            extract_dir: Directory to extract files to
            patterns: List of filename patterns to extract
            exclude_patterns: List of filename patterns to skip

        Returns:
            List[Path]: List of paths to extracted files
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
        Generate hash files for extracted files (sidecar files).

        This implements the legacy hash/sidecar behavior for extracted files.

        Args:
            extracted_files: List of paths to extracted files
            algorithm: Hash algorithm to use (sha256, md5, etc.)

        Returns:
            Dict[str, str]: Dictionary mapping file paths to their hashes
        """
        hash_dict = {}

        try:
            if algorithm.lower() == "sha256":
                hash_func = hashlib.sha256
            elif algorithm.lower() == "md5":
                hash_func = hashlib.md5
            else:
                logger.warning(
                    f"Unsupported hash algorithm: {algorithm}, using SHA-256"
                )
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
                        with open(hash_file_path, "w", encoding="utf-8") as hash_file:
                            hash_file.write(hash_value)

                        logger.debug(f"Created hash file: {hash_file_path}")

                    except IOError as e:
                        logger.error(f"Error generating hash for {file_path}: {e}")

            return hash_dict

        except Exception as e:
            logger.error(f"Error generating hashes for extracted files: {e}")
            return {}

    def cleanup_file(self, file_path: str) -> bool:
        """
        Clean up a file and any associated temporary files.

        Args:
            file_path: Path to the file to clean up

        Returns:
            bool: True if cleanup succeeded, False otherwise
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)

            # Clean up any temporary files
            temp_files = [f"{file_path}.tmp", f"{file_path}.tmp.*"]
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

            return True
        except OSError as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")
            return False

    def ensure_directory_exists(self, directory: str) -> bool:
        """
        Ensure a directory exists, creating it if necessary.

        Args:
            directory: Path to the directory

        Returns:
            bool: True if directory exists or was created, False otherwise
        """
        try:
            os.makedirs(directory, exist_ok=True)
            return True
        except OSError as e:
            logger.error(f"Could not create directory {directory}: {e}")
            return False

    def get_file_size(self, file_path: str) -> Optional[int]:
        """
        Get the size of a file in bytes.

        Args:
            file_path: Path to the file

        Returns:
            Optional[int]: File size in bytes, or None if file doesn't exist
        """
        try:
            return os.path.getsize(file_path)
        except (OSError, FileNotFoundError):
            return None

    def compare_file_hashes(self, file1: str, file2: str) -> bool:
        """
        Compare the SHA-256 hashes of two files.

        Args:
            file1: Path to the first file
            file2: Path to the second file

        Returns:
            bool: True if both files exist and have identical hashes, False otherwise
        """
        hash1 = self._get_file_hash(file1)
        hash2 = self._get_file_hash(file2)

        if hash1 is None or hash2 is None:
            return False

        return hash1 == hash2

    def _get_file_hash(self, file_path: str) -> Optional[str]:
        """
        Get the SHA-256 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            Optional[str]: SHA-256 hash as hex string, or None if file doesn't exist
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
