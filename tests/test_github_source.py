"""Targeted tests for github_source.py branch coverage."""

from unittest.mock import Mock

import pytest

from fetchtastic.download.github_source import (
    GithubReleaseSource,
    create_asset_from_github_data,
    create_release_from_github_data,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def _build_source() -> tuple[GithubReleaseSource, Mock]:
    """Create a GithubReleaseSource with a mocked cache manager."""
    cache_manager = Mock()
    cache_manager.build_url_cache_key.return_value = "cache-key"
    source = GithubReleaseSource(
        releases_url="https://api.github.com/repos/owner/repo/releases",
        cache_manager=cache_manager,
        config={},
    )
    return source, cache_manager


class TestGithubReleaseSourceGetReleases:
    """Tests for GithubReleaseSource.get_releases."""

    def test_get_releases_fetches_and_caches_on_cache_miss(self, mocker):
        """Cache miss should fetch from API and write cache for list payloads."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = None
        mocker.patch.object(
            source,
            "_fetch_from_api",
            return_value=[
                {
                    "tag_name": "v1.0.0",
                    "assets": [
                        {
                            "name": "firmware.bin",
                            "size": 12,
                            "browser_download_url": "https://example.com/fw.bin",
                        }
                    ],
                }
            ],
        )

        releases = source.get_releases({}, create_release_from_github_data)

        assert len(releases) == 1
        assert releases[0].tag_name == "v1.0.0"
        cache_manager.write_releases_cache_entry.assert_called_once()

    def test_get_releases_invalid_api_shape_skips_cache_write_and_returns_empty(
        self, mocker
    ):
        """Non-list API payloads should skip cache write and return empty list."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = None
        mocker.patch.object(source, "_fetch_from_api", return_value={"bad": "shape"})

        releases = source.get_releases({}, create_release_from_github_data)

        assert releases == []
        cache_manager.write_releases_cache_entry.assert_not_called()

    def test_get_releases_parser_exception_is_skipped(self):
        """Parser exceptions should skip only malformed release entries."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = [
            {
                "tag_name": "v1.0.0",
                "assets": [{"name": "x.bin", "size": 1, "browser_download_url": "url"}],
            }
        ]

        def bad_parser(_release_data):
            raise ValueError("bad release")

        releases = source.get_releases({}, bad_parser)

        assert releases == []

    def test_get_releases_skips_non_dict_entries_and_empty_assets(self):
        """Non-dict entries and releases without valid assets should be skipped."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = [
            "bad-entry",
            {"tag_name": "v1.0.0", "assets": []},
            {"tag_name": "v1.1.0", "assets": "bad-assets"},
            {
                "tag_name": "v1.2.0",
                "assets": [
                    {"name": "ok.bin", "size": 1, "browser_download_url": "url"}
                ],
            },
        ]

        releases = source.get_releases({}, create_release_from_github_data)

        assert len(releases) == 1
        assert releases[0].tag_name == "v1.2.0"


class TestGithubReleaseSourceFetchRaw:
    """Tests for GithubReleaseSource.fetch_raw_releases_data."""

    def test_fetch_raw_releases_data_uses_cached_value(self, mocker):
        """When cache has data, raw fetch should return it without API call."""
        source, cache_manager = _build_source()
        cached = [{"tag_name": "v1.0.0", "assets": []}]
        cache_manager.read_releases_cache_entry.return_value = cached
        fetch_mock = mocker.patch.object(source, "_fetch_from_api")

        result = source.fetch_raw_releases_data({})

        assert result == cached
        fetch_mock.assert_not_called()

    def test_fetch_raw_releases_data_cache_miss_writes_cache_for_list(self, mocker):
        """Cache miss with list response should write cache and return data."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = None
        mocker.patch.object(
            source,
            "_fetch_from_api",
            return_value=[{"tag_name": "v2.0.0", "assets": []}],
        )

        result = source.fetch_raw_releases_data({})

        assert result == [{"tag_name": "v2.0.0", "assets": []}]
        cache_manager.write_releases_cache_entry.assert_called_once()

    def test_fetch_raw_releases_data_invalid_api_response_returns_none(self, mocker):
        """Invalid API payloads should return None and skip cache write."""
        source, cache_manager = _build_source()
        cache_manager.read_releases_cache_entry.return_value = None
        mocker.patch.object(source, "_fetch_from_api", return_value={"bad": True})

        result = source.fetch_raw_releases_data({})

        assert result is None
        cache_manager.write_releases_cache_entry.assert_not_called()


class TestGithubReleaseAndAssetParsing:
    """Tests for create_release_from_github_data and create_asset_from_github_data."""

    def test_create_release_from_github_data_invalid_assets_field_returns_none(self):
        """Release with non-list assets field should be rejected."""
        release = create_release_from_github_data(
            {
                "tag_name": "v1.0.0",
                "assets": "not-a-list",
            }
        )
        assert release is None

    def test_create_asset_from_github_data_non_dict_returns_none(self):
        """Non-dict asset payload should be rejected."""
        asset = create_asset_from_github_data("bad-asset", "v1.0.0")
        assert asset is None

    def test_create_asset_from_github_data_invalid_name_returns_none(self):
        """Asset with invalid name type should be rejected."""
        asset = create_asset_from_github_data(
            {
                "name": 123,
                "size": 10,
                "browser_download_url": "https://example.com/fw.bin",
            },
            "v1.0.0",
        )
        assert asset is None

    def test_create_asset_from_github_data_invalid_size_type_returns_none(self):
        """Asset with unsupported size type should be rejected."""
        asset = create_asset_from_github_data(
            {
                "name": "fw.bin",
                "size": {"bad": "size"},
                "browser_download_url": "https://example.com/fw.bin",
            },
            "v1.0.0",
        )
        assert asset is None

    def test_create_asset_from_github_data_valid_dict_executes_parsing_path(self):
        """Valid asset payload should parse into an Asset object."""
        asset = create_asset_from_github_data(
            {
                "name": "fw.bin",
                "size": "42",
                "browser_download_url": " https://example.com/fw.bin ",
                "content_type": "application/octet-stream",
            },
            "v1.0.0",
        )

        assert asset is not None
        assert asset.name == "fw.bin"
        assert asset.size == 42
        assert asset.download_url == "https://example.com/fw.bin"
