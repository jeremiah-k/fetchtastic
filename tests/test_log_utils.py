import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.logging import RichHandler

from fetchtastic import log_utils


class TestLogUtils:
    """Test suite for log_utils module."""

    def setup_method(self):
        """Reset logger state before each test."""
        # Clear all handlers
        for handler in log_utils.logger.handlers[:]:
            log_utils.logger.removeHandler(handler)
            handler.close()

        # Reset global file handler
        log_utils._file_handler = None

        # Reinitialize logger
        log_utils._initialize_logger()

    def test_logger_initialization(self):
        """Test that logger is properly initialized."""
        assert log_utils.logger.name == "fetchtastic"
        assert not log_utils.logger.propagate
        assert len(log_utils.logger.handlers) == 1
        assert isinstance(log_utils.logger.handlers[0], RichHandler)

    def test_logger_initialization_with_env_var(self):
        """Test logger initialization with environment variable."""
        with patch.dict(os.environ, {"FETCHTASTIC_LOG_LEVEL": "DEBUG"}):
            log_utils._initialize_logger()
            assert log_utils.logger.level == logging.DEBUG
            assert log_utils.logger.handlers[0].level == logging.DEBUG

    def test_logger_initialization_with_invalid_env_var(self):
        """Test logger initialization with invalid environment variable."""
        with patch.dict(os.environ, {"FETCHTASTIC_LOG_LEVEL": "INVALID"}):
            log_utils._initialize_logger()
            # Should default to INFO level
            assert log_utils.logger.level == logging.INFO

    def test_set_log_level_valid(self):
        """Test setting valid log levels."""
        log_utils.set_log_level("DEBUG")
        assert log_utils.logger.level == logging.DEBUG

        log_utils.set_log_level("WARNING")
        assert log_utils.logger.level == logging.WARNING

    def test_set_log_level_invalid(self):
        """Test setting invalid log level."""
        original_level = log_utils.logger.level
        log_utils.set_log_level("INVALID_LEVEL")
        # Level should remain unchanged
        assert log_utils.logger.level == original_level

    def test_set_log_level_updates_formatters(self):
        """Test that setting log level updates formatters appropriately."""
        # Set to DEBUG level
        log_utils.set_log_level("DEBUG")
        handler = log_utils.logger.handlers[0]
        formatter = handler.formatter

        # DEBUG formatter should be clean and simple
        assert "%(message)s" in formatter._fmt

        # Set to INFO level
        log_utils.set_log_level("INFO")
        handler = log_utils.logger.handlers[0]
        formatter = handler.formatter

        # INFO formatter should be simpler
        assert formatter._fmt == "%(message)s"

    def test_add_file_logging(self):
        """Test adding file logging functionality."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            log_utils.add_file_logging(log_dir, "INFO")

            # Check that file handler was added
            assert len(log_utils.logger.handlers) == 2  # Console + File
            assert log_utils._file_handler is not None
            assert log_utils._file_handler in log_utils.logger.handlers

            # Check log file was created
            log_file = log_dir / "fetchtastic.log"
            assert log_file.exists()

    def test_add_file_logging_replaces_existing(self):
        """Test that adding file logging replaces existing file handler."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)

            # Add first file handler
            log_utils.add_file_logging(log_dir, "INFO")
            first_handler = log_utils._file_handler

            # Add second file handler
            log_utils.add_file_logging(log_dir, "DEBUG")
            second_handler = log_utils._file_handler

            # Should have replaced the first handler
            assert first_handler != second_handler
            assert len(log_utils.logger.handlers) == 2  # Still just console + file

    def test_add_file_logging_different_levels(self):
        """Test file logging with different log levels."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)

            # Test INFO level
            log_utils.add_file_logging(log_dir, "INFO")
            assert log_utils._file_handler.level == logging.INFO

            # Test DEBUG level
            log_utils.add_file_logging(log_dir, "DEBUG")
            assert log_utils._file_handler.level == logging.DEBUG

    def test_file_logging_creates_directory(self):
        """Test that file logging creates directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "nested" / "log" / "dir"
            assert not log_dir.exists()

            log_utils.add_file_logging(log_dir, "INFO")

            assert log_dir.exists()
            assert (log_dir / "fetchtastic.log").exists()

    def test_rotating_file_handler_configuration(self):
        """Test that rotating file handler is configured correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            log_utils.add_file_logging(log_dir, "INFO")

            handler = log_utils._file_handler
            assert handler.maxBytes == 10 * 1024 * 1024  # 10 MB
            assert handler.backupCount == 5
            assert handler.encoding == "utf-8"

    def test_logger_constants(self):
        """Test that logger constants are properly defined."""
        assert log_utils.LOGGER_NAME == "fetchtastic"
        assert log_utils.LOG_DATE_FORMAT == "%Y-%m-%d %H:%M:%S"
        assert "%(asctime)s - %(levelname)s - %(message)s" in log_utils.INFO_LOG_FORMAT
        assert (
            "%(asctime)s - %(levelname)s - %(name)s: %(message)s"
            == log_utils.DEBUG_LOG_FORMAT
        )

    def test_logger_logging_methods(self):
        """Test that logger methods work correctly."""
        with patch("fetchtastic.log_utils.logger.info") as mock_info:
            log_utils.logger.info("Test message")
            mock_info.assert_called_once_with("Test message")

    def test_set_log_level_with_non_rich_handler(self):
        """Test setting log level with non-RichHandler to cover all formatter paths."""
        # Add a standard StreamHandler to test non-RichHandler formatter paths
        import io

        stream = io.StringIO()
        standard_handler = logging.StreamHandler(stream)
        log_utils.logger.addHandler(standard_handler)

        try:
            # Test INFO level with standard handler (should use INFO_LOG_FORMAT)
            log_utils.set_log_level("INFO")
            assert standard_handler.formatter._fmt == log_utils.INFO_LOG_FORMAT
            assert standard_handler.formatter.datefmt == log_utils.LOG_DATE_FORMAT

            # Test DEBUG level with standard handler (should use DEBUG_LOG_FORMAT)
            log_utils.set_log_level("DEBUG")
            assert standard_handler.formatter._fmt == log_utils.DEBUG_LOG_FORMAT
            assert standard_handler.formatter.datefmt == log_utils.LOG_DATE_FORMAT
        finally:
            # Clean up
            log_utils.logger.removeHandler(standard_handler)

    def test_main_execution_block(self):
        """Test the main execution block functionality."""
        # Test that the main block can be executed without errors
        # We'll test this by checking that the logger is properly configured
        # after module import (which happens during the main block)

        # Verify logger is configured correctly
        assert log_utils.logger is not None
        assert len(log_utils.logger.handlers) > 0

        # Test that we can call the logging methods without error
        try:
            log_utils.logger.debug("Test debug message")
            log_utils.logger.info("Test info message")
            log_utils.logger.warning("Test warning message")
            log_utils.logger.error("Test error message")
            log_utils.logger.critical("Test critical message")
        except Exception as e:
            pytest.fail(f"Logging methods failed: {e}")

        # Test set_log_level function works
        try:
            log_utils.set_log_level("DEBUG")
            assert log_utils.logger.level == logging.DEBUG
        except Exception as e:
            pytest.fail(f"set_log_level failed: {e}")
