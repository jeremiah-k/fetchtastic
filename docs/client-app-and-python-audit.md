# Client app and Python workflow audit

Audit date: 2026-10-03. Fetchtastic baseline: `1f79eb8`.

## History and upstream contracts

- `659a350` (#195) introduced separate Android and Desktop downloaders and reporting.
- `d80f809` (#205) established shared client app configuration, storage, tracking, and retention. The surviving platform menus, compatibility mirrors, and CLI tuple branches did not reflect that model consistently.
- `2259a00` (#226) introduced opt-in snapshots when the rolling upstream release contained APKs.
- Meshtastic-Android `6447ef0993` (#6568) added desktop installers to that same rolling snapshot release.

Upstream references were read from the local Meshtastic-Android checkout at `37623607f2c5885529c9c73953bb44dd40cdd6b2` and firmware checkout at `ff3cc6682735fd2c0b9443037af0d242ecc0a804`. Their workflow directories had no local modifications.

The Android repository's `main-check.yml` publishes a single rolling `snapshot` tag. APKs and desktop installers carry the same numeric versionCode. Desktop formats are DMG, MSI, EXE, DEB, RPM, and AppImage; architecture and installer format remain selection criteria. Its `release.yml` publishes ordinary client app artifacts from the same repository, with desktop packaging optional for a given release.

Firmware `0becda301` (#11719) moved nightly publication to R2. Its `main_matrix.yml` stages manifests and firmware images before refreshing the nightly indices. Fetchtastic already has manifest validation, transactional nightly tracking, completeness checks, and independent nightly failure reporting; the client app changes retain that firmware pipeline.

## Findings and implemented changes

| Area               | Finding                                                                                | Resolution                                                                                                                                                |
| ------------------ | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Snapshots          | Desktop assets were absent from snapshot parsing and matching.                         | Parse their stamped versionCode and match selected installer formats and architectures through the shared snapshot flow.                                  |
| Discovery          | Setup requested the same upstream feed through separate platform menus.                | Request one feed and choose available formats from stable releases, with a prerelease fallback for formats absent from recent stable releases.            |
| Configuration      | Runtime settings retained platform mirrors after normalization.                        | Consume historical keys at the boundary; persist primary client app keys and preserve selections and retention preferences.                               |
| Reporting          | Mixed releases used separate lists, counters, downloader attributes, and tuple shapes. | Return one named DownloadReport, count client app release versions once, and share summaries, notifications, retry routing, and retention ownership.      |
| Snapshot checks    | Transport failures resembled an absent tag; an empty ordinary feed skipped snapshots.  | Distinguish 404 absence from failure and process the rolling channel independently. Suppress up-to-date reporting after failed checks or tracking writes. |
| Snapshot retention | Arbitrary directories ending in digits could be treated as managed builds.             | Accept numeric or valid timestamp-prefixed build names, choose directories deterministically, and reject symlinked managed paths.                         |
| Packaging          | PowerShell resources were omitted and installer lookup returned nonexistent filenames. | Ship both installer resources, resolve their actual names, and require a setuptools version supporting the declared license metadata.                     |
| Installation       | Installers preferred pipx and upgrades could target another installed copy.            | Default to uv, retain explicit pip and pipx routes, and identify the running tool environment before choosing an upgrade command.                         |
| Automation         | Custom uv executable directories were absent from scheduling fallback.                 | Honor UV_TOOL_BIN_DIR and XDG_BIN_HOME alongside pipx configuration.                                                                                      |
| Development        | Dependency installation was unrecorded and pip was the only CI path.                   | Commit uv.lock, use locked uv environments, and exercise independent pip installations at supported Python boundaries.                                    |
| Publication        | Release creation triggered publishing without an installed-wheel or tag check.         | Validate distributions in an unprivileged build job after publication, then publish the checked files through a separate OIDC job.                        |

## Preserved policies

- Android APKs and desktop installers share stable and prerelease storage under `app/`, along with one release lifecycle.
- Platform and architecture labels help select files. Selection, retention, tracking, and reporting use the client app family.
- Snapshot downloads require explicit opt-in and selected assets. Snapshot retention remains tied to completed stable or prerelease downloads; snapshot-only runs preserve existing builds.
- Primary client app settings are authoritative. Historical platform settings are accepted during configuration normalization, and historical layouts and serialized failures remain readable.
- pip remains supported through the test extra and standard package metadata. pip upgrades use the Python interpreter belonging to the active installation.
- Configuration and downloads remain outside tool environments. Switching managers requires refreshing scheduled commands or Windows integrations to use the installed executable path.

## Verification

- Full integration suite: 3,126 tests passed on Python 3.12 with locked uv dependencies; reported coverage was 87.71%.
- Standalone uv work: full suites passed on Python 3.10 with locked dependencies and Python 3.14 with an independent pip installation.
- Trunk checked the combined Python code, shell installer, workflows, dependency configuration, and documentation.
- Built the source distribution and its wheel, checked metadata with twine, and installed the wheel with pip in a separate environment. Verified the CLI and both shipped installers.
- Installed the wheel in a temporary uv tool environment and verified active-manager detection and its upgrade command. A mismatched release tag was rejected.
- Ran shell installer and bootstrap paths against isolated command stubs, including native Termux Python, pip, pipx, and failure handling. Parsed the Windows installer with PowerShell; CI also checks Windows PowerShell syntax.

Native Termux installation and Windows shortcut execution need platform runtime checks. Actual PyPI publication requires a published release with matching package metadata; the audit does not publish a release.
