# DFU Build Progress (Fetchtastic Setup)

Last updated: 2026-01-01

## Goal

- Add a `fetchtastic setup` option to clone Nordic Semiconductor's Android-DFU-Library, build a debug APK via `gradlew`, and copy the APK to `~/Downloads/Meshtastic/DFU/DFU-<version-or-commit>.apk` (Termux uses `~/storage/downloads`).
- Provide clear, environment-specific prerequisites (Linux, macOS, Termux; Windows optional/low priority).

## Current Status

- Research done on DFU repo build tooling, plugin versions, and SDK requirements.
- Termux build guidance captured from provided Reddit post and Termux issue comment.
- Verified current Termux package versions from `packages.termux.dev` (openjdk-21 21.0.9-1, gradle 9.2.0, aapt2 13.0.0.6-23, apksigner 33.0.1-1, android-tools 35.0.2-6, d8 13.0.0.6-23).
- Implemented a new setup section (`dfu`) and dedicated build module with clone/build/copy flow (pending review).
- Added a `fetchtastic dfu build` CLI command to run the DFU build outside of setup.
- Added a `fetchtastic dfu setup` CLI command to install dependencies and configure JAVA_HOME/ANDROID_SDK_ROOT.
- DFU setup now auto-downloads Android cmdline-tools when sdkmanager is missing.
- DFU setup now auto-accepts Android SDK licenses when installing packages.
- DFU source checkouts now live under the Fetchtastic data directory (not cache).
- Updated setup section lists and unit tests for new `dfu` option.
- Build module located at `src/fetchtastic/build/dfu/modules.py` with a shared Gradle build base.
- Android SDK auto-detection now includes `~/Library/Android/sdk` and `~/Android/Sdk`.
- Interactive DFU build flow refactored into `src/fetchtastic/build/interactive.py` to share prompts and checks.
- Environment helpers for shell rc updates live in `src/fetchtastic/build/environment.py`.

## Sources Reviewed

- Nordic DFU repo: `Android-DFU-Library` (build.gradle.kts, app/build.gradle.kts, gradle.properties, settings.gradle.kts).
- Nordic Android Gradle Plugins: `AndroidApplicationConventionPlugin.kt` and `AppConsts.kt`.
- Nordic version catalog on Maven Central (AGP/Kotlin versions).
- Termux build instructions from:
  - Reddit: build apps on Termux (manual aapt/dx flow; Gradle caveats).
- Termux issue comment: gradle + openjdk-17 + SDK manager + workarounds (legacy JDK info).
- Termux package index: `https://packages.termux.dev/apt/termux-main/`.
- Android environment variable docs: `https://developer.android.com/tools/variables`.
- Termux NDK reference: `https://github.com/lzhiyong/termux-ndk`.
- Termux SDK tool alternatives: `https://github.com/dingyi222666/termux-sdk-tools` (deprecated, points to aapt2 replacements).

## DFU Build Requirements (from Nordic config)

- Android Gradle Plugin: 8.13.0 (from Nordic version catalog `version-catalog:2.10-4`).
- Kotlin: 2.2.20 (Kotlin compiler opts set to Kotlin 2.3 language/API; JVM target 17).
- Java: Android toolchain targets 17, but Nordic Gradle plugins now require a Java 21 runtime.
- SDKs: compileSdk 36, targetSdk 36, minSdk 23 (from `AppConst` in Nordic plugins).
- Uses Gradle wrapper (`./gradlew`).

## Environment Notes

### Linux / macOS

- Need JDK 21 runtime, Android SDK with API 36 (platforms;android-36) and build-tools compatible with AGP 8.x.
- `ANDROID_HOME`/`ANDROID_SDK_ROOT` set; otherwise Fetchtastic looks for `~/Android/Sdk` or `~/Library/Android/sdk`. Android docs currently note `ANDROID_SDK_ROOT` as deprecated; Fetchtastic sets both for compatibility.
- Build command: `./gradlew :app:assembleDebug`.

### Termux

- 2026 package baseline (from `packages.termux.dev`):
  - Required: `openjdk-21`, `git`, `curl`, `unzip`, `zip`.
  - Recommended for on-device Android builds: `aapt2`, `apksigner`, `d8`, `android-tools`.
  - Optional: `gradle` (useful for `gradle --stop`, but may pull in newer JDKs).
- JAVA_HOME typically resolves to `$PREFIX/lib/jvm/java-21-openjdk` (verify via `readlink -f $(command -v javac)`).
- Fetchtastic auto-downloads Android cmdline-tools from the Google repository manifest and installs them to `~/Android/sdk/cmdline-tools/latest`.
- Gradle build is possible but heavy. Known issues and fixes:
  - If Gradle fails with module access errors, add `org.gradle.jvmargs=--add-opens=java.base/java.io=ALL-UNNAMED` to `gradle.properties`.
  - If Gradle fails with aapt2 exec errors, add `android.aapt2FromMavenOverride=$(command -v aapt2)` to `gradle.properties`.
  - Google build-tools are x86; on-device builds often need Termux-provided binaries (aapt2/apksigner/d8) instead of prebuilt build-tools.
- Expected disk usage: 5-10GB+ (SDK + Gradle caches).
- For Fetchtastic: provide prerequisite checklist and let user opt-in before attempting build.
- `fetchtastic dfu setup` can install Termux packages, download cmdline-tools, and update shell rc files with JAVA_HOME/ANDROID_SDK_ROOT.

### Windows

- Low priority. If supported, should warn that build is best via Android Studio or a properly configured SDK/JDK; otherwise skip.

## Proposed Setup Flow

1. Add setup section: `dfu` (shortcut `d`).
2. Prompt:
   - Explain requirements, time/disk cost, and that it can build debug or release.
   - Confirm opt-in.
3. Select build ref (tag/branch/commit):
   - Default is `latest`, which resolves to the newest tag and is shown in the prompt.
   - Enter a branch name to build from that branch (default branch is shown when available).
4. Clone/update repo:
   - Target: `~/.local/share/fetchtastic/builds/Android-DFU-Library` (macOS: `~/Library/Application Support/fetchtastic/builds/Android-DFU-Library`).
   - Override by setting `BUILD_REPOS_DIR` in config or passing `--repo-dir` to `fetchtastic dfu build`.
   - If exists: `git fetch` + `git pull` (or reset to remote unless user has local changes).
5. Build:
   - Run `./gradlew :app:assembleDebug` or `./gradlew :app:assembleRelease` from repo root.
   - Release build requires signing env vars: `KEYSTORE_PSWD`, `KEYSTORE_ALIAS`, `KEYSTORE_KEY_PSWD`.
   - For Termux: show warnings + ensure storage permissions and SDK path instructions.
6. Locate APK:
   - `app/build/outputs/apk/debug/*.apk` (use newest file).
7. Name output:
   - If HEAD is tagged, use tag (e.g., `DFU-v1.1.1.apk`).
   - Else use short commit hash (e.g., `DFU-<sha>.apk`).
8. Copy to: `<BASE_DIR>/DFU/` where BASE_DIR defaults to `~/Downloads/Meshtastic` (Termux uses `~/storage/downloads/Meshtastic`).

## Open Questions

- Should we force a clean build (`./gradlew clean assembleDebug`) or allow incremental?
- How strict should we be about SDK 36 availability? (Warn if missing; allow build attempt.)
- Should Termux path be `~/storage/downloads` always, or respect configured BASE_DIR?

## Next Actions

- Validate the DFU build flow on Linux/macOS/Termux (as available).
- Refine Termux warnings if new issues appear in practice.
- Update docs/usage if we want to advertise the new setup option.
