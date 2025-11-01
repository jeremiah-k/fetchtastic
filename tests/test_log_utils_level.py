import logging

import pytest

from fetchtastic.log_utils import logger, set_log_level


def _get_rich_handler():
    """
    Return the first logging handler attached to the module logger whose class name is "RichHandler", or None if not found.

    Uses duck-typing (inspecting the handler's class name) instead of importing the Rich library.
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

    # Switch to DEBUG and verify clean formatter
    set_log_level("DEBUG")
    assert logger.level == logging.DEBUG
    assert rich.level == logging.DEBUG
    fmt_debug = getattr(rich.formatter, "_fmt", "")
    assert "%(message)s" in fmt_debug

    # Switch back to INFO and verify terse formatter
    set_log_level("INFO")
    assert logger.level == logging.INFO
    assert rich.level == logging.INFO
    fmt_info = getattr(rich.formatter, "_fmt", "")
    assert fmt_info and "%(message)s" in fmt_info
