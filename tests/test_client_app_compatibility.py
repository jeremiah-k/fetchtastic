"""Compatibility coverage for the unified client app boundary."""

from unittest.mock import Mock

import pytest
import requests

from fetchtastic import menu_apk, menu_app, menu_desktop
from fetchtastic.download.android import MeshtasticAndroidAppDownloader
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.cli_integration import DownloadCLIIntegration
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.desktop import (
    MIN_DESKTOP_TRACKED_VERSION,
    MeshtasticDesktopDownloader,
    _is_desktop_prerelease_by_name,
)
from fetchtastic.download.interfaces import Asset, DownloadResult, Release


def test_legacy_selectors_preserve_platform_specific_contracts(monkeypatch):
    seen_options: list[list[str]] = []

    def pick_first(options, *_args, **_kwargs):
        seen_options.append(list(options))
        return [(options[0], 0)]

    monkeypatch.setattr(menu_app, "pick", pick_first)

    assert menu_apk.select_assets(
        [
            {"name": "app-google-release.apk", "size": 1048576},
            {"name": "Meshtastic.Desktop-2.8.3.dmg", "size": 2},
        ]
    ) == {"selected_assets": ["app-google-release.apk"]}
    assert seen_options.pop() == ["Android: app-google-release.apk"]

    assert menu_desktop.select_assets(
        [
            "app-google-release.apk",
            "meshtastic-desktop_2.8.3_amd64.deb",
            "Meshtastic.Desktop-2.8.3.dmg",
            "Meshtastic.Desktop-2.8.3.exe",
        ]
    ) == {"selected_assets": ["meshtastic-desktop_amd64.deb"]}
    assert seen_options.pop() == [
        "Linux: meshtastic-desktop_2.8.3_amd64.deb",
        "macOS: Meshtastic.Desktop-2.8.3.dmg",
        "Windows: Meshtastic.Desktop-2.8.3.exe",
    ]


def test_legacy_asset_fetchers_preserve_platform_contracts(monkeypatch):
    assets = [
        {"name": "app-google-release.apk", "size": 1},
        {"name": "Meshtastic.Desktop-2.8.3.dmg", "size": 2},
    ]
    monkeypatch.setattr(menu_apk, "fetch_app_assets", lambda: assets)
    monkeypatch.setattr(menu_desktop, "fetch_app_assets", lambda: assets)

    assert menu_apk.fetch_apk_assets() == [assets[0]]
    assert menu_desktop.fetch_desktop_assets() == [assets[1]["name"]]


def test_legacy_asset_fetchers_preserve_failure_contracts(monkeypatch):
    def fail():
        raise requests.RequestException("offline")

    monkeypatch.setattr(menu_apk, "fetch_app_assets", fail)
    monkeypatch.setattr(menu_desktop, "fetch_app_assets", fail)

    assert menu_apk.fetch_apk_assets() == []
    assert menu_desktop.fetch_desktop_assets() is None


def test_legacy_run_menus_keep_platform_scoping(monkeypatch):
    assets = [
        {"name": "app-google-release.apk", "size": 1},
        {"name": "Meshtastic.Desktop-2.8.3.dmg", "size": 2},
    ]
    apk_seen = []
    desktop_seen = []
    monkeypatch.setattr(menu_apk, "fetch_app_assets", lambda: assets)
    monkeypatch.setattr(menu_desktop, "fetch_app_assets", lambda: assets)
    monkeypatch.setattr(
        menu_apk,
        "select_assets",
        lambda selected: apk_seen.extend(selected) or {"selected_assets": ["apk"]},
    )
    monkeypatch.setattr(
        menu_desktop,
        "select_assets",
        lambda selected: desktop_seen.extend(selected)
        or {"selected_assets": ["desktop"]},
    )

    assert menu_apk.run_menu() == {"selected_assets": ["apk"]}
    assert menu_desktop.run_menu() == {"selected_assets": ["desktop"]}
    assert apk_seen == [assets[0]]
    assert desktop_seen == [assets[1]["name"]]


def test_legacy_run_menus_handle_empty_and_failed_fetch(monkeypatch):
    monkeypatch.setattr(menu_apk, "fetch_app_assets", lambda: [])
    monkeypatch.setattr(menu_desktop, "fetch_app_assets", lambda: [])
    assert menu_apk.run_menu() is None
    assert menu_desktop.run_menu() is None

    def fail():
        raise OSError("offline")

    monkeypatch.setattr(menu_desktop, "fetch_app_assets", fail)
    assert menu_desktop.run_menu() is None


def test_legacy_run_menus_contain_selector_exceptions(monkeypatch):
    monkeypatch.setattr(
        menu_apk,
        "fetch_apk_assets",
        lambda: [{"name": "app-google-release.apk", "size": 1}],
    )
    monkeypatch.setattr(
        menu_desktop,
        "fetch_desktop_assets",
        lambda: ["Meshtastic.Desktop-2.8.3.dmg"],
    )

    def fail(_assets):
        raise RuntimeError("selector failed")

    monkeypatch.setattr(menu_apk, "select_assets", fail)
    monkeypatch.setattr(menu_desktop, "select_assets", fail)
    assert menu_apk.run_menu() is None
    assert menu_desktop.run_menu() is None


def test_legacy_selectors_reject_non_platform_and_malformed_assets(capsys):
    assert (
        menu_apk.select_assets(["Meshtastic.Desktop-2.8.3.dmg", "", object()]) is None
    )
    assert (
        menu_desktop.select_assets(["app-google-release.apk", {"name": 7}, object()])
        is None
    )
    output = capsys.readouterr().out
    assert "No valid APK files found" in output
    assert "No desktop files found" in output


def test_legacy_run_menus_contain_expected_and_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        menu_apk,
        "fetch_apk_assets",
        lambda: [{"name": "app-google-release.apk", "size": 1}],
    )
    monkeypatch.setattr(
        menu_desktop,
        "fetch_desktop_assets",
        lambda: ["Meshtastic.Desktop-2.8.3.dmg"],
    )

    monkeypatch.setattr(
        menu_apk,
        "select_assets",
        lambda _assets: (_ for _ in ()).throw(ValueError("bad")),
    )
    monkeypatch.setattr(
        menu_desktop,
        "select_assets",
        lambda _assets: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert menu_apk.run_menu() is None
    assert menu_desktop.run_menu() is None

    monkeypatch.setattr(
        menu_apk,
        "select_assets",
        lambda _assets: (_ for _ in ()).throw(BaseException("unexpected")),
    )
    monkeypatch.setattr(
        menu_desktop,
        "select_assets",
        lambda _assets: (_ for _ in ()).throw(BaseException("unexpected")),
    )
    # BaseException is deliberately outside the compatibility catch-all.
    with pytest.raises(BaseException, match="unexpected"):
        menu_apk.run_menu()
    with pytest.raises(BaseException, match="unexpected"):
        menu_desktop.run_menu()


def test_legacy_download_result_translation_leaves_unknown_types_unchanged(
    tmp_path, monkeypatch
):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["*"],
    }
    cache = CacheManager(cache_dir=str(tmp_path / "cache"))
    release = Release(tag_name="v2.8.3")
    asset = Asset("asset.bin", "https://example.invalid/asset.bin", 1)

    android = MeshtasticAndroidAppDownloader(dict(config), cache)
    desktop = MeshtasticDesktopDownloader(dict(config), cache)
    android_result = DownloadResult(success=True, file_type="other")
    desktop_result = DownloadResult(success=True, file_type="other")
    monkeypatch.setattr(android, "download_app", Mock(return_value=android_result))
    monkeypatch.setattr(desktop, "download_app", Mock(return_value=desktop_result))

    assert android.download_apk(release, asset).file_type == "other"
    assert desktop.download_desktop(release, asset).file_type == "other"


def test_desktop_public_pattern_helpers_remain_available():
    assert MIN_DESKTOP_TRACKED_VERSION == (2, 7, 14)
    assert menu_desktop.PLATFORM_GROUPS["macOS"] == [".dmg"]
    assert (
        menu_desktop.extract_wildcard_pattern("Meshtastic.Desktop-2.8.3.dmg")
        == "meshtastic.desktop.dmg"
    )
    assert not _is_desktop_prerelease_by_name("v2.7.13-open.1")
    assert _is_desktop_prerelease_by_name("v2.7.14-open.1")


def test_legacy_downloader_classes_remain_scoped(tmp_path, monkeypatch):
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["*"],
        "CHECK_APP_PRERELEASES": True,
    }
    cache = CacheManager(cache_dir=str(tmp_path / "cache"))
    android = MeshtasticAndroidAppDownloader(dict(config), cache)
    desktop = MeshtasticDesktopDownloader(dict(config), cache)
    release = Release(
        tag_name="v2.8.3",
        assets=[
            Asset("app-google-release.apk", "https://example.invalid/app.apk", 1),
            Asset("Meshtastic.Desktop-2.8.3.dmg", "https://example.invalid/app.dmg", 2),
        ],
    )

    assert MeshtasticAndroidAppDownloader is not MeshtasticClientAppDownloader
    assert MeshtasticDesktopDownloader is not MeshtasticClientAppDownloader
    assert [asset.name for asset in android.get_assets(release)] == [
        "app-google-release.apk"
    ]
    assert [asset.name for asset in desktop.get_assets(release)] == [
        "Meshtastic.Desktop-2.8.3.dmg"
    ]
    assert android.should_download_asset("Meshtastic.Desktop-2.8.3.dmg") is False
    assert desktop.should_download_asset("app-google-release.apk") is False

    apk_result = DownloadResult(success=True, file_type="client_app")
    android_download = Mock(return_value=apk_result)
    monkeypatch.setattr(android, "download_app", android_download)
    assert android.download_apk(release, release.assets[0]).file_type == "android"

    apk_prerelease_result = DownloadResult(
        success=True, file_type="client_app_prerelease"
    )
    android_download.return_value = apk_prerelease_result
    assert (
        android.download_apk(release, release.assets[0]).file_type
        == "android_prerelease"
    )

    dmg_result = DownloadResult(success=True, file_type="client_app_prerelease")
    desktop_download = Mock(return_value=dmg_result)
    monkeypatch.setattr(desktop, "download_app", desktop_download)
    assert (
        desktop.download_desktop(release, release.assets[1]).file_type
        == "desktop_prerelease"
    )


def test_shared_cache_failure_is_reported():
    class CacheFailure:
        @staticmethod
        def clear_cache():
            return False

    integration = DownloadCLIIntegration()
    integration.client_app_downloader = CacheFailure()
    assert integration._clear_caches() is False
