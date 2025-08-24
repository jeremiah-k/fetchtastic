# src/fetchtastic/utils.py
import gc  # For Windows file operation retries
import hashlib
import os
import platform
import re
import time
import zipfile
from typing import Optional  # Callable removed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry  # type: ignore

# Import constants from constants module
from fetchtastic.constants import (
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    WINDOWS_INITIAL_RETRY_DELAY,
    WINDOWS_MAX_REPLACE_RETRIES,
    ZIP_EXTENSION,
)
from fetchtastic.log_utils import logger  # Import the new logger


def calculate_sha256(file_path: str) -> Optional[str]:
    """
    Return the SHA-256 hex digest of the file at file_path, or None if the file cannot be read.

    Reads the file in binary chunks and computes its SHA-256 checksum. If an I/O or OS error occurs (for example: file not found or permission denied), the function returns None.
    """
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
    Download a file from a URL with retries, integrity checks, and platform-specific atomic replacement.

    Performs these behaviors:
    - Uses a requests.Session with a robust Retry policy for network resilience.
    - If download_path already exists:
      - For ZIP files (by ZIP_EXTENSION), validates with zipfile.testzip() and then with the stored SHA-256 hash; if valid, skips download and returns True. Corrupted or mismatched files are removed before attempting a re-download.
      - For non-ZIP files, skips download if a non-empty file passes SHA-256 verification; empty or invalid files are removed before re-download.
    - Streams the HTTP response to a temporary file (download_path + ".tmp"), writing in chunks and validating ZIP integrity for downloaded archives.
    - Replaces the target file atomically using os.replace:
      - On Windows, retries replacements (with exponential backoff) to work around transient PermissionError conditions.
      - On non-Windows platforms, attempts a single replace.
    - After a successful replace, computes and saves a SHA-256 hash alongside the file (via a .sha256 file).
    - Cleans up temporary files and removes partially downloaded or corrupted files on error.

    Return value:
        True if the file was successfully downloaded or an existing file was present and verified; False on any failure.

    Side effects:
    - Creates, replaces, and removes files at download_path and download_path + ".tmp".
    - Writes a companion SHA-256 file next to the downloaded file when a hash can be computed.
    - Logs progress, validation results, and errors via the module logger.

    Errors and exceptions:
    - Network, IO, ZIP validation, and unexpected exceptions are caught internally; the function returns False on failure rather than propagating exceptions.
    """
    session = requests.Session()
    # Using type: ignore for Retry as it might not be perfectly typed by stubs,
    # but the parameters are standard for urllib3.
    try:
        retry_strategy: Retry = Retry(
            total=DEFAULT_CONNECT_RETRIES,
            connect=DEFAULT_CONNECT_RETRIES,
            read=DEFAULT_CONNECT_RETRIES,
            status=DEFAULT_CONNECT_RETRIES,
            backoff_factor=DEFAULT_BACKOFF_FACTOR,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
    except TypeError:
        # urllib3 v1 fallback
        retry_strategy = Retry(
            total=DEFAULT_CONNECT_RETRIES,
            connect=DEFAULT_CONNECT_RETRIES,
            read=DEFAULT_CONNECT_RETRIES,
            backoff_factor=DEFAULT_BACKOFF_FACTOR,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            method_whitelist=frozenset({"GET", "HEAD"}),  # type: ignore[arg-type]
            raise_on_status=False,
        )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Check if file exists and is valid (especially for zips)
    if os.path.exists(download_path):
        if download_path.lower().endswith(ZIP_EXTENSION.lower()):
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
        start_time = time.time()
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

        elapsed = time.time() - start_time
        file_size_mb = downloaded_bytes / (1024 * 1024)
        logger.debug(
            f"Finished downloading {url}. Total chunks: {downloaded_chunks}, total bytes: {downloaded_bytes}."
        )
        logger.debug("Download elapsed time: %.2fs for %s", elapsed, url)

        # Log completion after successful file replacement (moved below)

        if download_path.lower().endswith(ZIP_EXTENSION.lower()):
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


def extract_base_name(filename: str) -> str:
    """
    Return a filename with trailing version and commit/hash segments removed.

    This normalizes names like "-2.5.13", "_v1.2.3", "-2.5.13.1a2b3c4" and optional prerelease suffixes
    (e.g., rc, dev, beta, alpha) by stripping those version/hash segments while preserving other
    filename parts and separators. Consecutive separators produced by removal are collapsed to a single
    '-' or '_' as appropriate.

    Examples:
      'fdroidRelease-2.5.9.apk' -> 'fdroidRelease.apk'
      'firmware-rak4631-2.7.4.c1f4f79-ota.zip' -> 'firmware-rak4631-ota.zip'
      'meshtasticd_2.5.13.1a06f88_amd64.deb' -> 'meshtasticd_amd64.deb'
    """
    # Remove versions like: -2.5.13, _v1.2.3, -2.5.13.abcdef1, and optional prerelease: -rc1/.dev1/-beta2/-alpha3
    base_name = re.sub(
        r"[-_]v?\d+\.\d+\.\d+(?:\.[\da-f]+)?(?:[-_.]?(?:rc|dev|beta|alpha)\d*)?(?=[-_.]|$)",
        "",
        filename,
    )
    # Clean up double separators that might result from the substitution
    base_name = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], base_name)
    return base_name
