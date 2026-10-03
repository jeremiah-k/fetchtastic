"""Rolling desktop installer contracts from the upstream snapshot workflow."""

from unittest.mock import Mock

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release

pytestmark = pytest.mark.core_downloads

DESKTOP_NAMES = [
    "meshtastic-desktop-2.8.3-1.aarch64-29322596.rpm",
    "meshtastic-desktop-2.8.3-1.x86_64-29322596.rpm",
    "meshtastic-desktop_2.8.3_amd64-29322596.deb",
    "meshtastic-desktop_2.8.3_arm64-29322596.deb",
    "Meshtastic.Desktop-2.8.3-29322596.dmg",
    "Meshtastic.Desktop-2.8.3-29322596.exe",
    "Meshtastic.Desktop-2.8.3-29322596.msi",
    "Meshtastic_Desktop-snapshot-aarch64-29322596.AppImage",
    "Meshtastic_Desktop-snapshot-x86_64-29322596.AppImage",
]


@pytest.fixture
def downloader(tmp_path):
    return MeshtasticClientAppDownloader(
        {
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "SAVE_CLIENT_APPS": True,
            "SELECTED_APP_ASSETS": ["*"],
            "CHECK_APP_SNAPSHOTS": True,
            "EXCLUDE_PATTERNS": [],
        },
        CacheManager(cache_dir=str(tmp_path / "cache")),
    )


def make_release(names=DESKTOP_NAMES):
    return Release(
        tag_name="snapshot",
        prerelease=True,
        assets=[Asset(name, "https://example.invalid/" + name, 3) for name in names],
    )


@pytest.mark.parametrize("name", DESKTOP_NAMES)
def test_upstream_desktop_version_codes(name):
    assert MeshtasticClientAppDownloader.parse_snapshot_version_code(name) == 29322596


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("meshtastic.dmg", [DESKTOP_NAMES[4]]),
        ("Meshtastic.Desktop-2.8.3.msi", [DESKTOP_NAMES[6]]),
        ("*.exe", [DESKTOP_NAMES[5]]),
        ("meshtastic-desktop_amd64.deb", [DESKTOP_NAMES[2]]),
        ("meshtastic-desktop-1.x86_64.rpm", [DESKTOP_NAMES[1]]),
        ("meshtastic_desktop-x86_64.appimage", [DESKTOP_NAMES[8]]),
        ("Meshtastic_Desktop-snapshot-aarch64-*.AppImage", [DESKTOP_NAMES[7]]),
        ("*", DESKTOP_NAMES),
        ("*.apk", []),
    ],
)
def test_desktop_snapshot_selection_preserves_platform_and_architecture(
    downloader, pattern, expected
):
    downloader.config["SELECTED_APP_ASSETS"] = [pattern]
    release = make_release()
    assert downloader.handle_snapshots(release) is release
    assert [
        a.name for a in downloader.get_selected_snapshot_assets(release)
    ] == expected


def test_mixed_android_and_desktop_generations_are_rejected(downloader):
    release = make_release(
        [*DESKTOP_NAMES, "androidApp-google-universal-debug-29322597.apk"]
    )
    assert downloader.get_snapshot_version_code(release) is None
    assert downloader.get_selected_snapshot_assets(release) == []


def test_desktop_snapshot_exclusions(downloader):
    downloader.config["EXCLUDE_PATTERNS"] = ["*.AppImage"]
    selected = downloader.get_selected_snapshot_assets(make_release())
    assert len(selected) == 7
    assert not any(a.name.endswith(".AppImage") for a in selected)


def test_desktop_snapshot_download_and_completeness(downloader):
    downloader.config["SELECTED_APP_ASSETS"] = ["meshtastic.dmg"]
    release = make_release([DESKTOP_NAMES[4]])
    asset = release.assets[0]

    def download(_url, target):
        with open(target, "wb") as output:
            output.write(b"dmg")
        return True

    downloader.download = Mock(side_effect=download)
    downloader.verify = Mock(return_value=True)
    assert not downloader.is_snapshot_complete(release, 29322596)
    result = downloader.download_snapshot_asset(release, asset, 29322596)
    assert result.success and not result.was_skipped
    assert downloader.is_snapshot_complete(release, 29322596)
    assert downloader.update_snapshot_tracking(29322596)
    assert not downloader.should_process_snapshot(release, 29322596)
    assert downloader.download_snapshot_asset(release, asset, 29322596).was_skipped
    downloader.download.assert_called_once()
