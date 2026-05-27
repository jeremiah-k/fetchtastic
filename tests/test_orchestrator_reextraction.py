# Tests for re-extraction block in _process_firmware_downloads
#
# Covers orchestrator.py lines 809-827: the for-loop that re-extracts
# firmware zips when zips_needing_extraction is non-empty during
# processing of already-complete releases.

from unittest.mock import Mock, patch

import pytest

from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from fetchtastic.download.orchestrator import DownloadOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orch(tmp_path):
    """Create a DownloadOrchestrator with firmware-related mocks pre-wired.

    The fixture creates the orchestrator with SAVE_FIRMWARE=True and
    wires up the minimum set of mocks required for _process_firmware_downloads
    to reach the re-extraction block.  All state-affecting methods are
    mocked so that the test can focus solely on lines 809-827.
    """
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "SAVE_APKS": False,
        "SAVE_FIRMWARE": True,
        "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
        "EXCLUDE_PATTERNS": ["*debug*"],
        "EXTRACT_PATTERNS": ["*.bin"],
        "GITHUB_TOKEN": "test_token",
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "KEEP_LAST_BETA": False,
        "FILTER_REVOKED_RELEASES": False,
        "AUTO_EXTRACT": True,
        "CREATE_LATEST_SYMLINKS": True,
    }
    orch = DownloadOrchestrator(config)

    # Replace firmware_downloader entirely with a Mock so no real methods
    # leak through.  This matches the pattern in test_download_orchestrator.py.
    orch.firmware_downloader = Mock()
    orch.firmware_downloader.download_dir = str(tmp_path / "firmware")
    orch.firmware_downloader.is_release_revoked = Mock(return_value=False)
    orch.firmware_downloader.format_release_log_suffix = Mock(return_value="")
    orch.firmware_downloader.ensure_release_notes = Mock()
    orch.firmware_downloader.download_repo_prerelease_firmware = Mock(
        return_value=([], [], None, None)
    )
    orch.firmware_downloader.cleanup_superseded_prereleases = Mock()
    orch.firmware_downloader.is_release_complete = Mock(return_value=True)
    orch.firmware_downloader.download_manifests = Mock(return_value=[])

    def _collect_non_revoked(*, initial_releases, current_fetch_limit, **_unused):
        return initial_releases, initial_releases, current_fetch_limit

    orch.firmware_downloader.collect_non_revoked_releases = Mock(
        side_effect=_collect_non_revoked
    )

    # --- orchestrator-level mocks ---
    orch._has_selected_non_manifest_firmware_asset = Mock(return_value=True)
    orch._select_latest_release_by_version = Mock(return_value=None)

    return orch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(name="firmware-rak4631.zip") -> Asset:
    """Create a minimal Asset for use in extraction tests."""
    return Asset(
        name=name,
        download_url="https://example.com/" + name,
        size=100,
    )


def _wire_release(orch, tag="v2.0.0", assets=None):
    """Wire a release into the orchestrator's firmware processing pipeline.

    Returns the Release object so tests can make assertions against it.
    """
    if assets is None:
        assets = [_make_asset()]
    release = Release(tag_name=tag, prerelease=False, assets=list(assets))
    orch.firmware_downloader.get_releases = Mock(return_value=[release])
    orch.firmware_downloader.collect_non_revoked_releases = Mock(
        return_value=([release], [release], 8)
    )
    return release


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReExtractionBlock:
    """Tests for orchestrator._process_firmware_downloads lines 809-827.

    The block is entered when a release is already complete but
    get_zips_needing_extraction returns one or more assets — indicating
    that extraction patterns changed or previously extracted files are
    missing.
    """

    # ------------------------------------------------------------------
    # Extraction succeeds, not skipped
    # ------------------------------------------------------------------

    def test_re_extract_success_sets_firmware_downloaded(self, orch):
        """Re-extraction that succeeds (was_skipped=False) calls all
        expected collaborators."""
        asset = _make_asset("firmware-rak4631.zip")
        release = _wire_release(orch, "v2.0.0", [asset])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(
            return_value=[asset]
        )

        extract_result = Mock(spec=DownloadResult)
        extract_result.success = True
        extract_result.was_skipped = False
        orch.firmware_downloader.extract_firmware = Mock(return_value=extract_result)

        with patch.object(orch, "_handle_download_result") as mock_handle, patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ) as mock_pointer:
            orch._process_firmware_downloads()

        # extract_firmware called with correct arguments
        orch.firmware_downloader.extract_firmware.assert_called_once_with(
            release,
            asset,
            ["*.bin"],
            ["*debug*"],
        )

        # _handle_download_result called with "firmware_extraction"
        mock_handle.assert_called_once_with(extract_result, "firmware_extraction")

        # Latest pointer should be updated after successful re-extraction
        mock_pointer.assert_called_once()

    # ------------------------------------------------------------------
    # Extraction succeeds but was_skipped=True
    # ------------------------------------------------------------------

    def test_re_extract_skipped_still_handles_result(self, orch):
        """When extract_firmware returns was_skipped=True the result is
        still passed to _handle_download_result with the correct op type."""
        asset = _make_asset()
        _wire_release(orch, "v2.0.0", [asset])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(
            return_value=[asset]
        )

        extract_result = Mock(spec=DownloadResult)
        extract_result.success = True
        extract_result.was_skipped = True
        orch.firmware_downloader.extract_firmware = Mock(return_value=extract_result)

        with patch.object(orch, "_handle_download_result") as mock_handle, patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ):
            orch._process_firmware_downloads()

        # Still passed through to result handler
        mock_handle.assert_called_once_with(extract_result, "firmware_extraction")

    # ------------------------------------------------------------------
    # Extraction failure
    # ------------------------------------------------------------------

    def test_re_extract_failure_handles_result(self, orch):
        """A failed extraction result is still routed to
        _handle_download_result."""
        asset = _make_asset()
        _wire_release(orch, "v2.0.0", [asset])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(
            return_value=[asset]
        )

        extract_result = Mock(spec=DownloadResult)
        extract_result.success = False
        orch.firmware_downloader.extract_firmware = Mock(return_value=extract_result)

        with patch.object(orch, "_handle_download_result") as mock_handle, patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ):
            orch._process_firmware_downloads()

        mock_handle.assert_called_once_with(extract_result, "firmware_extraction")

    # ------------------------------------------------------------------
    # Multiple assets in zips_needing_extraction
    # ------------------------------------------------------------------

    def test_re_extract_multiple_assets(self, orch):
        """Each asset in zips_needing_extraction triggers its own
        extract_firmware and _handle_download_result call."""
        asset_a = _make_asset("firmware-rak4631.zip")
        asset_b = _make_asset("firmware-tbeam.zip")
        _wire_release(orch, "v2.0.0", [asset_a, asset_b])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(
            return_value=[asset_a, asset_b]
        )

        result_a = Mock(spec=DownloadResult)
        result_a.success = True
        result_a.was_skipped = False
        result_b = Mock(spec=DownloadResult)
        result_b.success = True
        result_b.was_skipped = False
        orch.firmware_downloader.extract_firmware = Mock(
            side_effect=[result_a, result_b]
        )

        with patch.object(orch, "_handle_download_result") as mock_handle, patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ):
            orch._process_firmware_downloads()

        assert orch.firmware_downloader.extract_firmware.call_count == 2
        assert mock_handle.call_count == 2
        mock_handle.assert_any_call(result_a, "firmware_extraction")
        mock_handle.assert_any_call(result_b, "firmware_extraction")

    # ------------------------------------------------------------------
    # No zips needing extraction → block skipped
    # ------------------------------------------------------------------

    def test_no_zips_needing_extraction_skips_block(self, orch):
        """When get_zips_needing_extraction returns [], the re-extraction
        block is never entered and extract_firmware is not called."""
        asset = _make_asset()
        _wire_release(orch, "v2.0.0", [asset])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(return_value=[])
        orch.firmware_downloader.extract_firmware = Mock()

        with patch.object(orch, "_handle_download_result") as mock_handle, patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ):
            orch._process_firmware_downloads()

        orch.firmware_downloader.extract_firmware.assert_not_called()
        # No extraction result → no firmware_extraction handle call
        extraction_calls = [
            c for c in mock_handle.call_args_list if c[0][1] == "firmware_extraction"
        ]
        assert len(extraction_calls) == 0

    # ------------------------------------------------------------------
    # Extraction patterns are pulled from config
    # ------------------------------------------------------------------

    def test_re_extract_uses_configured_patterns(self, orch):
        """Verify that _get_extraction_patterns / _get_exclude_patterns
        are called and their return values reach extract_firmware."""
        orch.config["EXTRACT_PATTERNS"] = ["*.uf2", "*.bin"]
        orch.config["EXCLUDE_PATTERNS"] = ["*debug*", "*test*"]

        asset = _make_asset()
        release = _wire_release(orch, "v2.0.0", [asset])

        orch.firmware_downloader.get_zips_needing_extraction = Mock(
            return_value=[asset]
        )

        extract_result = Mock(spec=DownloadResult)
        extract_result.success = True
        extract_result.was_skipped = False
        orch.firmware_downloader.extract_firmware = Mock(return_value=extract_result)

        with patch.object(orch, "_handle_download_result"), patch.object(
            orch.firmware_downloader, "update_latest_pointer_for_release"
        ):
            orch._process_firmware_downloads()

        orch.firmware_downloader.extract_firmware.assert_called_once_with(
            release,
            asset,
            ["*.uf2", "*.bin"],
            ["*debug*", "*test*"],
        )
