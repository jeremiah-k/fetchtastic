# Downloader Refactor – Remaining Work

Status: living checklist for completing parity and clean-up of the modular downloader. Update this file as tasks close or new gaps are found.

## Priorities (P1 = block release, P2 = needed for parity, P3 = polish)

### P1 – Functional Parity Gaps

1. Wire `RepositoryDownloader` into `DownloadOrchestrator` **(Done – integrated)**
   - Repo execution now runs in the pipeline with include/exclude semantics and metadata for retry/reporting.
   - Paths remain under `firmware/repo-dls`; shell scripts keep executable handling.
   - Follow-up: verify CLI summaries surface repo counts/failures and include download URLs.
2. Prerelease handling for firmware/APK
   - Port commit-history and directory-scan logic (expected version computation, commit hash suffix handling).
   - Recreate prerelease tracking JSONs (fields, expiry) and superseded-prerelease cleanup.
   - Honor `CHECK_APK_PRERELEASES` / `CHECK_FIRMWARE_PRERELEASES` with pattern-aware selection.
3. Version tracking + cache parity
   - Align latest-release/prerelease JSON write format and locations with legacy (atomic writes, timestamps).
   - Migrate existing tracking files safely; add backward-compatible readers.
   - Cache expiry semantics for releases/commit timestamps (respect legacy constants and timing).
4. Extraction parity & safety
   - Implement `_validate_extraction_patterns` / `check_extraction_needed` equivalents.
   - Ensure extraction produces hash/sidecar behavior consistent with legacy (if applicable) and applies excludes.
   - Add traversal-safe extraction path handling to mirror monolith behavior for nested members.

### P2 – Reliability & Reporting

5. Retry and failure metadata **(metadata captured; real retries pending)**
   - URLs/sizes/types now flow into `DownloadResult`; reporting uses file_type.
   - Implement actual retry using stored metadata instead of simulated success paths. **(Done – orchestration retries now call downloaders with stored URL/path and verification)**
6. Cache manager enhancements
   - Port commit timestamp caching and rate-limit tracking hooks from legacy utils.
   - Add cache invalidation/refresh switches (force_refresh) matching monolith.
7. Menu/config alignment
   - Ensure menu selections map to new config keys without duplication; keep compatibility for `SELECTED_FIRMWARE_ASSETS` and `SELECTED_PRERELEASE_ASSETS`.
   - Verify prompts/messages match legacy expectations (exclude defaults, prerelease prompts).

### P3 – Clean-up & Migration

8. CLI path simplification
   - Decide on sunsetting `DownloadMigration`: route CLI directly to orchestrator once parity verified.
   - Keep a thin compatibility shim only if tests require it; otherwise remove.
9. Legacy removal
   - Remove monolithic `downloader.py` once parity is proven and tests are migrated.
10. Documentation/tests

- Port high-value legacy tests (prerelease, repo downloads, extraction validation, cleanup, hash verification).
- Expand modular test suites for new retry/reporting behaviors. **(retry path covered in test_orchestrator_retry.py)**
- Keep this document updated as tasks complete.

## How to Update This File

- When a task is completed, mark it (e.g., "Done – <date>") and move any follow-ups to the appropriate section.
- Add new findings immediately so the next engineer has context.
- Keep priorities accurate; promote items that block release-quality parity.
