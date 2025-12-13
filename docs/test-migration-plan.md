# Test Migration Plan: Legacy Downloader Removal

## Overview

This document outlines the comprehensive plan for migrating tests away from the legacy downloader (`downloader.py`) to the new modular architecture, enabling complete removal of legacy code.

## Current State Analysis

### Legacy Dependencies Identified

Based on code analysis, **74 test files** still import from `fetchtastic.downloader`:

#### High-Impact Test Files

- `test_download_core.py` - Core download functionality tests
- `test_setup_config.py` - Configuration and setup tests
- `test_prereleases.py` - Prerelease handling tests
- `test_utils.py` - Utility function tests
- `test_single_blob_cache.py` - Cache management tests
- `test_token_warning_fix.py` - Token handling tests
- `test_security_paths.py` - Security validation tests
- `test_extraction.py` - Archive extraction tests
- `test_coverage_fix.py` - Coverage reporting tests

#### Medium-Impact Test Files

- `test_cli.py` - CLI integration tests
- `test_versions.py` - Version handling tests
- `test_migration_reporting.py` - Migration layer tests

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

## Migration Strategy

### Phase 1: Mapping and Analysis (Week 1)

#### 1.1 Create Function Mapping Matrix

Create comprehensive mapping of legacy functions to new architecture equivalents:

| Legacy Function                       | New Location                                                 | Status   | Priority |
| ------------------------------------- | ------------------------------------------------------------ | -------- | -------- |
| `_get_latest_releases_data`           | `CacheManager.read_releases_cache_entry`                     | High     | P1       |
| `_get_prerelease_patterns`            | `VersionManager` methods                                     | High     | P1       |
| `_safe_rmtree`                        | `FileOperations.safe_rmtree`                                 | Medium   | P2       |
| `_sanitize_path_component`            | `FileOperations.sanitize_path`                               | Medium   | P2       |
| `main`                                | `DownloadOrchestrator.run_download_pipeline`                 | Critical | P0       |
| `_format_api_summary`                 | `DownloadOrchestrator._log_download_summary`                 | Medium   | P2       |
| `clear_commit_timestamp_cache`        | `CacheManager.clear_cache`                                   | Medium   | P2       |
| `_ensure_cache_dir`                   | `CacheManager.__init__`                                      | Low      | P3       |
| `_enrich_history_from_commit_details` | `VersionManager.parse_commit_history_for_prerelease_version` | High     | P1       |
| `_extract_prerelease_dir_info`        | `PrereleaseHistoryManager`                                   | High     | P1       |
| `_get_prerelease_commit_history`      | `PrereleaseHistoryManager.get_commit_history`                | High     | P1       |
| `_cleanup_apk_prereleases`            | `AndroidDownloader.manage_prerelease_tracking_files`         | High     | P1       |
| `cleanup_superseded_prereleases`      | `PrereleaseHistoryManager.manage_prerelease_tracking_files`  | High     | P1       |
| `_get_release_tuple`                  | `VersionManager.get_release_tuple`                           | Medium   | P2       |
| `matches_extract_patterns`            | `FileOperations.matches_extract_patterns`                    | Medium   | P2       |
| `safe_extract_path`                   | `FileOperations.safe_extract_path`                           | Medium   | P2       |

#### 1.2 Dependency Impact Analysis

- **P0 (Blocking)**: `main` function - Core entry point
- **P1 (High)**: Release data, prerelease handling, version management
- **P2 (Medium)**: Utility functions, cache operations, path handling
- **P3 (Low)**: Cache directory setup

### Phase 2: Test Migration Implementation (Weeks 2-3)

#### 2.1 Create Compatibility Shims

Create temporary compatibility modules in `fetchtastic.download.legacy_shims`:

```python
# legacy_shims/__init__.py
"""Compatibility shims for legacy test migration."""

from .downloader_shim import (
    get_latest_releases_data,
    get_prerelease_patterns,
    safe_rmtree,
    sanitize_path_component,
    format_api_summary,
    clear_commit_timestamp_cache,
    ensure_cache_dir,
    enrich_history_from_commit_details,
    extract_prerelease_dir_info,
    get_prerelease_commit_history,
    cleanup_apk_prereleases,
    cleanup_superseded_prereleases,
    get_release_tuple,
    matches_extract_patterns,
    safe_extract_path,
)

# Re-export for backward compatibility
__all__ = [
    'get_latest_releases_data',
    'get_prerelease_patterns',
    'safe_rmtree',
    'sanitize_path_component',
    'format_api_summary',
    'clear_commit_timestamp_cache',
    'ensure_cache_dir',
    'enrich_history_from_commit_details',
    'extract_prerelease_dir_info',
    'get_prerelease_commit_history',
    'cleanup_apk_prereleases',
    'cleanup_superseded_prereleases',
    'get_release_tuple',
    'matches_extract_patterns',
    'safe_extract_path',
]
```

#### 2.2 Implement Shim Functions

Each shim function will:

1. Import the new equivalent from modular architecture
2. Provide the same interface/signature as legacy function
3. Add deprecation warnings
4. Include compatibility mapping for any signature differences

#### 2.3 Update Test Imports Incrementally

Update test files in priority order:

**Week 2: Critical Path Tests**

1. `test_download_core.py` - Update imports to use shims
2. `test_prereleases.py` - Update imports to use shims
3. `test_security_paths.py` - Update imports to use shims

**Week 3: Core Functionality Tests**  
4. `test_setup_config.py` - Update imports to use shims 5. `test_utils.py` - Update imports to use shims 6. `test_single_blob_cache.py` - Update imports to use shims

**Week 4: Integration Tests** 7. `test_cli.py` - Update imports to use shims  
8. `test_versions.py` - Update imports to use shims 9. `test_extraction.py` - Update imports to use shims 10. `test_coverage_fix.py` - Update imports to use shims

#### 2.4 Test and Validate

After each test file update:

1. Run the specific test suite
2. Verify all tests pass
3. Check for deprecation warnings (confirming shim usage)
4. Run regression tests to ensure no behavior changes

### Phase 3: Legacy Removal (Week 4)

#### 3.1 Remove Legacy Code

Once all tests pass with shims:

1. **Delete `legacy_downloader.py`** - Remove monolithic file
2. **Update imports** - Remove from `__init__.py` if present
3. **Clean up references** - Update any remaining documentation

#### 3.2 Remove Compatibility Shims

1. Delete `fetchtastic.download.legacy_shims` directory
2. Remove shim imports from test files
3. Update test imports to use new architecture directly

#### 3.3 Update Migration Layer

1. **Simplify `DownloadMigration`** - Remove complex compatibility logic
2. **Route CLI directly** - Update `cli_integration.py` to use orchestrator directly
3. **Update documentation** - Reflect simplified architecture

### Phase 4: Documentation Updates (Week 5)

#### 4.1 Update Architecture Documentation

- Update `docs/refactor-implementation-plan.md` - Mark legacy removal as complete
- Update `docs/refactor-remaining-work.md` - Mark all tasks as complete
- Create `docs/legacy-removal-summary.md` - Document the removal process

#### 4.2 Update Developer Documentation

- Update `AGENTS.md` - Remove legacy downloader references
- Update README.md - Reflect new architecture only
- Update contribution guidelines - Reference new modular patterns

## Risk Mitigation

### Testing Risks

- **Behavior Changes**: New architecture might have subtle differences
- **Test Coverage**: Some edge cases might be missed
- **Regression Risk**: Legacy behavior might not be perfectly replicated

**Mitigation**:

- Comprehensive test suite with 100% pass rate required
- Side-by-side comparison testing during migration
- Detailed logging of any differences found

### Operational Risks

- **Breaking Changes**: Existing integrations might break
- **Performance**: New architecture might have different performance characteristics
- **Dependencies**: New import paths might affect deployment

**Mitigation**:

- Feature flag for gradual rollout
- Performance benchmarking before and after
- Backward compatibility warnings during transition

## Success Criteria

### Functional Requirements

- [ ] All 74 test files import from new architecture
- [ ] All tests pass without modification
- [ ] No legacy imports remain in codebase
- [ ] CLI works directly with orchestrator
- [ ] Migration layer simplified or removed

### Quality Requirements

- [ ] No deprecation warnings in production runs
- [ ] Test coverage maintained or improved
- [ ] Performance benchmarks meet or exceed legacy
- [ ] Documentation accurately reflects new architecture

### Security Requirements

- [ ] Path traversal vulnerabilities fixed (completed)
- [ ] Input validation maintained or improved
- [ ] No new security vulnerabilities introduced
- [ ] Security tests pass with new architecture

## Timeline

| Week | Activities                            | Expected Outcome                             |
| ---- | ------------------------------------- | -------------------------------------------- |
| 1    | Mapping analysis, shim creation       | Complete function matrix                     |
| 2    | Critical path tests migration         | Core tests updated and passing               |
| 3    | Core functionality tests migration    | Remaining tests updated and passing          |
| 4    | Integration tests migration           | All tests updated and passing                |
| 5    | Legacy removal, documentation updates | Legacy code removed, architecture simplified |

## Next Steps

1. **Immediate**: Create compatibility shim directory structure
2. **Week 1**: Begin with highest priority test migrations
3. **Continuous**: Run full test suite after each migration batch
4. **Validation**: Perform regression testing before legacy removal
5. **Final**: Complete legacy removal and documentation updates

This plan ensures systematic, risk-managed migration from legacy to new architecture while maintaining test coverage and functionality.
