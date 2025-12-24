"""
Tests for Cache Management Module

Comprehensive tests for the cache.py module covering:
- Cache manager initialization and directory handling
- Atomic file operations (text and JSON)
- Cache expiry functionality
- GitHub API caching (releases, commit timestamps)
- Repository directory caching
- Backward compatibility features
- Legacy cache format support
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.cache import (
    CacheManager,
)

pytestmark = [pytest.mark.unit, pytest.mark.infrastructure]


class TestCacheManagerInitialization:
    """Test CacheManager initialization and basic functionality."""

    def test_init_default_cache_dir(self):
        """Test initialization with default cache directory."""
        cache_manager = CacheManager()
        assert cache_manager.cache_dir is not None
        assert os.path.exists(cache_manager.cache_dir)

    def test_init_custom_cache_dir(self, tmp_path):
        """Test initialization with custom cache directory."""
        custom_dir = str(tmp_path / "custom_cache")
        cache_manager = CacheManager(custom_dir)
        assert cache_manager.cache_dir == custom_dir
        assert os.path.exists(custom_dir)

    def test_ensure_cache_dir_exists(self, tmp_path):
        """Test that cache directory is created if it doesn't exist."""
        cache_dir = tmp_path / "new_cache_dir"
        assert not cache_dir.exists()

        CacheManager(str(cache_dir))
        assert cache_dir.exists()
        assert cache_dir.is_dir()


class TestAtomicOperations:
    """Test atomic file operations."""

    def test_atomic_write_text_success(self, tmp_path):
        """Test successful atomic text write."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "test.txt"
        content = "test content"

        result = cache_manager.atomic_write_text(str(test_file), content)
        assert result is True
        assert test_file.exists()
        assert test_file.read_text() == content

    def test_atomic_write_text_failure(self):
        """Test atomic text write failure."""
        cache_manager = CacheManager()
        # Try to write to a directory that doesn't exist and can't be created
        result = cache_manager.atomic_write_text(
            "/nonexistent/deep/path/test.txt", "content"
        )
        assert result is False

    def test_atomic_write_json_success(self, tmp_path):
        """Test successful atomic JSON write."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "test.json"
        data = {"key": "value", "number": 42}

        result = cache_manager.atomic_write_json(str(test_file), data)
        assert result is True
        assert test_file.exists()

        # Verify content
        with open(test_file, "r") as f:
            loaded_data = json.load(f)
        assert loaded_data == data

    def test_atomic_write_json_failure(self):
        """Test atomic JSON write failure."""
        cache_manager = CacheManager()
        result = cache_manager.atomic_write_json(
            "/nonexistent/path/test.json", {"data": "test"}
        )
        assert result is False

    def test_read_json_success(self, tmp_path):
        """Test successful JSON read."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "test.json"
        data = {"key": "value"}

        with open(test_file, "w") as f:
            json.dump(data, f)

        result = cache_manager.read_json(str(test_file))
        assert result == data

    def test_read_json_not_exists(self):
        """Test JSON read for non-existent file."""
        cache_manager = CacheManager()
        result = cache_manager.read_json("/non/existent/file.json")
        assert result is None

    def test_read_json_invalid(self, tmp_path):
        """Test JSON read for invalid JSON."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "invalid.json"

        with open(test_file, "w") as f:
            f.write("not valid json")

        result = cache_manager.read_json(str(test_file))
        assert result is None


class TestBackwardCompatibility:
    """Test backward compatibility features."""

    def test_read_json_with_backward_compatibility(self, tmp_path):
        """Test reading JSON with key mapping."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "test.json"
        data = {"old_key": "value", "new_key": "existing"}

        with open(test_file, "w") as f:
            json.dump(data, f)

        result = cache_manager.read_json_with_backward_compatibility(
            str(test_file), {"old_key": "new_key"}
        )
        assert result["old_key"] == "value"
        assert result["new_key"] == "existing"  # Should not overwrite existing

    def test_read_json_with_backward_compatibility_no_mapping(self, tmp_path):
        """Test reading JSON without key mapping."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "test.json"
        data = {"key": "value"}

        with open(test_file, "w") as f:
            json.dump(data, f)

        result = cache_manager.read_json_with_backward_compatibility(str(test_file))
        assert result == data


class TestCacheExpiry:
    """Test cache expiry functionality."""

    def test_cache_with_expiry_success(self, tmp_path):
        """Test successful cache write with expiry."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "cache.json"
        data = {"test": "data"}

        result = cache_manager.cache_with_expiry(str(test_file), data, 1.0)
        assert result is True
        assert test_file.exists()

        # Verify structure
        with open(test_file, "r") as f:
            cache_data = json.load(f)

        assert "data" in cache_data
        assert "cached_at" in cache_data
        assert "expires_at" in cache_data
        assert cache_data["data"] == data

    def test_read_cache_with_expiry_valid(self, tmp_path):
        """Test reading valid (non-expired) cache."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "cache.json"

        # Create cache data
        now = datetime.now(timezone.utc)
        cache_data = {
            "data": {"test": "data"},
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }

        with open(test_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_cache_with_expiry(str(test_file))
        assert result == {"test": "data"}

    def test_read_cache_with_expiry_expired(self, tmp_path):
        """Test reading expired cache."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "cache.json"

        # Create expired cache data
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        cache_data = {
            "data": {"test": "data"},
            "cached_at": past.isoformat(),
            "expires_at": (past + timedelta(hours=1)).isoformat(),
        }

        with open(test_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_cache_with_expiry(str(test_file))
        assert result is None

    def test_read_cache_with_expiry_malformed(self, tmp_path):
        """Test reading cache with malformed expiry (treated as non-expiring)."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "cache.json"

        # Create cache data without expires_at (treated as non-expiring)
        cache_data = {"data": {"test": "data"}, "cached_at": "2023-01-01T00:00:00Z"}

        with open(test_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_cache_with_expiry(str(test_file))
        assert result == {"test": "data"}


class TestCacheManagement:
    """Test cache management operations."""

    def test_clear_cache_success(self, tmp_path):
        """Test successful cache clearing."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "cache.json"

        # Create a cache file
        with open(test_file, "w") as f:
            json.dump({"test": "data"}, f)

        assert test_file.exists()

        result = cache_manager.clear_cache(str(test_file))
        assert result is True
        assert not test_file.exists()

    def test_clear_cache_not_exists(self):
        """Test clearing non-existent cache."""
        cache_manager = CacheManager()
        result = cache_manager.clear_cache("/non/existent/cache.json")
        assert result is True

    def test_get_cache_file_path(self, tmp_path):
        """Test getting cache file path."""
        cache_manager = CacheManager(str(tmp_path))
        result = cache_manager.get_cache_file_path("test", ".json")
        expected = os.path.join(str(tmp_path), "test.json")
        assert result == expected

    def test_clear_all_caches_success(self, tmp_path):
        """Test clearing all caches successfully."""
        cache_manager = CacheManager(str(tmp_path))

        # Create some cache files
        cache_files = ["cache1.json", "cache2.tmp", "not_cache.txt"]
        for filename in cache_files:
            file_path = tmp_path / filename
            with open(file_path, "w") as f:
                f.write("content")

        result = cache_manager.clear_all_caches()
        assert result is True

        # Check that cache files were removed but non-cache files remain
        assert not (tmp_path / "cache1.json").exists()
        assert not (tmp_path / "cache2.tmp").exists()
        assert (tmp_path / "not_cache.txt").exists()


class TestURLCacheKey:
    """Test URL cache key building."""

    def test_build_url_cache_key_no_params(self):
        """Test building cache key without parameters."""
        result = CacheManager.build_url_cache_key(
            "https://api.github.com/repos/owner/repo/releases"
        )
        assert result == "https://api.github.com/repos/owner/repo/releases"

    def test_build_url_cache_key_with_params(self):
        """Test building cache key with parameters (excludes page param only)."""
        params = {"per_page": 100, "page": 1}
        result = CacheManager.build_url_cache_key(
            "https://api.github.com/repos/owner/repo/releases", params
        )
        assert result == "https://api.github.com/repos/owner/repo/releases?per_page=100"

    def test_build_url_cache_key_different_per_page(self):
        """Test that different per_page values generate different cache keys."""
        url = "https://api.github.com/repos/owner/repo/releases"
        key1 = CacheManager.build_url_cache_key(url, {"per_page": 10})
        key2 = CacheManager.build_url_cache_key(url, {"per_page": 20})
        assert key1 == f"{url}?per_page=10"
        assert key2 == f"{url}?per_page=20"
        assert key1 != key2, "Different per_page should generate different keys"

    def test_build_url_cache_key_none_params(self):
        """Test building cache key with None values filtered and per_page retained."""
        params = {"per_page": 100, "token": None}
        result = CacheManager.build_url_cache_key(
            "https://api.github.com/repos/owner/repo/releases", params
        )
        assert result == "https://api.github.com/repos/owner/repo/releases?per_page=100"


class TestReleasesCache:
    """Test GitHub releases caching."""

    def test_read_releases_cache_entry_valid(self, tmp_path):
        """Test reading valid releases cache entry."""
        cache_manager = CacheManager(str(tmp_path))

        # Create cache file
        cache_file = cache_manager._get_releases_cache_file()
        now = datetime.now(timezone.utc)
        cache_data = {
            "releases_identifier": {
                "releases": [{"tag_name": "v1.0.0"}],
                "cached_at": now.isoformat(),
            }
        }

        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_releases_cache_entry(
            "releases_identifier", expiry_seconds=3600
        )
        assert result == [{"tag_name": "v1.0.0"}]

    def test_read_releases_cache_entry_expired(self, tmp_path):
        """Test reading expired releases cache entry."""
        cache_manager = CacheManager(str(tmp_path))

        # Create cache file with old timestamp
        cache_file = cache_manager._get_releases_cache_file()
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        cache_data = {
            "releases_identifier": {
                "releases": [{"tag_name": "v1.0.0"}],
                "cached_at": past.isoformat(),
            }
        }

        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_releases_cache_entry(
            "releases_identifier", expiry_seconds=3600
        )
        assert result is None

    def test_write_releases_cache_entry(self, tmp_path):
        """Test writing releases cache entry."""
        cache_manager = CacheManager(str(tmp_path))
        releases = [{"tag_name": "v1.0.0"}]

        cache_manager.write_releases_cache_entry("releases_identifier", releases)

        # Verify cache file was created
        cache_file = cache_manager._get_releases_cache_file()
        assert os.path.exists(cache_file)

        with open(cache_file, "r") as f:
            cache_data = json.load(f)

        assert "releases_identifier" in cache_data
        assert cache_data["releases_identifier"]["releases"] == releases
        assert "cached_at" in cache_data["releases_identifier"]


class TestCommitTimestampCache:
    """Test commit timestamp caching."""

    @patch("fetchtastic.download.cache.make_github_api_request")
    def test_get_commit_timestamp_success(self, mock_request, tmp_path):
        """Test successful commit timestamp retrieval."""
        cache_manager = CacheManager(str(tmp_path))

        # Mock API response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "commit": {"committer": {"date": "2023-01-01T12:00:00Z"}}
        }
        mock_request.return_value = mock_response

        result = cache_manager.get_commit_timestamp("owner", "repo", "abc123")

        assert result is not None
        expected_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert result == expected_time

    @patch("fetchtastic.download.cache.make_github_api_request")
    def test_get_commit_timestamp_cached(self, mock_request, tmp_path):
        """Test retrieving cached commit timestamp."""
        cache_manager = CacheManager(str(tmp_path))

        # First create a cache entry
        cache_file = os.path.join(cache_manager.cache_dir, "commit_timestamps.json")
        now = datetime.now(timezone.utc)
        cache_data = {"owner/repo/abc123": ["2023-01-01T12:00:00Z", now.isoformat()]}

        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        # Should return cached value without making API call
        result = cache_manager.get_commit_timestamp("owner", "repo", "abc123")
        mock_request.assert_not_called()

        expected_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert result == expected_time

    def test_read_commit_timestamp_cache(self, tmp_path):
        """Test reading commit timestamp cache."""
        cache_manager = CacheManager(str(tmp_path))

        # Create cache file
        cache_file = os.path.join(cache_manager.cache_dir, "commit_timestamps.json")
        now = datetime.now(timezone.utc)
        cache_data = {
            "key1": ["2023-01-01T12:00:00Z", now.isoformat()],
            "expired": [
                "2023-01-01T12:00:00Z",
                (now - timedelta(hours=25)).isoformat(),
            ],
        }

        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        result = cache_manager.read_commit_timestamp_cache()

        # Should contain key1 but not expired
        assert "key1" in result
        assert "expired" not in result


class TestRepositoryDirectories:
    """Test repository directory caching."""

    @patch("fetchtastic.download.cache.make_github_api_request")
    def test_get_repo_directories_success(self, mock_request, tmp_path):
        """Test successful repository directory retrieval."""
        cache_manager = CacheManager(str(tmp_path))

        # Mock API response
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"name": "dir1", "type": "dir"},
            {"name": "file1", "type": "file"},
            {"name": "dir2", "type": "dir"},
        ]
        mock_request.return_value = mock_response

        result = cache_manager.get_repo_directories("test/path")

        assert result == ["dir1", "dir2"]

    @patch("fetchtastic.download.cache.make_github_api_request")
    def test_get_repo_directories_cached(self, mock_request, tmp_path):
        """Test retrieving cached repository directories."""
        cache_manager = CacheManager(str(tmp_path))

        # Create cache entry
        cache_file = os.path.join(cache_manager.cache_dir, "prerelease_dirs.json")
        now = datetime.now(timezone.utc)
        cache_data = {
            "repo:test/path": {
                "directories": ["cached_dir1", "cached_dir2"],
                "cached_at": now.isoformat(),
            }
        }

        with open(cache_file, "w") as f:
            json.dump(cache_data, f)

        # Should return cached value
        result = cache_manager.get_repo_directories("test/path")
        mock_request.assert_not_called()
        assert result == ["cached_dir1", "cached_dir2"]


class TestMigration:
    """Test cache migration functionality."""

    def test_migrate_legacy_cache_file_success(self, tmp_path):
        """Test successful legacy cache migration."""
        cache_manager = CacheManager(str(tmp_path))

        # Create legacy file
        legacy_file = tmp_path / "legacy.json"
        legacy_data = {"old_key": "value", "shared_key": "legacy"}

        with open(legacy_file, "w") as f:
            json.dump(legacy_data, f)

        # Create new file with some data (this will be overwritten)
        new_file = tmp_path / "new.json"
        new_data = {"new_key": "new_value", "shared_key": "new"}

        with open(new_file, "w") as f:
            json.dump(new_data, f)

        result = cache_manager.migrate_legacy_cache_file(
            str(legacy_file), str(new_file), {"old_key": "migrated_key"}
        )

        assert result is True

        # Check migrated content (new file is completely replaced)
        with open(new_file, "r") as f:
            migrated_data = json.load(f)

        assert migrated_data["old_key"] == "value"  # From legacy
        assert migrated_data["shared_key"] == "legacy"  # From legacy
        assert "new_key" not in migrated_data  # Overwritten
        assert "last_updated" in migrated_data  # Timestamp added

    def test_migrate_legacy_cache_file_no_legacy(self, tmp_path):
        """Test migration when legacy file doesn't exist."""
        cache_manager = CacheManager(str(tmp_path))

        new_file = tmp_path / "new.json"
        new_data = {"key": "value"}

        with open(new_file, "w") as f:
            json.dump(new_data, f)

        result = cache_manager.migrate_legacy_cache_file(
            str(tmp_path / "nonexistent.json"), str(new_file), {}
        )

        assert result is False


class TestValidation:
    """Test cache validation functionality."""

    def test_validate_cache_format_valid(self):
        """Test validating valid cache format."""
        cache_manager = CacheManager()
        data = {"key1": "value1", "key2": "value2"}
        result = cache_manager.validate_cache_format(data, ["key1"])
        assert result is True

    def test_validate_cache_format_invalid(self):
        """Test validating invalid cache format."""
        cache_manager = CacheManager()
        data = {"key1": "value1"}
        result = cache_manager.validate_cache_format(data, ["key1", "missing_key"])
        assert result is False

    def test_get_cache_expiry_timestamp(self):
        """Test getting cache expiry timestamp."""
        cache_manager = CacheManager()
        before = datetime.now(timezone.utc) + timedelta(hours=2)
        result = cache_manager.get_cache_expiry_timestamp(2.0)

        after = datetime.now(timezone.utc) + timedelta(hours=2)

        # Should be a valid ISO timestamp ~2 hours in the future
        result_dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert before <= result_dt <= after

    def test_atomic_write_with_timestamp(self, tmp_path):
        """Test atomic write with timestamp."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "timestamped.json"
        data = {"test": "data"}

        result = cache_manager.atomic_write_with_timestamp(str(test_file), data)
        assert result is True
        assert test_file.exists()

        # Verify timestamp was added
        with open(test_file, "r") as f:
            saved_data = json.load(f)

        assert "last_updated" in saved_data
        assert saved_data["test"] == "data"

    def test_read_with_expiry_valid(self, tmp_path):
        """Test reading data with valid expiry."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "expiry_test.json"

        now = datetime.now(timezone.utc)
        data = {"test": "data", "last_updated": now.isoformat()}

        with open(test_file, "w") as f:
            json.dump(data, f)

        result = cache_manager.read_with_expiry(str(test_file), 1.0)
        assert result == data

    def test_read_with_expiry_expired(self, tmp_path):
        """Test reading expired data."""
        cache_manager = CacheManager(str(tmp_path))
        test_file = tmp_path / "expiry_test.json"

        past = datetime.now(timezone.utc) - timedelta(hours=2)
        data = {"test": "data", "last_updated": past.isoformat()}

        with open(test_file, "w") as f:
            json.dump(data, f)

        result = cache_manager.read_with_expiry(str(test_file), 1.0)
        assert result is None
