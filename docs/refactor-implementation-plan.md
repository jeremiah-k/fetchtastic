# Fetchtastic Downloader Refactor - Implementation Plan

## Executive Summary

This document provides a comprehensive, prioritized implementation plan for completing the Fetchtastic downloader refactor with full feature parity and backward compatibility.

Source of truth for current parity status: `docs/refactor-handoff.md`.

## Current State Analysis

### Completed Components

- ✅ Core modular architecture (interfaces, base classes, orchestration)
- ✅ Android APK downloader implementation
- ✅ Firmware downloader implementation
- ✅ Repository downloader skeleton
- ✅ CLI integration layer
- ✅ Migration compatibility layer
- ✅ Basic version management and caching

### P1 Functional Parity Gaps - In Progress

- ⚠️ **P1.1: Repository Downloader Integration** - Interactive repo browsing works; pipeline wiring needs decision (avoid drift).
- ⚠️ **P1.2: Prerelease Handling** - Core logic exists; remaining timing/caching parity still open (commit refresh timing, dir-list cache).
- ⚠️ **P1.3: Version Tracking + Cache Parity** - Atomic writes + compat readers exist; commit timestamp cache expiry unification still open.
- ⚠️ **P1.4: Extraction Parity & Safety** - Safety foundations exist; validate remaining sidecar/need-check alignment as parity hardening.

### Remaining Gaps (from refactor-remaining-work.md)

#### P1 - Functional Parity Gaps

1. **Repository Downloader Integration**
2. **Prerelease Handling**
3. **Version Tracking + Cache Parity**
4. **Extraction Parity & Safety**

#### P2 - Reliability & Reporting

5. **Retry and Failure Metadata**
6. **Cache Manager Enhancements**
7. **Menu/Config Alignment**

#### P3 - Clean-up & Migration

8. **CLI Path Simplification**
9. **Legacy Removal**
10. **Documentation/Tests**

## Detailed Implementation Plan

### 🔴 P1.1: Wire RepositoryDownloader into DownloadOrchestrator

**Task**: Integrate RepositoryDownloader with selection/exclude semantics matching legacy repo-dls

**Legacy Functionality**:

- Repository downloads from meshtastic.github.io
- Directory structure: `repo-dls` under firmware path
- Executable bit handling for shell scripts
- Selection/exclude pattern matching

**Implementation Steps**:

1. **Update DownloadOrchestrator** (`src/fetchtastic/download/orchestrator.py`)
   - Add repository downloader initialization
   - Add `_process_repository_downloads()` method
   - Integrate repository processing into `run_download_pipeline()`

2. **Enhance RepositoryDownloader** (`src/fetchtastic/download/repository.py`)
   - Implement proper repository file listing from GitHub API
   - Add selection/exclude pattern filtering
   - Implement legacy directory structure (`repo-dls` under firmware)
   - Add executable permission handling for shell scripts

3. **Update Interfaces** (`src/fetchtastic/download/interfaces.py`)
   - Ensure RepositoryDownloader implements all required interfaces
   - Add repository-specific result tracking

**Dependencies**:

- Requires GitHub API integration for repository listing
- Requires path traversal safety validation

**Validation Criteria**:

- Repository files downloaded to correct `repo-dls` directory
- Shell scripts have executable permissions set
- Selection/exclude patterns work correctly
- CLI reporting identifies repository downloads/failures

**Edge Cases**:

- Empty repository listings
- Invalid repository URLs
- Permission errors on executable bit setting
- Path traversal attempts in repository structure

---

### 🔴 P1.2: Prerelease Handling for Firmware/APK

**Task**: Port commit-history and directory-scan logic for prerelease handling

**Legacy Functionality**:

- Expected version computation from commit history
- Commit hash suffix handling
- Prerelease tracking JSONs with fields and expiry
- Superseded-prerelease cleanup
- `CHECK_APK_PRERELEASES` / `CHECK_FIRMWARE_PRERELEASES` pattern-aware selection

**Implementation Steps**:

1. **Enhance VersionManager** (`src/fetchtastic/download/version.py`)
   - Add commit history parsing logic
   - Implement expected version computation
   - Add commit hash suffix handling
   - Implement prerelease version comparison

2. **Update Downloaders** (Android & Firmware)
   - Add prerelease detection and filtering
   - Implement prerelease tracking JSON generation
   - Add expiry handling for prerelease tracking
   - Implement superseded-prerelease cleanup logic

3. **Update Orchestrator**
   - Add prerelease configuration handling
   - Implement pattern-aware prerelease selection
   - Add prerelease-specific result tracking

**Dependencies**:

- Requires VersionManager enhancements
- Requires cache manager for prerelease tracking

**Validation Criteria**:

- Prereleases correctly identified and filtered
- Prerelease tracking JSONs match legacy format
- Superseded prereleases are cleaned up
- Pattern-aware selection works correctly

**Edge Cases**:

- Malformed version strings
- Missing commit history
- Expired prerelease tracking files
- Conflicting prerelease patterns

---

### 🔴 P1.3: Version Tracking + Cache Parity

**Task**: Align version tracking and cache behavior with legacy implementation

**Legacy Functionality**:

- Latest-release/prerelease JSON write format and locations
- Atomic writes with timestamps
- Cache expiry semantics for releases/commit timestamps
- Backward-compatible readers for existing tracking files

**Implementation Steps**:

1. **Enhance CacheManager** (`src/fetchtastic/download/cache.py`)
   - Implement atomic write functionality
   - Add timestamp tracking for cache entries
   - Implement cache expiry based on legacy constants
   - Add backward-compatible JSON readers

2. **Update Version Tracking**
   - Implement latest-release JSON generation
   - Add prerelease version JSON generation
   - Implement safe migration of existing tracking files
   - Add version tracking validation

3. **Update Downloaders**
   - Integrate enhanced cache manager
   - Add version tracking calls
   - Implement cache invalidation logic

**Dependencies**:

- Requires CacheManager enhancements
- Requires VersionManager for version comparison

**Validation Criteria**:

- Version tracking JSONs match legacy format
- Atomic writes prevent corruption
- Cache expiry works according to legacy timing
- Existing tracking files are safely migrated

**Edge Cases**:

- Corrupted cache files
- Concurrent cache access
- Missing legacy tracking files
- Version format mismatches

---

### 🔴 P1.4: Extraction Parity & Safety

**Task**: Implement extraction validation and safety features

**Legacy Functionality**:

- `_validate_extraction_patterns` equivalent
- `check_extraction_needed` equivalent
- Hash/sidecar behavior consistency
- Traversal-safe extraction path handling
- Exclude-aware extraction

**Implementation Steps**:

1. **Enhance Files Module** (`src/fetchtastic/download/files.py`)
   - Implement `_validate_extraction_patterns` equivalent
   - Add `check_extraction_needed` functionality
   - Implement traversal-safe path handling
   - Add hash verification for extracted files

2. **Update Firmware Downloader**
   - Integrate extraction validation
   - Add exclude pattern handling during extraction
   - Implement hash/sidecar generation
   - Add extraction result tracking

3. **Update Interfaces**
   - Add extraction validation methods to interfaces
   - Update DownloadResult to include extraction metadata

**Dependencies**:

- Requires Files module enhancements
- Requires updated interfaces

**Validation Criteria**:

- Extraction patterns validated correctly
- Traversal attempts blocked
- Hash verification works for extracted files
- Exclude patterns applied during extraction

**Edge Cases**:

- Malicious archive with traversal attempts
- Corrupted archive files
- Missing extraction patterns
- Conflicting include/exclude patterns

---

### 🟡 P2.1: Retry and Failure Metadata

**Task**: Implement robust retry logic with detailed failure tracking

**Legacy Functionality**:

- Per-asset URL, size, and type in DownloadResult
- Real retry loop using stored metadata
- Detailed failure reporting in CLI

**Implementation Steps**:

1. **Enhance DownloadResult** (`src/fetchtastic/download/interfaces.py`)
   - Add URL, size, and type fields
   - Add retry count tracking
   - Add detailed error metadata

2. **Update Orchestrator Retry Logic**
   - Implement proper retry loop with backoff
   - Add retry configuration options
   - Implement retry result tracking

3. **Update CLI Reporting**
   - Add detailed failure metadata to CLI output
   - Implement retry progress reporting

**Dependencies**:

- Requires enhanced DownloadResult interface
- Requires updated orchestrator

**Validation Criteria**:

- Retries work with proper backoff
- Failure metadata captured correctly
- CLI shows detailed retry information

**Edge Cases**:

- Network failures during retry
- Rate limiting during retries
- Maximum retry attempts reached

---

### 🟡 P2.2: Cache Manager Enhancements

**Task**: Port legacy cache features to new architecture

**Legacy Functionality**:

- Commit timestamp caching
- Rate-limit tracking hooks
- Cache invalidation/refresh switches (force_refresh)

**Implementation Steps**:

1. **Enhance CacheManager**
   - Add commit timestamp caching
   - Implement rate-limit tracking
   - Add force_refresh functionality
   - Implement cache statistics

2. **Update Downloaders**
   - Integrate enhanced cache features
   - Add cache invalidation hooks
   - Implement rate-limit handling

**Dependencies**:

- Requires CacheManager updates
- Requires downloader integration

**Validation Criteria**:

- Commit timestamps cached correctly
- Rate limiting works as expected
- Force refresh clears appropriate caches

**Edge Cases**:

- Cache corruption during timestamp updates
- Rate limit exceeded scenarios
- Concurrent cache access

---

### 🟡 P2.3: Menu/Config Alignment

**Task**: Ensure menu selections map correctly to new architecture

**Legacy Functionality**:

- `SELECTED_FIRMWARE_ASSETS` compatibility
- `SELECTED_PRERELEASE_ASSETS` compatibility
- Prompt/message consistency
- Exclude defaults handling

**Implementation Steps**:

1. **Update Menu Systems**
   - Ensure menu_apk.py uses new config keys
   - Update menu_firmware.py for new architecture
   - Add menu_repo.py integration

2. **Update Configuration Handling**
   - Add backward-compatible config key mapping
   - Implement prompt consistency checks
   - Add exclude defaults validation

**Dependencies**:

- Requires menu system updates
- Requires configuration validation

**Validation Criteria**:

- Legacy config keys work with new system
- Menu prompts match legacy expectations
- Exclude patterns work correctly

**Edge Cases**:

- Missing configuration values
- Invalid menu selections
- Conflicting configuration options

---

### 🟢 P3.1: CLI Path Simplification

**Task**: Decide on DownloadMigration retirement strategy

**Legacy Functionality**:

- Direct CLI to orchestrator routing
- Thin compatibility shim if needed
- Test compatibility requirements

**Implementation Steps**:

1. **Analyze Test Requirements**
   - Identify tests requiring migration module
   - Determine if thin shim is sufficient

2. **Update CLI Integration**
   - Route CLI directly to orchestrator
   - Add compatibility shim if needed
   - Update CLI error handling

**Dependencies**:

- Requires test analysis
- Requires CLI integration updates

**Validation Criteria**:

- CLI works without migration module
- All tests pass with new routing
- Error handling preserved

**Edge Cases**:

- Tests requiring legacy behavior
- CLI parameter compatibility
- Error handling edge cases

---

### 🟢 P3.2: Legacy Removal

**Task**: Remove monolithic downloader after parity verification

**Legacy Functionality**:

- Remove old downloader.py
- Update imports and references
- Clean up legacy dependencies

**Implementation Steps**:

1. **Verify Full Parity**
   - Run comprehensive test suite
   - Manual testing of all scenarios
   - Performance benchmarking

2. **Remove Legacy Code**
   - Delete downloader.py
   - Update imports throughout codebase
   - Clean up legacy references

**Dependencies**:

- Requires full parity verification
- Requires test suite completion

**Validation Criteria**:

- All tests pass without legacy code
- No broken imports or references
- Performance not degraded

**Edge Cases**:

- Hidden legacy dependencies
- Import path conflicts
- Test failures after removal

---

## Implementation Priority Matrix

| Priority | Task Group             | Estimated Effort | Risk Level | Dependencies            | Status           |
| -------- | ---------------------- | ---------------- | ---------- | ----------------------- | ---------------- |
| P1       | Repository Integration | Medium           | Medium     | GitHub API, Path Safety | ✅ Completed     |
| P1       | Prerelease Handling    | High             | High       | Version Manager, Cache  | ✅ Completed     |
| P1       | Version Tracking       | Medium           | Medium     | Cache Manager           | ✅ Completed     |
| P1       | Extraction Parity      | High             | High       | Files Module            | ✅ Completed     |
| P2       | Retry Logic            | Medium           | Low        | Orchestrator            | ✅ Completed     |
| P2       | Cache Enhancements     | Low              | Low        | Cache Manager           | 🚧 Next Priority |
| P2       | Menu Alignment         | Low              | Low        | Menu Systems            | ⏳ Pending       |
| P3       | CLI Simplification     | Low              | Low        | Test Analysis           | ⏳ Pending       |
| P3       | Legacy Removal         | Low              | Medium     | Full Testing            | ⏳ Pending       |

## Recommended Implementation Order

1. **✅ P1.4: Extraction Parity & Safety** (Foundation for other features) - COMPLETED
2. **✅ P1.3: Version Tracking + Cache Parity** (Required for prerelease handling) - COMPLETED
3. **🚧 P1.2: Prerelease Handling** (Complex, depends on version tracking) - IN PROGRESS
4. **⏳ P1.1: Repository Integration** (Independent, can run in parallel)
5. **⏳ P2.1: Retry Logic** (Enhancement, lower risk)
6. **⏳ P2.2: Cache Enhancements** (Enhancement)
7. **⏳ P2.3: Menu Alignment** (UI/Config layer)
8. **⏳ P3.1: CLI Simplification** (Cleanup)
9. **⏳ P3.2: Legacy Removal** (Final step)

## Validation Strategy

### Test Coverage Requirements

- **Unit Tests**: Each component tested in isolation
- **Integration Tests**: Component interactions verified
- **Regression Tests**: All legacy functionality preserved
- **Performance Tests**: No degradation from legacy

### Test Migration Plan

1. Port high-value legacy tests (prerelease, repo downloads, extraction)
2. Expand modular test suites for new features
3. Add integration tests for orchestration layer
4. Verify all existing test scenarios work

### Validation Checklist

- [x] Repository downloads work with correct directory structure ✅
- [x] Prerelease handling matches legacy behavior ✅
- [x] Version tracking JSONs are backward compatible ✅
- [x] Extraction is safe and matches legacy patterns ✅
- [x] Retry logic works with proper metadata ✅
- [ ] Cache behavior matches legacy timing and expiry
- [ ] Menu selections map correctly to new config
- [ ] CLI works without migration module
- [ ] All tests pass after legacy removal

## Risk Mitigation

### Technical Risks

| Risk                            | Mitigation Strategy                     |
| ------------------------------- | --------------------------------------- |
| Breaking existing functionality | Comprehensive regression testing        |
| Performance degradation         | Performance profiling and optimization  |
| Complexity increase             | Clear documentation and examples        |
| Integration issues              | Incremental migration with verification |

### Project Risks

| Risk            | Mitigation Strategy                      |
| --------------- | ---------------------------------------- |
| Scope creep     | Clear boundaries and focus on core goals |
| Timeline delays | Phased approach with clear milestones    |
| Testing gaps    | Comprehensive test coverage requirements |

## Success Criteria

1. **Functional Parity**: All existing functionality preserved and verified
2. **Modularity**: Clear separation of concerns achieved
3. **Extensibility**: New downloaders can be added easily
4. **Maintainability**: Codebase is easier to understand and modify
5. **Test Coverage**: Comprehensive test suite maintained
6. **Performance**: No significant performance degradation
7. **Documentation**: Complete and accurate documentation

## Next Steps

1. **✅ Review and Approval**: Get stakeholder feedback on this implementation plan - COMPLETED
2. **✅ Implementation Planning**: Break down into specific coding tasks - COMPLETED
3. **✅ Environment Setup**: Ensure development environment is ready - COMPLETED
4. **✅ Incremental Implementation**: Start with P1.4 (Extraction Parity) as foundation - COMPLETED
5. **✅ Continue Implementation**: Proceed with P1.3 (Version Tracking + Cache Parity) - COMPLETED
6. **✅ Complete Prerelease Handling**: Implement P1.2 (Prerelease Handling for Firmware/APK) - COMPLETED
7. **✅ Finalize Repository Integration**: Implement P1.1 (Repository Downloader Integration) - COMPLETED
8. **✅ Complete Retry Logic**: Implement P2.1 (Retry and Failure Metadata) - COMPLETED
9. **🚧 Proceed with P2 Tasks**: Implement P2.2 (Cache Manager Enhancements) - NEXT PRIORITY
10. **⏳ Test and Validate**: Ensure all functionality works correctly
11. **⏳ Document and Cleanup**: Update documentation and remove legacy code
