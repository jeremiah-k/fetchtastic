"""The platform installer paths refer to the shipped resources."""

from pathlib import Path

import pytest

from fetchtastic.tools import get_install_script_path


@pytest.mark.parametrize(
    "platform,filename",
    [("windows", "setup_fetchtastic.ps1"), ("linux", "setup_fetchtastic.sh")],
)
def test_install_script_resource_exists(platform, filename):
    path = Path(get_install_script_path(platform))
    assert path.name == filename
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip()
