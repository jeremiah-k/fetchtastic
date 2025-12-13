"""
Legacy Compatibility Layer for Test Migration

This module provides backward compatibility functions and mocks for the test_prereleases.py
migration to the new modular architecture.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager
from fetchtastic.download.version import VersionManager
from fetchtastic.utils import make_github_api_request

# Global instances for backward compatibility
_cache_manager = CacheManager()
_prerelease_manager = PrereleaseHistoryManager()
_version_manager = VersionManager()
_firmware_downloader = FirmwareReleaseDownloader({})  # Empty config for testing


# Legacy function wrappers for backward compatibility during migration
def check_for_prereleases(*args, **kwargs):
    """Legacy wrapper - needs implementation in new architecture"""
    # For now, return mock data
    return [], []


def get_prerelease_tracking_info(*args, **kwargs):
    """Legacy wrapper - needs implementation in new architecture"""
    # For now, return mock data
    return {}


def cleanup_superseded_prereleases(latest_release_tag: str, *args, **kwargs):
    """Legacy wrapper using new firmware downloader"""
    return _firmware_downloader.cleanup_superseded_prereleases(latest_release_tag)


def _fetch_prerelease_directories(*args, **kwargs):
    """Legacy wrapper - needs implementation in new architecture"""
    # For now, return mock data
    return []


def _clear_prerelease_cache():
    """Legacy wrapper using new cache manager"""
    return _cache_manager.clear_all_caches()


def _get_prerelease_commit_history(*args, **kwargs):
    """Legacy wrapper using new prerelease manager"""
    return _prerelease_manager.get_prerelease_commit_history(*args, **kwargs)


def _build_simplified_prerelease_history(*args, **kwargs):
    """Legacy wrapper using new prerelease manager"""
    return _prerelease_manager.build_simplified_prerelease_history(*args, **kwargs)


def _fetch_recent_repo_commits(*args, **kwargs):
    """Legacy wrapper using new prerelease manager"""
    return _prerelease_manager.fetch_recent_repo_commits(*args, **kwargs)


def get_commit_timestamp(*args, **kwargs):
    """Legacy wrapper using new cache manager"""
    return _cache_manager.get_commit_timestamp(*args, **kwargs)


def _find_latest_remote_prerelease_dir(*args, **kwargs):
    """Legacy wrapper - needs implementation in new architecture"""
    # For now, return mock data
    return None


def _create_default_prerelease_entry(*args, **kwargs):
    """Legacy wrapper using new prerelease manager"""
    return _prerelease_manager._create_default_prerelease_entry(*args, **kwargs)


def _enrich_history_from_commit_details(*args, **kwargs):
    """Legacy wrapper - needs implementation in new architecture"""
    # For now, return None
    return None


def _extract_clean_version(version):
    """Legacy wrapper using new version manager"""
    return _version_manager.extract_clean_version(version)


def clear_all_caches():
    """Legacy wrapper using new cache manager"""
    return _cache_manager.clear_all_caches()


# Legacy global variables for backward compatibility
_commit_cache_file = None
_releases_cache_file = None
_prerelease_dir_cache_file = None
_prerelease_commit_history_file = None
_prerelease_dir_cache_loaded = False
_prerelease_commit_history_loaded = False
_commit_cache_loaded = False
_prerelease_dir_cache = {}
_prerelease_commit_history_cache = {}
_commit_timestamp_cache = {}
_cache_lock = Mock()  # Mock lock for compatibility


# Mock platformdirs for tests
class MockPlatformDirs:
    @staticmethod
    def user_cache_dir():
        return tempfile.gettempdir()


platformdirs = MockPlatformDirs()


# Mock menu_repo for tests
class MockMenuRepo:
    @staticmethod
    def fetch_repo_directories(*args, **kwargs):
        return []

    @staticmethod
    def fetch_directory_contents(*args, **kwargs):
        return []


menu_repo = MockMenuRepo()


# Mock download_file_with_retry for tests
def download_file_with_retry(*args, **kwargs):
    """Mock download function"""
    return True


# Mock _ensure_cache_dir for tests
def _ensure_cache_dir():
    """Mock cache dir function"""
