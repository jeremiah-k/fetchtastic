"""
Tests for WIFI_ONLY configuration option.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit]


@pytest.fixture
def mock_config():
    """Create a basic mock configuration."""
    return {
        "DOWNLOAD_DIR": "/tmp/test",
        "SAVE_FIRMWARE": True,
        "SAVE_APKS": True,
    }


@pytest.fixture
def orchestrator(mock_config):
    """Create a DownloadOrchestrator instance for testing."""
    return DownloadOrchestrator(mock_config)


def test_wifi_only_skips_downloads_when_not_connected_to_wifi(
    orchestrator, mock_config
):
    """Test that WIFI_ONLY=True skips downloads when not connected to Wi-Fi."""
    mock_config["WIFI_ONLY"] = True

    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = True

        with patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi"
        ) as mock_wifi:
            mock_wifi.return_value = False

            with patch(
                "fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"
            ):
                successful_results, failed_results = (
                    orchestrator.run_download_pipeline()
                )

                assert successful_results == []
                assert failed_results == []


def test_wifi_only_allows_downloads_when_connected_to_wifi(orchestrator, mock_config):
    """Test that WIFI_ONLY=True allows downloads when connected to Wi-Fi."""
    mock_config["WIFI_ONLY"] = True

    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = True

        with patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi"
        ) as mock_wifi:
            mock_wifi.return_value = True

            with patch(
                "fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"
            ):
                with patch.object(orchestrator, "_process_firmware_downloads"):
                    with patch.object(orchestrator, "_process_android_downloads"):
                        with patch.object(orchestrator, "_retry_failed_downloads"):
                            with patch.object(orchestrator, "_log_download_summary"):
                                orchestrator.run_download_pipeline()


def test_wifi_only_false_does_not_check_wifi(orchestrator, mock_config):
    """Test that WIFI_ONLY=False does not check Wi-Fi connection."""
    mock_config["WIFI_ONLY"] = False

    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = True

        with patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi"
        ) as mock_wifi:
            with patch(
                "fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"
            ):
                with patch.object(orchestrator, "_process_firmware_downloads"):
                    with patch.object(orchestrator, "_process_android_downloads"):
                        with patch.object(orchestrator, "_retry_failed_downloads"):
                            with patch.object(orchestrator, "_log_download_summary"):
                                orchestrator.run_download_pipeline()

                                mock_wifi.assert_not_called()


def test_wifi_only_default_false(orchestrator, mock_config):
    """Test that WIFI_ONLY defaults to False when not specified."""
    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = True

        with patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi"
        ) as mock_wifi:
            with patch(
                "fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"
            ):
                with patch.object(orchestrator, "_process_firmware_downloads"):
                    with patch.object(orchestrator, "_process_android_downloads"):
                        with patch.object(orchestrator, "_retry_failed_downloads"):
                            with patch.object(orchestrator, "_log_download_summary"):
                                orchestrator.run_download_pipeline()

                                mock_wifi.assert_not_called()


def test_wifi_only_non_termux_always_allows_downloads(orchestrator, mock_config):
    """Test that non-Termux platforms never check Wi-Fi connection."""
    mock_config["WIFI_ONLY"] = True

    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = False

        with patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi"
        ) as mock_wifi:
            with patch(
                "fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"
            ):
                with patch.object(orchestrator, "_process_firmware_downloads"):
                    with patch.object(orchestrator, "_process_android_downloads"):
                        with patch.object(orchestrator, "_retry_failed_downloads"):
                            with patch.object(orchestrator, "_log_download_summary"):
                                orchestrator.run_download_pipeline()

                                mock_wifi.assert_not_called()


def test_is_connected_to_wifi_termux_connected():
    """Test is_connected_to_wifi returns True when Termux has Wi-Fi."""
    wifi_data = {"supplicant_state": "COMPLETED", "ip": "192.168.1.100"}

    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = True

        with patch("fetchtastic.download.orchestrator.subprocess.run") as mock_run:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock_process.stdout = json.dumps(wifi_data)
            mock_process.stderr = ""
            mock_run.return_value = mock_process

            from fetchtastic.download.orchestrator import is_connected_to_wifi

            result = is_connected_to_wifi()
            assert result is True


def test_is_connected_to_wifi_termux_not_connected(mocker):
    """Test is_connected_to_wifi returns False when Termux has no Wi-Fi."""
    wifi_data = {"supplicant_state": "DISCONNECTED", "ip": ""}

    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mock_run = mocker.patch("fetchtastic.download.orchestrator.subprocess.run")
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = json.dumps(wifi_data)
    mock_process.stderr = ""
    mock_run.return_value = mock_process

    from fetchtastic.download.orchestrator import is_connected_to_wifi

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_termux_incomplete(mocker):
    """Test is_connected_to_wifi returns False when Termux Wi-Fi is incomplete."""
    wifi_data = {"supplicant_state": "SCANNING", "ip": ""}

    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mock_run = mocker.patch("fetchtastic.download.orchestrator.subprocess.run")
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = json.dumps(wifi_data)
    mock_process.stderr = ""
    mock_run.return_value = mock_process

    from fetchtastic.download.orchestrator import is_connected_to_wifi

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_non_termux():
    """Test is_connected_to_wifi returns True for non-Termux platforms."""
    with patch("fetchtastic.setup_config.is_termux") as mock_is_termux:
        mock_is_termux.return_value = False

        from fetchtastic.download.orchestrator import is_connected_to_wifi

        result = is_connected_to_wifi()
        assert result is True


def test_is_connected_to_wifi_json_decode_error(mocker):
    """Test is_connected_to_wifi handles JSON decode errors gracefully."""
    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mock_run = mocker.patch("fetchtastic.download.orchestrator.subprocess.run")
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "invalid json"
    mock_process.stderr = ""
    mock_run.return_value = mock_process

    from fetchtastic.download.orchestrator import is_connected_to_wifi

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_os_error(mocker):
    """Test is_connected_to_wifi handles OSError gracefully."""
    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mock_run = mocker.patch("fetchtastic.download.orchestrator.subprocess.run")
    mock_run.side_effect = FileNotFoundError("Command not found")

    from fetchtastic.download.orchestrator import is_connected_to_wifi

    result = is_connected_to_wifi()
    assert result is False
