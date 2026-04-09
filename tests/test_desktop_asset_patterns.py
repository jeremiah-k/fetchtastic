"""
Golden Tests for Desktop Asset Filename Patterns

Comprehensive tests for real-world desktop asset filename patterns
to prevent future regressions.
"""

from unittest.mock import MagicMock

import pytest

from fetchtastic.constants import DESKTOP_EXTENSIONS
from fetchtastic.download.desktop import MeshtasticDesktopDownloader
from fetchtastic.menu_desktop import (
    _get_platform_label,
    extract_wildcard_pattern,
)
from fetchtastic.utils import matches_selected_patterns

pytestmark = [
    pytest.mark.unit,
    pytest.mark.core_downloads,
    pytest.mark.user_interface,
]


class TestExtractWildcardPatternRealWorldFilenames:
    """Test extract_wildcard_pattern() with real-world desktop asset filenames."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            # macOS DMG files
            ("Meshtastic-2.7.14.dmg", "meshtastic.dmg"),
            # Windows MSI files (with _x64 architecture)
            ("Meshtastic_x64_2.7.14.msi", "meshtastic_x64.msi"),
            # Linux AppImage files with architecture suffix
            (
                "Meshtastic-2.7.14-linux-x86_64.AppImage",
                "meshtastic-linux-x86_64.appimage",
            ),
            # Debian ARM64 package
            ("Meshtastic-2.7.14-arm64.deb", "meshtastic-arm64.deb"),
            # RPM x86_64 package
            ("Meshtastic-2.7.14.x86_64.rpm", "meshtastic.x86_64.rpm"),
            # Windows EXE installer
            ("Meshtastic-2.7.14.exe", "meshtastic.exe"),
        ],
    )
    def test_real_world_filename_patterns(self, filename, expected):
        """Verify pattern extraction for various real-world desktop filenames."""
        result = extract_wildcard_pattern(filename)
        assert result == expected


class TestExtractWildcardPatternPrereleaseStripping:
    """Test prerelease version stripping in extract_wildcard_pattern()."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            # RC (release candidate) versions
            ("Meshtastic-2.7.14-rc1.dmg", "meshtastic.dmg"),
            ("Meshtastic-2.7.14-rc2.msi", "meshtastic.msi"),
            # Dev versions
            ("Meshtastic-2.7.14.dev1.msi", "meshtastic.msi"),
            ("Meshtastic-2.7.14.dev12.deb", "meshtastic.deb"),
            # Beta versions (various formats)
            ("Meshtastic-2.7.14beta1.AppImage", "meshtastic.appimage"),
            ("Meshtastic-2.7.14-beta2.dmg", "meshtastic.dmg"),
            ("Meshtastic-2.7.14b3.exe", "meshtastic.exe"),
            # Alpha versions
            ("Meshtastic-2.7.14-alpha1.deb", "meshtastic.deb"),
            ("Meshtastic-2.7.14b4.rpm", "meshtastic.rpm"),  # Using 'b' prefix for beta
        ],
    )
    def test_prerelease_version_stripping(self, filename, expected):
        """Verify that prerelease version strings are correctly stripped."""
        result = extract_wildcard_pattern(filename)
        assert result == expected


class TestPatternMatchingAlignment:
    """Test pattern matching alignment: filename → extract pattern → matches_selected_patterns."""

    @pytest.mark.parametrize(
        "filename,selected_patterns,should_match",
        [
            # DMG matching
            ("Meshtastic-2.7.14.dmg", ["meshtastic.dmg"], True),
            ("Meshtastic-2.8.0.dmg", ["meshtastic.dmg"], True),
            ("Meshtastic-2.7.14-rc1.dmg", ["meshtastic.dmg"], True),
            # MSI matching
            ("Meshtastic_x64_2.7.14.msi", ["meshtastic_x64.msi"], True),
            ("Meshtastic_x64_2.8.0.msi", ["meshtastic_x64.msi"], True),
            # AppImage matching
            (
                "Meshtastic-2.7.14-linux-x86_64.AppImage",
                ["meshtastic-linux-x86_64.appimage"],
                True,
            ),
            # Cross-pattern non-matching
            ("Meshtastic-2.7.14.dmg", ["meshtastic.exe"], False),
            ("Meshtastic-2.7.14.msi", ["meshtastic.dmg"], False),
        ],
    )
    def test_pattern_matching_flow(self, filename, selected_patterns, should_match):
        """Test the complete flow: filename → extract pattern → matches_selected_patterns.

        Verifies that the extracted pattern from one version can match similar
        filenames across different versions using the actual matches_selected_patterns
        function used in production.
        """
        result = matches_selected_patterns(filename, selected_patterns)
        assert result == should_match

    def test_should_download_asset_with_configured_patterns(self, mocker):
        """Test MeshtasticDesktopDownloader.should_download_asset with configured patterns.

        This test simulates the real-world scenario where a user selects desktop
        platforms in the menu and the downloader uses those patterns.
        """
        # Create a mock config with selected patterns
        config = {
            "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg", "meshtastic_x64.msi"],
            "EXCLUDE_PATTERNS": [],
        }

        mock_cache = MagicMock()
        downloader = MeshtasticDesktopDownloader(config, mock_cache)

        # Test that DMG files match
        assert downloader.should_download_asset("Meshtastic-2.7.14.dmg") is True
        assert downloader.should_download_asset("Meshtastic-2.8.0.dmg") is True

        # Test that MSI files match
        assert downloader.should_download_asset("Meshtastic_x64_2.7.14.msi") is True

        # Test that unselected patterns don't match
        assert downloader.should_download_asset("Meshtastic-2.7.14.exe") is False
        assert (
            downloader.should_download_asset("Meshtastic-2.7.14-linux-x86_64.AppImage")
            is False
        )

    def test_should_download_asset_with_excludes(self, mocker):
        """Test that exclude patterns take precedence over include patterns."""
        config = {
            "SELECTED_DESKTOP_ASSETS": ["meshtastic.dmg", "meshtastic.exe"],
            "EXCLUDE_PATTERNS": ["*beta*", "*rc*"],
        }

        mock_cache = MagicMock()
        downloader = MeshtasticDesktopDownloader(config, mock_cache)

        # Regular stable versions should match
        assert downloader.should_download_asset("Meshtastic-2.7.14.dmg") is True

        # Beta and RC versions should be excluded
        assert downloader.should_download_asset("Meshtastic-2.7.14-rc1.dmg") is False
        assert downloader.should_download_asset("Meshtastic-2.7.14beta1.exe") is False

    def test_should_download_asset_no_patterns(self, mocker):
        """Test that when no patterns are configured, all desktop assets match."""
        config = {
            "SELECTED_DESKTOP_ASSETS": [],
        }

        mock_cache = MagicMock()
        downloader = MeshtasticDesktopDownloader(config, mock_cache)

        # When no patterns selected, all should match
        assert downloader.should_download_asset("Meshtastic-2.7.14.dmg") is True
        assert downloader.should_download_asset("Meshtastic-2.7.14.msi") is True
        assert downloader.should_download_asset("Meshtastic-2.7.14.exe") is True


class TestDesktopExtensionsDetection:
    """Test desktop extension detection and case insensitivity."""

    @pytest.mark.parametrize(
        "filename,expected_platform",
        [
            # macOS extensions
            ("Meshtastic-2.7.14.dmg", "macOS"),
            ("Meshtastic-2.7.14.DMG", "macOS"),
            ("Meshtastic-2.7.14.Dmg", "macOS"),
            # Windows MSI extensions
            ("Meshtastic_x64_2.7.14.msi", "Windows"),
            ("Meshtastic_x64_2.7.14.MSI", "Windows"),
            ("Meshtastic_x64_2.7.14.Msi", "Windows"),
            # Windows EXE extensions
            ("Meshtastic-2.7.14.exe", "Windows"),
            ("Meshtastic-2.7.14.EXE", "Windows"),
            ("Meshtastic-2.7.14.Exe", "Windows"),
            # Linux DEB extensions
            ("Meshtastic-2.7.14-arm64.deb", "Linux"),
            ("Meshtastic-2.7.14-arm64.DEB", "Linux"),
            ("Meshtastic-2.7.14-arm64.Deb", "Linux"),
            # Linux RPM extensions
            ("Meshtastic-2.7.14.x86_64.rpm", "Linux"),
            ("Meshtastic-2.7.14.x86_64.RPM", "Linux"),
            ("Meshtastic-2.7.14.x86_64.Rpm", "Linux"),
            # Linux AppImage extensions (lowercase)
            ("Meshtastic-2.7.14-linux-x86_64.appimage", "Linux"),
            ("Meshtastic-2.7.14-linux-x86_64.AppImage", "Linux"),
            ("Meshtastic-2.7.14-linux-x86_64.APPIMAGE", "Linux"),
        ],
    )
    def test_extension_case_insensitivity(self, filename, expected_platform):
        """Verify that all supported desktop extensions work with any case.

        Tests that .dmg, .msi, .exe, .deb, .rpm, .appimage, and .AppImage
        are all recognized regardless of case.
        """
        result = _get_platform_label(filename)
        assert result == expected_platform

    @pytest.mark.parametrize(
        "ext",
        DESKTOP_EXTENSIONS,
    )
    def test_all_supported_extensions(self, ext):
        """Verify each supported desktop extension is recognized.

        This test ensures that the constants.DESKTOP_EXTENSIONS list matches
        what the platform detection function actually uses.
        """
        filename = f"Meshtastic-2.7.14{ext}"
        result = _get_platform_label(filename)
        assert result is not None, f"Extension {ext} should be recognized"


class TestEdgeCases:
    """Test edge cases for desktop asset pattern handling."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            # Multiple version-like numbers
            ("Meshtastic-2.7.14-2.1.0.dmg", "meshtastic.dmg"),
            # Version in different positions
            ("Meshtastic-2.7.14_arm64.deb", "meshtastic_arm64.deb"),
            # Underscore separators
            ("Meshtastic_2.7.14_x64.msi", "meshtastic_x64.msi"),
            # Mixed separators
            ("Meshtastic-2.7.14_x64.msi", "meshtastic_x64.msi"),
        ],
    )
    def test_edge_case_filenames(self, filename, expected):
        """Test edge cases with unusual filename formats."""
        result = extract_wildcard_pattern(filename)
        assert result == expected
