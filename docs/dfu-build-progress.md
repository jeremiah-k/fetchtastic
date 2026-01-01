# DFU Build Progress (Fetchtastic Setup)

Last updated: 2026-01-01

## Goal

- Add a `fetchtastic setup` option to clone Nordic Semiconductor's Android-DFU-Library, build a debug APK via `gradlew`, and copy the APK to `~/Downloads/Meshtastic/DFU/DFU-<version-or-commit>.apk` (Termux uses `~/storage/downloads`).
- Provide clear, environment-specific prerequisites (Linux, macOS, Termux; Windows optional/low priority).

## Current Status

- Research done on DFU repo build tooling, plugin versions, and SDK requirements.
- Termux build guidance captured from provided Reddit post and Termux issue comment.
- Implemented a new setup section (`dfu`) and dedicated build module with clone/build/copy flow (pending review).
- Added a `fetchtastic dfu build` CLI command to run the DFU build outside of setup.
- Updated setup section lists and unit tests for new `dfu` option.
- Build module located at `src/fetchtastic/build/dfu/modules.py` with a shared Gradle build base.
- Android SDK auto-detection now includes `~/Library/Android/sdk` and `~/Android/Sdk`.

## Sources Reviewed

- Nordic DFU repo: `Android-DFU-Library` (build.gradle.kts, app/build.gradle.kts, gradle.properties, settings.gradle.kts).
- Nordic Android Gradle Plugins: `AndroidApplicationConventionPlugin.kt` and `AppConsts.kt`.
- Nordic version catalog on Maven Central (AGP/Kotlin versions).
- Termux build instructions from:
  - Reddit: build apps on Termux (manual aapt/dx flow; Gradle caveats).
  - Termux issue comment: gradle + openjdk-17 + SDK manager + workarounds.

## DFU Build Requirements (from Nordic config)

- Android Gradle Plugin: 8.13.0 (from Nordic version catalog `version-catalog:2.10-4`).
- Kotlin: 2.2.20 (Kotlin compiler opts set to Kotlin 2.3 language/API; JVM target 17).
- Java: 17 (compileOptions set to Java 17).
- SDKs: compileSdk 36, targetSdk 36, minSdk 23 (from `AppConst` in Nordic plugins).
- Uses Gradle wrapper (`./gradlew`).

## Environment Notes

### Linux / macOS

- Need JDK 17, Android SDK with API 36 (platforms;android-36) and build-tools compatible with AGP 8.x.
- `ANDROID_HOME`/`ANDROID_SDK_ROOT` set; otherwise Fetchtastic looks for `~/Android/Sdk` or `~/Library/Android/sdk`.
- Build command: `./gradlew :app:assembleDebug`.

### Termux

- User-provided short path: `pkg install openjdk-17 git aapt dx` (manual build).
- Gradle build is possible but heavy. Known issues and fixes:
  - Need `gradle` + `openjdk-17` + Android SDK cmdline tools + SDK platforms/build-tools.
  - JDK 17 module access requires `--add-opens=java.base/java.io=ALL-UNNAMED` in `gradle.properties` (per Termux issue comment).
  - SDK build-tools often require termux-friendly binaries (Lzhiyong sdk-tools) and symlink hacks.
- Expected disk usage: 5-10GB+ (SDK + Gradle caches).
- For Fetchtastic: likely provide prerequisite checklist and let user opt-in before attempting build.

### Windows

- Low priority. If supported, should warn that build is best via Android Studio or a properly configured SDK/JDK; otherwise skip.

## Proposed Setup Flow

1. Add setup section: `dfu` (shortcut `d`).
2. Prompt:
   - Explain requirements, time/disk cost, and that it can build debug or release.
   - Confirm opt-in.
3. Clone/update repo:
   - Target: `~/.cache/fetchtastic/Android-DFU-Library`.
   - If exists: `git fetch` + `git pull` (or reset to remote unless user has local changes).
4. Build:
   - Run `./gradlew :app:assembleDebug` or `./gradlew :app:assembleRelease` from repo root.
   - Release build requires signing env vars: `KEYSTORE_PSWD`, `KEYSTORE_ALIAS`, `KEYSTORE_KEY_PSWD`.
   - For Termux: show warnings + ensure storage permissions and SDK path instructions.
5. Locate APK:
   - `app/build/outputs/apk/debug/*.apk` (use newest file).
6. Name output:
   - If HEAD is tagged, use tag (e.g., `DFU-v1.1.1.apk`).
   - Else use short commit hash (e.g., `DFU-<sha>.apk`).
7. Copy to: `<BASE_DIR>/DFU/` where BASE_DIR defaults to `~/Downloads/Meshtastic` (Termux uses `~/storage/downloads/Meshtastic`).

## Open Questions

- Should we force a clean build (`./gradlew clean assembleDebug`) or allow incremental?
- How strict should we be about SDK 36 availability? (Warn if missing; allow build attempt.)
- Should Termux path be `~/storage/downloads` always, or respect configured BASE_DIR?

## Next Actions

- Validate the DFU build flow on Linux/macOS/Termux (as available).
- Refine Termux warnings if new issues appear in practice.
- Update docs/usage if we want to advertise the new setup option.
