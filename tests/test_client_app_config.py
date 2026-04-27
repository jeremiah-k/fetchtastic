import pytest

from fetchtastic.client_app_config import normalize_client_app_config

pytestmark = [pytest.mark.unit, pytest.mark.configuration]


def test_normalize_client_app_config_unions_legacy_asset_selection():
    config = {
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APK_ASSETS": ["app-fdroid-universal-release.apk"],
        "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg"],
        "ANDROID_VERSIONS_TO_KEEP": 1,
        "DESKTOP_VERSIONS_TO_KEEP": 3,
        "CHECK_APK_PRERELEASES": False,
        "CHECK_DESKTOP_PRERELEASES": True,
    }

    normalized = normalize_client_app_config(config)

    assert normalized["SAVE_CLIENT_APPS"] is True
    assert "app-fdroid-universal-release.apk" in normalized["SELECTED_APP_ASSETS"]
    assert "meshtastic.dmg" in normalized["SELECTED_APP_ASSETS"]
    assert normalized["APP_VERSIONS_TO_KEEP"] == 3
    assert normalized["CHECK_APP_PRERELEASES"] is True


def test_new_client_app_keys_are_authoritative():
    config = {
        "SAVE_CLIENT_APPS": False,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": ["meshtastic.msi"],
        "SELECTED_APK_ASSETS": ["app.apk"],
        "APP_VERSIONS_TO_KEEP": 4,
        "ANDROID_VERSIONS_TO_KEEP": 1,
        "DESKTOP_VERSIONS_TO_KEEP": 2,
        "CHECK_APP_PRERELEASES": False,
        "CHECK_APK_PRERELEASES": True,
        "CHECK_DESKTOP_PRERELEASES": True,
    }

    normalized = normalize_client_app_config(config)

    assert normalized["SAVE_CLIENT_APPS"] is False
    assert normalized["SELECTED_APP_ASSETS"] == ["meshtastic.msi"]
    assert normalized["APP_VERSIONS_TO_KEEP"] == 4
    assert normalized["CHECK_APP_PRERELEASES"] is False


def test_ambiguous_client_app_asset_does_not_use_apk_substring_guess():
    config = {
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release"],
    }

    normalized = normalize_client_app_config(config)

    assert normalized["SELECTED_APP_ASSETS"] == ["app-fdroid-universal-release"]
    assert normalized["SELECTED_APK_ASSETS"] == []
    assert normalized["SELECTED_DESKTOP_ASSETS"] == []
    assert normalized["SAVE_APKS"] is True
    assert normalized["SAVE_DESKTOP_APP"] is True
