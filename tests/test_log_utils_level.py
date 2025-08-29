import logging

import pytest

from fetchtastic.log_utils import logger, set_log_level


def _get_rich_handler():
    """
    Find and return the first logging handler named "RichHandler" attached to the module logger.
    
    Searches logger.handlers using duck-typing (by the handler class name) so the test does not need to import the rich library. Returns the handler instance if found, otherwise None.
    """
    for h in logger.handlers:
        # Avoid importing RichHandler in test; duck-type by class name
        if h.__class__.__name__ == "RichHandler":
            return h
    return None


def test_set_log_level_updates_handler_formatters():
    rich = _get_rich_handler()
    if rich is None:
        pytest.skip(
            "RichHandler not configured on logger; skipping formatter assertions"
        )

    # Switch to DEBUG and verify verbose formatter
    set_log_level("DEBUG")
    assert logger.level == logging.DEBUG
    assert rich.level == logging.DEBUG
    fmt_debug = getattr(rich.formatter, "_fmt", "")
    assert "%(module)s" in fmt_debug or "%(funcName)s" in fmt_debug

    # Switch back to INFO and verify terse formatter
    set_log_level("INFO")
    assert logger.level == logging.INFO
    assert rich.level == logging.INFO
    fmt_info = getattr(rich.formatter, "_fmt", "")
    assert fmt_info and "%(message)s" in fmt_info
