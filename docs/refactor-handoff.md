# Downloader Refactor Handoff (rf-spectre-1)

Goal: preserve exact v0.8.9 download behavior while completing the modular refactor.

This document is the “current truth” for another engineer taking over. Update it whenever parity work lands.

## Ground Truth / Baseline

- Legacy reference implementation: `src/fetchtastic/downloader.py` (v0.8.9 behavior baseline; use it for parity comparisons).
- Refactored subsystem lives under: `src/fetchtastic/download/`.
- CI signal: local `python -m pytest -q` passes on this branch (at last checkpoint).

## What Works Now (Parity Recovered)

- **Setup in cron-less environments**: setup no longer crashes or loops when `crontab` is missing (container-friendly).
  - `src/fetchtastic/setup_config.py`
  - `tests/test_setup_config.py`
- **Completeness checks to avoid re-downloading**:
  - `DownloadResult.was_skipped` added to distinguish “already complete” vs “downloaded”.
  - `BaseDownloader.is_asset_complete()` validates: exists + size (when known) + hash/verify + zip integrity.
  - Firmware prerelease repo assets additionally validate zip integrity on skip.
  - `src/fetchtastic/download/base.py`
  - `src/fetchtastic/download/firmware.py`
- **Cache/compat and atomic writes**:
  - Atomic JSON writes and safe delete helpers moved to `src/fetchtastic/download/files.py`.
  - Legacy cache filename preference restored (`releases.json` first; fallback to `releases_cache.json`).
  - Backward-compatible readers for prerelease tracking formats exist in `src/fetchtastic/download/version.py`.
  - `src/fetchtastic/download/cache.py`
  - `src/fetchtastic/download/version.py`
- **Legacy import compatibility (tests + older code)**:
  - Shims re-export legacy downloader paths (`fetchtastic.downloaders.core`, `fetchtastic.repo_downloader`).
  - `src/fetchtastic/downloaders/core.py`
  - `src/fetchtastic/repo_downloader.py`
- **Failure metadata plumbing**:
  - Failures carry URL, retryable flag, and HTTP status (when known) into the legacy-style `failed_downloads` list.
  - `src/fetchtastic/download/migration.py`
  - `src/fetchtastic/cli.py`

## What Is Still Not Parity (High Priority)

### 1) Prerelease expected-version/commit-history integration timing

Problem: commit history refresh currently happens in `DownloadOrchestrator.update_version_tracking()` (after the download pipeline), so prerelease selection during the run can’t benefit from the commit cache.

- Fix: refresh commit history at the start of the pipeline and share it to downloaders before prerelease selection.
- Files: `src/fetchtastic/download/orchestrator.py`
- Tests: add a unit test that asserts commit refresh is called before prerelease handling and that `_recent_commits` is available to `FirmwareReleaseDownloader` during prerelease filtering.

### 2) Repo directory-scan caching parity for prerelease repo flow

Legacy behavior caches the meshtastic.github.io directory listing with a short TTL and logs cache age/expiry decisions.

- Current: firmware repo prerelease fallback fetches repo root directly without using the directory cache.
- Fix: use the prerelease directory cache helpers in `src/fetchtastic/download/cache.py` for the fallback listing path.
- Files: `src/fetchtastic/download/firmware.py`, `src/fetchtastic/download/cache.py`
- Tests: mock the cache hit/miss and ensure the fetch path is skipped when the cached listing is fresh.

### 3) Commit timestamp cache parity (expiry + compat)

There is a `CacheManager.get_commit_timestamp()` path with expiry, but module-level commit cache helpers also exist and should match legacy behavior (expiry enforcement, safe reads, and compat/migration if old formats exist).

- Fix: ensure all commit-timestamp cache reads apply expiry consistently (single source of truth).
- Files: `src/fetchtastic/download/cache.py`, possibly `src/fetchtastic/download/version.py` callers.
- Tests: expired commit timestamp entries are ignored; fresh entries are reused.

### 4) Repository downloader scope and wiring

The interactive repo browser (`fetchtastic repo browse`) is the primary “repo-dls” feature. The download pipeline currently calls `RepositoryDownloader.get_repository_files()` but that function is a stub returning `[]`.

- Decide:
  - Option A (minimal drift): remove repo downloads from the standard download pipeline (keep repo downloads only via `repo browse`).
  - Option B: support a config-backed repo selection list and have the pipeline download it.
- Files: `src/fetchtastic/download/orchestrator.py`, `src/fetchtastic/download/repository.py`, `src/fetchtastic/menu_repo.py`
- Note: `src/fetchtastic/menu_repo.py` currently imports `RepositoryDownloader` but doesn’t use it; that import can create circular coupling and should be removed if not needed.

### 5) CLI/reporting polish parity

CLI already prints failure URL/retryable/http status, but parity goals also include:

- stable/prerelease repo counts and failures in the final summary
- retry metadata surfaced consistently for all artifact types
- rate-limit/cache summary formatting aligned to v0.8.9 (where applicable)

Files: `src/fetchtastic/cli.py`, `src/fetchtastic/download/cli_integration.py`, `src/fetchtastic/utils.py`

## Step-by-Step Next Work (Do In This Order)

1. **Move commit-history refresh earlier**
   - Update `DownloadOrchestrator.run_download_pipeline()` to refresh commits before firmware prerelease selection.
   - Share `_recent_commits` to downloaders before any prerelease filtering.
2. **Use prerelease directory-list cache in repo fallback**
   - Wrap the `MESHTASTIC_GITHUB_IO_CONTENTS_URL` root listing with cache load/save + expiry logging.
3. **Unify commit timestamp cache expiry**
   - Make commit timestamp reads/writes go through one expiry-aware implementation.
   - Add tests for expiry and for legacy file compatibility.
4. **Settle repository downloader scope**
   - Either remove pipeline repo downloads (preferred for minimal drift) or implement config-backed selections.
5. **Add CLI summary assertions**
   - Tests that the CLI download command surfaces failure URLs/retryable/http status in output (and counts behave like legacy).

## How To Validate (Fast)

- `python -m pytest -q`
- Spot-check: run `fetchtastic download` with an existing v0.8.9-era config; verify:
  - doesn’t re-download already-complete assets
  - prerelease expected-version selection uses commit cache when available
  - failures show URL + retryable + HTTP status in summary
