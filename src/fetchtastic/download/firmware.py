"""
Firmware Release Downloader

This module implements the specific downloader for Meshtastic firmware releases.
"""

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fetchtastic.constants import (
    EXECUTABLE_PERMISSIONS,
    FIRMWARE_DIR_PREFIX,
    LATEST_FIRMWARE_PRERELEASE_JSON_FILE,
    LATEST_FIRMWARE_RELEASE_JSON_FILE,
    MESHTASTIC_FIRMWARE_RELEASES_URL,
    MESHTASTIC_GITHUB_IO_CONTENTS_URL,
    RELEASES_CACHE_EXPIRY_HOURS,
)
from fetchtastic.device_hardware import DeviceHardwareManager
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    download_file_with_retry,
    make_github_api_request,
    matches_extract_patterns,
    verify_file_integrity,
)

from .base import BaseDownloader
from .interfaces import Asset, DownloadResult, Release
from .prerelease_history import PrereleaseHistoryManager
from .version import VersionManager


class FirmwareReleaseDownloader(BaseDownloader):
    """
    Downloader for Meshtastic firmware releases.

    This class handles:
    - Fetching firmware releases from GitHub
    - Downloading firmware ZIP files
    - Extracting firmware files with pattern matching
    - Managing firmware-specific version tracking
    - Handling firmware prereleases
    - Cleaning up old firmware versions
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the firmware downloader.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.firmware_releases_url = MESHTASTIC_FIRMWARE_RELEASES_URL
        self.latest_release_file = LATEST_FIRMWARE_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_FIRMWARE_PRERELEASE_JSON_FILE

    def get_target_path_for_release(self, release_tag: str, file_name: str) -> str:
        """
        Get the target path for a firmware asset under the firmware directory.

        The legacy layout keeps firmware files in a firmware-specific subdirectory;
        preserve that structure so cleanup and reporting can detect firmware assets.
        """
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")

        version_dir = os.path.join(self.download_dir, "firmware", safe_release)
        os.makedirs(version_dir, exist_ok=True)
        return os.path.join(version_dir, safe_name)

    def get_releases(self, limit: Optional[int] = None) -> List[Release]:
        """
        Get available firmware releases from GitHub.

        Args:
            limit: Maximum number of releases to return

        Returns:
            List[Release]: List of available firmware releases
        """
        try:
            params = {"per_page": 8}
            url_key = self.cache_manager.build_url_cache_key(
                self.firmware_releases_url, params
            )
            releases_data = self.cache_manager.read_releases_cache_entry(
                url_key, expiry_seconds=int(RELEASES_CACHE_EXPIRY_HOURS * 3600)
            )

            if releases_data is None:
                response = make_github_api_request(
                    self.firmware_releases_url,
                    self.config.get("GITHUB_TOKEN"),
                    allow_env_token=True,
                    params=params,
                )
                releases_data = response.json() if hasattr(response, "json") else []
                if isinstance(releases_data, list):
                    logger.debug(
                        "Cached %d releases for %s (fetched from API)",
                        len(releases_data),
                        self.firmware_releases_url,
                    )
                self.cache_manager.write_releases_cache_entry(
                    url_key, releases_data if isinstance(releases_data, list) else []
                )

            if not releases_data or not isinstance(releases_data, list):
                logger.error("Invalid releases data received from GitHub API")
                return []

            releases = []
            for release_data in releases_data:
                # Filter out releases without assets
                if not release_data.get("assets"):
                    continue

                release = Release(
                    tag_name=release_data["tag_name"],
                    prerelease=release_data.get("prerelease", False),
                    published_at=release_data.get("published_at"),
                    body=release_data.get("body"),
                )

                # Add assets to the release
                for asset_data in release_data["assets"]:
                    asset = Asset(
                        name=asset_data["name"],
                        download_url=asset_data["browser_download_url"],
                        size=asset_data["size"],
                        browser_download_url=asset_data.get("browser_download_url"),
                        content_type=asset_data.get("content_type"),
                    )
                    release.assets.append(asset)

                releases.append(release)

                # Respect limit if specified
                if limit and len(releases) >= limit:
                    break

            return releases

        except Exception as e:
            logger.error(f"Error fetching firmware releases: {e}")
            return []

    def get_assets(self, release: Release) -> List[Asset]:
        """
        Get downloadable assets for a specific firmware release.

        Args:
            release: The release to get assets for

        Returns:
            List[Asset]: List of downloadable assets for the release
        """
        return release.assets or []

    def get_download_url(self, asset: Asset) -> str:
        """
        Get the download URL for a specific firmware asset.

        Args:
            asset: The asset to get download URL for

        Returns:
            str: Direct download URL for the asset
        """
        return asset.download_url

    def download_firmware(self, release: Release, asset: Asset) -> DownloadResult:
        """
        Download a specific firmware file.

        Args:
            release: The release containing the firmware
            asset: The firmware asset to download

        Returns:
            DownloadResult: Result of the download operation
        """
        target_path: Optional[str] = None
        try:
            # Get target path for the firmware ZIP
            target_path = self.get_target_path_for_release(release.tag_name, asset.name)

            # Check if we need to download
            if self.is_asset_complete(release.tag_name, asset):
                logger.debug(
                    "Firmware %s already exists and is complete",
                    asset.name,
                )
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type="firmware",
                    was_skipped=True,
                )

            # Download the firmware ZIP
            success = self.download(asset.download_url, target_path)

            if success:
                # Verify the download
                if self.verify(target_path):
                    logger.info("Downloaded and verified %s", asset.name)
                    return self.create_download_result(
                        success=True,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type="firmware",
                    )
                else:
                    logger.error(f"Verification failed for {asset.name}")
                    self.cleanup_file(target_path)
                    return self.create_download_result(
                        success=False,
                        release_tag=release.tag_name,
                        file_path=target_path,
                        error_message="Verification failed",
                        download_url=asset.download_url,
                        file_size=asset.size,
                        file_type="firmware",
                        is_retryable=True,
                        error_type="validation_error",
                    )
            else:
                logger.error(f"Download failed for {asset.name}")
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    error_message="download(...) returned False",
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type="firmware",
                    is_retryable=True,
                    error_type="network_error",
                )

        except Exception as e:
            logger.error(f"Error downloading firmware {asset.name}: {e}")
            safe_path = target_path or os.path.join(self.download_dir, "firmware")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message=str(e),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type="firmware",
                is_retryable=True,
                error_type="network_error",
            )

    def is_release_complete(self, release: Release) -> bool:
        """
        Check if a release is already fully downloaded by verifying that all selected asset files exist.
        Args:
            release: The release to check.
        Returns:
            bool: True if the release is complete, False otherwise.
        """
        version_dir = os.path.join(self.download_dir, "firmware", release.tag_name)
        if not os.path.isdir(version_dir):
            return False

        expected_assets = [
            asset for asset in release.assets if self.should_download_release(release.tag_name, asset.name)
        ]

        if not expected_assets:
            return False

        for asset in expected_assets:
            asset_path = os.path.join(version_dir, asset.name)
            if not os.path.exists(asset_path):
                return False
            if asset.name.lower().endswith(".zip"):
                try:
                    import zipfile
                    with zipfile.ZipFile(asset_path, "r") as zf:
                        if zf.testzip() is not None:
                            return False
                except (zipfile.BadZipFile, IOError, OSError):
                    return False
            try:
                if os.path.getsize(asset_path) != asset.size:
                    return False
            except (OSError, TypeError):
                return False
        return True

    def validate_extraction_patterns(
        self, patterns: List[str], exclude_patterns: List[str]
    ) -> bool:
        """
        Validate extraction patterns to ensure they are safe and well-formed.

        Args:
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if patterns are valid, False otherwise
        """
        return self.file_operations.validate_extraction_patterns(
            patterns, exclude_patterns
        )

    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: List[str],
        exclude_patterns: List[str],
    ) -> bool:
        """
        Check if extraction is needed by examining existing files.

        Args:
            file_path: Path to the archive file
            extract_dir: Directory where files would be extracted
            patterns: List of filename patterns for extraction
            exclude_patterns: List of filename patterns to exclude

        Returns:
            bool: True if extraction is needed, False if files already exist
        """
        return self.file_operations.check_extraction_needed(
            file_path, extract_dir, patterns, exclude_patterns
        )

    def extract_firmware(
        self,
        release: Release,
        asset: Asset,
        patterns: List[str],
        exclude_patterns: Optional[List[str]] = None,
    ) -> DownloadResult:
        """
        Extract firmware files from a downloaded ZIP archive.

        Args:
            release: The release containing the firmware
            asset: The firmware asset that was downloaded
            patterns: List of filename patterns to extract
            exclude_patterns: Optional list of patterns to skip during extraction

        Returns:
            DownloadResult: Result of the extraction operation
        """
        zip_path: str = ""
        try:
            exclude_patterns = exclude_patterns or []

            # Get the path to the downloaded ZIP file
            zip_path = self.get_target_path_for_release(release.tag_name, asset.name)
            if not os.path.exists(zip_path):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="ZIP file not found",
                    file_type="firmware",
                    error_type="validation_error",
                )

            # Get the directory where files will be extracted
            extract_dir = os.path.dirname(zip_path)

            # Legacy parity: extraction is a no-op success when all matching files
            # already exist with expected sizes (skip instead of treating as failure).
            if not self.file_operations.validate_extraction_patterns(
                patterns, exclude_patterns
            ):
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="Invalid extraction patterns",
                    file_type="firmware",
                    error_type="validation_error",
                )

            if not self.file_operations.check_extraction_needed(
                zip_path, extract_dir, patterns, exclude_patterns
            ):
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    extracted_files=[],
                    file_type="firmware",
                    was_skipped=True,
                )

            extracted_files = self.file_operations.extract_archive(
                zip_path, extract_dir, patterns, exclude_patterns
            )
            if extracted_files:
                self.file_operations.generate_hash_for_extracted_files(extracted_files)

            if extracted_files:
                logger.info(f"Extracted {len(extracted_files)} files from {asset.name}")

                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    extracted_files=extracted_files,  # type: ignore[arg-type]
                    file_type="firmware",
                )
            else:
                logger.warning(
                    f"No files extracted from {asset.name} - no matches for patterns"
                )
                return self.create_download_result(
                    success=False,
                    release_tag=release.tag_name,
                    file_path=zip_path,
                    error_message="No files matched extraction patterns",
                    file_type="firmware",
                    error_type="validation_error",
                    is_retryable=False,
                )

        except Exception as e:
            logger.error(f"Error extracting firmware {asset.name}: {e}")
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=zip_path or os.path.join(self.download_dir, "firmware"),
                error_message=str(e),
                file_type="firmware",
                error_type="extraction_error",
            )

    def cleanup_old_versions(self, keep_limit: int) -> None:
        """
        Clean up old firmware versions according to retention policy.

        Args:
            keep_limit: Maximum number of versions to keep
        """
        try:
            # Get all firmware version directories
            firmware_dir = os.path.join(self.download_dir, "firmware")
            if not os.path.exists(firmware_dir):
                return

            # Get all version directories (excluding special directories)
            version_dirs = []
            for item in os.listdir(firmware_dir):
                item_path = os.path.join(firmware_dir, item)
                if (
                    os.path.isdir(item_path)
                    and re.match(r"^(v)?\d+\.\d+(?:\.\d+)?", item)
                    and item not in ["prerelease", "repo-dls"]
                ):
                    version_dirs.append(item)

            # Sort versions and keep only the newest ones
            version_dirs.sort(reverse=True, key=self._get_version_sort_key)

            # Remove old versions
            for old_version in version_dirs[keep_limit:]:
                old_dir = os.path.join(firmware_dir, old_version)
                try:
                    import shutil

                    shutil.rmtree(old_dir)
                    logger.info(f"Removed old firmware version: {old_version}")
                except OSError as e:
                    logger.error(
                        f"Error removing old firmware version {old_version}: {e}"
                    )

        except Exception as e:
            logger.error(f"Error cleaning up old firmware versions: {e}")

    def _get_version_sort_key(self, version_dir: str) -> tuple:
        """Get a sort key for version directories."""
        # Extract version numbers for sorting
        version = version_dir.lstrip("v")
        try:
            parts = list(map(int, version.split(".")))
            # Pad to 3 components for consistent sorting
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        except ValueError:
            return (0, 0, 0)

    def get_latest_release_tag(self) -> Optional[str]:
        """
        Get the latest firmware release tag from the tracking file.

        Returns:
            Optional[str]: Latest release tag, or None if not found
        """
        latest_file = os.path.join(self.download_dir, self.latest_release_file)
        if os.path.exists(latest_file):
            try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("latest_version")
            except (IOError, json.JSONDecodeError):
                pass
        return None

    def update_latest_release_tag(self, release_tag: str) -> bool:
        """
        Update the latest firmware release tag in the tracking file.

        Args:
            release_tag: The release tag to record

        Returns:
            bool: True if update succeeded, False otherwise
        """
        latest_file = os.path.join(self.download_dir, self.latest_release_file)
        data = {
            "latest_version": release_tag,
            "file_type": "firmware",
            "last_updated": self._get_current_iso_timestamp(),
        }
        return self.cache_manager.atomic_write_json(latest_file, data)

    def _get_current_iso_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _get_expiry_timestamp(self) -> str:
        """Get expiry timestamp (24 hours from now) in ISO 8601 format."""
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    def _get_prerelease_base_dir(self) -> str:
        prerelease_dir = os.path.join(self.download_dir, "firmware", "prerelease")
        os.makedirs(prerelease_dir, exist_ok=True)
        return prerelease_dir

    def _get_prerelease_patterns(self) -> List[str]:
        patterns = self.config.get("SELECTED_PRERELEASE_ASSETS") or []
        return patterns if isinstance(patterns, list) else [str(patterns)]

    def _matches_exclude_patterns(self, filename: str, patterns: List[str]) -> bool:
        filename_lower = filename.lower()
        return any(
            fnmatch.fnmatch(filename_lower, str(pattern).lower())
            for pattern in patterns or []
        )

    def _fetch_prerelease_directory_listing(
        self,
        prerelease_dir: str,
        *,
        force_refresh: bool,
    ) -> List[Dict[str, Any]]:
        contents = self.cache_manager.get_repo_contents(
            prerelease_dir,
            force_refresh=force_refresh,
            github_token=self.config.get("GITHUB_TOKEN"),
            allow_env_token=True,
        )
        logger.debug("Fetched %d items from repository", len(contents))
        return contents

    def _download_prerelease_assets(
        self,
        remote_dir: str,
        *,
        selected_patterns: List[str],
        exclude_patterns: List[str],
        force_refresh: bool,
    ) -> tuple[list[DownloadResult], list[DownloadResult], bool]:
        prerelease_base_dir = self._get_prerelease_base_dir()
        target_dir = os.path.join(prerelease_base_dir, remote_dir)
        os.makedirs(target_dir, exist_ok=True)

        device_manager = DeviceHardwareManager()

        contents = self._fetch_prerelease_directory_listing(
            remote_dir, force_refresh=force_refresh
        )
        file_items = [
            item
            for item in contents
            if isinstance(item, dict) and item.get("type") == "file"
        ]

        matching: list[Dict[str, Any]] = []
        for item in file_items:
            name = str(item.get("name") or "")
            if not name:
                continue
            if exclude_patterns and self._matches_exclude_patterns(
                name, exclude_patterns
            ):
                logger.debug(
                    "Skipping pre-release file %s (matched exclude pattern)", name
                )
                continue
            if selected_patterns and not matches_extract_patterns(
                name, selected_patterns, device_manager=device_manager
            ):
                continue
            matching.append(item)

        logger.debug("Found %d matching prerelease files", len(matching))

        successes: list[DownloadResult] = []
        failures: list[DownloadResult] = []
        any_downloaded = False

        for item in matching:
            name = str(item.get("name") or "")
            url = item.get("download_url") or item.get("browser_download_url")
            if not name or not url:
                continue

            target_path = os.path.join(target_dir, name)
            try:
                if not force_refresh and os.path.exists(target_path):
                    zip_ok = True
                    if name.lower().endswith(".zip"):
                        try:
                            import zipfile

                            with zipfile.ZipFile(target_path, "r") as zf:
                                zip_ok = zf.testzip() is None
                        except Exception:
                            zip_ok = False

                    if zip_ok and verify_file_integrity(target_path):
                        logger.debug(
                            "Prerelease file already exists and is valid: %s", name
                        )
                        successes.append(
                            self.create_download_result(
                                success=True,
                                release_tag=remote_dir,
                                file_path=target_path,
                                download_url=str(url),
                                file_size=item.get("size"),
                                file_type="firmware_prerelease",
                                was_skipped=True,
                            )
                        )
                        continue

                ok = download_file_with_retry(str(url), target_path)
                if ok:
                    any_downloaded = True
                    if name.lower().endswith(".sh") and os.name != "nt":
                        try:
                            os.chmod(target_path, EXECUTABLE_PERMISSIONS)
                        except OSError:
                            pass
                    successes.append(
                        self.create_download_result(
                            success=True,
                            release_tag=remote_dir,
                            file_path=target_path,
                            download_url=str(url),
                            file_size=item.get("size"),
                            file_type="firmware_prerelease",
                        )
                    )
                else:
                    failures.append(
                        self.create_download_result(
                            success=False,
                            release_tag=remote_dir,
                            file_path=target_path,
                            error_message="download(...) returned False",
                            download_url=str(url),
                            file_size=item.get("size"),
                            file_type="firmware_prerelease",
                            is_retryable=True,
                            error_type="network_error",
                        )
                    )
            except Exception as exc:
                failures.append(
                    self.create_download_result(
                        success=False,
                        release_tag=remote_dir,
                        file_path=target_path,
                        error_message=str(exc),
                        download_url=str(url),
                        file_size=item.get("size"),
                        file_type="firmware_prerelease",
                        is_retryable=True,
                        error_type="network_error",
                    )
                )

        return successes, failures, any_downloaded

    def download_repo_prerelease_firmware(
        self,
        latest_release_tag: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[DownloadResult], list[DownloadResult], Optional[str]]:
        """
        Download firmware prerelease assets from meshtastic.github.io (legacy behavior).
        """
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return [], [], None

        logger.info("Checking for pre-release firmware...")

        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()
        clean_latest_release = (
            version_manager.extract_clean_version(latest_release_tag)
            or latest_release_tag
        )
        expected_version = version_manager.calculate_expected_prerelease_version(
            clean_latest_release
        )
        if not expected_version:
            return [], [], None

        logger.debug("Expected prerelease version: %s", expected_version)

        active_dir, history_entries = (
            prerelease_manager.get_latest_active_prerelease_from_history(
                expected_version,
                cache_manager=self.cache_manager,
                github_token=self.config.get("GITHUB_TOKEN"),
                allow_env_token=True,
                force_refresh=force_refresh,
            )
        )
        if active_dir:
            logger.info("Using commit history for prerelease detection")
        else:
            # Fallback: scan repo root for prerelease directories
            try:
                dirs = self.cache_manager.get_repo_directories(
                    "",
                    force_refresh=force_refresh,
                    github_token=self.config.get("GITHUB_TOKEN"),
                    allow_env_token=True,
                )
                matches = prerelease_manager.scan_prerelease_directories(
                    [d for d in dirs if isinstance(d, str)], expected_version
                )
                if matches:
                    # Choose newest by tuple then string
                    matches.sort(
                        key=lambda ident: (
                            version_manager.get_release_tuple(ident) or (),
                            ident,
                        ),
                        reverse=True,
                    )
                    active_dir = f"{FIRMWARE_DIR_PREFIX}{matches[0]}"
            except Exception:
                active_dir = None

        if not active_dir:
            return [], [], None

        selected_patterns = self._get_prerelease_patterns()
        exclude_patterns = self._get_exclude_patterns()
        if selected_patterns:
            logger.debug(
                "Using your extraction patterns for pre-release selection: %s",
                " ".join(selected_patterns),
            )

        prerelease_base_dir = self._get_prerelease_base_dir()
        existing_dirs = [
            d
            for d in os.listdir(prerelease_base_dir)
            if os.path.isdir(os.path.join(prerelease_base_dir, d))
        ]

        successes, failures, any_downloaded = self._download_prerelease_assets(
            active_dir,
            selected_patterns=selected_patterns,
            exclude_patterns=exclude_patterns,
            force_refresh=force_refresh,
        )

        if not any_downloaded and active_dir in existing_dirs and not failures:
            logger.info("Found an existing pre-release, but no new files to download.")

        if any_downloaded or force_refresh:
            prerelease_manager.update_prerelease_tracking(
                latest_release_tag, active_dir, cache_manager=self.cache_manager
            )

        # Emit legacy-style history summary when available
        if history_entries:
            summary = prerelease_manager.summarize_prerelease_history(history_entries)
            logger.info(
                "Prereleases since %s: %d created, %d deleted, %d active",
                clean_latest_release,
                summary["created"],
                summary["deleted"],
                summary["active"],
            )
            active_ids = [
                str(e.get("identifier"))
                for e in history_entries
                if e.get("status") == "active" and e.get("identifier") is not None
            ]
            if active_ids:
                logger.info(
                    "Prerelease commits for %s: %s",
                    expected_version,
                    ", ".join(active_ids[:10]),
                )

        return successes, failures, active_dir

    def handle_prereleases(
        self,
        releases: List[Release],
        recent_commits: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Release]:
        """
        Filter and manage firmware prereleases with enhanced functionality.

        Args:
            releases: List of all releases
            recent_commits: Optional list of recent commits for filtering

        Returns:
            List[Release]: Filtered list of prereleases
        """
        # Check if prereleases are enabled in config
        check_prereleases = self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )

        if not check_prereleases:
            return []

        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()

        # Filter prereleases (GitHub's prerelease flag can be noisy for hash-suffixed tags)
        prereleases = [
            r
            for r in releases
            if r.prerelease
            and not version_manager.HASH_SUFFIX_VERSION_RX.match(
                r.tag_name.lstrip("vV")
            )
        ]

        # Sort by published date (newest first)
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)

        # Apply pattern filtering if configured
        include_patterns = self.config.get("FIRMWARE_PRERELEASE_INCLUDE_PATTERNS", [])
        exclude_patterns = self.config.get("FIRMWARE_PRERELEASE_EXCLUDE_PATTERNS", [])

        if include_patterns or exclude_patterns:
            prerelease_tags = [r.tag_name for r in prereleases]
            filtered_tags = version_manager.filter_prereleases_by_pattern(
                prerelease_tags, include_patterns, exclude_patterns
            )
            prereleases = [r for r in prereleases if r.tag_name in filtered_tags]

        # Further restrict to prereleases that match expected base version
        expected_base = None
        latest_tuple = None
        latest_tag = None
        for candidate in releases:
            candidate_is_hash_suffix = bool(
                version_manager.HASH_SUFFIX_VERSION_RX.match(
                    candidate.tag_name.lstrip("vV")
                )
            )
            candidate_is_stable = (not candidate.prerelease) or candidate_is_hash_suffix
            if not candidate_is_stable:
                continue
            candidate_tuple = version_manager.get_release_tuple(candidate.tag_name)
            if candidate_tuple is None:
                continue
            if latest_tuple is None or candidate_tuple > latest_tuple:
                latest_tuple = candidate_tuple
                latest_tag = candidate.tag_name

        if latest_tag:
            expected_base = version_manager.calculate_expected_prerelease_version(
                latest_tag
            )

        if expected_base:
            filtered_prereleases = []
            for pr in prereleases:
                clean_version = version_manager.extract_clean_version(pr.tag_name)
                if clean_version and clean_version.lstrip("vV").startswith(
                    expected_base
                ):
                    filtered_prereleases.append(pr)
            prereleases = filtered_prereleases

        # Further restrict using commit history cache if available
        if recent_commits and expected_base:
            commit_hashes = []
            for commit in recent_commits:
                sha = commit.get("sha")
                if sha:
                    commit_hashes.append(sha[:7])
            filtered_by_commits = [
                pr
                for pr in prereleases
                if any(hash_part in pr.tag_name for hash_part in commit_hashes)
            ]
            if filtered_by_commits:
                prereleases = filtered_by_commits

        return prereleases

    def get_prerelease_tracking_file(self) -> str:
        """
        Get the path to the firmware prerelease tracking file.

        Returns:
            str: Path to the prerelease tracking file
        """
        return os.path.join(self.download_dir, self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        """
        Update the firmware prerelease tracking information with enhanced metadata.

        Args:
            prerelease_tag: The prerelease tag to record

        Returns:
            bool: True if update succeeded, False otherwise
        """
        tracking_file = self.get_prerelease_tracking_file()

        # Extract metadata from prerelease tag
        version_manager = VersionManager()
        metadata = version_manager.get_prerelease_metadata_from_version(prerelease_tag)

        # Create tracking data with enhanced metadata
        data = {
            "latest_version": prerelease_tag,
            "file_type": "firmware_prerelease",
            "last_updated": self._get_current_iso_timestamp(),
            "base_version": metadata.get("base_version", ""),
            "prerelease_type": metadata.get("prerelease_type", ""),
            "prerelease_number": metadata.get("prerelease_number", ""),
            "commit_hash": metadata.get("commit_hash", ""),
        }

        return self.cache_manager.atomic_write_json(tracking_file, data)

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        """
        Determine if a prerelease should be downloaded.

        Args:
            prerelease_tag: The prerelease tag to check

        Returns:
            bool: True if prerelease should be downloaded, False otherwise
        """
        # Check if prereleases are enabled in config
        if not self.config.get(
            "CHECK_FIRMWARE_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        ):
            return False

        # Check if we have a tracking file
        tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(tracking_file):
            try:
                data = self.cache_manager.read_json(tracking_file) or {}
                current_prerelease = data.get("latest_version")

                if current_prerelease:
                    version_manager = VersionManager()
                    comparison = version_manager.compare_versions(
                        prerelease_tag, current_prerelease
                    )
                    return comparison > 0  # Download if newer
            except Exception:
                return True

        # No tracking file or unreadable; default to download
        return True

    def manage_prerelease_tracking_files(self) -> None:
        """
        Manage firmware prerelease tracking files including cleanup of superseded prereleases.

        This method scans the prerelease tracking directory and cleans up any superseded
        or expired prerelease tracking files.
        """
        tracking_dir = os.path.dirname(self.get_prerelease_tracking_file())
        if not os.path.exists(tracking_dir):
            return

        # Get all prerelease tracking files
        tracking_files = []
        for filename in os.listdir(tracking_dir):
            if filename.startswith("prerelease_") and filename.endswith(".json"):
                tracking_files.append(os.path.join(tracking_dir, filename))

        # Read all existing prerelease tracking data
        existing_prereleases = []
        version_manager = VersionManager()
        prerelease_manager = PrereleaseHistoryManager()

        for file_path in tracking_files:
            try:
                tracking_data = self.cache_manager.read_json(file_path)
                if (
                    tracking_data
                    and "latest_version" in tracking_data
                    and "base_version" in tracking_data
                ):
                    existing_prereleases.append(tracking_data)
            except Exception:
                continue

        # Get current prereleases from GitHub (if available)
        current_releases = self.get_releases(limit=10)
        current_prereleases = self.handle_prereleases(current_releases)

        # Create tracking data for current prereleases
        current_tracking_data = [
            prerelease_manager.create_prerelease_tracking_data(
                prerelease_version=prerelease.tag_name,
                base_version=version_manager.extract_clean_version(prerelease.tag_name)
                or "",
                expiry_hours=24,
                commit_hash=version_manager.get_prerelease_metadata_from_version(
                    prerelease.tag_name
                ).get("commit_hash", ""),
            )
            for prerelease in current_prereleases
        ]

        # Clean up superseded/expired prereleases using shared helper
        prerelease_manager.manage_prerelease_tracking_files(
            tracking_dir, current_tracking_data, self.cache_manager
        )

    def cleanup_superseded_prereleases(self, latest_release_tag: str) -> bool:
        """
        Remove prerelease firmware directories that are superseded by an official release.

        Args:
            latest_release_tag: Latest official release tag

        Returns:
            bool: True if cleanup was performed, False otherwise
        """
        try:
            # Strip the 'v' prefix if present
            clean_release_tag = latest_release_tag.lstrip("vV")
            if not clean_release_tag:
                return False

            # Get version tuple for comparison
            version_manager = VersionManager()
            release_tuple = version_manager.get_release_tuple(clean_release_tag)
            if not release_tuple:
                return False

            # Path to prerelease directory
            prerelease_dir = os.path.join(self.download_dir, "firmware", "prerelease")
            if not os.path.exists(prerelease_dir):
                return False

            cleaned_up = False

            # Check for matching pre-release directories
            for raw_dir_name in os.listdir(prerelease_dir):
                if raw_dir_name.startswith(FIRMWARE_DIR_PREFIX):
                    dir_name = raw_dir_name[len(FIRMWARE_DIR_PREFIX) :]

                    # Extract version from directory name
                    if "." in dir_name:
                        parts = dir_name.split(".")
                        if len(parts) >= 3:
                            try:
                                dir_major, dir_minor, dir_patch = map(int, parts[:3])
                                dir_tuple = (dir_major, dir_minor, dir_patch)

                                # Check if this prerelease is superseded
                                if dir_tuple <= release_tuple:
                                    prerelease_path = os.path.join(
                                        prerelease_dir, raw_dir_name
                                    )
                                    try:
                                        import shutil

                                        shutil.rmtree(prerelease_path)
                                        logger.info(
                                            f"Removed superseded prerelease: {raw_dir_name}"
                                        )
                                        cleaned_up = True
                                    except OSError as e:
                                        logger.error(
                                            f"Error removing superseded prerelease {raw_dir_name}: {e}"
                                        )

                            except ValueError:
                                continue

            return cleaned_up

        except Exception as e:
            logger.error(f"Error cleaning up superseded prereleases: {e}")
            return False

    @staticmethod
    def check_and_download(
        releases,
        cache_dir,
        release_type,
        download_dir,
        versions_to_keep=2,
        extract_patterns=None,
        selected_patterns=None,
        auto_extract=False,
        exclude_patterns=None,
    ):
        """
        Static method to check and download releases (for backward compatibility with tests).

        This method creates a temporary downloader instance and performs the download operation.

        Args:
            releases: List of release data
            cache_dir: Directory for caching
            release_type: Type of release (e.g., "Firmware")
            download_dir: Directory to download files to
            versions_to_keep: Number of versions to keep
            extract_patterns: Patterns for files to extract
            selected_patterns: Patterns for selecting assets to download
            auto_extract: Whether to automatically extract files
            exclude_patterns: Patterns for excluding assets

        Returns:
            Tuple of (downloaded, new_versions, failures)
        """
        # Create a mock config for the downloader
        mock_config = {
            "DOWNLOAD_DIR": download_dir,
            "VERSIONS_TO_KEEP": versions_to_keep,
            "FIRMWARE_VERSIONS_TO_KEEP": versions_to_keep,
            "ANDROID_VERSIONS_TO_KEEP": versions_to_keep,
            "SELECTED_PATTERNS": selected_patterns or [],
            "EXCLUDE_PATTERNS": exclude_patterns or [],
            "EXTRACT_PATTERNS": extract_patterns or [],
            "AUTO_EXTRACT": auto_extract,
            "GITHUB_TOKEN": None,
        }

        # Create downloader instance
        downloader = FirmwareReleaseDownloader(mock_config)
        downloader.download_dir = download_dir

        # Convert releases to the expected format
        processed_releases = []
        for release_data in releases:
            release = Release(
                tag_name=release_data["tag_name"],
                prerelease=release_data.get("prerelease", False),
                published_at=release_data.get("published_at"),
                body=release_data.get("body"),
            )

            # Add assets
            for asset_data in release_data.get("assets", []):
                asset = Asset(
                    name=asset_data["name"],
                    download_url=asset_data.get("browser_download_url"),
                    size=asset_data.get("size"),
                    browser_download_url=asset_data.get("browser_download_url"),
                    content_type=asset_data.get("content_type"),
                )
                release.assets.append(asset)

            processed_releases.append(release)

        # Process downloads
        downloaded = []
        new_versions = []
        failures = []

        for release in processed_releases:
            # Check if this release should be downloaded based on patterns
            should_download = False
            for asset in release.assets:
                if downloader.should_download_release(release.tag_name, asset.name):
                    should_download = True
                    break

            if not should_download:
                logger.info(
                    f"Release {release.tag_name} found, but no assets matched current selection/exclude filters"
                )
                continue

            # Check if this is a new version
            latest_tag = downloader.get_latest_release_tag()
            is_new_version = (
                latest_tag is None
                or downloader.version_manager.compare_versions(
                    release.tag_name, latest_tag
                )
                > 0
            )

            # Download each asset
            release_downloaded = False
            attempted_download = False
            for asset in release.assets:
                if not downloader.should_download_release(release.tag_name, asset.name):
                    continue

                if not getattr(asset, "download_url", None):
                    failures.append(
                        {
                            "release_tag": release.tag_name,
                            "asset": asset.name,
                            "reason": "Missing browser_download_url",
                        }
                    )
                    continue

                attempted_download = True
                download_result = downloader.download_firmware(release, asset)

                if download_result.success:
                    if not download_result.was_skipped:
                        release_downloaded = True

                    # Handle extraction if needed
                    if auto_extract and extract_patterns:
                        extract_result = downloader.extract_firmware(
                            release, asset, extract_patterns, exclude_patterns
                        )
                        if not extract_result.success:
                            logger.warning(
                                f"Extraction failed for {asset.name}: {extract_result.error_message}"
                            )

                    # Update latest release tag if this is the newest version
                    if is_new_version:
                        downloader.update_latest_release_tag(release.tag_name)
                else:
                    failures.append(
                        {
                            "release_tag": release.tag_name,
                            "asset": asset.name,
                            "reason": download_result.error_message
                            or "Download failed",
                        }
                    )

            if release_downloaded:
                downloaded.append(release.tag_name)
            if attempted_download and is_new_version:
                new_versions.append(release.tag_name)

        # Clean up old versions
        downloader.cleanup_old_versions(versions_to_keep)

        return downloaded, new_versions, failures
