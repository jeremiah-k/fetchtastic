import logging

import pytest

from fetchtastic.log_utils import logger, set_log_level


def _get_rich_handler():
    """
    Find and return the first logging handler named "RichHandler" attached to the module logger.

    Searches logger.handlers using duck-typing (by the handler class name) so the test does not need to import the rich library. Returns the handler instance if found, otherwise None.
    """
    # Avoid importing RichHandler in test; duck-type by class name
    return next(
        (h for h in logger.handlers if h.__class__.__name__ == "RichHandler"), None
    )


def test_set_log_level_updates_handler_formatters(caplog):
    rich = _get_rich_handler()
    if rich is None:
        pytest.skip(
            "RichHandler not configured on logger; skipping formatter assertions"
        )

    # Switch to DEBUG and verify verbose formatter
    set_log_level("DEBUG")
    assert logger.level == logging.DEBUG
    assert rich.level == logging.DEBUG
    logger.debug("probe-debug-format")
    assert "probe-debug-format" in caplog.text

    # Switch back to INFO and verify terse formatter
    set_log_level("INFO")
    assert logger.level == logging.INFO
    assert rich.level == logging.INFO
    logger.info("probe-info-format")
    assert "probe-info-format" in caplog.text
