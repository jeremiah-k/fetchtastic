"""
GitHub Release Source

This module provides a reusable class for fetching releases from GitHub APIs
with caching support, reducing code duplication across downloaders.
"""

import json
from typing import Any, Callable, Dict, List, Optional

import requests  # type: ignore[import-untyped]

from fetchtastic.constants import RELEASES_CACHE_EXPIRY_HOURS
from fetchtastic.log_utils import logger
from fetchtastic.utils import make_github_api_request

from .cache import CacheManager
from .interfaces import Asset, Release


class GithubReleaseSource:
    """
    A reusable component for fetching releases from GitHub with caching.

    This class handles the common pattern of:
    1. Building cache key with params
    2. Reading from cache (if valid)
    3. Fetching from GitHub API if not cached
    4. Writing to cache
    5. Parsing releases via a customizable callback

    Usage:
        source = GithubReleaseSource(
            releases_url="https://api.github.com/repos/owner/repo/releases",
            cache_manager=cache_manager,
            config=config
        )

        releases = source.get_releases(
            params={"per_page": 10},
            parse_release_func=my_parser
        )
    """

    def __init__(
        self,
        releases_url: str,
        cache_manager: CacheManager,
        config: Dict[str, Any],
    ):
        """
        Initialize the GitHub release source.

        Parameters:
            releases_url (str): The GitHub API URL for fetching releases.
            cache_manager (CacheManager): Cache manager for reading/writing cached responses.
            config (Dict[str, Any]): Configuration dictionary for tokens and settings.
        """
        self.releases_url = releases_url
        self.cache_manager = cache_manager
        self.config = config

    def get_releases(
        self,
        params: Dict[str, Any],
        parse_release_func: Callable[[Dict[str, Any]], Optional[Release]],
    ) -> List[Release]:
        """
        Fetch releases from GitHub, using cache when available.

        This method:
        1. Builds a cache key from the URL and params
        2. Attempts to read from cache
        3. Fetches from GitHub API if not cached
        4. Caches the raw response data
        5. Parses releases using the provided callback function

        Parameters:
            params (Dict[str, Any]): Query parameters for the API request (e.g., {"per_page": 10}).
            parse_release_func (Callable): A function that takes a raw release dict from the GitHub API
                and returns a Release object, or None to skip the release.

        Returns:
            List[Release]: List of parsed Release objects. Empty list on error or if no valid releases.
        """
        try:
            url_key = self.cache_manager.build_url_cache_key(self.releases_url, params)
            releases_data = self.cache_manager.read_releases_cache_entry(
                url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
            )

            if releases_data is None:
                releases_data = self._fetch_from_api(params)
                if isinstance(releases_data, list):
                    logger.debug(
                        "Cached %d releases for %s (fetched from API)",
                        len(releases_data),
                        self.releases_url,
                    )
                    self.cache_manager.write_releases_cache_entry(
                        url_key, releases_data
                    )
                else:
                    logger.debug(
                        "Skipping cache write for %s due to invalid API response",
                        self.releases_url,
                    )

            if releases_data is None or not isinstance(releases_data, list):
                logger.error("Invalid releases data received from GitHub API")
                return []

            releases: List[Release] = []
            for release_data in releases_data:
                if not isinstance(release_data, dict):
                    logger.warning(
                        "Skipping malformed release entry from %s: expected dict, got %s",
                        self.releases_url,
                        type(release_data).__name__,
                    )
                    continue

                assets_data = release_data.get("assets")
                # Filter out releases without a valid asset list
                if not isinstance(assets_data, list) or not assets_data:
                    continue

                try:
                    release = parse_release_func(release_data)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Skipping malformed release entry from %s: %s",
                        self.releases_url,
                        exc,
                    )
                    continue
                if release is not None:
                    releases.append(release)

            return releases

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
        ) as exc:
            logger.exception(
                "Error fetching releases from %s: %s", self.releases_url, exc
            )
            return []

    def fetch_raw_releases_data(
        self, params: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch raw releases data from GitHub, using cache when available.

        This method handles the common fetch-with-caching pattern without parsing:
        1. Builds a cache key from the URL and params
        2. Attempts to read from cache
        3. Fetches from GitHub API if not cached
        4. Caches the raw response data

        Use this method when you need the raw release data for custom processing
        (e.g., scanning loops that need to know the total count of releases returned).

        Parameters:
            params (Dict[str, Any]): Query parameters for the API request (e.g., {"per_page": 10}).

        Returns:
            Optional[List[Dict[str, Any]]]: List of raw release dicts from the API,
                or None on error or if invalid data received.
        """
        try:
            url_key = self.cache_manager.build_url_cache_key(self.releases_url, params)
            releases_data = self.cache_manager.read_releases_cache_entry(
                url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
            )

            if releases_data is not None:
                logger.debug(
                    "Using cached releases for %s (%d releases)",
                    self.releases_url,
                    len(releases_data),
                )

            if releases_data is None:
                releases_data = self._fetch_from_api(params)
                if isinstance(releases_data, list):
                    logger.debug(
                        "Cached %d releases for %s (fetched from API)",
                        len(releases_data),
                        self.releases_url,
                    )
                    self.cache_manager.write_releases_cache_entry(
                        url_key, releases_data
                    )
                else:
                    logger.debug(
                        "Skipping cache write for %s due to invalid API response",
                        self.releases_url,
                    )

            if releases_data is None or not isinstance(releases_data, list):
                logger.error("Invalid releases data received from GitHub API")
                return None

            return releases_data

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
        ) as exc:
            logger.exception(
                "Error fetching releases from %s: %s", self.releases_url, exc
            )
            return None

    def _fetch_from_api(self, params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch releases data directly from the GitHub API.

        Parameters:
            params (Dict[str, Any]): Query parameters for the API request.

        Returns:
            Optional[List[Dict[str, Any]]]: List of release dicts from the API response,
                or None if the request failed or returned invalid data.
        """
        response = make_github_api_request(
            self.releases_url,
            self.config.get("GITHUB_TOKEN"),
            allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
            params=params,
        )
        return response.json() if hasattr(response, "json") else None


def create_release_from_github_data(release_data: Dict[str, Any]) -> Optional[Release]:
    """
    Create a Release object from GitHub API release data.

    This is a default parser that creates a Release with standard fields
    and populates it with all assets from the release.

    Parameters:
        release_data (Dict[str, Any]): Raw release data from GitHub API.

    Returns:
        Optional[Release]: A Release object populated with assets, or None
            when required fields are missing/invalid.
    """
    tag_name = release_data.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name.strip():
        logger.warning("Skipping release with missing or invalid tag_name")
        return None

    release = Release(
        tag_name=tag_name,
        prerelease=release_data.get("prerelease", False),
        published_at=release_data.get("published_at"),
        name=release_data.get("name"),
        body=release_data.get("body"),
    )

    # Add assets to the release
    assets_data = release_data.get("assets")
    if not isinstance(assets_data, list):
        logger.warning("Skipping release %s with invalid assets field", tag_name)
        return None

    for asset_data in assets_data:
        if not isinstance(asset_data, dict):
            logger.warning("Skipping malformed asset for release %s", tag_name)
            continue
        asset_name = asset_data.get("name")
        if not isinstance(asset_name, str) or not asset_name.strip():
            logger.warning("Skipping asset with invalid name for release %s", tag_name)
            continue
        raw_size = asset_data.get("size")
        try:
            asset_size = int(raw_size)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping asset %s with invalid size for release %s",
                asset_name,
                tag_name,
            )
            continue
        asset = Asset(
            name=asset_name,
            download_url=asset_data.get("browser_download_url", ""),
            size=asset_size,
            browser_download_url=asset_data.get("browser_download_url"),
            content_type=asset_data.get("content_type"),
        )
        release.assets.append(asset)

    if not release.assets:
        logger.warning("Skipping release %s with no valid assets", tag_name)
        return None

    return release
