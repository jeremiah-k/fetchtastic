# Tests for unified app/<version>/ storage cleanup safety
#
# Ensures that Android and Desktop cleanup passes never delete each other's
# artifacts when both platforms share the same version directory.

from unittest.mock import Mock

import pytest

from fetchtastic.constants import (
    APK_PRERELEASES_DIR_NAME,
    APP_DIR_NAME,
    DESKTOP_PRERELEASES_DIR_NAME,
)
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.desktop import MeshtasticDesktopDownloader
from fetchtastic.download.interfaces import Release
from fetchtastic.download.version import VersionManager

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def _make_android_downloader(tmp_path):
    """Create an Android downloader wired with a real VersionManager."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_APK_PRERELEASES": True,
        "ANDROID_VERSIONS_TO_KEEP": 1,
    }
    dl = MeshtasticAndroidAppDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )
    real_vm = VersionManager()
    dl.version_manager.get_release_tuple = real_vm.get_release_tuple
    dl.version_manager.is_prerelease_version = real_vm.is_prerelease_version
    return dl


def _make_desktop_downloader(tmp_path):
    """Create a Desktop downloader wired with a real VersionManager."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path),
        "CHECK_DESKTOP_PRERELEASES": True,
        "DESKTOP_VERSIONS_TO_KEEP": 1,
        "SELECTED_DESKTOP_ASSETS": [],
    }
    dl = MeshtasticDesktopDownloader(
        config, CacheManager(cache_dir=str(tmp_path / "cache"))
    )
    real_vm = VersionManager()
    dl.version_manager.get_release_tuple = real_vm.get_release_tuple
    dl.version_manager.is_prerelease_version = real_vm.is_prerelease_version
    return dl


# ---------------------------------------------------------------------------
# Mixed directory tests — app/ (stable releases)
# ---------------------------------------------------------------------------


class TestMixedDirectoryCleanupStable:
    """Android and Desktop cleanup must not delete each other's files in app/<version>/."""

    def test_android_cleanup_preserves_desktop_files_in_mixed_dir(self, tmp_path):
        """Android cleanup should remove APKs but leave Desktop installers intact."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.14"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")
        (version_dir / "Meshtastic-2.7.14.dmg").write_bytes(b"dmg")
        (version_dir / "release_notes-android-v2.7.14.md").write_text("android notes")
        (version_dir / "release_notes-desktop-v2.7.14.md").write_text("desktop notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert not (version_dir / "app-universal.apk").exists()
        assert not (version_dir / "release_notes-android-v2.7.14.md").exists()
        assert (version_dir / "Meshtastic-2.7.14.dmg").exists()
        assert (version_dir / "release_notes-desktop-v2.7.14.md").exists()
        assert version_dir.exists()

    def test_desktop_cleanup_preserves_android_files_in_mixed_dir(self, tmp_path):
        """Desktop cleanup should remove installers but leave APKs intact."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.14"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")
        (version_dir / "Meshtastic-2.7.14.dmg").write_bytes(b"dmg")
        (version_dir / "release_notes-android-v2.7.14.md").write_text("android notes")
        (version_dir / "release_notes-desktop-v2.7.14.md").write_text("desktop notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert not (version_dir / "Meshtastic-2.7.14.dmg").exists()
        assert not (version_dir / "release_notes-desktop-v2.7.14.md").exists()
        assert (version_dir / "app-universal.apk").exists()
        assert (version_dir / "release_notes-android-v2.7.14.md").exists()
        assert version_dir.exists()

    def test_android_cleanup_deletes_dir_when_only_android_files(self, tmp_path):
        """Android-only stale dir should be fully removed including the directory."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.13"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")
        (version_dir / "release_notes-android-v2.7.13.md").write_text("notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert not version_dir.exists()

    def test_desktop_cleanup_deletes_dir_when_only_desktop_files(self, tmp_path):
        """Desktop-only stale dir should be fully removed including the directory."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.13"
        version_dir.mkdir()
        (version_dir / "Meshtastic-2.7.13.dmg").write_bytes(b"dmg")
        (version_dir / "release_notes-desktop-v2.7.13.md").write_text("notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert not version_dir.exists()


# ---------------------------------------------------------------------------
# Mixed directory tests — app/prerelease/
# ---------------------------------------------------------------------------


class TestMixedDirectoryCleanupPrerelease:
    """Android and Desktop cleanup must not delete each other's files in app/prerelease/<version>/."""

    def test_android_cleanup_preserves_desktop_files_in_mixed_prerelease_dir(
        self, tmp_path
    ):
        """Android cleanup should remove APKs but leave Desktop prerelease installers."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)
        prerelease_base = app_dir / APK_PRERELEASES_DIR_NAME
        prerelease_base.mkdir(exist_ok=True)

        version_dir = prerelease_base / "v2.7.15-open.1"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")
        (version_dir / "Meshtastic-2.7.15-open.1.dmg").write_bytes(b"dmg")
        (version_dir / "release_notes-android-v2.7.15-open.1.md").write_text(
            "android notes"
        )
        (version_dir / "release_notes-desktop-v2.7.15-open.1.md").write_text(
            "desktop notes"
        )

        stable = Release(tag_name="v2.7.14", prerelease=False)
        prerelease = Release(tag_name="v2.7.15-open.1", prerelease=True)
        dl.handle_prereleases = Mock(return_value=[prerelease])
        dl.cleanup_prerelease_directories(cached_releases=[stable, prerelease])

        assert not (version_dir / "app-universal.apk").exists()
        assert not (version_dir / "release_notes-android-v2.7.15-open.1.md").exists()
        assert (version_dir / "Meshtastic-2.7.15-open.1.dmg").exists()
        assert (version_dir / "release_notes-desktop-v2.7.15-open.1.md").exists()
        assert version_dir.exists()

    def test_desktop_cleanup_preserves_android_files_in_mixed_prerelease_dir(
        self, tmp_path
    ):
        """Desktop cleanup should remove installers but leave Android prerelease APKs."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)
        prerelease_base = app_dir / DESKTOP_PRERELEASES_DIR_NAME
        prerelease_base.mkdir(exist_ok=True)

        version_dir = prerelease_base / "v2.7.15-open.1"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")
        (version_dir / "Meshtastic-2.7.15-open.1.dmg").write_bytes(b"dmg")
        (version_dir / "release_notes-android-v2.7.15-open.1.md").write_text(
            "android notes"
        )
        (version_dir / "release_notes-desktop-v2.7.15-open.1.md").write_text(
            "desktop notes"
        )

        stable = Release(tag_name="v2.7.14", prerelease=False)
        prerelease = Release(tag_name="v2.7.15-open.1", prerelease=True)
        dl.handle_prereleases = Mock(return_value=[prerelease])
        dl.cleanup_prerelease_directories(cached_releases=[stable, prerelease])

        assert not (version_dir / "Meshtastic-2.7.15-open.1.dmg").exists()
        assert not (version_dir / "release_notes-desktop-v2.7.15-open.1.md").exists()
        assert (version_dir / "app-universal.apk").exists()
        assert (version_dir / "release_notes-android-v2.7.15-open.1.md").exists()
        assert version_dir.exists()

    def test_android_cleanup_deletes_dir_when_only_android_prerelease_files(
        self, tmp_path
    ):
        """Android-only stale prerelease dir should be fully removed."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)
        prerelease_base = app_dir / APK_PRERELEASES_DIR_NAME
        prerelease_base.mkdir(exist_ok=True)

        version_dir = prerelease_base / "v2.7.13-open.1"
        version_dir.mkdir()
        (version_dir / "app-universal.apk").write_bytes(b"apk")

        stable = Release(tag_name="v2.7.14", prerelease=False)
        dl.handle_prereleases = Mock(return_value=[])
        dl.cleanup_prerelease_directories(cached_releases=[stable])

        assert not version_dir.exists()

    def test_desktop_cleanup_deletes_dir_when_only_desktop_prerelease_files(
        self, tmp_path
    ):
        """Desktop-only stale prerelease dir should be fully removed."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)
        prerelease_base = app_dir / DESKTOP_PRERELEASES_DIR_NAME
        prerelease_base.mkdir(exist_ok=True)

        version_dir = prerelease_base / "v2.7.13-open.1"
        version_dir.mkdir()
        (version_dir / "Meshtastic-2.7.13-open.1.dmg").write_bytes(b"dmg")

        stable = Release(tag_name="v2.7.14", prerelease=False)
        dl.handle_prereleases = Mock(return_value=[])
        dl.cleanup_prerelease_directories(cached_releases=[stable])

        assert not version_dir.exists()


# ---------------------------------------------------------------------------
# Release notes coexistence tests
# ---------------------------------------------------------------------------


class TestReleaseNotesCoexistence:
    """Both platform release notes should coexist in the same version directory."""

    def test_both_release_notes_survive_android_cleanup(self, tmp_path):
        """Android cleanup should not delete Desktop release notes."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.14"
        version_dir.mkdir()
        android_notes = version_dir / "release_notes-android-v2.7.14.md"
        desktop_notes = version_dir / "release_notes-desktop-v2.7.14.md"
        android_notes.write_text("android notes")
        desktop_notes.write_text("desktop notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert not android_notes.exists()
        assert desktop_notes.exists()

    def test_both_release_notes_survive_desktop_cleanup(self, tmp_path):
        """Desktop cleanup should not delete Android release notes."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.14"
        version_dir.mkdir()
        android_notes = version_dir / "release_notes-android-v2.7.14.md"
        desktop_notes = version_dir / "release_notes-desktop-v2.7.14.md"
        android_notes.write_text("android notes")
        desktop_notes.write_text("desktop notes")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert android_notes.exists()
        assert not desktop_notes.exists()

    def test_mixed_dir_kept_when_both_platforms_have_release_notes_only(self, tmp_path):
        """A dir with only release notes from both platforms should be kept."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        version_dir = app_dir / "v2.7.14"
        version_dir.mkdir()
        (version_dir / "release_notes-android-v2.7.14.md").write_text("android")
        (version_dir / "release_notes-desktop-v2.7.14.md").write_text("desktop")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert version_dir.exists()
        assert not (version_dir / "release_notes-android-v2.7.14.md").exists()
        assert (version_dir / "release_notes-desktop-v2.7.14.md").exists()


# ---------------------------------------------------------------------------
# Non-version directory protection tests
# ---------------------------------------------------------------------------


class TestNonVersionDirectoryProtection:
    """Non-version directories should not be subject to file-level pruning."""

    def test_android_cleanup_skips_non_version_dir_without_android_assets(
        self, tmp_path
    ):
        """Non-version dirs without Android assets should be skipped."""
        dl = _make_android_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        non_version_dir = app_dir / "some-random-dir"
        non_version_dir.mkdir()
        (non_version_dir / "desktop-only.dmg").write_bytes(b"dmg")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert non_version_dir.exists()
        assert (non_version_dir / "desktop-only.dmg").exists()

    def test_desktop_cleanup_skips_non_version_dir_without_desktop_assets(
        self, tmp_path
    ):
        """Non-version dirs without Desktop assets should be skipped."""
        dl = _make_desktop_downloader(tmp_path)
        app_dir = tmp_path / APP_DIR_NAME
        app_dir.mkdir(parents=True)

        non_version_dir = app_dir / "some-random-dir"
        non_version_dir.mkdir()
        (non_version_dir / "android-only.apk").write_bytes(b"apk")

        releases = [Release(tag_name="v2.7.15", prerelease=False)]
        dl.cleanup_prerelease_directories(cached_releases=releases)

        assert non_version_dir.exists()
        assert (non_version_dir / "android-only.apk").exists()
