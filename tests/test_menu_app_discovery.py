"""Discovery and setup share one policy for every installer format."""

from unittest.mock import Mock

import pytest
import requests

from fetchtastic import menu_app
from fetchtastic.constants import MESHTASTIC_CLIENT_APP_RELEASES_URL

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]

FORMATS = ("apk", "dmg", "exe", "msi", "deb", "rpm", "AppImage")


def release(tag, filenames, prerelease=False):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "assets": [{"name": name, "size": 42} for name in filenames],
    }


@pytest.mark.parametrize("extension", FORMATS)
def test_discovery_prefers_stable_and_preserves_format_fallback(mocker, extension):
    response = Mock()
    response.json.return_value = [
        release("snapshot", [f"ignored.{extension}"]),
        release("v3.0-internal.1", [f"preview.{extension}"]),
        release("v2.9", ["readme.txt"]),
        release("v2.8", [f"selected.{extension}"]),
    ]
    request = mocker.patch.object(
        menu_app, "make_github_api_request", return_value=response
    )
    assert menu_app.fetch_app_assets() == [
        {"name": f"selected.{extension}", "size": 42}
    ]
    request.assert_called_once_with(MESHTASTIC_CLIENT_APP_RELEASES_URL)


@pytest.mark.parametrize("extension", FORMATS)
def test_discovery_uses_prerelease_when_no_stable_format_exists(mocker, extension):
    response = Mock()
    response.json.return_value = [release("v3.0-open.1", [f"preview.{extension}"])]
    mocker.patch.object(menu_app, "make_github_api_request", return_value=response)
    assert menu_app.fetch_app_assets()[0]["name"] == f"preview.{extension}"


def test_one_request_discovers_mixed_formats_across_partial_releases(mocker):
    response = Mock()
    response.json.return_value = [
        release("v3.0", ["app.apk", "readme.txt"]),
        release("v2.9", [f"installer.{extension}" for extension in FORMATS[1:]]),
    ]
    request = mocker.patch.object(
        menu_app, "make_github_api_request", return_value=response
    )
    assert len(menu_app.fetch_app_assets()) == len(FORMATS)
    request.assert_called_once()


@pytest.mark.parametrize("tag", ["snapshot", "Snapshot", " SNAPSHOT ", None, {}, 7])
def test_discovery_excludes_snapshot_tags_consistently(mocker, tag):
    response = Mock()
    response.json.return_value = [
        release(tag, ["androidApp-google-debug-29322596.apk", "snapshot.dmg"]),
        release("v2.8.3", ["app-google-release.apk", "stable.dmg"]),
    ]
    mocker.patch.object(menu_app, "make_github_api_request", return_value=response)
    assert [asset["name"] for asset in menu_app.fetch_app_assets()] == [
        "app-google-release.apk",
        "stable.dmg",
    ]


@pytest.mark.parametrize("payload", [None, {}, "invalid", 5])
def test_invalid_release_response_fails_clearly(mocker, payload, capsys):
    response = Mock()
    response.json.return_value = payload
    mocker.patch.object(menu_app, "make_github_api_request", return_value=response)
    picker = mocker.patch.object(menu_app, "pick")
    assert menu_app.run_menu() is None
    picker.assert_not_called()
    assert "unable to fetch client app assets" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout("timeout"),
        OSError("io"),
        ValueError("json"),
        RuntimeError("failed"),
    ],
)
def test_menu_handles_discovery_failure(mocker, error):
    mocker.patch.object(menu_app, "fetch_app_assets", side_effect=error)
    assert menu_app.run_menu() is None


def test_menu_uses_shared_assets(mocker):
    mocker.patch.object(
        menu_app, "fetch_app_assets", return_value=["app.apk", "Installer-2.8.0.dmg"]
    )
    picker = mocker.patch.object(
        menu_app,
        "pick",
        return_value=[("Android: app.apk", 0), ("macOS: Installer-2.8.0.dmg", 1)],
    )
    assert menu_app.run_menu() == {"selected_assets": ["app.apk", "installer.dmg"]}
    picker.assert_called_once()


def test_invalid_asset_entries_are_ignored(mocker):
    response = Mock()
    response.json.return_value = [
        None,
        {
            "tag_name": "v3.0",
            "assets": [None, {}, {"name": 42}, {"name": "APP.APK", "size": "invalid"}],
        },
    ]
    mocker.patch.object(menu_app, "make_github_api_request", return_value=response)
    assert menu_app.fetch_app_assets() == [{"name": "APP.APK", "size": 0}]
