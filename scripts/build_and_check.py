"""Build distributions and smoke check the wheel in a separate environment."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
distribution_dir = root / "dist"
if distribution_dir.exists():
    shutil.rmtree(distribution_dir)
subprocess.run([sys.executable, "-m", "build", "--no-isolation"], cwd=root, check=True)
distributions = sorted(distribution_dir.glob("*"))
subprocess.run(
    [sys.executable, "-m", "twine", "check", *map(str, distributions)], check=True
)
wheels = [path for path in distributions if path.suffix == ".whl"]
if len(wheels) != 1:
    raise RuntimeError("Expected exactly one wheel from the build")

with tempfile.TemporaryDirectory(prefix="fetchtastic-wheel-") as temporary:
    subprocess.run([sys.executable, "-m", "venv", temporary], check=True)
    python = Path(temporary) / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])], check=True)
    subprocess.run(
        [str(python), "-I", str(root / "scripts/check_installed_package.py")],
        check=True,
    )
