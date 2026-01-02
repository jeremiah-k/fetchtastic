import os
from pathlib import Path

import pytest

from fetchtastic.build.mtand.modules import MtandBuildModule


@pytest.mark.unit
def test_mtand_build_module_build_debug_copies_apk(mocker, tmp_path):
    module = MtandBuildModule()
    repo_base_dir = tmp_path / "builds"
    repo_dir = repo_base_dir / module.repo_dirname
    base_dir = tmp_path / "base"

    (repo_dir / ".git").mkdir(parents=True)
    gradlew = repo_dir / "gradlew"
    gradlew.write_text("#!/bin/sh\nexit 0\n")
    debug_apk = (
        repo_dir
        / "app"
        / "build"
        / "outputs"
        / "apk"
        / "google"
        / "debug"
        / "app-google-debug.apk"
    )
    debug_apk.parent.mkdir(parents=True, exist_ok=True)
    debug_apk.write_bytes(b"apk")

    mocker.patch("fetchtastic.build.base.git_identifier", return_value="deadbeef")
    mocker.patch("fetchtastic.build.base.subprocess.run", return_value=mocker.Mock())

    result = module.build(
        "debug",
        base_dir=str(base_dir),
        repo_base_dir=str(repo_base_dir),
        sdk_root=None,
        allow_update=False,
    )

    assert result.success is True
    assert result.dest_path is not None
    dest_path = Path(result.dest_path)
    assert dest_path.exists()
    assert dest_path.name == "mtand-deadbeef.apk"
    assert os.path.dirname(result.dest_path).endswith(module.output_dirname)
