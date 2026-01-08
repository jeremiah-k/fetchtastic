"""
Tests for channel suffixes feature (adding -alpha/-beta/-rc to release directories)
"""

import os
import tempfile
from pathlib import Path

import pytest

from fetchtastic.constants import APKS_DIR_NAME, FIRMWARE_DIR_NAME
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import Release


@pytest.mark.unit
@pytest.mark.core_downloads
class TestChannelSuffixes:
    """Tests for channel suffixes configuration in firmware and APK downloaders."""

    def test_firmware_channel_suffix_enabled(self, tmp_path):
        """Firmware should add -alpha suffix when ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES is True."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="Meshtastic Firmware 1.0.0 Alpha",
            body="This is an alpha release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0-alpha"

    def test_firmware_channel_suffix_disabled(self, tmp_path):
        """Firmware should NOT add -alpha suffix when ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES is False."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": False,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="Meshtastic Firmware 1.0.0 Alpha",
            body="This is an alpha release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0"

    def test_firmware_beta_suffix(self, tmp_path):
        """Firmware should add -beta suffix for beta releases when enabled."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v2.0.0",
            prerelease=False,
            name="Meshtastic Firmware 2.0.0 Beta",
            body="This is a beta release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v2.0.0-beta"

    def test_firmware_prerelease_no_suffix(self, tmp_path):
        """Firmware prereleases should NOT get channel suffixes."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.1",
            prerelease=True,
            name="Meshtastic Firmware 1.0.1 Prerelease",
            body="This is a prerelease",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        # Prereleases should not get channel suffixes
        assert storage_tag == "v1.0.1"

    def test_firmware_stable_no_suffix(self, tmp_path):
        """Firmware stable releases should NOT get suffixes."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v2.0.0",
            prerelease=False,
            name="Meshtastic Firmware 2.0.0 Stable",
            body="This is a stable release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v2.0.0"

    def test_android_channel_suffix_disabled(self, tmp_path):
        """Android APK should NOT add channel suffixes regardless of ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES setting."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="Meshtastic Android 1.0.0 Alpha",
            body="This is an alpha release",
        )

        target_path = downloader.get_target_path_for_release(
            release.tag_name, "app.apk", is_prerelease=False, release=release
        )
        version_dir = Path(target_path).parent
        assert str(version_dir.name) == "v1.0.0"

    def test_android_prerelease_no_suffix(self, tmp_path):
        """Android prereleases should NOT get channel suffixes."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.1-open",
            prerelease=True,
            name="Meshtastic Android 1.0.1 Prerelease",
            body="This is a prerelease",
        )

        target_path = downloader.get_target_path_for_release(
            release.tag_name, "app.apk"
        )
        # Prereleases should go to prerelease subdirectory
        version_dir = Path(target_path).parent
        assert "prerelease" in str(version_dir)
        assert str(version_dir.name) == "v1.0.1-open"

    def test_android_stable_no_suffix(self, tmp_path):
        """Android stable releases should NOT get suffixes."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)

        release = Release(
            tag_name="v2.0.0",
            prerelease=False,
            name="Meshtastic Android 2.0.0 Stable",
            body="This is a stable release",
        )

        target_path = downloader.get_target_path_for_release(
            release.tag_name, "app.apk", is_prerelease=False, release=release
        )
        version_dir = Path(target_path).parent
        assert str(version_dir.name) == "v2.0.0"

    def test_android_ensure_release_notes_no_suffix(self, tmp_path):
        """Android ensure_release_notes should NOT use channel suffixes."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = MeshtasticAndroidAppDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="Meshtastic Android 1.0.0 Alpha",
            body="Alpha release notes",
        )

        notes_path = downloader.ensure_release_notes(release)
        assert notes_path is not None
        assert "v1.0.0" in notes_path
        assert (Path(config["DOWNLOAD_DIR"]) / APKS_DIR_NAME / "v1.0.0").exists()

    def test_revoked_alpha_channel_suffix(self, tmp_path):
        """Revoked alpha releases should produce v1.0.0-revoked (no -alpha suffix)."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="(Revoked) Meshtastic Firmware 1.0.0 Alpha",
            body="This is an alpha release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0-revoked"

    def test_revoked_beta_channel_suffix(self, tmp_path):
        """Revoked beta releases should produce v1.0.0-revoked (replaces -beta suffix)."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="(Revoked) Meshtastic Firmware 1.0.0 Beta",
            body="This is a beta release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0-revoked"

    def test_revoked_rc_channel_suffix(self, tmp_path):
        """Revoked rc releases should produce v1.0.0-revoked (replaces -rc suffix)."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="(Revoked) Meshtastic Firmware 1.0.0 RC",
            body="This is a rc release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0-revoked"

    def test_revoked_stable_channel_suffix(self, tmp_path):
        """Revoked stable releases should produce v1.0.0-revoked (no channel suffix)."""
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        config = {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": True,
        }
        downloader = FirmwareReleaseDownloader(config, cache_manager)

        release = Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="(Revoked) Meshtastic Firmware 1.0.0 Stable",
            body="This is a stable release",
        )

        storage_tag = downloader._get_release_storage_tag(release)
        assert storage_tag == "v1.0.0-revoked"
