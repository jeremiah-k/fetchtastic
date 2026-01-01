"""
Nordic DFU build module definitions.
"""

from __future__ import annotations

from fetchtastic.build.base import GradleBuildModule

DFU_REQUIREMENTS = {
    "default": [
        "JDK 17",
        "Android SDK with platforms;android-36 and build-tools",
        "ANDROID_SDK_ROOT/ANDROID_HOME set (or SDK under ~/Android/Sdk)",
        "Gradle wrapper downloads dependencies (network required)",
    ],
    "darwin": [
        "JDK 17",
        "Android SDK with platforms;android-36 and build-tools",
        "ANDROID_SDK_ROOT/ANDROID_HOME set (or SDK under ~/Library/Android/sdk)",
        "Gradle wrapper downloads dependencies (network required)",
    ],
    "termux": [
        "Packages: openjdk-17, git, curl, unzip, zip",
        "JAVA_HOME set to the OpenJDK 17 install",
        "Android SDK cmdline-tools + platforms;android-36 + build-tools",
        "Extra SDK tool workarounds may be required (see docs/dfu-build-progress.md)",
        "Gradle builds can require multiple GB of disk space",
    ],
    "windows": [
        "JDK 17",
        "Android SDK via Android Studio",
        "ANDROID_SDK_ROOT/ANDROID_HOME set",
        "Gradle wrapper downloads dependencies (network required)",
    ],
}


class DFUBuildModule(GradleBuildModule):
    """
    Build module for Nordic Android-DFU-Library.
    """

    def __init__(self) -> None:
        super().__init__(
            name="dfu",
            display_name="Nordic DFU Android App",
            repo_url="https://github.com/NordicSemiconductor/Android-DFU-Library.git",
            repo_dirname="Android-DFU-Library",
            output_prefix="DFU",
            output_dirname="DFU",
            gradle_tasks={
                "debug": ":app:assembleDebug",
                "release": ":app:assembleRelease",
            },
            artifact_globs={
                "debug": ["app/build/outputs/apk/debug/*.apk"],
                "release": ["app/build/outputs/apk/release/*.apk"],
            },
            release_env_vars=(
                "KEYSTORE_PSWD",
                "KEYSTORE_ALIAS",
                "KEYSTORE_KEY_PSWD",
            ),
            required_sdk_packages=(
                "platforms;android-36",
                "build-tools;36.0.0",
                "platform-tools",
            ),
            requirements=DFU_REQUIREMENTS,
        )
