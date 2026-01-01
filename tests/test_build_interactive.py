import pytest

from fetchtastic.build.environment import BuildEnvironment
from fetchtastic.build.interactive import prepare_build_environment


@pytest.mark.unit
def test_prepare_build_environment_missing_packages_declined(mocker):
    env_status = BuildEnvironment(
        java_home=None,
        sdk_root=None,
        sdkmanager_path=None,
        missing_packages=["openjdk-17"],
        missing_sdk_packages=[],
    )
    mocker.patch(
        "fetchtastic.build.interactive.check_build_environment", return_value=env_status
    )
    mocker.patch("fetchtastic.build.interactive.prompt_yes_no", return_value=False)

    result = prepare_build_environment(mocker.MagicMock())

    assert result is None


@pytest.mark.unit
def test_prepare_build_environment_ready(mocker):
    env_status = BuildEnvironment(
        java_home="/tmp/java",
        sdk_root="/tmp/sdk",
        sdkmanager_path=None,
        missing_packages=[],
        missing_sdk_packages=[],
    )
    mocker.patch(
        "fetchtastic.build.interactive.check_build_environment", return_value=env_status
    )
    mocker.patch("fetchtastic.build.interactive.update_shell_configs", return_value=[])
    mocker.patch("fetchtastic.build.interactive.update_process_env")
    mocker.patch("fetchtastic.build.interactive.find_sdkmanager", return_value=None)
    mocker.patch("fetchtastic.build.interactive.missing_sdk_packages", return_value=[])

    result = prepare_build_environment(mocker.MagicMock())

    assert result is env_status
    assert result.is_ready() is True
