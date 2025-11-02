"""
API tracking functionality tests for the fetchtastic utils module.

This module contains tests for:
- API request counting and tracking
- Cache hit/miss statistics
- Authentication usage tracking
- API summary generation
- Thread safety of tracking operations
"""

import threading
from unittest.mock import patch

import pytest

from fetchtastic import utils


@pytest.mark.infrastructure
@pytest.mark.unit
class TestAPITracking:
    """Test API request tracking functionality."""

    def setup_method(self):
        """Reset API tracking before each test."""
        utils.reset_api_tracking()

    def test_reset_api_tracking(self):
        """Test that API tracking can be reset to initial state."""
        # Add some tracking data
        utils.track_api_cache_hit()
        utils.track_api_cache_miss()

        # Reset and verify
        utils.reset_api_tracking()
        summary = utils.get_api_request_summary()

        assert summary["total_requests"] == 0
        assert summary["cache_hits"] == 0
        assert summary["cache_misses"] == 0
        assert summary["auth_used"] is False

    def test_track_api_cache_hit(self):
        """Test tracking cache hits."""
        utils.track_api_cache_hit()
        utils.track_api_cache_hit()

        summary = utils.get_api_request_summary()
        assert summary["cache_hits"] == 2

    def test_track_api_cache_miss(self):
        """Test tracking cache misses."""
        utils.track_api_cache_miss()
        utils.track_api_cache_miss()
        utils.track_api_cache_miss()

        summary = utils.get_api_request_summary()
        assert summary["cache_misses"] == 3

    def test_get_api_request_summary_structure(self):
        """Test that API request summary has correct structure."""
        utils.track_api_cache_hit()

        summary = utils.get_api_request_summary()

        required_keys = ["total_requests", "cache_hits", "cache_misses", "auth_used"]

        for key in required_keys:
            assert key in summary
            assert isinstance(summary[key], (int, bool))

    @pytest.mark.slow
    def test_thread_safety_concurrent_tracking(self):
        """Test thread safety of concurrent API tracking."""
        num_threads = 10
        operations_per_thread = 50

        def track_operations():
            for _ in range(operations_per_thread):
                if _ % 2 == 0:
                    utils.track_api_cache_hit()
                else:
                    utils.track_api_cache_miss()

        threads = []
        for _ in range(num_threads):
            thread = threading.Thread(target=track_operations)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        summary = utils.get_api_request_summary()
        expected_operations = num_threads * operations_per_thread
        assert summary["cache_hits"] + summary["cache_misses"] == expected_operations
