"""Tools and resources for Fetchtastic."""

import importlib.resources
import os
import pathlib


def get_batch_file_path(filename):
    """Get the path to a batch file in the tools directory.

    Args:
        filename: The name of the batch file

    Returns:
        str: The path to the batch file
    """
    try:
        # For Python 3.9+
        return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))
    except AttributeError:
        # Fallback for older Python versions
        return str(pathlib.Path(__file__).parent / filename)


def get_install_script_path(platform):
    """Get the path to the installation script for the specified platform.

    Args:
        platform: The platform name ('windows', 'linux', 'mac')

    Returns:
        str: The path to the installation script
    """
    filename = f"install_{platform}.{'bat' if platform == 'windows' else 'sh'}"
    try:
        # For Python 3.9+
        return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))
    except AttributeError:
        # Fallback for older Python versions
        return str(pathlib.Path(__file__).parent / filename)
