# Delegated Cleanup Tasks (Monotonous / Mechanical)

Use this as a prompt for another agent to handle the remaining low-risk cleanup items. Keep changes focused, do not reintroduce the legacy monolithic downloader, and do not change runtime behavior unless explicitly stated.

## Goal

- Reduce lint/test nits and align docs with current refactor state.
- Keep behavior parity with legacy `0.8.9` (no functional drift).
- Avoid adding network calls to tests; keep tests hermetic.

## Tasks

### 1) Docs consistency (no behavior changes)

- Update `docs/test-migration-plan.md` to remove the stale note that `_get_prerelease_patterns()` “NEEDS TO BE ADDED”; point to:
  - `src/fetchtastic/download/firmware.py` method `_get_prerelease_patterns()` (already exists)
  - `src/fetchtastic/download/config_utils.py` function `get_prerelease_patterns()`
- Fix the inconsistent legacy-import progress numbers in `docs/migration-progress-summary.md` (ensure “migrated” and “remaining” percentages match the counts shown).

### 2) Small test hygiene fixes (safe, mechanical)

- `tests/test_cli_extended.py`: remove duplicated `@pytest.mark.user_interface`.
- `tests/test_cli_additional.py`: remove unused imports (`os`, `subprocess`) and remove unused fixture parameters (or rename to `_unused`).
- `tests/test_migration_reporting.py`: remove unused `monkeypatch` fixture parameter.
- `tests/test_download_integration.py`: replace `assert result is not None or True`-style assertions with meaningful shape assertions (e.g., tuple length, list types).
- `test_config_parity.py`: either convert print-only logic into real assertions or rename/move it so pytest doesn’t treat it as a test module.

### 3) Reduce unused mock arguments (Ruff ARG/F841 cleanup)

- Sweep tests for unused patched parameters/locals and remove bindings when only patch side-effects are needed.
- Pattern: if a patched object isn’t referenced, either remove the patch or rename the arg to `_`.

Candidate files (not exhaustive):

- `tests/test_download_cli_integration.py` (many tests declare `mocker` but only use `unittest.mock`)
- `tests/test_download_android.py` (unused patched args in a few tests)
- `tests/test_firmware_downloader_comprehensive.py` (optional: drop unused patches when short-circuiting)
- `tests/test_config_coverage_fixes.py` (unused local bindings that only exist for patch handles)

### 4) Cache module internal duplication (bigger but still internal-only)

In `src/fetchtastic/download/cache.py` there are still both:

- `CacheManager` methods (new abstraction)
- module-level cache globals/helpers (legacy compatibility surface)

Deliverable:

- Either route the module-level helpers through `CacheManager` internally, or move the shared TTL/JSON loading logic into a single internal helper used by both paths until the module-level API can be removed.
- Do not change on-disk cache formats.
- Add/adjust tests to ensure behavior is unchanged (expiry, naive timestamps, cache hit/miss tracking).

### 5) Small API smell fixes (optional)

- `src/fetchtastic/download/cache.py`: `get_cache_expiry_timestamp(self, cache_file, expiry_hours)` doesn’t use `cache_file`; either remove the parameter or document why it’s reserved for future policies (keep backward compatibility if needed).

## Guardrails

- Don’t run or add network calls in tests.
- Don’t add new dependencies.
- If a change affects user-visible behavior/logging, stop and ask for confirmation.
