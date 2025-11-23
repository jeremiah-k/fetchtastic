# src/fetchtastic/utils.py
import gc  # For Windows file operation retries
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple  # Callable removed

import platformdirs
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry  # type: ignore

# Import constants from constants module
from fetchtastic.constants import (
    API_CALL_DELAY,
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    GITHUB_API_TIMEOUT,
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

# Cache for the User-Agent string to avoid repeated metadata lookups
_USER_AGENT_CACHE = None

# Thread-safe token warning tracking (centralized)
_token_warning_shown = False
_token_warning_lock = threading.Lock()

# GitHub API rate limit tracking
_rate_limit_cache: Dict[str, Tuple[int, datetime]] = {}  # remaining, reset_timestamp
_rate_limit_lock = threading.Lock()
_rate_limit_cache_file = None
_last_rate_limit_token_hash: Optional[str] = None

# Track whether rate limit cache has been loaded
_rate_limit_cache_loaded = False

# Track last cache save time for throttling (5 second minimum interval)
_last_cache_save_time = 0.0

# Minimum seconds between disk writes for rate-limit cache
RATE_LIMIT_CACHE_SAVE_INTERVAL = 5.0

# API request tracking for session summary
_api_request_count = 0
_api_cache_hits = 0
_api_cache_misses = 0
_api_auth_used = False
_api_tracking_lock = threading.Lock()


def get_user_agent() -> str:
    """
    Get the User-Agent string used for HTTP requests.

    Returns:
        The string `fetchtastic/{version}`, where `{version}` is the installed package version or `unknown` if the version cannot be determined.
    """
    global _USER_AGENT_CACHE

    if _USER_AGENT_CACHE is None:
        try:
            app_version = importlib.metadata.version("fetchtastic")
        except importlib.metadata.PackageNotFoundError:
            app_version = "unknown"

        _USER_AGENT_CACHE = f"fetchtastic/{app_version}"

    return _USER_AGENT_CACHE


def track_api_cache_hit() -> None:
    """Track a cache hit for API requests."""
    global _api_cache_hits
    with _api_tracking_lock:
        _api_cache_hits += 1


def track_api_cache_miss() -> None:
    """Track a cache miss for API requests."""
    global _api_cache_misses
    with _api_tracking_lock:
        _api_cache_misses += 1


def get_api_request_summary() -> Dict[str, Any]:
    """
    Builds a session-wide summary of API request and cache statistics.

    The returned dictionary contains aggregate request counters and authentication usage for the current session only,
    and may include cached rate-limit details if available for the last used token.

    Returns:
        summary (dict): Keys include:
            - "total_requests" (int): Total number of API requests made this session.
            - "cache_hits" (int): Number of API cache hits during this session.
            - "cache_misses" (int): Number of API cache misses during this session.
            - "auth_used" (bool): Whether any request used authentication during this session.
            - "rate_limit_remaining" (int, optional): Remaining requests for the last token (includes consumption from previous sessions).
            - "rate_limit_reset" (datetime.datetime, optional): Reset timestamp for the cached rate limit, present when available.
    """
    with _api_tracking_lock:
        summary = {
            "total_requests": _api_request_count,
            "cache_hits": _api_cache_hits,
            "cache_misses": _api_cache_misses,
            "auth_used": _api_auth_used,
        }

    # Add rate limit info if available
    global _last_rate_limit_token_hash
    if _last_rate_limit_token_hash:
        rate_limit_info = get_rate_limit_info(_last_rate_limit_token_hash)
        if rate_limit_info:
            remaining, reset_timestamp = rate_limit_info
            summary["rate_limit_remaining"] = remaining
            summary["rate_limit_reset"] = reset_timestamp

    return summary


def reset_api_tracking() -> None:
    """
    Reset session-wide API request tracking counters and flags.

    Resets request count, cache hit and miss counters, and authentication-used flag
    while holding the module's tracking lock to ensure thread-safety.
    """
    global _api_request_count, _api_cache_hits, _api_cache_misses, _api_auth_used
    with _api_tracking_lock:
        _api_request_count = 0
        _api_cache_hits = 0
        _api_cache_misses = 0
        _api_auth_used = False


def get_effective_github_token(
    github_token: Optional[str], allow_env_token: bool = True
) -> Optional[str]:
    """
    Determine the GitHub token to use, preferring the explicit argument over the environment.

    Parameters:
        github_token (Optional[str]): Explicit token to use; leading and trailing whitespace are ignored.
        allow_env_token (bool): If True, fall back to the `GITHUB_TOKEN` environment variable when no explicit token is provided.

    Returns:
        Optional[str]: The chosen token with surrounding whitespace removed, or `None` if no token is available.
    """
    candidate = (github_token or "").strip()
    if candidate:
        return candidate
    if not allow_env_token:
        return None
    env_token = os.environ.get("GITHUB_TOKEN")
    return env_token.strip() if env_token else None


def _show_token_warning_if_needed(effective_token: Optional[str]) -> None:
    """
    Log a one-time warning when no GitHub token is available.

    This function is thread-safe and ensures the warning is emitted at most once per session/process.

    Parameters:
        effective_token: The resolved GitHub token, or `None` if no token is available.
    """
    if not effective_token:
        global _token_warning_shown
        with _token_warning_lock:
            if not _token_warning_shown:
                logger.debug(
                    "No GITHUB_TOKEN found - using unauthenticated API requests (60/hour limit). "
                    "Requests are cached, so this is fine for normal usage. "
                    "Set GITHUB_TOKEN environment variable or run 'fetchtastic setup github' for higher limits (5000/hour)."
                )
                _token_warning_shown = True


def _get_rate_limit_cache_file() -> str:
    """Get the path to the rate limit cache file."""
    global _rate_limit_cache_file
    if _rate_limit_cache_file is None:
        cache_dir = platformdirs.user_cache_dir("fetchtastic")
        os.makedirs(cache_dir, exist_ok=True)
        _rate_limit_cache_file = os.path.join(cache_dir, "rate_limits.json")
    return _rate_limit_cache_file


def _load_rate_limit_cache() -> None:
    """
    Load persisted rate-limit entries from disk into the in-memory cache if they have not already been loaded.

    Reads the on-disk rate-limit cache, validates its structure, converts stored reset timestamps to datetimes, and retains only entries whose reset time is in the future. Malformed entries, missing files, and I/O or JSON errors are ignored; the function publishes the validated entries into the module cache under a lock to ensure thread-safe one-time initialization.
    """
    global _rate_limit_cache_loaded

    # Fast path without lock
    if _rate_limit_cache_loaded:
        return

    # Guarded re-check with minimal lock time
    with _rate_limit_lock:
        if _rate_limit_cache_loaded:
            return

    # Load cache data outside the lock to avoid holding it during I/O
    cache_file = _get_rate_limit_cache_file()
    loaded: Dict[str, Tuple[int, datetime]] = {}
    try:
        if not os.path.exists(cache_file):
            loaded = {}  # No file; treat as empty
        else:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            # Validate cache structure
            if not isinstance(cache_data, dict):
                loaded = {}  # Invalid structure; treat as empty
            else:
                # Convert string timestamps back to datetime objects (build locally)
                current_time = datetime.now(timezone.utc)
                for cache_key, cache_value in cache_data.items():
                    try:
                        # Validate value structure
                        if (
                            not isinstance(cache_value, (list, tuple))
                            or len(cache_value) != 2
                        ):
                            continue

                        remaining_str, reset_timestamp_str = cache_value
                        remaining = int(remaining_str)
                        reset_timestamp = datetime.fromisoformat(reset_timestamp_str)

                        # Only keep cache entries where reset is in the future
                        if reset_timestamp > current_time:
                            loaded[cache_key] = (remaining, reset_timestamp)
                    except (ValueError, TypeError):
                        continue
    except (OSError, json.JSONDecodeError):
        loaded = {}  # Silently ignore cache loading errors

    # Publish under lock, double-check flag
    with _rate_limit_lock:
        if not _rate_limit_cache_loaded:
            _rate_limit_cache.update(loaded)
            _rate_limit_cache_loaded = True


def _parse_rate_limit_header(header_value: Any) -> Optional[int]:
    """
    Parse an HTTP rate-limit header value into an integer remaining count.

    Accepts numeric strings, integers, or floats and returns their integer representation.
    Non-numeric or otherwise unparsable values return `None`.

    Parameters:
        header_value (Any): The raw header value to parse (commonly a str, int, or float).

    Returns:
        Optional[int]: The parsed integer value if successful, `None` otherwise.
    """
    try:
        if isinstance(header_value, str) and header_value.isdigit():
            return int(header_value)
        elif isinstance(header_value, (int, float)):
            return int(header_value)
    except (ValueError, TypeError):
        pass
    return None


def _save_rate_limit_cache() -> None:
    """
    Persist the in-memory rate-limit cache to the on-disk cache file.

    Serializes cached entries (timestamps as ISO 8601 strings) and writes them atomically via a temporary file replacement. I/O errors during the save are ignored.
    """
    cache_file = _get_rate_limit_cache_file()

    try:
        # Snapshot under lock, then write outside to minimize contention
        with _rate_limit_lock:
            cache_data = {
                cache_key: (remaining, reset_timestamp.isoformat())
                for cache_key, (remaining, reset_timestamp) in _rate_limit_cache.items()
            }

        # Write to a unique temporary file first, then atomically replace
        fd, temp_file = tempfile.mkstemp(
            dir=os.path.dirname(cache_file), prefix="tmp-", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
            os.replace(temp_file, cache_file)
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    except OSError:
        pass  # Silently ignore cache saving errors


def _update_rate_limit(
    token_hash: str, remaining: int, reset_timestamp: Optional[datetime] = None
) -> None:
    """
    Update cached rate-limit information for a specific token and optionally persist the cache to disk.

    Parameters:
        token_hash (str): Short hash identifying the token whose rate-limit is being updated.
        remaining (int): Number of remaining requests reported for the token.
        reset_timestamp (Optional[datetime]): Time when the rate limit resets; if omitted, defaults to one hour from now (timezone-aware).

    Details:
        - Stores (remaining, reset_timestamp) in the in-memory rate-limit cache.
        - Triggers persistence to the on-disk cache if this is a new entry, if `remaining` decreased compared to the cached value, or if the configured save interval has elapsed.
        - Updates the session's last-cache-save timestamp when persisting.
        - Persistence is performed outside the internal lock to avoid deadlocks.
    """
    global _rate_limit_cache, _last_cache_save_time

    now = datetime.now(timezone.utc)
    current_time = now.timestamp()

    # Ensure reset_timestamp is set
    if reset_timestamp is None:
        reset_timestamp = now + timedelta(hours=1)

    assert reset_timestamp is not None  # For type checker

    with _rate_limit_lock:
        # Check if remaining decreased and at least 5 seconds have passed
        should_save = False
        if token_hash in _rate_limit_cache:
            old_remaining, _ = _rate_limit_cache[token_hash]
            # Save if remaining decreased or it's been more than RATE_LIMIT_CACHE_SAVE_INTERVAL seconds
            if (
                remaining < old_remaining
                or (current_time - _last_cache_save_time)
                >= RATE_LIMIT_CACHE_SAVE_INTERVAL
            ):
                should_save = True
        else:
            # Always save on first entry
            should_save = True

        _rate_limit_cache[token_hash] = (remaining, reset_timestamp)

        if should_save:
            _last_cache_save_time = current_time

    # Persist outside the lock to avoid re-entrancy deadlock
    if should_save:
        _save_rate_limit_cache()


def _get_cached_rate_limit(token_hash: str) -> Optional[int]:
    """
    Retrieve the cached remaining GitHub API requests for a token when its reset time is in the future.

    Parameters:
        token_hash (str): The cache key derived from a GitHub token.

    Returns:
        int | None: The cached remaining request count for the token, or `None` if no valid cached entry exists or the reset time has passed.
    """
    global _rate_limit_cache

    with _rate_limit_lock:
        if token_hash in _rate_limit_cache:
            remaining, reset_timestamp = _rate_limit_cache[token_hash]
            if reset_timestamp > datetime.now(timezone.utc):
                return remaining
    return None


def get_rate_limit_info(token_hash: str) -> Optional[Tuple[int, datetime]]:
    """
    Retrieve cached GitHub API rate-limit remaining count and reset time for a token hash.

    Returns:
        (remaining, reset_timestamp) as (int, datetime) if a cached entry exists for the given token hash, `None` otherwise.
    """
    global _rate_limit_cache

    with _rate_limit_lock:
        if token_hash in _rate_limit_cache:
            return _rate_limit_cache[token_hash]
    return None


def clear_rate_limit_cache() -> None:
    """
    Clear the in-memory and on-disk rate-limit cache.

    Clears the process-global rate-limit cache under the internal lock and marks it as not loaded. Also attempts to remove the persisted cache file next to the user cache directory; I/O errors during file removal are logged and ignored.
    """
    global _rate_limit_cache, _rate_limit_cache_loaded

    # Clear cache under lock to avoid races with concurrent readers/writers
    with _rate_limit_lock:
        _rate_limit_cache.clear()
        _rate_limit_cache_loaded = False

    # Also clear the persistent cache file
    try:
        cache_file = _get_rate_limit_cache_file()
        if os.path.exists(cache_file):
            os.remove(cache_file)
            logger.debug("Removed rate limit cache file")
    except IOError as e:
        logger.warning(f"Failed to remove rate limit cache file: {e}")


def make_github_api_request(
    url: str,
    github_token: Optional[str] = None,
    allow_env_token: bool = True,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
    _is_retry: bool = False,
    custom_403_message: Optional[str] = None,
) -> requests.Response:
    """
    Perform a GitHub API GET request with optional token authentication, update in-memory and on-disk rate-limit tracking, and retry once without authentication if token-based auth returns 401.

    Parameters:
        url (str): GitHub API URL to request.
        github_token (Optional[str]): Explicit GitHub token to prefer for Authorization; trimmed before use.
        allow_env_token (bool): If True, allow falling back to the GITHUB_TOKEN environment variable when no explicit token is provided.
        params (Optional[Dict[str, Any]]): Query parameters to include in the request.
        timeout (Optional[int]): Request timeout in seconds; if omitted the module default is used.
        custom_403_message (Optional[str]): Custom message to use when raising on 403 responses; if omitted a default rate-limit message is used.

    Returns:
        requests.Response: The HTTP response returned by GitHub.

    Raises:
        requests.HTTPError: For HTTP error responses (including handled 401/403 conditions where a descriptive message is raised).
        requests.RequestException: For lower-level network or request errors.
    """
    from fetchtastic.log_utils import logger

    # Prepare headers with optional authentication
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": get_user_agent(),
    }

    # Add authentication if token provided
    effective_token = get_effective_github_token(github_token, allow_env_token)
    if effective_token:
        headers["Authorization"] = f"token {effective_token}"
        logger.debug("Using GitHub token for API authentication")
    else:
        logger.debug("No GitHub token available - using unauthenticated API requests")

    # Show warning if no token available (centralized logic)
    _show_token_warning_if_needed(effective_token)

    # Initialize rate limit cache if needed
    global _rate_limit_cache_loaded
    if not _rate_limit_cache_loaded:
        _load_rate_limit_cache()

    # Create token hash for caching
    token_hash = hashlib.sha256((effective_token or "no-token").encode()).hexdigest()[
        :16
    ]
    global _last_rate_limit_token_hash
    _last_rate_limit_token_hash = token_hash

    try:
        # Make the request
        actual_timeout = timeout or GITHUB_API_TIMEOUT
        logger.debug(f"Making GitHub API request: {url}")
        response = requests.get(
            url, timeout=actual_timeout, headers=headers, params=params
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        if (
            not _is_retry
            and e.response is not None
            and e.response.status_code == 401
            and effective_token
        ):
            logger.warning(
                f"GitHub token authentication failed for {url}. Retrying without authentication."
            )
            return make_github_api_request(
                url,
                github_token=None,
                allow_env_token=False,  # Don't try env token on retry
                params=params,
                timeout=timeout,
                _is_retry=True,
                custom_403_message=custom_403_message,
            )
        elif e.response is not None and e.response.status_code == 403:
            rate_limit_remaining = e.response.headers.get("X-RateLimit-Remaining")
            remaining_val = _parse_rate_limit_header(rate_limit_remaining)
            if remaining_val == 0:
                reset_time = e.response.headers.get("X-RateLimit-Reset")
                reset_time_str = (
                    datetime.fromtimestamp(int(reset_time), timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                    if reset_time
                    else "unknown"
                )
                error_msg = (
                    custom_403_message
                    or f"GitHub API rate limit exceeded. Resets at {reset_time_str}. "
                    f"Set GITHUB_TOKEN environment variable for higher rate limits."
                )
            else:
                error_msg = custom_403_message or "GitHub API access forbidden"
            logger.error(error_msg)
            raise requests.HTTPError(error_msg, response=e.response) from None
        else:
            raise
    finally:
        # Small delay to be respectful to GitHub API, even on errors
        time.sleep(API_CALL_DELAY)
        # Track API request statistics and log first requests
        global _api_request_count, _api_auth_used
        global _api_first_auth_logged, _api_first_unauth_logged
        with _api_tracking_lock:
            _api_request_count += 1
            # API request counter increment
            if effective_token:
                _api_auth_used = True

    # Enhanced rate limit tracking and logging
    try:
        # Safely get headers with fallback for missing headers attribute (e.g., in tests)
        resp_headers = getattr(response, "headers", None)

        if resp_headers is None:
            resp_headers = {}
        # CaseInsensitiveDict is not a dict subclass, so check for dict-like behavior
        elif not hasattr(resp_headers, "get"):
            resp_headers = {}

        rl_header = resp_headers.get("X-RateLimit-Remaining")
        rl_reset = resp_headers.get("X-RateLimit-Reset")

        if rl_header is not None:
            remaining = _parse_rate_limit_header(rl_header)
            if remaining is not None:
                # Parse reset timestamp
                reset_timestamp = None
                if rl_reset is not None:
                    try:
                        reset_timestamp = datetime.fromtimestamp(
                            int(rl_reset), timezone.utc
                        )
                    except (ValueError, TypeError):
                        pass  # Keep None if parsing fails

                # Update cache with new rate limit info
                _update_rate_limit(token_hash, remaining, reset_timestamp)

                # Log enhanced rate limit information
                logger.debug(f"GitHub API rate-limit remaining: {remaining}")

                # Add rate limit estimation and warnings
                if remaining <= 10:
                    logger.warning(
                        f"GitHub API rate limit running low: {remaining} requests remaining"
                    )

                # Add reset time information if available
                if rl_reset:
                    try:
                        reset_time = datetime.fromtimestamp(int(rl_reset), timezone.utc)
                        time_until_reset = reset_time - datetime.now(timezone.utc)
                        if time_until_reset.total_seconds() > 0:
                            minutes_until_reset = int(
                                time_until_reset.total_seconds() / 60
                            )
                            logger.debug(
                                f"GitHub API rate limit resets in ~{minutes_until_reset} minutes"
                            )
                    except (ValueError, OSError):
                        pass
                else:
                    # Skip rate limit tracking for invalid values (including Mock objects)
                    logger.debug(f"Invalid rate-limit header value: {rl_header}")
        else:
            # No rate limit info available (might be a different endpoint)
            cached_remaining = _get_cached_rate_limit(token_hash)
            if cached_remaining is not None:
                logger.debug(
                    f"GitHub API rate-limit remaining: ~{cached_remaining} (cached estimate)"
                )
            else:
                logger.debug("No rate limit information available")

    except (KeyError, ValueError, AttributeError) as e:
        logger.debug(f"Could not parse rate-limit headers: {e}")

    return response


def calculate_sha256(file_path: str) -> Optional[str]:
    """
    Compute the SHA-256 hex digest of a file.

    Reads the file in binary mode and streams its contents without loading the whole file into memory.
    Returns the 64-character lowercase hexadecimal digest on success, or None if the file cannot be opened or read (e.g., missing file or permission error).
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
    # Do not attempt to hash directories
    if os.path.isdir(file_path):
        logger.debug("verify_file_integrity called on a directory: %s", file_path)
        return False

    stored_hash = load_file_hash(file_path)
    if not stored_hash:
        # No stored hash, calculate and save it
        current_hash = calculate_sha256(file_path)
        if current_hash:
            save_file_hash(file_path, current_hash)
            logger.debug(f"Generated initial hash for {os.path.basename(file_path)}")
            return True
        # Could not read file to create initial hash; treat as invalid to trigger remediation
        return False

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
    Download a remote file to disk, verify integrity, and atomically install it.

    Streams the URL to a temporary file with retry-capable HTTP requests, validates ZIP archives when applicable, verifies or writes a SHA-256 `.sha256` sidecar, and atomically replaces the destination. If an existing file is already verified it is left in place. Temporary and partially downloaded files are removed on failure; corrupted or mismatched files and their sidecars are removed before re-downloading.

    Parameters:
        url (str): The HTTP(S) URL of the remote file to download.
        download_path (str): Final filesystem path where the downloaded file will be installed.

    Returns:
        bool: `True` if the destination file is present and verified or was downloaded and installed successfully, `False` otherwise.
    """
    # Note: Session is created after pre-checks and closed in finally

    # Check if file exists and is valid (especially for zips)
    if os.path.exists(download_path):
        if download_path.lower().endswith(ZIP_EXTENSION):
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

    temp_path = f"{download_path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
    session = requests.Session()
    response = None  # ensure we can close the Response in finally
    try:
        # Log before session.get()
        logger.debug(
            f"Attempting to download file from URL: {url} to temp path: {temp_path}"
        )
        start_time = time.time()
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

        if download_path.lower().endswith(ZIP_EXTENSION):
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
                    # Force garbage collection to release file handles before attempting move
                    gc.collect()
                    logger.debug(
                        f"Attempting to move temporary file {temp_path} to {download_path} (Windows attempt {i + 1}/{WINDOWS_MAX_REPLACE_RETRIES})"
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
    Determine whether a filename matches any of the provided selection patterns after normalization.

    Compares the filename's normalized base forms (modern and legacy) against each non-empty pattern case-insensitively. For patterns that end with a separator ("-" or "_"), the legacy base is preferred; otherwise both bases are checked. If a pattern uses mixed case, contains a dot, or includes keywords like "release", "apk", "aab", or "fdroid", a punctuation-stripped fallback comparison is attempted. Very short patterns (≤3 characters) are also matched against the punctuation-stripped bases as a last-chance fallback. If `selected_patterns` is None or empty, all filenames match.

    Parameters:
        filename (str): The filename to test.
        selected_patterns (Optional[List[str]]): Iterable of substring patterns to search for; empty or None means match all.

    Returns:
        bool: `true` if any non-empty pattern matches according to the above rules, `false` otherwise.
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
        # For patterns ending with a separator, the legacy form is more reliable
        # because it preserves the separator, which is critical for matching.
        match_found = False
        if pat_lower.endswith(("-", "_")):
            match_found = pat_lower in base_legacy_lower
        else:
            # For other patterns, the modern form is generally preferred,
            # but we check both for backward compatibility.
            match_found = (
                pat_lower in base_modern_lower or pat_lower in base_legacy_lower
            )
        if match_found:
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
