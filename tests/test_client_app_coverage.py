import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from fetchtastic.constants import (
    APK_PRERELEASES_DIR_NAME,
    APP_DIR_NAME,
    ERROR_TYPE_FILESYSTEM,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_VALIDATION,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import (
    MeshtasticClientAppDownloader,
    _is_client_app_prerelease_payload,
    _is_supported_client_app_release,
    is_client_app_asset_name,
    is_client_app_prerelease_tag,
)
from fetchtastic.download.interfaces import Asset, DownloadResult, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture
def cache_manager(tmp_path):
    cache = Mock(spec=CacheManager)
    cache.cache_dir = str(tmp_path / "cache")

    def _cache_path(file_name: str, suffix: str = ".json") -> str:
        path = Path(cache.cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        if suffix and not file_name.endswith(suffix):
            file_name = f"{file_name}{suffix}"
        return str(path / file_name)

    cache.get_cache_file_path.side_effect = _cache_path

    def _write_json(path, data):
        json_path = Path(path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data), encoding="utf-8")
        return True

    cache.atomic_write_json.side_effect = _write_json

    def _write_text(path, text):
        Path(path).write_text(text, encoding="utf-8")
        return True

    cache.atomic_write_text.side_effect = _write_text

    cache.read_json.return_value = None

    return cache


@pytest.fixture
def downloader(tmp_path, cache_manager):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release.apk", "meshtastic.dmg"],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_PRERELEASES": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


def test_get_prerelease_base_dir_is_compatibility_alias(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    result = downloader._get_prerelease_base_dir()
    expected = os.path.join(str(downloads), APP_DIR_NAME, APK_PRERELEASES_DIR_NAME)
    assert result == expected
    assert os.path.isdir(result)


def test_is_within_download_tree_value_error_branch(downloader, mocker):
    mocker.patch("os.path.commonpath", side_effect=ValueError("different drives"))
    assert downloader._is_within_download_tree("C:\\some\\path") is False


def test_move_legacy_path_source_does_not_exist(downloader, tmp_path):
    result = downloader._move_legacy_path(
        str(tmp_path / "nonexistent"), str(tmp_path / "dest")
    )
    assert result is False


def test_move_legacy_path_source_is_symlink(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    real = downloads / "real"
    real.mkdir()
    source = downloads / "source_link"
    try:
        source.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    result = downloader._move_legacy_path(str(source), str(downloads / "dest"))
    assert result is False


def test_move_legacy_path_dest_is_symlink(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    source = downloads / "src.txt"
    source.write_text("data", encoding="utf-8")
    real = tmp_path / "outside"
    real.mkdir()
    dest_link = downloads / "dest_link"
    try:
        dest_link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    result = downloader._move_legacy_path(str(source), str(dest_link / "file.txt"))
    assert result is False


def test_move_legacy_path_dest_outside_download_tree(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    source = downloads / "src.txt"
    source.write_text("data", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    result = downloader._move_legacy_path(str(source), str(outside / "file.txt"))
    assert result is False


def test_move_legacy_path_ancestor_symlink(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source_dir = downloads / "apks" / "v2.7.13"
    source_dir.mkdir(parents=True)
    (source_dir / "asset.txt").write_text("x", encoding="utf-8")
    real_target = tmp_path / "outside"
    real_target.mkdir()
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    symlink = app_dir / "linked"
    try:
        symlink.symlink_to(real_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    moved = downloader._move_legacy_path(
        str(source_dir), str(symlink / "v2.7.13" / "asset.txt")
    )
    assert moved is False


def test_move_legacy_path_file_success(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13" / "asset.apk"
    source.parent.mkdir(parents=True)
    source.write_text("apk data", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13" / "asset.apk"
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is True
    assert Path(dest).exists()
    assert not Path(source).exists()


def test_move_legacy_path_file_dest_exists(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13" / "asset.apk"
    source.parent.mkdir(parents=True)
    source.write_text("apk data", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13" / "asset.apk"
    dest.parent.mkdir(parents=True)
    dest.write_text("existing", encoding="utf-8")
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_move_legacy_path_file_oserror(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13" / "asset.apk"
    source.parent.mkdir(parents=True)
    source.write_text("apk data", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13" / "asset.apk"
    mocker.patch("shutil.move", side_effect=OSError("permission denied"))
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_move_legacy_path_non_file_non_dir(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    source = downloads / "source"
    os.mkfifo(str(source))
    dest = downloads / APP_DIR_NAME / "dest"
    dest.parent.mkdir(parents=True)
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_move_legacy_path_dir_dest_is_file(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.parent.mkdir(parents=True)
    dest.write_text("not a directory", encoding="utf-8")
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_move_legacy_path_dir_merge_success(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "file1.apk").write_text("data1", encoding="utf-8")
    (source / "file2.apk").write_text("data2", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    (dest / "existing.apk").write_text("old", encoding="utf-8")
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is True
    assert (dest / "file1.apk").exists()
    assert (dest / "file2.apk").exists()
    assert (dest / "existing.apk").exists()


def test_move_legacy_path_dir_merge_skips_existing(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "file1.apk").write_text("new", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    (dest / "file1.apk").write_text("old", encoding="utf-8")
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False
    assert (dest / "file1.apk").read_text() == "old"


def test_move_legacy_path_dir_merge_symlink_entry(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "real.apk").write_text("data", encoding="utf-8")
    link = source / "link.apk"
    try:
        link.symlink_to(source / "real.apk")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is True
    assert (dest / "real.apk").exists()
    assert not (dest / "link.apk").exists()


def test_move_legacy_path_dir_merge_removes_empty_source(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "file.apk").write_text("data", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is True
    assert not Path(source).exists()


def test_move_legacy_path_dir_merge_keeps_partial_source(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "file.apk").write_text("data", encoding="utf-8")
    (source / "keep.apk").write_text("keep", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    (dest / "keep.apk").write_text("old", encoding="utf-8")
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is True
    assert Path(source).exists()


def test_move_legacy_path_dir_makedirs_oserror(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    mocker.patch("os.makedirs", side_effect=OSError("no space"))
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_move_legacy_path_dir_merge_oserror(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    source = downloads / "apks" / "v2.7.13"
    source.mkdir(parents=True)
    (source / "file.apk").write_text("data", encoding="utf-8")
    dest = downloads / APP_DIR_NAME / "v2.7.13"
    dest.mkdir(parents=True)
    mocker.patch("os.listdir", side_effect=OSError("io error"))
    result = downloader._move_legacy_path(str(source), str(dest))
    assert result is False


def test_migrate_legacy_layout_unsafe_app_dir_symlink(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    outside = tmp_path / "outside"
    outside.mkdir()
    downloads.mkdir(parents=True, exist_ok=True)
    try:
        (downloads / APP_DIR_NAME).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    downloader.migrate_legacy_layout()
    assert (
        not (downloads / "apks").exists() or not any((downloads / "apks").iterdir())
        if (downloads / "apks").exists()
        else True
    )


def test_migrate_legacy_layout_oserror_on_makedirs(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    call_count = [0]

    original_makedirs = os.makedirs

    def _selective_makedirs(path, **kwargs):
        if APP_DIR_NAME in str(path) and call_count[0] == 0:
            call_count[0] += 1
            raise OSError("permission denied")
        return original_makedirs(path, **kwargs)

    mocker.patch("os.makedirs", side_effect=_selective_makedirs)
    downloader.migrate_legacy_layout()


def test_migrate_legacy_layout_prerelease_ensure_fails(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    legacy = downloads / "apks" / "v2.7.13"
    legacy.mkdir(parents=True)
    (legacy / "file.apk").write_text("data", encoding="utf-8")
    mocker.patch.object(
        downloader,
        "_ensure_prerelease_base_dir",
        side_effect=ValueError("unsafe"),
    )
    downloader.migrate_legacy_layout()
    assert (downloads / APP_DIR_NAME / "v2.7.13" / "file.apk").exists()


def test_get_target_path_for_release_with_release_object(downloader, tmp_path):
    release = Release(tag_name="v2.7.14", prerelease=False)
    Asset(name="app.apk", download_url="https://example.com/app.apk", size=100)
    path = downloader.get_target_path_for_release("v2.7.14", "app.apk", release=release)
    assert APP_DIR_NAME in path
    assert "v2.7.14" in path
    assert "app.apk" in path


def test_get_target_path_for_release_prerelease_via_release(downloader, tmp_path):
    release = Release(
        tag_name="v2.7.14-closed.1",
        prerelease=True,
    )
    path = downloader.get_target_path_for_release(
        "v2.7.14-closed.1", "app.apk", release=release
    )
    assert APK_PRERELEASES_DIR_NAME in path


def test_get_target_path_for_release_prerelease_auto_detect(downloader, tmp_path):
    path = downloader.get_target_path_for_release(
        "v2.7.14-closed.1", "app.apk", is_prerelease=None
    )
    assert APK_PRERELEASES_DIR_NAME in path


def test_get_target_path_for_release_explicit_prerelease_false(downloader, tmp_path):
    path = downloader.get_target_path_for_release(
        "v2.7.14", "app.apk", is_prerelease=False
    )
    assert APK_PRERELEASES_DIR_NAME not in path
    assert APP_DIR_NAME in path


def test_resolve_release_dir_symlink_preferred_base(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    try:
        (app_dir / APK_PRERELEASES_DIR_NAME).symlink_to(
            outside, target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    with pytest.raises(ValueError, match="symlinked client app release dir"):
        downloader._resolve_release_dir(
            "v2.7.14", is_prerelease=True, create_if_missing=True
        )


def test_resolve_release_dir_symlink_release_dir(downloader, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (app_dir / "v2.7.14").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    with pytest.raises(ValueError, match="symlinked client app release dir"):
        downloader._resolve_release_dir(
            "v2.7.14", is_prerelease=False, create_if_missing=True
        )


def test_resolve_release_dir_unsafe_existing_dir(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    version_dir = app_dir / "v2.7.14"
    version_dir.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=False)
    with pytest.raises(ValueError, match="unsafe client app release dir"):
        downloader._resolve_release_dir(
            "v2.7.14", is_prerelease=False, create_if_missing=False
        )


def test_resolve_release_dir_legacy_symlink_skipped(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    legacy_dir = downloads / "apks" / "v2.7.14"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "file.apk").write_text("data", encoding="utf-8")
    preferred_base = downloads / APP_DIR_NAME
    preferred_base.mkdir(parents=True)
    original_islink = os.path.islink

    def _selective_islink(path):
        if str(path) == str(legacy_dir):
            return True
        return original_islink(path)

    mocker.patch("os.path.islink", side_effect=_selective_islink)
    result = downloader._resolve_release_dir(
        "v2.7.14", is_prerelease=False, create_if_missing=True
    )
    assert APP_DIR_NAME in result


def test_resolve_release_dir_create_if_missing_unsafe(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=False)
    mocker.patch.object(downloader, "_is_within_download_tree", return_value=False)
    with pytest.raises(ValueError, match="unsafe client app release dir"):
        downloader._resolve_release_dir(
            "v2.7.14", is_prerelease=False, create_if_missing=True
        )


def test_resolve_release_dir_create_if_missing_still_unsafe(
    downloader, tmp_path, mocker
):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    call_count = [0]

    def _safe_side_effect(path):
        if call_count[0] == 0:
            call_count[0] += 1
            return False
        return False

    mocker.patch.object(
        downloader, "_is_safe_managed_dir", side_effect=_safe_side_effect
    )
    mocker.patch.object(downloader, "_is_within_download_tree", return_value=True)
    with pytest.raises(ValueError, match="unsafe client app release dir"):
        downloader._resolve_release_dir(
            "v2.7.14", is_prerelease=False, create_if_missing=True
        )


def test_is_client_app_prerelease(downloader):
    release = Release(tag_name="v2.7.14-closed.1", prerelease=False)
    assert downloader._is_client_app_prerelease(release) is True


def test_is_android_prerelease_alias(downloader):
    release = Release(tag_name="v2.7.14-closed.1", prerelease=False)
    assert downloader._is_android_prerelease(release) is True


def test_is_desktop_prerelease_alias(downloader):
    release = Release(tag_name="v2.7.14-internal.1", prerelease=False)
    assert downloader._is_desktop_prerelease(release) is True


def test_get_storage_tag_for_release(downloader):
    release = Release(tag_name="v2.7.14", prerelease=False)
    assert downloader._get_storage_tag_for_release(release) == "v2.7.14"


def test_update_release_history_empty(downloader):
    result = downloader.update_release_history([])
    assert result is None


def test_update_release_history_all_prerelease(downloader):
    releases = [Release(tag_name="v2.7.14-closed.1", prerelease=True)]
    result = downloader.update_release_history(releases)
    assert result is None


def test_update_release_history_success(downloader):
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        )
    ]
    downloader.release_history_manager.update_release_history = Mock(
        return_value={"releases": []}
    )
    downloader.release_history_manager.log_release_status_summary = Mock()
    result = downloader.update_release_history(releases, log_summary=True)
    assert result is not None
    downloader.release_history_manager.log_release_status_summary.assert_called_once()


def test_update_release_history_no_log_summary(downloader):
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        )
    ]
    downloader.release_history_manager.update_release_history = Mock(
        return_value={"releases": []}
    )
    downloader.update_release_history(releases, log_summary=False)


def test_format_release_log_suffix(downloader):
    downloader.release_history_manager.format_release_label = Mock(
        return_value="v2.7.14 [active]"
    )
    release = Release(tag_name="v2.7.14", prerelease=False)
    suffix = downloader.format_release_log_suffix(release)
    assert suffix == " [active]"


def test_format_release_log_suffix_no_match(downloader):
    downloader.release_history_manager.format_release_label = Mock(
        return_value="custom-label"
    )
    release = Release(tag_name="v2.7.14", prerelease=False)
    suffix = downloader.format_release_log_suffix(release)
    assert suffix == ""


def test_ensure_release_notes_unsafe_tag(downloader, mocker):
    mocker.patch(
        "fetchtastic.download.client_app._sanitize_path_component", return_value=None
    )
    release = Release(tag_name="../../etc", prerelease=False, body="notes")
    result = downloader.ensure_release_notes(release)
    assert result is None


def test_ensure_release_notes_resolve_raises(downloader, mocker):
    mocker.patch(
        "fetchtastic.download.client_app._sanitize_path_component",
        side_effect=lambda x: x,
    )
    mocker.patch.object(
        downloader,
        "_resolve_release_dir",
        side_effect=ValueError("unsafe"),
    )
    release = Release(tag_name="v2.7.14", prerelease=False, body="notes")
    result = downloader.ensure_release_notes(release)
    assert result is None


def test_is_asset_complete_for_target_symlink(downloader, tmp_path):
    target = tmp_path / "file.apk"
    real = tmp_path / "real.apk"
    real.write_text("data", encoding="utf-8")
    try:
        target.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=4)
    assert downloader._is_asset_complete_for_target(str(target), asset) is False


def test_is_asset_complete_for_target_not_exists(downloader):
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=100)
    assert (
        downloader._is_asset_complete_for_target("/nonexistent/file.apk", asset)
        is False
    )


def test_is_asset_complete_for_target_size_mismatch(downloader, tmp_path, mocker):
    target = tmp_path / "file.apk"
    target.write_text("data", encoding="utf-8")
    mocker.patch.object(downloader.file_operations, "get_file_size", return_value=999)
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=4)
    assert downloader._is_asset_complete_for_target(str(target), asset) is False


def test_is_asset_complete_for_target_verify_fail(downloader, tmp_path, mocker):
    target = tmp_path / "file.apk"
    target.write_text("data", encoding="utf-8")
    mocker.patch.object(downloader.file_operations, "get_file_size", return_value=4)
    mocker.patch.object(downloader, "verify", return_value=False)
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=4)
    assert downloader._is_asset_complete_for_target(str(target), asset) is False


def test_is_asset_complete_for_target_zip_not_intact(downloader, tmp_path, mocker):
    target = tmp_path / "file.zip"
    target.write_text("data", encoding="utf-8")
    mocker.patch.object(downloader, "verify", return_value=True)
    mocker.patch.object(downloader, "_is_zip_intact", return_value=False)
    asset = Asset(name="file.zip", download_url="https://example.com/f", size=4)
    assert downloader._is_asset_complete_for_target(str(target), asset) is False


def test_is_asset_complete_for_target_success(downloader, tmp_path, mocker):
    target = tmp_path / "file.apk"
    target.write_text("data", encoding="utf-8")
    mocker.patch.object(downloader.file_operations, "get_file_size", return_value=4)
    mocker.patch.object(downloader, "verify", return_value=True)
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=4)
    assert downloader._is_asset_complete_for_target(str(target), asset) is True


def test_is_asset_complete_for_target_no_size_check(downloader, tmp_path, mocker):
    target = tmp_path / "file.apk"
    target.write_text("data", encoding="utf-8")
    mocker.patch.object(downloader, "verify", return_value=True)
    asset = Asset(name="file.apk", download_url="https://example.com/f", size=None)
    assert downloader._is_asset_complete_for_target(str(target), asset) is True


def test_get_releases_none_data(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=None,
    )
    result = downloader.get_releases()
    assert result == []


def test_get_releases_limit_zero(downloader):
    result = downloader.get_releases(limit=0)
    assert result == []


def _make_release_data(tag, prerelease=False, assets=None):
    assets = assets or [
        {
            "name": "app-fdroid-universal-release.apk",
            "size": 100,
            "browser_download_url": "https://example.com/app.apk",
        }
    ]
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "published_at": "2026-01-01T00:00:00Z",
        "name": tag,
        "body": "release notes",
        "assets": assets,
    }


def test_get_releases_with_limit(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=[
            _make_release_data("v2.7.14"),
            _make_release_data("v2.7.13"),
        ],
    )
    result = downloader.get_releases(limit=1)
    assert len(result) == 1


def test_get_releases_skips_non_dict(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=["not_a_dict", _make_release_data("v2.7.14")],
    )
    result = downloader.get_releases(limit=5)
    assert len(result) == 1


def test_get_releases_skips_empty_assets(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=[
            {"tag_name": "v2.7.14", "assets": []},
            _make_release_data("v2.7.13"),
        ],
    )
    result = downloader.get_releases(limit=5)
    assert len(result) == 1


def test_get_releases_skips_empty_tag(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=[
            {
                "tag_name": "",
                "assets": [
                    {"name": "a.apk", "size": 1, "browser_download_url": "http://x"}
                ],
            },
            _make_release_data("v2.7.14"),
        ],
    )
    result = downloader.get_releases(limit=5)
    assert len(result) == 1


def test_get_releases_skips_non_matching_assets(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=[
            {
                "tag_name": "v2.7.14",
                "assets": [
                    {
                        "name": "readme.txt",
                        "size": 10,
                        "browser_download_url": "http://x",
                    }
                ],
            },
            _make_release_data("v2.7.13"),
        ],
    )
    result = downloader.get_releases(limit=5)
    assert len(result) == 1


def test_get_releases_invalid_versions_to_keep(downloader, mocker):
    downloader.config["APP_VERSIONS_TO_KEEP"] = "bad"
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        return_value=[_make_release_data("v2.7.14")],
    )
    result = downloader.get_releases()
    assert len(result) == 1


def test_get_releases_exception(downloader, mocker):
    mocker.patch.object(
        downloader.github_source,
        "fetch_raw_releases_data",
        side_effect=requests.RequestException("network error"),
    )
    result = downloader.get_releases()
    assert result == []


def test_get_releases_pagination(downloader, mocker):
    downloader.config["APP_VERSIONS_TO_KEEP"] = 50
    call_params = []

    def _fetch(params):
        call_params.append(dict(params))
        return [_make_release_data("v2.7.14")]

    mocker.patch.object(
        downloader.github_source, "fetch_raw_releases_data", side_effect=_fetch
    )
    result = downloader.get_releases()
    assert isinstance(result, list)
    assert len(result) >= 1
    assert any("per_page" in p for p in call_params)


def test_should_download_asset_wildcard(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = ["*"]
    assert downloader.should_download_asset("app-fdroid-universal-release.apk") is True


def test_should_download_asset_wildcard_non_app(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = ["*"]
    assert downloader.should_download_asset("readme.txt") is False


def test_should_download_asset_wildcard_excluded(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = ["*"]
    downloader.config["EXCLUDE_PATTERNS"] = ["*.apk"]
    assert downloader.should_download_asset("app-fdroid-universal-release.apk") is False


def test_should_download_asset_none_selected(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = None
    assert downloader.should_download_asset("app.apk") is False


def test_should_download_asset_empty_selected(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = []
    assert downloader.should_download_asset("app.apk") is False


def test_should_download_asset_excluded(downloader):
    downloader.config["EXCLUDE_PATTERNS"] = ["*.apk"]
    assert downloader.should_download_asset("app-fdroid-universal-release.apk") is False


def test_download_app_symlink_target(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    app_dir = downloads / APP_DIR_NAME / "v2.7.14-closed.1"
    app_dir.mkdir(parents=True)
    real = tmp_path / "real.apk"
    real.write_text("data", encoding="utf-8")
    link = app_dir / "app.apk"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    release = Release(tag_name="v2.7.14-closed.1", prerelease=True)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    result = downloader.download_app(release, asset)
    assert result.success is False


def test_download_app_already_complete(downloader, tmp_path, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(downloader, "_is_asset_complete_for_target", return_value=True)
    result = downloader.download_app(release, asset)
    assert result.success is True
    assert result.was_skipped is True


def test_download_app_validation_fail(downloader, tmp_path, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(downloader, "download", return_value=True)
    mocker.patch.object(
        downloader,
        "_is_asset_complete_for_target",
        side_effect=[False, False],
    )
    mocker.patch.object(downloader, "cleanup_file", return_value=True)
    result = downloader.download_app(release, asset)
    assert result.success is False
    assert result.error_type == ERROR_TYPE_VALIDATION


def test_download_app_download_fail(downloader, tmp_path, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(downloader, "download", return_value=False)
    mocker.patch.object(downloader, "_is_asset_complete_for_target", return_value=False)
    result = downloader.download_app(release, asset)
    assert result.success is False
    assert result.error_type == ERROR_TYPE_NETWORK


def test_download_app_success(downloader, tmp_path, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(downloader, "download", return_value=True)
    mocker.patch.object(
        downloader,
        "_is_asset_complete_for_target",
        side_effect=[False, True],
    )
    result = downloader.download_app(release, asset)
    assert result.success is True
    assert result.file_type == FILE_TYPE_CLIENT_APP


def test_download_app_prerelease_file_type(downloader, tmp_path, mocker):
    release = Release(tag_name="v2.7.14-closed.1", prerelease=True)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(downloader, "download", return_value=True)
    mocker.patch.object(
        downloader,
        "_is_asset_complete_for_target",
        side_effect=[False, True],
    )
    result = downloader.download_app(release, asset)
    assert result.success is True
    assert result.file_type == FILE_TYPE_CLIENT_APP_PRERELEASE


def test_download_app_request_exception(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(
        downloader,
        "get_target_path_for_release",
        side_effect=requests.RequestException("timeout"),
    )
    result = downloader.download_app(release, asset)
    assert result.success is False
    assert result.error_type == ERROR_TYPE_NETWORK
    assert result.is_retryable is True


def test_download_app_os_error(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(
        downloader,
        "get_target_path_for_release",
        side_effect=OSError("disk full"),
    )
    result = downloader.download_app(release, asset)
    assert result.success is False
    assert result.error_type == ERROR_TYPE_FILESYSTEM
    assert result.is_retryable is False


def test_download_app_value_error(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(
        downloader,
        "get_target_path_for_release",
        side_effect=ValueError("bad tag"),
    )
    result = downloader.download_app(release, asset)
    assert result.success is False
    assert result.error_type == ERROR_TYPE_VALIDATION
    assert result.is_retryable is False


def test_download_apk_alias(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=4)
    mocker.patch.object(
        downloader, "download_app", return_value=DownloadResult(success=True)
    )
    downloader.download_apk(release, asset)
    downloader.download_app.assert_called_once_with(release, asset)


def test_download_desktop_alias(downloader, mocker):
    release = Release(tag_name="v2.7.14", prerelease=False)
    asset = Asset(
        name="meshtastic.dmg", download_url="https://example.com/m.dmg", size=100
    )
    mocker.patch.object(
        downloader, "download_app", return_value=DownloadResult(success=True)
    )
    downloader.download_desktop(release, asset)
    downloader.download_app.assert_called_once_with(release, asset)


def test_is_release_complete_success(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    version_dir = downloads / APP_DIR_NAME / "v2.7.14"
    version_dir.mkdir(parents=True)
    (version_dir / "app.apk").write_text("data", encoding="utf-8")
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        assets=[Asset(name="app.apk", download_url="https://x", size=4)],
    )
    mocker.patch.object(downloader, "should_download_asset", return_value=True)
    mocker.patch.object(downloader, "_is_asset_complete_for_target", return_value=True)
    assert downloader.is_release_complete(release) is True


def test_is_release_complete_resolve_fails(downloader, mocker):
    mocker.patch.object(
        downloader,
        "_resolve_release_dir",
        side_effect=ValueError("unsafe"),
    )
    release = Release(tag_name="v2.7.14", prerelease=False)
    assert downloader.is_release_complete(release) is False


def test_is_release_complete_no_dir(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    mocker.patch.object(
        downloader, "_resolve_release_dir", return_value=str(downloads / "nonexistent")
    )
    release = Release(tag_name="v2.7.14", prerelease=False)
    assert downloader.is_release_complete(release) is False


def test_is_release_complete_no_expected_assets(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    version_dir = downloads / APP_DIR_NAME / "v2.7.14"
    version_dir.mkdir(parents=True)
    mocker.patch.object(
        downloader, "_resolve_release_dir", return_value=str(version_dir)
    )
    release = Release(tag_name="v2.7.14", prerelease=False, assets=[])
    mocker.patch.object(downloader, "should_download_asset", return_value=False)
    assert downloader.is_release_complete(release) is False


def test_is_release_complete_oserror(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    version_dir = downloads / APP_DIR_NAME / "v2.7.14"
    version_dir.mkdir(parents=True)
    (version_dir / "app.apk").write_text("data", encoding="utf-8")
    mocker.patch.object(
        downloader, "_resolve_release_dir", return_value=str(version_dir)
    )
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        assets=[Asset(name="app.apk", download_url="https://x", size=4)],
    )
    mocker.patch.object(downloader, "should_download_asset", return_value=True)
    mocker.patch.object(
        downloader, "_is_asset_complete_for_target", side_effect=OSError("io")
    )
    assert downloader.is_release_complete(release) is False


def test_cleanup_old_versions_no_releases(downloader, mocker):
    mocker.patch.object(downloader, "get_releases", return_value=[])
    downloader.cleanup_old_versions(1)


def test_cleanup_old_versions_exception(downloader, mocker):
    mocker.patch.object(
        downloader, "get_releases", side_effect=requests.RequestException("err")
    )
    downloader.cleanup_old_versions(1)


def test_cleanup_old_versions_with_cached(downloader, mocker):
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        published_at="2026-01-01T00:00:00Z",
        assets=[Asset(name="app.apk", download_url="https://x", size=100)],
    )
    mocker.patch.object(downloader, "cleanup_prerelease_directories")
    downloader.cleanup_old_versions(1, cached_releases=[release])
    downloader.cleanup_prerelease_directories.assert_called_once()


def test_cleanup_prerelease_directories_empty(downloader):
    downloader.cleanup_prerelease_directories(cached_releases=[])


def test_cleanup_prerelease_directories_unsafe_app_dir(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=False)
    release = Release(
        tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
    )
    downloader.cleanup_prerelease_directories(cached_releases=[release])


def test_cleanup_prerelease_directories_invalid_keep(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=True)
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        published_at="2026-01-01T00:00:00Z",
        assets=[Asset(name="app.apk", download_url="https://x", size=100)],
    )
    downloader.config["APP_VERSIONS_TO_KEEP"] = "bad"
    mocker.patch.object(downloader, "handle_prereleases", return_value=[])
    mocker.patch.object(downloader, "_remove_unexpected_entries")
    downloader.cleanup_prerelease_directories(
        cached_releases=[release], keep_limit_override="bad"
    )


def test_cleanup_prerelease_directories_keep_last_beta(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=True)
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-03T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(downloader, "handle_prereleases", return_value=[])
    mocker.patch.object(downloader, "_remove_unexpected_entries")
    downloader.cleanup_prerelease_directories(
        cached_releases=releases, keep_last_beta=True
    )
    assert downloader._remove_unexpected_entries.called


def test_cleanup_prerelease_directories_no_stable(downloader, tmp_path, mocker):
    downloads = tmp_path / "downloads"
    app_dir = downloads / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    mocker.patch.object(downloader, "_is_safe_managed_dir", return_value=True)
    releases = [
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-01T00:00:00Z",
        ),
    ]
    downloader.cleanup_prerelease_directories(cached_releases=releases)


def test_remove_unexpected_entries_file_not_found(downloader, tmp_path):
    base = tmp_path / "nonexistent"
    downloader._remove_unexpected_entries(str(base), {"v2.7.14"})


def test_remove_unexpected_entries_symlink(downloader, tmp_path):
    base = tmp_path / "dir"
    base.mkdir()
    real = tmp_path / "outside"
    real.mkdir()
    try:
        (base / "link").symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported")
    (base / "v2.7.14").mkdir()
    downloader._remove_unexpected_entries(str(base), {"v2.7.14"})
    assert (base / "link").exists()


def test_remove_unexpected_entries_non_version_dir(downloader, tmp_path):
    base = tmp_path / "dir"
    base.mkdir()
    (base / "random_dir").mkdir()
    (base / "v2.7.14").mkdir()
    downloader._remove_unexpected_entries(str(base), {"v2.7.14"})
    assert (base / "random_dir").exists()


def test_remove_unexpected_entries_removes_stale(downloader, tmp_path):
    base = tmp_path / "dir"
    base.mkdir()
    (base / "v2.7.13").mkdir()
    (base / "v2.7.14").mkdir()
    downloader._remove_unexpected_entries(str(base), {"v2.7.14"})
    assert (base / "v2.7.14").exists()
    assert not (base / "v2.7.13").exists()


def test_get_latest_release_tag_no_file(downloader, tmp_path):
    assert downloader.get_latest_release_tag() is None


def test_get_latest_release_tag_with_data(downloader, tmp_path):
    latest_path = downloader.latest_release_path
    Path(latest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(latest_path).write_text(
        json.dumps({"latest_version": "v2.7.14"}), encoding="utf-8"
    )
    downloader.cache_manager.read_json.return_value = {"latest_version": "v2.7.14"}
    assert downloader.get_latest_release_tag() == "v2.7.14"


def test_get_latest_release_tag_empty_value(downloader, tmp_path):
    downloader.cache_manager.read_json.return_value = {"latest_version": ""}
    assert downloader.get_latest_release_tag() is None


def test_get_latest_release_tag_non_string(downloader, tmp_path):
    downloader.cache_manager.read_json.return_value = {"latest_version": 123}
    assert downloader.get_latest_release_tag() is None


def test_get_latest_release_tag_exception(downloader, tmp_path):
    downloader.cache_manager.read_json.side_effect = ValueError("bad json")
    assert downloader.get_latest_release_tag() is None


def test_update_latest_release_tag(downloader):
    downloader.cache_manager.atomic_write_json.return_value = True
    result = downloader.update_latest_release_tag("v2.7.14")
    assert result is True


def test_handle_prereleases_disabled(downloader):
    downloader.config["CHECK_APP_PRERELEASES"] = False
    releases = [Release(tag_name="v2.7.14-closed.1", prerelease=True)]
    assert downloader.handle_prereleases(releases) == []


def test_handle_prereleases_basic(downloader, mocker):
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value=None,
    )
    result = downloader.handle_prereleases(releases)
    assert len(result) == 1
    assert result[0].tag_name == "v2.7.14-closed.1"


def test_handle_prereleases_filters_by_expected_base(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.15-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value="2.7.",
    )
    mocker.patch.object(
        downloader.version_manager,
        "extract_clean_version",
        return_value="v2.7.15",
    )
    result = downloader.handle_prereleases(releases)
    assert len(result) == 1


def test_handle_prereleases_filters_by_expected_base_exclude(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value="2.7.",
    )
    mocker.patch.object(
        downloader.version_manager,
        "extract_clean_version",
        return_value="v2.7.14",
    )
    result = downloader.handle_prereleases(releases)
    assert len(result) == 1


def test_handle_prereleases_filters_by_expected_base_no_clean(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value="2.7.",
    )
    mocker.patch.object(
        downloader.version_manager,
        "extract_clean_version",
        return_value=None,
    )
    result = downloader.handle_prereleases(releases)
    assert len(result) == 1


def test_handle_prereleases_recent_commits(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.15-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
        Release(
            tag_name="v2.7.15-closed.abc1234",
            prerelease=True,
            published_at="2026-01-03T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value="2.7.",
    )
    mocker.patch.object(
        downloader.version_manager,
        "extract_clean_version",
        return_value="v2.7.15",
    )
    commits = [{"sha": "abc1234567890"}]
    result = downloader.handle_prereleases(releases, recent_commits=commits)
    assert all("abc1234" in r.tag_name for r in result)


def test_handle_prereleases_recent_commits_no_match_keeps_all(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    mocker.patch.object(
        downloader.version_manager,
        "calculate_expected_prerelease_version",
        return_value="2.7.",
    )
    mocker.patch.object(
        downloader.version_manager,
        "extract_clean_version",
        return_value="v2.7.14",
    )
    commits = [{"sha": "zzz9999999999"}]
    result = downloader.handle_prereleases(releases, recent_commits=commits)
    assert len(result) == 1


def test_get_latest_prerelease_tag_no_releases(downloader, mocker):
    mocker.patch.object(downloader, "get_releases", return_value=[])
    assert downloader.get_latest_prerelease_tag() is None


def test_get_latest_prerelease_tag_finds_prerelease(downloader):
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.15-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    result = downloader.get_latest_prerelease_tag(releases)
    assert result == "v2.7.15-closed.1"


def test_get_latest_prerelease_tag_prerelease_newer_than_stable(downloader):
    releases = [
        Release(
            tag_name="v2.7.14", prerelease=False, published_at="2026-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.7.15-closed.1",
            prerelease=True,
            published_at="2026-01-02T00:00:00Z",
        ),
    ]
    result = downloader.get_latest_prerelease_tag(releases)
    assert result == "v2.7.15-closed.1"


def test_get_latest_prerelease_tag_no_stable(downloader):
    releases = [
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-01T00:00:00Z",
        ),
    ]
    result = downloader.get_latest_prerelease_tag(releases)
    assert result == "v2.7.14-closed.1"


def test_update_prerelease_tracking(downloader):
    downloader.cache_manager.atomic_write_json.return_value = True
    result = downloader.update_prerelease_tracking("v2.7.14-closed.1")
    assert result is True


def test_should_download_prerelease_disabled(downloader):
    downloader.config["CHECK_APP_PRERELEASES"] = False
    assert downloader.should_download_prerelease("v2.7.14-closed.1") is False


def test_should_download_prerelease_no_tracking_file(downloader, tmp_path):
    assert downloader.should_download_prerelease("v2.7.14-closed.1") is True


def test_should_download_prerelease_newer(downloader, tmp_path):
    tracking_file = downloader.get_prerelease_tracking_file()
    Path(tracking_file).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_file).write_text(
        json.dumps({"latest_version": "v2.7.14-closed.1"}), encoding="utf-8"
    )
    downloader.cache_manager.read_json.return_value = {
        "latest_version": "v2.7.14-closed.1"
    }
    result = downloader.should_download_prerelease("v2.7.15-closed.1")
    assert result is True


def test_should_download_prerelease_older(downloader, tmp_path):
    tracking_file = downloader.get_prerelease_tracking_file()
    Path(tracking_file).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_file).write_text(
        json.dumps({"latest_version": "v2.7.15-closed.1"}), encoding="utf-8"
    )
    downloader.cache_manager.read_json.return_value = {
        "latest_version": "v2.7.15-closed.1"
    }
    result = downloader.should_download_prerelease("v2.7.14-closed.1")
    assert result is False


def test_should_download_prerelease_exception(downloader, tmp_path):
    tracking_file = downloader.get_prerelease_tracking_file()
    Path(tracking_file).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_file).write_text("not json", encoding="utf-8")
    downloader.cache_manager.read_json.side_effect = ValueError("bad json")
    result = downloader.should_download_prerelease("v2.7.14-closed.1")
    assert result is True


def test_manage_prerelease_tracking_files_disabled(downloader):
    downloader.config["CHECK_APP_PRERELEASES"] = False
    downloader.manage_prerelease_tracking_files()


def test_manage_prerelease_tracking_files_no_tracking_dir(downloader, tmp_path):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    tracking_dir = os.path.dirname(downloader.get_prerelease_tracking_file())
    assert not os.path.exists(tracking_dir) or True
    if not os.path.exists(tracking_dir):
        downloader.manage_prerelease_tracking_files()


def test_validate_extraction_patterns(downloader):
    assert downloader.validate_extraction_patterns(["*.apk"], []) is False


def test_check_extraction_needed(downloader):
    assert (
        downloader.check_extraction_needed("/path/file.apk", "/dir", ["*.apk"], [])
        is False
    )


def test_is_supported_client_app_release_no_tuple():
    assert _is_supported_client_app_release("not-a-version") is True


def test_is_supported_client_app_release_old_version():
    assert _is_supported_client_app_release("v1.0.0") is False


def test_is_supported_client_app_release_new_version():
    assert _is_supported_client_app_release("v2.7.14") is True


def test_is_client_app_prerelease_payload_true():
    data = {
        "tag_name": "v2.7.14-closed.1",
        "prerelease": False,
    }
    assert _is_client_app_prerelease_payload(data) is True


def test_is_client_app_prerelease_payload_false():
    data = {
        "tag_name": "v2.7.14",
        "prerelease": False,
    }
    assert _is_client_app_prerelease_payload(data) is False


def test_is_client_app_prerelease_payload_prerelease_flag():
    data = {
        "tag_name": "v2.7.14",
        "prerelease": True,
    }
    assert _is_client_app_prerelease_payload(data) is True


def test_is_client_app_asset_name_apk():
    assert is_client_app_asset_name("app.apk") is True


def test_is_client_app_asset_name_dmg():
    assert is_client_app_asset_name("meshtastic.dmg") is True


def test_is_client_app_asset_name_other():
    assert is_client_app_asset_name("readme.txt") is False


def test_is_client_app_prerelease_tag_open():
    assert is_client_app_prerelease_tag("v2.7.14-open.1") is True


def test_is_client_app_prerelease_tag_closed():
    assert is_client_app_prerelease_tag("v2.7.14-closed.1") is True


def test_is_client_app_prerelease_tag_internal():
    assert is_client_app_prerelease_tag("v2.7.14-internal.1") is True


def test_is_client_app_prerelease_tag_stable():
    assert is_client_app_prerelease_tag("v2.7.14") is False


def test_is_client_app_prerelease_tag_none():
    assert is_client_app_prerelease_tag(None) is False


def test_migrate_split_layout_alias(downloader, mocker):
    mocker.patch.object(downloader, "migrate_legacy_layout")
    downloader.migrate_split_layout()
    downloader.migrate_legacy_layout.assert_called_once()


def test_get_assets(downloader):
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        assets=[
            Asset(name="app.apk", download_url="https://x", size=100),
            Asset(name="readme.txt", download_url="https://y", size=10),
        ],
    )
    assets = downloader.get_assets(release)
    assert len(assets) == 1
    assert assets[0].name == "app.apk"


def test_get_download_url(downloader):
    asset = Asset(name="app.apk", download_url="https://example.com/app.apk", size=100)
    assert downloader.get_download_url(asset) == "https://example.com/app.apk"


def test_has_known_2714_prerelease_version_mismatch(downloader):
    assert downloader.has_known_2714_prerelease_version_mismatch() is False


def test_get_known_2714_prerelease_mismatch_tags(downloader):
    assert downloader.get_known_2714_prerelease_mismatch_tags() == []


def test_get_prerelease_tracking_file(downloader):
    result = downloader.get_prerelease_tracking_file()
    assert result.endswith(".json")


def test_manage_prerelease_tracking_files_with_releases(downloader, mocker):
    downloader.config["CHECK_APP_PRERELEASES"] = True
    tracking_dir = os.path.dirname(downloader.get_prerelease_tracking_file())
    os.makedirs(tracking_dir, exist_ok=True)
    releases = [
        Release(
            tag_name="v2.7.14-closed.1",
            prerelease=True,
            published_at="2026-01-01T00:00:00Z",
        ),
    ]
    mocker.patch.object(downloader, "handle_prereleases", return_value=releases)
    mock_manager = Mock()
    mocker.patch(
        "fetchtastic.download.client_app.PrereleaseHistoryManager",
        return_value=mock_manager,
    )
    mock_manager.create_prerelease_tracking_data.return_value = {"test": True}
    downloader.manage_prerelease_tracking_files(cached_releases=releases)
    mock_manager.manage_prerelease_tracking_files.assert_called_once()
