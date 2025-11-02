"""
Direct tests for coverage of specific lines.
"""

from unittest.mock import MagicMock, patch

# Import to ensure coverage tracking


def test_token_warning_lines_coverage():
    """Direct test to cover token warning lines in main function."""
    with patch("fetchtastic.downloader._initial_setup_and_config") as mock_setup:
        with patch("fetchtastic.downloader._check_wifi_connection") as _:
            with patch(
                "fetchtastic.downloader._process_firmware_downloads"
            ) as mock_firmware:
                with patch("fetchtastic.downloader._process_apk_downloads") as mock_apk:
                    with patch("fetchtastic.downloader._finalize_and_notify") as _:

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
                        mock_firmware.return_value = ([], [], [], None)
                        mock_apk.return_value = ([], [], [], None)

                        # Import and call main function directly
                        from fetchtastic.downloader import main

                        # This should execute lines 3827-3828
                        main(force_refresh=False)


def test_cache_logging_lines_coverage():
    """Direct test to cover cache logging lines."""
    # Import module to access its globals
    from datetime import datetime, timezone

    import fetchtastic.downloader as downloader_module

    # Reset cache state and populate with fresh data
    downloader_module._releases_cache = {}
    downloader_module._releases_cache_loaded = True

    # Manually populate cache to simulate previous successful fetch
    cache_key = "https://api.github.com/repos/meshtastic/firmware/releases?per_page=5"
    test_data = [{"tag_name": "v2.7.8"}]
    downloader_module._releases_cache[cache_key] = (
        test_data,
        datetime.now(timezone.utc),
    )

    with patch("fetchtastic.downloader.make_github_api_request") as _:
        from fetchtastic.downloader import _get_latest_releases_data

        # This call should hit cache logging lines (2267-2276)
        result = _get_latest_releases_data(
            "https://api.github.com/repos/meshtastic/firmware/releases",
            5,
            None,
            True,
            force_refresh=False,  # Important: not force refresh
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
        )

        assert result2 == test_data


def test_api_fetch_logging_lines_coverage():
    """Test to cover API fetch logging lines (2283-2364)."""
    import fetchtastic.downloader as downloader_module

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


def test_main_function_full_coverage():
    """Test to cover remaining main function lines."""
    with patch("fetchtastic.downloader._initial_setup_and_config") as mock_setup:
        with patch("fetchtastic.downloader._check_wifi_connection") as _:
            with patch(
                "fetchtastic.downloader._process_firmware_downloads"
            ) as mock_firmware:
                with patch("fetchtastic.downloader._process_apk_downloads") as mock_apk:
                    with patch("fetchtastic.downloader._finalize_and_notify") as _:
                        with patch(
                            "fetchtastic.downloader.clear_all_caches"
                        ) as mock_clear:
                            with patch(
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
                                mock_firmware.return_value = ([], [], [], None)
                                mock_apk.return_value = ([], [], [], None)

                                from fetchtastic.downloader import main

                                # Test with force_refresh=True to hit cache clearing lines
                                main(force_refresh=True)

                                # Verify cache clearing was called
                                mock_clear.assert_called_once()

                                # Verify device manager was instantiated and cache cleared
                                mock_device_mgr.assert_called()
                                mock_device_mgr.return_value.clear_cache.assert_called_once()
