"""
Environment detection helpers.
"""

from __future__ import annotations

import os


def is_termux() -> bool:
    """
    Check if the current environment is Termux.
    """
    return "com.termux" in os.environ.get("PREFIX", "")
