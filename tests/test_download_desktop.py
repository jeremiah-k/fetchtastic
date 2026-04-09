import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fetchtastic.constants import (
    APP_DIR_NAME,
    DESKTOP_DIR_NAME,
    DESKTOP_PRERELEASES_DIR_NAME,
    FILE_TYPE_DESKTOP_PRERELEASE,
    RELEASE_SCAN_COUNT,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.desktop import MeshtasticDesktopDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def mock_cache_manager(tmp_path):
    """Create a minimal cache manager mock for Desktop downloader tests."""
    mock = Mock(spec=CacheManager)
    mock.cache_dir = str(tmp_path / "cache")

    def _get_cache_file_path(file_name: str, suffix: str = ".json") -> str:
        normalized_suffix = suffix or ""
        normalized_name = file_name
        if normalized_suffix and not normalized_name.lower().endswith(
            normalized_suffix.lower()
        ):
            normalized_name = f"{normalized_name}{normalized_suffix}"
        return os.path.join(mock.cache_dir, normalized_name)

    mock.get_cache_file_path.side_effect = _get_cache_file_path
    return mock


@pytest.fixture
def downloader(tmp_path, mock_cache_manager):
    """Create a Desktop downloader with mocked dependencies."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "EXCLUDE_PATTERNS": [],
        "SELECTED_DESKTOP_ASSETS": [],
    }
    dl = MeshtasticDesktopDownloader(config, mock_cache_manager)
    dl.cache_manager = mock_cache_manager
    dl.version_manager = Mock()
    real_version_manager = VersionManager()
    dl.version_manager.get_release_tuple.side_effect = (
        real_version_manager.get_release_tuple
    )
    dl.version_manager.is_prerelease_version.side_effect = (
        real_version_manager.is_prerelease_version
    )
    return dl


def test_is_release_complete_uses_prerelease_directory(downloader, tmp_path):
    """Prerelease completeness checks should read from app/desktop/prereleases/<tag>."""
    downloader.verify = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20-open.1",
        prerelease=True,
        assets=[
            Asset(
                name="Meshtastic-2.7.20-open.1.dmg",
                download_url="https://example.invalid/desktop",
                size=4,
            )
        ],
    )

    prerelease_dir = (
        tmp_path
        / "downloads"
        / APP_DIR_NAME
        / DESKTOP_DIR_NAME
        / DESKTOP_PRERELEASES_DIR_NAME
        / "v2.7.20-open.1"
    )
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "Meshtastic-2.7.20-open.1.dmg").write_bytes(b"desk")

    assert downloader.is_release_complete(release) is True


def test_is_release_complete_ignores_non_installer_assets(downloader, tmp_path):
    """Completeness should only require installer assets selected by get_assets()."""
    downloader.verify = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[
            Asset(
                name="Meshtastic-2.7.20.dmg",
                download_url="https://example.invalid/dmg",
                size=4,
            ),
            Asset(
                name="Meshtastic-2.7.20.sha256",
                download_url="https://example.invalid/sha",
                size=64,
            ),
        ],
    )

    stable_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    stable_dir.mkdir(parents=True)
    (stable_dir / "Meshtastic-2.7.20.dmg").write_bytes(b"dmg!")

    assert downloader.is_release_complete(release) is True


def test_get_releases_uses_retention_default_for_scan_window(downloader):
    """Without explicit desktop retention config, initial scan should stay at RELEASE_SCAN_COUNT."""
    downloader.config.pop("DESKTOP_VERSIONS_TO_KEEP", None)
    downloader.github_source.fetch_raw_releases_data = Mock(return_value=[])

    releases = downloader.get_releases()

    assert releases == []
    downloader.github_source.fetch_raw_releases_data.assert_called_once_with(
        {"per_page": RELEASE_SCAN_COUNT, "page": 1}
    )


def test_get_target_path_for_release_sanitizes_inputs(downloader, tmp_path):
    """Path construction should sanitize release tag and file name."""
    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[],
    )
    path = downloader.get_target_path_for_release(
        "v2.7.20", "Meshtastic-2.7.20.dmg", release=release
    )
    assert "v2.7.20" in path
    assert "Meshtastic-2.7.20.dmg" in path


def test_get_target_path_for_release_prerelease_uses_prerelease_dir(
    downloader, tmp_path
):
    """Prerelease releases should use the prerelease subdirectory."""
    release = Release(
        tag_name="v2.7.20-open.1",
        prerelease=True,
        assets=[],
    )
    path = downloader.get_target_path_for_release(
        "v2.7.20-open.1", "test.dmg", release=release
    )
    assert DESKTOP_PRERELEASES_DIR_NAME in path


def test_get_target_path_for_release_infers_prerelease_from_tag(downloader, tmp_path):
    """When release is None, prerelease status should be inferred from tag name."""
    real_vm = VersionManager()
    downloader.version_manager.is_prerelease_version.side_effect = (
        real_vm.is_prerelease_version
    )
    path = downloader.get_target_path_for_release(
        "v2.7.20-open.1", "test.dmg", is_prerelease=None
    )
    assert DESKTOP_PRERELEASES_DIR_NAME in path


def test_get_target_path_for_release_uses_explicit_prerelease(downloader, tmp_path):
    """Explicit is_prerelease=True should place file in prerelease directory."""
    path = downloader.get_target_path_for_release(
        "v2.7.20", "test.dmg", is_prerelease=True
    )
    assert DESKTOP_PRERELEASES_DIR_NAME in path


def test_update_release_history_returns_none_for_empty_list(downloader):
    """Empty release list should return None."""
    result = downloader.update_release_history([])
    assert result is None


def test_update_release_history_returns_none_for_no_stable(downloader):
    """Only prereleases should return None."""
    releases = [
        Release(tag_name="v2.7.20-open.1", prerelease=True, assets=[]),
    ]
    result = downloader.update_release_history(releases, log_summary=False)
    assert result is None


def test_update_release_history_calls_manager(downloader):
    """With stable releases, should call release_history_manager."""
    downloader.release_history_manager.update_release_history = Mock(
        return_value={"releases": []}
    )
    downloader.release_history_manager.log_release_status_summary = Mock()
    releases = [
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    result = downloader.update_release_history(releases, log_summary=True)
    assert result == {"releases": []}
    downloader.release_history_manager.log_release_status_summary.assert_called_once()


def test_format_release_log_suffix(downloader):
    """Log suffix should be extracted from formatted release label."""
    downloader.release_history_manager.format_release_label = Mock(
        return_value="v2.7.20 [alpha]"
    )
    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    suffix = downloader.format_release_log_suffix(release)
    assert suffix == " [alpha]"


def test_ensure_release_notes_skips_unsafe_tag(downloader):
    """Unsafe tag should return None and log warning."""
    from fetchtastic.download.files import _sanitize_path_component

    unsafe_tag = "../../../etc/passwd"
    if _sanitize_path_component(unsafe_tag) is not None:
        pytest.skip("Tag was not actually unsafe")
    release = Release(tag_name=unsafe_tag, prerelease=False, assets=[], body="notes")
    result = downloader.ensure_release_notes(release)
    assert result is None


def test_ensure_release_notes_prerelease_path(downloader, tmp_path):
    """Prerelease notes should be written to prerelease directory."""
    downloader._write_release_notes = Mock(return_value="/path/to/notes.md")
    release = Release(
        tag_name="v2.7.20-open.1",
        prerelease=True,
        assets=[],
        body="Release notes content",
    )
    result = downloader.ensure_release_notes(release)
    assert result == "/path/to/notes.md"


def test_ensure_release_notes_rejects_symlinked_desktop_base(downloader, tmp_path):
    """Stable release notes should not be written through a symlinked desktop root."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    app_dir = tmp_path / "downloads" / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    desktop_dir = app_dir / DESKTOP_DIR_NAME
    try:
        desktop_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported in this test environment")

    downloader._write_release_notes = Mock(return_value="/outside/notes.md")
    release = Release(tag_name="v2.7.20", prerelease=False, assets=[], body="notes")
    result = downloader.ensure_release_notes(release)

    assert result is None
    downloader._write_release_notes.assert_not_called()


def test_ensure_release_notes_rejects_symlinked_desktop_ancestor(downloader, tmp_path):
    """Stable release notes should not be written through symlinked desktop ancestors."""
    outside_app = tmp_path / "outside-app"
    (outside_app / DESKTOP_DIR_NAME).mkdir(parents=True)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True)
    app_link = downloads_dir / APP_DIR_NAME
    try:
        app_link.symlink_to(outside_app, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported in this test environment")

    downloader._write_release_notes = Mock(return_value="/outside/notes.md")
    release = Release(tag_name="v2.7.20", prerelease=False, assets=[], body="notes")

    result = downloader.ensure_release_notes(release)

    assert result is None
    downloader._write_release_notes.assert_not_called()


def test_is_asset_complete_for_target_missing_file(downloader):
    """Missing file should return False."""
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)
    result = downloader._is_asset_complete_for_target("/nonexistent/path", asset)
    assert result is False


def test_is_asset_complete_for_target_size_mismatch(downloader, tmp_path):
    """Size mismatch should return False."""
    downloader.file_operations = Mock()
    downloader.file_operations.get_file_size = Mock(return_value=50)
    downloader.verify = Mock(return_value=True)

    test_file = tmp_path / "test.dmg"
    test_file.write_bytes(b"x" * 100)
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)
    result = downloader._is_asset_complete_for_target(str(test_file), asset)
    assert result is False


def test_is_asset_complete_for_target_verify_fails(downloader, tmp_path):
    """Failed verification should return False."""
    downloader.file_operations = Mock()
    downloader.file_operations.get_file_size = Mock(return_value=100)
    downloader.verify = Mock(return_value=False)

    test_file = tmp_path / "test.dmg"
    test_file.write_bytes(b"x" * 100)
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)
    result = downloader._is_asset_complete_for_target(str(test_file), asset)
    assert result is False


def test_is_asset_complete_for_target_zip_integrity_fails(downloader, tmp_path):
    """Failed zip integrity should return False."""
    downloader.file_operations = Mock()
    downloader.file_operations.get_file_size = Mock(return_value=100)
    downloader.verify = Mock(return_value=True)
    downloader._is_zip_intact = Mock(return_value=False)

    test_file = tmp_path / "test.zip"
    test_file.write_bytes(b"x" * 100)
    asset = Asset(name="test.zip", download_url="http://example.com/test.zip", size=100)
    result = downloader._is_asset_complete_for_target(str(test_file), asset)
    assert result is False


def test_is_asset_complete_for_target_success(downloader, tmp_path):
    """All checks passing should return True."""
    downloader.file_operations = Mock()
    downloader.file_operations.get_file_size = Mock(return_value=100)
    downloader.verify = Mock(return_value=True)

    test_file = tmp_path / "test.dmg"
    test_file.write_bytes(b"x" * 100)
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)
    result = downloader._is_asset_complete_for_target(str(test_file), asset)
    assert result is True


def test_get_releases_zero_limit(downloader):
    """Zero limit should return empty list."""
    result = downloader.get_releases(limit=0)
    assert result == []


def test_get_releases_negative_limit(downloader):
    """Negative limit should return empty list."""
    result = downloader.get_releases(limit=-1)
    assert result == []


def test_get_releases_malformed_release_entry(downloader):
    """Non-dict release entry should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=["not a dict", {"tag_name": "v2.7.20", "assets": []}]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_missing_assets(downloader):
    """Release without assets should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[{"tag_name": "v2.7.20", "assets": None}]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_empty_assets(downloader):
    """Release with empty assets list should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[{"tag_name": "v2.7.20", "assets": []}]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_missing_tag_name(downloader):
    """Release without tag_name should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "",
                "assets": [{"name": "test.dmg", "browser_download_url": "http://x"}],
            }
        ]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_non_string_tag(downloader):
    """Release with non-string tag_name should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": 123,
                "assets": [{"name": "test.dmg", "browser_download_url": "http://x"}],
            }
        ]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_filters_legacy_tags(downloader):
    """Legacy pre-2.7.14 releases should be filtered."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.10",
                "assets": [{"name": "test.dmg", "browser_download_url": "http://x"}],
            }
        ]
    )
    result = downloader.get_releases(limit=10)
    assert result == []


def test_get_releases_with_valid_release(downloader):
    """Valid release with assets should be included."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.20",
                "prerelease": False,
                "assets": [
                    {
                        "name": "Meshtastic-2.7.20.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )
    result = downloader.get_releases(limit=10)
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.20"


def test_get_releases_marks_semver_prerelease_from_tag(downloader):
    """Semver prerelease tags should be classified as prerelease consistently."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.20-open.1",
                "prerelease": False,
                "assets": [
                    {
                        "name": "Meshtastic-2.7.20-open.1.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )

    result = downloader.get_releases(limit=10)
    assert len(result) == 1
    assert result[0].prerelease is True


def test_get_releases_tracks_known_2714_prerelease_version_mismatch(downloader):
    """2.7.14 prerelease installer filename mismatches should be tracked and logged once."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.14-closed.10",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic-1.0.0.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )

    with patch("fetchtastic.download.desktop.logger") as mock_logger:
        result = downloader.get_releases(limit=10)

    assert len(result) == 1
    assert downloader.has_known_2714_prerelease_version_mismatch() is True
    assert downloader.get_known_2714_prerelease_mismatch_tags() == ["v2.7.14-closed.10"]
    assert mock_logger.warning.call_count == 0
    assert any(
        "known transitional packaging names" in call.args[0]
        for call in mock_logger.info.call_args_list
    )


def test_get_releases_logs_known_2714_info_only_once_per_scan(downloader):
    """Known 2.7.14 transitional mismatch info should log once per scan."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.14-closed.10",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic-1.0.0.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            },
            {
                "tag_name": "v2.7.14-closed.1",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic-1.0.0.exe",
                        "browser_download_url": "http://example.com/test.exe",
                        "size": 100,
                    }
                ],
            },
        ]
    )

    with patch("fetchtastic.download.desktop.logger") as mock_logger:
        result = downloader.get_releases(limit=10)

    assert len(result) == 2
    assert downloader.get_known_2714_prerelease_mismatch_tags() == [
        "v2.7.14-closed.10",
        "v2.7.14-closed.1",
    ]
    known_info_calls = [
        call
        for call in mock_logger.info.call_args_list
        if "known transitional packaging names" in call.args[0]
    ]
    assert len(known_info_calls) == 1
    assert any(
        "also matches the known 2.7.14 transitional packaging discrepancy"
        in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_get_releases_does_not_track_2714_mismatch_when_versions_align(downloader):
    """Matching desktop installer version labels should not set the known mismatch flag."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.14-closed.9",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic-2.7.14.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )

    result = downloader.get_releases(limit=10)

    assert len(result) == 1
    assert downloader.has_known_2714_prerelease_version_mismatch() is False
    assert downloader.get_known_2714_prerelease_mismatch_tags() == []


def test_get_releases_resets_known_2714_mismatch_observation_each_scan(downloader):
    """Known mismatch observations should be cleared at the start of each release scan."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        side_effect=[
            [
                {
                    "tag_name": "v2.7.14-closed.10",
                    "prerelease": True,
                    "assets": [
                        {
                            "name": "Meshtastic-1.0.0.dmg",
                            "browser_download_url": "http://example.com/test.dmg",
                            "size": 100,
                        }
                    ],
                }
            ],
            [
                {
                    "tag_name": "v2.7.14-closed.9",
                    "prerelease": True,
                    "assets": [
                        {
                            "name": "Meshtastic-2.7.14.dmg",
                            "browser_download_url": "http://example.com/test.dmg",
                            "size": 100,
                        }
                    ],
                }
            ],
        ]
    )

    first_result = downloader.get_releases(limit=10)
    second_result = downloader.get_releases(limit=10)

    assert len(first_result) == 1
    assert len(second_result) == 1
    assert downloader.has_known_2714_prerelease_version_mismatch() is False
    assert downloader.get_known_2714_prerelease_mismatch_tags() == []


def test_get_releases_logs_warning_for_unexpected_version_mismatch(downloader):
    """Unexpected installer filename/version mismatches should still emit warning-level logs."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.8.0-open.1",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic-1.0.0.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )

    with patch("fetchtastic.download.desktop.logger") as mock_logger:
        result = downloader.get_releases(limit=10)

    assert len(result) == 1
    assert downloader.has_known_2714_prerelease_version_mismatch() is False
    assert mock_logger.warning.call_count > 0
    assert any(
        "installer filename version mismatch" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


def test_get_releases_no_valid_assets(downloader):
    """Release with only non-desktop assets should be skipped (no valid installer assets)."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.20",
                "prerelease": False,
                "assets": [
                    {
                        "name": "source.zip",
                        "browser_download_url": "http://example.com/source.zip",
                        "size": 100,
                    }
                ],
            }
        ]
    )
    result = downloader.get_releases(limit=10)
    assert len(result) == 0  # Release with no valid installer assets is skipped


def test_get_releases_fetch_returns_none(downloader):
    """When fetch returns None, should return empty list."""
    downloader.github_source.fetch_raw_releases_data = Mock(return_value=None)
    result = downloader.get_releases()
    assert result == []


def test_get_releases_exception_handling(downloader):
    """Exceptions during fetch should be caught and return empty list."""
    import requests

    downloader.github_source.fetch_raw_releases_data = Mock(
        side_effect=requests.RequestException("network error")
    )
    result = downloader.get_releases()
    assert result == []


def test_get_download_url(downloader):
    """get_download_url should return asset's download_url."""
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)
    result = downloader.get_download_url(asset)
    assert result == "http://example.com/test.dmg"


def test_should_download_asset_exclude_pattern(downloader):
    """Exclude patterns should block matching assets."""
    downloader._get_exclude_patterns = Mock(return_value=["*.sha256"])
    result = downloader.should_download_asset("checksums.sha256")
    assert result is False


def test_should_download_asset_no_patterns(downloader):
    """No selected patterns should allow all assets."""
    downloader.config["SELECTED_DESKTOP_ASSETS"] = []
    result = downloader.should_download_asset("test.dmg")
    assert result is True


def test_should_download_asset_with_patterns(downloader):
    """Selected patterns should filter assets."""
    downloader.config["SELECTED_DESKTOP_ASSETS"] = ["*.dmg"]
    result = downloader.should_download_asset("test.dmg")
    assert result is True


def test_should_download_asset_pattern_mismatch(downloader):
    """Non-matching selected patterns should block assets."""
    downloader.config["SELECTED_DESKTOP_ASSETS"] = ["*.AppImage"]
    result = downloader.should_download_asset("test.dmg")
    assert result is False


def test_should_download_asset_backward_compat_old_key(downloader):
    """Backward compatibility: Old SELECTED_DESKTOP_PLATFORMS key should still work."""
    downloader.config.pop("SELECTED_DESKTOP_ASSETS", None)
    downloader.config["SELECTED_DESKTOP_PLATFORMS"] = ["*.dmg"]
    result = downloader.should_download_asset("test.dmg")
    assert result is True


def test_should_download_asset_new_key_presence_prevents_legacy_fallback(downloader):
    """Explicit empty new key should not fall back to legacy selections."""
    downloader.config["SELECTED_DESKTOP_ASSETS"] = []
    downloader.config["SELECTED_DESKTOP_PLATFORMS"] = ["*.exe"]
    result = downloader.should_download_asset("test.dmg")
    assert result is True


def test_download_desktop_already_complete(downloader, tmp_path):
    """Already complete file should skip download."""
    downloader._is_asset_complete_for_target = Mock(return_value=True)
    downloader.create_download_result = Mock(return_value={"success": True})

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": True}


def test_download_desktop_success(downloader, tmp_path):
    """Successful download and verification."""
    downloader._is_asset_complete_for_target = Mock(side_effect=[False, True])
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    downloader.download = Mock(return_value=True)
    downloader.create_download_result = Mock(return_value={"success": True})

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": True}


def test_download_desktop_infers_prerelease_from_tag_for_path_and_file_type(
    downloader, tmp_path
):
    """Semver/open prerelease tags should drive prerelease path + file type even when flag is false."""
    downloader._is_asset_complete_for_target = Mock(side_effect=[False, True])
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    downloader.download = Mock(return_value=True)
    downloader.create_download_result = Mock(return_value={"success": True})

    release = Release(tag_name="v2.7.20-open.1", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)

    assert result == {"success": True}
    downloader.get_target_path_for_release.assert_called_once_with(
        "v2.7.20-open.1",
        "test.dmg",
        is_prerelease=True,
        release=release,
    )
    assert (
        downloader.create_download_result.call_args.kwargs["file_type"]
        == FILE_TYPE_DESKTOP_PRERELEASE
    )


def test_download_desktop_verify_fails(downloader, tmp_path):
    """Failed verification should cleanup and return error."""
    downloader._is_asset_complete_for_target = Mock(side_effect=[False, False])
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    downloader.download = Mock(return_value=True)
    downloader.cleanup_file = Mock()
    downloader.create_download_result = Mock(return_value={"success": False})

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    downloader.cleanup_file.assert_called_once()
    assert result == {"success": False}


def test_download_desktop_download_fails(downloader, tmp_path):
    """Failed download should return error."""
    downloader._is_asset_complete_for_target = Mock(return_value=False)
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    downloader.download = Mock(return_value=False)
    downloader.create_download_result = Mock(return_value={"success": False})

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": False}


def test_download_desktop_network_exception(downloader, tmp_path):
    """Network exception should return retryable error."""
    import requests

    downloader._is_asset_complete_for_target = Mock(
        side_effect=requests.RequestException("network error")
    )
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    mock_create_result = Mock(return_value={"success": False})
    downloader.create_download_result = mock_create_result

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": False}

    # Verify create_download_result was called with correct kwargs
    mock_create_result.assert_called_once()
    call_kwargs = mock_create_result.call_args[1]
    assert call_kwargs.get("error_type") == "network_error"
    assert call_kwargs.get("is_retryable") is True


def test_download_desktop_oserror(downloader, tmp_path):
    """OSError should return non-retryable filesystem error."""
    downloader._is_asset_complete_for_target = Mock(side_effect=OSError("disk full"))
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    mock_create_result = Mock(return_value={"success": False})
    downloader.create_download_result = mock_create_result

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": False}

    # Verify create_download_result was called with correct kwargs
    mock_create_result.assert_called_once()
    call_kwargs = mock_create_result.call_args[1]
    assert call_kwargs.get("error_type") == "filesystem_error"
    assert call_kwargs.get("is_retryable") is False


def test_download_desktop_value_error(downloader, tmp_path):
    """ValueError should return validation error."""
    downloader._is_asset_complete_for_target = Mock(side_effect=ValueError("bad value"))
    downloader.get_target_path_for_release = Mock(
        return_value=str(tmp_path / "test.dmg")
    )
    mock_create_result = Mock(return_value={"success": False})
    downloader.create_download_result = mock_create_result

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": False}

    # Verify create_download_result was called with correct kwargs
    mock_create_result.assert_called_once()
    call_kwargs = mock_create_result.call_args[1]
    assert call_kwargs.get("error_type") == "validation_error"
    assert call_kwargs.get("is_retryable") is False


def test_download_desktop_no_target_path(downloader, tmp_path):
    """Exception before target_path is set should use fallback path."""
    import requests

    downloader.get_target_path_for_release = Mock(
        side_effect=requests.RequestException("error")
    )
    mock_create_result = Mock(return_value={"success": False})
    downloader.create_download_result = mock_create_result

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    asset = Asset(name="test.dmg", download_url="http://example.com/test.dmg", size=100)

    result = downloader.download_desktop(release, asset)
    assert result == {"success": False}

    # Verify create_download_result was called with fallback file_path
    mock_create_result.assert_called_once()
    call_kwargs = mock_create_result.call_args[1]
    expected_fallback = str(
        Path(downloader.download_dir) / APP_DIR_NAME / DESKTOP_DIR_NAME
    )
    assert call_kwargs.get("file_path") == expected_fallback


def test_is_release_complete_missing_dir(downloader):
    """Missing version directory should return False."""
    downloader.verify = Mock(return_value=True)
    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=100)],
    )
    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_rejects_symlinked_desktop_base(downloader, tmp_path):
    """Completeness checks should not follow symlinked desktop roots."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    app_dir = tmp_path / "downloads" / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    desktop_dir = app_dir / DESKTOP_DIR_NAME
    try:
        desktop_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported in this test environment")

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=4)],
    )

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_rejects_symlinked_desktop_ancestor(downloader, tmp_path):
    """Completeness checks should reject releases under symlinked desktop ancestors."""
    outside_app = tmp_path / "outside-app"
    outside_release_dir = outside_app / DESKTOP_DIR_NAME / "v2.7.20"
    outside_release_dir.mkdir(parents=True)
    (outside_release_dir / "Meshtastic-2.7.20.dmg").write_bytes(b"dmg!")

    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True)
    app_link = downloads_dir / APP_DIR_NAME
    try:
        app_link.symlink_to(outside_app, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported in this test environment")

    downloader.verify = Mock(return_value=True)
    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[
            Asset(
                name="Meshtastic-2.7.20.dmg",
                download_url="https://example.invalid/dmg",
                size=4,
            )
        ],
    )

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_no_expected_assets(downloader, tmp_path):
    """No assets to download should return False."""
    downloader.verify = Mock(return_value=True)
    downloader.should_download_asset = Mock(return_value=False)

    release = Release(tag_name="v2.7.20", prerelease=False, assets=[])
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_missing_file(downloader, tmp_path):
    """Missing asset file should return False."""
    downloader.verify = Mock(return_value=True)
    downloader.should_download_asset = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=100)],
    )
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_size_mismatch(downloader, tmp_path):
    """Size mismatch should return False."""
    downloader.verify = Mock(return_value=True)
    downloader.should_download_asset = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=1000)],
    )
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)
    (version_dir / "test.dmg").write_bytes(b"tiny")

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_with_unknown_asset_size(downloader, tmp_path):
    """Unknown asset sizes should skip size checks but still require verification."""
    downloader.verify = Mock(return_value=True)
    downloader.should_download_asset = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=None)],
    )
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)
    (version_dir / "test.dmg").write_bytes(b"test")

    result = downloader.is_release_complete(release)
    assert result is True


def test_is_release_complete_verify_fails(downloader, tmp_path):
    """Failed verification should return False."""
    downloader.verify = Mock(return_value=False)
    downloader.should_download_asset = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=4)],
    )
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)
    (version_dir / "test.dmg").write_bytes(b"test")

    result = downloader.is_release_complete(release)
    assert result is False


def test_is_release_complete_oserror(downloader, tmp_path):
    """OSError when checking file should return False."""
    downloader.verify = Mock(return_value=True)
    downloader.should_download_asset = Mock(return_value=True)

    release = Release(
        tag_name="v2.7.20",
        prerelease=False,
        assets=[Asset(name="test.dmg", download_url="http://x", size=4)],
    )
    version_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME / "v2.7.20"
    version_dir.mkdir(parents=True)
    test_file = version_dir / "test.dmg"
    test_file.write_bytes(b"test")

    import os

    original_getsize = os.path.getsize

    def mock_getsize(path):
        if "test.dmg" in path:
            raise OSError("permission denied")
        return original_getsize(path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os.path, "getsize", mock_getsize)
    try:
        result = downloader.is_release_complete(release)
        assert result is False
    finally:
        monkeypatch.undo()


def test_cleanup_old_versions_no_releases(downloader):
    """No releases should not raise error."""
    downloader.get_releases = Mock(return_value=[])
    downloader.cleanup_old_versions(keep_limit=3)


def test_cleanup_old_versions_with_cached_releases(downloader):
    """Cached releases should be used instead of fetching."""
    downloader.cleanup_prerelease_directories = Mock()
    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    downloader.cleanup_old_versions(keep_limit=3, cached_releases=releases)
    downloader.cleanup_prerelease_directories.assert_called_once()


def test_cleanup_old_versions_exception(downloader):
    """Exception during cleanup should be caught."""
    downloader.get_releases = Mock(side_effect=ValueError("test error"))
    downloader.cleanup_old_versions(keep_limit=3)


def test_cleanup_prerelease_directories_no_releases(downloader):
    """No releases should return early."""
    downloader.cleanup_prerelease_directories(cached_releases=[])


def test_cleanup_prerelease_directories_missing_desktop_dir(downloader):
    """Missing desktop directory should return early."""
    downloader.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    )


def test_cleanup_prerelease_directories_no_stable(downloader, tmp_path):
    """No stable releases should skip cleanup."""
    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    downloader.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.20-open.1", prerelease=True, assets=[])]
    )


def test_cleanup_prerelease_directories_removes_unexpected(downloader, tmp_path):
    """Unexpected entries should be removed, and keep expected version directory."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    # Create expected version directory
    new_version = desktop_dir / "v2.7.20"
    new_version.mkdir()
    (new_version / "test.txt").write_text("new")

    # Create unexpected (old) version directory
    old_version = desktop_dir / "v2.7.19"
    old_version.mkdir()
    (old_version / "test.txt").write_text("old")

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    downloader.cleanup_prerelease_directories(cached_releases=releases)

    # Expected version should exist, unexpected should be removed
    assert new_version.exists()
    assert not old_version.exists()


def test_cleanup_prerelease_directories_handles_invalid_keep_limit(
    downloader, tmp_path
):
    """Invalid keep_limit should use default."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_limit_override="invalid"
    )


def test_cleanup_prerelease_directories_sorts_by_iso_published_at_fallback(
    downloader, tmp_path, monkeypatch
):
    """When version tuples are unavailable, ISO published_at should drive sorting."""
    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)
    newest = desktop_dir / "v2.7.20"
    oldest = desktop_dir / "v2.7.19"
    newest.mkdir()
    oldest.mkdir()

    downloader.version_manager.get_release_tuple = Mock(return_value=None)
    monkeypatch.setattr(
        "fetchtastic.download.desktop._is_supported_desktop_release",
        lambda *_args, **_kwargs: True,
    )

    releases = [
        Release(
            tag_name="v2.7.20",
            prerelease=False,
            assets=[],
            published_at="2024-01-02T00:00:00Z",
        ),
        Release(
            tag_name="v2.7.19",
            prerelease=False,
            assets=[],
            published_at="2024-01-01T00:00:00Z",
        ),
    ]

    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_limit_override=1
    )

    assert newest.exists()
    assert not oldest.exists()


def test_cleanup_prerelease_directories_skips_symlinks(downloader, tmp_path):
    """Symlinks should be skipped during cleanup."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)
    link_target = tmp_path / "link_target"
    link_target.mkdir()
    symlink = desktop_dir / "v2.7.10"
    try:
        symlink.symlink_to(link_target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are not supported in this test environment")

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    downloader.cleanup_prerelease_directories(cached_releases=releases)

    assert symlink.exists()


def test_cleanup_prerelease_directories_exception(downloader, tmp_path):
    """Exception during cleanup should be caught."""
    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    downloader.version_manager.get_release_tuple.side_effect = ValueError("test")
    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]
    downloader.cleanup_prerelease_directories(cached_releases=releases)


def test_get_latest_release_tag_missing_file(downloader):
    """Missing tracking file should return None."""
    result = downloader.get_latest_release_tag()
    assert result is None


def test_get_latest_release_tag_with_file(downloader, tmp_path):
    """Existing tracking file should return stored version."""
    import json

    tracking_file = tmp_path / "cache" / "latest_desktop_release.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text(json.dumps({"latest_version": "v2.7.20"}))

    downloader.latest_release_path = str(tracking_file)
    result = downloader.get_latest_release_tag()
    assert result == "v2.7.20"


def test_get_latest_release_tag_invalid_json(downloader, tmp_path):
    """Invalid JSON should return None."""
    tracking_file = tmp_path / "cache" / "latest_desktop_release.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text("not valid json")

    downloader.latest_release_path = str(tracking_file)
    result = downloader.get_latest_release_tag()
    assert result is None


def test_get_latest_release_tag_non_mapping_json(downloader, tmp_path):
    """Valid non-object JSON tracking file should return None."""
    tracking_file = tmp_path / "cache" / "latest_desktop_release.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text('["v2.7.20"]', encoding="utf-8")

    downloader.latest_release_path = str(tracking_file)
    result = downloader.get_latest_release_tag()
    assert result is None


def test_update_latest_release_tag(downloader):
    """update_latest_release_tag should write tracking file."""
    downloader.cache_manager.atomic_write_json = Mock(return_value=True)
    result = downloader.update_latest_release_tag("v2.7.20")
    assert result is True
    downloader.cache_manager.atomic_write_json.assert_called_once()


def test_get_current_iso_timestamp(downloader):
    """Timestamp should be ISO format."""
    result = downloader._get_current_iso_timestamp()
    assert "T" in result


def test_handle_prereleases_disabled(downloader):
    """Disabled prereleases should return empty list."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = False
    releases = [Release(tag_name="v2.7.20-open.1", prerelease=True, assets=[])]
    result = downloader.handle_prereleases(releases)
    assert result == []


def test_handle_prereleases_filters_by_patterns(downloader):
    """Pattern filtering should be applied."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = ["*-open*"]
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value=None
    )
    downloader.version_manager.extract_clean_version.side_effect = (
        real_vm.extract_clean_version
    )

    releases = [
        Release(
            tag_name="v2.7.20-open.1",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
        Release(
            tag_name="v2.7.20-closed.1",
            prerelease=True,
            assets=[],
            published_at="2024-01-02",
        ),
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    result = downloader.handle_prereleases(releases)
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.20-open.1"


def test_handle_prereleases_with_expected_base(downloader):
    """Expected base version should filter prereleases."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = []
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value="2.7.21"
    )
    downloader.version_manager.extract_clean_version.side_effect = (
        real_vm.extract_clean_version
    )

    releases = [
        Release(
            tag_name="v2.7.21-open.1",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    result = downloader.handle_prereleases(releases)
    # Should only include prereleases matching expected base version 2.7.21
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.21-open.1"


def test_handle_prereleases_with_recent_commits(downloader):
    """Recent commits should filter prereleases by hash."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = []
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value="2.7.21"
    )
    downloader.version_manager.extract_clean_version.side_effect = (
        real_vm.extract_clean_version
    )

    releases = [
        Release(
            tag_name="v2.7.21-open.1-abc1234",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    recent_commits = [{"sha": "abc1234567890"}]
    result = downloader.handle_prereleases(releases, recent_commits=recent_commits)
    # Should match prerelease containing the commit hash abc1234
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.21-open.1-abc1234"


def test_get_latest_prerelease_tag_no_releases(downloader):
    """No releases should return None."""
    downloader.get_releases = Mock(return_value=[])
    result = downloader.get_latest_prerelease_tag()
    assert result is None


def test_get_latest_prerelease_tag_with_prereleases(downloader):
    """Should return newest prerelease that's newer than latest stable."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple
    downloader.get_releases = Mock(
        return_value=[
            Release(
                tag_name="v2.7.21-open.1",
                prerelease=True,
                assets=[],
                published_at="2024-01-02",
            ),
            Release(
                tag_name="v2.7.20",
                prerelease=False,
                assets=[],
                published_at="2024-01-01",
            ),
        ]
    )
    result = downloader.get_latest_prerelease_tag()
    assert result == "v2.7.21-open.1"


def test_get_latest_prerelease_tag_treats_tag_prerelease_as_prerelease(downloader):
    """Prerelease-like tags should be treated as prereleases even when flag is false."""
    real_vm = VersionManager()
    downloader.get_releases = Mock(
        return_value=[
            Release(
                tag_name="v2.7.20",
                prerelease=False,
                published_at="2025-01-01T00:00:00Z",
            ),
            Release(
                tag_name="v2.7.21-open.1",
                prerelease=False,
                published_at="2025-01-02T00:00:00Z",
            ),
        ]
    )
    downloader.version_manager.get_release_tuple = Mock(
        side_effect=real_vm.get_release_tuple
    )

    result = downloader.get_latest_prerelease_tag()

    assert result == "v2.7.21-open.1"


def test_get_prerelease_tracking_file(downloader):
    """Should return path to prerelease tracking file."""
    result = downloader.get_prerelease_tracking_file()
    assert "prerelease" in result.lower()


def test_update_prerelease_tracking(downloader):
    """Should write prerelease tracking data."""
    downloader.cache_manager.atomic_write_json = Mock(return_value=True)
    real_vm = VersionManager()
    downloader.version_manager.get_prerelease_metadata_from_version.side_effect = (
        real_vm.get_prerelease_metadata_from_version
    )
    result = downloader.update_prerelease_tracking("v2.7.20-open.1")
    assert result is True


def test_validate_extraction_patterns(downloader):
    """Extraction patterns should always return False for Desktop."""
    result = downloader.validate_extraction_patterns(["*.zip"], [])
    assert result is False


def test_check_extraction_needed(downloader):
    """Extraction check should always return False for Desktop."""
    result = downloader.check_extraction_needed("/path/to/file", "/extract/dir", [], [])
    assert result is False


def test_should_download_prerelease_disabled(downloader):
    """Disabled prereleases should return False."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = False
    result = downloader.should_download_prerelease("v2.7.20-open.1")
    assert result is False


def test_handle_prereleases_coerces_string_false_config(downloader):
    """String config values like 'false' should disable desktop prereleases."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = "false"

    releases = [Release(tag_name="v2.7.20-open.1", prerelease=True, assets=[])]
    result = downloader.handle_prereleases(releases)

    assert result == []


def test_should_download_prerelease_coerces_string_false_config(downloader):
    """String config values like '0' should disable prerelease downloads."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = "0"

    result = downloader.should_download_prerelease("v2.7.20-open.1")

    assert result is False


def test_should_download_prerelease_no_tracking_file(downloader, tmp_path):
    """No tracking file should return True (download)."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))

    result = downloader.should_download_prerelease("v2.7.20-open.1")
    assert result is True


def test_should_download_prerelease_newer_version(downloader, tmp_path):
    """Newer prerelease should return True."""
    import json

    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text(json.dumps({"latest_version": "v2.7.20-open.1"}))

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(
        return_value={"latest_version": "v2.7.20-open.1"}
    )
    real_vm = VersionManager()
    downloader.version_manager.compare_versions.side_effect = real_vm.compare_versions

    result = downloader.should_download_prerelease("v2.7.20-open.2")
    assert result is True


def test_should_download_prerelease_same_version(downloader, tmp_path):
    """Same version should return False."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text('{"latest_version": "v2.7.20-open.1"}')

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(
        return_value={"latest_version": "v2.7.20-open.1"}
    )
    downloader.version_manager.compare_versions = Mock(return_value=0)

    result = downloader.should_download_prerelease("v2.7.20-open.1")
    assert result is False


def test_should_download_prerelease_read_error(downloader, tmp_path):
    """Read error should default to True (download)."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text("invalid json")

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(side_effect=ValueError("invalid json"))

    result = downloader.should_download_prerelease("v2.7.20-open.1")
    assert result is True


def test_get_current_tracked_prerelease_tag_non_mapping_returns_none(
    downloader, tmp_path
):
    """Non-object JSON from cache manager should be treated as missing tracking."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text("[]", encoding="utf-8")

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(return_value=["v2.7.20-open.1"])

    assert downloader.get_current_tracked_prerelease_tag() is None


def test_manage_prerelease_tracking_files_disabled(downloader):
    """Disabled prereleases should return early."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = False
    downloader.manage_prerelease_tracking_files()


def test_manage_prerelease_tracking_files_coerces_string_false_config(downloader):
    """String config values like 'false' should skip prerelease tracking maintenance."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = "false"
    downloader.get_releases = Mock()

    downloader.manage_prerelease_tracking_files()

    downloader.get_releases.assert_not_called()


def test_manage_prerelease_tracking_files_missing_dir(downloader):
    """Missing tracking directory should return early."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.get_prerelease_tracking_file = Mock(
        return_value="/nonexistent/path.json"
    )
    downloader.manage_prerelease_tracking_files()


def test_manage_prerelease_tracking_files_with_files(downloader, tmp_path):
    """Should process existing tracking files."""
    import json

    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True

    tracking_dir = tmp_path / "cache"
    tracking_dir.mkdir(parents=True)
    tracking_file = tracking_dir / "prerelease_desktop.json"
    tracking_file.write_text(
        json.dumps(
            {
                "latest_version": "v2.7.20-open.1",
                "base_version": "2.7.20",
            }
        )
    )

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(
        return_value={
            "latest_version": "v2.7.20-open.1",
            "base_version": "2.7.20",
        }
    )
    downloader.get_releases = Mock(return_value=[])
    downloader.handle_prereleases = Mock(return_value=[])

    downloader.manage_prerelease_tracking_files()


def test_is_desktop_prerelease_by_name_open():
    """Tags with -open should be prerelease."""
    from fetchtastic.download.desktop import _is_desktop_prerelease_by_name

    assert _is_desktop_prerelease_by_name("v2.7.20-open.1") is True


def test_is_desktop_prerelease_by_name_closed():
    """Tags with -closed should be prerelease."""
    from fetchtastic.download.desktop import _is_desktop_prerelease_by_name

    assert _is_desktop_prerelease_by_name("v2.7.20-closed.1") is True


def test_is_desktop_prerelease_by_name_internal():
    """Tags with -internal should be prerelease."""
    from fetchtastic.download.desktop import _is_desktop_prerelease_by_name

    assert _is_desktop_prerelease_by_name("v2.7.20-internal.1") is True


def test_is_desktop_prerelease_by_name_stable():
    """Stable tags should not be prerelease."""
    from fetchtastic.download.desktop import _is_desktop_prerelease_by_name

    assert _is_desktop_prerelease_by_name("v2.7.20") is False


def test_is_desktop_prerelease_by_name_empty():
    """Empty tag should not be prerelease."""
    from fetchtastic.download.desktop import _is_desktop_prerelease_by_name

    assert _is_desktop_prerelease_by_name("") is False


def test_is_supported_desktop_release_newer():
    """Newer versions should be supported."""
    from fetchtastic.download.desktop import _is_supported_desktop_release

    assert _is_supported_desktop_release("v2.7.20") is True


def test_is_supported_desktop_release_older():
    """Older versions should not be supported."""
    from fetchtastic.download.desktop import _is_supported_desktop_release

    assert _is_supported_desktop_release("v2.7.10") is False


def test_is_supported_desktop_release_unparsable():
    """Unparsable versions should be allowed through."""
    from fetchtastic.download.desktop import _is_supported_desktop_release

    assert _is_supported_desktop_release("unknown-format") is True


def test_is_desktop_prerelease_function():
    """_is_desktop_prerelease should check both tag and prerelease flag."""
    from fetchtastic.download.desktop import _is_desktop_prerelease

    assert _is_desktop_prerelease({"tag_name": "v2.7.20-open.1"}) is True
    assert _is_desktop_prerelease({"tag_name": "v2.7.20", "prerelease": True}) is True
    assert _is_desktop_prerelease({"tag_name": "v2.7.20", "prerelease": False}) is False
    assert _is_desktop_prerelease({}) is False


def test_update_release_history_no_log_summary(downloader):
    """log_summary=False should skip logging."""
    downloader.release_history_manager.update_release_history = Mock(
        return_value={"releases": []}
    )
    downloader.release_history_manager.log_release_status_summary = Mock()
    releases = [
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    result = downloader.update_release_history(releases, log_summary=False)
    assert result == {"releases": []}
    downloader.release_history_manager.log_release_status_summary.assert_not_called()


def test_get_releases_asset_creation_fails(downloader):
    """When asset creation returns None, it should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.20",
                "prerelease": False,
                "assets": [
                    {
                        "name": "test.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
        ]
    )
    from unittest.mock import patch

    with patch(
        "fetchtastic.download.desktop.create_asset_from_github_data",
        return_value=None,
    ):
        result = downloader.get_releases(limit=10)
        # Release should be skipped because no valid assets
        assert result == []


def test_get_releases_no_assets_after_filtering(downloader):
    """Release with no assets after filtering should be skipped."""
    downloader.github_source.fetch_raw_releases_data = Mock(
        return_value=[
            {
                "tag_name": "v2.7.20",
                "prerelease": False,
                "assets": [
                    {
                        "name": "source.zip",
                        "browser_download_url": "http://example.com/source.zip",
                        "size": 100,
                    }
                ],
            }
        ]
    )
    from unittest.mock import patch

    with patch(
        "fetchtastic.download.desktop.create_asset_from_github_data",
        return_value=None,
    ):
        result = downloader.get_releases(limit=10)
        assert result == []


def test_get_releases_expands_scan_window(downloader):
    """Should expand scan window (fetch next page) when not enough stable releases found."""
    # With higher keep value, scan_count will be larger, allowing us to test expansion
    downloader.config["DESKTOP_VERSIONS_TO_KEEP"] = 10

    # With keep=10: scan_count = min(100, max(10*2, 10)) = 20
    # First call returns 20 prereleases (all >= 2.7.14, no stable) to trigger page increment
    # Second call returns stable releases (without -open suffix)
    call_tracker = {"count": 0}

    def mock_fetch_side_effect(*args, **kwargs):
        call_tracker["count"] += 1
        params = args[0] if args else kwargs.get("params", {})
        params.get("page", 1)

        if call_tracker["count"] == 1:
            # First call (page=1): return 20 prereleases (with -open suffix), all >= 2.7.14
            # Since stable_count (0) < min_stable_releases (10) and we got a full page,
            # the code will increment page and fetch again
            return [
                {
                    "tag_name": f"v2.7.{33 - i}-open.1",
                    "prerelease": True,
                    "assets": [
                        {
                            "name": "test.dmg",
                            "browser_download_url": "http://example.com/test.dmg",
                            "size": 100,
                        }
                    ],
                }
                for i in range(20)
            ]
        else:
            # Second call (page=2): return stable releases (no -open suffix), all >= 2.7.14
            return [
                {
                    "tag_name": f"v2.7.{23 - i}",
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "test.dmg",
                            "browser_download_url": "http://example.com/test.dmg",
                            "size": 100,
                        }
                    ],
                }
                for i in range(10)
            ]

    mock_fetch = Mock(side_effect=mock_fetch_side_effect)
    downloader.github_source.fetch_raw_releases_data = mock_fetch

    result = downloader.get_releases()

    # Verify fetch was called twice (scan window expansion via page increment)
    assert mock_fetch.call_count == 2

    # Verify the first call used page=1
    first_call_args = mock_fetch.call_args_list[0]
    assert first_call_args[0][0]["page"] == 1
    assert first_call_args[0][0]["per_page"] == 20

    # Verify the second call used page=2 (expanded scan window)
    second_call_args = mock_fetch.call_args_list[1]
    assert second_call_args[0][0]["page"] == 2
    assert second_call_args[0][0]["per_page"] == 20

    # Total releases: 20 prereleases + 10 stable = 30
    # The function returns when stable_count >= min_stable_releases (10)
    assert len(result) == 30

    # Verify stable releases were found
    stable_releases = [r for r in result if not r.prerelease]
    assert len(stable_releases) == 10


def test_get_releases_stops_on_repeated_page_payload(downloader):
    """Repeated full-page payloads should stop pagination to avoid endless scans."""

    repeated_page = [
        {
            "tag_name": f"v2.7.{40 - i}-open.1",
            "prerelease": True,
            "assets": [
                {
                    "name": "Meshtastic.dmg",
                    "browser_download_url": "http://example.com/test.dmg",
                    "size": 100,
                }
            ],
        }
        for i in range(RELEASE_SCAN_COUNT)
    ]

    mock_fetch = Mock(side_effect=[repeated_page, repeated_page])
    downloader.github_source.fetch_raw_releases_data = mock_fetch

    result = downloader.get_releases()

    assert mock_fetch.call_count == 2
    assert len(result) == RELEASE_SCAN_COUNT
    assert all(r.prerelease for r in result)


def test_get_releases_stops_at_configured_max_pages(downloader):
    """Pagination should stop at configured max pages when stable target isn't reached."""
    downloader.config["DESKTOP_RELEASE_SCAN_MAX_PAGES"] = 3

    def _page(page_num: int) -> list[dict]:
        return [
            {
                "tag_name": f"v2.7.{70 - (page_num * 10) - i}-open.1",
                "prerelease": True,
                "assets": [
                    {
                        "name": "Meshtastic.dmg",
                        "browser_download_url": "http://example.com/test.dmg",
                        "size": 100,
                    }
                ],
            }
            for i in range(RELEASE_SCAN_COUNT)
        ]

    mock_fetch = Mock(side_effect=[_page(1), _page(2), _page(3), _page(4)])
    downloader.github_source.fetch_raw_releases_data = mock_fetch

    result = downloader.get_releases()

    assert mock_fetch.call_count == 3
    assert len(result) == RELEASE_SCAN_COUNT * 3
    assert all(r.prerelease for r in result)


def test_cleanup_prerelease_directories_no_expected_stable(
    downloader, tmp_path, mocker
):
    """Empty expected_stable with keep_limit > 0 should log warning and return."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)
    mock_logger = mocker.patch("fetchtastic.download.desktop.logger")
    mocker.patch(
        "fetchtastic.download.desktop._sanitize_path_component", return_value=None
    )

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]

    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_limit_override=1
    )
    assert any(
        "no safe release tags found to keep" in str(call.args[0]).lower()
        for call in mock_logger.warning.call_args_list
    )


def test_cleanup_prerelease_directories_scandir_filenotfound(
    downloader, tmp_path, mocker
):
    """FileNotFoundError during scandir should be handled gracefully."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]

    real_scandir = os.scandir

    def _scandir(path):
        if os.fspath(path) == str(desktop_dir):
            raise FileNotFoundError("desktop dir disappeared")
        return real_scandir(path)

    mocker.patch("fetchtastic.download.desktop.os.scandir", side_effect=_scandir)
    downloader.cleanup_prerelease_directories(cached_releases=releases)


def test_cleanup_prerelease_directories_keep_limit_zero(downloader, tmp_path):
    """keep_limit=0 should skip the expected_stable intersection check."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    # Create unexpected version directory
    old_version = desktop_dir / "v2.7.19"
    old_version.mkdir()

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]

    # With keep_limit=0, the intersection check is skipped
    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_limit_override=0
    )


def test_cleanup_prerelease_directories_mismatch_warning(downloader, tmp_path):
    """Mismatch between expected and existing entries should log warning."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    # Create a version directory that doesn't match expected
    other_version = desktop_dir / "v2.7.15"
    other_version.mkdir()

    # Expected is v2.7.20, but existing is v2.7.15 - no intersection
    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]

    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_limit_override=3
    )


def test_cleanup_prerelease_directories_unsafe_tags(downloader, tmp_path):
    """Unsafe tags during cleanup should be logged and skipped."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    # Create a release that will result in None when sanitized
    releases = [Release(tag_name="../../../etc/passwd", prerelease=False, assets=[])]

    downloader.cleanup_prerelease_directories(cached_releases=releases)


def test_cleanup_prerelease_directories_remove_unexpected_filenotfound(
    downloader, tmp_path, mocker
):
    """FileNotFoundError in _remove_unexpected_entries should return quietly."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple

    desktop_dir = tmp_path / "downloads" / APP_DIR_NAME / DESKTOP_DIR_NAME
    desktop_dir.mkdir(parents=True)

    # Create prerelease dir
    prerelease_dir = desktop_dir / DESKTOP_PRERELEASES_DIR_NAME
    prerelease_dir.mkdir()
    # Ensure expected_stable intersects existing entries so cleanup reaches prerelease scan.
    (desktop_dir / "v2.7.20").mkdir()

    releases = [Release(tag_name="v2.7.20", prerelease=False, assets=[])]

    real_scandir = os.scandir

    def _scandir(path):
        if os.fspath(path) == str(prerelease_dir):
            raise FileNotFoundError("prerelease dir disappeared")
        return real_scandir(path)

    mocker.patch("fetchtastic.download.desktop.os.scandir", side_effect=_scandir)
    downloader.cleanup_prerelease_directories(cached_releases=releases)


def test_handle_prereleases_no_latest_release(downloader):
    """handle_prereleases when no latest stable release exists."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = []
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value=None
    )
    downloader.version_manager.extract_clean_version.side_effect = (
        real_vm.extract_clean_version
    )

    # Only prereleases, no stable release
    releases = [
        Release(
            tag_name="v2.7.20-open.1",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
    ]
    result = downloader.handle_prereleases(releases)
    # Should still return prereleases even without stable release
    assert len(result) == 1


def test_handle_prereleases_extract_clean_version_fails(downloader):
    """handle_prereleases when extract_clean_version returns empty."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = []
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value="2.7.21"
    )
    # Simulate failing to extract clean version
    downloader.version_manager.extract_clean_version = Mock(return_value="")

    releases = [
        Release(
            tag_name="v2.7.21-open.1",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    result = downloader.handle_prereleases(releases)
    # Prerelease should be preserved when clean_version is empty
    assert len(result) == 1


def test_handle_prereleases_filtered_by_commits_empty_result(downloader):
    """handle_prereleases when commit filtering returns empty list."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    downloader.config["DESKTOP_PRERELEASE_INCLUDE_PATTERNS"] = []
    downloader.config["DESKTOP_PRERELEASE_EXCLUDE_PATTERNS"] = []

    real_vm = VersionManager()
    downloader.version_manager.filter_prereleases_by_pattern.side_effect = (
        real_vm.filter_prereleases_by_pattern
    )
    downloader.version_manager.calculate_expected_prerelease_version = Mock(
        return_value="2.7.21"
    )
    downloader.version_manager.extract_clean_version.side_effect = (
        real_vm.extract_clean_version
    )

    releases = [
        Release(
            tag_name="v2.7.21-open.1-abc1234",
            prerelease=True,
            assets=[],
            published_at="2024-01-01",
        ),
        Release(tag_name="v2.7.20", prerelease=False, assets=[]),
    ]
    # Provide commits that don't match any prerelease tags
    recent_commits = [{"sha": "xyz9999"}]
    result = downloader.handle_prereleases(releases, recent_commits=recent_commits)
    # When no commits match, should fall back to all prereleases matching expected base
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.21-open.1-abc1234"


def test_get_latest_prerelease_tag_no_stable(downloader):
    """get_latest_prerelease_tag with no stable release and older prerelease."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple
    downloader.get_releases = Mock(
        return_value=[
            Release(
                tag_name="v2.7.19-open.1",
                prerelease=True,
                assets=[],
                published_at="2024-01-01",
            ),
        ]
    )
    result = downloader.get_latest_prerelease_tag()
    # No stable release, prerelease should be returned
    assert result == "v2.7.19-open.1"


def test_get_latest_prerelease_tag_none_tuple(downloader):
    """get_latest_prerelease_tag when get_release_tuple returns None."""
    downloader.version_manager.get_release_tuple = Mock(return_value=None)
    downloader.get_releases = Mock(
        return_value=[
            Release(
                tag_name="v2.7.21-open.1",
                prerelease=True,
                assets=[],
                published_at="2024-01-02",
            ),
            Release(
                tag_name="v2.7.20",
                prerelease=False,
                assets=[],
                published_at="2024-01-01",
            ),
        ]
    )
    result = downloader.get_latest_prerelease_tag()
    # When tuple is None, prerelease should be returned
    assert result == "v2.7.21-open.1"


def test_get_latest_prerelease_tag_no_matching_prerelease(downloader):
    """get_latest_prerelease_tag when no prerelease is newer than stable."""
    real_vm = VersionManager()
    downloader.version_manager.get_release_tuple.side_effect = real_vm.get_release_tuple
    downloader.get_releases = Mock(
        return_value=[
            Release(
                tag_name="v2.7.19-open.1",
                prerelease=True,
                assets=[],
                published_at="2024-01-01",
            ),
            Release(
                tag_name="v2.7.20",
                prerelease=False,
                assets=[],
                published_at="2024-01-02",
            ),
        ]
    )
    result = downloader.get_latest_prerelease_tag()
    # Prerelease v2.7.19 is older than stable v2.7.20, should return None
    assert result is None


def test_should_download_prerelease_older_version(downloader, tmp_path):
    """Older prerelease should return False (don't download)."""
    import json

    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True
    tracking_file = tmp_path / "cache" / "prerelease_desktop.json"
    tracking_file.parent.mkdir(parents=True)
    tracking_file.write_text(json.dumps({"latest_version": "v2.7.20-open.2"}))

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.cache_manager.read_json = Mock(
        return_value={"latest_version": "v2.7.20-open.2"}
    )
    real_vm = VersionManager()
    downloader.version_manager.compare_versions.side_effect = real_vm.compare_versions

    result = downloader.should_download_prerelease("v2.7.20-open.1")
    assert result is False


def test_manage_prerelease_tracking_files_read_error(downloader, tmp_path):
    """manage_prerelease_tracking_files should handle read errors gracefully."""
    import json

    from fetchtastic.download.cache import CacheManager

    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True

    tracking_dir = tmp_path / "cache"
    tracking_dir.mkdir(parents=True)

    # Create a tracking file with invalid JSON that will cause read_json to fail
    tracking_file = tracking_dir / "prerelease_desktop.json"
    tracking_file.write_text("invalid json content")

    # Also create a secondary tracking file with valid JSON
    secondary_file = tracking_dir / "prerelease_secondary.json"
    secondary_file.write_text(
        json.dumps(
            {
                "latest_version": "v2.7.20-open.1",
                "base_version": "2.7.20",
            }
        )
    )

    # Use a real CacheManager to test actual file reading behavior
    real_cache_manager = CacheManager(str(tracking_dir))
    downloader.cache_manager = real_cache_manager

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.get_releases = Mock(return_value=[])
    downloader.handle_prereleases = Mock(return_value=[])

    # Should not raise exception - the ValueError from invalid JSON is caught internally
    downloader.manage_prerelease_tracking_files()


def test_manage_prerelease_tracking_files_filenotfound(downloader, tmp_path):
    """manage_prerelease_tracking_files should handle FileNotFoundError."""
    downloader.config["CHECK_DESKTOP_PRERELEASES"] = True

    tracking_dir = tmp_path / "cache"
    tracking_dir.mkdir(parents=True)
    tracking_file = tracking_dir / "prerelease_desktop.json"

    downloader.get_prerelease_tracking_file = Mock(return_value=str(tracking_file))
    downloader.get_releases = Mock(return_value=[])
    downloader.handle_prereleases = Mock(return_value=[])

    # No tracking file exists - FileNotFoundError should be handled
    downloader.manage_prerelease_tracking_files()
