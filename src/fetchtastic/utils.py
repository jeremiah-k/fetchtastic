# src/fetchtastic/utils.py
import gc  # For Windows file operation retries
import hashlib
import os
import platform
import re
import time
import zipfile
from typing import List, Optional  # Callable removed

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

# Precompiled regexes for version stripping
MODERN_VER_RX = re.compile(
    r"[-_]v?\d+\.\d+\.\d+(?:\.[\da-f]+)?(?:[-_.]?(?:rc|dev|beta|alpha)\d*)?(?=[-_.]|$)"
)
LEGACY_VER_RX = re.compile(
    r"([-_])v?\d+\.\d+\.\d+(?:\.[\da-f]+)?(?:[-_.]?(?:rc|dev|beta|alpha)\d*)?(?=[-_.]|$)"
)

# Precompiled regex for punctuation stripping (performance optimization)
_PUNC_RX = re.compile(r"[^a-z0-9]+")


def calculate_sha256(file_path: str) -> Optional[str]:
    """
    Compute and return the SHA-256 hex digest of a file, or None if the file cannot be read.

    Reads the file in binary mode and streams its contents to the SHA-256 digest (no full-file buffering). On success returns the 64-character lowercase hexadecimal digest string. If the file cannot be opened or read (e.g., missing file or permission error), the function logs the error at debug level and returns None instead of raising.
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
    r"""
    Write the given SHA-256 hex digest to a companion `.sha256` sidecar file next to `file_path`.

    The sidecar file is created at the path returned by `get_hash_file_path(file_path)` and contains a single line in the format:
        "<hash_value>  <basename>\n"

    Parameters:
        file_path (str): Path to the original file whose hash is being recorded; only the basename is written into the sidecar.
        hash_value (str): Hexadecimal SHA-256 digest to persist.

    Side effects:
        Creates or overwrites the `.sha256` sidecar file. IO errors are caught and logged; this function does not raise on failure.
    """
    hash_file = get_hash_file_path(file_path)
    tmp_file = f"{hash_file}.tmp.{os.getpid()}"
    try:
        with open(tmp_file, "w", encoding="ascii", newline="\n") as f:
            f.write(f"{hash_value}  {os.path.basename(file_path)}\n")
        os.replace(tmp_file, hash_file)
        logger.debug("Saved hash for %s", os.path.basename(file_path))
    except (IOError, OSError) as e:
        logger.debug("Error saving hash file %s: %s", hash_file, e)
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass


def _remove_file_and_hash(path: str) -> bool:
    """
    Remove a file and its .sha256 sidecar if present. Returns True on success, False on error.

    Errors are logged and False is returned; exceptions are not raised.
    """
    try:
        if os.path.exists(path):
            os.remove(path)
        hash_file = get_hash_file_path(path)
        if os.path.exists(hash_file):
            os.remove(hash_file)
        return True
    except (IOError, OSError) as e:
        logger.error(f"Error removing {path} or its hash sidecar: {e}")
        return False


def load_file_hash(file_path: str) -> Optional[str]:
    """
    Return the SHA-256 hex string stored in the file_path's `.sha256` sidecar, if available.

    Reads the companion `<file_path>.sha256` file and returns the first whitespace-separated token from its first line (the stored hash). If the sidecar is missing, unreadable, or empty, returns None. Does not raise on I/O errors.
    """
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
    # Note: Session is created after pre-checks and closed in finally

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
                    if not _remove_file_and_hash(download_path):
                        return False
            except zipfile.BadZipFile:
                logger.debug(f"Removing corrupted zip file: {download_path}")
                if not _remove_file_and_hash(download_path):
                    return False
            except (IOError, OSError) as e_check:  # More specific for file check issues
                logger.debug(
                    f"IO/OS Error checking existing zip file {download_path}: {e_check}. Attempting re-download."
                )
                if not _remove_file_and_hash(download_path):
                    return False
            except (
                Exception
            ) as e_unexp_check:  # Catch other unexpected errors during check
                logger.error(
                    f"Unexpected error checking existing zip file {download_path}: {e_unexp_check}. Attempting re-download."
                )
                if not _remove_file_and_hash(download_path):
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
                    # Remove any stale hash sidecar
                    hash_file = get_hash_file_path(download_path)
                    if os.path.exists(hash_file):
                        try:
                            os.remove(hash_file)
                        except (IOError, OSError) as e_rm_hash:
                            logger.debug(
                                f"Error removing hash file {hash_file}: {e_rm_hash}"
                            )
            except (
                IOError,
                OSError,
            ) as e_rm_empty:  # Catch error if removal or getsize fails
                logger.error(
                    f"Error with existing empty file {download_path}: {e_rm_empty}"
                )
                return False

    temp_path = f"{download_path}.tmp.{os.getpid()}.{int(time.time()*1000)}"
    session = requests.Session()
    response = None  # ensure we can close the Response in finally
    try:
        # Log before session.get()
        logger.debug(
            f"Attempting to download file from URL: {url} to temp path: {temp_path}"
        )
        start_time = time.time()
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
                status=DEFAULT_CONNECT_RETRIES,
                backoff_factor=DEFAULT_BACKOFF_FACTOR,
                status_forcelist=[408, 429, 500, 502, 503, 504],
                method_whitelist=frozenset({"GET", "HEAD"}),  # type: ignore[arg-type]
                raise_on_status=False,
            )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        response = session.get(url, stream=True, timeout=DEFAULT_REQUEST_TIMEOUT)

        # Log HTTP response status code
        logger.debug(
            f"Received HTTP response status code: {response.status_code} for URL: {url}"
        )
        # Status-based retries have already been applied by urllib3's Retry;
        # raise_for_status will surface the final HTTP error, if any.
        response.raise_for_status()  # Handled by requests.exceptions.RequestException

        downloaded_chunks = 0
        downloaded_bytes = 0
        # Ensure destination directory exists for the temp file
        parent_dir = os.path.dirname(download_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
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
    except Exception as e_gen:  # noqa: BLE001 - Catch-all for unexpected errors
        logger.error(
            f"An unexpected error occurred during download/processing for {url}: {e_gen}",
            exc_info=True,
        )
    finally:
        # Final cleanup of temp_path if it still exists due to an error
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except (IOError, OSError) as e_rm_final_tmp:
                logger.warning(
                    f"Error removing temporary file {temp_path} after failure: {e_rm_final_tmp}"
                )
        # Close HTTP response explicitly to release the connection
        if response is not None:
            try:
                response.close()
            except Exception as e:
                logger.debug(f"Error closing HTTP response for {url}: {e}")
        session.close()
    return False


def extract_base_name(filename: str) -> str:
    """
    Return a filename with trailing version and commit/hash segments removed.

    Removes the separator that immediately precedes the version token so results do not
    contain a stray dash/underscore before the extension. This matches test expectations
    and prior behavior used throughout the codebase.

    Examples:
      'fdroidRelease-2.5.9.apk' -> 'fdroidRelease.apk'
      'firmware-rak4631-2.7.4.c1f4f79-ota.zip' -> 'firmware-rak4631-ota.zip'
      'firmware-rak4631-2.7.4.c1f4f79.zip' -> 'firmware-rak4631.zip'
      'meshtasticd_2.5.13.1a06f88_amd64.deb' -> 'meshtasticd_amd64.deb'
    """
    # Remove versions like: -2.5.13, _v1.2.3, -2.5.13.abcdef1, and optional prerelease: -rc1/.dev1/-beta2/-alpha3
    base_name = MODERN_VER_RX.sub("", filename)
    # Clean up double separators that might result from the substitution
    base_name = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], base_name)
    return base_name


def legacy_strip_version_numbers(filename: str) -> str:
    """
    Return the filename with trailing version/commit/hash segments removed while preserving the separator immediately before the version token.

    Preserves the separator ('-' or '_') that directly precedes the removed version token so patterns that include that separator still match (for example, "rak4631-" or "t1000-e-"). Collapses consecutive separators into a single '-' or '_'.

    Returns:
        The normalized filename with the legacy-style version portion stripped.
    """
    legacy = LEGACY_VER_RX.sub(r"\1", filename)
    legacy = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], legacy)
    return legacy


def matches_selected_patterns(
    filename: str, selected_patterns: Optional[List[str]]
) -> bool:
    """
    Return True if any of the provided patterns match the filename's normalized base name.

    Checks both the modern normalization (which removes the version token and its preceding separator)
    and the legacy normalization (which preserves the separator before the version token). If
    `selected_patterns` is falsy (None or empty) the function returns True.

    The matcher is forgiving about minor naming changes introduced upstream by normalising both the
    candidate filename and the patterns to lower-case and by also performing a punctuation-stripped
    comparison. This keeps existing configurations working when asset names switch between styles such
    as ``fdroidRelease-`` and ``app-fdroid-release``.

    Parameters:
        selected_patterns: Iterable of substring patterns to search for; empty or None means "match all".

    Returns:
        True if any non-empty pattern appears in either normalized base name; otherwise False.
    """

    if not selected_patterns:
        return True

    base_modern = extract_base_name(filename)
    base_legacy = legacy_strip_version_numbers(filename)
    base_modern_lower = base_modern.lower()
    base_legacy_lower = base_legacy.lower()
    base_modern_sanitised = None  # lazy
    base_legacy_sanitised = None  # lazy

    def _strip_punctuation(value: str) -> str:
        """Return a simplified token by removing punctuation characters and lower-casing."""
        return _PUNC_RX.sub("", value.lower())

    for pat in selected_patterns:
        pat = pat.strip()
        if not pat:
            continue
        pat_lower = pat.lower()
        if pat_lower in base_modern_lower or pat_lower in base_legacy_lower:
            return True

        # Fall back to punctuation-stripped matching when the pattern appears to target
        # mixed-case or dotted segments (e.g., fdroidRelease-, *.zip), or when it contains
        # common keywords that are known to have changed naming schemes. This preserves the
        # ability to distinguish dash vs underscore selections (e.g., "rak4631-" vs "rak4631_")
        # while being more forgiving for patterns that are likely affected by upstream renames.
        needs_sanitised = (
            any(ch.isupper() for ch in pat)
            or "." in pat
            or any(
                keyword in pat.lower()
                for keyword in ["release", "apk", "aab", "fdroid"]
            )
        )
        if needs_sanitised:
            pat_sanitised = _strip_punctuation(pat)
            if pat_sanitised:
                # Compute sanitised bases only when needed
                if base_modern_sanitised is None:
                    base_modern_sanitised = _strip_punctuation(base_modern)
                if base_legacy_sanitised is None:
                    base_legacy_sanitised = _strip_punctuation(base_legacy)

                if (
                    pat_sanitised in base_modern_sanitised
                    or pat_sanitised in base_legacy_sanitised
                ):
                    return True

    # Last-chance fallback: for very short patterns (≤3 chars), try sanitised matching
    # This helps with patterns like "rak" matching "RAK4631" after sanitization
    for pat in selected_patterns:
        pat = pat.strip()
        if not pat or len(pat) > 3:
            continue
        pat_sanitised = _strip_punctuation(pat)
        if pat_sanitised:
            # Compute sanitised bases only when needed
            if base_modern_sanitised is None:
                base_modern_sanitised = _strip_punctuation(base_modern)
            if base_legacy_sanitised is None:
                base_legacy_sanitised = _strip_punctuation(base_legacy)

            if (
                pat_sanitised in base_modern_sanitised
                or pat_sanitised in base_legacy_sanitised
            ):
                return True

    return False
