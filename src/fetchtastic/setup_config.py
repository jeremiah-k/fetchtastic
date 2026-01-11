# src/fetchtastic/setup_config.py

import functools
import getpass
import math
import os
import platform
import random
import re
import shutil
import string
import subprocess
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import platformdirs
import yaml  # type: ignore[import-untyped]

from fetchtastic import menu_apk, menu_firmware
from fetchtastic.constants import (
    CONFIG_FILE_NAME,
    CRON_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_CHECK_APK_PRERELEASES,
    DEFAULT_EXTRACTION_PATTERNS,
    DEFAULT_KEEP_LAST_BETA,
    MESHTASTIC_DIR_NAME,
    NTFY_REQUEST_TIMEOUT,
    WINDOWS_SHORTCUT_FILE,
)
from fetchtastic.log_utils import logger

# Recommended default exclude patterns for firmware extraction
# These patterns exclude specialized variants and debug files that most users don't need
# Patterns use fnmatch (glob-style) matching against the base filename
RECOMMENDED_EXCLUDE_PATTERNS = [
    "*.hex",  # hex files (debug/raw files)
    "*tcxo*",  # TCXO related files (crystal oscillator)
    "*s3-core*",  # S3 core files (specific hardware)
    "*request*",  # request files (debug/test files) - NOTE: May be too broad, consider review
    # TODO: This pattern could exclude legitimate firmware assets containing "request" in their names.
    # Consider making this pattern more specific or moving it to optional patterns if false positives are reported.
    "*rak4631_*",  # RAK4631 underscore variants (like rak4631_eink)
    "*heltec_*",  # Heltec underscore variants
    "*tbeam_*",  # T-Beam underscore variants
    "*tlora_*",  # TLORA underscore variants
    "*_tft*",  # TFT display variants
    "*_oled*",  # OLED display variants
    "*_lcd*",  # LCD display variants
    "*_epaper*",  # e-paper display variants
    "*_eink*",  # e-ink display variants
]


def _safe_input(prompt: str, *, default: str = "") -> str:
    """
    Safely get user input with EOFError handling for non-interactive environments.

    Wraps input() to handle EOFError which can occur in non-interactive
    environments (CI, piped input, etc.). When EOFError is raised or when
    the user provides empty input, returns the default value.

    Parameters:
        prompt (str): The prompt string to display to the user.
        default (str): Default value to return if EOFError is raised or input is empty.

    Returns:
        str: User input or default value if EOFError occurred or input is empty.
    """
    try:
        response = input(prompt)
        return response or default
    except (EOFError, KeyboardInterrupt):
        return default


def _crontab_available() -> bool:
    """
    Check whether the system has the 'crontab' command available.

    Returns:
        bool: True if the 'crontab' executable is present on PATH, False otherwise.
    """
    return shutil.which("crontab") is not None


# Decorator for functions that require crontab command
def cron_command_required(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator that ensures the system 'crontab' command is available before running the wrapped function.

    If 'crontab' is not found, logs a warning and the decorated call returns None without invoking the wrapped function. If available, the wrapped function is called with an extra keyword argument `crontab_path` containing the resolved path to the 'crontab' executable.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """
        Ensure the system `crontab` command is available and inject its path into the wrapped function.

        If `crontab` is not present, logs a warning and returns None. Otherwise, calls the wrapped function with the same arguments and an additional keyword argument `crontab_path` containing the path to the `crontab` executable.

        Returns:
            The wrapped function's return value, or `None` if `crontab` is unavailable.
        """
        if not _crontab_available():
            logger.warning(
                "Cron configuration skipped: 'crontab' command not found on this system."
            )
            return None
        crontab_path = shutil.which("crontab")
        if crontab_path is None:
            # Rare edge case: PATH changed between checks
            return None
        return func(*args, crontab_path=crontab_path, **kwargs)

    return wrapper


# Convenience decorator for check functions that should return False when crontab unavailable
def cron_check_command_required(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator for check-style functions that ensures the system 'crontab' command is available before running them.

    When the 'crontab' command is not found, the wrapped function is skipped and `False` is returned; otherwise the wrapped function is invoked with an extra keyword argument `crontab_path` containing the resolved path to the 'crontab' executable.

    Parameters:
        func (callable): The check function to wrap. The wrapped function should accept a `crontab_path` keyword argument.

    Returns:
        callable: A wrapped function that returns `False` if crontab is unavailable, or forwards to `func` with `crontab_path` when available.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """
        Ensure the system 'crontab' command is available and inject its resolved path into the wrapped call.

        If the 'crontab' executable is not found, logs a warning and returns False. If found, adds or overwrites the keyword argument `crontab_path` with the resolved executable path (falls back to the literal string "crontab" if resolution returns a non-string) before invoking the wrapped function.

        Parameters:
            args: Positional arguments forwarded to the wrapped function.
            kwargs: Keyword arguments forwarded to the wrapped function; the `crontab_path` key may be added or overwritten.

        Returns:
            The wrapped function's return value, or `False` if the 'crontab' command is not available.
        """
        if not _crontab_available():
            logger.warning(
                "Cron configuration skipped: 'crontab' command not found on this system."
            )
            return False
        crontab_path = shutil.which("crontab")
        if crontab_path is None:
            logger.warning(
                "Cron configuration skipped: 'crontab' command not found on this system."
            )
            return False
        if not isinstance(crontab_path, str):
            logger.debug(
                "shutil.which returned non-str (%s); falling back to literal 'crontab'.",
                type(crontab_path).__name__,
            )
            crontab_path = "crontab"
        return func(*args, crontab_path=crontab_path, **kwargs)

    return wrapper


# Cron job schedule configurations
CRON_SCHEDULES = {
    "hourly": {"schedule": "0 * * * *", "desc": "hourly"},
    "daily": {"schedule": "0 3 * * *", "desc": "daily at 3 AM"},
}

# Import Windows-specific modules if on Windows
if platform.system() == "Windows":
    try:
        import winshell  # type: ignore[import-not-found]

        WINDOWS_MODULES_AVAILABLE = True
    except ImportError:
        WINDOWS_MODULES_AVAILABLE = False
        print(
            "Windows detected. For full Windows integration, install optional dependencies:"
        )
        print("pipx install -e .[win]")
        print("or if using pip: pip install fetchtastic[win]")
else:
    WINDOWS_MODULES_AVAILABLE = False

# Windows Start Menu folder for Fetchtastic
WINDOWS_START_MENU_FOLDER = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Fetchtastic",
)

# Supported setup sections for partial reconfiguration
SETUP_SECTION_CHOICES: Set[str] = {
    "base",  # Base directory and environment-specific options
    "android",  # Android APK download preferences
    "firmware",  # Firmware download preferences (including prereleases/extraction)
    "notifications",  # NTFY configuration
    "automation",  # Cron/startup automation choices
    "github",  # GitHub API token configuration
}

SECTION_SHORTCUTS = {
    "b": "base",
    "a": "android",
    "f": "firmware",
    "n": "notifications",
    "m": "automation",
    "g": "github",
}


def is_termux() -> bool:
    """
    Detect whether the current process is running inside Termux.

    Checks the `PREFIX` environment variable for the Termux identifier.

    Returns:
        True if Termux is detected, False otherwise.
    """
    return "com.termux" in os.environ.get("PREFIX", "")


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """
    Normalize a variety of common truthy and falsey representations to a boolean.

    Accepts booleans, integers, and common string forms such as "y"/"yes", "n"/"no",
    "true"/"false", "1"/"0", and "on"/"off". If the input cannot be interpreted,
    returns the provided default.

    Parameters:
        value (Any): The value to coerce to bool.
        default (bool): Value to return when `value` is unrecognized (defaults to False).

    Returns:
        bool: `True` if `value` represents truth, `False` if it represents falsehood,
        or `default` when the representation is unrecognized.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if re.fullmatch(r"[+-]?\d+", normalized or ""):
            return int(normalized) != 0
        if normalized in {"y", "yes", "true", "t", "1", "on"}:
            return True
        if normalized in {"n", "no", "false", "f", "0", "off"}:
            return False
    return default


def _load_yaml_mapping(path: str) -> Optional[Dict[str, Any]]:
    """
    Load a YAML mapping from the given file path.

    Parses the file as YAML and returns the resulting mapping. Returns None if the file cannot be read, the content cannot be parsed as YAML, or the parsed value is not a mapping.

    Parameters:
        path (str): Path to the YAML file to load.

    Returns:
        dict | None: Parsed mapping on success, or `None` on read/parse error or if the YAML root is not a mapping.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        logger.exception("Error loading config %s: %s", path, exc)
        return None
    if config is None:
        config = {}
    if not isinstance(config, dict):
        logger.error(
            "Invalid config %s: expected YAML mapping, got %s",
            path,
            type(config).__name__,
        )
        return None
    return config


def is_fetchtastic_installed_via_pip() -> bool:
    """
    Check whether Fetchtastic appears among the packages reported by the system `pip` command.

    If the `pip` command is unavailable or the check fails, the function returns `false`.

    Returns:
        `true` if `fetchtastic` appears in the output of `pip list`, `false` otherwise.
    """
    try:
        # Check if fetchtastic is in pip list
        result = subprocess.run(
            ["pip", "list"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return "fetchtastic" in result.stdout.lower()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        # pip command not found or failed to execute
        pass
    return False


def is_fetchtastic_installed_via_pipx() -> bool:
    """
    Check if fetchtastic is installed via pipx.

    Returns:
        bool: True if installed via pipx, False otherwise
    """
    try:
        # Check if fetchtastic is in pipx list
        result = subprocess.run(
            ["pipx", "list"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return "fetchtastic" in result.stdout.lower()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        # pipx command not found or failed to execute
        pass
    return False


def get_fetchtastic_installation_method() -> str:
    """
    Determine the method used to install Fetchtastic.

    Returns:
        str: 'pipx' if installed via pipx, 'pip' if installed via pip, 'unknown' otherwise.
    """
    if is_fetchtastic_installed_via_pipx():
        return "pipx"
    elif is_fetchtastic_installed_via_pip():
        return "pip"
    else:
        return "unknown"


def migrate_pip_to_pipx() -> bool:
    """
    Migrate a Termux-installed Fetchtastic package from pip to pipx while preserving the user's configuration.

    This operation is interactive and only runs on Termux. If Fetchtastic is not installed via pip, the function exits successfully without making changes. On success, the user's configuration file is preserved and restored when possible.

    Returns:
        bool: `True` if migration completed successfully, `False` otherwise.
    """
    if not is_termux():
        print("Migration is only supported in Termux.")
        return False

    if get_fetchtastic_installation_method() != "pip":
        print("Fetchtastic is not installed via pip. No migration needed.")
        return True

    print("\n" + "=" * 50)
    print("MIGRATING FROM PIP TO PIPX")
    print("=" * 50)
    print("We recommend using pipx for better package isolation.")
    print("This will:")
    print("1. Backup your current configuration")
    print("2. Install pipx if not available")
    print("3. Uninstall fetchtastic from pip")
    print("4. Install fetchtastic with pipx")
    print("5. Restore your configuration")
    print()

    migrate = _safe_input(
        "Do you want to migrate to pipx? [y/n] (default: yes): ",
        default="y",
    )
    if not _coerce_bool(migrate, default=True):
        print("Migration cancelled. You can continue using pip, but we recommend pipx.")
        return False

    try:
        # Step 1: Backup configuration
        print("1. Backing up configuration...")
        config_backup = None
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config_backup = f.read()
                print("   Configuration backed up.")
            except (OSError, UnicodeDecodeError) as exc:
                print(f"   Error: Could not back up configuration: {exc}")
                print("   Aborting migration to prevent data loss.")
                return False
        else:
            print("   No existing configuration found.")

        # Step 2: Install pipx if needed
        print("2. Ensuring pipx is available...")
        pipx_path = shutil.which("pipx")
        if not pipx_path:
            print("   Installing pipx...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "pipx"],
                capture_output=True,
                text=True,
                timeout=CRON_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                print(f"   Failed to install pipx: {result.stderr}")
                return False

            # Ensure pipx path
            result = subprocess.run(
                [sys.executable, "-m", "pipx", "ensurepath"],
                capture_output=True,
                text=True,
                timeout=CRON_COMMAND_TIMEOUT_SECONDS,
            )
            print("   pipx installed successfully.")
            pipx_path = shutil.which("pipx")
            if not pipx_path:
                local_pipx = os.path.expanduser("~/.local/bin/pipx")
                if os.path.exists(local_pipx):
                    pipx_path = local_pipx
            if not pipx_path:
                print("   pipx executable not found after installation.")
                return False
        else:
            print("   pipx is already available.")

        # Step 3: Uninstall from pip
        print("3. Uninstalling fetchtastic from pip...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "fetchtastic", "-y"],
            capture_output=True,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            print(f"   Warning: Failed to uninstall from pip: {result.stderr}")
        else:
            print("   Uninstalled from pip successfully.")

        # Step 4: Install with pipx
        print("4. Installing fetchtastic with pipx...")
        result = subprocess.run(
            [pipx_path, "install", "fetchtastic"],
            capture_output=True,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            print(f"   Failed to install with pipx: {result.stderr}")
            # Try to restore pip installation
            print("   Attempting to restore pip installation...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "fetchtastic"],
                check=False,
                capture_output=True,
                text=True,
                timeout=CRON_COMMAND_TIMEOUT_SECONDS,
            )
            return False
        else:
            print("   Installed with pipx successfully.")

        # Step 5: Restore configuration (best-effort)
        if config_backup:
            print("5. Restoring configuration...")
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(config_backup)
                print("   Configuration restored.")
            except OSError as exc:
                print(f"   Warning: Could not restore configuration: {exc}")

        print("\n" + "=" * 50)
        print("MIGRATION COMPLETED SUCCESSFULLY!")
        print("=" * 50)
        print("Fetchtastic is now installed via pipx.")
        print("You can now use 'pipx upgrade fetchtastic' to upgrade.")
        print("Your configuration has been preserved.")
        print()

        return True

    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
        print(f"Migration failed with error: {e}")
        print(
            "Your installation may be in a partial state; re-run migration or reinstall manually if needed."
        )
        return False


def get_platform() -> str:
    """
    Determine the running platform identifier.

    Returns:
        str: "termux" if running in Termux, "mac" for macOS, "linux" for Linux, or "unknown" if none of the above.
    """
    if is_termux():
        return "termux"
    elif platform.system() == "Darwin":
        return "mac"
    elif platform.system() == "Linux":
        return "linux"
    else:
        return "unknown"


def get_downloads_dir() -> str:
    """
    Determine the default Downloads directory for the current platform.

    For Termux this resolves to the expanded path "~/storage/downloads". On other platforms the function prefers "~/Downloads", then "~/Download", and falls back to the user's home directory if neither exists.

    Returns:
        str: Path to the selected downloads directory.
    """
    # For Termux, use ~/storage/downloads
    if is_termux():
        return os.path.expanduser("~/storage/downloads")
    # For other environments, use standard Downloads directories
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, "Downloads")
    if os.path.exists(downloads_dir):
        return downloads_dir
    downloads_dir = os.path.join(home_dir, "Download")
    if os.path.exists(downloads_dir):
        return downloads_dir
    # Fallback to home directory
    return home_dir


# Default directories
DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_BASE_DIR = os.path.join(DOWNLOADS_DIR, MESHTASTIC_DIR_NAME)

# Get the config directory using platformdirs
CONFIG_DIR = platformdirs.user_config_dir("fetchtastic")

# Old config file location (for migration)
OLD_CONFIG_FILE = os.path.join(DEFAULT_BASE_DIR, CONFIG_FILE_NAME)

# New config file location using platformdirs
CONFIG_FILE = os.path.join(CONFIG_DIR, CONFIG_FILE_NAME)

# These will be set during setup or when loading config
BASE_DIR = DEFAULT_BASE_DIR


def config_exists(directory: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Check for the presence of a Fetchtastic configuration file and report its path.

    Parameters:
        directory (Optional[str]): If provided, look for CONFIG_FILE_NAME inside this directory.
            If omitted, check the canonical CONFIG_FILE location first, then the legacy OLD_CONFIG_FILE.

    Returns:
        Tuple[bool, Optional[str]]: `True` if a configuration file was found, `False` otherwise;
        the second element is the full path to the found configuration file, or `None` if not found.
    """
    if directory:
        config_path = os.path.join(directory, CONFIG_FILE_NAME)
        if os.path.exists(config_path):
            return True, config_path
        return False, None

    # Check new location first
    if os.path.exists(CONFIG_FILE):
        return True, CONFIG_FILE

    # Then check old location
    if os.path.exists(OLD_CONFIG_FILE):
        return True, OLD_CONFIG_FILE

    return False, None


def check_storage_setup() -> bool:
    """
    Verify that Termux storage and the Downloads directory exist and are writable.

    If not running in an interactive terminal or if running under CI, the function will not attempt setup and returns False. In interactive Termux environments, it may invoke setup_storage() and prompt the user to grant storage permissions, waiting for confirmation before re-checking.

    Returns:
        bool: `True` if storage access and the Downloads directory are available and writable, `False` otherwise.

    Side effects:
        - May call `setup_storage()`.
        - May block for interactive user input while awaiting permission grant.
    """
    # Check if the Termux storage directory and Downloads are set up and writable
    storage_dir = os.path.expanduser("~/storage")
    storage_downloads = os.path.expanduser("~/storage/downloads")

    if not sys.stdin.isatty() or os.environ.get("CI"):
        print(
            "Termux storage setup requires an interactive terminal; aborting storage setup checks."
        )
        return False

    while True:
        if (
            os.path.exists(storage_dir)
            and os.path.exists(storage_downloads)
            and os.access(storage_downloads, os.W_OK)
        ):
            print("Termux storage access is already set up.")
            return True
        else:
            print("Termux storage access is not set up or permission was denied.")
            # Run termux-setup-storage
            setup_storage()
            print("Please grant storage permissions when prompted.")
            _safe_input("Press Enter after granting storage permissions to continue...")
            # Re-check if storage is set up
            continue


def _prompt_for_setup_sections() -> Optional[Set[str]]:
    """
    Prompt the user to choose which setup sections to run when an existing configuration is detected.

    Displays the available setup sections with single-letter shortcuts and accepts a comma/space/semicolon-separated
    selection. Pressing ENTER (an empty response) or entering one of the keywords "all", "full", or "everything"
    signals a full run and the function returns None. Entering "q", "quit", "x", or "exit" returns an empty set to
    signal cancellation. Shortcuts defined in SECTION_SHORTCUTS and full section names from SETUP_SECTION_CHOICES are
    accepted; invalid tokens cause the prompt to repeat until a valid selection is given.

    Returns:
        Optional[Set[str]]: A set of chosen section names (subset of SETUP_SECTION_CHOICES), or None to indicate
        the full setup should be run.
    """

    print(
        "\nExisting configuration detected. You can re-run the full wizard or update specific areas."
    )
    print(
        "Press ENTER to run the full setup, or choose one or more sections (comma separated):"
    )
    print("  [b] base           — base directory and general settings")
    print("  [a] android        — Android APK download preferences")
    print("  [f] firmware       — firmware download preferences")
    print("  [n] notifications  — NTFY server/topic settings")
    print("  [m] automation     — scheduled/automatic execution options")
    print("  [g] github         — GitHub API token (rate-limit boost)")
    print("  [q] quit           — exit setup without changes")

    while True:
        response = _safe_input(
            "Selection (examples: f, android; default: full setup, q to quit): ",
            default="",
        ).strip()
        if not response:
            return None

        lowered_response = response.strip().lower()
        if lowered_response in {"q", "quit", "x", "exit"}:
            return set()

        tokens = [tok for tok in re.split(r"[\s,;]+", response) if tok]
        selected: Set[str] = set()
        for token in tokens:
            lowered = token.strip().lower()
            if not lowered:
                continue
            if lowered in {"all", "full", "everything"}:
                return None
            if lowered in SECTION_SHORTCUTS:
                selected.add(SECTION_SHORTCUTS[lowered])
                continue
            if lowered in SETUP_SECTION_CHOICES:
                selected.add(lowered)
                continue
            print(
                f"Unrecognised section '{token}'. Please choose from the listed options."
            )
            selected.clear()
            break

        if selected:
            return selected


def _disable_asset_downloads(
    config: Dict[str, Any], asset_type: str, message: Optional[str] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    Disable downloads for the given asset type and clear related configuration keys.

    Mutates and returns the provided configuration mapping, clears selected-asset lists, and disables related prerelease checks. Prints the provided message or a sensible default.

    Parameters:
        config (Dict[str, Any]): Configuration dictionary to update in place and return.
        asset_type (str): Asset type to disable; expected values include "firmware" or "APK".
        message (Optional[str]): Message to print to the user; if None a default message is printed.

    Returns:
        Tuple[Dict[str, Any], bool]: The (possibly mutated) configuration dictionary and `False` to indicate asset downloads are disabled.
    """
    if message is None:
        asset_plural = {"firmware": "Firmware", "APK": "APKs"}
        message = f"No {asset_type} assets selected. {asset_plural.get(asset_type, asset_type)} will not be downloaded."
    print(message)
    config["SAVE_FIRMWARE" if asset_type == "firmware" else "SAVE_APKS"] = False
    config[
        (
            "SELECTED_FIRMWARE_ASSETS"
            if asset_type == "firmware"
            else "SELECTED_APK_ASSETS"
        )
    ] = []
    if asset_type == "firmware":
        config["CHECK_PRERELEASES"] = False
        config["SELECTED_PRERELEASE_ASSETS"] = []
    else:
        config["CHECK_APK_PRERELEASES"] = False
    return config, False


def _setup_downloads(
    config: Dict[str, Any], is_partial_run: bool, wants: Callable[[str], bool]
) -> Tuple[Dict[str, Any], bool, bool]:
    """
    Configure which asset types (Android APKs and firmware) should be downloaded and update the provided configuration accordingly.

    Updates the config in place with keys such as "SAVE_APKS", "SAVE_FIRMWARE", and, when asset selection menus run, "SELECTED_APK_ASSETS", "SELECTED_FIRMWARE_ASSETS", "CHECK_PRERELEASES", "CHECK_APK_PRERELEASES", and "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES". Prompts the user as needed (or reuses existing values during a partial run) and may disable downloads if no assets are selected.

    Parameters:
        config (dict): Mutable configuration dictionary to update.
        is_partial_run (bool): When True, only prompt sections for which wants(section) is True and prefer existing config defaults.
        wants (Callable[[str], bool]): Callable that accepts a section name (for example "android" or "firmware") and returns True when that section should be processed in this run.

    Returns:
        tuple[dict, bool, bool]: (updated_config, save_apks, save_firmware) where `save_apks` and `save_firmware` indicate whether APKs and firmware, respectively, will be downloaded.
    """
    # Prompt to save APKs, firmware, or both
    if not is_partial_run:
        save_choice = (
            _safe_input(
                "Would you like to download APKs, firmware, or both? [a/f/b] (default: both): ",
                default="both",
            )
            .strip()
            .lower()
            or "both"
        )
        if save_choice == "a":
            save_apks = True
            save_firmware = False
        elif save_choice == "f":
            save_apks = False
            save_firmware = True
        else:
            save_apks = True
            save_firmware = True
    else:
        save_apks = _coerce_bool(config.get("SAVE_APKS", False))
        save_firmware = _coerce_bool(config.get("SAVE_FIRMWARE", False))
        if wants("android"):
            current_apk_default = "y" if save_apks else "n"
            choice = (
                _safe_input(
                    f"Download Android APKs? [y/n] (current: {current_apk_default}): ",
                    default=current_apk_default,
                )
                or current_apk_default
            )
            save_apks = _coerce_bool(choice)
        if wants("firmware"):
            current_fw_default = "y" if save_firmware else "n"
            choice = (
                _safe_input(
                    f"Download firmware releases? [y/n] (current: {current_fw_default}): ",
                    default=current_fw_default,
                )
                or current_fw_default
            )
            save_firmware = _coerce_bool(choice)

    config["SAVE_APKS"] = save_apks
    config["SAVE_FIRMWARE"] = save_firmware
    if not save_firmware and (not is_partial_run or wants("firmware")):
        config["CHECK_PRERELEASES"] = False
        config["SELECTED_PRERELEASE_ASSETS"] = []

    if save_firmware and (not is_partial_run or wants("firmware")):
        rerun_menu = True
        if is_partial_run:
            if config.get("SELECTED_FIRMWARE_ASSETS"):
                rerun_menu_choice = _safe_input(
                    "Re-run the firmware asset selection menu? [y/n] (default: yes): ",
                    default="y",
                )
                if not _coerce_bool(rerun_menu_choice, default=True):
                    rerun_menu = False
        if rerun_menu:
            firmware_selection = menu_firmware.run_menu()
            selected_assets = (
                firmware_selection.get("selected_assets")
                if isinstance(firmware_selection, dict)
                else None
            )
            if not selected_assets:
                config, save_firmware = _disable_asset_downloads(config, "firmware")
            else:
                config["SELECTED_FIRMWARE_ASSETS"] = selected_assets
        elif not config.get("SELECTED_FIRMWARE_ASSETS"):
            config, save_firmware = _disable_asset_downloads(
                config,
                "firmware",
                "No existing firmware selection found. Firmware will not be downloaded.",
            )

    # --- Firmware Pre-release Configuration ---
    if save_firmware and (not is_partial_run or wants("firmware")):
        check_prereleases_current = _coerce_bool(config.get("CHECK_PRERELEASES", False))
        check_prereleases_default = "y" if check_prereleases_current else "n"
        check_prereleases_input = _safe_input(
            f"\nWould you like to check for and download pre-release firmware from meshtastic.github.io? [y/n] (default: {check_prereleases_default}): ",
            default=check_prereleases_default,
        )
        config["CHECK_PRERELEASES"] = _coerce_bool(check_prereleases_input)

    if save_apks and (not is_partial_run or wants("android")):
        rerun_menu = True
        if is_partial_run:
            if config.get("SELECTED_APK_ASSETS"):
                rerun_menu_choice = _safe_input(
                    "Re-run the Android APK selection menu? [y/n] (default: yes): ",
                    default="y",
                )
                if not _coerce_bool(rerun_menu_choice, default=True):
                    rerun_menu = False
        if rerun_menu:
            apk_selection = menu_apk.run_menu()
            selected_assets = (
                apk_selection.get("selected_assets")
                if isinstance(apk_selection, dict)
                else None
            )
            if not selected_assets:
                config, save_apks = _disable_asset_downloads(config, "APK")
            else:
                config["SELECTED_APK_ASSETS"] = selected_assets
        elif not config.get("SELECTED_APK_ASSETS"):
            config, save_apks = _disable_asset_downloads(
                config,
                "APK",
                "No existing APK selection found. APKs will not be downloaded.",
            )

    # --- APK Pre-release Configuration ---
    if save_apks and (not is_partial_run or wants("android")):
        check_apk_prereleases_current = _coerce_bool(
            config.get("CHECK_APK_PRERELEASES", DEFAULT_CHECK_APK_PRERELEASES)
        )  # Default: True. APK prereleases are typically more stable than firmware prereleases and safer to enable by default.
        check_apk_prereleases_default = "yes" if check_apk_prereleases_current else "no"
        check_apk_prereleases_input = _safe_input(
            f"\nWould you like to check for and download pre-release APKs from GitHub? [y/n] (default: {check_apk_prereleases_default}): ",
            default=check_apk_prereleases_default,
        )
        config["CHECK_APK_PRERELEASES"] = _coerce_bool(check_apk_prereleases_input)

    # --- Channel Suffix Configuration ---
    if save_firmware:
        if not is_partial_run or wants("firmware"):
            add_channel_suffixes_current = _coerce_bool(
                config.get("ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES", True)
            )
            add_channel_suffixes_default = (
                "yes" if add_channel_suffixes_current else "no"
            )
            add_channel_suffixes_input = _safe_input(
                f"\nWould you like to add -alpha/-beta/-rc suffixes to release directories (e.g., v1.0.0-alpha)? [y/n] (default: {add_channel_suffixes_default}): ",
                default=add_channel_suffixes_default,
            )
            config["ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES"] = _coerce_bool(
                add_channel_suffixes_input
            )

    # If both save_apks and save_firmware are False, inform the user and exit setup.
    # During partial runs that only update non-download sections (e.g. automation),
    # allow setup to proceed even when downloads are disabled.
    if (
        not save_apks
        and not save_firmware
        and (not is_partial_run or wants("android") or wants("firmware"))
    ):
        print("Please select at least one type of asset to download (APK or firmware).")
        print("Run 'fetchtastic setup' again and select at least one asset.")
        return config, save_apks, save_firmware

    return config, save_apks, save_firmware


def _setup_android(
    config: Dict[str, Any], is_first_run: bool, default_versions: int
) -> Dict[str, Any]:
    """
    Prompt the user for how many Android APK versions to keep and store that value in the configuration.

    Prompts with first-run or regular phrasing based on is_first_run, parses the user's input as an integer, and updates config["ANDROID_VERSIONS_TO_KEEP"]. If the config already contains a value, it is used as the prompt default; otherwise default_versions is used. On invalid input, the existing numeric value is retained.

    Parameters:
        config (dict): Configuration mapping to read and update; the function sets "ANDROID_VERSIONS_TO_KEEP" in-place.
        is_first_run (bool): If True, use first-run wording in the prompt.
        default_versions (int): Fallback number to use when the config does not already contain a value.

    Returns:
        dict: The updated configuration dictionary with "ANDROID_VERSIONS_TO_KEEP" set to an integer.
    """
    current_versions = config.get("ANDROID_VERSIONS_TO_KEEP", default_versions)
    if is_first_run:
        prompt_text = f"How many versions of the Android app would you like to keep? (default is {current_versions}): "
    else:
        prompt_text = f"How many versions of the Android app would you like to keep? (current: {current_versions}): "
    raw = _safe_input(prompt_text, default=str(current_versions)).strip() or str(
        current_versions
    )
    try:
        config["ANDROID_VERSIONS_TO_KEEP"] = int(raw)
    except ValueError:
        print("Invalid number — keeping current value.")
        config["ANDROID_VERSIONS_TO_KEEP"] = int(current_versions)
    return config


def configure_exclude_patterns(_config: Dict[str, Any]) -> List[str]:
    """
    Prompt the user to select firmware exclude patterns and return the final list.

    In interactive mode this offers the recommended defaults, allows adding extra patterns,
    or accepts a custom space-separated list. In non-interactive environments (CI or when
    stdin is not a TTY) the recommended patterns are returned automatically. Input is
    normalized by trimming whitespace, removing empty entries, and deduplicating while
    preserving order. This function does not mutate or persist the provided config.

    Parameters:
        _config (dict): Reserved for compatibility; not modified or inspected.

    Returns:
        List[str]: The ordered list of exclude patterns selected by the user.
    """
    # In non-interactive environments, use recommended defaults
    if not sys.stdin.isatty() or os.environ.get("CI"):
        print("Using recommended exclude patterns (non-interactive mode).")
        return RECOMMENDED_EXCLUDE_PATTERNS.copy()

    print("\n--- Exclude Pattern Configuration ---")
    print(
        "Some firmware files are specialized variants (like display-specific variants and debug files)"
    )
    print("that most users don't need. We can exclude these automatically.")

    # Offer recommended defaults
    recommended_str = " ".join(RECOMMENDED_EXCLUDE_PATTERNS)
    print(f"Recommended exclude patterns: {recommended_str}")
    use_defaults_default = "yes"
    use_defaults = _coerce_bool(
        _safe_input(
            "If you use any of these variants, answer no. Exclude these patterns? [y/n] "
            f"(default: {use_defaults_default}): ",
            default=use_defaults_default,
        )
        or use_defaults_default,
        default=True,
    )

    if use_defaults:
        # Start with recommended patterns
        exclude_patterns = RECOMMENDED_EXCLUDE_PATTERNS.copy()

        # Ask for additional patterns
        add_more_default = "no"
        add_more = _coerce_bool(
            _safe_input(
                f"Would you like to add any additional exclude patterns? [y/n] (default: {add_more_default}): ",
                default=add_more_default,
            ),
            default=False,
        )

        if add_more:
            additional_patterns = _safe_input(
                "Enter additional patterns (space-separated): ", default=""
            ).strip()
            if additional_patterns:
                exclude_patterns.extend(additional_patterns.split())
    else:
        # User doesn't want defaults, get custom patterns
        custom_patterns = _safe_input(
            "Enter your exclude patterns (space-separated, or press Enter for none): ",
            default="",
        ).strip()
        if custom_patterns:
            exclude_patterns = custom_patterns.split()
        else:
            exclude_patterns = []

    # Normalize and de-duplicate while preserving order
    stripped_patterns = [p.strip() for p in exclude_patterns if p.strip()]
    exclude_patterns = list(dict.fromkeys(stripped_patterns))
    return exclude_patterns


def _setup_firmware(
    config: Dict[str, Any], is_first_run: bool, default_versions: int
) -> Dict[str, Any]:
    """
    Configure firmware retention, automatic extraction, exclusion patterns, and prerelease handling in the provided config.

    Parameters:
        config (Dict[str, Any]): Configuration mapping to read defaults from and write firmware-related keys into.
        is_first_run (bool): When True, prompt wording and defaults are oriented for a first-time setup.
        default_versions (int): Default number of firmware versions to retain when the config does not specify one.

    Returns:
        Dict[str, Any]: The same config object updated with firmware-related keys, including:
            - FIRMWARE_VERSIONS_TO_KEEP: number of firmware versions to retain
            - AUTO_EXTRACT: whether automatic extraction from firmware archives is enabled
            - EXTRACT_PATTERNS: list of patterns/keywords to extract from archives
            - EXCLUDE_PATTERNS: list of patterns to exclude from extraction
            - CHECK_PRERELEASES: whether to check for pre-release firmware
            - SELECTED_PRERELEASE_ASSETS: list of asset patterns selected for pre-release downloads
    """

    # Prompt for firmware versions to keep
    current_versions = config.get("FIRMWARE_VERSIONS_TO_KEEP", default_versions)
    if is_first_run:
        prompt_text = f"How many versions of the firmware would you like to keep? (default is {current_versions}): "
    else:
        prompt_text = f"How many versions of the firmware would you like to keep? (current: {current_versions}): "
    raw = _safe_input(prompt_text, default=str(current_versions)).strip() or str(
        current_versions
    )
    try:
        config["FIRMWARE_VERSIONS_TO_KEEP"] = int(raw)
    except ValueError:
        print("Invalid number — keeping current value.")
        config["FIRMWARE_VERSIONS_TO_KEEP"] = int(current_versions)

    # Prompt for keeping last beta
    # For non-interactive/CI runs, keep the existing value/default without auto-enabling.
    # For new interactive setups, default to yes to nudge users toward this useful feature.
    keep_last_beta_current = _coerce_bool(
        config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
    )
    non_interactive = not sys.stdin.isatty() or os.environ.get("CI")
    if non_interactive:
        config["KEEP_LAST_BETA"] = keep_last_beta_current
    else:
        keep_last_beta_default_bool = is_first_run or keep_last_beta_current
        keep_last_beta_default = "yes" if keep_last_beta_default_bool else "no"
        keep_last_beta_input = _safe_input(
            f"Would you like to always keep the most recent beta firmware release? [y/n] (default: {keep_last_beta_default}): ",
            default=keep_last_beta_default,
        ).strip()
        config["KEEP_LAST_BETA"] = _coerce_bool(
            keep_last_beta_input, default=keep_last_beta_default_bool
        )

    # Prompt for automatic extraction
    auto_extract_current = _coerce_bool(config.get("AUTO_EXTRACT", False))
    auto_extract_default = "yes" if auto_extract_current else "no"
    auto_extract_input = _safe_input(
        f"Would you like to automatically extract specific files from firmware zip archives? [y/n] (default: {auto_extract_default}): ",
        default=auto_extract_default,
    ).strip()
    auto_extract = _coerce_bool(
        auto_extract_input or auto_extract_default,
        default=auto_extract_current,
    )
    config["AUTO_EXTRACT"] = auto_extract

    if config["AUTO_EXTRACT"]:
        while True:
            # --- File Extraction Configuration ---
            print("\n--- File Extraction Configuration ---")
            print("Configure which files to extract from downloaded firmware archives.")
            print("\nTips for precise selection:")
            print(
                "- Use the separator seen in filenames to target a family (e.g., 'rak4631-' vs 'rak4631_')."
            )
            print(
                "- 'rak4631-' matches base RAK4631 files (e.g., firmware-rak4631-...), "
                "while 'rak4631_' matches underscore variants (e.g., firmware-rak4631_eink-...)."
            )
            print(f"- Example keywords: {' '.join(DEFAULT_EXTRACTION_PATTERNS)}")
            print(
                "- You can re-run 'fetchtastic setup' anytime to adjust your patterns.\n"
            )

            print(
                "Enter the keywords to match for extraction from the firmware zip files, separated by spaces."
            )

            current_patterns = config.get("EXTRACT_PATTERNS")
            if current_patterns is None:
                current_patterns = []
            elif isinstance(current_patterns, str):
                current_patterns = current_patterns.split()
            elif isinstance(current_patterns, (list, tuple, set)):
                current_patterns = [str(item) for item in current_patterns]
            else:
                logger.warning(
                    "Unexpected type for EXTRACT_PATTERNS: %s. Treating as empty.",
                    type(current_patterns).__name__,
                )
                current_patterns = []
            config["EXTRACT_PATTERNS"] = current_patterns
            if current_patterns:
                print(f"Current patterns: {' '.join(current_patterns)}")
                keep_patterns_default = "yes"
                keep_patterns_input = _safe_input(
                    f"Do you want to keep current extraction patterns? [y/n] (default: {keep_patterns_default}): ",
                    default=keep_patterns_default,
                )
                if not _coerce_bool(
                    keep_patterns_input or keep_patterns_default,
                    default=True,
                ):
                    current_patterns = []  # Clear to prompt for new ones

            if not current_patterns:
                extract_patterns_input = _safe_input(
                    "Extraction patterns: ", default=""
                ).strip()
                if extract_patterns_input:
                    config["EXTRACT_PATTERNS"] = extract_patterns_input.split()
                    print(f"Extraction patterns set to: {extract_patterns_input}")
                else:
                    # User entered no patterns, so disable auto-extract
                    config["AUTO_EXTRACT"] = False
                    config["EXTRACT_PATTERNS"] = []
                    print("No extraction patterns provided; disabling auto-extract.")
                    break

            if config.get("AUTO_EXTRACT") and config.get("EXTRACT_PATTERNS"):
                exclude_patterns = configure_exclude_patterns(config)
            else:
                exclude_patterns = []

            extraction_str = " ".join(config.get("EXTRACT_PATTERNS", [])) or "(none)"
            exclude_str = " ".join(exclude_patterns) or "(none)"
            print(f"\nExtraction patterns: {extraction_str}")
            print(f"Exclude patterns: {exclude_str}")
            confirm_default = "yes"
            confirm = _coerce_bool(
                _safe_input(
                    f"Is this correct? [y/n] (default: {confirm_default}): ",
                    default=confirm_default,
                )
                or confirm_default,
                default=True,
            )
            if confirm:
                config["EXCLUDE_PATTERNS"] = exclude_patterns
                break

            print("Let's reconfigure the extraction patterns...")
            config["AUTO_EXTRACT"] = True
            config["EXTRACT_PATTERNS"] = []
            config["EXCLUDE_PATTERNS"] = []
    else:
        # If auto-extract is off, clear all related settings
        config["AUTO_EXTRACT"] = False
        config["EXTRACT_PATTERNS"] = []
        config["EXCLUDE_PATTERNS"] = []

    # --- Pre-release Configuration ---
    config["CHECK_PRERELEASES"] = _coerce_bool(config.get("CHECK_PRERELEASES", False))
    if config["CHECK_PRERELEASES"]:
        # Use a copy to avoid aliasing EXTRACT_PATTERNS
        prerelease_patterns = list(config.get("EXTRACT_PATTERNS", []))
        config["SELECTED_PRERELEASE_ASSETS"] = prerelease_patterns

        if prerelease_patterns:
            print(
                f"Using your extraction patterns for pre-release selection: {' '.join(prerelease_patterns)}"
            )
        else:
            # Correct the message to be accurate
            print(
                "No extraction patterns set. No pre-release files will be downloaded."
            )
            print(
                "To select specific pre-release files, first set up extraction patterns."
            )
    else:
        # Prereleases disabled, clear the setting
        config["SELECTED_PRERELEASE_ASSETS"] = []

    return config


def _configure_cron_job(install_crond_needed: bool = False) -> None:
    """
    Prompt the user for a cron frequency and configure a Fetchtastic cron job accordingly.

    If the chosen frequency is not "none", the function will install the Termux crond service first when requested and then create/update the cron job at the selected cadence. If the user selects "none", no cron job is configured and a message is printed.

    Parameters:
        install_crond_needed (bool): If True, install and enable the Termux crond service before configuring the cron job.
    """
    frequency = _prompt_for_cron_frequency()
    if frequency != "none":
        if install_crond_needed:
            install_crond()
        setup_cron_job(frequency)
    else:
        print("Cron job has not been set up.")


def _prompt_for_cron_frequency() -> str:
    """
    Prompt the user to choose a cron frequency for scheduled checks.

    Accepts short or full inputs: 'h'/'hourly', 'd'/'daily', 'n'/'none'. Defaults to 'hourly' when no input is provided.

    Returns:
        str: One of 'hourly', 'daily', or 'none'.
    """
    choices = {
        "h": "hourly",
        "hourly": "hourly",
        "d": "daily",
        "daily": "daily",
        "n": "none",
        "none": "none",
    }
    while True:
        cron_choice = (
            _safe_input(
                "How often should Fetchtastic check for updates? [h/d/n] (h=hourly, d=daily, n=none, default: hourly): ",
                default="h",
            )
            .strip()
            .lower()
            or "h"
        )
        if cron_choice in choices:
            return choices[cron_choice]
        else:
            print(
                f"Invalid choice '{cron_choice}'. Please enter 'h'/'hourly', 'd'/'daily', or 'n'/'none'."
            )


def _setup_automation(
    config: Dict[str, Any], is_partial_run: bool, wants: Callable[[str], bool]
) -> Dict[str, Any]:
    """
    Configure platform-specific automation for Fetchtastic (cron jobs, startup/boot shortcuts).

    Depending on the platform, this function will offer to create, remove, or reconfigure:
    - Windows: a startup shortcut to run Fetchtastic on user login.
    - Termux: a scheduled cron job and an optional boot script to run on device boot.
    - Linux/macOS: a scheduled cron job and an optional reboot/startup cron entry.

    Parameters:
        config (dict): Current configuration dictionary that may be read and updated.
        is_partial_run (bool): If True, only run when the caller indicates the automation section is desired.
        wants (Callable[[str], bool]): Predicate that returns True if a named setup section should be processed.

    Returns:
        dict: The potentially updated configuration dictionary.
    """
    if not is_partial_run or wants("automation"):
        if platform.system() == "Windows":
            # Windows doesn't support cron jobs, but we can offer to create a startup shortcut
            if WINDOWS_MODULES_AVAILABLE:
                # Check if startup shortcut already exists
                startup_folder: str = winshell.startup()
                startup_shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")

                if os.path.exists(startup_shortcut_path):
                    startup_option = _coerce_bool(
                        _safe_input(
                            "Fetchtastic is already set to run at startup. Would you like to remove this? [y/n] (default: no): ",
                            default="n",
                        ),
                        default=False,
                    )
                    if startup_option:
                        try:
                            # Also remove the batch file if it exists
                            batch_dir = os.path.join(CONFIG_DIR, "batch")
                            batch_path = os.path.join(
                                batch_dir, "fetchtastic_startup.bat"
                            )
                            if os.path.exists(batch_path):
                                os.remove(batch_path)

                            # Remove the shortcut
                            os.remove(startup_shortcut_path)
                            print(
                                "✓ Startup shortcut removed. Fetchtastic will no longer run automatically at startup."
                            )
                        except Exception as e:
                            print(f"Failed to remove startup shortcut: {e}")
                            print("You can manually remove it from: " + startup_folder)
                    else:
                        print(
                            "✓ Fetchtastic will continue to run automatically at startup."
                        )
                else:
                    startup_option = _coerce_bool(
                        _safe_input(
                            "Would you like to run Fetchtastic automatically on Windows startup? [y/n] (default: yes): ",
                            default="y",
                        ),
                        default=True,
                    )
                    if startup_option:
                        if create_startup_shortcut():
                            print(
                                "✓ Fetchtastic will now run automatically when Windows starts."
                            )
                        else:
                            print(
                                "Failed to create startup shortcut. You can manually set up Fetchtastic to run at startup."
                            )
                            print(
                                "You can use Windows Task Scheduler or add a shortcut to: "
                                + startup_folder
                            )
                    else:
                        print("Fetchtastic will not run automatically on startup.")
            else:
                # Don't show this message again since we already showed it earlier
                pass
        elif is_termux():
            # Termux: Ask about cron job and boot script individually
            # Check if cron job already exists
            cron_job_exists = check_cron_job_exists()
            if cron_job_exists:
                cron_prompt = _coerce_bool(
                    _safe_input(
                        "A cron job is already set up. Do you want to reconfigure it? [y/n] (default: no): ",
                        default="n",
                    ),
                    default=False,
                )
                if cron_prompt:
                    # First, remove existing cron job
                    remove_cron_job()
                    print("Existing cron job removed for reconfiguration.")

                    # Configure cron job
                    _configure_cron_job(install_crond_needed=True)
                else:
                    print("Cron job configuration left unchanged.")
            else:
                # Configure cron job
                _configure_cron_job(install_crond_needed=True)

            # Check if boot script already exists
            boot_script_exists = check_boot_script_exists()
            if boot_script_exists:
                boot_prompt = _coerce_bool(
                    _safe_input(
                        "A boot script is already set up. Do you want to reconfigure it? [y/n] (default: no): ",
                        default="n",
                    ),
                    default=False,
                )
                if boot_prompt:
                    # First, remove existing boot script
                    remove_boot_script()
                    print("Existing boot script removed for reconfiguration.")

                    # Then set up new boot script
                    setup_boot_script()
                    print("Boot script has been reconfigured.")
                else:
                    print("Boot script configuration left unchanged.")
            else:
                # Ask if the user wants to set up a boot script
                boot_default = "yes"
                setup_boot = _coerce_bool(
                    _safe_input(
                        f"Do you want Fetchtastic to run on device boot? [y/n] (default: {boot_default}): ",
                        default=boot_default,
                    ),
                    default=True,
                )
                if setup_boot:
                    setup_boot_script()
                else:
                    print("Boot script has not been set up.")

        else:
            if not _crontab_available():
                print(
                    "Cron configuration skipped: 'crontab' command not found on this system."
                )
                return config

            # Linux/Mac: Check if any Fetchtastic cron jobs exist
            any_cron_jobs_exist = check_cron_job_exists() or check_any_cron_jobs_exist()
            if any_cron_jobs_exist:
                cron_prompt = _coerce_bool(
                    _safe_input(
                        "Fetchtastic cron jobs are already set up. Do you want to reconfigure them? [y/n] (default: no): ",
                        default="n",
                    ),
                    default=False,
                )
                if cron_prompt:
                    # First, remove existing cron jobs
                    remove_cron_job()
                    remove_reboot_cron_job()
                    print("Existing cron jobs removed for reconfiguration.")

                    # Configure cron job
                    _configure_cron_job(install_crond_needed=False)

                    # Ask if they want to set up a reboot cron job
                    boot_default = "yes"
                    setup_reboot = _coerce_bool(
                        _safe_input(
                            f"Do you want Fetchtastic to run on system startup? [y/n] (default: {boot_default}): ",
                            default=boot_default,
                        ),
                        default=True,
                    )
                    if setup_reboot:
                        setup_reboot_cron_job()
                        print("Reboot cron job has been set up.")
                    else:
                        print("Reboot cron job will not be set up.")
                else:
                    print("Cron job configurations left unchanged.")
            else:
                # No existing cron jobs, ask if they want to set them up
                # Configure cron job
                _configure_cron_job(install_crond_needed=False)

                # Ask if they want to set up a reboot cron job
                boot_default = "yes"
                setup_reboot = _coerce_bool(
                    _safe_input(
                        f"Do you want Fetchtastic to run on system startup? [y/n] (default: {boot_default}): ",
                        default=boot_default,
                    ),
                    default=True,
                )
                if setup_reboot:
                    setup_reboot_cron_job()
                else:
                    print("Reboot cron job has not been set up.")

    return config


def _setup_notifications(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Configure NTFY notifications interactively.

    Prompts the user to enable or disable NTFY-based notifications, collect the NTFY server URL and topic when enabling, and set whether notifications should be sent only for new downloads. Updates the configuration dictionary in place — setting or clearing the keys `NTFY_TOPIC`, `NTFY_SERVER`, `NTFY_REQUEST_TIMEOUT`, and `NOTIFY_ON_DOWNLOAD_ONLY` as appropriate.

    Parameters:
        config (dict): Current configuration dictionary to modify.

    Returns:
        dict: The updated configuration dictionary.
    """

    has_ntfy_config = bool(config.get("NTFY_TOPIC")) and bool(config.get("NTFY_SERVER"))
    notifications_default = "yes" if has_ntfy_config else "no"

    notifications = _coerce_bool(
        _safe_input(
            f"Would you like to set up notifications via NTFY? [y/n] (default: {notifications_default}): ",
            default=notifications_default,
        ),
        default=has_ntfy_config,
    )

    if notifications:
        # Get NTFY server
        current_server = config.get("NTFY_SERVER", "ntfy.sh")
        ntfy_server = (
            _safe_input(
                f"Enter the NTFY server (current: {current_server}): ",
                default=str(current_server),
            ).strip()
            or current_server
        )

        if not ntfy_server.startswith("http://") and not ntfy_server.startswith(
            "https://"
        ):
            ntfy_server = "https://" + ntfy_server

        # Get topic name
        current_topic = config.get("NTFY_TOPIC") or "fetchtastic-" + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=6)
        )

        topic_name = (
            _safe_input(
                f"Enter a unique topic name (current: {current_topic}): ",
                default=str(current_topic),
            ).strip()
            or current_topic
        )

        # Update config
        config["NTFY_TOPIC"] = topic_name
        config["NTFY_SERVER"] = ntfy_server
        config["NTFY_REQUEST_TIMEOUT"] = NTFY_REQUEST_TIMEOUT

        # Display information
        full_topic_url = f"{ntfy_server.rstrip('/')}/{topic_name}"
        print(f"Notifications enabled using topic: {topic_name}")
        if is_termux():
            print("Subscribe by pasting the topic name in the ntfy app.")
        else:
            print(
                "Subscribe by visiting the full topic URL in your browser or ntfy app."
            )
        print(f"Full topic URL: {full_topic_url}")

        # Offer to copy to clipboard
        if is_termux():
            copy_prompt_text = "Do you want to copy the topic name to the clipboard? [y/n] (default: yes): "
            text_to_copy = topic_name
        else:
            copy_prompt_text = "Do you want to copy the topic URL to the clipboard? [y/n] (default: yes): "
            text_to_copy = full_topic_url

        copy_to_clipboard = _safe_input(copy_prompt_text, default="y")
        if _coerce_bool(copy_to_clipboard, default=True):
            success = copy_to_clipboard_func(text_to_copy)
            if success:
                if is_termux():
                    print("Topic name copied to clipboard.")
                else:
                    print("Topic URL copied to clipboard.")
            else:
                print("Failed to copy to clipboard.")

        # Ask if the user wants notifications only when new files are downloaded
        notify_on_download_only_default = (
            "yes" if config.get("NOTIFY_ON_DOWNLOAD_ONLY", False) else "no"
        )
        notify_on_download_only = _coerce_bool(
            _safe_input(
                f"Do you want to receive notifications only when new files are downloaded? [y/n] (default: {notify_on_download_only_default}): ",
                default=notify_on_download_only_default,
            ),
            default=config.get("NOTIFY_ON_DOWNLOAD_ONLY", False),
        )
        config["NOTIFY_ON_DOWNLOAD_ONLY"] = notify_on_download_only

    else:
        # User chose not to use notifications
        if has_ntfy_config:
            # Ask for confirmation to disable existing notifications
            disable_confirm = _coerce_bool(
                _safe_input(
                    "You currently have notifications enabled. Are you sure you want to disable them? [y/n] (default: no): ",
                    default="n",
                ),
                default=False,
            )

            if disable_confirm:
                config["NTFY_TOPIC"] = ""
                config["NTFY_SERVER"] = ""
                config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
                config.pop("NTFY_REQUEST_TIMEOUT", None)
                print("Notifications have been disabled.")
            else:
                print("Keeping existing notification settings.")
        else:
            # No existing notifications, just confirm they're disabled
            config["NTFY_TOPIC"] = ""
            config["NTFY_SERVER"] = ""
            config["NOTIFY_ON_DOWNLOAD_ONLY"] = False
            config.pop("NTFY_REQUEST_TIMEOUT", None)
            print("Notifications will remain disabled.")

    return config


def _setup_github(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Configure GitHub API token for higher rate limits interactively.

    Prompts the user to optionally set up a GitHub personal access token for API requests.
    Explains the rate limit benefits (60/hour vs 5000/hour) and provides guidance
    on token creation. Updates the GITHUB_TOKEN key in the provided config.

    Parameters:
        config (dict): Current configuration dictionary to be modified in-place and returned.

    Returns:
        dict: The updated configuration dictionary with GITHUB_TOKEN potentially set.
    """
    print("\n" + "=" * 60)
    print("GitHub API Token Configuration")
    print("=" * 60)
    print()
    print("GitHub API requests have different rate limits:")
    print("  • Without token: 60 requests per hour")
    print("  • With personal token: 5,000 requests per hour")
    print()
    print("A token is optional. You can use one if you want to avoid rate limits.")
    print()

    # Check if token already exists
    current_token = config.get("GITHUB_TOKEN")
    if current_token:
        masked_token = current_token[:4] + "..." if len(current_token) > 4 else "***"
        print(f"Current status: Token configured ({masked_token})")
        change_choice = (
            _safe_input(
                "Would you like to change the GitHub token? [y/n] (default: no): ",
                default="n",
            )
            .strip()
            .lower()
        )
        if change_choice not in ["y", "yes"]:
            print("Keeping existing GitHub token configuration.")
            return config
    else:
        print("No GitHub token currently configured.")

    print()
    setup_choice = (
        _safe_input(
            "Would you like to set up a GitHub token now? [y/n] (default: no): ",
            default="n",
        )
        .strip()
        .lower()
    )

    if setup_choice in ["y", "yes"]:
        print("\nTo create a GitHub personal access token:")
        print("1. Visit: https://github.com/settings/tokens")
        print("2. Click 'Generate new token (classic)'")
        print("3. Give it a descriptive name (e.g., 'Fetchtastic')")
        print("4. Select 'public_repo' scope only (no additional permissions needed)")
        print("5. Click 'Generate token'")
        print("6. Copy the token and paste it below")
        print()

        token = getpass.getpass(
            "Enter your GitHub personal access token (or press Enter to skip): "
        ).strip()

        if token:
            # Enhanced validation - GitHub tokens have specific prefixes and formats
            # Classic PATs: start with "ghp_" and are 40 characters total (including prefix)
            # Fine-grained PATs: start with "github_pat_"
            # OAuth tokens: start with "gho_"
            # GitHub App user tokens: start with "ghu_"
            # GitHub App installation tokens: start with "ghs_"
            # GitHub App refresh tokens: start with "ghr_"
            valid_prefixes = ("ghp_", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_")

            if token.startswith(valid_prefixes) and len(token) >= 20:
                config["GITHUB_TOKEN"] = token
                print("✓ GitHub token saved successfully!")
                print(
                    "  This will be used for API authentication (5000 requests/hour)."
                )
            else:
                print(
                    "⚠ Invalid token format. GitHub tokens must start with one of: "
                    "ghp_ (classic PAT), github_pat_ (fine-grained PAT), gho_ (OAuth), "
                    "ghu_ (GitHub App user), ghs_ (GitHub App installation), ghr_ (GitHub App refresh)."
                )
                print("  No changes saved. Please try again if needed.")
        else:
            print("No token entered. Continuing without GitHub token.")
            config.pop("GITHUB_TOKEN", None)
    else:
        print("Skipping GitHub token setup.")
        config.pop("GITHUB_TOKEN", None)

    print()
    return config


def _setup_base(
    config: Dict[str, Any],
    is_partial_run: bool,
    is_first_run: bool,
    wants: Callable[[str], bool],
) -> Dict[str, Any]:
    """
    Ensure and configure the application's BASE_DIR and perform any required platform-specific base setup.

    Prompts for or confirms the base directory, loads an existing configuration if present, updates the global BASE_DIR, creates the directory if missing, and performs platform-specific initialization (Termux package/storage setup and optional Windows shortcut creation).

    Parameters:
        config (Dict[str, Any]): Current configuration dictionary; may be replaced by a loaded configuration.
        is_partial_run (bool): If true, only process this section when requested via `wants`.
        is_first_run (bool): If true, use first-run defaults for prompts.
        wants (Callable[[str], bool]): Predicate that returns True if the named setup section should be processed.

    Returns:
        Dict[str, Any]: The updated configuration dictionary with an ensured "BASE_DIR" value.
    """
    global BASE_DIR

    # Install required Termux packages first
    if is_termux() and (not is_partial_run or wants("base")):
        install_termux_packages()
        # Check if storage is set up
        if check_storage_setup():
            print("Termux storage is set up.")
        else:
            print(
                "Termux storage is not set up; skipping Termux storage-dependent steps."
            )

        # Check for pip installation and offer migration to pipx
        if get_fetchtastic_installation_method() == "pip":
            print("\n" + "=" * 60)
            print("NOTICE: Fetchtastic is installed via pip")
            print("=" * 60)
            print("We now recommend using pipx for better package isolation.")
            print("pipx provides:")
            print("• Isolated environments for each package")
            print("• Better dependency management")
            print("• Consistent experience across platforms")
            print("\nTo migrate to pipx:")
            print("1. Install pipx: pkg install python-pipx")
            print("2. Uninstall current version: pip uninstall fetchtastic")
            print("3. Install with pipx: pipx install fetchtastic")
            print("4. Restart your terminal")
            print("=" * 60)

            migrate_to_pipx = _coerce_bool(
                _safe_input(
                    "Would you like to migrate to pipx now? [y/n] (default: no): ",
                    default="n",
                ),
                default=False,
            )

            if migrate_to_pipx:
                print("Starting migration to pipx...")
                try:
                    # Install pipx if not already installed
                    pkg_exe = shutil.which("pkg") or "pkg"
                    subprocess.run(
                        [pkg_exe, "install", "python-pipx"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ pipx installed")

                    # Uninstall current fetchtastic
                    pip_exe = shutil.which("pip") or "pip"
                    subprocess.run(
                        [pip_exe, "uninstall", "fetchtastic", "-y"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ Removed pip installation")

                    # Install with pipx
                    pipx_exe = shutil.which("pipx") or "pipx"
                    subprocess.run(
                        [pipx_exe, "install", "fetchtastic"],
                        check=True,
                        capture_output=True,
                    )
                    print("✓ Installed with pipx")

                    print("\nMigration complete! Please restart your terminal.")
                    print("You can then run 'fetchtastic setup' to continue.")
                    sys.exit(0)

                except subprocess.CalledProcessError as e:
                    print(f"Migration failed: {e}")
                    if e.stderr:
                        print(f"Error details:\n{e.stderr.decode(errors='ignore')}")
                    print("You can migrate manually later using the steps above.")

        from fetchtastic.log_utils import logger

        separator = "=" * 60
        logger.info(f"{separator}\n")

    # Ask for base directory as the first question
    exists, config_path = config_exists()

    if exists:
        # Load existing configuration
        loaded_config = load_config()
        if loaded_config is None:
            print("Failed to load existing configuration. Starting with defaults.")
            config = {}
            current_base_dir = DEFAULT_BASE_DIR
        else:
            config = loaded_config
            print(
                "Existing configuration found. You can keep current settings or change them."
            )
            current_base_dir = config.get("BASE_DIR", DEFAULT_BASE_DIR)
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (current: {current_base_dir}): "
        )
    else:
        # Initialize default configuration
        config = {}
        base_dir_prompt = (
            f"Enter the base directory for Fetchtastic (default: {DEFAULT_BASE_DIR}): "
        )

    if not is_partial_run or wants("base"):
        # Prompt for base directory
        base_dir_input = _safe_input(base_dir_prompt, default="").strip()

        if base_dir_input:
            # User entered a custom directory
            base_dir = os.path.expanduser(base_dir_input)

            # Check if there's a config file in the specified directory
            exists_in_dir, _ = config_exists(base_dir)
            if exists_in_dir and base_dir != BASE_DIR:
                print(f"Found existing configuration in {base_dir}")
                # Load the configuration from the specified directory
                loaded_config = load_config(base_dir)
                if loaded_config is not None:
                    config = loaded_config
            else:
                # No config in the specified directory or it's the same as current
                BASE_DIR = base_dir
                # Keep CONFIG_FILE in the platformdirs location
                # CONFIG_FILE should not be changed here
        else:
            # User accepted the default/current directory
            if is_first_run:
                base_dir = DEFAULT_BASE_DIR
            else:
                base_dir = config.get("BASE_DIR", DEFAULT_BASE_DIR)

            # Expand user directory if needed (e.g., ~/Downloads/Meshtastic)
            base_dir = os.path.expanduser(base_dir)

            # Update global variables
            BASE_DIR = base_dir
            # Keep CONFIG_FILE in the platformdirs location
            # CONFIG_FILE should not be changed here
    else:
        # Partial run retaining the existing base directory
        BASE_DIR = os.path.expanduser(config.get("BASE_DIR", DEFAULT_BASE_DIR))

    # Store the base directory in the config
    config["BASE_DIR"] = BASE_DIR

    # Create the base directory if it doesn't exist
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)

    # On Windows, handle shortcuts
    if platform.system() == "Windows" and (not is_partial_run or wants("base")):
        # Always create a shortcut to the config file in the base directory without asking
        if WINDOWS_MODULES_AVAILABLE:
            create_config_shortcut(CONFIG_FILE, BASE_DIR)

            # Check if Start Menu shortcuts already exist
            if os.path.exists(WINDOWS_START_MENU_FOLDER):
                create_menu_choice = (
                    _safe_input(
                        "Fetchtastic shortcuts already exist in the Start Menu. Would you like to update them? [y/n] (default: yes): ",
                        default="y",
                    )
                    .strip()
                    .lower()
                    or "y"
                )
                create_menu = _coerce_bool(create_menu_choice, default=True)
            else:
                create_menu = _coerce_bool(
                    _safe_input(
                        "Would you like to create Fetchtastic shortcuts in the Start Menu? (recommended) [y/n] (default: yes): ",
                        default="y",
                    ),
                    default=True,
                )

            if create_menu:
                create_windows_menu_shortcuts(CONFIG_FILE, BASE_DIR)
        else:
            print(
                "Windows shortcuts not available. Install optional dependencies for full Windows integration:"
            )
            print("pip install fetchtastic[windows]")

    return config


def run_setup(
    sections: Optional[Sequence[str]] = None, perform_initial_download: bool = True
) -> None:
    """
    Run the interactive Fetchtastic setup wizard.

    Guides the user through creating or migrating the Fetchtastic configuration, selecting asset types and retention policies, configuring notifications and GitHub token, and setting up platform-specific automation (cron/boot scripts on Termux/Linux/macOS or Start Menu/startup shortcuts on Windows). The wizard can run in full interactive mode or in a partial mode limited to specified sections; results are persisted to the YAML config file.

    Parameters:
        sections (Optional[Sequence[str]]): Sequence of setup section names to run (lowercase names from SETUP_SECTION_CHOICES). When provided, only those sections are prompted and other configuration values are preserved; when `None`, a full interactive setup is executed.
        perform_initial_download (bool): If True (default) and a full setup was performed on non-Windows platforms, offer to start an initial download after setup completes. When False, skip the initial-download prompt.
    """
    global BASE_DIR
    partial_sections: Optional[Set[str]] = None
    if sections:
        section_names = [s.lower() for s in sections]
        invalid = sorted({s for s in section_names if s not in SETUP_SECTION_CHOICES})
        if invalid:
            raise ValueError("Unsupported setup section(s): " + ", ".join(invalid))
        partial_sections = set(section_names)

    config_present, _ = config_exists()
    if not partial_sections and config_present:
        user_sections = _prompt_for_setup_sections()
        if user_sections is not None and not user_sections:
            print("Setup cancelled.")
            return
        if user_sections:
            partial_sections = user_sections

    is_partial_run = partial_sections is not None

    def wants(section: str) -> bool:
        """
        Decide whether the given setup section should be included in the current run.

        Parameters:
            section (str): Name of the setup section to check.

        Returns:
            True if the section is requested for this run, False otherwise.
        """

        return partial_sections is None or section in partial_sections

    if is_partial_run:
        section_list = ", ".join(sorted(partial_sections or []))
        print(f"Updating Fetchtastic setup sections: {section_list}")
    else:
        print("Running Fetchtastic Setup...")

    # Determine if this is a first run
    config_present, _ = config_exists()
    is_first_run = not config_present

    # Handle base setup (Termux packages, base directory, Windows shortcuts)
    config = _setup_base({}, is_partial_run, is_first_run, wants)

    # Handle download type selection and asset menus
    config, save_apks, save_firmware = _setup_downloads(config, is_partial_run, wants)

    # If both save_apks and save_firmware are False, exit setup.
    # During partial runs that only update non-download sections, continue instead.
    if (
        not save_apks
        and not save_firmware
        and (not is_partial_run or wants("android") or wants("firmware"))
    ):
        return

    # Determine default number of versions to keep based on platform
    default_versions_to_keep = 2 if is_termux() else 3

    # Handle Android configuration
    if save_apks and (not is_partial_run or wants("android")):
        config = _setup_android(config, is_first_run, default_versions_to_keep)
    # Handle firmware configuration
    if save_firmware and (not is_partial_run or wants("firmware")):
        config = _setup_firmware(config, is_first_run, default_versions_to_keep)

    # Ask if the user wants to only download when connected to Wi-Fi (Termux only)
    if is_termux():
        if not is_partial_run or wants("base"):
            wifi_only_default = "yes" if config.get("WIFI_ONLY", True) else "no"
            wifi_only = _coerce_bool(
                _safe_input(
                    f"Do you want to only download when connected to Wi-Fi? [y/n] (default: {wifi_only_default}): ",
                    default=wifi_only_default,
                ),
                default=config.get("WIFI_ONLY", True),
            )
            config["WIFI_ONLY"] = wifi_only
    else:
        if not is_partial_run or wants("base"):
            # For non-Termux environments, remove WIFI_ONLY from config if it exists
            config.pop("WIFI_ONLY", None)

    # Set the download directory to the same as the base directory
    download_dir = BASE_DIR
    config["DOWNLOAD_DIR"] = download_dir

    # Record the version at which setup was last run
    try:
        current_version = version("fetchtastic")
        config["LAST_SETUP_VERSION"] = current_version
    except PackageNotFoundError:
        # If fetchtastic package is not found, we can't get the version.
        pass
    except Exception as e:
        # If other error, we can't get the version.
        # Log the specific error for debugging but don't fail setup
        logger.debug("Could not determine package version: %s", e)
    config["LAST_SETUP_DATE"] = datetime.now().isoformat()

    # Make sure the config directory exists
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except OSError as e:
            print(f"Error creating config directory: {e}")

    # Handle automation configuration
    config = _setup_automation(config, is_partial_run, wants)

    # Handle notifications configuration
    if not is_partial_run or wants("notifications"):
        config = _setup_notifications(config)

    # Handle GitHub token configuration
    if not is_partial_run or wants("github"):
        config = _setup_github(config)

    # Persist configuration after all interactive sections
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    print(f"Configuration saved to: {CONFIG_FILE}")

    if not is_partial_run and perform_initial_download:
        # Ask if the user wants to perform a first run
        if platform.system() == "Windows":
            # On Windows, we'll just tell them how to run it
            print("Setup complete. Run 'fetchtastic download' to start downloading.")
            if WINDOWS_MODULES_AVAILABLE:
                print("You can also use the shortcuts created in the Start Menu.")

            # If running from a batch file or shortcut, pause at the end
            if os.environ.get("PROMPT") is None or "cmd.exe" in os.environ.get(
                "COMSPEC", ""
            ):
                print("\nPress Enter to close this window...")
                _safe_input("")
        else:
            # On other platforms, offer to run it now
            perform_first_run = _safe_input(
                "Would you like to start first run now? [y/n] (default: yes): ",
                default="y",
            )
            if _coerce_bool(perform_first_run, default=True):
                from fetchtastic.download.cli_integration import DownloadCLIIntegration

                print(
                    "Setup complete. Starting first run, this may take a few minutes..."
                )
                DownloadCLIIntegration().main(config=config)
            else:
                print(
                    "Setup complete. Run 'fetchtastic download' to start downloading."
                )
    else:
        print("Selected setup sections updated. Run 'fetchtastic download' when ready.")


def check_for_updates() -> Tuple[str, Optional[str], bool]:
    """
    Check whether a newer release of Fetchtastic is available on PyPI.

    Returns:
        tuple: (current_version, latest_version, update_available)
            current_version (str): Installed fetchtastic version or "unknown" if it cannot be determined.
            latest_version (str|None): Latest version string from PyPI, or `None` if the lookup failed.
            update_available (bool): `True` if a newer release exists on PyPI, `False` otherwise.
    """
    try:
        # Get current version
        current_version = version("fetchtastic")

        # Get latest version from PyPI
        import requests  # type: ignore[import-untyped]

        response = requests.get("https://pypi.org/pypi/fetchtastic/json", timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_version = data["info"]["version"]
            # Use packaging.version for proper version comparison
            from packaging import version as pkg_version

            current_ver = pkg_version.parse(current_version)
            latest_ver = pkg_version.parse(latest_version)
            return current_version, latest_version, latest_ver > current_ver
        return current_version, None, False
    except Exception:
        # If anything fails, just return that no update is available
        try:
            return version("fetchtastic"), None, False
        except Exception:
            return "unknown", None, False


def get_upgrade_command() -> str:
    """
    Select the shell command to upgrade Fetchtastic for the current platform and installation method.

    On Termux this will choose the pip command if Fetchtastic was installed via pip, otherwise it selects the pipx upgrade command. On non-Termux platforms it returns the pipx upgrade command.
    Returns:
        str: Shell command to run to upgrade Fetchtastic.
    """
    if is_termux():
        # Check how fetchtastic is installed in Termux
        install_method = get_fetchtastic_installation_method()
        if install_method == "pip":
            return "pip install --upgrade fetchtastic"
        else:
            # Default to pipx for new installations and pipx installations
            return "pipx upgrade fetchtastic"
    else:
        return "pipx upgrade fetchtastic"


def should_recommend_setup() -> Tuple[bool, str, Optional[str], Optional[str]]:
    """
    Decides whether the interactive setup wizard should be recommended.

    Returns:
        tuple:
            should_recommend (bool): `True` if setup should be recommended (no configuration found, no recorded setup version, installed package version differs from recorded version, or an error occurred); `False` if the recorded setup version matches the installed package.
            reason (str): Short human-readable explanation for the recommendation.
            last_setup_version (Optional[str]): Version string stored in configuration under `LAST_SETUP_VERSION`, or `None` if unavailable.
            current_version (Optional[str]): Installed fetchtastic package version as reported by importlib.metadata, or `None` if it cannot be determined.
    """
    try:
        config = load_config()
        if not config:
            return True, "No configuration found", None, None

        last_setup_version = config.get("LAST_SETUP_VERSION")
        if not last_setup_version:
            return True, "Setup version not tracked", None, None

        # Get current version
        current_version = version("fetchtastic")

        if last_setup_version != current_version:
            return (
                True,
                f"Version changed from {last_setup_version} to {current_version}",
                last_setup_version,
                current_version,
            )

        return False, "Setup is current", last_setup_version, current_version

    except Exception:
        return True, "Could not determine setup status", None, None


def get_version_info() -> tuple[str, str | None, bool]:
    """
    Return to installed Fetchtastic version, to latest available version (if known), and whether an update is available.

    Returns:
        tuple: (current_version, latest_version, update_available)
            current_version (str): Installed package version or "unknown" if it cannot be determined.
            latest_version (str | None): Latest version from registry, or `None` on network/error.
            update_available (bool): `True` if a newer version is available, `False` otherwise.
    """
    current_version, latest_version, update_available = check_for_updates()

    # Return version information without printing
    # The caller will handle logging/printing as appropriate
    return current_version, latest_version, update_available


def migrate_config() -> bool:
    """
    Migrate the legacy configuration file from OLD_CONFIG_FILE to CONFIG_FILE and remove the legacy file on success.

    Creates CONFIG_DIR if needed, writes the migrated YAML configuration to CONFIG_FILE, and logs errors encountered during migration or when removing the legacy file.

    Returns:
        `True` if the configuration was successfully written to CONFIG_FILE (the legacy file will have been removed or a removal failure logged), `False` otherwise.
    """
    # Import here to avoid circular imports
    from fetchtastic.log_utils import logger

    # Check if old config exists
    if not os.path.exists(OLD_CONFIG_FILE):
        return False

    # Check if new config directory exists, create if not
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating config directory: {e}")
            return False

    # Load the old config
    config = _load_yaml_mapping(OLD_CONFIG_FILE)
    if config is None:
        return False

    # Save to new location
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, sort_keys=False)

        # Remove the old file after successful migration
        try:
            os.remove(OLD_CONFIG_FILE)
            logger.info(f"Configuration migrated to {CONFIG_FILE} and old file removed")
        except Exception as e:
            logger.error(
                f"Configuration migrated to {CONFIG_FILE} but failed to remove old file: {e}"
            )

        return True
    except Exception as e:
        logger.error(f"Error saving config to new location: {e}")
        return False


def prompt_for_migration() -> bool:
    """
    Automatically migrates configuration from old location to new location
    without prompting user.

    Returns:
        bool: Always returns True to indicate migration should proceed.
    """
    # Import here to avoid circular imports
    from fetchtastic.log_utils import logger

    logger.info(f"Found configuration file at old location: {OLD_CONFIG_FILE}")
    logger.info(f"Automatically migrating to the new location: {CONFIG_FILE}")
    return True


def create_windows_menu_shortcuts(config_file_path: str, base_dir: str) -> bool:
    """
    Create Windows Start Menu shortcuts and supporting batch files for Fetchtastic.

    Creates a Fetchtastic folder in the user's Start Menu containing shortcuts to:
    - a download runner, repository browser, setup, update checker (all implemented as batch files placed in CONFIG_DIR/batch),
    - the Fetchtastic configuration file,
    - the Meshtastic downloads base directory,
    - the Fetchtastic log file.

    This function is a no-op on non-Windows platforms or when required Windows modules are unavailable.

    Parameters:
        config_file_path (str): Full path to the Fetchtastic YAML configuration file used as the target for the configuration shortcut.
        base_dir (str): Path to the Meshtastic downloads base directory used as the target for the downloads-folder shortcut.

    Returns:
        bool: True if shortcuts and batch files were created successfully; False if running on a non-Windows platform, required Windows modules are missing, or any error occurred while creating files/shortcuts.

    Side effects:
        - May create CONFIG_DIR/batch and several .bat files.
        - May create or recreate the Start Menu folder at WINDOWS_START_MENU_FOLDER and write .lnk shortcuts.
        - May create the user log directory and an empty log file if none exists.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        # Completely remove the Start Menu folder and recreate it
        # This ensures we don't have any leftover shortcuts
        import shutil

        # First, make sure the parent directory exists
        parent_dir = os.path.dirname(WINDOWS_START_MENU_FOLDER)
        if not os.path.exists(parent_dir):
            print(f"Warning: Parent directory {parent_dir} does not exist")
            # Try to create the parent directory structure
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                print(f"Error creating parent directory: {e}")
                return False

        # Now handle the Fetchtastic folder
        if os.path.exists(WINDOWS_START_MENU_FOLDER):
            print(f"Removing existing Start Menu folder: {WINDOWS_START_MENU_FOLDER}")
            try:
                # Try to remove the entire folder
                shutil.rmtree(WINDOWS_START_MENU_FOLDER)
                print("Successfully removed existing shortcuts folder")
            except Exception as e:
                print(f"Warning: Could not remove shortcuts folder: {e}")
                # Try to remove individual files as a fallback
                try:
                    # First list all files
                    with os.scandir(WINDOWS_START_MENU_FOLDER) as it:
                        files = list(it)
                    print(f"Found {len(files)} files in shortcuts folder")

                    # Try to remove each file
                    for entry in files:
                        try:
                            if entry.is_file():
                                os.remove(entry.path)
                                print(f"Removed: {entry.name}")
                            elif entry.is_dir():
                                shutil.rmtree(entry.path)
                                print(f"Removed directory: {entry.name}")
                        except OSError as e3:
                            print(f"Could not remove {entry.name}: {e3}")

                    print("Attempted to remove individual files")
                except Exception as e2:
                    print(f"Warning: Could not clean shortcuts folder: {e2}")
                    print("Will attempt to overwrite existing shortcuts")

        # Create a fresh Fetchtastic folder in the Start Menu
        print(f"Creating Start Menu folder: {WINDOWS_START_MENU_FOLDER}")
        try:
            os.makedirs(WINDOWS_START_MENU_FOLDER, exist_ok=True)
        except Exception as e:
            print(f"Error creating Start Menu folder: {e}")
            return False

        # Get the path to the fetchtastic executable
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return False

        # Create batch files in the config directory instead of the Start Menu
        batch_dir = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        # Create a batch file for download that pauses at the end
        download_batch_path = os.path.join(batch_dir, "fetchtastic_download.bat")
        with open(download_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Download\n")
            f.write(f'"{fetchtastic_path}" download\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for repo browse that pauses at the end
        repo_batch_path = os.path.join(batch_dir, "fetchtastic_repo_browse.bat")
        with open(repo_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Repository Browser\n")
            f.write(f'"{fetchtastic_path}" repo browse\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for setup that pauses at the end
        setup_batch_path = os.path.join(batch_dir, "fetchtastic_setup.bat")
        with open(setup_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Setup\n")
            f.write(f'"{fetchtastic_path}" setup\n')
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create a batch file for checking updates that pauses at the end
        update_batch_path = os.path.join(batch_dir, "fetchtastic_update.bat")
        with open(update_batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Update Check\n")
            f.write("echo Checking for Fetchtastic updates...\n")
            f.write("echo.\n")

            # Use pipx to upgrade fetchtastic with improved logic
            pipx_path = shutil.which("pipx")
            if pipx_path:
                f.write("echo Attempting to upgrade Fetchtastic...\n")
                f.write(f'"{pipx_path}" upgrade fetchtastic\n')
                f.write("if %ERRORLEVEL% EQU 0 (\n")
                f.write("    echo Upgrade completed successfully!\n")
                f.write(") else (\n")
                f.write("    echo Upgrade failed or already at latest version.\n")
                f.write("    echo Trying force reinstall...\n")
                f.write(f'    "{pipx_path}" install "fetchtastic[win]" --force\n')
                f.write("    if %ERRORLEVEL% EQU 0 (\n")
                f.write("        echo Force reinstall completed successfully!\n")
                f.write("    ) else (\n")
                f.write(
                    "        echo Force reinstall failed. Trying uninstall/reinstall...\n"
                )
                f.write(
                    f'        "{pipx_path}" uninstall fetchtastic --force >nul 2>&1\n'
                )
                f.write(f'        "{pipx_path}" install "fetchtastic[win]"\n')
                f.write("        if %ERRORLEVEL% EQU 0 (\n")
                f.write("            echo Reinstall completed successfully!\n")
                f.write("        ) else (\n")
                f.write(
                    "            echo All upgrade methods failed. Please check your internet connection.\n"
                )
                f.write("        )\n")
                f.write("    )\n")
                f.write(")\n")
                f.write("echo.\n")
                f.write("echo Checking final version...\n")
                f.write("fetchtastic version\n")
            else:
                # Fallback to pip if pipx is not found
                pip_path = shutil.which("pip")
                if pip_path:
                    f.write(f'"{pip_path}" install --upgrade "fetchtastic[win]"\n')
                else:
                    f.write("echo Error: Neither pipx nor pip was found in PATH.\n")
                    f.write("echo Please install pipx or pip to upgrade Fetchtastic.\n")
            f.write("echo.\n")
            f.write("echo Press any key to close this window...\n")
            f.write("pause >nul\n")

        # Create shortcut for fetchtastic download (using batch file)
        download_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Download.lnk"
        )
        winshell.CreateShortcut(
            Path=download_shortcut_path,
            Target=download_batch_path,
            Description="Download Meshtastic firmware and APKs",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for fetchtastic setup (using batch file)
        setup_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Setup.lnk"
        )
        winshell.CreateShortcut(
            Path=setup_shortcut_path,
            Target=setup_batch_path,
            Description="Configure Fetchtastic settings",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for fetchtastic repo browse (using batch file)
        repo_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Repository Browser.lnk"
        )
        winshell.CreateShortcut(
            Path=repo_shortcut_path,
            Target=repo_batch_path,
            Description="Browse and download files from the Meshtastic repository",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut to configuration file
        config_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Configuration.lnk"
        )
        winshell.CreateShortcut(
            Path=config_shortcut_path,
            Target=config_file_path,
            Description=f"Edit Fetchtastic Configuration File ({CONFIG_FILE_NAME})",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut to Meshtastic base directory
        base_dir_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Meshtastic Downloads.lnk"
        )
        winshell.CreateShortcut(
            Path=base_dir_shortcut_path,
            Target=base_dir,
            Description="Open Meshtastic Downloads Folder",
            Icon=(
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "explorer.exe"),
                0,
            ),
        )

        # Create shortcut to log file
        # First check if there's a log file in the base directory (old location)
        base_log_file = os.path.join(BASE_DIR, "fetchtastic.log")
        if os.path.exists(base_log_file):
            log_file = base_log_file
        else:
            # Use the platformdirs log location
            log_dir = platformdirs.user_log_dir("fetchtastic")
            log_file = os.path.join(log_dir, "fetchtastic.log")
            # Create log directory if it doesn't exist
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            # Create an empty log file if it doesn't exist
            if not os.path.exists(log_file):
                with open(log_file, "w") as f:
                    f.write("# Fetchtastic log file\n")
                print(f"Created empty log file at: {log_file}")

        log_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic Log.lnk"
        )
        winshell.CreateShortcut(
            Path=log_shortcut_path,
            Target=log_file,
            Description="View Fetchtastic Log File",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        # Create shortcut for checking updates
        update_shortcut_path = os.path.join(
            WINDOWS_START_MENU_FOLDER, "Fetchtastic - Check for Updates.lnk"
        )
        winshell.CreateShortcut(
            Path=update_shortcut_path,
            Target=update_batch_path,
            Description="Check for and install Fetchtastic updates",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        print("Shortcuts created in Start Menu")
        return True
    except Exception as e:
        print(f"Failed to create Windows Start Menu shortcuts: {e}")
        return False


def create_config_shortcut(config_file_path: str, target_dir: str) -> bool:
    """
    Create a Windows shortcut to the Fetchtastic configuration file in the specified directory.

    Parameters:
        config_file_path (str): Path to the configuration file to link.
        target_dir (str): Directory where the shortcut file will be created.

    Returns:
        bool: `True` if the shortcut was created successfully, `False` otherwise.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        shortcut_path = os.path.join(target_dir, WINDOWS_SHORTCUT_FILE)

        # Create the shortcut using winshell
        winshell.CreateShortcut(
            Path=shortcut_path,
            Target=config_file_path,
            Description=f"Fetchtastic Configuration File ({CONFIG_FILE_NAME})",
            Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
        )

        print(f"Created shortcut to configuration file at: {shortcut_path}")
        return True
    except Exception as e:
        print(f"Failed to create shortcut to configuration file: {e}")
        return False


def create_startup_shortcut() -> bool:
    """
    Create a Windows startup shortcut that runs Fetchtastic at user login.

    This attempts to create a minimized shortcut in the current user's Startup folder that runs a batch wrapper which invokes the `fetchtastic download` command. Has no effect on non-Windows systems or when the required Windows helper modules are not available.

    Returns:
        bool: `True` if the shortcut was created successfully, `False` otherwise.
    """
    if platform.system() != "Windows" or not WINDOWS_MODULES_AVAILABLE:
        return False

    try:
        # Get the path to the fetchtastic executable
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return False

        # Get the startup folder path
        startup_folder: str = winshell.startup()

        # Create batch files in the config directory instead of the startup folder
        batch_dir = os.path.join(CONFIG_DIR, "batch")
        if not os.path.exists(batch_dir):
            os.makedirs(batch_dir, exist_ok=True)

        # Create a batch file for startup that runs silently
        batch_path = os.path.join(batch_dir, "fetchtastic_startup.bat")
        with open(batch_path, "w") as f:
            f.write("@echo off\n")
            f.write("title Fetchtastic Automatic Download\n")
            f.write(f'"{fetchtastic_path}" download\n')
            # Don't pause at the end for startup - we want it to run silently

        # Create the shortcut to the batch file
        shortcut_path = os.path.join(startup_folder, "Fetchtastic.lnk")

        # Use direct shortcut creation without WindowStyle parameter
        try:
            # First try with WindowStyle parameter (newer versions of winshell)
            winshell.CreateShortcut(
                Path=shortcut_path,
                Target=batch_path,
                Description="Run Fetchtastic on startup",
                Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
                WindowStyle=7,  # Minimized
            )
        except TypeError:
            # If WindowStyle is not supported, use basic parameters
            winshell.CreateShortcut(
                Path=shortcut_path,
                Target=batch_path,
                Description="Run Fetchtastic on startup",
                Icon=(os.path.join(sys.exec_prefix, "pythonw.exe"), 0),
            )

        print(f"Created startup shortcut at: {shortcut_path}")
        return True
    except Exception as e:
        print(f"Failed to create startup shortcut: {e}")
        return False


def copy_to_clipboard_func(text: Optional[str]) -> bool:
    """
    Copy the provided text to the system clipboard using a platform-appropriate mechanism.

    Supports Termux, Windows (when win32 modules are available), macOS (pbcopy), and Linux (xclip or xsel). If no supported mechanism is available or an error occurs, nothing is copied.

    Parameters:
        text (Optional[str]): The text to copy. If `None`, the function does nothing.

    Returns:
        bool: `True` if the text was successfully copied to the clipboard, `False` otherwise.
    """
    if text is None:
        return False

    if is_termux():
        # Termux environment
        try:
            subprocess.run(
                ["termux-clipboard-set"], input=text.encode("utf-8"), check=True
            )
            return True
        except Exception as e:
            logger.error("Error copying to Termux clipboard: %s", e)
            return False
    elif platform.system() == "Windows" and WINDOWS_MODULES_AVAILABLE:
        # Windows environment with win32com available
        try:
            import win32clipboard  # type: ignore[import-untyped]

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            try:
                # Try the newer API with explicit format
                import win32con  # type: ignore[import-untyped]

                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            except (ImportError, TypeError):
                # Fall back to SetClipboardText for older versions or if win32con is missing attributes
                win32clipboard.SetClipboardText(text)
            win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            logger.error("Error copying to Windows clipboard: %s", e)
            return False
    else:
        # Other platforms
        system = platform.system()
        try:
            if system == "Darwin":
                # macOS
                subprocess.run("pbcopy", text=True, input=text, check=True)
                return True
            elif system == "Linux":
                # Linux
                encoded_text = text.encode("utf-8")
                if shutil.which("xclip"):
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=encoded_text,
                        check=True,
                    )
                    return True
                elif shutil.which("xsel"):
                    subprocess.run(
                        ["xsel", "--clipboard", "--input"],
                        input=encoded_text,
                        check=True,
                    )
                    return True
                else:
                    logger.warning(
                        "xclip or xsel not found. Install xclip or xsel to use clipboard functionality."
                    )
                    return False
            else:
                logger.warning(
                    "Clipboard functionality is not supported on this platform."
                )
                return False
        except Exception as e:
            logger.error("Error copying to clipboard on %s: %s", system, e)
            return False


def install_termux_packages() -> None:
    """
    Ensure Termux has termux-api, termux-services, and cronie installed.

    Checks for the required Termux utilities using `shutil.which` and installs any missing packages via the `pkg` package manager by invoking `pkg install`. May raise subprocess.CalledProcessError if the underlying package installation command fails.
    """
    # Install termux-api, termux-services, and cronie if they are not installed
    packages_to_install = []
    # Check for termux-api
    if shutil.which("termux-battery-status") is None:
        packages_to_install.append("termux-api")
    # Check for termux-services
    if shutil.which("sv-enable") is None:
        packages_to_install.append("termux-services")
    # Check for cronie
    if shutil.which("crond") is None:
        packages_to_install.append("cronie")
    if packages_to_install:
        print("Installing required Termux packages...")
        subprocess.run(["pkg", "install"] + packages_to_install + ["-y"], check=True)
        print("Required Termux packages installed.")
    else:
        print("All required Termux packages are already installed.")


def setup_storage() -> None:
    """
    Invoke Termux's storage permission flow to grant the app access to shared storage.

    On Termux, this runs the system prompt (termux-setup-storage) and prints status messages.
    On non-Termux platforms, prints a message indicating the operation is not supported.
    If the setup command fails, an informative message is printed advising the user to grant permissions when prompted.
    """
    # Run termux-setup-storage
    print("Setting up Termux storage access...")
    try:
        subprocess.run(["termux-setup-storage"], check=True)
    except subprocess.CalledProcessError:
        print("An error occurred while setting up Termux storage.")
        print("Please grant storage permissions when prompted.")


def install_crond() -> None:
    """
    Install and enable the Termux crond service.

    On Termux, installs the cronie package if it is not present and enables the crond service; on non-Termux platforms this function has no effect.
    """
    if is_termux():
        try:
            crond_path = shutil.which("crond")
            if crond_path is None:
                print("Installing cronie...")
                # Install cronie
                subprocess.run(["pkg", "install", "cronie", "-y"], check=True)
                print("cronie installed.")
            else:
                print("cronie is already installed.")
            # Enable crond service
            subprocess.run(["sv-enable", "crond"], check=True)
            print("crond service enabled.")
        except Exception as e:
            print(f"An error occurred while installing or enabling crond: {e}")
    else:
        # For non-Termux environments, crond installation is not needed
        pass


@cron_command_required
def setup_cron_job(frequency="hourly", *, crontab_path: str = "crontab"):
    """
    Configure the user's crontab to run Fetchtastic on a regular schedule.

    Removes existing Fetchtastic scheduled entries (excluding any `@reboot` lines) and writes a single scheduled entry for the chosen frequency. Unknown frequency values default to "hourly". This function is a no-op on Windows.
    Parameters:
        frequency (str): Schedule key from CRON_SCHEDULES (for example "hourly" or "daily"); unknown values default to "hourly".
    """
    # Skip cron job setup on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    # Validate frequency and get schedule info
    if frequency not in CRON_SCHEDULES:
        print(f"Warning: Invalid cron frequency '{frequency}'. Defaulting to hourly.")
        frequency = "hourly"

    schedule_info = CRON_SCHEDULES[frequency]
    cron_schedule = schedule_info["schedule"]
    frequency_desc = schedule_info["desc"]

    try:
        # Use the pre-validated crontab path
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,  # Add timeout to prevent hanging
        )
        if result.returncode != 0:
            existing_cron = ""
        else:
            existing_cron = result.stdout.strip()

        # Remove existing Fetchtastic cron jobs (excluding @reboot ones)
        cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
        cron_lines = [
            line
            for line in cron_lines
            if not (
                ("# fetchtastic" in line or "fetchtastic download" in line)
                and not line.strip().startswith("@reboot")
            )
        ]

        # Add new cron job
        if is_termux():
            cron_lines.append(f"{cron_schedule} fetchtastic download  # fetchtastic")
        else:
            # Non-Termux environments
            fetchtastic_path = shutil.which("fetchtastic")
            if not fetchtastic_path:
                print("Error: fetchtastic executable not found in PATH.")
                return
            cron_lines.append(
                f"{cron_schedule} {fetchtastic_path} download  # fetchtastic"
            )

        # Join cron lines
        new_cron = "\n".join(cron_lines)

        # Ensure new_cron ends with a newline
        if not new_cron.endswith("\n"):
            new_cron += "\n"

        # Update crontab
        try:
            process = subprocess.Popen(
                [crontab_path, "-"],
                stdin=subprocess.PIPE,
                text=True,
            )
            process.communicate(
                input=new_cron, timeout=CRON_COMMAND_TIMEOUT_SECONDS
            )  # Add timeout to prevent hanging
            print(f"Cron job added to run Fetchtastic {frequency_desc}.")
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
            logger.error(f"An error occurred while setting up the cron job: {e}")

    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as e:
        logger.error(f"Error reading crontab: {e}")
        return


@cron_command_required
def remove_cron_job(*, crontab_path: str = "crontab"):
    """
    Remove Fetchtastic's non-@reboot cron entries from the current user's crontab.

    Removes crontab lines that contain "# fetchtastic" or "fetchtastic download" while preserving any lines that start with "@reboot". Does nothing on Windows or if the crontab command is unavailable. Errors encountered while reading or updating the crontab are logged and not raised.

    Parameters:
        crontab_path (str): Path to the system crontab executable (for example "crontab").
    """
    # Skip cron job removal on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    try:
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            existing_cron = result.stdout.strip()
            # Remove existing Fetchtastic cron jobs (excluding @reboot)
            cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
            cron_lines = [
                line
                for line in cron_lines
                if not (
                    ("# fetchtastic" in line or "fetchtastic download" in line)
                    and not line.strip().startswith("@reboot")
                )
            ]
            # Join cron lines
            new_cron = "\n".join(cron_lines)
            # Ensure new_cron ends with a newline
            if not new_cron.endswith("\n"):
                new_cron += "\n"
            try:
                process = subprocess.Popen(
                    [crontab_path, "-"],
                    stdin=subprocess.PIPE,
                    text=True,
                )
                process.communicate(
                    input=new_cron, timeout=CRON_COMMAND_TIMEOUT_SECONDS
                )
                print("Daily cron job removed.")
            except (
                subprocess.SubprocessError,
                subprocess.TimeoutExpired,
                OSError,
            ) as exc:
                logger.error(f"An error occurred while removing the cron job: {exc}")
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"An error occurred while removing the cron job: {exc}")


def setup_boot_script() -> None:
    """
    Create a boot script that runs fetchtastic on device boot in Termux.

    This function is intended for Termux environments only. On other platforms, it does nothing.
    """
    boot_dir = os.path.expanduser("~/.termux/boot")
    boot_script = os.path.join(boot_dir, "fetchtastic.sh")
    if not os.path.exists(boot_dir):
        os.makedirs(boot_dir)
        print("Created the Termux:Boot directory.")
        print(
            "Please install Termux:Boot from F-Droid and run it once to enable boot scripts."
        )
    # Write the boot script
    with open(boot_script, "w") as f:
        f.write("#!/data/data/com.termux/files/usr/bin/sh\n")
        f.write("sleep 30\n")
        f.write("fetchtastic download\n")
    os.chmod(boot_script, 0o700)
    print("Boot script created to run Fetchtastic on device boot.")
    print(
        "Note: The script may not run on boot until you have installed and run Termux:Boot at least once."
    )


def remove_boot_script() -> None:
    """
    Delete the Termux boot script used to run Fetchtastic on device startup.

    If the file ~/.termux/boot/fetchtastic.sh exists, it is removed; on non-Termux systems this function has no effect.
    """
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    if os.path.exists(boot_script):
        os.remove(boot_script)
        print("Boot script removed.")


@cron_command_required
def setup_reboot_cron_job(*, crontab_path: str = "crontab"):
    """
    Ensure an @reboot crontab entry exists to run the `fetchtastic download` command after system reboot.

    If running on Windows this is a no-op. Removes any existing `@reboot` entries associated with Fetchtastic and adds a single `@reboot <path-to-fetchtastic> download  # fetchtastic` entry. If the `fetchtastic` executable cannot be found, the crontab is left unchanged. Subprocess and I/O errors are logged.
    Parameters:
        crontab_path (str): Filesystem path to the `crontab` command used to read and update the user's crontab.
    """
    # Skip cron job setup on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    try:
        # Get current crontab entries
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            existing_cron = ""
        else:
            existing_cron = result.stdout.strip()

        # Remove existing @reboot Fetchtastic cron jobs
        cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
        cron_lines = [
            line
            for line in cron_lines
            if not (
                ("# fetchtastic" in line or "fetchtastic download" in line)
                and line.strip().startswith("@reboot")
            )
        ]

        # Add new @reboot cron job
        fetchtastic_path = shutil.which("fetchtastic")
        if not fetchtastic_path:
            print("Error: fetchtastic executable not found in PATH.")
            return
        cron_lines.append(f"@reboot {fetchtastic_path} download  # fetchtastic")

        # Join cron lines
        new_cron = "\n".join(cron_lines)

        # Ensure new_cron ends with a newline
        if not new_cron.endswith("\n"):
            new_cron += "\n"

        # Update crontab
        try:
            process = subprocess.Popen(
                [crontab_path, "-"],
                stdin=subprocess.PIPE,
                text=True,
            )
            process.communicate(input=new_cron, timeout=CRON_COMMAND_TIMEOUT_SECONDS)
            print("Reboot cron job added to run Fetchtastic on system startup.")
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.error(
                f"An error occurred while setting up the reboot cron job: {exc}"
            )
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"An error occurred while setting up the reboot cron job: {exc}")


@cron_command_required
def remove_reboot_cron_job(*, crontab_path: str = "crontab"):
    """
    Remove any @reboot cron entries that run or are labeled for Fetchtastic.

    If running on Windows the function performs no action. Otherwise it reads the current user crontab using the provided `crontab_path`, removes any `@reboot` lines that invoke or are commented for Fetchtastic, and writes the updated crontab back. Errors encountered while reading or writing are logged.
    Parameters:
        crontab_path (str): Path to the `crontab` executable used to read and write the user's crontab.
    """
    # Skip cron job removal on Windows
    if platform.system() == "Windows":
        print("Cron jobs are not supported on Windows.")
        return

    try:
        # Get current crontab entries
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            existing_cron = result.stdout.strip()
            # Remove existing @reboot Fetchtastic cron jobs
            cron_lines = [line for line in existing_cron.splitlines() if line.strip()]
            cron_lines = [
                line
                for line in cron_lines
                if not (
                    ("# fetchtastic" in line or "fetchtastic download" in line)
                    and line.strip().startswith("@reboot")
                )
            ]
            # Join cron lines
            new_cron = "\n".join(cron_lines)
            # Ensure new_cron ends with a newline
            if not new_cron.endswith("\n"):
                new_cron += "\n"
            try:
                process = subprocess.Popen(
                    [crontab_path, "-"],
                    stdin=subprocess.PIPE,
                    text=True,
                )
                process.communicate(
                    input=new_cron, timeout=CRON_COMMAND_TIMEOUT_SECONDS
                )
                print("Reboot cron job removed.")
            except (
                subprocess.SubprocessError,
                subprocess.TimeoutExpired,
                OSError,
            ) as exc:
                logger.error(
                    f"An error occurred while removing the reboot cron job: {exc}"
                )
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"An error occurred while removing the reboot cron job: {exc}")


@cron_check_command_required
def check_any_cron_jobs_exist(*, crontab_path: str = "crontab"):
    """
    Determine whether the current user's crontab contains any entries.

    Parameters:
        crontab_path (str): Path to the `crontab` executable used to list the user's crontab.

    Returns:
        bool: `True` if at least one cron entry exists for the current user, `False` if the crontab is empty or an error occurs.
    """
    try:
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False
        existing_cron = result.stdout.strip()
        return any(line.strip() for line in existing_cron.splitlines())
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"An error occurred while checking for existing cron jobs: {exc}")
        return False


def check_boot_script_exists() -> bool:
    """
    Determine whether the Termux boot script for Fetchtastic exists.

    This checks for the file at ~/.termux/boot/fetchtastic.sh; on non-Termux platforms the path will not exist and the function returns False.

    Returns:
        True if the boot script file exists at ~/.termux/boot/fetchtastic.sh, False otherwise.
    """
    boot_script = os.path.expanduser("~/.termux/boot/fetchtastic.sh")
    return os.path.exists(boot_script)


@cron_check_command_required
def check_cron_job_exists(*, crontab_path: str = "crontab"):
    """
    Determine whether any Fetchtastic cron entries exist in the current user's crontab (ignoring '@reboot' lines).

    Parameters:
        crontab_path (str): Path or command name for the `crontab` executable to invoke.

    Returns:
        True if any matching Fetchtastic cron entries are found (excluding lines that start with '@reboot'), False otherwise.
    """
    try:
        result = subprocess.run(
            [crontab_path, "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CRON_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False
        existing_cron = result.stdout.strip()
        return any(
            ("# fetchtastic" in line or "fetchtastic download" in line)
            for line in existing_cron.splitlines()
            if not line.strip().startswith("@reboot")
        )
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"An error occurred while checking for existing cron jobs: {exc}")
        return False


def load_config(directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Load configuration from a YAML file and return it as a dictionary.

    If `directory` is provided, looks for CONFIG_FILE_NAME in that directory and loads it.
    Otherwise checks the new platform-specific CONFIG_FILE first, then the legacy OLD_CONFIG_FILE.
    When a configuration is loaded, the global BASE_DIR is updated from the config or set to
    the provided directory. When a config is found in a non-standard or legacy location,
    the function may print a message suggesting migration to the standard CONFIG_FILE location.

    Parameters:
        directory (str | None): Optional directory to load CONFIG_FILE_NAME from. If omitted,
                                default locations (CONFIG_FILE then OLD_CONFIG_FILE) are used.

    Returns:
        dict | None: Parsed configuration dictionary if a config file was found and parsed,
                     otherwise `None`.
    """
    global BASE_DIR

    if directory:
        # This is for backward compatibility or when explicitly loading from a specific directory
        config_path = os.path.join(directory, CONFIG_FILE_NAME)
        if not os.path.exists(config_path):
            return None

        config = _load_yaml_mapping(config_path)
        if config is None:
            return None

        # Update global variables
        BASE_DIR = directory

        # If we're loading from a non-standard location, check if we should migrate
        if config_path != CONFIG_FILE and config_path != OLD_CONFIG_FILE:
            print(f"Found configuration in non-standard location: {config_path}")
            print(f"Consider migrating to the standard location: {CONFIG_FILE}")

        return config
    else:
        # First check if config exists in the platformdirs location
        if os.path.exists(CONFIG_FILE):
            config = _load_yaml_mapping(CONFIG_FILE)
            if config is None:
                return None

            # Update BASE_DIR from config
            if "BASE_DIR" in config:
                BASE_DIR = config["BASE_DIR"]

            return config

        # Then check the old location
        elif os.path.exists(OLD_CONFIG_FILE):
            config = _load_yaml_mapping(OLD_CONFIG_FILE)
            if config is None:
                return None

            # Update BASE_DIR from config
            if "BASE_DIR" in config:
                BASE_DIR = config["BASE_DIR"]

            # Suggest migration
            print(f"Using configuration from old location: {OLD_CONFIG_FILE}")
            print(
                f"Consider running setup to migrate to the new location: {CONFIG_FILE}"
            )

            return config

        return None
