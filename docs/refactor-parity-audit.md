# Downloader Refactor Parity Audit (rf-spectre-1 vs 0.8.9)

Date: 2025-12-13  
Scope: Identify missing/ regressed behaviors vs v0.8.9; enumerate fixes and tests required for parity.

## Observed Regressions (from user runs)

### Resolved / Mitigated

- ✅ Completeness checks: APK/firmware now short-circuit when already complete (size + verify + zip integrity), preventing large re-downloads.
- ✅ Prerelease config compatibility: firmware prerelease repo flow honors `CHECK_PRERELEASES` fallback and uses user patterns for selection.
- ✅ Prerelease repo flow: expected-version computation + commit-history parsing + legacy-style history summaries are implemented.
- ✅ CLI failure metadata: failures can surface URL/retryable/HTTP status in the download summary output.
- ✅ Logging spam: chunk download logging completely removed to match 0.8.9 behavior.
- ✅ Execution Order: restored legacy order (Firmware first, then Android) in download pipeline.
- ✅ Prerelease iteration: applied `*_VERSIONS_TO_KEEP` limits to download loops to prevent iterating and logging "Skipping..." for ancient releases.
- ✅ Commit-history refresh: now occurs early in the pipeline (`run_download_pipeline`) to ensure prerelease selection benefits from cached history.
- ✅ Prerelease base version: fixed regression where `latest_stable` filter caused the downloader to seek old prereleases (e.g., 2.7.16) when a newer release (e.g., 2.7.16 stable) was already available. Now uses the absolute latest release.

### Still Open (Parity Gaps)

- Commit timestamp cache expiry/compat needs unification (avoid multiple code paths with different expiry behavior).
- (If needed) Audit commit timestamp consumers to ensure all paths use `CacheManager.get_commit_timestamp()` consistently.

## Parity Tasks (fix + tests)

1. **Config compatibility for prereleases and selections**
   - Map `CHECK_PRERELEASES` ➜ APK + firmware prerelease flags; map `SELECTED_APK_ASSETS`/`SELECTED_FIRMWARE_ASSETS`/`SELECTED_PRERELEASE_ASSETS` ➜ selection logic.
   - Tests: config mapping drives prerelease enablement; selection filters applied for APK/firmware/prerelease assets.

2. **Release fetch + cache parity**
   - Paginate GitHub releases; cache by URL+params with short TTL (~60s) and rate-limit tracking.
   - Respect legacy cache files/keys; add backward-compatible readers for cached releases.
   - Tests: cache hit/miss behavior, expiry enforcement, rate-limit summaries.

3. **Completeness checks before download**
   - Recreate legacy `_is_release_complete` logic: size/hash/zip integrity and selection/exclude awareness; skip downloads when complete.
   - Apply to firmware/APK/prerelease repo assets; ensure retries only for incomplete/failed items.
   - Tests: existing files with correct size/hash are skipped; corrupt/missing files are redownloaded.

4. **Prerelease pipeline parity**
   - Use commit history + repo directory scan to compute expected prerelease version; filter prereleases accordingly.
   - Cache commit history with expiry; reuse cached commit list when fresh.
   - Track active prerelease identifiers and write tracking JSON with legacy fields; report created/deleted/active counts.
   - Tests: prerelease expected version selection, commit-cache expiry behavior, tracking file writes/cleanup, pattern/exclude application during prerelease repo pulls.

5. **Selection/exclude handling**
   - Ensure selection patterns from config are applied before download (APK/firmware/prerelease/repo); exclude patterns match legacy glob semantics.
   - Tests: assets outside selection are skipped; exclude patterns prevent download/extraction.

6. **Extraction validation & sidecars**
   - Gate extraction on need-check; validate patterns/excludes; produce .sha256 sidecars and verify hashes for extracted files.
   - Tests: extraction skipped when files already extracted; sidecars created; traversal protection enforced.

7. **Repo downloader parity**
   - Integrate prerelease/repo asset filtering with selection/exclude; classify failures with URLs; align directory layout and executable-bit behavior.
   - Tests: repo asset selection, path traversal protection, failure reporting with URLs.

8. **CLI/reporting parity**
   - Add legacy-style summary: latest versions (firmware/apk/prerelease), counts, failed downloads with URLs/types/retryable flag.
   - Include repo download stats/failures in CLI output (not just logs).
   - Tests: CLI integration returns legacy tuple unchanged; summary strings include repo stats and failure details.

9. **Cache/commit timestamp parity**
   - Apply expiry to commit timestamp cache; migrate old cache files if present; expose age logging similar to legacy.
   - Tests: expired commit cache triggers refresh; valid cache reused.

## Recommended Implementation Order

1. Config compatibility (prerelease flags and selection keys).
2. Release fetch/cache parity + completeness checks to prevent re-downloads.
3. Prerelease pipeline (expected version, commit history, repo scan, tracking).
4. Selection/exclude enforcement across all downloaders.
5. Extraction validation/sidecars.
6. Repo downloader parity and failure reporting.
7. CLI/reporting enhancements.
8. Cache timestamp parity and migration glue.

Keep this document updated as each item is delivered; add tests alongside fixes to lock behavior.
