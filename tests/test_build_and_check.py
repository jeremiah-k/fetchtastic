"""Exercise distribution selection without building or installing packages."""

import importlib.metadata
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.infrastructure]


@pytest.fixture
def build_script(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts/build_and_check.py"
    script = scripts / source.name
    shutil.copyfile(source, script)
    return script


def test_build_checks_and_installs_only_fresh_distributions(build_script, monkeypatch):
    root = build_script.parent.parent
    dist = root / "dist"
    dist.mkdir()
    (dist / "stale.whl").write_bytes(b"stale wheel")
    (dist / "stale.tar.gz").write_bytes(b"stale sdist")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True
        if command[1:3] == ["-m", "build"]:
            assert not dist.exists()
            dist.mkdir()
            (dist / "fresh.whl").write_bytes(b"fresh wheel")
            (dist / "fresh.tar.gz").write_bytes(b"fresh sdist")

    monkeypatch.setattr(subprocess, "run", run)
    runpy.run_path(str(build_script))

    twine = next(call for call in calls if call[1:3] == ["-m", "twine"])
    assert set(twine[4:]) == {str(dist / "fresh.whl"), str(dist / "fresh.tar.gz")}
    install = next(call for call in calls if call[1:4] == ["-m", "pip", "install"])
    assert install[4:] == [str(dist / "fresh.whl")]
    assert sorted(path.name for path in dist.iterdir()) == ["fresh.tar.gz", "fresh.whl"]


def test_failed_build_stops_before_distribution_checks(build_script, monkeypatch):
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        runpy.run_path(str(build_script))
    assert len(calls) == 1


def test_installed_package_check_accepts_equivalent_pep440_release_tag(
    tmp_path, monkeypatch
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts/check_installed_package.py"
    script = scripts / source.name
    shutil.copyfile(source, script)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.0.0rc1")
    monkeypatch.setenv("RELEASE_TAG", "v1.0.0-rc1")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: None)
    runpy.run_path(str(script))
