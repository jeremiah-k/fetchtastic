"""Check an installed wheel without importing the source checkout.

Run with an isolated interpreter: python -I scripts/check_installed_package.py.
RELEASE_TAG, when set, must match the distribution's version before publication.
"""

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import fetchtastic
from fetchtastic.tools import get_install_script_path

checkout = Path(__file__).resolve().parent.parent
module = Path(fetchtastic.__file__).resolve()
if module.is_relative_to(checkout / "src"):
    raise RuntimeError("Package smoke check imported the source checkout")

version = importlib.metadata.version("fetchtastic")
tag = os.environ.get("RELEASE_TAG")
if tag is not None and tag.removeprefix("v") != version:
    raise RuntimeError(
        f"Release tag {tag!r} does not match package version {version!r}"
    )

for platform in ("windows", "linux"):
    script = Path(get_install_script_path(platform))
    if not script.is_file() or not script.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"Installer resource missing for {platform}: {script}")

subprocess.run([sys.executable, "-I", "-m", "fetchtastic.cli", "--help"], check=True)
print(f"Validated installed Fetchtastic {version}")
