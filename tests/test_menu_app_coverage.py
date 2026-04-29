from unittest.mock import patch

import pytest

from fetchtastic.menu_app import (
    _asset_name,
    _normalize_assets,
    get_desktop_platform_label,
    run_menu,
    select_assets,
)

pytestmark = [pytest.mark.unit, pytest.mark.user_interface]


class TestAssetName:
    def test_non_empty_string(self):
        assert _asset_name("file.apk") == "file.apk"

    def test_empty_string(self):
        assert _asset_name("") is None

    def test_dict_with_name(self):
        assert _asset_name({"name": "app.apk"}) == "app.apk"

    def test_dict_with_empty_name(self):
        assert _asset_name({"name": ""}) is None

    def test_dict_with_non_string_name(self):
        assert _asset_name({"name": 42}) is None

    def test_dict_without_name_key(self):
        assert _asset_name({"url": "http://x"}) is None

    def test_other_type(self):
        assert _asset_name(123) is None

    def test_none_type(self):
        assert _asset_name(None) is None


class TestGetDesktopPlatformLabel:
    def test_dmg(self):
        assert get_desktop_platform_label("Meshtastic-2.7.14.dmg") == "macOS"

    def test_exe(self):
        assert get_desktop_platform_label("Meshtastic-2.7.14.exe") == "Windows"

    def test_msi(self):
        assert get_desktop_platform_label("setup.msi") == "Windows"

    def test_deb(self):
        assert get_desktop_platform_label("meshtasticd_2.5.13_amd64.deb") == "Linux"

    def test_rpm(self):
        assert get_desktop_platform_label("meshtastic-2.7.14.x86_64.rpm") == "Linux"

    def test_appimage(self):
        assert get_desktop_platform_label("Meshtastic-2.7.14.AppImage") == "Linux"

    def test_unknown_extension(self):
        assert get_desktop_platform_label("readme.txt") is None

    def test_no_extension(self):
        assert get_desktop_platform_label("somefile") is None


class TestNormalizeAssets:
    def test_apk_strings(self):
        result = _normalize_assets(["app.apk"], [])
        assert result == [("Android APK: app.apk", "app.apk")]

    def test_apk_dicts(self):
        result = _normalize_assets([{"name": "app.apk"}], [])
        assert result == [("Android APK: app.apk", "app.apk")]

    def test_apk_skips_empty_name(self):
        result = _normalize_assets(["", {"name": ""}, {"name": 5}], [])
        assert result == []

    def test_desktop_with_platform(self):
        result = _normalize_assets([], ["Meshtastic-2.7.14.dmg"])
        assert result == [("macOS: Meshtastic-2.7.14.dmg", "Meshtastic-2.7.14.dmg")]

    def test_desktop_unknown_platform(self):
        result = _normalize_assets([], ["readme.txt"])
        assert result == [("Desktop: readme.txt", "readme.txt")]

    def test_desktop_skips_empty(self):
        result = _normalize_assets([], ["", None])
        assert result == []

    def test_mixed(self):
        result = _normalize_assets(
            ["app.apk"],
            ["Meshtastic-2.7.14.exe"],
        )
        assert result == [
            ("Android APK: app.apk", "app.apk"),
            ("Windows: Meshtastic-2.7.14.exe", "Meshtastic-2.7.14.exe"),
        ]

    def test_empty_inputs(self):
        assert _normalize_assets([], []) == []


class TestSelectAssets:
    def test_no_entries(self, capsys):
        result = select_assets([], [])
        assert result is None
        assert "No client app assets found" in capsys.readouterr().out

    def test_selection_returns_patterns(self):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = [
                ("Android APK: app-2.5.9.apk", 0),
            ]
            result = select_assets(["app-2.5.9.apk"], [])
        assert result == {"selected_assets": ["app.apk"]}

    def test_no_selection(self, capsys):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = []
            result = select_assets(["app.apk"], [])
        assert result is None
        assert "No client app assets selected" in capsys.readouterr().out

    def test_desktop_pattern_lowered(self):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = [
                ("macOS: Meshtastic-2.7.14.dmg", 0),
            ]
            result = select_assets([], ["Meshtastic-2.7.14.dmg"])
        assert result is not None
        for p in result["selected_assets"]:
            assert p == p.lower()

    def test_index_out_of_range_skipped(self):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = [
                ("Android APK: app.apk", 99),
            ]
            result = select_assets(["app.apk"], [])
        assert result is None

    def test_negative_index_skipped(self):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = [
                ("Android APK: app.apk", -1),
            ]
            result = select_assets(["app.apk"], [])
        assert result is None

    def test_mixed_apk_and_desktop(self):
        with patch("fetchtastic.menu_app.pick") as mock_pick:
            mock_pick.return_value = [
                ("Android APK: app-2.5.9.apk", 0),
                ("Windows: Meshtastic-2.7.14.exe", 1),
            ]
            result = select_assets(
                ["app-2.5.9.apk"],
                ["Meshtastic-2.7.14.exe"],
            )
        assert result is not None
        assert len(result["selected_assets"]) == 2


class TestRunMenu:
    def test_successful_both(self):
        with (
            patch("fetchtastic.menu_apk.fetch_apk_assets", return_value=["app.apk"]),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                return_value=["Meshtastic-2.7.14.dmg"],
            ),
            patch("fetchtastic.menu_app.pick") as mock_pick,
        ):
            mock_pick.return_value = [("Android APK: app.apk", 0)]
            result = run_menu()
        assert result is not None

    def test_apk_fetch_error(self, capsys):
        with (
            patch(
                "fetchtastic.menu_apk.fetch_apk_assets",
                side_effect=OSError("fail"),
            ),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                return_value=[],
            ),
            patch("fetchtastic.menu_app.pick") as mock_pick,
        ):
            mock_pick.return_value = [("Android APK: x.apk", 0)]
            run_menu()
        assert "Warning: unable to fetch Android APK assets" in capsys.readouterr().out

    def test_desktop_fetch_error(self, capsys):
        with (
            patch("fetchtastic.menu_apk.fetch_apk_assets", return_value=[]),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                side_effect=ValueError("bad"),
            ),
            patch("fetchtastic.menu_app.pick") as mock_pick,
        ):
            mock_pick.return_value = [("macOS: Meshtastic-2.7.14.dmg", 0)]
            run_menu()
        assert (
            "Warning: unable to fetch Desktop installer assets"
            in capsys.readouterr().out
        )

    def test_apk_returns_none(self):
        with (
            patch("fetchtastic.menu_apk.fetch_apk_assets", return_value=None),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                return_value=["Meshtastic-2.7.14.dmg"],
            ),
            patch("fetchtastic.menu_app.pick") as mock_pick,
        ):
            mock_pick.return_value = [("macOS: Meshtastic-2.7.14.dmg", 0)]
            result = run_menu()
        assert result is not None

    def test_desktop_returns_none(self):
        with (
            patch("fetchtastic.menu_apk.fetch_apk_assets", return_value=["app.apk"]),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                return_value=None,
            ),
            patch("fetchtastic.menu_app.pick") as mock_pick,
        ):
            mock_pick.return_value = [("Android APK: app.apk", 0)]
            result = run_menu()
        assert result is not None

    def test_runtime_error_handled(self, capsys):
        with (
            patch(
                "fetchtastic.menu_apk.fetch_apk_assets",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "fetchtastic.menu_desktop.fetch_desktop_assets",
                side_effect=TypeError("type err"),
            ),
        ):
            result = run_menu()
        assert result is None
        out = capsys.readouterr().out
        assert "Warning: unable to fetch Android APK assets" in out
        assert "Warning: unable to fetch Desktop installer assets" in out
