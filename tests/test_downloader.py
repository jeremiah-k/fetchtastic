"""
Main test module for fetchtastic downloader functionality.

This module imports tests from focused submodules to maintain test discovery
while keeping the codebase organized and maintainable.

The original large test file (test_downloader_original.py) has been eliminated after
splitting tests into focused modules:
- test_versions.py: Version comparison and parsing tests
- test_prereleases.py: Prerelease functionality tests
- test_security_paths.py: Path validation and symlink security tests
- test_extraction.py: File extraction and pattern matching tests
- test_download_core.py: Core download orchestration tests
- test_notifications.py: Notifications and UI message tests
- test_device_hardware.py: DeviceHardwareManager tests (already existed)

This module serves as the main entry point for test discovery.
"""

# Pytest automatically discovers test functions from all modules in the tests/
# directory, so no explicit imports are needed for test discovery.

# test_device_hardware.py already exists and will be discovered separately

# Any remaining utility functions or fixtures that weren't moved can be added here
# For now, this serves as the main entry point for test discovery
