# Test Migration Plan: Legacy Downloader Removal

## Overview

This document provides a comprehensive roadmap for migrating test files from the legacy monolithic downloader (`src/fetchtastic/downloader.py`) to the new modular architecture (`src/fetchtastic/download/`).

## Current State Analysis

### Test Files Requiring Migration

| Test File                 | Lines | Legacy Imports | Priority | Complexity        | New Module Mapping                                         |
| ------------------------- | ----- | -------------- | -------- | ----------------- | ---------------------------------------------------------- |
| test_prereleases.py       | 2,531 | 21             | P1       | HIGH              | version.py, prerelease_history.py, android.py, firmware.py |
| test_download_core.py     | 3,632 | 3              | P1       | HIGH              | firmware.py, base.py, cache.py                             |
| test_security_paths.py    | 752   | 13             | P1       | SECURITY CRITICAL | files.py, base.py, repository.py                           |
| test_utils.py             | 1,366 | 3              | P2       | MEDIUM            | utils.py, cache.py                                         |
| test_extraction.py        | 545   | 1              | P2       | LOW               | utils.py                                                   |
| test_setup_config.py      | 1,750 | 6              | P2       | MEDIUM            | base.py, version.py                                        |
| test_coverage_fix.py      | 402   | 8              | P3       | MEDIUM            | cli_integration.py, orchestrator.py                        |
| test_token_warning_fix.py | 181   | 1              | P3       | LOW               | orchestrator.py, cli_integration.py                        |

**Total: 11,159 lines of test code, 56 legacy import statements**

### Critical Functions Imported

The most commonly imported legacy functions:

- `_get_latest_releases_data` - Release data fetching
- `_get_prerelease_patterns` - Prerelease pattern matching
- `_safe_rmtree` - Safe directory removal
- `_sanitize_path_component` - Path sanitization
- `main` - Main download entry point
- `_format_api_summary` - Summary formatting
- `clear_commit_timestamp_cache` - Cache management
- `_ensure_cache_dir` - Directory setup
- `_enrich_history_from_commit_details` - History processing
- `_extract_prerelease_dir_info` - Prerelease directory analysis
- `_get_prerelease_commit_history` - Commit history retrieval
- `_cleanup_apk_prereleases` - APK cleanup
- `cleanup_superseded_prereleases` - Prerelease cleanup
- `_get_release_tuple` - Version parsing
- `matches_extract_patterns` - Pattern matching
- `safe_extract_path` - Safe extraction paths

## Legacy Function Mapping

### Security Functions

```python
# Legacy → New Location
_safe_rmtree() → FileOperations._safe_rmtree() (files.py:228)
_sanitize_path_component() → BaseDownloader._sanitize_path_component() (base.py:267)
_atomic_write() → FileOperations.atomic_write() (files.py:317) & CacheManager.atomic_write() (cache.py:66)
safe_extract_path() → FileOperations.safe_extract_path() (files.py)
```

### Version Functions

```python
# Legacy → New Location
_normalize_version() → VersionManager.normalize_version() (version.py:63)
_get_release_tuple() → VersionManager.get_release_tuple()
_sort_key → VersionManager._sort_key
```

### Cache Functions

```python
# Legacy → New Location
get_commit_timestamp() → CacheManager.get_commit_timestamp() (cache.py:635)
clear_commit_timestamp_cache() → CacheManager.clear_cache()
_commit_timestamp_cache → CacheManager internal state
_cache_lock → CacheManager internal state
```

### Prerelease Functions

```python
# Legacy → New Location
cleanup_superseded_prereleases() → PrereleaseHistoryManager.should_cleanup_superseded_prerelease() (prerelease_history.py:547)
get_prerelease_tracking_info() → PrereleaseHistoryManager methods
update_prerelease_tracking() → PrereleaseHistoryManager.update_prerelease_tracking() (prerelease_history.py:376)
_get_prerelease_commit_history() → PrereleaseHistoryManager.get_prerelease_commit_history() (prerelease_history.py:270)
_extract_prerelease_dir_info() → PrereleaseHistoryManager methods
_enrich_history_from_commit_details() → PrereleaseHistoryManager methods
```

### Utility Functions

```python
# Legacy → New Location
matches_extract_patterns() → utils.matches_extract_patterns() (utils.py:1275) - UNCHANGED
_format_api_summary() → utils.format_api_summary() - UNCHANGED
_extract_identifier_from_entry() → VersionManager methods
_format_history_entry() → PrereleaseHistoryManager methods
_get_commit_cache_file() → CacheManager methods
_get_commit_hash_from_dir() → PrereleaseHistoryManager methods
_is_entry_deleted() → PrereleaseHistoryManager methods
_load_commit_cache() → CacheManager methods
_save_commit_cache() → CacheManager methods
```

### Configuration Functions

```python
# Legacy → New Location
_get_prerelease_patterns() → FirmwareReleaseDownloader._get_prerelease_patterns() (src/fetchtastic/download/firmware.py)
get_prerelease_patterns() → fetchtastic.download.config_utils.get_prerelease_patterns() (src/fetchtastic/download/config_utils.py)
_get_latest_releases_data() → DownloadOrchestrator methods
main() → DownloadCLIIntegration.main()
```

### File Operations

```python
# Legacy → New Location
_ensure_cache_dir() → CacheManager._ensure_cache_dir()
_cleanup_apk_prereleases() → MeshtasticAndroidAppDownloader methods
```

## Detailed Import Analysis

### test_prereleases.py (21 imports)

```python
# Current imports from legacy:
from fetchtastic.downloader import (
    _commit_timestamp_cache,           # → CacheManager internal state
    _extract_identifier_from_entry,    # → VersionManager method
    _format_history_entry,             # → PrereleaseHistoryManager method
    _get_commit_cache_file,            # → CacheManager method
    _get_commit_hash_from_dir,         # → PrereleaseHistoryManager method
    _is_entry_deleted,                 # → PrereleaseHistoryManager method
    _load_commit_cache,                # → CacheManager method
    _normalize_version,                 # → VersionManager.normalize_version()
    _save_commit_cache,                # → CacheManager method
    _sort_key,                         # → VersionManager._sort_key
    clear_commit_timestamp_cache,      # → CacheManager.clear_cache()
    get_commit_timestamp,              # → CacheManager.get_commit_timestamp()
    get_prerelease_tracking_info,      # → PrereleaseHistoryManager method
    matches_extract_patterns,           # → utils.matches_extract_patterns()
    update_prerelease_tracking,        # → PrereleaseHistoryManager.update_prerelease_tracking()
)
```

### test_download_core.py (3 imports)

```python
# Limited imports, mostly uses new modules already
# Focus on cache and file operation functions
```

### test_security_paths.py (13 imports)

```python
# SECURITY CRITICAL - All path safety functions
from fetchtastic.downloader import (
    _safe_rmtree,                    # → FileOperations._safe_rmtree()
    _sanitize_path_component,        # → BaseDownloader._sanitize_path_component()
    safe_extract_path,               # → FileOperations.safe_extract_path()
)
```

### test_utils.py (3 imports)

```python
from fetchtastic.downloader import (
    _format_api_summary,             # → utils.format_api_summary()
    clear_commit_timestamp_cache,    # → CacheManager.clear_cache()
    _cache_lock, _commit_timestamp_cache,  # → CacheManager internal state
)
```

### test_setup_config.py (6 imports)

```python
from fetchtastic.downloader import (
    _get_prerelease_patterns,         # → NEEDS TO BE ADDED
)
```

### test_coverage_fix.py (8 imports)

```python
# Dynamic imports and main function
import fetchtastic.downloader as downloader_module  # → Use new modules directly
from fetchtastic.downloader import main, _get_latest_releases_data  # → DownloadCLIIntegration, DownloadOrchestrator
```

### test_extraction.py (1 import)

```python
from fetchtastic.downloader import matches_extract_patterns  # → utils.matches_extract_patterns()
```

### test_token_warning_fix.py (1 import)

```python
from fetchtastic.downloader import _get_latest_releases_data, main  # → DownloadOrchestrator, DownloadCLIIntegration
```

## Risk Assessment

### HIGH RISK - Security Tests

- **test_security_paths.py**: Path traversal protection tests must maintain exact security guarantees
- **Mitigation**: Manual verification of each security test after migration
- **Validation**: Test must still block all attack vectors

### HIGH RISK - Complex Mocking

- **test_prereleases.py**: 259 mock operations, complex test fixtures
- **test_download_core.py**: 191 mock operations
- **Mitigation**: Systematic mock path updates, test-by-test validation

### MEDIUM RISK - Missing Functions

- **\_get_prerelease_patterns()**: Not found in new modules, must be added
- **Mitigation**: Add function to appropriate module before migration

### LOW RISK - Utility Functions

- **test_extraction.py, test_utils.py**: Simple function replacements
- **Mitigation**: Direct function mapping, minimal test changes

## Migration Strategy

### Phase 1: Analysis & Preparation ✅ COMPLETED

- [x] Dependency mapping completed
- [x] Migration priority matrix created
- [x] Module equivalency document created
- [x] Risk assessment completed
- [x] Add missing \_get_prerelease_patterns() function

### Phase 2: Core Tests (P1) 🔄 IN PROGRESS

- [🔄] Migrate test_prereleases.py (2,531 lines, 21 imports) - BASIC MIGRATION WORKING
- [ ] Migrate test_download_core.py (3,632 lines, 3 imports)

### Phase 3: Security & Utilities (P1/P2) ✅ PARTIALLY COMPLETED

- [ ] Migrate test_security_paths.py (752 lines, 13 imports) - SECURITY CRITICAL
- [x] Migrate test_utils.py (1,366 lines, 3 imports) - COMPLETED
- [x] Migrate test_extraction.py (545 lines, 1 import) - COMPLETED

### Phase 4: Integration (P2/P3) 🔄 MOSTLY COMPLETED

- [x] Migrate test_setup_config.py (1,750 lines, 6 imports) - COMPLETED
- [🔄] Migrate test_coverage_fix.py (402 lines, 8 imports) - PARTIALLY COMPLETED (complex test)
- [🔄] Migrate test_token_warning_fix.py (181 lines, 1 import) - PARTIALLY COMPLETED (complex test)

### Phase 5: Menu/Config Alignment

- [ ] Config key parity verification
- [ ] Menu prompt alignment
- [ ] Default value confirmation

### Phase 6: Legacy Removal

- [ ] All tests passing (0 legacy imports)
- [ ] Performance parity confirmed
- [ ] Documentation updated
- [ ] Legacy files removed

## Success Criteria

- ✅ Zero legacy imports in test files
- ✅ 100% test pass rate maintained
- ✅ Security tests still catch vulnerabilities
- ✅ Performance parity or better vs legacy
- ✅ Config compatibility fully verified
- ✅ Documentation completely updated
- ✅ Legacy code removed from codebase

## Next Steps

1. **Add missing function**: Implement `_get_prerelease_patterns()` in appropriate module
2. **Begin Phase 2**: Start with test_prereleases.py migration
3. **Security validation**: Pay special attention to test_security_paths.py
4. **Progress tracking**: Update this document after each phase completion

---

_Last Updated: Phase 1 Analysis Complete_
