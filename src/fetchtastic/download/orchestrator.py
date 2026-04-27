"""
Download Pipeline Orchestrator

This module implements the orchestration layer that coordinates multiple
downloaders in a single fetchtastic download run.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests  # type: ignore[import-untyped]

from fetchtastic.client_app_config import normalize_client_app_config
from fetchtastic.constants import (
    APKS_DIR_NAME,
    DEFAULT_APP_VERSIONS_TO_KEEP,
    DEFAULT_FILTER_REVOKED_RELEASES,
    DEFAULT_FIRMWARE_VERSIONS_TO_KEEP,
    DEFAULT_KEEP_LAST_BETA,
    DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
    ERROR_TYPE_RETRY_FAILURE,
    ERROR_TYPE_UNKNOWN,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
    FILE_TYPE_DESKTOP,
    FILE_TYPE_DESKTOP_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_MANIFEST,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
    FILE_TYPE_REPOSITORY,
    FILE_TYPE_UNKNOWN,
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_MANIFEST_EXTENSION,
    FIRMWARE_PRERELEASES_DIR_NAME,
    MAX_RETRY_DELAY,
    RELEASE_SCAN_COUNT,
    REPO_DOWNLOADS_DIR,
)
from fetchtastic.log_utils import logger
from fetchtastic.setup_config import is_termux
from fetchtastic.utils import cleanup_legacy_hash_sidecars

from .base import BaseDownloader
from .cache import CacheManager
from .client_app import MeshtasticClientAppDownloader
from .files import _safe_rmtree
from .firmware import FirmwareReleaseDownloader
from .interfaces import DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .version import VersionManager, is_prerelease_directory


def is_connected_to_wifi() -> bool:
    """
    Determine whether the device is connected to a Wi-Fi network.

    On non-Termux platforms this function assumes connectivity and returns `true`. On Termux it examines the output of the Termux Wi-Fi API and returns `true` only when the API reports a supplicant state of "COMPLETED" and a non-empty IP address; any command, parsing, or execution error results in `false`.

    Returns:
        `true` if the device is (or is assumed to be) connected to Wi-Fi, `false` otherwise.
    """
    if not is_termux():
        return True

    try:
        process = subprocess.run(
            ["termux-wifi-connectioninfo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if process.returncode != 0:
            error_message = process.stderr.strip()
            logger.warning(
                f"termux-wifi-connectioninfo command failed with exit code {process.returncode}: {error_message}"
            )
            return False

        output = process.stdout.strip()
        if not output:
            return False

        data = json.loads(output)
        if not isinstance(data, dict):
            return False
        raw_supplicant_state = data.get("supplicant_state", "")
        raw_ip_address = data.get("ip", "")
        supplicant_state = (
            raw_supplicant_state if isinstance(raw_supplicant_state, str) else ""
        )
        ip_address = raw_ip_address if isinstance(raw_ip_address, str) else ""
        return supplicant_state == "COMPLETED" and ip_address != ""
    except json.JSONDecodeError as e:
        logger.warning(f"Error decoding JSON from termux-wifi-connectioninfo: {e}")
        return False
    except FileNotFoundError:
        logger.warning(
            "termux-wifi-connectioninfo command not found. Is Termux:API installed and configured?"
        )
        return False
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(
            f"Unexpected error checking Wi-Fi connection: {e}", exc_info=True
        )
        return False


class DownloadOrchestrator:
    """
    Orchestrates the download pipeline for multiple artifact types.

    This class coordinates:
    - Multiple downloaders (Android, Firmware, Desktop, etc.)
    - Release fetching and filtering
    - Download execution and retry logic
    - Result aggregation and reporting
    - Error handling and recovery
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Create a DownloadOrchestrator configured to run the download pipeline.

        Initializes version, prerelease history, and cache managers; instantiates Android and firmware downloaders with the provided cache manager; and prepares result lists and in-run release caches.

        Parameters:
            config (Dict[str, Any]): Configuration mapping used by the orchestrator and its downloaders (controls behavior such as keep counts, prerelease handling, retry settings, extraction/exclude patterns, etc.).
        """
        self.config = normalize_client_app_config(config)
        self.version_manager = VersionManager()
        self.prerelease_manager = PrereleaseHistoryManager()
        self.cache_manager = CacheManager()

        # Initialize downloaders
        self.client_app_downloader: MeshtasticClientAppDownloader = (
            MeshtasticClientAppDownloader(self.config, self.cache_manager)
        )
        # Compatibility aliases for older tests/extensions that still reach for
        # Android or Desktop downloader attributes directly.
        self.android_downloader = self.client_app_downloader
        self.desktop_downloader = self.client_app_downloader
        self.firmware_downloader: FirmwareReleaseDownloader = FirmwareReleaseDownloader(
            self.config, self.cache_manager
        )

        # Track results
        self.download_results: List[DownloadResult] = []
        self.failed_downloads: List[DownloadResult] = []

        # Cache releases to avoid redundant API calls within a single run
        # None => complete/unbounded fetch; int => fetched with a limit (partial cache)
        self._client_app_releases_fetch_limit: Optional[int] = None
        self.client_app_releases: Optional[List[Release]] = None
        self._android_releases_fetch_limit: Optional[int] = None
        self.android_releases: Optional[List[Release]] = None
        self._firmware_releases_fetch_limit: Optional[int] = None
        self.firmware_releases: Optional[List[Release]] = None
        self._desktop_releases_fetch_limit: Optional[int] = None
        self.desktop_releases: Optional[List[Release]] = None
        self.firmware_release_history: Optional[Dict[str, Any]] = None
        # Single-run only: cleared after _log_prerelease_summary()
        self.firmware_prerelease_summary: Optional[Dict[str, Any]] = None
        # Run-scoped selected set: reset at start of _process_firmware_downloads()
        self.firmware_releases_selected: Optional[List[Release]] = None
        self.wifi_skipped: bool = False
        self.available_new_firmware_versions: List[str] = []
        self.available_new_apk_versions: List[str] = []

    def run_download_pipeline(
        self,
    ) -> Tuple[List[DownloadResult], List[DownloadResult]]:
        """
        Orchestrates discovery, downloading, retrying, and summary reporting for all configured artifact types.

        Returns:
            Tuple[List[DownloadResult], List[DownloadResult]]: A tuple (successful_results, failed_results) where `successful_results` is the list of completed DownloadResult entries and `failed_results` is the list of DownloadResult entries that remain failed after retry attempts.
        """
        start_time = time.time()
        self.wifi_skipped = False
        self.available_new_firmware_versions = []
        self.available_new_apk_versions = []
        logger.info("Starting download pipeline...")
        logger.debug(
            "Execution context: cwd=%s, python=%s, fetchtastic=%s",
            os.getcwd(),
            sys.executable,
            shutil.which("fetchtastic"),
        )

        if is_termux() and self.config.get("WIFI_ONLY", False):
            if not is_connected_to_wifi():
                logger.warning("Not connected to Wi-Fi. Skipping all downloads.")
                self.wifi_skipped = True
                self._discover_available_versions_when_wifi_skipped()
                return [], []

        cleanup_legacy_hash_sidecars(self.config.get("DOWNLOAD_DIR", ""))

        # Process firmware downloads
        self._process_firmware_downloads()

        # Process client app downloads through the legacy Android entry point so
        # existing extension/test seams still observe the app lifecycle.
        self._process_android_downloads()
        if self.desktop_downloader is not self.android_downloader:
            self._process_desktop_downloads()

        # Legacy parity: Repository downloads are handled separately through the interactive
        # "repo browse" command and are not part of the automatic download pipeline.

        # Enhance results with metadata before retry
        self._enhance_download_results_with_metadata()

        # Retry failed downloads
        self._retry_failed_downloads()

        # Log summary
        self._log_download_summary(start_time)

        return self.download_results, self.failed_downloads

    def _discover_available_versions_when_wifi_skipped(self) -> None:
        self._discover_available_firmware_versions_when_wifi_skipped()
        self._discover_available_apk_versions_when_wifi_skipped()

    def _discover_available_firmware_versions_when_wifi_skipped(self) -> None:
        try:
            if not self.config.get("SAVE_FIRMWARE", False):
                return

            keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
            keep_limit = self._get_firmware_keep_limit()
            filter_revoked = self.config.get(
                "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
            )
            fetch_limit = (
                max(keep_limit, RELEASE_SCAN_COUNT) if keep_last_beta else keep_limit
            )
            if filter_revoked and fetch_limit > 0:
                fetch_limit += RELEASE_SCAN_COUNT
            fetch_limit = min(100, fetch_limit if fetch_limit >= 0 else 0)

            firmware_releases = self._ensure_firmware_releases(limit=fetch_limit)
            if not firmware_releases:
                return

            (
                releases_for_processing,
                firmware_releases,
                fetch_limit,
            ) = self.firmware_downloader.collect_non_revoked_releases(
                initial_releases=firmware_releases,
                target_count=keep_limit,
                current_fetch_limit=fetch_limit,
            )
            self.firmware_releases = firmware_releases

            stable_releases = [
                r for r in releases_for_processing[:keep_limit] if not r.prerelease
            ]
            tracked_tag = self.firmware_downloader.get_latest_release_tag()
            new_versions: List[str] = []
            for release in stable_releases:
                if (
                    tracked_tag is None
                    or self.version_manager.compare_versions(
                        release.tag_name, tracked_tag
                    )
                    > 0
                ):
                    new_versions.append(release.tag_name)
            self.available_new_firmware_versions = list(dict.fromkeys(new_versions))
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.debug(
                "Error discovering available firmware versions during Wi-Fi skip: %s",
                e,
                exc_info=True,
            )

    def _discover_available_apk_versions_when_wifi_skipped(self) -> None:
        try:
            if not self.config.get("SAVE_CLIENT_APPS", False):
                return

            keep_count = self.config.get(
                "APP_VERSIONS_TO_KEEP", DEFAULT_APP_VERSIONS_TO_KEEP
            )
            app_releases = (
                self.client_app_releases
                or self.android_releases
                or self._ensure_android_releases()
            )
            stable_releases = [r for r in app_releases if not r.prerelease]
            releases_in_window = stable_releases[:keep_count]
            tracking_downloader = (
                self.android_downloader
                if self.android_downloader is not self.client_app_downloader
                else self.client_app_downloader
            )
            tracked_tag = tracking_downloader.get_latest_release_tag()
            new_versions: List[str] = []
            for release in releases_in_window:
                if (
                    tracked_tag is None
                    or self.version_manager.compare_versions(
                        release.tag_name, tracked_tag
                    )
                    > 0
                ):
                    new_versions.append(release.tag_name)
            self.available_new_apk_versions = list(dict.fromkeys(new_versions))
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.debug(
                "Error discovering available client app versions during Wi-Fi skip: %s",
                e,
                exc_info=True,
            )

    def _process_client_app_downloads(self) -> None:
        """Coordinate discovery and retrieval of selected client app assets."""
        try:
            if not self.config.get("SAVE_CLIENT_APPS", False):
                logger.info("Client app downloads are disabled in configuration")
                return

            self.client_app_downloader.migrate_legacy_layout()
            logger.info("Scanning client app releases")
            app_releases = (
                self.client_app_releases
                or self.android_releases
                or self._ensure_client_app_releases()
            )
            if not app_releases:
                logger.info("No client app releases found")
                return

            self.client_app_downloader.update_release_history(app_releases)
            raw_keep_count = self.config.get(
                "APP_VERSIONS_TO_KEEP", DEFAULT_APP_VERSIONS_TO_KEEP
            )
            try:
                keep_count = max(0, int(raw_keep_count))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid APP_VERSIONS_TO_KEEP value %r, using default %s",
                    raw_keep_count,
                    DEFAULT_APP_VERSIONS_TO_KEEP,
                )
                keep_count = int(DEFAULT_APP_VERSIONS_TO_KEEP)
            stable_releases = [r for r in app_releases if not r.prerelease]
            releases_to_process = stable_releases[:keep_count]

            for release in releases_to_process:
                self.client_app_downloader.ensure_release_notes(release)
                suffix = self.client_app_downloader.format_release_log_suffix(release)
                logger.info(f"Checking {release.tag_name}{suffix}…")

            completion_states = self._check_releases_complete(
                releases_to_process, self.client_app_downloader.is_release_complete
            )
            releases_to_download = []
            for release, is_complete in zip(
                releases_to_process, completion_states, strict=True
            ):
                if is_complete:
                    logger.debug(
                        f"Release {release.tag_name} already exists and is complete"
                    )
                else:
                    releases_to_download.append(release)

            any_app_downloaded = False
            if releases_to_download:
                for release in releases_to_download:
                    logger.info(f"Downloading client app release {release.tag_name}")
                    download_release = getattr(
                        self,
                        "_client_app_download_release",
                        self._download_client_app_release,
                    )
                    if download_release(release):
                        any_app_downloaded = True

            logger.info("Checking for client app prereleases...")
            prereleases = self.client_app_downloader.handle_prereleases(app_releases)
            tracked_prerelease_tag = self._get_tracked_prerelease_tag(
                self.client_app_downloader
            )
            for prerelease in prereleases:
                is_newer = self.client_app_downloader.should_download_prerelease(
                    prerelease.tag_name
                )
                if not is_newer:
                    should_backfill_tracked = (
                        tracked_prerelease_tag is not None
                        and prerelease.tag_name == tracked_prerelease_tag
                        and not self.client_app_downloader.is_release_complete(
                            prerelease
                        )
                    )
                    if should_backfill_tracked:
                        logger.info(
                            "Backfilling tracked client app prerelease %s to include newly selected assets",
                            prerelease.tag_name,
                        )
                    else:
                        logger.debug(
                            "Skipping client app prerelease %s because it is not newer than tracked prerelease",
                            prerelease.tag_name,
                        )
                        continue

                selected_assets = [
                    asset
                    for asset in self.client_app_downloader.get_assets(prerelease)
                    if self.client_app_downloader.should_download_asset(asset.name)
                ]
                if not selected_assets:
                    logger.debug(
                        "Skipping client app prerelease %s because no selected assets matched",
                        prerelease.tag_name,
                    )
                    continue

                self.client_app_downloader.ensure_release_notes(prerelease)
                prerelease_results: list[DownloadResult] = []
                for asset in selected_assets:
                    download_asset = getattr(
                        self,
                        "_client_app_download_asset",
                        self.client_app_downloader.download_app,
                    )
                    result = download_asset(prerelease, asset)
                    if result.success and not result.was_skipped:
                        any_app_downloaded = True
                    prerelease_results.append(result)
                    self._handle_download_result(
                        result,
                        getattr(
                            self,
                            "_client_app_prerelease_type",
                            FILE_TYPE_CLIENT_APP_PRERELEASE,
                        ),
                    )
                if prerelease_results and all(
                    result.success for result in prerelease_results
                ):
                    if not self.client_app_downloader.update_prerelease_tracking(
                        prerelease.tag_name
                    ):
                        logger.warning(
                            "Failed to update client app prerelease tracking for %s",
                            prerelease.tag_name,
                        )
                    tracked_prerelease_tag = prerelease.tag_name

            if (
                self.config.get(
                    "CHECK_APP_PRERELEASES",
                    self.config.get("CHECK_PRERELEASES", False),
                )
                and app_releases
                and not prereleases
            ):
                logger.info("No client app prereleases available")

            if not any_app_downloaded and not releases_to_download:
                logger.info("All client app assets are up to date.")

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error processing client app downloads: {e}", exc_info=True)

    def _process_android_downloads(self) -> None:
        """Compatibility wrapper for the unified client app downloader."""
        if not self.config.get("SAVE_APKS", self.config.get("SAVE_CLIENT_APPS", False)):
            logger.info("Android app downloads are disabled in configuration")
            return
        self._with_client_app_downloader(self.android_downloader)
        self._client_app_download_release = self._download_android_release
        self._client_app_download_asset = self.android_downloader.download_apk
        self._client_app_prerelease_type = "android_prerelease"
        self._process_client_app_downloads()

    def _process_desktop_downloads(self) -> None:
        """Compatibility wrapper for the unified client app downloader."""
        if not self.config.get("SAVE_DESKTOP_APP", False):
            logger.info("Desktop app downloads are disabled in configuration")
            return
        self._with_client_app_downloader(self.desktop_downloader)
        self._client_app_download_release = self._download_desktop_release
        self._client_app_download_asset = self.desktop_downloader.download_desktop
        self._client_app_prerelease_type = "desktop_prerelease"
        self._process_client_app_downloads()

    def _with_client_app_downloader(self, downloader: Any) -> None:
        self.client_app_downloader = downloader

    def _get_tracked_prerelease_tag(self, downloader: Any) -> Optional[str]:
        """
        Best-effort retrieval of a downloader's currently tracked prerelease tag.

        Returns:
            Optional[str]: Tracked prerelease tag string when available, else None.
        """
        getter = getattr(downloader, "get_current_tracked_prerelease_tag", None)
        if not callable(getter):
            return None
        try:
            tracked = getter()
        except (OSError, ValueError, TypeError):
            return None
        return tracked if isinstance(tracked, str) and tracked else None

    def _ensure_client_app_releases(self, limit: Optional[int] = None) -> List[Release]:
        """Return cached client app releases, fetching them once when needed."""
        releases = self._ensure_releases(
            downloader=self.client_app_downloader,
            releases_attr="client_app_releases",
            fetch_limit_attr="_client_app_releases_fetch_limit",
            limit=limit,
        )
        self.android_releases = releases
        self.desktop_releases = releases
        self._android_releases_fetch_limit = self._client_app_releases_fetch_limit
        self._desktop_releases_fetch_limit = self._client_app_releases_fetch_limit
        return releases

    def _ensure_android_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Return cached Android releases, fetching them once from the downloader if not already cached.

        Parameters:
            limit (Optional[int]): Maximum number of releases to fetch on the initial request; if releases are already cached with a smaller limit and the requested limit is larger or None (unbounded), refetches to ensure a complete result set.

        Returns:
            List[Release]: The cached list of Android releases.
        """
        return self._ensure_releases(
            downloader=self.android_downloader,
            releases_attr="android_releases",
            fetch_limit_attr="_android_releases_fetch_limit",
            limit=limit,
        )

    def _ensure_releases(
        self,
        downloader: Union[
            MeshtasticClientAppDownloader,
            FirmwareReleaseDownloader,
        ],
        releases_attr: str,
        fetch_limit_attr: str,
        limit: Optional[int] = None,
    ) -> List[Release]:
        """
        Generic helper to fetch and cache releases with partial cache detection.

        Parameters:
            downloader: The downloader instance to use for fetching releases.
            releases_attr (str): Attribute name on self that holds the cached releases list.
            fetch_limit_attr (str): Attribute name on self that holds the fetch limit used for caching.
            limit (Optional[int]): Maximum number of releases to fetch; if releases are already cached with a smaller limit and the requested limit is larger or None (unbounded), refetches to ensure a complete result set.

        Returns:
            List[Release]: The cached or newly fetched list of releases.
        """
        if limit == 0:
            return []

        current_releases = getattr(self, releases_attr)
        current_fetch_limit = getattr(self, fetch_limit_attr)

        should_fetch = current_releases is None or (
            current_fetch_limit is not None
            and (limit is None or limit > current_fetch_limit)
        )

        if should_fetch:
            new_releases = downloader.get_releases(limit=limit) or []
            setattr(self, releases_attr, new_releases)
            setattr(self, fetch_limit_attr, limit)
            return new_releases

        cached = current_releases or []
        if limit is None:
            return cached
        return cached[: max(0, limit)]

    def _ensure_firmware_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Ensure firmware releases are fetched (if needed) and return the cached list.

        Parameters:
            limit (Optional[int]): Maximum number of releases to fetch when loading from the downloader; if None or larger than a previously fetched limit, the method may refetch to satisfy the requested amount.

        Returns:
            List[Release]: The cached list of firmware releases (may be empty).
        """
        return self._ensure_releases(
            downloader=self.firmware_downloader,
            releases_attr="firmware_releases",
            fetch_limit_attr="_firmware_releases_fetch_limit",
            limit=limit,
        )

    def _ensure_desktop_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Ensure Desktop releases are fetched (if needed) and return the cached list.

        Parameters:
            limit (Optional[int]): Maximum number of releases to fetch when loading from the downloader; if None or larger than a previously fetched limit, the method may refetch to satisfy the requested amount.

        Returns:
            List[Release]: The cached list of Desktop releases (may be empty).
        """
        return self._ensure_releases(
            downloader=self.desktop_downloader,
            releases_attr="desktop_releases",
            fetch_limit_attr="_desktop_releases_fetch_limit",
            limit=limit,
        )

    def _get_release_check_workers(self) -> int:
        """
        Return the number of worker threads to use when checking release completeness.

        Reads `MAX_PARALLEL_RELEASE_CHECKS` from configuration and falls back to 4.
        Values below 1 or invalid values are clamped/fallback to safe defaults.

        Returns:
            int: Positive worker count for parallel release completeness checks.
        """
        raw_workers = self.config.get("MAX_PARALLEL_RELEASE_CHECKS", 4)
        try:
            return max(1, int(raw_workers))
        except (TypeError, ValueError):
            logger.debug(
                "Invalid MAX_PARALLEL_RELEASE_CHECKS value %r; using default 4",
                raw_workers,
            )
            return 4

    def _check_releases_complete(
        self, releases: List[Release], checker: Callable[[Release], bool]
    ) -> List[bool]:
        """
        Check completeness of each release and return flags in the same order as the input.

        Uses bounded thread parallelism when more than one release to reduce wall-clock time for I/O-heavy checks.

        Parameters:
            releases (List[Release]): Releases to evaluate.
            checker (Callable[[Release], bool]): Callable that returns `True` if a release is complete, `False` otherwise.

        Returns:
            List[bool]: A list where each element is `True` if the corresponding release is complete, `False` otherwise, aligned with `releases`.
        """
        if not releases:
            return []

        def _safe_check(r: Release) -> bool:
            try:
                return checker(r)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Release check failed for %s; treating as incomplete",
                    r.tag_name,
                    exc_info=True,
                )
                return False

        worker_count = min(len(releases), self._get_release_check_workers())
        if worker_count <= 1:
            return [_safe_check(release) for release in releases]

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_safe_check, r) for r in releases]
            return [f.result() for f in futures]

    def _process_firmware_downloads(self) -> None:
        """
        Ensure configured firmware releases and repository prereleases are present locally and remove unmanaged prerelease directories.

        Scans firmware releases according to retention and filtering settings, downloads missing release assets and repository prerelease firmware for the selected latest release, records each outcome in the orchestrator's result lists, and prunes prerelease subdirectories that do not match the managed naming and versioning conventions.
        """
        try:
            # Reset the selected releases at the start of each run
            self.firmware_releases_selected = None

            if not self.config.get("SAVE_FIRMWARE", False):
                logger.info("Firmware downloads are disabled in configuration")
                return

            logger.info("Scanning Firmware releases")
            keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
            keep_limit = self._get_firmware_keep_limit()
            filter_revoked = self.config.get(
                "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
            )
            fetch_limit = (
                max(keep_limit, RELEASE_SCAN_COUNT) if keep_last_beta else keep_limit
            )
            if filter_revoked and fetch_limit > 0:
                fetch_limit += RELEASE_SCAN_COUNT
            fetch_limit = min(100, fetch_limit if fetch_limit >= 0 else 0)
            firmware_releases = self._ensure_firmware_releases(limit=fetch_limit)
            if not firmware_releases:
                logger.info("No firmware releases found")
                return

            self.firmware_release_history = (
                self.firmware_downloader.update_release_history(
                    firmware_releases, log_summary=False
                )
            )
            latest_release = self._select_latest_release_by_version(firmware_releases)
            (
                releases_for_processing,
                firmware_releases,
                fetch_limit,
            ) = self.firmware_downloader.collect_non_revoked_releases(
                initial_releases=firmware_releases,
                target_count=keep_limit,
                current_fetch_limit=fetch_limit,
            )
            self.firmware_releases = firmware_releases

            releases_to_process = releases_for_processing[:keep_limit]
            if keep_last_beta:
                most_recent_beta = self.firmware_downloader.release_history_manager.find_most_recent_beta(
                    releases_for_processing
                )
                if most_recent_beta and most_recent_beta not in releases_to_process:
                    releases_to_process.append(most_recent_beta)

            for release in releases_to_process:
                suffix = self.firmware_downloader.format_release_log_suffix(release)
                logger.info(f"Checking {release.tag_name}{suffix}…")

            completion_states = self._check_releases_complete(
                releases_to_process, self.firmware_downloader.is_release_complete
            )
            releases_to_download = []
            for release, is_complete in zip(
                releases_to_process, completion_states, strict=True
            ):
                if is_complete:
                    self.firmware_downloader.ensure_release_notes(release)
                    logger.debug(
                        f"Release {release.tag_name} already exists and is complete"
                    )
                else:
                    releases_to_download.append(release)

            any_firmware_downloaded = False
            if releases_to_download:
                for release in releases_to_download:
                    logger.info(f"Downloading firmware release {release.tag_name}")
                    self.firmware_downloader.ensure_release_notes(release)
                    if self._download_firmware_release(release):
                        any_firmware_downloaded = True

            if latest_release:
                (
                    successes,
                    failures,
                    _active_dir,
                    prerelease_summary,
                ) = self.firmware_downloader.download_repo_prerelease_firmware(
                    latest_release.tag_name, force_refresh=False
                )
                if prerelease_summary:
                    self.firmware_prerelease_summary = prerelease_summary
                for result in successes:
                    if not result.was_skipped:
                        any_firmware_downloaded = True
                    self._handle_download_result(
                        result, FILE_TYPE_FIRMWARE_PRERELEASE_REPO
                    )
                for result in failures:
                    self._handle_download_result(
                        result, FILE_TYPE_FIRMWARE_PRERELEASE_REPO
                    )

            if not any_firmware_downloaded and not releases_to_download:
                logger.info("All Firmware assets are up to date.")

            # Remove prerelease directories whose version is <= the latest
            # release to prevent accumulation of old prereleases.
            if latest_release:
                self.firmware_downloader.cleanup_superseded_prereleases(
                    latest_release.tag_name
                )

            # Clean up prerelease directory
            prerelease_dir = (
                Path(self.firmware_downloader.download_dir)
                / FIRMWARE_DIR_NAME
                / FIRMWARE_PRERELEASES_DIR_NAME
            )
            if prerelease_dir.exists():
                for item in prerelease_dir.iterdir():
                    # Skip symlinks to prevent path traversal attacks
                    if item.is_symlink():
                        logger.warning(
                            f"Skipping symlink in prerelease folder: {item.name}"
                        )
                        continue
                    if not item.is_dir():
                        continue

                    # Only remove directories that are clearly Fetchtastic-managed prerelease
                    # directories (firmware prefix + parseable version). This prevents accidental
                    # deletion of user-created directories under the prerelease folder.
                    if not item.name.startswith(FIRMWARE_DIR_PREFIX):
                        continue
                    suffix = item.name[len(FIRMWARE_DIR_PREFIX) :]
                    if self.version_manager.get_release_tuple(suffix) is None:
                        logger.warning(
                            "Skipping unexpected directory in prerelease folder: %s",
                            item.name,
                        )
                        continue

                    # A prerelease directory should contain a hash; if it doesn't, it's likely a
                    # stable release directory misplaced into the prerelease folder.
                    if not is_prerelease_directory(item.name):
                        logger.warning(
                            "Removing unexpected directory from prerelease folder: %s",
                            item.name,
                        )
                        if not _safe_rmtree(str(item), str(prerelease_dir), item.name):
                            logger.warning(
                                "Failed to safely remove directory: %s", item.name
                            )

            # Store the actual selected releases for accurate summary reporting
            self.firmware_releases_selected = list(releases_to_process)

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error processing firmware downloads: {e}", exc_info=True)

    def _select_latest_release_by_version(
        self, releases: List[Release]
    ) -> Optional[Release]:
        """
        Choose the release with the highest semantic version parsed from its tag name, preferring non-revoked releases when possible.

        If one or more tag names parse as semantic versions, returns the release whose parsed version is greatest. If no tag parses successfully, returns the first release in the provided list. Returns None when the input list is empty.

        Returns:
            The selected Release, the first release if no tags parse, or None if no releases were provided.
        """
        best_release: Optional[Release] = None
        best_tuple: Optional[Tuple[int, ...]] = None
        best_revoked_release: Optional[Release] = None
        best_revoked_tuple: Optional[Tuple[int, ...]] = None

        for release in releases:
            release_tuple = self.version_manager.get_release_tuple(release.tag_name)
            if release_tuple is None:
                continue
            is_revoked = self.firmware_downloader.is_release_revoked(release)
            if is_revoked:
                if best_revoked_tuple is None or release_tuple > best_revoked_tuple:
                    best_revoked_tuple = release_tuple
                    best_revoked_release = release
            else:
                if best_tuple is None or release_tuple > best_tuple:
                    best_tuple = release_tuple
                    best_release = release

        return (
            best_release or best_revoked_release or (releases[0] if releases else None)
        )

    def _download_client_app_release(self, release: Release) -> bool:
        """Download all selected client app assets for a release."""
        any_downloaded = False
        try:
            for asset in self.client_app_downloader.get_assets(release):
                if not self.client_app_downloader.should_download_asset(asset.name):
                    continue
                result = self.client_app_downloader.download_app(release, asset)
                if result.success and not result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(result, FILE_TYPE_CLIENT_APP)
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(
                f"Error downloading client app release {release.tag_name}: {e}"
            )
            return False
        else:
            return any_downloaded

    def _download_android_release(self, release: Release) -> bool:
        """Download selected app assets through the Android-compatible seam."""
        any_downloaded = False
        try:
            for asset in self.android_downloader.get_assets(release):
                if not self.android_downloader.should_download_asset(asset.name):
                    continue
                result = self.android_downloader.download_apk(release, asset)
                if result.success and not result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(result, "android")
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error downloading Android release {release.tag_name}: {e}")
            return False
        else:
            return any_downloaded

    def _download_firmware_release(self, release: Release) -> bool:
        """
        Download firmware assets from a release and optionally extract them based on configuration.

        If matching assets are found they are downloaded. Extraction is performed only when the
        `AUTO_EXTRACT` configuration flag is true and the release was not skipped due to being revoked.

        Parameters:
            release (Release): Firmware release whose matching assets will be downloaded and (optionally) extracted.

        Returns:
            bool: `True` if at least one asset was downloaded, `False` otherwise.
        """
        any_downloaded = False
        try:
            # Get extraction patterns from configuration
            extract_patterns = self._get_extraction_patterns()
            exclude_patterns = self._get_exclude_patterns()

            # Download manifest JSON files separately so they are categorized correctly
            # and so release-level firmware-<version>.json is always retained.
            raw_manifest_results = self.firmware_downloader.download_manifests(release)
            manifest_results = (
                raw_manifest_results if isinstance(raw_manifest_results, list) else []
            )
            for result in manifest_results:
                if result.success and not result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(result, FILE_TYPE_FIRMWARE_MANIFEST)

            # Filter binary assets based on selection/exclude rules.
            assets_to_download = [
                asset
                for asset in release.assets
                if (
                    asset.name
                    and not self._is_firmware_manifest_asset(asset.name)
                    and self.firmware_downloader.should_download_release(
                        release.tag_name, asset.name
                    )
                )
            ]

            if not assets_to_download and not manifest_results:
                logger.info(
                    "Release %s found, but no assets matched current selection/exclude filters",
                    release.tag_name,
                )
                return False

            # Download each asset in the release
            for asset in assets_to_download:
                # Download the firmware ZIP
                download_result = self.firmware_downloader.download_firmware(
                    release, asset
                )
                if download_result.success and not download_result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(download_result, FILE_TYPE_FIRMWARE)

                # If download succeeded, extract files if AUTO_EXTRACT is enabled.
                # Skip extraction when a release is intentionally skipped (e.g., revoked).
                if (
                    download_result.success
                    and self.config.get("AUTO_EXTRACT", False)
                    and asset.name.lower().endswith(".zip")
                    and not (
                        download_result.was_skipped
                        and download_result.error_type == "revoked_release"
                    )
                ):
                    extract_result = self.firmware_downloader.extract_firmware(
                        release, asset, extract_patterns, exclude_patterns
                    )
                    self._handle_download_result(extract_result, "firmware_extraction")
            return any_downloaded
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error downloading firmware release {release.tag_name}: {e}")
            return False

    def _is_firmware_manifest_asset(self, asset_name: str) -> bool:
        """
        Determine whether a firmware release asset name is a manifest JSON file.

        This includes both per-device manifests (`*.mt.json`) and release-level
        manifests (`firmware-<version>.json`).
        """
        asset_name_lower = asset_name.lower()
        return asset_name_lower.endswith(FIRMWARE_MANIFEST_EXTENSION) or (
            asset_name_lower.startswith(FIRMWARE_DIR_PREFIX)
            and asset_name_lower.endswith(".json")
            and not asset_name_lower.endswith(FIRMWARE_MANIFEST_EXTENSION)
        )

    def _download_desktop_release(self, release: Release) -> bool:
        """Download selected app assets through the Desktop-compatible seam."""
        any_downloaded = False
        try:
            for asset in self.desktop_downloader.get_assets(release):
                if not self.desktop_downloader.should_download_asset(asset.name):
                    continue
                result = self.desktop_downloader.download_desktop(release, asset)
                if result.success and not result.was_skipped:
                    any_downloaded = True
                self._handle_download_result(result, "desktop")
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error downloading Desktop release {release.tag_name}: {e}")
            return False
        else:
            return any_downloaded

    def _get_extraction_patterns(self) -> List[str]:
        """
        Retrieve extraction filename patterns from the orchestrator configuration.

        Returns:
            List[str]: Filename patterns to extract. If the configured value is a single string, it is returned as a one-element list.
        """
        patterns = self.config.get("EXTRACT_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _get_exclude_patterns(self) -> List[str]:
        """
        Normalize and return filename patterns to exclude from processing.

        If the configuration key "EXCLUDE_PATTERNS" is missing, returns an empty list. If the configured value is a string, it is wrapped in a single-element list; if it is already a list, it is returned unchanged.

        Returns:
            List[str]: Filename patterns to exclude.
        """
        patterns = self.config.get("EXCLUDE_PATTERNS", [])
        return patterns if isinstance(patterns, list) else [patterns]

    def _handle_download_result(
        self, result: DownloadResult, operation_type: str
    ) -> None:
        """
        Record a download result by adding it to the orchestrator's success or failure lists and logging the outcome.

        Parameters:
            result (DownloadResult): The result of a download attempt. If `result.success` is True the result is appended to `download_results`; if `result.success` is False it is appended to `failed_downloads`. A `was_skipped` attribute on `result` (when present and True) is treated as a skipped success.
            operation_type (str): Human-readable operation/category used in logs (for example 'android', 'firmware', or include 'prerelease' to indicate prerelease handling).
        """
        if result.success:
            self.download_results.append(result)
            if getattr(result, "was_skipped", False) is True:
                if "prerelease" not in operation_type:
                    logger.debug("Skipped %s: %s", operation_type, result.release_tag)
            else:
                logger.debug("Completed %s: %s", operation_type, result.release_tag)
        else:
            self.failed_downloads.append(result)
            error_msg = result.error_message or "Unknown error"
            logger.error(
                f"Failed {operation_type} for {result.release_tag}: {error_msg}"
            )
            if result.download_url:
                logger.error(f"URL: {result.download_url}")

    def _retry_failed_downloads(self) -> None:
        """
        Retry failed downloads using per-result metadata and exponential backoff.

        Reads MAX_RETRIES, RETRY_DELAY_SECONDS, and RETRY_BACKOFF_FACTOR from configuration, separates failures into retryable and non-retryable groups, and attempts retries for eligible failures. Each retry increments the result's retry count, stamps a retry timestamp, updates the error message with retry context, waits using exponential backoff, and records the retry outcome (successful retries are moved to completed results; persistent failures are marked non-retryable and retained). After processing, replaces the stored failed downloads with the remaining non-retryable failures and generates a summary report of retry activity.
        """
        if not self.failed_downloads:
            return

        # Get retry configuration
        max_retries = self.config.get("MAX_RETRIES", 3)
        retry_delay = self.config.get("RETRY_DELAY_SECONDS", 0)
        retry_backoff_factor = self.config.get("RETRY_BACKOFF_FACTOR", 2.0)

        logger.info(
            f"Retrying {len(self.failed_downloads)} failed downloads with enhanced retry logic..."
        )

        retryable_failures: List[DownloadResult] = []
        non_retryable_failures: List[DownloadResult] = []

        # Separate retryable and non-retryable failures
        original_failures = list(self.failed_downloads)
        for failed_result in original_failures:
            if failed_result.is_retryable and failed_result.retry_count < max_retries:
                retryable_failures.append(failed_result)
            else:
                non_retryable_failures.append(failed_result)

        logger.info(
            f"Found {len(retryable_failures)} retryable failures and {len(non_retryable_failures)} non-retryable failures"
        )

        remaining_failures: List[DownloadResult] = list(non_retryable_failures)

        # Process retryable failures with exponential backoff
        for i, failed_result in enumerate(retryable_failures):
            try:
                # Calculate delay with exponential backoff
                current_delay = retry_delay * (
                    retry_backoff_factor**failed_result.retry_count
                )
                current_delay = min(current_delay, MAX_RETRY_DELAY)
                logger.info(
                    f"Waiting {current_delay:.1f} seconds before retry attempt {failed_result.retry_count + 1}/{max_retries}..."
                )

                if current_delay > 0:
                    time.sleep(current_delay)

                # Update retry metadata
                failed_result.retry_count += 1
                failed_result.retry_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                original_error = failed_result.error_message or "Unknown error"
                failed_result.error_message = f"Retry attempt {failed_result.retry_count}/{max_retries} - Original: {original_error}"

                # Log detailed retry information
                logger.info(f"Retrying download {i + 1}/{len(retryable_failures)}:")
                logger.info(f"  - Release: {failed_result.release_tag}")
                logger.info(f"  - URL: {failed_result.download_url}")
                logger.info(f"  - File: {failed_result.file_path}")
                logger.info(f"  - Error: {failed_result.error_type}")
                logger.info(f"  - Attempt: {failed_result.retry_count}/{max_retries}")

                retry_result = self._retry_single_failure(failed_result)
                operation = f"{retry_result.file_type or 'unknown'}_retry"
                if retry_result.success:
                    failed_result.success = True
                    failed_result.file_path = retry_result.file_path
                    failed_result.extracted_files = retry_result.extracted_files
                    failed_result.error_message = None
                    failed_result.error_type = None
                    failed_result.error_details = None
                    failed_result.http_status_code = None
                    failed_result.is_retryable = False
                    failed_result.was_skipped = retry_result.was_skipped

                    self.download_results.append(failed_result)
                    logger.debug(
                        "Completed %s: %s", operation, failed_result.release_tag
                    )
                else:
                    failed_result.success = False
                    failed_result.file_path = (
                        retry_result.file_path or failed_result.file_path
                    )
                    failed_result.extracted_files = retry_result.extracted_files
                    failed_result.error_message = (
                        retry_result.error_message or failed_result.error_message
                    )
                    failed_result.error_type = retry_result.error_type
                    failed_result.error_details = retry_result.error_details
                    failed_result.http_status_code = retry_result.http_status_code
                    failed_result.is_retryable = retry_result.is_retryable
                    failed_result.was_skipped = False

                    remaining_failures.append(failed_result)
                    error_msg = failed_result.error_message or "Unknown error"
                    logger.error(
                        "Failed %s for %s: %s",
                        operation,
                        failed_result.release_tag,
                        error_msg,
                    )
                    if failed_result.download_url:
                        logger.error("URL: %s", failed_result.download_url)

            except (requests.RequestException, OSError, ValueError, TypeError) as e:
                logger.error(f"Retry failed for {failed_result.release_tag}: {e}")
                # Mark as non-retryable after max attempts
                failed_result.is_retryable = False
                failed_result.error_message = f"Max retries exceeded: {e!s}"
                remaining_failures.append(failed_result)

        # Update the failed downloads list with remaining failures (including failed retries)
        self.failed_downloads = remaining_failures

        # Generate detailed retry report
        self._generate_retry_report(retryable_failures, non_retryable_failures)

    def _create_failure_result(
        self,
        failed_result: DownloadResult,
        file_path: Path,
        download_url: str,
        file_type: str,
        error_message: str,
        exception_message: Optional[str] = None,
        is_retryable_override: Optional[bool] = None,
    ) -> DownloadResult:
        """
        Create a standardized failure DownloadResult populated for retry handling.

        Parameters:
            failed_result (DownloadResult): Original failed result whose metadata (release_tag, file_size, retry_count, retry_timestamp) will be carried forward.
            file_path (Path): Target filesystem path for the attempted download.
            download_url (str): URL that was being downloaded.
            file_type (str): Logical file type to record (e.g., "android", "firmware").
            error_message (str): Human-readable description of the failure.
            exception_message (Optional[str]): Optional low-level exception text to prefer over error_message when present.
            is_retryable_override (Optional[bool]): If provided, explicitly sets the result's retryability; otherwise retryability is derived from retry_count and MAX_RETRIES config.

        Returns:
            DownloadResult: A failure result with success=False, populated error fields, preserved retry metadata, and an `is_retryable` flag.
        """
        if is_retryable_override is not None:
            is_retryable = is_retryable_override
        else:
            is_retryable = failed_result.retry_count < self.config.get("MAX_RETRIES", 3)

        final_message = exception_message or error_message

        return DownloadResult(
            success=False,
            release_tag=failed_result.release_tag,
            file_path=file_path,
            download_url=download_url,
            file_size=failed_result.file_size,
            file_type=file_type,
            retry_count=failed_result.retry_count,
            retry_timestamp=failed_result.retry_timestamp,
            error_message=final_message,
            error_type=ERROR_TYPE_RETRY_FAILURE,
            is_retryable=is_retryable,
        )

    def _retry_single_failure(self, failed_result: DownloadResult) -> DownloadResult:
        """
        Attempt a single retry of a previously failed download using metadata from the provided DownloadResult.

        Parameters:
            failed_result (DownloadResult): The original failed download result containing the URL, target path, retry counters, and file type used to perform the retry.

        Returns:
            DownloadResult: A result representing the outcome of the retry. On success the returned result has `success=True` and contains the validated file path; on failure the returned result has `success=False` and includes an error message and updated `is_retryable`/retry metadata.
        """
        url = failed_result.download_url
        target_path = str(failed_result.file_path) if failed_result.file_path else None
        file_type = failed_result.file_type or FILE_TYPE_UNKNOWN

        if not url or not target_path:
            return DownloadResult(
                success=False,
                release_tag=failed_result.release_tag,
                file_path=failed_result.file_path,
                download_url=url,
                file_size=failed_result.file_size,
                file_type=file_type,
                retry_count=failed_result.retry_count,
                retry_timestamp=failed_result.retry_timestamp,
                error_message="Retry skipped: missing URL or target path",
                error_type=ERROR_TYPE_RETRY_FAILURE,
                is_retryable=False,
            )

        try:
            downloader: Optional[BaseDownloader] = None
            if file_type in ("android", "android_prerelease"):
                downloader = self.android_downloader
            elif file_type in (
                "desktop",
                "desktop_prerelease",
                FILE_TYPE_DESKTOP,
                FILE_TYPE_DESKTOP_PRERELEASE,
            ):
                downloader = self.desktop_downloader
            elif file_type in (FILE_TYPE_CLIENT_APP, FILE_TYPE_CLIENT_APP_PRERELEASE):
                downloader = self.client_app_downloader
            elif file_type in (
                FILE_TYPE_FIRMWARE,
                FILE_TYPE_FIRMWARE_PRERELEASE,
                FILE_TYPE_FIRMWARE_MANIFEST,
                FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
            ):
                downloader = self.firmware_downloader
            if downloader:
                ok = downloader.download(url, target_path)
                if ok and downloader.verify(target_path):
                    if file_type in (FILE_TYPE_DESKTOP, FILE_TYPE_DESKTOP_PRERELEASE):
                        zip_checker = getattr(downloader, "_is_zip_intact", None)
                        if (
                            str(target_path).lower().endswith(".zip")
                            and callable(zip_checker)
                            and not zip_checker(target_path)
                        ):
                            downloader.cleanup_file(target_path)
                            return self._create_failure_result(
                                failed_result,
                                Path(target_path),
                                url,
                                file_type,
                                "Retry attempt failed",
                                "Downloaded desktop asset failed post-download validation",
                                is_retryable_override=False,
                            )
                    if file_type == FILE_TYPE_FIRMWARE_MANIFEST:
                        try:
                            with open(
                                target_path, "r", encoding="utf-8"
                            ) as manifest_file:
                                json.load(manifest_file)
                        except (json.JSONDecodeError, OSError, ValueError):
                            downloader.cleanup_file(target_path)
                            return self._create_failure_result(
                                failed_result,
                                Path(target_path),
                                url,
                                file_type,
                                "Retry attempt failed",
                                "Downloaded manifest is not valid JSON",
                                is_retryable_override=False,
                            )
                    return DownloadResult(
                        success=True,
                        release_tag=failed_result.release_tag,
                        file_path=Path(target_path),
                        download_url=url,
                        file_size=failed_result.file_size,
                        file_type=file_type,
                        retry_count=failed_result.retry_count,
                        retry_timestamp=failed_result.retry_timestamp,
                        error_message=None,
                        is_retryable=False,
                    )
            else:
                logger.debug("Retry not supported for file type: %s", file_type)

            # If we reach here, retry failed verification or download
            return self._create_failure_result(
                failed_result, Path(target_path), url, file_type, "Retry attempt failed"
            )

        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.error(f"Retry exception for {failed_result.release_tag}: {exc}")
            return self._create_failure_result(
                failed_result,
                Path(target_path),
                url,
                file_type,
                "",
                str(exc),
                is_retryable_override=False,
            )

    def _generate_retry_report(
        self,
        retryable_failures: List[DownloadResult],
        non_retryable_failures: List[DownloadResult],
    ) -> None:
        """
        Log a structured report summarizing retry outcomes for failed downloads.

        Produces aggregate statistics and breakdowns including total failures, count of retryable
        and non-retryable failures, retry success rate, per-file-type counts, distribution by
        retry attempt, and counts by error reason. Also logs the effective retry configuration
        (max retries, base delay, backoff factor).

        Parameters:
            retryable_failures (List[DownloadResult]): Failures that were subject to retry attempts.
            non_retryable_failures (List[DownloadResult]): Failures that were not eligible for retries.
        """
        total_failures = len(retryable_failures) + len(non_retryable_failures)
        retry_success_rate = 0.0

        if retryable_failures:
            # Count how many retries succeeded (by checking if they're no longer in failed_downloads)
            successful_retries = len(retryable_failures) - len(
                [f for f in retryable_failures if f in self.failed_downloads]
            )
            retry_success_rate = (successful_retries / len(retryable_failures)) * 100

        logger.info("\n" + "=" * 60)
        logger.info("📊 DETAILED RETRY REPORT")
        logger.info("=" * 60)

        logger.info("📈 Overall Statistics:")
        logger.info(f"  - Total failures processed: {total_failures}")
        logger.info(f"  - Retryable failures: {len(retryable_failures)}")
        logger.info(f"  - Non-retryable failures: {len(non_retryable_failures)}")
        logger.info(f"  - Retry success rate: {retry_success_rate:.1f}%")

        if retryable_failures:
            logger.info("\n🔄 Retryable Failures Summary:")
            by_type: Dict[str, int] = {}
            for failure in retryable_failures:
                failure_type = failure.file_type or FILE_TYPE_UNKNOWN
                by_type[failure_type] = by_type.get(failure_type, 0) + 1

            for file_type, count in by_type.items():
                logger.info(f"  - {file_type}: {count} failures")

            # Show retry distribution
            by_attempt: Dict[int, int] = {}
            for failure in retryable_failures:
                attempt = failure.retry_count
                by_attempt[attempt] = by_attempt.get(attempt, 0) + 1

            logger.info("\n📊 Retry Attempt Distribution:")
            for attempt, count in sorted(by_attempt.items()):
                logger.info(f"  - Attempt {attempt}: {count} failures")

        if non_retryable_failures:
            logger.info("\n❌ Non-Retryable Failures Summary:")
            by_reason: Dict[str, int] = {}
            for failure in non_retryable_failures:
                reason = failure.error_type or ERROR_TYPE_UNKNOWN
                by_reason[reason] = by_reason.get(reason, 0) + 1

            for reason, count in by_reason.items():
                logger.info(f"  - {reason}: {count} failures")

        logger.info("\n💡 Retry Configuration:")
        logger.info(f"  - Max retries: {self.config.get('MAX_RETRIES', 3)}")
        logger.info(
            f"  - Base delay: {self.config.get('RETRY_DELAY_SECONDS', 0)} seconds"
        )
        logger.info(
            f"  - Backoff factor: {self.config.get('RETRY_BACKOFF_FACTOR', 2.0)}"
        )

        logger.info("=" * 60 + "\n")

    def _enhance_download_results_with_metadata(self) -> None:
        """
        Populate missing metadata fields on aggregated download results after a pipeline run.

        For each DownloadResult in `download_results` and `failed_downloads` this infers a missing `file_type` from the result's `file_path` (mapping to "android", "firmware", "repository", or "unknown") and, for failed results lacking retry data, sets `is_retryable` using `_is_download_retryable(result)` and initializes `retry_count` to 0.
        """
        for result in self.download_results + self.failed_downloads:
            # Set file type based on file path if not already set
            if not result.file_type and result.file_path:
                # Convert to Path for reliable path component checking
                file_path = (
                    Path(result.file_path)
                    if not isinstance(result.file_path, Path)
                    else result.file_path
                )
                file_path_str = str(result.file_path)
                path_parts = file_path.parts

                # Check repository first since repo paths contain both firmware and repo directories
                if REPO_DOWNLOADS_DIR in path_parts:
                    result.file_type = FILE_TYPE_REPOSITORY
                elif APKS_DIR_NAME in path_parts or file_path_str.endswith(".apk"):
                    result.file_type = "android"
                elif self._is_firmware_manifest_asset(file_path.name):
                    result.file_type = FILE_TYPE_FIRMWARE_MANIFEST
                elif any(
                    file_path_str.lower().endswith(ext)
                    for ext in (".dmg", ".msi", ".exe", ".deb", ".rpm", ".appimage")
                ):
                    result.file_type = "desktop"
                elif FIRMWARE_DIR_NAME in path_parts or file_path_str.endswith(
                    (".zip", ".bin", ".elf")
                ):
                    result.file_type = FILE_TYPE_FIRMWARE
                else:
                    result.file_type = FILE_TYPE_UNKNOWN

            # Set retry metadata for failed downloads
            if not result.success and result.retry_count is None:
                result.is_retryable = self._is_download_retryable(result)
                result.retry_count = 0

    def _is_download_retryable(self, result: DownloadResult) -> bool:
        """
        Decide if a failed download should be retried based on the result's error_type.

        Parameters:
            result (DownloadResult): The failed download result to evaluate.

        Returns:
            true if the failure is considered retryable, false otherwise.

        Notes:
            - Treats `network_error`, `connection_error`, `timeout`, `http_error`, `rate_limit`, and `temporary_failure` as retryable.
            - Treats `permission_error`, `validation_error`, `corrupted_file`, `disk_full`, `invalid_url`, and `authentication_error` as non-retryable.
            - Unknown or missing `error_type` defaults to retryable.
        """
        if not result.error_type:
            return True  # Unknown errors are retryable by default

        # These error types are generally retryable
        retryable_errors = {
            "network_error",
            "connection_error",
            "timeout",
            "http_error",
            "rate_limit",
            "temporary_failure",
        }

        # These error types are generally not retryable
        non_retryable_errors = {
            "permission_error",
            "validation_error",
            "corrupted_file",
            "disk_full",
            "invalid_url",
            "authentication_error",
        }

        if result.error_type in retryable_errors:
            return True
        elif result.error_type in non_retryable_errors:
            return False
        else:
            # Default to retryable for unknown error types
            return True

    def _log_download_summary(self, start_time: float) -> None:
        """
        Log a concise summary of the download pipeline results.

        Logs the elapsed time since `start_time`, counts of successfully downloaded assets (excluding skipped),
        counts of skipped successful downloads, and the number of failed downloads. Emits a warning if any downloads failed.

        Parameters:
            start_time (float): Epoch timestamp (seconds) marking when the pipeline started (as returned by time.time()).
        """
        elapsed_time = time.time() - start_time
        downloaded = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        ]
        skipped = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is True
        ]
        total_failures = len(self.failed_downloads)

        logger.info("Download pipeline completed")
        logger.info(f"Time taken: {elapsed_time:.2f} seconds")
        if not downloaded and total_failures == 0:
            logger.info("All assets are up to date.")
        else:
            # Group results by product category
            client_app_downloads = [
                r
                for r in downloaded
                if r.file_type
                in (
                    FILE_TYPE_CLIENT_APP,
                    FILE_TYPE_CLIENT_APP_PRERELEASE,
                )
            ]
            firmware_downloads = [
                r
                for r in downloaded
                if r.file_type
                in (
                    FILE_TYPE_FIRMWARE,
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                    FILE_TYPE_FIRMWARE_MANIFEST,
                )
            ]

            # Group failures by product category
            client_app_failures = [
                f
                for f in self.failed_downloads
                if f.file_type
                in (
                    FILE_TYPE_CLIENT_APP,
                    FILE_TYPE_CLIENT_APP_PRERELEASE,
                )
            ]
            firmware_failures = [
                f
                for f in self.failed_downloads
                if f.file_type
                in (
                    FILE_TYPE_FIRMWARE,
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                    FILE_TYPE_FIRMWARE_MANIFEST,
                )
            ]

            if client_app_downloads or client_app_failures:
                failure_part = (
                    f", {len(client_app_failures)} failed"
                    if client_app_failures
                    else ""
                )
                logger.info(
                    f"Client apps: {len(client_app_downloads)} downloaded{failure_part}"
                )
            if firmware_downloads or firmware_failures:
                failure_part = (
                    f", {len(firmware_failures)} failed" if firmware_failures else ""
                )
                logger.info(
                    f"Firmware: {len(firmware_downloads)} downloaded{failure_part}"
                )
        if skipped:
            logger.debug("Skipped %d existing assets", len(skipped))

        if total_failures > 0:
            logger.warning(
                f"{total_failures} downloads failed - check logs for details"
            )

    def log_firmware_release_history_summary(self) -> None:
        """
        Emit firmware release summaries when firmware release history and releases are available.

        Logs three reports via the firmware release history manager: a release channel summary, a release status summary, and a duplicate base-version summary. If the `FILTER_REVOKED_RELEASES` config is enabled, revoked firmware releases are excluded from the channel and status summaries. If the `KEEP_LAST_BETA` config is enabled, the channel summary's retention window may be expanded to include the most recent beta release according to the configured firmware keep limit.

        Uses `self.firmware_releases_selected` (the actual set of releases processed
        during the download phase) as the source of truth for the summary when available.
        This ensures the summary accurately reflects the retained firmware set, including
        any beta appended via KEEP_LAST_BETA behavior.
        """
        if not self.firmware_release_history or not self.firmware_releases:
            return

        manager = self.firmware_downloader.release_history_manager
        keep_limit_for_summary = self._get_firmware_keep_limit()
        keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
        filter_revoked = self.config.get(
            "FILTER_REVOKED_RELEASES", DEFAULT_FILTER_REVOKED_RELEASES
        )

        # Use the actual selected releases as source of truth when available
        # These are the releases that were actually processed/retained during the run
        if self.firmware_releases_selected:
            releases_for_summary = self.firmware_releases_selected
            # Apply revoked filtering if enabled (beta appended via KEEP_LAST_BETA
            # may need to be checked for revoked status)
            if filter_revoked:
                releases_for_summary = [
                    release
                    for release in releases_for_summary
                    if not self.firmware_downloader.is_release_revoked(release)
                ]
            # Update keep_limit_for_summary to match actual count when using selected set
            keep_limit_for_summary = len(releases_for_summary)
        else:
            # Fallback: reconstruct from all releases (legacy behavior)
            releases_for_summary = self.firmware_releases
            if filter_revoked:
                releases_for_summary = [
                    release
                    for release in self.firmware_releases
                    if not self.firmware_downloader.is_release_revoked(release)
                ]

            if keep_last_beta:
                keep_limit_for_summary = manager.expand_keep_limit_to_include_beta(
                    releases_for_summary, keep_limit_for_summary
                )

        manager.log_release_channel_summary(
            releases_for_summary, label="Firmware", keep_limit=keep_limit_for_summary
        )

        kept_releases = manager.get_releases_for_summary(
            releases_for_summary, keep_limit=keep_limit_for_summary
        )
        kept_tags = {release.tag_name for release in kept_releases}
        entries = self.firmware_release_history.get("entries")
        entries_dict = entries if isinstance(entries, dict) else {}
        filtered_history: Dict[str, Any] = {
            "entries": {
                tag: entry for tag, entry in entries_dict.items() if tag in kept_tags
            }
        }

        manager.log_release_status_summary(filtered_history, label="Firmware")
        manager.log_duplicate_base_versions(kept_releases, label="Firmware")
        self._log_prerelease_summary()

    def _log_prerelease_summary(self) -> None:
        """
        Log prerelease history details that were captured during firmware downloads.

        The summary is emitted near other release history reports so prerelease commit
        information appears with the final summaries instead of during the download loop.
        """
        summary = self.firmware_prerelease_summary
        if not summary:
            return

        self.firmware_prerelease_summary = None

        history_entries = summary.get("history_entries") or []
        clean_latest_release = summary.get("clean_latest_release")
        expected_version = summary.get("expected_version")

        if not history_entries:
            logger.debug("Skipping prerelease summary: missing history_entries")
            return
        if not isinstance(clean_latest_release, str):
            logger.debug(
                "Skipping prerelease summary: clean_latest_release is not a string (got %s)",
                type(clean_latest_release).__name__,
            )
            return
        if not isinstance(expected_version, str):
            logger.debug(
                "Skipping prerelease summary: expected_version is not a string (got %s)",
                type(expected_version).__name__,
            )
            return

        self.firmware_downloader.log_prerelease_summary(
            history_entries, clean_latest_release, expected_version
        )

    def _get_firmware_keep_limit(self) -> int:
        """
        Get the configured firmware versions-to-keep limit as a non-negative integer.

        If the configuration value is missing or cannot be converted to an integer, the default
        DEFAULT_FIRMWARE_VERSIONS_TO_KEEP is returned.

        Returns:
            int: The configured limit coerced to an int and clamped to zero or greater.
        """
        raw_keep_limit = self.config.get(
            "FIRMWARE_VERSIONS_TO_KEEP", DEFAULT_FIRMWARE_VERSIONS_TO_KEEP
        )
        try:
            return max(0, int(raw_keep_limit))
        except (TypeError, ValueError):
            return int(DEFAULT_FIRMWARE_VERSIONS_TO_KEEP)

    def get_download_statistics(self) -> Dict[str, Any]:
        """
        Summarizes download attempts and outcomes for the current run.

        Returns:
            dict: Mapping with the following keys:
                - "total_downloads": number of attempted downloads (excludes skipped results).
                - "successful_downloads": number of completed, non-skipped downloads.
                - "skipped_downloads": number of downloads marked as skipped.
                - "failed_downloads": number of failed downloads.
                - "success_rate": overall success percentage as a float (0-100).
                - "android_downloads": count of successful Android artifact downloads.
                - "firmware_downloads": count of successful firmware artifact downloads.
                - "desktop_downloads": count of successful Desktop artifact downloads.
                - "repository_downloads": count of repository downloads (always 0 for automatic pipeline).
        """
        downloaded = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        ]
        skipped = [
            result
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is True
        ]
        attempted = len(downloaded) + len(self.failed_downloads)
        return {
            # "Downloads" excludes skipped results for legacy-parity reporting.
            "total_downloads": attempted,
            "successful_downloads": len(downloaded),
            "skipped_downloads": len(skipped),
            "failed_downloads": len(self.failed_downloads),
            "success_rate": self._calculate_success_rate(),
            "client_app_downloads": self._count_artifact_downloads(
                FILE_TYPE_CLIENT_APP
            ),
            "android_downloads": self._count_artifact_downloads(FILE_TYPE_CLIENT_APP),
            "firmware_downloads": self._count_artifact_downloads(FILE_TYPE_FIRMWARE),
            "desktop_downloads": self._count_artifact_downloads(FILE_TYPE_CLIENT_APP),
            # Repository downloads are not part of the automatic download pipeline.
            "repository_downloads": 0,
        }

    def _calculate_success_rate(self) -> float:
        """
        Compute the percentage of attempted downloads that completed successfully.

        Returns:
            float: Percentage (0.0-100.0) of successful downloads. Returns 100.0 when there were no attempted downloads.
        """
        downloaded_count = sum(
            1
            for result in self.download_results
            if result.success and getattr(result, "was_skipped", False) is not True
        )
        attempted = downloaded_count + len(self.failed_downloads)
        return (downloaded_count / attempted) * 100 if attempted > 0 else 100.0

    def _count_artifact_downloads(self, artifact_type: str) -> int:
        """
        Count successful (non-skipped) downloads that correspond to the given artifact type.

        Parameters:
            artifact_type (str): Artifact identifier to match against a result's `file_type` or as a substring in `file_path` (e.g., "android", "firmware").

        Returns:
            int: Number of matching downloads that were not skipped.
        """

        def _matches_group(file_type: str) -> bool:
            if artifact_type == FILE_TYPE_FIRMWARE:
                return file_type in {
                    FILE_TYPE_FIRMWARE,
                    FILE_TYPE_FIRMWARE_MANIFEST,
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                }
            if artifact_type == FILE_TYPE_CLIENT_APP:
                return file_type in {
                    FILE_TYPE_CLIENT_APP,
                    FILE_TYPE_CLIENT_APP_PRERELEASE,
                    "android",
                    "android_prerelease",
                    FILE_TYPE_DESKTOP,
                    FILE_TYPE_DESKTOP_PRERELEASE,
                }
            if artifact_type == FILE_TYPE_DESKTOP:
                return file_type == FILE_TYPE_DESKTOP
            if artifact_type == "android":
                return file_type in {"android", "android_prerelease"}
            return file_type == artifact_type

        count = 0
        for result in self.download_results:
            if not result.success or getattr(result, "was_skipped", False) is True:
                continue

            file_type = getattr(result, "file_type", None)
            if isinstance(file_type, str) and file_type:
                if _matches_group(file_type):
                    count += 1
                continue

            # Legacy fallback for untyped results.
            file_path = getattr(result, "file_path", None)
            if file_path and artifact_type in str(file_path):
                count += 1

        return count

    def cleanup_old_versions(self) -> None:
        """
        Prune locally stored client app and firmware artifacts according to configured retention settings and remove prerelease directories marked as deleted.

        This routine reads retention settings (e.g., `ANDROID_VERSIONS_TO_KEEP`, `FIRMWARE_VERSIONS_TO_KEEP`) and instructs the Android and firmware downloaders to remove older releases. When firmware retention is applied, the `KEEP_LAST_BETA` setting is honored if present. After pruning releases, it removes any prerelease directories that have been recorded as deleted. On filesystem or configuration-related errors (`OSError`, `ValueError`, `TypeError`) it logs an error.
        """
        try:
            logger.info("Cleaning up old versions...")

            # Clean up client app versions once for the unified app tree.
            if self.android_downloader is not self.client_app_downloader:
                app_keep = 5
            else:
                app_keep = self.config.get(
                    "APP_VERSIONS_TO_KEEP",
                    self.config.get("ANDROID_VERSIONS_TO_KEEP", 5),
                )
            cached_app_releases = (
                self.client_app_releases
                or self.android_releases
                or self.desktop_releases
            )
            app_cleanup_downloader = (
                self.android_downloader
                if cached_app_releases is None
                and self.android_downloader is not self.client_app_downloader
                else self.client_app_downloader
            )
            app_cleanup_downloader.cleanup_old_versions(
                app_keep, cached_releases=cached_app_releases
            )
            if (
                self.config.get("SAVE_DESKTOP_APP", False)
                and self.desktop_downloader is not self.client_app_downloader
            ):
                desktop_keep = self.config.get("DESKTOP_VERSIONS_TO_KEEP", app_keep)
                self.desktop_downloader.cleanup_old_versions(
                    desktop_keep, cached_releases=self.desktop_releases
                )

            # Clean up firmware versions
            firmware_keep = self._get_firmware_keep_limit()
            keep_last_beta = self.config.get("KEEP_LAST_BETA", DEFAULT_KEEP_LAST_BETA)
            self.firmware_downloader.cleanup_old_versions(
                firmware_keep,
                cached_releases=self.firmware_releases,
                keep_last_beta=keep_last_beta,
            )
            self._cleanup_deleted_prereleases()

            logger.info("Old version cleanup completed")

        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Error cleaning up old versions: {e}")

    def _cleanup_deleted_prereleases(self) -> None:
        """
        Remove local firmware prerelease directories that are recorded as deleted in prerelease history.

        Queries the prerelease commit history for the expected firmware prerelease version derived from the latest firmware release. For each history entry with status "deleted", verifies the directory name is safe and removes the corresponding directory under the firmware prereleases folder if it exists. Uses the configured cache and the optional GitHub token when fetching history. Network and filesystem errors are caught and logged; the function does not raise on those errors.
        """
        try:
            # This logic is specific to firmware prereleases from meshtastic.github.io
            latest_firmware_release = self.firmware_downloader.get_latest_release_tag()
            if not latest_firmware_release:
                return

            expected_version = (
                self.version_manager.calculate_expected_prerelease_version(
                    latest_firmware_release
                )
            )
            if not expected_version:
                return

            history = self.prerelease_manager.get_prerelease_commit_history(
                expected_version,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                force_refresh=False,
            )

            deleted_entries = [e for e in history if e.get("status") == "deleted"]
            if not deleted_entries:
                return

            prerelease_base_dir = (
                Path(self.firmware_downloader.download_dir)
                / FIRMWARE_DIR_NAME
                / FIRMWARE_PRERELEASES_DIR_NAME
            )
            if not prerelease_base_dir.exists():
                return

            for entry in deleted_entries:
                directory_name = entry.get("directory")
                if not directory_name:
                    continue

                safe_name = os.path.basename(directory_name)
                if not safe_name or safe_name != directory_name:
                    logger.warning(
                        "Skipping unsafe prerelease directory name: %s", directory_name
                    )
                    continue
                dir_to_delete = prerelease_base_dir / safe_name
                if dir_to_delete.exists() and dir_to_delete.is_dir():
                    logger.info(
                        f"Removing deleted prerelease directory: {directory_name}"
                    )
                    if not _safe_rmtree(
                        str(dir_to_delete), str(prerelease_base_dir), directory_name
                    ):
                        logger.warning(
                            f"Failed to safely remove directory: {directory_name}"
                        )

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error during deleted prerelease cleanup: {e}", exc_info=True)

    def get_latest_versions(self) -> Dict[str, Optional[str]]:
        """
        Retrieve the latest known version tags for Android, Desktop, and firmware artifacts, including active prerelease identifiers when available.

        Returns:
            Dict[str, Optional[str]]: Mapping with keys:
                - "android": latest Android release tag or None
                - "firmware": latest firmware release tag or None
                - "firmware_prerelease": active firmware prerelease identifier (without "firmware-" prefix when applicable) or None
                - "android_prerelease": latest Android prerelease tag or None
                - "desktop": latest Desktop release tag or None
                - "desktop_prerelease": latest Desktop prerelease tag or None
        """
        firmware_prerelease = None
        latest_firmware_release = self.firmware_downloader.get_latest_release_tag()

        if latest_firmware_release:
            clean_latest_release = (
                self.version_manager.extract_clean_version(latest_firmware_release)
                or latest_firmware_release
            )
            expected_version = (
                self.version_manager.calculate_expected_prerelease_version(
                    clean_latest_release
                )
            )
            if expected_version:
                # Do not force refresh here to avoid API calls just for status display
                active_dir, _ = (
                    self.prerelease_manager.get_latest_active_prerelease_from_history(
                        expected_version,
                        cache_manager=self.cache_manager,
                        github_token=self.config.get("GITHUB_TOKEN"),
                        allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
                        force_refresh=False,
                    )
                )
                if active_dir and active_dir.startswith(FIRMWARE_DIR_PREFIX):
                    firmware_prerelease = active_dir[len(FIRMWARE_DIR_PREFIX) :]
                else:
                    firmware_prerelease = active_dir

        app_releases = (
            self.client_app_releases
            or self.android_releases
            or self.desktop_releases
            or self._ensure_android_releases()
        )
        latest_app_release = next(
            (release.tag_name for release in app_releases if not release.prerelease),
            None,
        )
        latest_app_prerelease = (
            self.client_app_downloader.get_latest_prerelease_tag(app_releases)
            if app_releases
            else None
        )

        return {
            "client_app": latest_app_release,
            "client_app_prerelease": latest_app_prerelease,
            "android": latest_app_release,
            "firmware": latest_firmware_release,
            "firmware_prerelease": firmware_prerelease,
            "android_prerelease": latest_app_prerelease,
            "desktop": latest_app_release,
            "desktop_prerelease": latest_app_prerelease,
        }

    def update_version_tracking(self) -> None:
        """
        Refresh recorded latest release tags for Android, Desktop, and firmware and update prerelease tracking.

        If per-run release caches are present, uses them; otherwise fetches the most recent release for each artifact and updates the corresponding downloader's latest release tag. Invokes prerelease tracking refresh and logs an error if the update fails.
        """
        try:
            # Use cached releases if available
            app_releases = (
                self.client_app_releases
                or self.android_releases
                or self.desktop_releases
                or self._ensure_android_releases()
            )
            firmware_releases = self._ensure_firmware_releases()

            # Update tracking
            latest_app_release = next(
                (release for release in app_releases if not release.prerelease),
                None,
            )
            if latest_app_release:
                self.client_app_downloader.update_latest_release_tag(
                    latest_app_release.tag_name
                )
                if self.android_downloader is not self.client_app_downloader:
                    self.android_downloader.update_latest_release_tag(
                        latest_app_release.tag_name
                    )

            if firmware_releases:
                self.firmware_downloader.update_latest_release_tag(
                    firmware_releases[0].tag_name
                )

            desktop_releases = self.desktop_releases or []
            latest_desktop_release = next(
                (release for release in desktop_releases if not release.prerelease),
                None,
            )
            if (
                latest_desktop_release
                and self.desktop_downloader is not self.client_app_downloader
            ):
                self.desktop_downloader.update_latest_release_tag(
                    latest_desktop_release.tag_name
                )

            # Manage prerelease tracking files
            self._manage_prerelease_tracking()

        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error updating version tracking: {e}")

    def _refresh_commit_history_cache(self) -> None:
        """
        Refresh the commit history cache for prerelease filtering.

        Uses the prerelease manager to fetch recent repository commits with the configured GitHub token.
        This is used to determine which prereleases should be kept or filtered out.
        """
        try:
            logger.debug("Refreshing commit history cache...")
            self.prerelease_manager.fetch_recent_repo_commits(
                DEFAULT_PRERELEASE_COMMITS_TO_FETCH,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=self.config.get("ALLOW_ENV_TOKEN", True),
            )
            logger.debug("Commit history cache refreshed")
        except (requests.RequestException, OSError, ValueError, TypeError) as e:
            logger.error(f"Error refreshing commit history cache: {e}")

    def _manage_prerelease_tracking(self) -> None:
        """
        Manage prerelease tracking files for Android, Desktop, and firmware.

        Cleans up superseded prerelease directories and ensures prerelease tracking files remain consistent for each artifact type.
        """
        try:
            logger.info("Managing prerelease tracking files...")

            # Share recent commits with downloaders for prerelease filtering
            self._refresh_commit_history_cache()

            # Manage client app prerelease tracking once for the unified app tree.
            app_tracking_downloader = (
                self.android_downloader
                if self.android_downloader is not self.client_app_downloader
                else self.client_app_downloader
            )
            app_tracking_downloader.manage_prerelease_tracking_files(
                cached_releases=self.client_app_releases or self.android_releases
            )
            if self.desktop_downloader is not self.client_app_downloader:
                self.desktop_downloader.manage_prerelease_tracking_files(
                    cached_releases=self.desktop_releases
                )

            # Manage firmware prerelease tracking - pass cached releases to avoid redundant API calls
            self.firmware_downloader.manage_prerelease_tracking_files(
                cached_releases=self.firmware_releases
            )

            logger.info("Prerelease tracking management completed")

        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Error managing prerelease tracking: {e}")
