"""
Tests for release-triggered snapshot cleanup.

cleanup_superseded_snapshots must run ONLY when a new stable or prerelease
download ships at least one new asset.  Snapshot-only transactions must never
trigger cleanup.
"""

from unittest.mock import Mock

import pytest

from fetchtastic.constants import FILE_TYPE_APP_SNAPSHOT
from fetchtastic.download.interfaces import Asset, DownloadResult, Release
from tests.test_snapshots import (
    _make_orchestrator_for_snapshots,
    _make_snapshot_release,
)


@pytest.fixture
def cache_manager(tmp_path):
    """Real CacheManager backed by tmp_path for file I/O."""
    from fetchtastic.download.cache import CacheManager

    return CacheManager(cache_dir=str(tmp_path))


def _make_prerelease() -> Release:
    return Release(
        tag_name="v2.8.1-open.1",
        prerelease=True,
        published_at="2024-01-01",
        assets=[
            Asset(
                name="app-fdroid-universal-release.apk",
                download_url="http://x",
                size=1,
            ),
            Asset(
                name="app-google-universal-release.apk",
                download_url="http://y",
                size=1,
            ),
        ],
    )


def _ok_result(file_path: str = "/tmp/x.apk") -> DownloadResult:
    return DownloadResult(
        success=True,
        release_tag="snapshot",
        file_path=file_path,
        download_url="http://x",
        file_size=1,
        file_type=FILE_TYPE_APP_SNAPSHOT,
    )


@pytest.mark.integration
def test_snapshot_only_no_cleanup(tmp_path, cache_manager):
    """Successful snapshot download with no release/prerelease → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=_ok_result(str(tmp_path / "x.apk"))
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_new_stable_download_triggers_cleanup(tmp_path, cache_manager):
    """Stable release downloads new assets → cleanup called once."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    # Allow stable assets to match so releases_to_download is non-empty.
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=False)
    orch._download_client_app_release = Mock(return_value=True)
    # No snapshot in this test.
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)

    orch._process_client_app_downloads()

    orch._download_client_app_release.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_called_once()


@pytest.mark.integration
def test_new_prerelease_download_triggers_cleanup(tmp_path, cache_manager):
    """Prerelease with successful non-skipped download → cleanup called once."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    prerelease = _make_prerelease()

    orch.client_app_downloader.handle_prereleases = Mock(return_value=[prerelease])
    orch.client_app_downloader.should_download_prerelease = Mock(return_value=True)
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.download_app = Mock(
        return_value=DownloadResult(
            success=True,
            release_tag=prerelease.tag_name,
            file_path=str(tmp_path / "pre.apk"),
            download_url="http://x",
            file_size=1,
            was_skipped=False,
        )
    )
    orch.client_app_downloader.update_prerelease_tracking = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=False)
    # Stable flow must be a no-op so only the prerelease path can trigger cleanup.
    orch._download_client_app_release = Mock(return_value=False)
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.cleanup_superseded_snapshots.assert_called_once()


@pytest.mark.integration
def test_already_complete_stable_no_cleanup(tmp_path, cache_manager):
    """Stable release already complete → no download → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=True)
    orch._download_client_app_release = Mock(return_value=True)
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)

    orch._process_client_app_downloads()

    orch._download_client_app_release.assert_not_called()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_already_complete_prerelease_no_cleanup(tmp_path, cache_manager):
    """Prerelease already tracked and complete → no download → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    prerelease = _make_prerelease()

    orch.client_app_downloader.handle_prereleases = Mock(return_value=[prerelease])
    orch.client_app_downloader.should_download_prerelease = Mock(return_value=False)
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=True)
    orch.client_app_downloader.download_app = Mock()
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.download_app.assert_not_called()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_partial_prerelease_failure_no_cleanup(tmp_path, cache_manager):
    """Some prerelease assets fail → not all succeed → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    prerelease = _make_prerelease()

    orch.client_app_downloader.handle_prereleases = Mock(return_value=[prerelease])
    orch.client_app_downloader.should_download_prerelease = Mock(return_value=True)
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.download_app = Mock(
        side_effect=[
            DownloadResult(
                success=True,
                release_tag=prerelease.tag_name,
                file_path=str(tmp_path / "a.apk"),
                download_url="http://x",
                file_size=1,
                was_skipped=False,
            ),
            DownloadResult(
                success=False,
                release_tag=prerelease.tag_name,
                file_path=str(tmp_path / "b.apk"),
                download_url="http://y",
                file_size=1,
                was_skipped=False,
            ),
        ]
    )
    # Stable flow must be a no-op so only the prerelease path is exercised.
    orch._download_client_app_release = Mock(return_value=False)
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_stable_failure_no_cleanup(tmp_path, cache_manager):
    """_download_client_app_release returns False → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=False)
    orch._download_client_app_release = Mock(return_value=False)
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=None)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)

    orch._process_client_app_downloads()

    orch._download_client_app_release.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_snapshot_tracking_failure_no_cleanup(tmp_path, cache_manager):
    """update_snapshot_tracking returns False → snapshot-only, no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=_ok_result(str(tmp_path / "x.apk"))
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=False)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.update_snapshot_tracking.assert_called_once()
    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()


@pytest.mark.integration
def test_both_release_and_snapshot_cleanup_once(tmp_path, cache_manager):
    """Stable download succeeds AND snapshot succeeds → cleanup called exactly once."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    # Stable flow: asset matches, incomplete, download succeeds.
    orch.client_app_downloader.should_download_asset = Mock(return_value=True)
    orch.client_app_downloader.is_release_complete = Mock(return_value=False)
    orch._download_client_app_release = Mock(return_value=True)

    # Snapshot flow: succeeds.
    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(
        return_value=[release.assets[0]]
    )
    orch.client_app_downloader.download_snapshot_asset = Mock(
        return_value=_ok_result(str(tmp_path / "snap.apk"))
    )
    orch.client_app_downloader.update_snapshot_tracking = Mock(return_value=True)
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.cleanup_superseded_snapshots.assert_called_once()


@pytest.mark.integration
def test_empty_selected_assets_no_cleanup(tmp_path, cache_manager):
    """No matching snapshot assets and no release download → no cleanup."""
    orch = _make_orchestrator_for_snapshots(tmp_path, cache_manager)
    release = _make_snapshot_release(vc=100)

    orch.client_app_downloader.fetch_snapshot_release = Mock(return_value=release)
    orch.client_app_downloader.handle_snapshots = Mock(return_value=release)
    orch.client_app_downloader.get_snapshot_version_code = Mock(return_value=100)
    orch.client_app_downloader.should_process_snapshot = Mock(return_value=True)
    orch.client_app_downloader.get_selected_snapshot_assets = Mock(return_value=[])
    orch.client_app_downloader.cleanup_superseded_snapshots = Mock(return_value=0)
    orch._handle_download_result = Mock()

    orch._process_client_app_downloads()

    orch.client_app_downloader.cleanup_superseded_snapshots.assert_not_called()
