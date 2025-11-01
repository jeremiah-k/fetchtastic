import logging
import os  # Added for environment variable
from logging.handlers import RotatingFileHandler  # Already here, ensure it stays
from pathlib import Path
from typing import Optional  # Added Optional

from rich.logging import RichHandler  # Keep Rich for console

from fetchtastic.constants import (
    DEBUG_LOG_FORMAT,
    INFO_LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_FILE_BACKUP_COUNT,
    LOG_FILE_MAX_BYTES,
    LOG_LEVEL_ENV_VAR,
    LOGGER_NAME,
)

# 1. Initialize a Logger
logger = logging.getLogger(LOGGER_NAME)

# Global variable for the file handler to allow removal/reconfiguration if needed
_file_handler: Optional[RotatingFileHandler] = None


def set_log_level(level_name: str) -> None:
    """
    Set the log level for the fetchtastic logger and reconfigure all attached handlers.

    If `level_name` is not a valid logging level name (e.g., "DEBUG", "INFO"), the function logs a warning and leaves the current configuration unchanged.

    Behavior:
    - Sets the logger's level and each handler's level to the resolved level.
    - Replaces each handler's formatter according to the new level:
      - For INFO and above:
        - RichHandler: message-only formatter ("%(message)s").
        - Non-Rich handlers: INFO_LOG_FORMAT with LOG_DATE_FORMAT.
       - For levels below INFO:
         - RichHandler: message-only formatter for cleaner console output.
         - Non-Rich handlers: DEBUG_LOG_FORMAT with LOG_DATE_FORMAT.
    - Emits a log at the configured level confirming the new level when successful.

    Parameters:
        level_name (str): Case-insensitive name of the desired logging level (e.g., "debug", "INFO").
    """
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        logger.warning(f"Invalid log level name: {level_name}. Using current level.")
        return

    logger.setLevel(level)

    # Update formatters for all handlers based on new level
    for handler in logger.handlers:
        handler.setLevel(level)

        # Update formatter based on level - never show module/function/line for INFO and above
        if level >= logging.INFO:
            if isinstance(handler, RichHandler):
                formatter = logging.Formatter("%(message)s")
            else:
                formatter = logging.Formatter(INFO_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        else:
            if isinstance(handler, RichHandler):
                formatter = logging.Formatter("%(message)s")
            else:
                formatter = logging.Formatter(DEBUG_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

        handler.setFormatter(formatter)

    logger.log(level, f"Log level set to {logging.getLevelName(level)}")


def add_file_logging(log_dir_path: Path, level_name: str = "INFO") -> None:
    """
    Enable rotating file logging for the fetchtastic logger.

    Creates the directory if necessary and attaches a RotatingFileHandler writing to
    `fetchtastic.log` inside the provided directory. The handler's level is taken
    from `level_name` (falls back to INFO for invalid names). Formatter verbosity is
    chosen based on the resolved level (INFO-or-higher uses the informational format;
    below INFO uses the debug format). Existing file logging configured by this
    module is removed and closed before reconfiguring. The handler uses the module's
    configured max-bytes and backup-count rotation constants.
    """
    global _file_handler
    if _file_handler and _file_handler in logger.handlers:
        logger.removeHandler(_file_handler)  # Remove existing if any, to reconfigure
        _file_handler.close()

    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / "fetchtastic.log"

    # Choose formatter based on log level
    resolved = getattr(logging, level_name.upper(), None)
    if not isinstance(resolved, int):
        logger.warning(
            f"Invalid file log level name: {level_name}. Defaulting to INFO."
        )
        resolved = logging.INFO
    file_log_level = resolved
    if file_log_level >= logging.INFO:
        file_formatter = logging.Formatter(INFO_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    else:
        file_formatter = logging.Formatter(DEBUG_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    _file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    _file_handler.setFormatter(file_formatter)
    _file_handler.setLevel(file_log_level)

    logger.addHandler(_file_handler)
    logger.info(
        f"File logging enabled at {log_file} with level {logging.getLevelName(file_log_level)}"
    )


def _initialize_logger() -> None:
    """
    Initialize the fetchtastic logger with a console RichHandler and an initial log level.

    This removes any existing handlers, disables propagation to the root logger, and attaches a RichHandler configured for console output. The initial log level is read from the environment variable named by LOG_LEVEL_ENV_VAR (defaults to "INFO" if unset) and applied to both the logger and the console handler. When the level is INFO or higher a terse formatter ("%(message)s") is used; for levels below INFO a more verbose formatter including module, function and line number is applied. File logging is not enabled by default; call add_file_logging() to enable rotating file output.
    """
    # Prevent propagation to root logger
    logger.propagate = False

    # Remove any pre-existing handlers from previous imports or runs (especially in interactive sessions)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    # Configure Console Handler (using RichHandler)
    console_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_level=True,
        show_path=False,
        markup=True,
        log_time_format=LOG_DATE_FORMAT,
    )

    # Set initial log level from environment variable or default to INFO
    default_log_level = os.environ.get(LOG_LEVEL_ENV_VAR, "INFO").upper()
    resolved = getattr(logging, default_log_level, None)
    if not isinstance(resolved, int):
        logger.warning(
            f"Invalid {LOG_LEVEL_ENV_VAR}={default_log_level}; defaulting to INFO."
        )
        resolved = logging.INFO
    initial_level = resolved

    # Choose console formatter based on log level
    if initial_level >= logging.INFO:
        console_formatter = logging.Formatter("%(message)s")
    else:
        console_formatter = logging.Formatter("%(message)s")

    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logger.setLevel(initial_level)  # Set logger level first
    console_handler.setLevel(initial_level)  # Then set handler level

    # Note: File logging is not enabled by default, call add_file_logging() to enable it.
    # This is a change from the original setup_logging which could do both.


# Initialize the logger when the module is imported
_initialize_logger()

# The old functions like setup_logging, get_logger, log_message, log_info, etc.,
# are removed as per the requirement to use the `logger` object directly.
# Modules should now `from fetchtastic.log_utils import logger, set_log_level, add_file_logging`
# and use `logger.info()`, `logger.error()`, etc.
# The Rich console object is no longer explicitly part of this module's public API,
# as RichHandler manages its own console.
# LOG_LEVEL_STYLES is also removed as RichHandler handles styling.
# The global `config` and `log_file_path` are also removed or managed internally.

if __name__ == "__main__":
    # Example Usage:
    logger.debug("This is a debug message.")
    logger.info("This is an info message.")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")
    logger.critical("This is a critical message.")

    set_log_level("DEBUG")
    logger.debug("This is another debug message after changing level.")

    try:
        _ = 1 / 0  # Intentional division by zero for testing exception logging
    except ZeroDivisionError:
        logger.exception("A handled exception occurred (logged with exception info).")

    # To test file logging (assuming you have a directory ./logs)
    # from pathlib import Path
    # log_dir = Path("./logs")
    # add_file_logging(log_dir, level_name="DEBUG")
    # logger.info("This message should go to both console and file.")
    # logger.debug("This debug message should also go to both console and file.")

    # Test FETCHTASTIC_LOG_LEVEL (run as `FETCHTASTIC_LOG_LEVEL=DEBUG python src/fetchtastic/log_utils.py`)
    # print(f"Logger effective level: {logging.getLevelName(logger.getEffectiveLevel())}")
    # for handler in logger.handlers:
    # print(f"Handler {handler.name if hasattr(handler, 'name') else handler} level: {logging.getLevelName(handler.level)}")
    pass
