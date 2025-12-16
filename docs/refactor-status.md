# Refactor Status & Remaining Work

This document tracks the ongoing modular refactor. It is intentionally detailed so any follow-on engineer can pick up where we left off, especially in areas where legacy feature parity is critical.

## Current Status

1. Modular download subsystem exists (`DownloadOrchestrator`, `MeshtasticAndroidAppDownloader`, `FirmwareReleaseDownloader`, `DownloadCLIIntegration`).
2. Cache management now centralizes on `CacheManager`, defaulting to `platformdirs.user_cache_dir("fetchtastic")` and providing atomic writes/expiry helpers.
3. CLI wiring has been re-implemented to call the new integration, log API summaries, report failures, and honor `LOG_LEVEL` plus clipboard helpers.
4. Repository download flows and repo cleanup command are routed through `RepositoryDownloader` and its new menu helpers.
5. Major shared utilities (`utils`, version management, prerelease history) have been split into dedicated modules.

These items are checkboxes only when both implementation and automated coverage (pytest+mypy) confirm parity with `v0.8.9` behavior.

## High-Priority Remaining Tasks

Each item below must be addressed to call the refactor “complete.”

1. **Release history / prerelease parity**
   - Ensure `PrereleaseHistoryManager` consumes cached commit histories and GitHub repo listings when selecting expected prerelease versions (commit hash lookup, timestamp-based cache expiry).
   - Wire commit history caches (meshtastic.github.io commits) into the prerelease selection so expected-version calculations match legacy results.
   - Enforce release/commit cache expiry constants consistently for all fetch paths (firmware, Android, repo directories). Update or migrate any tracking files previously stored outside `~/.cache/fetchtastic`.

2. **Version tracking / caching parity**
   - `CacheManager` must power the legacy `latest_*_release.json` files, always writing under `~/.cache/fetchtastic`, and the downloaders must read/write those exact filenames (without stray double `.json`).
   - All caches (releases, commits, repo directories, rate-limit summaries) need atomic writes and explicit expiry logic that mirrors the monolithic flow, including compatibility readers for older formats.
   - Cache eviction should be clearable via CLI (`DownloadCLIIntegration._clear_caches` and `BaseDownloader` flows). Add tests to prove on-disk cache files live entirely under `~/.cache/fetchtastic` and any root-level artifacts are removed.

3. **Extraction validation / prerelease sidecars**
   - Paths, hash checks, and zip validation must match the legacy extraction logic so `AUTO_EXTRACT` and `EXTRACT_PATTERNS` behave identically.
   - Ensure prerelease downloads use exclude patterns for device-specific debug builds (e.g., `*_tft*`, `heltec_*`, etc.) and verify files with hash info before skipping.
   - Sidecar metadata files (e.g., `.mt.json`) should be treated the same as in the old `download.py` when validating completeness.

4. **CLI reporting**
   - Summaries (`log_download_results_summary`, CLI `repo`, `topic`, and `setup` paths) must mention repository failure URLs, retry metadata, and any retryable flags that were reported in logs.
   - The CLI should expose `get_api_request_summary` so tests can mock rate-limit stats; ensure the `help`/`version`/`clean`/`repo` commands log the `
Update Available` block exactly as in legacy CLI.
   - Windows integration flags (`--update-integrations`) should log success, failure, and missing-config states consistently.

5. **Cron handling / automation**
   - The cleanup/setup logic must gracefully handle missing `crontab` binaries (e.g., non-existent in containers) and not raise when `crontab -l` fails.
   - Termux boot script creation/removal should honor `is_termux` and avoid raising when `termux-clipboard-set` or `termux-setup-storage` are absent.
   - Structured tests should mock `install_crond`, `setup_cron_job`, `check_cron_job_exists`, etc., so automated test coverage exists for Linux/Termux flows.

6. **Testing and tooling**
   - All tests must avoid real network calls (use `pytest` mocks and `CacheManager` fixtures).
   - Run `python -m pytest tests/ -v --cov=src/fetchtastic --cov-report=term-missing --cov-report=xml --junitxml=junit.xml` to validate coverage.
   - Run `.trunk/trunk check --fix --all` to satisfy Ruff/Bandit/Gitleaks expectations.
   - Run `python -m mypy src tests` (after tests are green) before concluding.

## Near-Term Verification Steps

1. Confirm no `latest_*_release.json` files exist in the repo root or any non-cache directory. Delete any leftovers.
2. Ensure CLI commands that rely on `log_utils.logger` (setup updates, repo clean, topic, version, help) are exercised by unit tests with mocks that assert side effects and logging calls.
3. Add regression tests for `CacheManager.get_cache_file_path` and `CacheManager.clear_all_caches` showing everything stays inside `~/.cache/fetchtastic`.

## Notes for Future Handovers

- This refactor must preserve _release completion checks_ (size/hash/zip) before skipping downloads, so keep an eye on `BaseDownloader.download`, verification flows, and `DownloadResult.was_skipped` semantics.
- The new architecture keeps `DownloadCLIIntegration` as the CLI-facing façade; avoid reintroducing a monolithic `download.py` file. Any “migration” helpers should be moved into this module if needed later.
- Document additional discoveries here before the next checkpoint (update the `## Current Status` list and mark items as done when fully tested).

Update this document every time you change/investigate a high-level area so a future agent can quickly see what is pending.
