import os

import pytest

from fetchtastic.build.environment import (
    build_shell_exports,
    detect_java_home,
    detect_missing_termux_optional_packages,
    detect_missing_termux_packages,
    find_sdkmanager,
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
    assert "git" in missing


@pytest.mark.unit
def test_detect_missing_termux_optional_packages(mocker):
    mocker.patch(
        "fetchtastic.build.environment.shutil.which",
        side_effect=lambda cmd: None if cmd in {"aapt2", "apksigner"} else "/bin/ok",
    )
    missing = detect_missing_termux_optional_packages()
    assert "aapt2" in missing
    assert "apksigner" in missing


@pytest.mark.unit
def test_detect_java_home_from_env(mocker):
    mocker.patch.dict(os.environ, {"JAVA_HOME": "/tmp/java"}, clear=True)
    mocker.patch("fetchtastic.build.environment.os.path.isdir", return_value=True)
    assert detect_java_home() == "/tmp/java"


@pytest.mark.unit
def test_detect_java_home_termux_prefers_jdk17(mocker, tmp_path):
    prefix = tmp_path / "prefix"
    java17 = prefix / "lib" / "jvm" / "java-17-openjdk"
    java21 = prefix / "lib" / "jvm" / "java-21-openjdk"
    java17.mkdir(parents=True)
    java21.mkdir(parents=True)

    mocker.patch.dict(os.environ, {"PREFIX": str(prefix)}, clear=True)
    mocker.patch("fetchtastic.build.environment.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.build.environment.shutil.which", return_value="/bin/javac"
    )
    mocker.patch(
        "fetchtastic.build.environment.os.path.realpath",
        return_value=str(java21 / "bin" / "javac"),
    )

    assert detect_java_home() == str(java17)


@pytest.mark.unit
def test_detect_java_home_termux_min_version_prefers_21(mocker, tmp_path):
    prefix = tmp_path / "prefix"
    java17 = prefix / "lib" / "jvm" / "java-17-openjdk"
    java21 = prefix / "lib" / "jvm" / "java-21-openjdk"
    java17.mkdir(parents=True)
    java21.mkdir(parents=True)

    mocker.patch.dict(os.environ, {"PREFIX": str(prefix)}, clear=True)
    mocker.patch("fetchtastic.build.environment.is_termux", return_value=True)

    assert detect_java_home(min_version=21) == str(java21)


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


@pytest.mark.unit
def test_find_sdkmanager_marks_executable(tmp_path, mocker):
    sdk_root = tmp_path / "sdk"
    bin_dir = sdk_root / "cmdline-tools" / "latest" / "bin"
    bin_dir.mkdir(parents=True)
    sdkmanager = bin_dir / "sdkmanager"
    sdkmanager.write_text("#!/bin/sh\n")
    os.chmod(sdkmanager, 0o644)

    mocker.patch("fetchtastic.build.environment.shutil.which", return_value=None)

    found = find_sdkmanager(str(sdk_root))

    assert found == str(sdkmanager)
    assert os.access(found, os.X_OK)
