# src/fetchtastic/utils.py
import gc  # For Windows file operation retries
import hashlib
import os
import platform
import time
import zipfile
from typing import Optional  # Callable removed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry  # type: ignore

from fetchtastic.log_utils import logger  # Import the new logger

# Constants for download_file_with_retry
DEFAULT_CONNECT_RETRIES: int = 3
DEFAULT_BACKOFF_FACTOR: float = 1.0  # Make it float for potential fractional backoff
DEFAULT_REQUEST_TIMEOUT: int = 30  # seconds
DEFAULT_CHUNK_SIZE: int = 8 * 1024  # 8KB
WINDOWS_MAX_REPLACE_RETRIES: int = 3
WINDOWS_INITIAL_RETRY_DELAY: float = 1.0  # seconds


def calculate_sha256(file_path: str) -> Optional[str]:
    """Calculate SHA-256 hash of a file."""
    try:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except (IOError, OSError) as e:
        logger.debug(f"Error calculating SHA-256 for {file_path}: {e}")
        return None


def get_hash_file_path(file_path: str) -> str:
    """Get the path for storing the hash file."""
    return file_path + ".sha256"


def save_file_hash(file_path: str, hash_value: str) -> None:
    """Save hash to a .sha256 file."""
    hash_file = get_hash_file_path(file_path)
    try:
        with open(hash_file, "w") as f:
            f.write(f"{hash_value}  {os.path.basename(file_path)}\n")
        logger.debug(f"Saved hash for {os.path.basename(file_path)}")
    except (IOError, OSError) as e:
        logger.debug(f"Error saving hash file {hash_file}: {e}")


def load_file_hash(file_path: str) -> Optional[str]:
    """Load hash from a .sha256 file."""
    hash_file = get_hash_file_path(file_path)
    try:
        with open(hash_file, "r") as f:
            line = f.readline().strip()
            if line:
                return line.split()[0]  # First part is the hash
    except (IOError, OSError):
        pass  # File doesn't exist or can't be read
    return None


def verify_file_integrity(file_path: str) -> bool:
    """Verify file integrity using stored hash."""
    if not os.path.exists(file_path):
        return False

    stored_hash = load_file_hash(file_path)
    if not stored_hash:
        # No stored hash, calculate and save it
        current_hash = calculate_sha256(file_path)
        if current_hash:
            save_file_hash(file_path, current_hash)
            logger.debug(f"Generated initial hash for {os.path.basename(file_path)}")
        return True  # Assume valid for new files

    current_hash = calculate_sha256(file_path)
    if not current_hash:
        return False

    if current_hash == stored_hash:
        logger.debug(f"Hash verified for {os.path.basename(file_path)}")
        return True
    else:
        logger.warning(
            f"Hash mismatch for {os.path.basename(file_path)} - file may be corrupted"
        )
        return False


def download_file_with_retry(
    url: str,
    download_path: str,
    # log_message_func: Callable[[str], None] # Removed
) -> bool:
    """
    Downloads a file with a robust retry mechanism and platform-specific handling.
    Checks for existing valid files (especially zips) before downloading.
    Validates zip files after download. Handles temporary files and cleanup.

    Args:
        url (str): The URL to download the file from.
        download_path (str): The final path to save the downloaded file.
        # log_message_func removed from args

    Returns:
        bool: True if the file was successfully downloaded (or already existed and was valid),
              False otherwise.
    """
    session = requests.Session()
    # Using type: ignore for Retry as it might not be perfectly typed by stubs,
    # but the parameters are standard for urllib3.
    retry_strategy: Retry = Retry(
        connect=DEFAULT_CONNECT_RETRIES,
        backoff_factor=DEFAULT_BACKOFF_FACTOR,
        status_forcelist=[502, 503, 504],
    )  # type: ignore
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Check if file exists and is valid (especially for zips)
    if os.path.exists(download_path):
        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(download_path, "r") as zf:
                    if zf.testzip() is not None:  # None means no errors
                        raise zipfile.BadZipFile(
                            "Zip file integrity check failed (testzip)."
                        )

                # Additional hash verification
                if verify_file_integrity(download_path):
                    logger.info(
                        f"Skipped: {os.path.basename(download_path)} (already present & verified)"
                    )
                    return True
                else:
                    logger.info(
                        f"Hash verification failed for {os.path.basename(download_path)}, re-downloading"
                    )
                    try:
                        os.remove(download_path)
                        # Also remove hash file
                        hash_file = get_hash_file_path(download_path)
                        if os.path.exists(hash_file):
                            os.remove(hash_file)
                    except (IOError, OSError) as e_rm:
                        logger.error(
                            f"Error removing file with hash mismatch {download_path}: {e_rm}"
                        )
                        return False
            except zipfile.BadZipFile:
                logger.debug(f"Removing corrupted zip file: {download_path}")
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm:
                    logger.error(
                        f"Error removing corrupted zip {download_path}: {e_rm}"
                    )
                    return False
            except (IOError, OSError) as e_check:  # More specific for file check issues
                logger.debug(
                    f"IO/OS Error checking existing zip file {download_path}: {e_check}. Attempting re-download."
                )
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm_other:
                    logger.error(
                        f"Error removing file {download_path} before re-download: {e_rm_other}"
                    )
                    return False
            except (
                Exception
            ) as e_unexp_check:  # Catch other unexpected errors during check
                logger.error(
                    f"Unexpected error checking existing zip file {download_path}: {e_unexp_check}. Attempting re-download."
                )
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm_unexp:
                    logger.error(
                        f"Error removing file {download_path} after unexpected check error: {e_rm_unexp}"
                    )
                    return False
        else:  # For non-zip files
            try:
                if os.path.getsize(download_path) > 0:
                    # Hash verification for non-zip files
                    if verify_file_integrity(download_path):
                        logger.info(
                            f"Skipped: {os.path.basename(download_path)} (already present & verified)"
                        )
                        return True
                    else:
                        logger.info(
                            f"Hash verification failed for {os.path.basename(download_path)}, re-downloading"
                        )
                        try:
                            os.remove(download_path)
                            # Also remove hash file
                            hash_file = get_hash_file_path(download_path)
                            if os.path.exists(hash_file):
                                os.remove(hash_file)
                        except (IOError, OSError) as e_rm:
                            logger.error(
                                f"Error removing file with hash mismatch {download_path}: {e_rm}"
                            )
                            return False
                else:
                    logger.debug(f"Removing empty file: {download_path}")
                    os.remove(download_path)  # Try removing first
            except (
                IOError,
                OSError,
            ) as e_rm_empty:  # Catch error if removal or getsize fails
                logger.error(
                    f"Error with existing empty file {download_path}: {e_rm_empty}"
                )
                return False

    temp_path = download_path + ".tmp"
    try:
        # Log before session.get()
        logger.debug(
            f"Attempting to download file from URL: {url} to temp path: {temp_path}"
        )
        time.time()
        response = session.get(url, stream=True, timeout=DEFAULT_REQUEST_TIMEOUT)

        # Log HTTP response status code
        logger.debug(
            f"Received HTTP response status code: {response.status_code} for URL: {url}"
        )
        response.raise_for_status()  # Handled by requests.exceptions.RequestException

        downloaded_chunks = 0
        downloaded_bytes = 0
        with open(temp_path, "wb") as file:  # Can raise IOError
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                if chunk:
                    file.write(chunk)
                    downloaded_chunks += 1
                    downloaded_bytes += len(chunk)
                    if downloaded_chunks % 100 == 0:
                        logger.debug(
                            f"Downloaded {downloaded_chunks} chunks ({downloaded_bytes} bytes) so far for {url}"
                        )

        # end-to-end download duration is available in
        #   elapsed = time.time() - start_time
        # if you intend to log or return it later.
        file_size_mb = downloaded_bytes / (1024 * 1024)
        logger.debug(
            f"Finished downloading {url}. Total chunks: {downloaded_chunks}, total bytes: {downloaded_bytes}."
        )

        # Log completion after successful file replacement (moved below)

        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(temp_path, "r") as zf_temp:
                    if zf_temp.testzip() is not None:
                        raise zipfile.BadZipFile(
                            "Downloaded zip file integrity check failed (testzip)."
                        )
            except zipfile.BadZipFile as e_zip_bad:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except (IOError, OSError) as e_rm_bad_zip:
                        logger.error(
                            f"Error removing temp file after bad zip: {e_rm_bad_zip}"
                        )
                logger.error(
                    f"Error: Downloaded zip file {url} is corrupted: {e_zip_bad}"
                )
                return False
            except (
                IOError,
                OSError,
            ) as e_zip_io:  # Catch IO errors during zip validation (e.g. file not found if removed)
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except (IOError, OSError) as e_rm_zip_io:
                        logger.error(
                            f"Error removing temp file after zip IO error: {e_rm_zip_io}"
                        )
                logger.error(
                    f"IO/OS error validating temporary zip file {temp_path} from {url}: {e_zip_io}"
                )
                return False

        # File replacement logic
        if platform.system() == "Windows":
            retry_delay = WINDOWS_INITIAL_RETRY_DELAY
            for i in range(WINDOWS_MAX_REPLACE_RETRIES):
                try:
                    gc.collect()
                    logger.debug(
                        f"Attempting to move temporary file {temp_path} to {download_path} (Windows attempt {i+1}/{WINDOWS_MAX_REPLACE_RETRIES})"
                    )
                    os.replace(temp_path, download_path)
                    logger.debug(
                        f"Successfully moved temporary file {temp_path} to {download_path}"
                    )

                    # Generate hash for the downloaded file
                    current_hash = calculate_sha256(download_path)
                    if current_hash:
                        save_file_hash(download_path, current_hash)

                    # Log successful download after file is in place
                    if file_size_mb >= 1.0:
                        logger.info(
                            f"Downloaded: {os.path.basename(download_path)} ({file_size_mb:.1f} MB)"
                        )
                    else:
                        logger.info(
                            f"Downloaded: {os.path.basename(download_path)} ({downloaded_bytes} bytes)"
                        )
                    return True
                except (
                    PermissionError
                ) as e_perm:  # Specific to Windows replace issues often
                    if i < WINDOWS_MAX_REPLACE_RETRIES - 1:
                        logger.debug(
                            f"File access error (PermissionError) on Windows for {download_path}, retrying in {retry_delay}s: {e_perm}"
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        logger.error(
                            f"Final attempt failed (PermissionError) for {download_path} on Windows: {e_perm}"
                        )
                        if os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except (IOError, OSError) as e_rm_perm:
                                logger.error(
                                    f"Error removing temp file after permission error: {e_rm_perm}"
                                )
                        return False
                except (
                    IOError,
                    OSError,
                ) as e_win_io_other:  # Catch other IO/OS errors during replace
                    logger.error(
                        f"Unexpected IO/OS error replacing file on Windows {download_path}: {e_win_io_other}"
                    )
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except (IOError, OSError) as e_rm_win_io:
                            logger.error(
                                f"Error removing temp file after Windows IO error: {e_rm_win_io}"
                            )
                    return False
        else:  # Non-Windows
            try:
                logger.debug(
                    f"Attempting to move temporary file {temp_path} to {download_path} (non-Windows)"
                )
                os.replace(temp_path, download_path)
                logger.debug(
                    f"Successfully moved temporary file {temp_path} to {download_path}"
                )

                # Generate hash for the downloaded file
                current_hash = calculate_sha256(download_path)
                if current_hash:
                    save_file_hash(download_path, current_hash)

                # Log successful download after file is in place
                if file_size_mb >= 1.0:
                    logger.info(
                        f"Downloaded: {os.path.basename(download_path)} ({file_size_mb:.1f} MB)"
                    )
                else:
                    logger.info(
                        f"Downloaded: {os.path.basename(download_path)} ({downloaded_bytes} bytes)"
                    )
                return True
            except (IOError, OSError) as e_nix_replace:
                logger.error(
                    f"Error replacing file {temp_path} to {download_path} on non-Windows: {e_nix_replace}"
                )
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except (IOError, OSError) as e_rm_nix_replace:
                        logger.error(
                            f"Error removing temp file after non-Windows replace error: {e_rm_nix_replace}"
                        )
                return False

    except (
        requests.exceptions.RequestException
    ) as e_req:  # Handles session.get, response.raise_for_status
        logger.error(f"Network error downloading {url}: {e_req}")
    except IOError as e_io:  # Handles open()
        logger.error(
            f"File I/O error during download process for {url} (temp path: {temp_path}): {e_io}"
        )
    except (
        Exception
    ) as e_gen:  # Catch-all for truly unexpected errors in the download block
        logger.error(
            f"An unexpected error occurred during download/processing for {url}: {e_gen}",
            exc_info=True,
        )

    # Final cleanup of temp_path if it still exists due to an error
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except (IOError, OSError) as e_rm_final_tmp:
            logger.warning(
                f"Error removing temporary file {temp_path} after failure: {e_rm_final_tmp}"
            )
    return False
