# Downloader Refactor Parity Audit (rf-spectre-1 vs 0.8.9)

Date: today  
Scope: Identify missing/ regressed behaviors vs v0.8.9; enumerate fixes and tests required for parity.

## Observed Regressions (from user runs)

- APK downloads re-run despite existing files; no “already complete” short-circuit. Large APK download continues until manual Ctrl+C.
- Prerelease handling effectively disabled: legacy config uses `CHECK_PRERELEASES`/`SELECTED_PRERELEASE_ASSETS`, but modular code only checks `CHECK_APK_PRERELEASES`/`CHECK_FIRMWARE_PRERELEASES`, so prereleases are skipped.
- Legacy prerelease repo scan/commit-history flow missing: no expected-version computation using commit history or repo directory listings; no cache age/refresh checks beyond simple expiry; no active prerelease selection/reporting.
- Release caching semantics differ: legacy cache keyed by per_page URL with 60s expiry and rate-limit accounting; new code caches raw JSON once without pagination handling, rate-limit tracking, or per-page query.
- Release completeness checks absent: legacy validated size/hash/zip integrity and skipped when releases were fully downloaded; new pipeline downloads every asset that passes simple selection.
- Selection mapping gaps: legacy used `SELECTED_APK_ASSETS`, `SELECTED_FIRMWARE_ASSETS`, `SELECTED_PRERELEASE_ASSETS`; new code looks at `SELECTED_PATTERNS`/`EXCLUDE_PATTERNS` only (config compat missing).
- Extraction validation/sidecars: legacy validated patterns, ensured need-to-extract, verified hashes and produced .sha256 sidecars; modular code partially does this, but does not gate extraction on completion checks or integrate with selection/exclude during prerelease repo pulls.
- Repo/reporting: CLI summary does not surface failed downloads with URLs in a user-facing way; migration failure report improved but CLI output still terse. Repository download failures not included in legacy-style summaries.
- Cache/commit timestamp parity: legacy cached commit histories with strict expiry and statistics; modular code fetches commits but does not integrate results into prerelease selection or expose expiry/age logs.

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
