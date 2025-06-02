import logging
import os  # Added for environment variable
from logging.handlers import RotatingFileHandler  # Already here, ensure it stays
from pathlib import Path
from typing import Optional  # Added Optional and Any

from rich.logging import RichHandler  # Keep Rich for console

# 1. Initialize a Logger
LOGGER_NAME = "fetchtastic"
logger = logging.getLogger(LOGGER_NAME)

# Log message formats - split between INFO and DEBUG levels
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
INFO_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DEBUG_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"

# Global variable for the file handler to allow removal/reconfiguration if needed
_file_handler: Optional[RotatingFileHandler] = None


def set_log_level(level_name: str) -> None:
    """
    Set the logging level for the 'fetchtastic' logger and its handlers.
    Also updates formatters based on the new level.

    Args:
        level_name (str): The desired logging level (e.g., "DEBUG", "INFO").
    """
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        logger.warning(f"Invalid log level name: {level_name}. Using current level.")
        return

    logger.setLevel(level)

    # Update formatters for all handlers based on new level
    for handler in logger.handlers:
        handler.setLevel(level)

        # Update formatter based on level
        if level >= logging.INFO:
            if isinstance(handler, RichHandler):
                formatter = logging.Formatter("%(message)s")
            else:
                formatter = logging.Formatter(INFO_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        else:
            if isinstance(handler, RichHandler):
                formatter = logging.Formatter(
                    "%(message)s (%(name)s - %(module)s.%(funcName)s:%(lineno)d)"
                )
            else:
                formatter = logging.Formatter(DEBUG_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

        handler.setFormatter(formatter)

    logger.info(f"Log level set to {level_name.upper()}")


def add_file_logging(log_dir_path: Path, level_name: str = "INFO") -> None:
    """
    Adds file logging to the 'fetchtastic' logger.

    Args:
        log_dir_path (Path): The directory to store log files.
        level_name (str): The logging level for the file handler.
    """
    global _file_handler
    if _file_handler and _file_handler in logger.handlers:
        logger.removeHandler(_file_handler)  # Remove existing if any, to reconfigure
        _file_handler.close()

    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / "fetchtastic.log"

    # Choose formatter based on log level
    file_log_level = getattr(logging, level_name.upper(), logging.INFO)
    if file_log_level >= logging.INFO:
        file_formatter = logging.Formatter(INFO_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    else:
        file_formatter = logging.Formatter(DEBUG_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    _file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setFormatter(file_formatter)
    _file_handler.setLevel(file_log_level)

    logger.addHandler(_file_handler)
    logger.info(f"File logging enabled at {log_file} with level {level_name.upper()}")


def _initialize_logger() -> None:
    """
    Initializes the 'fetchtastic' logger with a default console handler.
    Reads log level from FETCHTASTIC_LOG_LEVEL environment variable if set.
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
    default_log_level = os.environ.get("FETCHTASTIC_LOG_LEVEL", "INFO").upper()
    initial_level = getattr(logging, default_log_level, logging.INFO)

    # Choose console formatter based on log level
    if initial_level >= logging.INFO:
        console_formatter = logging.Formatter("%(message)s")
    else:
        console_formatter = logging.Formatter(
            "%(message)s (%(name)s - %(module)s.%(funcName)s:%(lineno)d)"
        )

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
