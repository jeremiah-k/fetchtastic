import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# Initialize Rich console
console = Console()

# Define custom log level styles
LOG_LEVEL_STYLES = {
    "DEBUG": "dim blue",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold white on red",
}

# Global variables
config = None
log_file_path = None


def setup_logging(base_dir=None, log_level="INFO"):
    """
    Set up logging with Rich formatting.

    Args:
        base_dir: Base directory for log files. If None, logs will only go to console.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Logger object
    """
    # Create logger
    logger = logging.getLogger("Fetchtastic")

    # Set log level
    log_level = getattr(logging, log_level.upper())
    logger.setLevel(log_level)

    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Prevent propagation to root logger
    logger.propagate = False

    # Add Rich console handler
    console_handler = RichHandler(
        rich_tracebacks=True,
        console=console,
        show_time=True,
        show_level=True,
        show_path=False,
        markup=True,
        log_time_format="%Y-%m-%d %H:%M:%S",
        omit_repeated_times=False,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    # Add file handler if base_dir is provided
    if base_dir:
        log_dir = Path(base_dir)
        log_dir.mkdir(exist_ok=True, parents=True)

        global log_file_path
        log_file_path = log_dir / "fetchtastic.log"

        # Set up size-based log rotation (10 MB max, keep 3 backups)
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    return logger


def get_logger():
    """
    Get the Fetchtastic logger. If it doesn't exist, create a basic console logger.

    Returns:
        Logger object
    """
    logger = logging.getLogger("Fetchtastic")

    # If logger has no handlers, set up a basic console handler
    if not logger.handlers:
        logger = setup_logging()

    return logger


def log_message(message, level="INFO"):
    """
    Log a message with the specified level.

    Args:
        message: Message to log
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logger = get_logger()
    log_method = getattr(logger, level.lower())
    log_method(message)


def log_debug(message):
    """Log a debug message"""
    log_message(message, "DEBUG")


def log_info(message):
    """Log an info message"""
    log_message(message, "INFO")


def log_warning(message):
    """Log a warning message"""
    log_message(message, "WARNING")


def log_error(message):
    """Log an error message"""
    log_message(message, "ERROR")


def log_critical(message):
    """Log a critical message"""
    log_message(message, "CRITICAL")
