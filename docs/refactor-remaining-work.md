# Downloader Refactor – Remaining Work

Status: living checklist for completing parity and clean-up of the modular downloader. Update this file as tasks close or new gaps are found.

Current handoff/status: `docs/refactor-handoff.md`.

## Priorities (P1 = block release, P2 = needed for parity, P3 = polish)

### P1 – Functional Parity Gaps

1. Repository downloads scope decision **(Done – pipeline does not touch repo-dls)**
   - `fetchtastic download` no longer processes or cleans `repo-dls`; repo downloads remain an interactive `repo browse` feature.
2. Prerelease handling for firmware/APK
   - Commit-history and directory scan helpers added (expected version, directory matching, tracking creation/cleanup).
   - Tracking write/cleanup unified via VersionManager; tracking files include metadata and expiry.
   - Done: Refresh commit history before prerelease selection; validate commit-history-selected dirs against cached repo listings.
3. Version tracking + cache parity
   - Added backward-compatible readers/writers and legacy key support; expiry-aware cache reads.
   - TODO: Unify commit timestamp cache expiry paths (single source of truth); confirm migration/compat for existing on-disk caches.
4. Extraction parity & safety
   - Implement `_validate_extraction_patterns` / `check_extraction_needed` equivalents.
   - Ensure extraction skip is treated as success (already-extracted is not an error). **(Done)**
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
