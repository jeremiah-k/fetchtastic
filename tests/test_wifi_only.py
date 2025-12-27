"""
Tests for WIFI_ONLY configuration option.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from fetchtastic.download.orchestrator import (
    DownloadOrchestrator,
    is_connected_to_wifi,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture
def mock_config():
    """
    Provide a minimal test configuration used by the download orchestrator unit tests.

    Returns:
        dict: A configuration dictionary containing keys:
            - "DOWNLOAD_DIR": path to the temporary download directory (str)
            - "SAVE_FIRMWARE": whether to save firmware files (bool)
            - "SAVE_APKS": whether to save APK files (bool)
    """
    return {
        "DOWNLOAD_DIR": "/tmp/test",
        "SAVE_FIRMWARE": True,
        "SAVE_APKS": True,
    }


@pytest.fixture
def orchestrator(mock_config):
    """Create a DownloadOrchestrator instance for testing."""
    return DownloadOrchestrator(mock_config)


@pytest.fixture
def mock_wifi_subprocess(mocker):
    """Fixture to mock the subprocess call for Wi-Fi check."""
    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mock_run = mocker.patch("fetchtastic.download.orchestrator.subprocess.run")

    def _setup_mock(returncode=0, stdout="", stderr=""):
        mock_process = MagicMock()
        mock_process.returncode = returncode
        mock_process.stdout = stdout
        mock_process.stderr = stderr
        mock_run.return_value = mock_process
        return mock_run

    return _setup_mock


def test_wifi_only_skips_downloads_when_not_connected_to_wifi(
    orchestrator, mock_config
):
    """Test that WIFI_ONLY=True skips downloads when not connected to Wi-Fi."""
    mock_config["WIFI_ONLY"] = True

    with (
        patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
        patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi",
            return_value=False,
        ),
        patch("fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"),
    ):
        successful_results, failed_results = orchestrator.run_download_pipeline()

        assert successful_results == []
        assert failed_results == []


def test_wifi_only_allows_downloads_when_connected_to_wifi(orchestrator, mock_config):
    """Test that WIFI_ONLY=True allows downloads when connected to Wi-Fi."""
    mock_config["WIFI_ONLY"] = True

    with (
        patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
        patch(
            "fetchtastic.download.orchestrator.is_connected_to_wifi",
            return_value=True,
        ),
        patch("fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"),
        patch.object(orchestrator, "_process_firmware_downloads"),
        patch.object(orchestrator, "_process_android_downloads"),
        patch.object(orchestrator, "_retry_failed_downloads"),
        patch.object(orchestrator, "_log_download_summary"),
    ):
        orchestrator.run_download_pipeline()


def test_wifi_only_false_does_not_check_wifi(orchestrator, mock_config):
    """Test that WIFI_ONLY=False does not check Wi-Fi connection."""
    mock_config["WIFI_ONLY"] = False

    with (
        patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
        patch("fetchtastic.download.orchestrator.is_connected_to_wifi") as mock_wifi,
        patch("fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"),
        patch.object(orchestrator, "_process_firmware_downloads"),
        patch.object(orchestrator, "_process_android_downloads"),
        patch.object(orchestrator, "_retry_failed_downloads"),
        patch.object(orchestrator, "_log_download_summary"),
    ):
        orchestrator.run_download_pipeline()

        mock_wifi.assert_not_called()


def test_wifi_only_default_false(orchestrator):
    """Test that WIFI_ONLY defaults to False when not specified."""
    with (
        patch("fetchtastic.download.orchestrator.is_termux", return_value=True),
        patch("fetchtastic.download.orchestrator.is_connected_to_wifi") as mock_wifi,
        patch("fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"),
        patch.object(orchestrator, "_process_firmware_downloads"),
        patch.object(orchestrator, "_process_android_downloads"),
        patch.object(orchestrator, "_retry_failed_downloads"),
        patch.object(orchestrator, "_log_download_summary"),
    ):
        orchestrator.run_download_pipeline()

        mock_wifi.assert_not_called()


def test_wifi_only_non_termux_always_allows_downloads(orchestrator, mock_config):
    """Test that non-Termux platforms never check Wi-Fi connection."""
    mock_config["WIFI_ONLY"] = True

    with (
        patch("fetchtastic.download.orchestrator.is_termux", return_value=False),
        patch("fetchtastic.download.orchestrator.is_connected_to_wifi") as mock_wifi,
        patch("fetchtastic.download.orchestrator.cleanup_legacy_hash_sidecars"),
        patch.object(orchestrator, "_process_firmware_downloads"),
        patch.object(orchestrator, "_process_android_downloads"),
        patch.object(orchestrator, "_retry_failed_downloads"),
        patch.object(orchestrator, "_log_download_summary"),
    ):
        orchestrator.run_download_pipeline()

        mock_wifi.assert_not_called()


def test_is_connected_to_wifi_termux_connected(mock_wifi_subprocess):
    """Test is_connected_to_wifi returns True when Termux has Wi-Fi."""
    wifi_data = {"supplicant_state": "COMPLETED", "ip": "192.168.1.100"}

    mock_wifi_subprocess(stdout=json.dumps(wifi_data))

    result = is_connected_to_wifi()
    assert result is True


def test_is_connected_to_wifi_termux_not_connected(mock_wifi_subprocess):
    """Test is_connected_to_wifi returns False when Termux has no Wi-Fi."""
    wifi_data = {"supplicant_state": "DISCONNECTED", "ip": ""}

    mock_wifi_subprocess(stdout=json.dumps(wifi_data))

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_termux_incomplete(mock_wifi_subprocess):
    """Test is_connected_to_wifi returns False when Termux Wi-Fi is incomplete."""
    wifi_data = {"supplicant_state": "SCANNING", "ip": ""}

    mock_wifi_subprocess(stdout=json.dumps(wifi_data))

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_non_termux(mocker):
    """Test is_connected_to_wifi returns True for non-Termux platforms."""
    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=False)

    result = is_connected_to_wifi()
    assert result is True


def test_is_connected_to_wifi_json_decode_error(mock_wifi_subprocess):
    """Test is_connected_to_wifi handles JSON decode errors gracefully."""
    mock_wifi_subprocess(stdout="invalid json")

    result = is_connected_to_wifi()
    assert result is False


def test_is_connected_to_wifi_os_error(mocker):
    """Test is_connected_to_wifi handles OSError gracefully."""
    mocker.patch("fetchtastic.download.orchestrator.is_termux", return_value=True)
    mocker.patch(
        "fetchtastic.download.orchestrator.subprocess.run",
        side_effect=FileNotFoundError("Command not found"),
    )

    result = is_connected_to_wifi()
    assert result is False
