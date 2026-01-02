# mtand Build Progress (Fetchtastic Setup)

Last updated: 2026-01-01

## Goal

- Add a `fetchtastic mtand` command to clone Meshtastic-Android, build a debug or release APK via Gradle, and copy the APK to `~/Downloads/Meshtastic/mtand/mtand-<version-or-commit>.apk` (Termux uses `~/storage/downloads/Meshtastic`).
- Keep the existing APK download directory (`apks/`) for release downloads. Use new build directories under the base folder for `dfu/`, `mtand/`, and future build targets.
- Support building from tag, branch, or commit, and allow building a fork via GitHub owner or full repo URL.

## Current Status

- Implemented: mtand build module based on the DFU Gradle build flow.
- Implemented: `fetchtastic mtand build` CLI command with `--type`, `--ref`, `--repo-dir`, `--repo-url`, and `--fork`.
- Implemented: `fetchtastic mtand setup` command for Android SDK/JDK setup (shared with DFU).
- Implemented: setup wizard section `mtand` for interactive build flow and repo selection.
- Implemented: repo override support that creates fork-specific checkout directories.
- Implemented: tests for mtand module, CLI entry points, and setup flow.

## Sources Reviewed

- Meshtastic-Android repository:
  - `config.properties` (SDK versions and app IDs)
  - `gradle/libs.versions.toml` (AGP/Kotlin versions)
  - `gradle/wrapper/gradle-wrapper.properties` (Gradle wrapper version)
  - `app/build.gradle.kts` (build types, signing, flavors)
  - `README.md` (build notes, MAPS_API_KEY)

## mtand Build Requirements

- Android Gradle Plugin: 9.0.0-rc02 (from version catalog).
- Kotlin: 2.3.0 (from version catalog).
- Gradle: wrapper 9.2.1.
- Java: JDK 21 runtime (build logic targets JVM 21).
- SDKs: compileSdk 36, targetSdk 36, minSdk 26 (from `config.properties`).
- Flavors: `google` and `fdroid`.
  - `assembleDebug` builds both flavors by default.
  - `MAPS_API_KEY` is required for the Google flavor to provide map tiles.

## Environment Notes

### Linux / macOS

- JDK 21 runtime and Android SDK with API 36 and compatible build-tools.
- `ANDROID_SDK_ROOT`/`ANDROID_HOME` set; Fetchtastic can create `local.properties` with `sdk.dir`.
- Build command: `./gradlew :app:assembleDebug` or `./gradlew :app:assembleRelease`.

### Termux

- Required packages (similar to DFU):
  - `openjdk-21`, `git`, `curl`, `unzip`, `zip`, `aapt2`
- Recommended tools:
  - `apksigner`, `d8`, `android-tools`
- `JAVA_HOME` should point to `$PREFIX/lib/jvm/java-21-openjdk`.
- Fetchtastic can download Android cmdline-tools and install required SDK packages.
- Termux builds often need the `aapt2` override (`-Pandroid.aapt2FromMavenOverride=`).
- Expect large disk usage (SDK + Gradle caches).

### Windows

- JDK 21 runtime and Android SDK via Android Studio.
- `ANDROID_SDK_ROOT`/`ANDROID_HOME` set.
- Use Gradle wrapper from repo; builds may be slower without Android Studio integration.

## Proposed Setup Flow

1. Add setup section: `mtand` (shortcut `t`).
2. Prompt:
   - Explain requirements, time/disk cost, and debug vs release builds.
   - Confirm opt-in.
3. Select repo source:
   - Default: upstream `meshtastic/Meshtastic-Android`.
   - Accept a GitHub owner (fork), owner/repo, or full repo URL.
   - Persist non-default repo URL in config as `MTAND_REPO_URL`.
4. Select build ref (tag/branch/commit):
   - Default is `latest`, resolved from tags when available.
   - Show default branch when discovered.
5. Clone/update repo:
   - Default repo root: `~/.local/share/fetchtastic/builds/`.
   - Forks use derived checkout dirs like `<owner>-<repo>` to avoid conflicts.
   - If repo exists and clean: `git fetch --tags` + `git pull --ff-only`.
6. Build:
   - Run Gradle wrapper task for debug/release.
   - Use `MAPS_API_KEY` if building Google flavor (local.properties).
   - Release signing uses `keystore.properties` when present; otherwise debug signing is used.
7. Locate APK:
   - Search under `app/build/outputs/apk/...` (flavor + build type).
8. Name output:
   - If HEAD is tagged, use tag (e.g., `mtand-v2.7.10.apk`).
   - Else use short commit hash (e.g., `mtand-<sha>.apk`).
9. Copy to: `<BASE_DIR>/mtand/` where BASE_DIR defaults to `~/Downloads/Meshtastic` (Termux uses `~/storage/downloads/Meshtastic`).

## CLI Plan

- `fetchtastic mtand setup`
  - Reuse Android SDK/JDK setup from DFU build.
  - Install Termux packages and SDK packages if needed.
- `fetchtastic mtand build`
  - `--type debug|release`
  - `--ref <tag|branch|commit>`
  - `--repo-dir <base checkout dir>`
  - `--repo-url <full URL>`
  - `--fork <owner or owner/repo>`
  - `--no-update` to skip git fetch/pull

## Open Questions

- Should we expose flavor selection (google/fdroid) as a build option?
- Should we support building AAB (`bundle`) as a distinct build type?
- Should we populate `MAPS_API_KEY` from an env var if present?

## Next Actions

- Implement mtand build module + CLI wiring.
- Add mtand setup section in `fetchtastic setup`.
- Validate mtand build on Linux/macOS; document Termux-specific pitfalls.
- Add tests to cover repo override handling and mtand build flow.
