"""
Meshtastic Android (mtand) build module definitions.
"""

from __future__ import annotations

from fetchtastic.build.base import GradleBuildModule

MTAND_REQUIREMENTS = {
    "default": [
        "JDK 21 runtime (AGP 9 + Kotlin 2.3 build logic targets JVM 21)",
        "Android SDK with platforms;android-36 and build-tools",
        "ANDROID_SDK_ROOT/ANDROID_HOME set (or SDK under ~/Android/Sdk)",
        "Gradle wrapper downloads dependencies (network required)",
        "Optional: MAPS_API_KEY in local.properties for Google flavor builds",
    ],
    "darwin": [
        "JDK 21 runtime (AGP 9 + Kotlin 2.3 build logic targets JVM 21)",
        "Android SDK with platforms;android-36 and build-tools",
        "ANDROID_SDK_ROOT/ANDROID_HOME set (or SDK under ~/Library/Android/sdk)",
        "Gradle wrapper downloads dependencies (network required)",
        "Optional: MAPS_API_KEY in local.properties for Google flavor builds",
    ],
    "termux": [
        "Packages: openjdk-21, git, curl, unzip, zip, aapt2",
        "Recommended Termux tools: apksigner, d8, android-tools (gradle optional)",
        "JAVA_HOME set to the OpenJDK 21 install (see $PREFIX/lib/jvm/java-21-openjdk)",
        "Android SDK cmdline-tools + platforms;android-36 + build-tools",
        "Optional: MAPS_API_KEY in local.properties for Google flavor builds",
        "Gradle builds can require multiple GB of disk space",
    ],
    "windows": [
        "JDK 21 runtime (AGP 9 + Kotlin 2.3 build logic targets JVM 21)",
        "Android SDK via Android Studio",
        "ANDROID_SDK_ROOT/ANDROID_HOME set",
        "Gradle wrapper downloads dependencies (network required)",
        "Optional: MAPS_API_KEY in local.properties for Google flavor builds",
    ],
}


class MtandBuildModule(GradleBuildModule):
    """
    Build module for the Meshtastic Android app (mtand).
    """

    def __init__(self) -> None:
        super().__init__(
            name="mtand",
            display_name="Meshtastic Android App (mtand)",
            repo_url="https://github.com/meshtastic/Meshtastic-Android.git",
            repo_dirname="Meshtastic-Android",
            output_prefix="mtand",
            output_dirname="mtand",
            gradle_tasks={
                "debug": ":app:assembleDebug",
                "release": ":app:assembleRelease",
            },
            artifact_globs={
                "debug": [
                    "app/build/outputs/apk/debug/*.apk",
                    "app/build/outputs/apk/*/debug/*.apk",
                    "app/build/outputs/apk/*/*/debug/*.apk",
                ],
                "release": [
                    "app/build/outputs/apk/release/*.apk",
                    "app/build/outputs/apk/*/release/*.apk",
                    "app/build/outputs/apk/*/*/release/*.apk",
                ],
            },
            min_java_version=21,
            required_sdk_packages=(
                "platforms;android-36",
                "build-tools;36.0.0",
                "platform-tools",
            ),
            requirements=MTAND_REQUIREMENTS,
        )
