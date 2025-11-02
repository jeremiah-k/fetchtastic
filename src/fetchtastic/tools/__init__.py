"""Tools and resources for Fetchtastic."""

import importlib.resources


def get_batch_file_path(filename):
    """
    Return the filesystem path to a batch file stored in the `fetchtastic.tools` package resources.

    Parameters:
        filename (str): Name of the batch file within the package resources.

    Returns:
        str: Filesystem path to the requested batch file.
    """
    # For Python 3.10+
    return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))


def get_install_script_path(platform):
    """
    Get the filesystem path to the Fetchtastic installation script for the given platform.

    Parameters:
        platform (str): Platform name; use 'windows' to select the Windows batch installer, any other value selects the Unix shell installer.

    Returns:
        str: Filesystem path to the selected installation script.
    """
    filename = f"fetchtastic-setup.{'bat' if platform == 'windows' else 'sh'}"
    # For Python 3.10+
    return str(importlib.resources.files("fetchtastic.tools").joinpath(filename))
