import pytest

from fetchtastic.client_release_discovery import (
    extract_matching_asset_dicts,
    extract_matching_asset_names,
    is_android_asset_name,
    is_android_prerelease_tag,
    is_desktop_asset_name,
    is_desktop_prerelease_tag,
    is_release_at_or_above_minimum,
    is_release_prerelease,
    release_has_matching_assets,
    select_best_release_with_assets,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


class _DummyVersionManager:
    def __init__(self, mapping: dict[str, tuple[int, ...] | None]):
        self._mapping = mapping

    def get_release_tuple(self, version: str | None) -> tuple[int, ...] | None:
        return self._mapping.get(version or "")


def test_is_android_asset_name():
    assert is_android_asset_name("Meshtastic.apk") is True
    assert is_android_asset_name("Meshtastic.APK") is True
    assert is_android_asset_name("Meshtastic.dmg") is False


def test_is_desktop_asset_name():
    assert is_desktop_asset_name("Meshtastic-2.7.14.dmg") is True
    assert is_desktop_asset_name("Meshtastic_x64_2.7.14.MSI") is True
    assert is_desktop_asset_name("Meshtastic-2.7.14.AppImage") is True
    assert is_desktop_asset_name("Meshtastic-2.7.14.appimage") is True
    assert is_desktop_asset_name("Meshtastic.apk") is False


def test_is_android_prerelease_tag():
    assert is_android_prerelease_tag("v2.7.20-open.1") is True
    assert is_android_prerelease_tag("v2.7.20-CLOSED.1") is True
    assert is_android_prerelease_tag("v2.7.20") is False


def test_is_desktop_prerelease_tag():
    assert is_desktop_prerelease_tag("v2.7.20-open.1") is True
    assert is_desktop_prerelease_tag("v2.7.20-closed.1") is True
    assert is_desktop_prerelease_tag("v2.7.20-INTERNAL.1") is True
    assert is_desktop_prerelease_tag("v2.7.20") is False


def test_release_has_matching_assets():
    release = {
        "assets": [
            {"name": "notes.txt"},
            {"name": "Meshtastic.apk"},
            {"size": 10},
            "invalid",
        ]
    }
    assert release_has_matching_assets(
        release, asset_name_matcher=is_android_asset_name
    )


def test_release_has_matching_assets_invalid_shape():
    release = {"assets": "not-a-list"}
    assert (
        release_has_matching_assets(release, asset_name_matcher=is_android_asset_name)
        is False
    )


def test_extract_matching_asset_names():
    release = {
        "assets": [
            {"name": "Meshtastic-2.7.14.dmg"},
            {"name": "notes.txt"},
            {"name": "Meshtastic_x64_2.7.14.msi"},
            {"size": 10},
        ]
    }
    assert extract_matching_asset_names(
        release,
        asset_name_matcher=is_desktop_asset_name,
    ) == ["Meshtastic-2.7.14.dmg", "Meshtastic_x64_2.7.14.msi"]


def test_extract_matching_asset_dicts_normalizes_sizes():
    release = {
        "assets": [
            {"name": "a.apk", "size": 10},
            {"name": "b.apk", "size": "20"},
            {"name": "c.apk", "size": "bad-size"},
            {"name": "d.apk", "size": -5},
            {"name": "notes.txt", "size": 1},
            {"size": 7},
            "invalid",
        ]
    }
    assert extract_matching_asset_dicts(
        release,
        asset_name_matcher=is_android_asset_name,
    ) == [
        {"name": "a.apk", "size": 10},
        {"name": "b.apk", "size": 20},
        {"name": "c.apk", "size": 0},
        {"name": "d.apk", "size": 0},
    ]


def test_is_release_prerelease_uses_github_flag():
    release = {"tag_name": "v2.7.20", "prerelease": True}
    assert is_release_prerelease(
        release,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )


def test_is_release_prerelease_uses_tag_matcher():
    release = {"tag_name": "v2.7.20-open.1", "prerelease": False}
    assert is_release_prerelease(
        release,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )


def test_select_best_release_with_assets_prefers_stable():
    releases = [
        {
            "tag_name": "v2.8.0-open.1",
            "prerelease": True,
            "assets": [{"name": "Meshtastic-pr.apk"}],
        },
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "Meshtastic-stable.apk"}],
        },
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )
    assert selected is not None
    assert selected["tag_name"] == "v2.7.20"


def test_select_best_release_with_assets_falls_back_to_prerelease():
    releases = [
        {
            "tag_name": "v2.8.0-open.1",
            "prerelease": True,
            "assets": [{"name": "Meshtastic-pr.apk"}],
        },
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "notes.txt"}],
        },
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )
    assert selected is not None
    assert selected["tag_name"] == "v2.8.0-open.1"


def test_select_best_release_with_assets_respects_scan_limit():
    releases = [
        {
            "tag_name": "v2.8.0-open.1",
            "prerelease": True,
            "assets": [{"name": "Meshtastic-pr.apk"}],
        },
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "Meshtastic-stable.apk"}],
        },
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
        max_releases_to_scan=1,
    )
    assert selected is not None
    assert selected["tag_name"] == "v2.8.0-open.1"


def test_select_best_release_with_assets_ignores_invalid_release_entries():
    releases = [
        "not-a-dict",
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "Meshtastic-stable.apk"}],
        },
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )
    assert selected is not None
    assert selected["tag_name"] == "v2.7.20"


def test_select_best_release_with_assets_returns_none_for_no_matches():
    releases = [
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "notes.txt"}],
        }
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
    )
    assert selected is None


def test_select_best_release_with_assets_returns_none_for_non_positive_scan_window():
    releases = [
        {
            "tag_name": "v2.7.20",
            "prerelease": False,
            "assets": [{"name": "Meshtastic-stable.apk"}],
        }
    ]
    selected = select_best_release_with_assets(
        releases,
        asset_name_matcher=is_android_asset_name,
        tag_prerelease_matcher=is_android_prerelease_tag,
        max_releases_to_scan=0,
    )
    assert selected is None


def test_is_release_at_or_above_minimum_unparsable_allowed():
    manager = _DummyVersionManager({"unknown-format": None})
    assert (
        is_release_at_or_above_minimum(
            "unknown-format",
            minimum_version=(2, 7, 14),
            version_manager=manager,
        )
        is True
    )


@pytest.mark.parametrize(
    "tag_name,minimum,expected",
    [
        ("v2.7.14", (2, 7, 14), True),
        ("v2.7.13", (2, 7, 14), False),
        ("v2.8", (2, 7, 14), True),
        ("v2.7", (2, 7, 14), False),
        ("v2.7.14.1", (2, 7, 14), True),
    ],
)
def test_is_release_at_or_above_minimum(tag_name, minimum, expected):
    manager = _DummyVersionManager(
        {
            "v2.7.14": (2, 7, 14),
            "v2.7.13": (2, 7, 13),
            "v2.8": (2, 8),
            "v2.7": (2, 7),
            "v2.7.14.1": (2, 7, 14, 1),
        }
    )
    assert (
        is_release_at_or_above_minimum(
            tag_name,
            minimum_version=minimum,
            version_manager=manager,
        )
        is expected
    )
