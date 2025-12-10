"""
File Operations for Fetchtastic Download Subsystem

This module provides file operations utilities including atomic writes,
hash verification, and archive extraction.
"""

import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

from fetchtastic.log_utils import logger


class FileOperations:
    """
    Provides file operations utilities for the download subsystem.

    Includes methods for:
    - Atomic file writes
    - File hash verification
    - Archive extraction
    - File cleanup
    - Path validation
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
        self, zip_path: str, extract_dir: str, patterns: List[str]
    ) -> List[Path]:
        """
        Extract files from a ZIP archive matching specific patterns.

        Args:
            zip_path: Path to the ZIP archive
            extract_dir: Directory to extract files to
            patterns: List of filename patterns to extract (empty list extracts all)

        Returns:
            List[Path]: List of paths to extracted files
        """
        if not patterns:
            # Extract all files if no patterns specified
            patterns = ["*"]

        extracted_files = []

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for file_info in zip_ref.infolist():
                    # Skip directory entries
                    if file_info.is_dir():
                        continue

                    file_name = file_info.filename
                    base_name = os.path.basename(file_name)

                    # Check if file matches any pattern
                    if self._matches_pattern(base_name, patterns):
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

    def _matches_pattern(self, filename: str, patterns: List[str]) -> bool:
        """
        Check if a filename matches any of the given patterns.

        Args:
            filename: The filename to check
            patterns: List of patterns to match against

        Returns:
            bool: True if filename matches any pattern, False otherwise
        """
        filename_lower = filename.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if pattern_lower == "*":
                return True
            if pattern_lower in filename_lower:
                return True
            # Simple glob-style matching
            if pattern_lower.endswith("*") and filename_lower.startswith(
                pattern_lower[:-1]
            ):
                return True
            if pattern_lower.startswith("*") and filename_lower.endswith(
                pattern_lower[1:]
            ):
                return True
        return False

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
