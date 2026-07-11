# Tests for unified app/<version>/ client-app storage cleanup safety.

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from fetchtastic.constants import APP_DIR_NAME, LATEST_POINTER_NAME
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def _make_downloader(tmp_path) -> MeshtasticClientAppDownloader:
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "SAVE_CLIENT_APPS": True,
        "CHECK_APP_PRERELEASES": True,
        "APP_VERSIONS_TO_KEEP": 1,
        "SELECTED_APP_ASSETS": ["*.apk", "*.dmg"],
    }
    dl = MeshtasticClientAppDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )
    real_vm = VersionManager()
    dl.version_manager.get_release_tuple = real_vm.get_release_tuple
    dl.version_manager.is_prerelease_version = real_vm.is_prerelease_version
    return dl


def test_stale_stable_version_dir_is_deleted(tmp_path):
    dl = _make_downloader(tmp_path)
    version_dir = tmp_path / APP_DIR_NAME / "v2.7.13"
    version_dir.mkdir(parents=True)
    (version_dir / "app-universal.apk").write_bytes(b"apk")
    (version_dir / "Meshtastic-2.7.13.dmg").write_bytes(b"dmg")
    (version_dir / "release_notes-v2.7.13.md").write_text("notes")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert not version_dir.exists()


def test_stale_prerelease_version_dir_is_deleted(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_dir = tmp_path / APP_DIR_NAME / "prerelease" / "v2.7.13-open.1"
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "app-universal.apk").write_bytes(b"apk")
    dl.handle_prereleases = Mock(return_value=[])

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not prerelease_dir.exists()


def test_all_fetched_expected_prerelease_dirs_are_retained(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    tags = ["v2.7.14-closed.1", "v2.7.14-closed.17", "v2.7.14-open.1"]
    for tag in tags:
        version_dir = prerelease_base / tag
        version_dir.mkdir(parents=True)
        (version_dir / "app-universal.apk").write_bytes(b"apk")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.13", prerelease=False),
            *[Release(tag_name=tag, prerelease=True) for tag in tags],
        ]
    )

    for tag in tags:
        assert (prerelease_base / tag).exists()


def test_stable_release_supersedes_matching_prerelease_dirs(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    superseded = prerelease_base / "v2.7.14-closed.17"
    next_expected = prerelease_base / "v2.7.15-open.1"
    superseded.mkdir(parents=True)
    next_expected.mkdir(parents=True)

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.14-closed.17", prerelease=True),
            Release(tag_name="v2.7.15-open.1", prerelease=True),
        ]
    )

    assert not superseded.exists()
    assert next_expected.exists()


def test_unknown_non_version_entries_under_app_are_preserved(tmp_path):
    dl = _make_downloader(tmp_path)
    unknown_dir = tmp_path / APP_DIR_NAME / "manual-files"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "keep.txt").write_text("mine")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert unknown_dir.exists()
    assert (unknown_dir / "keep.txt").exists()


def test_cleanup_skips_symlinks(tmp_path):
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    target = tmp_path / "outside"
    app_dir.mkdir(parents=True)
    target.mkdir()
    link = app_dir / "v2.7.13"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.15", prerelease=False)]
    )

    assert link.is_symlink()
    assert target.exists()


def test_prerelease_cleanup_skips_symlinks(tmp_path):
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    target = tmp_path / "outside-prerelease"
    prerelease_base.mkdir(parents=True)
    target.mkdir()
    link = prerelease_base / "v2.7.14-closed.1"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.13", prerelease=False)]
    )

    assert link.is_symlink()
    assert target.exists()


def test_prerelease_cleanup_skips_unknown_non_version_entries(tmp_path):
    dl = _make_downloader(tmp_path)
    unknown_dir = tmp_path / APP_DIR_NAME / "prerelease" / "manual-files"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "keep.txt").write_text("mine")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.13", prerelease=False)]
    )

    assert unknown_dir.exists()
    assert (unknown_dir / "keep.txt").exists()


def test_release_notes_use_single_upstream_release_filename(tmp_path):
    dl = _make_downloader(tmp_path)
    release = Release(
        tag_name="v2.7.14",
        prerelease=False,
        body="release notes",
        assets=[],
    )

    notes_path = dl.ensure_release_notes(release)

    assert notes_path is not None
    assert notes_path.endswith("release_notes-v2.7.14.md")
    assert Path(notes_path).name == "release_notes-v2.7.14.md"


def test_mixed_apk_and_desktop_assets_live_together(tmp_path):
    dl = _make_downloader(tmp_path)
    release = Release(tag_name="v2.7.14", prerelease=False)
    apk = Asset(
        name="app-universal.apk",
        download_url="https://example.invalid/app-universal.apk",
        size=None,
    )
    dmg = Asset(
        name="Meshtastic-2.7.14.dmg",
        download_url="https://example.invalid/Meshtastic-2.7.14.dmg",
        size=None,
    )

    apk_path = dl.get_target_path_for_release(
        release.tag_name, apk.name, release=release
    )
    dmg_path = dl.get_target_path_for_release(
        release.tag_name, dmg.name, release=release
    )

    expected_dir = tmp_path / APP_DIR_NAME / "v2.7.14"
    assert Path(apk_path).parent == expected_dir
    assert Path(dmg_path).parent == expected_dir


def test_legacy_platform_classes_use_client_app_lifecycle(tmp_path):
    from fetchtastic.download.android import MeshtasticAndroidAppDownloader
    from fetchtastic.download.desktop import MeshtasticDesktopDownloader

    config = {"DOWNLOAD_DIR": str(tmp_path), "SAVE_CLIENT_APPS": True}
    cache = CacheManager(cache_dir=str(tmp_path / "cache"))

    assert isinstance(
        MeshtasticAndroidAppDownloader(config, cache), MeshtasticClientAppDownloader
    )
    assert isinstance(
        MeshtasticDesktopDownloader(config, cache), MeshtasticClientAppDownloader
    )


# Regression coverage for required cleanup cases 12-19. These exercise the
# desired post-hotfix behavior: higher-minor prerelease directories are retained
# on their own merits (not over-filtered through handle_prereleases), and the
# managed "latest" pointer is validated only after removals so dangling, unsafe,
# or non-retained symlinks are removed via remove_latest_pointer while valid and
# non-symlink latest entries are preserved. Against current production the
# missing-behavior cases fail (red); the preserve-valid cases lock existing
# correct behavior.


def test_higher_minor_prerelease_dir_is_retained_alongside_stable(tmp_path):
    """Case 12: retain v2.8.0-closed.8 directory while stable v2.7.14 is current."""
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    higher_minor = prerelease_base / "v2.8.0-closed.8"
    higher_minor.mkdir(parents=True)
    (higher_minor / "app-universal.apk").write_bytes(b"apk")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.8.0-closed.8", prerelease=True),
        ]
    )

    assert higher_minor.exists()
    assert (higher_minor / "app-universal.apk").exists()


def test_same_base_superseded_prerelease_dir_is_removed(tmp_path):
    """Case 13: remove a superseded same-base build, retain the current one."""
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    superseded = prerelease_base / "v2.8.0-closed.7"
    current = prerelease_base / "v2.8.0-closed.8"
    for directory in (superseded, current):
        directory.mkdir(parents=True)
        (directory / "app-universal.apk").write_bytes(b"apk")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.8.0-closed.8", prerelease=True),
        ]
    )

    assert not superseded.exists()
    assert current.exists()


def test_older_prerelease_base_removed_when_newer_active_base_exists(tmp_path):
    """Case 14: drop the older prerelease base when a newer active base exists."""
    dl = _make_downloader(tmp_path)
    prerelease_base = tmp_path / APP_DIR_NAME / "prerelease"
    older_base = prerelease_base / "v2.7.15-open.1"
    newer_base = prerelease_base / "v2.8.0-closed.8"
    for directory in (older_base, newer_base):
        directory.mkdir(parents=True)
        (directory / "app-universal.apk").write_bytes(b"apk")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.7.15-open.1", prerelease=True),
            Release(tag_name="v2.8.0-closed.8", prerelease=True),
        ]
    )

    assert not older_base.exists()
    assert newer_base.exists()


def test_valid_latest_pointer_to_retained_directory_is_preserved(tmp_path):
    """Case 15: a latest pointer at a retained directory survives cleanup."""
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    retained = app_dir / "v2.7.14"
    retained.mkdir(parents=True)
    (retained / "app-universal.apk").write_bytes(b"apk")
    latest = app_dir / LATEST_POINTER_NAME
    try:
        os.symlink("v2.7.14", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert latest.is_symlink()
    assert os.readlink(latest) == "v2.7.14"
    assert retained.exists()


def test_latest_pointer_whose_target_was_deleted_is_removed(tmp_path):
    """Case 16: a latest pointer left dangling by removals must be removed too."""
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    stale = app_dir / "v2.7.13"
    retained = app_dir / "v2.7.14"
    for directory in (stale, retained):
        directory.mkdir(parents=True)
        (directory / "app-universal.apk").write_bytes(b"apk")
    latest = app_dir / LATEST_POINTER_NAME
    try:
        os.symlink("v2.7.13", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not stale.exists()
    assert not latest.is_symlink()


def test_preexisting_dangling_latest_pointer_is_removed(tmp_path):
    """Case 17: a latest pointer that was already dangling gets cleaned up."""
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    retained = app_dir / "v2.7.14"
    retained.mkdir(parents=True)
    (retained / "app-universal.apk").write_bytes(b"apk")
    latest = app_dir / LATEST_POINTER_NAME
    try:
        os.symlink("v2.7.99", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not latest.is_symlink()
    assert retained.exists()


def test_non_symlink_latest_entry_is_preserved(tmp_path):
    """Case 18: a user-managed non-symlink latest entry is never removed."""
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    retained = app_dir / "v2.7.14"
    retained.mkdir(parents=True)
    (retained / "app-universal.apk").write_bytes(b"apk")
    latest = app_dir / LATEST_POINTER_NAME
    latest.write_text("user-managed-not-a-symlink")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert latest.exists()
    assert not latest.is_symlink()
    assert latest.read_text() == "user-managed-not-a-symlink"
    assert retained.exists()


def test_unsafe_latest_pointer_target_removed_without_following(tmp_path):
    """Case 19: an unsafe latest symlink is removed without following its target."""
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    retained = app_dir / "v2.7.14"
    retained.mkdir(parents=True)
    (retained / "app-universal.apk").write_bytes(b"apk")
    # Real target lives outside the managed app tree but inside tmp_path so the
    # test never mutates outside the temporary directory.
    outside_target = tmp_path / "outside-target"
    outside_target.write_text("precious")
    latest = app_dir / LATEST_POINTER_NAME
    try:
        os.symlink(os.path.relpath(outside_target, app_dir), latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not latest.is_symlink()
    assert outside_target.exists()
    assert outside_target.read_text() == "precious"
    assert retained.exists()


# Regression coverage for cleanup/latest-pointer behavior when prerelease tags
# spell the same semantic base at different component widths. v2.8-open.1 and
# v2.8.0-closed.1 are both semantic base 2.8, and v2.7.0-open.1 shares base 2.7
# with the stable v2.7 release. The selector normalizes release tuples to a
# fixed width-6 form, so (2, 8) and (2, 8, 0) collapse into one stream and
# (2, 7, 0) compares equal to the stable (2, 7); these equivalent bases are
# treated as one. The cleanup guard locks the already-correct production path.


def test_equivalent_semantic_base_prerelease_dir_removed_with_latest(tmp_path):
    """Stable v2.7 supersedes local v2.7.0-open.1 on the same semantic base.

    The prerelease directory and the latest pointer targeting it are removed
    once the equivalent base (2.7) is recognized as covered by the stable
    release: width-6 normalization makes (2, 7, 0) equal to the stable tuple
    (2, 7), so the prerelease is superseded.
    """
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    prerelease_base = app_dir / "prerelease"
    stable_dir = app_dir / "v2.7"
    stable_dir.mkdir(parents=True)
    (stable_dir / "app-universal.apk").write_bytes(b"apk")
    equivalent_prerelease = prerelease_base / "v2.7.0-open.1"
    equivalent_prerelease.mkdir(parents=True)
    (equivalent_prerelease / "app-universal.apk").write_bytes(b"apk")
    latest = prerelease_base / LATEST_POINTER_NAME
    try:
        os.symlink("v2.7.0-open.1", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7", prerelease=False),
            Release(tag_name="v2.7.0-open.1", prerelease=True),
        ]
    )

    assert not equivalent_prerelease.exists()
    assert not latest.is_symlink()
    assert stable_dir.exists()


def test_equivalent_semantic_base_prerelease_dirs_retained_with_latest(tmp_path):
    """Equivalent winning spellings of semantic base 2.8 are all retained.

    v2.8-open.1, v2.8.0-closed.1, and v2.8.0.0 all resolve to semantic base 2.8
    and win over the older stable v2.7.14, so every equivalent-base directory
    survives and the latest pointer targeting a retained directory stays valid:
    width-6 normalization collapses (2, 8) and (2, 8, 0) into one stream so
    every spelling is retained.
    """
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    prerelease_base = app_dir / "prerelease"
    stable_dir = app_dir / "v2.7.14"
    stable_dir.mkdir(parents=True)
    (stable_dir / "app-universal.apk").write_bytes(b"apk")
    equivalent_tags = ["v2.8-open.1", "v2.8.0-closed.1", "v2.8.0.0"]
    for tag in equivalent_tags:
        version_dir = prerelease_base / tag
        version_dir.mkdir(parents=True)
        (version_dir / "app-universal.apk").write_bytes(b"apk")
    latest = prerelease_base / LATEST_POINTER_NAME
    try:
        os.symlink("v2.8.0-closed.1", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            *[Release(tag_name=tag, prerelease=True) for tag in equivalent_tags],
        ]
    )

    for tag in equivalent_tags:
        assert (prerelease_base / tag).exists()
    assert latest.is_symlink()
    assert (prerelease_base / os.readlink(latest)).exists()
    assert stable_dir.exists()


def test_production_stable_with_higher_base_prerelease_retained_with_latest(tmp_path):
    """Cleanup guard: stable v2.7.14 + winning v2.8.0-closed.8 stay valid.

    Locks the already-correct production path so the semantic-base
    normalization fix cannot regress the ordinary higher-base prerelease
    retention case. Both directories survive and the latest pointer targeting
    the retained prerelease directory remains valid.
    """
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    prerelease_base = app_dir / "prerelease"
    stable_dir = app_dir / "v2.7.14"
    stable_dir.mkdir(parents=True)
    (stable_dir / "app-universal.apk").write_bytes(b"apk")
    prerelease_dir = prerelease_base / "v2.8.0-closed.8"
    prerelease_dir.mkdir(parents=True)
    (prerelease_dir / "app-universal.apk").write_bytes(b"apk")
    latest = prerelease_base / LATEST_POINTER_NAME
    try:
        os.symlink("v2.8.0-closed.8", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[
            Release(tag_name="v2.7.14", prerelease=False),
            Release(tag_name="v2.8.0-closed.8", prerelease=True),
        ]
    )

    assert stable_dir.exists()
    assert prerelease_dir.exists()
    assert latest.is_symlink()
    assert (prerelease_base / os.readlink(latest)).exists()


def test_latest_pointer_to_regular_file_is_removed(tmp_path):
    """Case 20: a latest pointer at a retained name that is a file, not a dir.

    ``os.path.exists`` returns True for a regular file named like a retained
    tag, so the pre-fix check left ``latest`` pointing somewhere that cannot
    serve as a release directory. Require a directory via ``os.path.isdir``
    so the pointer is removed instead.
    """
    dl = _make_downloader(tmp_path)
    app_dir = tmp_path / APP_DIR_NAME
    app_dir.mkdir(parents=True)
    # Degenerate but possible: a regular file occupying a retained tag name.
    file_target = app_dir / "v2.7.14"
    file_target.write_text("not-a-directory")
    latest = app_dir / LATEST_POINTER_NAME
    try:
        os.symlink("v2.7.14", latest)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not supported in this test environment")

    dl.cleanup_prerelease_directories(
        cached_releases=[Release(tag_name="v2.7.14", prerelease=False)]
    )

    assert not latest.is_symlink()
