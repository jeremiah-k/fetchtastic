"""Tools and resources for Fetchtastic."""

import importlib.resources


def get_batch_file_path(filename: str) -> str:
    """
    Get the filesystem path to a batch file located in the fetchtastic.tools package resources.

    Parameters:
        filename (str): Name of the resource file to locate within the package.

    Returns:
        str: Filesystem path to the requested batch file.
    """
    # For Python 3.10+
    return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))


def get_install_script_path(platform: str) -> str:
    """
    Get the filesystem path to the Fetchtastic installation script for the given platform.

    Parameters:
        platform (str): Use 'windows' for the PowerShell installer; other values select the shell installer.

    Returns:
        str: Filesystem path to the selected installation script.
    """
    filename = f"setup_fetchtastic.{'ps1' if platform == 'windows' else 'sh'}"
    # For Python 3.10+
    return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))
