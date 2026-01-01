import os

import pytest

from fetchtastic.build.environment import (
    build_shell_exports,
    detect_java_home,
    detect_missing_termux_packages,
    missing_sdk_packages,
    render_shell_block,
    update_process_env,
    update_shell_config,
)


@pytest.mark.unit
def test_detect_missing_termux_packages(mocker):
    mocker.patch(
        "fetchtastic.build.environment.shutil.which",
        side_effect=lambda cmd: None if cmd in {"javac", "java", "git"} else "/bin/ok",
    )
    missing = detect_missing_termux_packages()
    assert "openjdk-17" in missing
    assert "git" in missing


@pytest.mark.unit
def test_detect_java_home_from_env(mocker):
    mocker.patch.dict(os.environ, {"JAVA_HOME": "/tmp/java"}, clear=True)
    mocker.patch("fetchtastic.build.environment.os.path.isdir", return_value=True)
    assert detect_java_home() == "/tmp/java"


@pytest.mark.unit
def test_shell_block_update(tmp_path):
    exports, path_entries = build_shell_exports("/tmp/java", "/tmp/sdk")
    block = render_shell_block(exports, path_entries)
    rc_path = tmp_path / ".bashrc"

    assert update_shell_config(str(rc_path), block) is True
    content = rc_path.read_text()
    assert "JAVA_HOME" in content
    assert "ANDROID_SDK_ROOT" in content


@pytest.mark.unit
def test_missing_sdk_packages(tmp_path):
    sdk_root = tmp_path / "sdk"
    (sdk_root / "platforms" / "android-36").mkdir(parents=True)
    (sdk_root / "platform-tools").mkdir(parents=True)
    missing = missing_sdk_packages(
        str(sdk_root),
        ["platforms;android-36", "build-tools;36.0.0", "platform-tools"],
    )
    assert missing == ["build-tools;36.0.0"]


@pytest.mark.unit
def test_update_process_env_path(mocker):
    mocker.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True)
    update_process_env({"JAVA_HOME": "/tmp/java"}, ["$JAVA_HOME/bin"])
    assert os.environ["JAVA_HOME"] == "/tmp/java"
    assert os.environ["PATH"].startswith("/tmp/java/bin:")
