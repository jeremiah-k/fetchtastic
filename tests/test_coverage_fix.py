"""
Direct tests for coverage of specific lines.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def populated_releases_cache():
    """Fixture to populate releases cache for testing."""
    from datetime import datetime, timezone

    import fetchtastic.downloader as downloader_module

    # Save original state
    original_cache = downloader_module._releases_cache.copy()
    original_loaded = downloader_module._releases_cache_loaded

    # Set up test state
    downloader_module._releases_cache_loaded = True
    cache_key = "https://api.github.com/repos/meshtastic/firmware/releases?per_page=5"
    test_data = [{"tag_name": "v2.7.8"}]
    downloader_module._releases_cache[cache_key] = (
        test_data,
        datetime.now(timezone.utc),
    )

    yield test_data, cache_key

    # Restore original state
    downloader_module._releases_cache = original_cache
    downloader_module._releases_cache_loaded = original_loaded


def test_token_warning_lines_coverage():
    """Direct test to cover token warning lines in main function."""
    with patch("fetchtastic.downloader._initial_setup_and_config") as mock_setup, patch(
        "fetchtastic.downloader._check_wifi_connection"
    ) as _, patch(
        "fetchtastic.downloader._process_firmware_downloads"
    ) as mock_firmware, patch(
        "fetchtastic.downloader._process_apk_downloads"
    ) as mock_apk, patch(
        "fetchtastic.downloader._finalize_and_notify"
    ) as _:

        # Mock setup to return valid config
        mock_setup.return_value = (
            {"GITHUB_TOKEN": None},  # config
            "v0.8.0",  # current_version
            "v0.8.0",  # latest_version
            False,  # update_available
            {
                "firmware_releases_url": "https://api.github.com/repos/meshtastic/firmware/releases"
            },
        )

        # Mock download processing to return empty results
        mock_firmware.return_value = ([], [], [], None, None)
        mock_apk.return_value = ([], [], [], None, None)

        # Import and call main function directly
        from fetchtastic.downloader import main

        # This should execute token warning behavior
        main(force_refresh=False)


def test_cache_logging_lines_coverage(populated_releases_cache):
    """Direct test to cover cache logging lines."""
    from datetime import datetime, timezone

    import fetchtastic.downloader as downloader_module

    test_data, _ = populated_releases_cache

    with patch("fetchtastic.downloader.make_github_api_request") as _:
        from fetchtastic.downloader import _get_latest_releases_data

        # This call should hit cache logging lines
        result = _get_latest_releases_data(
            "https://api.github.com/repos/meshtastic/firmware/releases",
            5,
            None,
            True,
            force_refresh=False,  # Important: not force refresh
            release_type="firmware",  # Test new release_type parameter
        )

        # Verify call completed successfully and returned cached data
        assert result == test_data

        # Test Android URL cache logging too
        android_cache_key = (
            "https://api.github.com/repos/meshtastic/android/releases?per_page=5"
        )
        downloader_module._releases_cache[android_cache_key] = (
            test_data,
            datetime.now(timezone.utc),
        )

        result2 = _get_latest_releases_data(
            "https://api.github.com/repos/meshtastic/android/releases",
            5,
            None,
            True,
            force_refresh=False,
            release_type="Android APK",  # Test new release_type parameter
        )

        assert result2 == test_data

        # Test fallback URL parsing when release_type is None
        result3 = _get_latest_releases_data(
            "https://api.github.com/repos/meshtastic/firmware/releases",
            5,
            None,
            True,
            force_refresh=False,
            release_type=None,  # Test fallback logic
        )
        assert result3 == test_data

        # Test fallback URL parsing for generic URL (no firmware/android)
        generic_cache_key = "https://api.github.com/repos/meshtastic/some-other-repo/releases?per_page=5"
        downloader_module._releases_cache[generic_cache_key] = (
            test_data,
            datetime.now(timezone.utc),
        )

        result4 = _get_latest_releases_data(
            "https://api.github.com/repos/meshtastic/some-other-repo/releases",
            5,
            None,
            True,
            force_refresh=False,
            release_type=None,  # Test fallback logic for generic case
        )
        assert result4 == test_data


def test_api_fetch_logging_lines_coverage():
    """Test to cover API fetch logging lines."""
    import fetchtastic.downloader as downloader_module

    # Store original state to prevent test pollution
    original_cache = getattr(downloader_module, "_releases_cache", {}).copy()
    original_cache_loaded = getattr(downloader_module, "_releases_cache_loaded", False)

    try:
        # Reset cache to ensure API fetch path
        downloader_module._releases_cache = {}
        downloader_module._releases_cache_loaded = True

        with patch("fetchtastic.downloader.make_github_api_request") as mock_request:
            # Mock successful API response
            mock_response = MagicMock()
            mock_response.json.return_value = [{"tag_name": "v2.7.8"}]
            mock_request.return_value = mock_response

            from fetchtastic.downloader import _get_latest_releases_data

            # This call should go through API fetch path and hit logging lines
            result = _get_latest_releases_data(
                "https://api.github.com/repos/meshtastic/firmware/releases",
                5,
                None,
                True,
                force_refresh=True,  # Force API fetch
            )

            # Verify call completed successfully
            assert result == [{"tag_name": "v2.7.8"}]

            # Test Android URL logging too
            result2 = _get_latest_releases_data(
                "https://api.github.com/repos/meshtastic/android/releases",
                5,
                None,
                True,
                force_refresh=True,
            )

            assert result2 == [{"tag_name": "v2.7.8"}]
    finally:
        # Restore original state to prevent test pollution
        downloader_module._releases_cache = original_cache
        downloader_module._releases_cache_loaded = original_cache_loaded


def test_main_function_full_coverage():
    """Test to cover remaining main function lines."""
    with patch("fetchtastic.downloader._initial_setup_and_config") as mock_setup, patch(
        "fetchtastic.downloader._check_wifi_connection"
    ) as _, patch(
        "fetchtastic.downloader._process_firmware_downloads"
    ) as mock_firmware, patch(
        "fetchtastic.downloader._process_apk_downloads"
    ) as mock_apk, patch(
        "fetchtastic.downloader._finalize_and_notify"
    ) as _, patch(
        "fetchtastic.downloader.clear_all_caches"
    ) as mock_clear, patch(
        "fetchtastic.downloader.DeviceHardwareManager"
    ) as mock_device_mgr:

        # Mock setup to return valid config
        mock_setup.return_value = (
            {"GITHUB_TOKEN": "fake_token"},  # config with token
            "v0.8.0",  # current_version
            "v0.8.0",  # latest_version
            False,  # update_available
            {
                "firmware_releases_url": "https://api.github.com/repos/meshtastic/firmware/releases"
            },
        )

        # Mock download processing to return empty results
        mock_firmware.return_value = ([], [], [], None, None)
        mock_apk.return_value = ([], [], [], None, None)

        from fetchtastic.downloader import main

        # Test with force_refresh=True to hit cache clearing lines
        main(force_refresh=True)

        # Verify cache clearing was called
        mock_clear.assert_called_once()

        # Verify device manager was instantiated and cache cleared
        mock_device_mgr.assert_called()
        mock_device_mgr.return_value.clear_cache.assert_called_once()
