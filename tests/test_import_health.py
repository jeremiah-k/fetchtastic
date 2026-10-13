"""Guards for the CLI's cold-import path across package boundaries."""

import subprocess
import sys


def test_cli_cold_import_in_fresh_interpreter() -> None:
    """Importing fetchtastic.cli first must not hit a partially initialized module.

    The download package imports back into setup_config (for example
    is_termux), while setup_config imports the menu modules. A menu module
    importing the download package at module level therefore closes a cycle
    that only breaks when the CLI is imported first in a fresh interpreter —
    the state every installed launch and wheel smoke check starts from.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import fetchtastic.cli"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
