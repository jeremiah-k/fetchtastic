# src/fetchtastic/utils.py
import os
import platform
import time
import zipfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry # type: ignore
import gc # For Windows file operation retries
from typing import Callable, Optional

# Constants for download_file_with_retry
DEFAULT_CONNECT_RETRIES: int = 3
DEFAULT_BACKOFF_FACTOR: float = 1.0 # Make it float for potential fractional backoff
DEFAULT_REQUEST_TIMEOUT: int = 30  # seconds
DEFAULT_CHUNK_SIZE: int = 8 * 1024  # 8KB
WINDOWS_MAX_REPLACE_RETRIES: int = 3
WINDOWS_INITIAL_RETRY_DELAY: float = 1.0 # seconds

def download_file_with_retry(
    url: str,
    download_path: str,
    log_message_func: Callable[[str], None]
) -> bool:
    """
    Downloads a file with a robust retry mechanism and platform-specific handling.
    Checks for existing valid files (especially zips) before downloading.
    Validates zip files after download. Handles temporary files and cleanup.

    Args:
        url (str): The URL to download the file from.
        download_path (str): The final path to save the downloaded file.
        log_message_func (Callable[[str], None]): Function to log messages.

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
        status_forcelist=[502, 503, 504]
    ) # type: ignore
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Check if file exists and is valid (especially for zips)
    if os.path.exists(download_path):
        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(download_path, "r") as zf:
                    if zf.testzip() is not None : # None means no errors
                            raise zipfile.BadZipFile("Zip file integrity check failed (testzip).")
                log_message_func(f"File {download_path} already exists and is a valid zip. Skipping download.")
                return True
            except zipfile.BadZipFile:
                log_message_func(f"Removing corrupted zip file: {download_path}")
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm:
                    log_message_func(f"Error removing corrupted zip {download_path}: {e_rm}")
                    return False
            except (IOError, OSError) as e_check: # More specific for file check issues
                log_message_func(f"IO/OS Error checking existing zip file {download_path}: {e_check}. Attempting re-download.")
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm_other:
                    log_message_func(f"Error removing file {download_path} before re-download: {e_rm_other}")
                    return False
            except Exception as e_unexp_check: # Catch other unexpected errors during check
                log_message_func(f"Unexpected error checking existing zip file {download_path}: {e_unexp_check}. Attempting re-download.")
                try:
                    os.remove(download_path)
                except (IOError, OSError) as e_rm_unexp:
                    log_message_func(f"Error removing file {download_path} after unexpected check error: {e_rm_unexp}")
                    return False
        else: # For non-zip files
            try:
                if os.path.getsize(download_path) > 0:
                    log_message_func(f"File {download_path} already exists and is not empty. Skipping download.")
                    return True
                else:
                    log_message_func(f"Removing empty file: {download_path}")
                    os.remove(download_path) # Try removing first
            except (IOError, OSError) as e_rm_empty: # Catch error if removal or getsize fails
                log_message_func(f"Error with existing empty file {download_path}: {e_rm_empty}")
                return False

    temp_path = download_path + ".tmp"
    try:
        # Log before session.get()
        log_message_func(f"Attempting to download file from URL: {url} to temp path: {temp_path}")
        response = session.get(url, stream=True, timeout=DEFAULT_REQUEST_TIMEOUT)

        # Log HTTP response status code
        log_message_func(f"Received HTTP response status code: {response.status_code} for URL: {url}")
        response.raise_for_status() # Handled by requests.exceptions.RequestException

        downloaded_chunks = 0
        downloaded_bytes = 0
        with open(temp_path, "wb") as file: # Can raise IOError
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                if chunk:
                    file.write(chunk)
                    downloaded_chunks += 1
                    downloaded_bytes += len(chunk)
                    if downloaded_chunks % 100 == 0:
                        log_message_func(f"Downloaded {downloaded_chunks} chunks ({downloaded_bytes} bytes) so far for {url}")
            log_message_func(f"Finished downloading {url}. Total chunks: {downloaded_chunks}, total bytes: {downloaded_bytes}.")

        if download_path.endswith(".zip"):
            try:
                with zipfile.ZipFile(temp_path, "r") as zf_temp:
                    if zf_temp.testzip() is not None:
                        raise zipfile.BadZipFile("Downloaded zip file integrity check failed (testzip).")
            except zipfile.BadZipFile as e_zip_bad:
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except (IOError, OSError) as e_rm_bad_zip: log_message_func(f"Error removing temp file after bad zip: {e_rm_bad_zip}")
                log_message_func(f"Error: Downloaded zip file {url} is corrupted: {e_zip_bad}")
                return False
            except (IOError, OSError) as e_zip_io: # Catch IO errors during zip validation (e.g. file not found if removed)
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except (IOError, OSError) as e_rm_zip_io: log_message_func(f"Error removing temp file after zip IO error: {e_rm_zip_io}")
                log_message_func(f"IO/OS error validating temporary zip file {temp_path} from {url}: {e_zip_io}")
                return False

        # File replacement logic
        if platform.system() == "Windows":
            retry_delay = WINDOWS_INITIAL_RETRY_DELAY
            for i in range(WINDOWS_MAX_REPLACE_RETRIES):
                try:
                    gc.collect()
                    log_message_func(f"Attempting to move temporary file {temp_path} to {download_path} (Windows attempt {i+1}/{WINDOWS_MAX_REPLACE_RETRIES})")
                    os.replace(temp_path, download_path)
                    log_message_func(f"Successfully moved temporary file {temp_path} to {download_path}")
                    return True
                except PermissionError as e_perm: # Specific to Windows replace issues often
                    if i < WINDOWS_MAX_REPLACE_RETRIES - 1:
                        log_message_func(f"File access error (PermissionError) on Windows for {download_path}, retrying in {retry_delay}s: {e_perm}")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        log_message_func(f"Final attempt failed (PermissionError) for {download_path} on Windows: {e_perm}")
                        if os.path.exists(temp_path):
                            try: os.remove(temp_path)
                            except (IOError, OSError) as e_rm_perm: log_message_func(f"Error removing temp file after permission error: {e_rm_perm}")
                        return False
                except (IOError, OSError) as e_win_io_other: # Catch other IO/OS errors during replace
                    log_message_func(f"Unexpected IO/OS error replacing file on Windows {download_path}: {e_win_io_other}")
                    if os.path.exists(temp_path):
                        try: os.remove(temp_path)
                        except (IOError, OSError) as e_rm_win_io: log_message_func(f"Error removing temp file after Windows IO error: {e_rm_win_io}")
                    return False
        else: # Non-Windows
            try:
                log_message_func(f"Attempting to move temporary file {temp_path} to {download_path} (non-Windows)")
                os.replace(temp_path, download_path)
                log_message_func(f"Successfully moved temporary file {temp_path} to {download_path}")
                return True
            except (IOError, OSError) as e_nix_replace:
                log_message_func(f"Error replacing file {temp_path} to {download_path} on non-Windows: {e_nix_replace}")
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except (IOError, OSError) as e_rm_nix_replace: log_message_func(f"Error removing temp file after non-Windows replace error: {e_rm_nix_replace}")
                return False

    except requests.exceptions.RequestException as e_req: # Handles session.get, response.raise_for_status
        log_message_func(f"Network error downloading {url}: {e_req}")
    except IOError as e_io: # Handles open()
        log_message_func(f"File I/O error during download process for {url} (temp path: {temp_path}): {e_io}")
    except Exception as e_gen: # Catch-all for truly unexpected errors in the download block
        log_message_func(f"An unexpected error occurred during download/processing for {url}: {e_gen}")

    # Final cleanup of temp_path if it still exists due to an error
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except (IOError, OSError) as e_rm_final_tmp:
            log_message_func(f"Error removing temporary file {temp_path} after failure: {e_rm_final_tmp}")
    return False
