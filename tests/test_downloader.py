"""
DEPRECATED: Main test module for fetchtastic downloader functionality.

This module previously served as the main entry point for test discovery, but
tests have been split into focused submodules:
- test_versions.py: Version comparison and parsing tests
- test_prereleases.py: Prerelease functionality tests
- test_security_paths.py: Path validation and symlink security tests
- test_extraction.py: File extraction and pattern matching tests
- test_download_core.py: Core download orchestration tests
- test_notifications.py: Notifications and UI message tests
- test_device_hardware.py: DeviceHardwareManager tests

This file is kept for backward compatibility during the transition period.
All test functionality has been migrated to the focused modules above.
TODO: Remove this file in v0.11.0 after confirming all tests are discovered properly.
"""

# This file is intentionally minimal - all tests have been migrated to focused modules
# See the list above for the new test locations
