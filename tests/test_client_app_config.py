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
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


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
    assert "SELECTED_APK_ASSETS" not in normalized
    assert "SELECTED_DESKTOP_ASSETS" not in normalized
    assert "SAVE_APKS" not in normalized
    assert "SAVE_DESKTOP_APP" not in normalized
    assert normalized["APP_VERSIONS_TO_KEEP"] == 4
    assert normalized["CHECK_APP_PRERELEASES"] is False
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


def test_explicit_platform_prerelease_opt_out_survives_legacy_union():
    config = {
        "CHECK_APK_PRERELEASES": True,
        "CHECK_DESKTOP_PRERELEASES": False,
    }

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is True
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


def test_explicit_primary_prerelease_sets_missing_platform_mirrors():
    config = {"CHECK_APP_PRERELEASES": True}

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is True
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


def test_empty_primary_client_app_assets_disable_legacy_save_flags():
    config = {
        "SAVE_CLIENT_APPS": True,
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": [],
        "SELECTED_APK_ASSETS": ["app.apk"],
        "SELECTED_DESKTOP_ASSETS": ["Meshtastic.dmg"],
    }

    normalized = normalize_client_app_config(config)

    assert normalized["SELECTED_APP_ASSETS"] == []
    assert "SELECTED_APK_ASSETS" not in normalized
    assert "SELECTED_DESKTOP_ASSETS" not in normalized
    assert "SAVE_APKS" not in normalized
    assert "SAVE_DESKTOP_APP" not in normalized


def test_explicit_apk_prerelease_false_does_not_default_desktop_true():
    config = {
        "CHECK_APK_PRERELEASES": False,
    }

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is False
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


def test_legacy_save_apks_preserved_when_no_selection_keys():
    """Legacy SAVE_APKS=True without selections: SAVE_CLIENT_APPS=True, but no assets to download."""
    config = {"SAVE_APKS": True}

    normalized = normalize_client_app_config(config)

    assert normalized["SAVE_CLIENT_APPS"] is True
    # Empty selection means nothing to download; legacy booleans alone cannot
    # bypass should_download_asset, so SAVE_* sub-flags are forced False.
    assert "SAVE_APKS" not in normalized
    assert normalized["SELECTED_APP_ASSETS"] == []


def test_legacy_save_desktop_app_preserved_when_no_selection_keys():
    """Legacy SAVE_DESKTOP_APP=True without selections: SAVE_CLIENT_APPS=True, but no assets to download."""
    config = {"SAVE_DESKTOP_APP": True}

    normalized = normalize_client_app_config(config)

    assert normalized["SAVE_CLIENT_APPS"] is True
    assert "SAVE_DESKTOP_APP" not in normalized
    assert normalized["SELECTED_APP_ASSETS"] == []


def test_empty_selected_apk_assets_disables_legacy_save():
    config = {
        "SAVE_APKS": True,
        "SELECTED_APK_ASSETS": [],
    }

    normalized = normalize_client_app_config(config)

    assert "SAVE_APKS" not in normalized
    assert "SAVE_DESKTOP_APP" not in normalized


def test_empty_selected_app_assets_disables_legacy_save():
    config = {
        "SAVE_APKS": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_APP_ASSETS": [],
    }

    normalized = normalize_client_app_config(config)

    assert "SAVE_APKS" not in normalized
    assert "SAVE_DESKTOP_APP" not in normalized


def test_ambiguous_client_app_asset_does_not_use_apk_substring_guess():
    config = {
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": ["app-fdroid-universal-release"],
    }

    normalized = normalize_client_app_config(config)

    assert normalized["SELECTED_APP_ASSETS"] == ["app-fdroid-universal-release"]
    assert "SELECTED_APK_ASSETS" not in normalized
    assert "SELECTED_DESKTOP_ASSETS" not in normalized
    assert "SAVE_APKS" not in normalized
    assert "SAVE_DESKTOP_APP" not in normalized


# --- Regression: explicit primary CHECK_APP_PRERELEASES overrides legacy flags ---


@pytest.mark.unit
@pytest.mark.configuration
def test_explicit_primary_false_overrides_true_legacy_apk_prerelease():
    """CHECK_APP_PRERELEASES=False must override CHECK_APK_PRERELEASES=True."""
    config = {"CHECK_APP_PRERELEASES": False, "CHECK_APK_PRERELEASES": True}

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is False
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


@pytest.mark.unit
@pytest.mark.configuration
def test_explicit_primary_false_overrides_true_legacy_desktop_prerelease():
    """CHECK_APP_PRERELEASES=False must override CHECK_DESKTOP_PRERELEASES=True."""
    config = {"CHECK_APP_PRERELEASES": False, "CHECK_DESKTOP_PRERELEASES": True}

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is False
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


@pytest.mark.unit
@pytest.mark.configuration
def test_absent_primary_still_ors_legacy_prerelease_flags():
    """Absent CHECK_APP_PRERELEASES still ORs legacy prerelease flags."""
    config = {
        "CHECK_APK_PRERELEASES": False,
        "CHECK_DESKTOP_PRERELEASES": True,
        "SAVE_DESKTOP_APP": True,
        "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg"],
    }

    normalized = normalize_client_app_config(config)

    assert normalized["CHECK_APP_PRERELEASES"] is True
    assert "CHECK_APK_PRERELEASES" not in normalized
    assert "CHECK_DESKTOP_PRERELEASES" not in normalized


def test_normalization_is_idempotent_and_preserves_unknown_settings():
    config = {"SAVE_APKS": "yes", "SELECTED_APK_ASSETS": ["app.apk"], "EXTRA": 42}
    normalized = normalize_client_app_config(config)
    snapshot = dict(normalized)
    assert normalize_client_app_config(normalized) == snapshot
    assert normalized["EXTRA"] == 42
    assert normalized["SAVE_CLIENT_APPS"] is True


@pytest.mark.parametrize("value,expected", [("false", False), ("yes", True)])
def test_primary_save_setting_coerces_strings(value, expected):
    assert (
        normalize_client_app_config({"SAVE_CLIENT_APPS": value})["SAVE_CLIENT_APPS"]
        is expected
    )
