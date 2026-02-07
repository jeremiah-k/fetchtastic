"""
Integration test for cache flow: mismatch detection and refresh.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.constants import GITHUB_RELEASES_CACHE_SCHEMA_VERSION
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader


@pytest.mark.unit
@pytest.mark.core_downloads
def test_cache_schema_mismatch_triggers_refresh(tmp_path):
    """
    Test the full flow when a schema mismatch is detected:
    1. Load old cache (mismatched schema).
    2. Attempt to read (should miss/reject).
    3. Simulate fresh fetch from API.
    4. Write new cache (should have new schema).
    """
    cache_manager = CacheManager(str(tmp_path))
    config = {"DOWNLOAD_DIR": str(tmp_path)}
    downloader = FirmwareReleaseDownloader(config, cache_manager)

    url_key = cache_manager.build_url_cache_key(downloader.firmware_releases_url, {"per_page": 8})
    cache_file = cache_manager._get_releases_cache_file()

    # 1. Pre-populate with mismatched schema
    old_data = {
        url_key: {
            "releases": [{"tag_name": "v1.0.0", "prerelease": False, "published_at": "2023-01-01T00:00:00Z"}],
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "0.1"  # Old version
        }
    }
    with open(cache_file, "w") as f:
        json.dump(old_data, f)

    # 2 & 3. Simulate get_releases() call which should miss cache and fetch from API
    mock_response = MagicMock()
    fresh_releases_json = [
        {
            "tag_name": "v2.0.0",
            "prerelease": False,
            "published_at": "2024-01-01T00:00:00Z",
            "assets": [{"name": "firmware.zip", "browser_download_url": "http://example.com", "size": 100}]
        }
    ]
    mock_response.json.return_value = fresh_releases_json

    with patch("fetchtastic.download.firmware.make_github_api_request", return_value=mock_response) as mock_request:
        releases = downloader.get_releases(limit=8)

        # Verify it went to API
        mock_request.assert_called_once()
        assert len(releases) == 1
        assert releases[0].tag_name == "v2.0.0"

    # 4. Verify new cache has correct schema
    with open(cache_file, "r") as f:
        new_cache = json.load(f)

    assert url_key in new_cache
    assert new_cache[url_key]["schema_version"] == GITHUB_RELEASES_CACHE_SCHEMA_VERSION
    assert new_cache[url_key]["releases"][0]["tag_name"] == "v2.0.0"
