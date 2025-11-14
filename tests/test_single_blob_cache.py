"""
Tests for the new generic caching functions in downloader.py.

These tests ensure the new single-blob caching helpers work correctly
and handle all edge cases and error conditions.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from src.fetchtastic.downloader import (
    _load_single_blob_cache_with_expiry,
    _save_single_blob_cache,
)


class TestSingleBlobCache:
    """Test suite for single-blob caching functions."""

    def test_load_single_blob_cache_with_expiry_hit(self, tmp_path):
        """Test successful cache hit with valid data."""
        cache_file = tmp_path / "test_cache.json"
        test_data = {"key": "value", "items": [1, 2, 3]}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "data": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,  # 5 minutes
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result == test_data
        hit_callback.assert_called_once()
        miss_callback.assert_not_called()

    def test_load_single_blob_cache_with_expiry_expired(self, tmp_path):
        """Test cache miss due to expired data."""
        cache_file = tmp_path / "test_cache.json"
        test_data = {"key": "value"}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "data": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,  # 5 minutes
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_force_refresh(self, tmp_path):
        """Test force refresh bypasses cache."""
        cache_file = tmp_path / "test_cache.json"
        test_data = {"key": "value"}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "data": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            force_refresh=True,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()
        assert not cache_file.exists()  # File should be deleted

    def test_load_single_blob_cache_with_expiry_no_file(self, tmp_path):
        """Test cache miss when file doesn't exist."""
        cache_file = tmp_path / "nonexistent_cache.json"

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_invalid_json(self, tmp_path):
        """Test cache miss with invalid JSON."""
        cache_file = tmp_path / "invalid_cache.json"
        cache_file.write_text("invalid json content")

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_invalid_structure(self, tmp_path):
        """Test cache miss with invalid cache structure (not a dict)."""
        cache_file = tmp_path / "invalid_structure_cache.json"
        cache_file.write_text(json.dumps(["not", "a", "dict"]))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_missing_timestamp(self, tmp_path):
        """Test cache miss when cached_at timestamp is missing."""
        cache_file = tmp_path / "no_timestamp_cache.json"
        cache_content = {
            "data": {"key": "value"},
            # Missing "cached_at" key
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_invalid_timestamp(self, tmp_path):
        """Test cache miss with invalid timestamp format."""
        cache_file = tmp_path / "invalid_timestamp_cache.json"
        cache_content = {
            "cached_at": "not-a-valid-timestamp",
            "data": {"key": "value"},
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_hit_callback=hit_callback,
            cache_miss_callback=miss_callback,
            cache_name="test",
            data_key="data",
        )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()

    def test_load_single_blob_cache_with_expiry_custom_data_key(self, tmp_path):
        """Test loading cache with custom data key."""
        cache_file = tmp_path / "custom_key_cache.json"
        test_data = {"custom": "data"}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "custom": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_name="test",
            data_key="custom",
        )

        assert result == test_data

    def test_load_single_blob_cache_with_expiry_no_callbacks(self, tmp_path):
        """Test loading cache without callbacks."""
        cache_file = tmp_path / "no_callbacks_cache.json"
        test_data = {"key": "value"}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "data": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        # Should not raise any exceptions without callbacks
        result = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_name="test",
            data_key="data",
        )

        assert result == test_data

    def test_load_single_blob_cache_with_expiry_force_refresh_file_error(
        self, tmp_path
    ):
        """Test force refresh when file deletion fails."""
        cache_file = tmp_path / "test_cache.json"
        test_data = {"key": "value"}
        cached_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        cache_content = {
            "cached_at": cached_at,
            "data": test_data,
        }

        cache_file.write_text(json.dumps(cache_content))

        hit_callback = Mock()
        miss_callback = Mock()

        # Mock os.remove to raise OSError
        with patch("os.remove", side_effect=OSError("Permission denied")):
            result = _load_single_blob_cache_with_expiry(
                cache_file_path=str(cache_file),
                expiry_seconds=300,
                force_refresh=True,
                cache_hit_callback=hit_callback,
                cache_miss_callback=miss_callback,
                cache_name="test",
                data_key="data",
            )

        assert result is None
        hit_callback.assert_not_called()
        miss_callback.assert_called_once()
        # File should still exist since deletion failed
        assert cache_file.exists()

    def test_save_single_blob_cache_success(self, tmp_path):
        """Test successful cache saving."""
        cache_file = tmp_path / "save_test_cache.json"
        test_data = {"key": "value", "items": [1, 2, 3]}

        result = _save_single_blob_cache(
            cache_file_path=str(cache_file),
            data=test_data,
            cache_name="test",
            data_key="data",
        )

        assert result is True
        assert cache_file.exists()

        # Verify cache content
        cache_content = json.loads(cache_file.read_text())
        assert "cached_at" in cache_content
        assert cache_content["data"] == test_data

        # Verify timestamp is recent
        cached_at = datetime.fromisoformat(cache_content["cached_at"])
        now = datetime.now(timezone.utc)
        assert abs((now - cached_at).total_seconds()) < 5  # Within 5 seconds

    def test_save_single_blob_cache_custom_data_key(self, tmp_path):
        """Test saving cache with custom data key."""
        cache_file = tmp_path / "custom_key_save_cache.json"
        test_data = {"custom": "data"}

        result = _save_single_blob_cache(
            cache_file_path=str(cache_file),
            data=test_data,
            cache_name="test",
            data_key="custom",
        )

        assert result is True
        cache_content = json.loads(cache_file.read_text())
        assert cache_content["custom"] == test_data
        assert "data" not in cache_content

    def test_save_single_blob_cache_atomic_write_failure(self, tmp_path):
        """Test cache saving when atomic write fails."""
        cache_file = tmp_path / "failed_save_cache.json"
        test_data = {"key": "value"}

        with patch("src.fetchtastic.downloader._atomic_write_json", return_value=False):
            result = _save_single_blob_cache(
                cache_file_path=str(cache_file),
                data=test_data,
                cache_name="test",
                data_key="data",
            )

        assert result is False

    def test_save_single_blob_cache_exception(self, tmp_path):
        """Test cache saving when an exception occurs."""
        cache_file = tmp_path / "exception_save_cache.json"
        test_data = {"key": "value"}

        # Mock _atomic_write_json to raise OSError
        with patch(
            "src.fetchtastic.downloader._atomic_write_json",
            side_effect=OSError("Disk full"),
        ):
            result = _save_single_blob_cache(
                cache_file_path=str(cache_file),
                data=test_data,
                cache_name="test",
                data_key="data",
            )

        assert result is False

    def test_save_single_blob_cache_no_callbacks_needed(self, tmp_path):
        """Test cache saving without any special parameters."""
        cache_file = tmp_path / "simple_save_cache.json"
        test_data = {"key": "value"}

        # Should work with default parameters
        result = _save_single_blob_cache(
            cache_file_path=str(cache_file),
            data=test_data,
        )

        assert result is True
        cache_content = json.loads(cache_file.read_text())
        assert cache_content["data"] == test_data

    def test_save_single_blob_cache_large_data(self, tmp_path):
        """Test saving cache with large data."""
        cache_file = tmp_path / "large_data_cache.json"
        # Create a large data structure
        test_data = {"items": list(range(1000)), "nested": {"data": "x" * 1000}}

        result = _save_single_blob_cache(
            cache_file_path=str(cache_file),
            data=test_data,
            cache_name="test",
            data_key="data",
        )

        assert result is True
        cache_content = json.loads(cache_file.read_text())
        assert cache_content["data"] == test_data

    def test_integration_load_save_cycle(self, tmp_path):
        """Test integration between save and load functions."""
        cache_file = tmp_path / "cycle_test_cache.json"
        original_data = {
            "version": "1.2.3",
            "files": ["file1.bin", "file2.bin"],
            "metadata": {"author": "test", "created": "2024-01-01"},
        }

        # Save data
        save_result = _save_single_blob_cache(
            cache_file_path=str(cache_file),
            data=original_data,
            cache_name="integration_test",
            data_key="payload",
        )
        assert save_result is True

        # Load data back
        loaded_data = _load_single_blob_cache_with_expiry(
            cache_file_path=str(cache_file),
            expiry_seconds=300,
            cache_name="integration_test",
            data_key="payload",
        )

        assert loaded_data == original_data
