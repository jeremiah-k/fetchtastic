"""
Meshtastic client app asset downloader.

Client app assets include Android APKs and Desktop installers published from the
Meshtastic-Android release feed. Storage is intentionally platform-neutral:

- app/<version>/
- app/prerelease/<version>/
"""

import filecmp
import fnmatch
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]

from fetchtastic.client_app_config import normalize_client_app_config
from fetchtastic.client_release_discovery import (
    is_android_asset_name,
    is_android_prerelease_tag,
    is_desktop_asset_name,
    is_release_at_or_above_minimum,
    is_release_prerelease,
)
from fetchtastic.constants import (
    APK_PRERELEASES_DIR_NAME,
    APKS_DIR_NAME,
    APP_DIR_NAME,
    CLIENT_APP_RELEASE_HISTORY_JSON_FILE,
    DEFAULT_APP_VERSIONS_TO_KEEP,
    DEFAULT_CREATE_LATEST_SYMLINKS,
    ERROR_TYPE_FILESYSTEM,
    ERROR_TYPE_NETWORK,
    ERROR_TYPE_VALIDATION,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
    GITHUB_MAX_PER_PAGE,
    LATEST_CLIENT_APP_PRERELEASE_JSON_FILE,
    LATEST_CLIENT_APP_RELEASE_JSON_FILE,
    LATEST_POINTER_NAME,
    MESHTASTIC_CLIENT_APP_RELEASES_URL,
    RELEASE_SCAN_COUNT,
)
from fetchtastic.log_utils import logger
from fetchtastic.utils import (
    coerce_bool,
    expand_apk_selected_patterns,
    matches_selected_patterns,
)

from .base import BaseDownloader
from .cache import CacheManager, parse_iso_datetime_utc
from .files import _safe_rmtree, _sanitize_path_component
from .github_source import GithubReleaseSource, create_asset_from_github_data
from .interfaces import Asset, DownloadResult, Release
from .latest_pointer import update_latest_pointer
from .prerelease_history import PrereleaseHistoryManager
from .release_history import ReleaseHistoryManager
from .version import VersionManager

MIN_ANDROID_TRACKED_VERSION = (2, 7, 0)


def is_client_app_asset_name(asset_name: str) -> bool:
    """Return True when the asset is a supported client app artifact."""
    return is_android_asset_name(asset_name) or is_desktop_asset_name(asset_name)


def is_client_app_prerelease_tag(tag_name: str) -> bool:
    """Return True for client app prerelease tag styles."""
    return (
        "-open" in (tag_name or "").lower()
        or "-closed" in (tag_name or "").lower()
        or "-internal" in (tag_name or "").lower()
    )


def _is_apk_prerelease_by_name(
    tag_name: str, version_manager: VersionManager | None = None
) -> bool:
    """Return whether an Android tag should be treated as a tracked prerelease."""
    if not is_android_prerelease_tag(tag_name):
        return False
    manager = version_manager or VersionManager()
    return is_release_at_or_above_minimum(
        tag_name,
        minimum_version=MIN_ANDROID_TRACKED_VERSION,
        version_manager=manager,
    )


class MeshtasticClientAppDownloader(BaseDownloader):
    """
    Downloader for Meshtastic client app assets.

    This is the primary lifecycle owner for app/<version>/ and
    app/prerelease/<version>/.
    """

    def __init__(self, config: dict[str, Any], cache_manager: CacheManager):
        normalized_config = normalize_client_app_config(config)
        super().__init__(normalized_config, cache_manager)
        self.client_app_releases_url = MESHTASTIC_CLIENT_APP_RELEASES_URL
        self.github_source = GithubReleaseSource(
            releases_url=MESHTASTIC_CLIENT_APP_RELEASES_URL,
            cache_manager=cache_manager,
            config=self.config,
        )
        self.latest_release_file = LATEST_CLIENT_APP_RELEASE_JSON_FILE
        self.latest_prerelease_file = LATEST_CLIENT_APP_PRERELEASE_JSON_FILE
        self.latest_release_path = self.cache_manager.get_cache_file_path(
            self.latest_release_file
        )
        self.release_history_path = self.cache_manager.get_cache_file_path(
            CLIENT_APP_RELEASE_HISTORY_JSON_FILE
        )
        self.release_history_manager = ReleaseHistoryManager(
            self.cache_manager, self.release_history_path
        )

    def _get_app_base_dir(self) -> str:
        return os.path.join(self.download_dir, APP_DIR_NAME)

    def update_latest_pointer_for_release(self, release: Release) -> bool:
        """Best-effort update of app latest pointer for a completed release."""
        if not coerce_bool(
            self.config.get("CREATE_LATEST_SYMLINKS", DEFAULT_CREATE_LATEST_SYMLINKS),
            DEFAULT_CREATE_LATEST_SYMLINKS,
        ):
            return False
        try:
            safe_release = self._get_storage_tag_for_release(release)
            parent_dir = (
                self._ensure_prerelease_base_dir()
                if self._is_client_app_prerelease(release)
                else self._get_app_base_dir()
            )
            return update_latest_pointer(
                parent_dir,
                safe_release,
                LATEST_POINTER_NAME,
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.debug(
                "Skipping client app latest pointer for %s: %s",
                release.tag_name,
                exc,
            )
            return False

    def _ensure_prerelease_base_dir(self) -> str:
        """Return the client app prerelease directory, creating it when needed."""
        app_dir = os.path.join(self.download_dir, APP_DIR_NAME)
        prerelease_dir = os.path.join(app_dir, APK_PRERELEASES_DIR_NAME)
        if os.path.islink(app_dir):
            raise ValueError(f"Refusing symlinked client app dir: {app_dir}")
        if os.path.islink(prerelease_dir):
            raise ValueError(
                f"Refusing symlinked client app prerelease dir: {prerelease_dir}"
            )
        download_root = os.path.realpath(self.download_dir)
        prerelease_real = os.path.realpath(prerelease_dir)
        try:
            if os.path.commonpath([download_root, prerelease_real]) != download_root:
                raise ValueError(
                    f"Refusing client app prerelease dir outside download tree: {prerelease_dir}"
                )
        except ValueError as exc:
            raise ValueError(
                f"Refusing unsafe client app prerelease dir: {prerelease_dir}"
            ) from exc
        os.makedirs(prerelease_dir, exist_ok=True)
        return prerelease_dir

    def _get_prerelease_base_dir(self) -> str:
        """Compatibility alias for older callers."""
        return self._ensure_prerelease_base_dir()

    def has_known_2714_prerelease_version_mismatch(self) -> bool:
        """Compatibility no-op for removed Desktop-specific mismatch tracking."""
        return False

    def get_known_2714_prerelease_mismatch_tags(self) -> list[str]:
        """Compatibility no-op for removed Desktop-specific mismatch tracking."""
        return []

    def _get_legacy_android_base_dir(self) -> str:
        return os.path.join(self.download_dir, APKS_DIR_NAME)

    def _get_legacy_prerelease_base_dir(self) -> str:
        return os.path.join(
            self._get_legacy_android_base_dir(), APK_PRERELEASES_DIR_NAME
        )

    def _get_split_android_base_dir(self) -> str:
        return os.path.join(self.download_dir, APP_DIR_NAME, "android")

    def _get_split_android_prerelease_base_dir(self) -> str:
        return os.path.join(
            self._get_split_android_base_dir(), APK_PRERELEASES_DIR_NAME
        )

    def _get_split_desktop_base_dir(self) -> str:
        return os.path.join(self.download_dir, APP_DIR_NAME, "desktop")

    def _get_split_desktop_prerelease_base_dir(self) -> str:
        return os.path.join(
            self._get_split_desktop_base_dir(), APK_PRERELEASES_DIR_NAME
        )

    def _is_within_download_tree(self, path: str) -> bool:
        try:
            download_root = os.path.realpath(self.download_dir)
            candidate = os.path.realpath(path)
            return os.path.commonpath([download_root, candidate]) == download_root
        except ValueError:
            return False

    def _is_safe_managed_dir(self, path: str) -> bool:
        return (
            os.path.isdir(path)
            and not os.path.islink(path)
            and self._is_within_download_tree(path)
        )

    def _is_duplicate_migration_file(
        self, source_path: str, destination_path: str
    ) -> bool:
        try:
            return (
                os.path.isfile(source_path)
                and os.path.isfile(destination_path)
                and not os.path.islink(destination_path)
                and filecmp.cmp(source_path, destination_path, shallow=False)
            )
        except OSError:
            return False

    def _move_legacy_path(self, source_path: str, destination_path: str) -> bool:
        if not os.path.exists(source_path):
            return False
        if os.path.islink(source_path):
            logger.debug(
                "Skipping client app migration because source is symlinked: %s",
                source_path,
            )
            return False

        abs_destination = os.path.abspath(destination_path)
        if os.path.islink(abs_destination):
            logger.debug(
                "Skipping client app migration because destination is symlinked: %s",
                abs_destination,
            )
            return False
        if not self._is_within_download_tree(abs_destination):
            logger.warning(
                "Skipping client app migration because destination is outside download tree: %s",
                abs_destination,
            )
            return False
        try:
            dest_parent = os.path.dirname(abs_destination)
            rel = os.path.relpath(dest_parent, self.download_dir)
            if rel.startswith(".."):
                raise ValueError("destination parent escapes download tree")
            check_dir = self.download_dir
            for part in rel.split(os.sep):
                if part == ".":
                    continue
                check_dir = os.path.join(check_dir, part)
                if os.path.islink(check_dir):
                    logger.warning(
                        "Skipping client app migration because ancestor is symlinked: %s",
                        check_dir,
                    )
                    return False
        except (OSError, ValueError):
            logger.warning(
                "Skipping client app migration because destination ancestor check failed: %s",
                abs_destination,
            )
            return False

        if os.path.isfile(source_path):
            if os.path.exists(abs_destination):
                if self._is_duplicate_migration_file(source_path, abs_destination):
                    try:
                        os.remove(source_path)
                        logger.debug(
                            "Removed duplicate client app migration file already present at destination: %s",
                            source_path,
                        )
                        return True
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove duplicate client app migration file %s: %s",
                            source_path,
                            exc,
                        )
                        return False
                logger.debug(
                    "Skipping client app migration because destination file exists: %s",
                    abs_destination,
                )
                return False
            try:
                os.makedirs(os.path.dirname(abs_destination), exist_ok=True)
                shutil.move(source_path, abs_destination)
                logger.info(
                    "Migrated client app path %s -> %s",
                    source_path,
                    abs_destination,
                )
                return True
            except OSError as exc:
                logger.warning(
                    "Failed to migrate client app path %s -> %s: %s",
                    source_path,
                    abs_destination,
                    exc,
                )
                return False

        if not os.path.isdir(source_path):
            return False

        if os.path.exists(abs_destination) and not os.path.isdir(abs_destination):
            logger.warning(
                "Skipping client app migration because destination is not a directory: %s",
                abs_destination,
            )
            return False

        try:
            os.makedirs(abs_destination, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Failed to create client app migration destination %s: %s",
                abs_destination,
                exc,
            )
            return False

        moved_any = False
        try:
            for entry in os.listdir(source_path):
                src_entry = os.path.join(source_path, entry)
                dst_entry = os.path.join(abs_destination, entry)
                if os.path.islink(src_entry):
                    logger.warning(
                        "Skipping symlink during client app migration merge: %s",
                        src_entry,
                    )
                    continue
                if os.path.exists(dst_entry):
                    if self._is_duplicate_migration_file(src_entry, dst_entry):
                        try:
                            os.remove(src_entry)
                            logger.debug(
                                "Removed duplicate client app migration file already present at destination: %s",
                                src_entry,
                            )
                            moved_any = True
                        except OSError as exc:
                            logger.warning(
                                "Failed to remove duplicate client app migration file %s: %s",
                                src_entry,
                                exc,
                            )
                        continue
                    logger.debug(
                        "Skipping client app migration file because destination exists: %s",
                        dst_entry,
                    )
                    continue
                shutil.move(src_entry, dst_entry)
                logger.info(
                    "Migrated client app file %s -> %s",
                    src_entry,
                    dst_entry,
                )
                moved_any = True
        except OSError as exc:
            logger.warning(
                "Failed to merge client app directory %s -> %s: %s",
                source_path,
                abs_destination,
                exc,
            )

        if moved_any:
            try:
                remaining = os.listdir(source_path)
                if not remaining:
                    os.rmdir(source_path)
                else:
                    logger.debug(
                        "Keeping source directory after partial merge: %s (%d entries remain)",
                        source_path,
                        len(remaining),
                    )
            except OSError:
                pass

        return moved_any

    def migrate_legacy_layout(self) -> None:
        """
        Migrate legacy app layouts into the unified app tree.

        Sources:
        - apks/<version>
        - apks/prerelease/<version>
        - app/android/<version>
        - app/android/prerelease/<version>
        - app/desktop/<version>
        - app/desktop/prerelease/<version>
        """
        app_dir = self._get_app_base_dir()
        prerelease_dir = os.path.join(app_dir, APK_PRERELEASES_DIR_NAME)
        if os.path.islink(app_dir) or not self._is_within_download_tree(app_dir):
            logger.warning("Skipping client app migration because app root is unsafe")
            return
        try:
            os.makedirs(app_dir, exist_ok=True)
        except OSError as exc:
            logger.warning("Skipping client app migration: %s", exc)
            return

        stable_sources = (
            self._get_legacy_android_base_dir(),
            self._get_split_android_base_dir(),
            self._get_split_desktop_base_dir(),
        )
        prerelease_sources = (
            self._get_legacy_prerelease_base_dir(),
            self._get_split_android_prerelease_base_dir(),
            self._get_split_desktop_prerelease_base_dir(),
        )

        for source_dir in stable_sources:
            self._migrate_entries(source_dir, app_dir)
        try:
            self._ensure_prerelease_base_dir()
        except ValueError as exc:
            logger.warning("Skipping client app prerelease migration: %s", exc)
            return
        for source_dir in prerelease_sources:
            self._migrate_entries(source_dir, prerelease_dir)

        for directory in (
            *prerelease_sources,
            *stable_sources,
        ):
            try:
                os.rmdir(directory)
            except OSError:
                continue

    def migrate_split_layout(self) -> None:
        """Compatibility alias used by older Desktop paths."""
        self.migrate_legacy_layout()

    def _migrate_entries(self, source_dir: str, destination_dir: str) -> None:
        if not self._is_safe_managed_dir(source_dir):
            return
        try:
            with os.scandir(source_dir) as it:
                entries = list(it)
        except OSError as exc:
            logger.warning(
                "Unable to scan client app migration dir %s: %s", source_dir, exc
            )
            return

        for entry in entries:
            if entry.is_symlink():
                logger.warning(
                    "Skipping symlink during client app migration: %s", entry.path
                )
                continue
            if entry.name == APK_PRERELEASES_DIR_NAME:
                continue
            self._move_legacy_path(
                entry.path, os.path.join(destination_dir, entry.name)
            )

    def get_target_path_for_release(
        self,
        release_tag: str,
        file_name: str,
        is_prerelease: bool | None = None,
        release: Release | None = None,
    ) -> str:
        safe_release = self._sanitize_required(release_tag, "release tag")
        safe_name = self._sanitize_required(file_name, "file name")
        if release is not None:
            safe_release = self._get_storage_tag_for_release(release)
            is_prerelease = self._is_client_app_prerelease(release)
        elif is_prerelease is None:
            is_prerelease = (
                is_client_app_prerelease_tag(release_tag)
                or self.version_manager.is_prerelease_version(release_tag) is True
                or _is_apk_prerelease_by_name(release_tag, self.version_manager)
            )

        version_dir = self._resolve_release_dir(
            safe_release,
            is_prerelease=bool(is_prerelease),
            create_if_missing=True,
        )
        return os.path.join(version_dir, safe_name)

    def _resolve_release_dir(
        self,
        safe_release: str,
        *,
        is_prerelease: bool,
        create_if_missing: bool,
    ) -> str:
        preferred_base_dir = (
            os.path.join(self.download_dir, APP_DIR_NAME, APK_PRERELEASES_DIR_NAME)
            if is_prerelease
            else os.path.join(self.download_dir, APP_DIR_NAME)
        )
        legacy_base_dirs = (
            (
                self._get_legacy_prerelease_base_dir(),
                self._get_split_android_prerelease_base_dir(),
                self._get_split_desktop_prerelease_base_dir(),
            )
            if is_prerelease
            else (
                self._get_legacy_android_base_dir(),
                self._get_split_android_base_dir(),
                self._get_split_desktop_base_dir(),
            )
        )
        preferred_release_dir = os.path.join(preferred_base_dir, safe_release)
        if os.path.islink(preferred_base_dir) or os.path.islink(preferred_release_dir):
            raise ValueError(
                f"Refusing symlinked client app release dir: {preferred_release_dir}"
            )
        if os.path.exists(preferred_release_dir) and not self._is_safe_managed_dir(
            preferred_release_dir
        ):
            raise ValueError(
                f"Refusing unsafe client app release dir: {preferred_release_dir}"
            )
        if self._is_safe_managed_dir(preferred_release_dir):
            return preferred_release_dir
        for legacy_base_dir in legacy_base_dirs:
            legacy_release_dir = os.path.join(legacy_base_dir, safe_release)
            if os.path.islink(legacy_release_dir):
                logger.warning(
                    "Ignoring symlinked client app legacy dir: %s", legacy_release_dir
                )
                continue
            if self._is_safe_managed_dir(legacy_release_dir):
                if self._move_legacy_path(legacy_release_dir, preferred_release_dir):
                    return preferred_release_dir
                if self._is_safe_managed_dir(preferred_release_dir):
                    return preferred_release_dir
        if create_if_missing:
            if not self._is_within_download_tree(preferred_release_dir):
                raise ValueError(
                    f"Refusing unsafe client app release dir: {preferred_release_dir}"
                )
            os.makedirs(preferred_release_dir, exist_ok=True)
            if not self._is_safe_managed_dir(preferred_release_dir):
                raise ValueError(
                    f"Refusing unsafe client app release dir: {preferred_release_dir}"
                )
        return preferred_release_dir

    def _is_client_app_prerelease(self, release: Release) -> bool:
        return (
            release.prerelease
            or is_client_app_prerelease_tag(release.tag_name)
            or self.version_manager.is_prerelease_version(release.tag_name) is True
            or _is_apk_prerelease_by_name(release.tag_name, self.version_manager)
        )

    def _is_android_prerelease(self, release: Release) -> bool:
        """Compatibility alias."""
        return self._is_client_app_prerelease(release)

    def _is_desktop_prerelease(self, release: Release) -> bool:
        """Compatibility alias."""
        return self._is_client_app_prerelease(release)

    def _get_storage_tag_for_release(self, release: Release) -> str:
        return self._sanitize_required(release.tag_name, "release tag")

    def update_release_history(
        self, releases: list[Release], *, log_summary: bool = True
    ) -> dict[str, Any] | None:
        if not releases:
            return None
        stable_releases = [r for r in releases if not self._is_client_app_prerelease(r)]
        if not stable_releases:
            return None
        history = self.release_history_manager.update_release_history(stable_releases)
        if log_summary:
            self.release_history_manager.log_release_status_summary(
                history, label="Client app"
            )
        return history

    def format_release_log_suffix(self, release: Release) -> str:
        label = self.release_history_manager.format_release_label(
            release, include_channel=False, include_status=True
        )
        if not label.startswith(release.tag_name):
            return ""
        return label[len(release.tag_name) :]

    def ensure_release_notes(self, release: Release) -> str | None:
        safe_release = _sanitize_path_component(release.tag_name)
        if safe_release is None:
            logger.warning(
                "Skipping release notes for unsafe client app tag: %s",
                release.tag_name,
            )
            return None
        try:
            release_dir = self._resolve_release_dir(
                self._get_storage_tag_for_release(release),
                is_prerelease=self._is_client_app_prerelease(release),
                create_if_missing=True,
            )
        except ValueError:
            return None
        return self._write_release_notes(
            release_dir=release_dir,
            release_tag=release.tag_name,
            body=release.body,
            base_dir=os.path.dirname(release_dir),
        )

    def _is_asset_complete_for_target(self, target_path: str, asset: Asset) -> bool:
        if os.path.islink(target_path):
            return False
        if not os.path.exists(target_path):
            return False
        if (
            asset.size is not None
            and self.file_operations.get_file_size(target_path) != asset.size
        ):
            return False
        if not self.verify(target_path):
            return False
        if target_path.lower().endswith(".zip") and not self._is_zip_intact(
            target_path
        ):
            return False
        return True

    def get_releases(self, limit: int | None = None) -> list[Release]:
        try:
            max_scan = GITHUB_MAX_PER_PAGE
            raw_keep = self.config.get(
                "APP_VERSIONS_TO_KEEP", DEFAULT_APP_VERSIONS_TO_KEEP
            )
            try:
                min_stable_releases = max(0, int(raw_keep))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid APP_VERSIONS_TO_KEEP value %r, using default %s",
                    raw_keep,
                    DEFAULT_APP_VERSIONS_TO_KEEP,
                )
                min_stable_releases = int(DEFAULT_APP_VERSIONS_TO_KEEP)
            scan_count = min(max_scan, max(min_stable_releases * 2, RELEASE_SCAN_COUNT))
            if limit is not None:
                if limit <= 0:
                    return []
                scan_count = min(max_scan, limit)

            while True:
                releases_data = self.github_source.fetch_raw_releases_data(
                    {"per_page": scan_count}
                )
                if releases_data is None:
                    return []
                releases: list[Release] = []
                stable_count = 0
                for release_data in releases_data:
                    if not isinstance(release_data, dict):
                        continue
                    assets_data = release_data.get("assets")
                    if not isinstance(assets_data, list) or not assets_data:
                        continue
                    tag_name = release_data.get("tag_name", "")
                    if not isinstance(tag_name, str) or not tag_name.strip():
                        continue
                    if not _is_supported_client_app_release(
                        tag_name, version_manager=self.version_manager
                    ):
                        continue
                    release = Release(
                        tag_name=tag_name,
                        prerelease=_is_client_app_prerelease_payload(release_data),
                        published_at=release_data.get("published_at"),
                        name=release_data.get("name"),
                        body=release_data.get("body"),
                    )
                    for asset_data in assets_data:
                        asset = create_asset_from_github_data(
                            asset_data,
                            tag_name,
                            asset_label="Client app asset",
                        )
                        if asset is not None and is_client_app_asset_name(asset.name):
                            release.assets.append(asset)
                    if not release.assets:
                        continue
                    releases.append(release)
                    if not self._is_client_app_prerelease(release):
                        stable_count += 1
                    if limit is not None and len(releases) >= limit:
                        break
                if limit is not None:
                    return releases
                if (
                    stable_count >= min_stable_releases
                    or len(releases_data) < scan_count
                ):
                    return releases
                if scan_count >= max_scan:
                    return releases
                scan_count = min(max_scan, scan_count * 2)
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            TypeError,
        ) as exc:
            logger.exception("Error fetching client app releases: %s", exc)
            return []

    def get_assets(self, release: Release) -> list[Asset]:
        return [
            asset
            for asset in (release.assets or [])
            if is_client_app_asset_name(asset.name)
        ]

    def get_download_url(self, asset: Asset) -> str:
        return asset.download_url

    def _is_excluded(self, asset_name: str) -> bool:
        exclude = self._get_exclude_patterns()
        if not exclude:
            return False
        return any(fnmatch.fnmatch(asset_name.lower(), pat.lower()) for pat in exclude)

    def should_download_asset(self, asset_name: str) -> bool:
        raw_selected = self.config.get("SELECTED_APP_ASSETS")
        if raw_selected is None:
            return False
        if raw_selected == ["*"]:
            if not (
                is_android_asset_name(asset_name) or is_desktop_asset_name(asset_name)
            ):
                return False
            if self._is_excluded(asset_name):
                return False
            return True
        selected = expand_apk_selected_patterns(raw_selected)
        if not selected:
            return False
        if self._is_excluded(asset_name):
            return False
        return matches_selected_patterns(asset_name, selected)

    def download_app(self, release: Release, asset: Asset) -> DownloadResult:
        target_path: str | None = None
        file_type = (
            FILE_TYPE_CLIENT_APP_PRERELEASE
            if self._is_client_app_prerelease(release)
            else FILE_TYPE_CLIENT_APP
        )
        try:
            target_path = self.get_target_path_for_release(
                release.tag_name,
                asset.name,
                is_prerelease=release.prerelease,
                release=release,
            )
            if os.path.islink(target_path):
                raise ValueError(f"Refusing symlinked client app target: {target_path}")
            if self._is_asset_complete_for_target(target_path, asset):
                logger.debug(
                    "Client app asset %s (release %s) already exists and is complete",
                    asset.name,
                    release.tag_name,
                )
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type=file_type,
                    was_skipped=True,
                )
            success = self.download(asset.download_url, target_path)
            if success and self._is_asset_complete_for_target(target_path, asset):
                logger.info("Successfully downloaded and verified %s", asset.name)
                return self.create_download_result(
                    success=True,
                    release_tag=release.tag_name,
                    file_path=target_path,
                    download_url=asset.download_url,
                    file_size=asset.size,
                    file_type=file_type,
                )
            if success:
                self.cleanup_file(target_path)
                message = "Validation failed"
                error_type = ERROR_TYPE_VALIDATION
            else:
                message = "download_file_with_retry returned False"
                error_type = ERROR_TYPE_NETWORK
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=target_path,
                error_message=message,
                download_url=asset.download_url,
                file_size=asset.size,
                file_type=file_type,
                is_retryable=True,
                error_type=error_type,
            )
        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.exception(
                "Error downloading client app asset %s: %s", asset.name, exc
            )
            safe_path = target_path or os.path.join(self.download_dir, APP_DIR_NAME)
            if isinstance(exc, requests.RequestException):
                error_type = ERROR_TYPE_NETWORK
                is_retryable = True
            elif isinstance(exc, OSError):
                error_type = ERROR_TYPE_FILESYSTEM
                is_retryable = False
            else:
                error_type = ERROR_TYPE_VALIDATION
                is_retryable = False
            return self.create_download_result(
                success=False,
                release_tag=release.tag_name,
                file_path=str(Path(safe_path)),
                error_message=str(exc),
                download_url=getattr(asset, "download_url", None),
                file_size=getattr(asset, "size", None),
                file_type=file_type,
                is_retryable=is_retryable,
                error_type=error_type,
            )

    def download_apk(self, release: Release, asset: Asset) -> DownloadResult:
        """Compatibility alias."""
        return self.download_app(release, asset)

    def download_desktop(self, release: Release, asset: Asset) -> DownloadResult:
        """Compatibility alias."""
        return self.download_app(release, asset)

    def is_release_complete(self, release: Release) -> bool:
        try:
            version_dir = self._resolve_release_dir(
                self._get_storage_tag_for_release(release),
                is_prerelease=self._is_client_app_prerelease(release),
                create_if_missing=False,
            )
        except ValueError:
            return False
        if not os.path.isdir(version_dir):
            return False
        expected_assets = [
            asset
            for asset in self.get_assets(release)
            if self.should_download_asset(asset.name)
        ]
        if not expected_assets:
            return False
        for asset in expected_assets:
            asset_path = os.path.join(version_dir, asset.name)
            try:
                if not self._is_asset_complete_for_target(asset_path, asset):
                    return False
            except OSError:
                return False
        return True

    def cleanup_old_versions(
        self,
        keep_limit: int,
        cached_releases: list[Release] | None = None,
        keep_last_beta: bool = False,
    ) -> None:
        try:
            releases = cached_releases or self.get_releases()
            if not releases:
                return
            self.cleanup_prerelease_directories(
                cached_releases=releases,
                keep_limit_override=keep_limit,
                keep_last_beta=keep_last_beta,
            )
        except (requests.RequestException, OSError, ValueError, TypeError) as exc:
            logger.error("Error cleaning up old client app versions: %s", exc)

    def cleanup_prerelease_directories(
        self,
        cached_releases: list[Release] | None = None,
        keep_limit_override: int | None = None,
        keep_last_beta: bool = False,
    ) -> None:
        if not cached_releases:
            return
        app_dir = os.path.join(self.download_dir, APP_DIR_NAME)
        if not self._is_safe_managed_dir(app_dir):
            return
        raw_keep = (
            keep_limit_override
            if keep_limit_override is not None
            else self.config.get("APP_VERSIONS_TO_KEEP", DEFAULT_APP_VERSIONS_TO_KEEP)
        )
        try:
            keep_limit = max(0, int(raw_keep))
        except (TypeError, ValueError):
            keep_limit = int(DEFAULT_APP_VERSIONS_TO_KEEP)

        def _stable_sort_key(release: Release) -> tuple[Any, ...]:
            release_tuple = self.version_manager.get_release_tuple(release.tag_name)
            published_dt = parse_iso_datetime_utc(release.published_at)
            published_ts = published_dt.timestamp() if published_dt else 0
            if release_tuple:
                max_components = 6
                normalized = tuple(release_tuple[:max_components]) + (0,) * (
                    max_components - len(release_tuple)
                )
                return (1, *normalized, published_ts)
            return (0, 0, 0, 0, 0, 0, published_ts)

        stable_releases = sorted(
            [r for r in cached_releases if not self._is_client_app_prerelease(r)],
            key=_stable_sort_key,
            reverse=True,
        )
        if not stable_releases:
            return
        prerelease_releases = self.handle_prereleases(cached_releases)

        def _safe_tags(releases: list[Release], label: str) -> set[str]:
            tags: set[str] = set()
            for release in releases:
                safe = _sanitize_path_component(release.tag_name)
                if safe is None:
                    logger.warning(
                        "Skipping unsafe client app %s tag during cleanup: %s",
                        label,
                        release.tag_name,
                    )
                    continue
                tags.add(safe)
            return tags

        expected_stable = _safe_tags(stable_releases[:keep_limit], "release")
        expected_prerelease = _safe_tags(prerelease_releases, "prerelease")
        if keep_last_beta:
            latest_prerelease = next(
                (
                    release
                    for release in sorted(
                        cached_releases,
                        key=lambda item: item.published_at or "",
                        reverse=True,
                    )
                    if self._is_client_app_prerelease(release)
                ),
                None,
            )
            if latest_prerelease:
                expected_prerelease.update(
                    _safe_tags([latest_prerelease], "prerelease")
                )
        self._remove_unexpected_entries(
            app_dir, expected_stable | {APK_PRERELEASES_DIR_NAME}
        )
        prerelease_dir = os.path.join(app_dir, APK_PRERELEASES_DIR_NAME)
        if self._is_safe_managed_dir(prerelease_dir):
            self._remove_unexpected_entries(prerelease_dir, expected_prerelease)

    def _remove_unexpected_entries(self, base_dir: str, allowed: set[str]) -> None:
        try:
            with os.scandir(base_dir) as it:
                entries = list(it)
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.name in allowed:
                continue
            if entry.name == LATEST_POINTER_NAME:
                if entry.is_symlink():
                    continue
                logger.debug(
                    "Preserving non-symlink latest entry that may block latest pointer creation: %s",
                    entry.path,
                )
                continue
            if entry.is_symlink():
                logger.warning("Skipping symlink in client app cleanup: %s", entry.name)
                continue
            is_recognized_version = (
                entry.is_dir()
                and self.version_manager.get_release_tuple(entry.name) is not None
            )
            if not is_recognized_version:
                logger.debug(
                    "Skipping non-version entry in client app cleanup: %s",
                    entry.name,
                )
                continue
            logger.info("Removing stale client app version dir: %s", entry.name)
            _safe_rmtree(entry.path, base_dir, entry.name)

    def get_latest_release_tag(self) -> str | None:
        if os.path.exists(self.latest_release_path):
            try:
                data = self.cache_manager.read_json(self.latest_release_path) or {}
                value = data.get("latest_version")
                return value if isinstance(value, str) and value else None
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                pass
        return None

    def update_latest_release_tag(self, release_tag: str) -> bool:
        return self.cache_manager.atomic_write_json(
            self.latest_release_path,
            {
                "latest_version": release_tag,
                "file_type": FILE_TYPE_CLIENT_APP,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
        )

    def handle_prereleases(
        self,
        releases: list[Release],
        recent_commits: list[dict[str, Any]] | None = None,
    ) -> list[Release]:
        check_prereleases = self.config.get(
            "CHECK_APP_PRERELEASES", self.config.get("CHECK_PRERELEASES", False)
        )
        if not check_prereleases:
            return []
        prereleases = [r for r in releases if self._is_client_app_prerelease(r)]
        prereleases.sort(key=lambda r: r.published_at or "", reverse=True)
        latest_release = next(
            (r for r in releases if not self._is_client_app_prerelease(r)), None
        )
        expected_base = (
            self.version_manager.calculate_expected_prerelease_version(
                latest_release.tag_name
            )
            if latest_release
            else None
        )
        if expected_base:
            expected_tuple = self.version_manager.get_release_tuple(expected_base)
            filtered = []
            for prerelease in prereleases:
                clean = self.version_manager.extract_clean_version(prerelease.tag_name)
                clean_tuple = self.version_manager.get_release_tuple(clean)
                if not clean or clean_tuple is None or clean_tuple == expected_tuple:
                    filtered.append(prerelease)
            prereleases = filtered
        if recent_commits and expected_base:
            hashes = [
                commit.get("sha", "")[:7]
                for commit in recent_commits
                if commit.get("sha")
            ]
            by_commit = [
                prerelease
                for prerelease in prereleases
                if any(hash_part in prerelease.tag_name for hash_part in hashes)
            ]
            if by_commit:
                prereleases = by_commit
        return prereleases

    def get_latest_prerelease_tag(
        self, releases: list[Release] | None = None
    ) -> str | None:
        available = releases or self.get_releases()
        if not available:
            return None
        sorted_releases = sorted(
            available,
            key=lambda release: release.published_at or "",
            reverse=True,
        )
        latest_stable = next(
            (
                release
                for release in sorted_releases
                if not self._is_client_app_prerelease(release)
            ),
            None,
        )
        latest_stable_tuple = (
            self.version_manager.get_release_tuple(latest_stable.tag_name)
            if latest_stable
            else None
        )
        for release in sorted_releases:
            if not self._is_client_app_prerelease(release):
                continue
            prerelease_tuple = self.version_manager.get_release_tuple(release.tag_name)
            if (
                latest_stable_tuple is None
                or prerelease_tuple is None
                or prerelease_tuple > latest_stable_tuple
            ):
                return release.tag_name
        return None

    def get_prerelease_tracking_file(self) -> str:
        return self.cache_manager.get_cache_file_path(self.latest_prerelease_file)

    def update_prerelease_tracking(self, prerelease_tag: str) -> bool:
        metadata = self.version_manager.get_prerelease_metadata_from_version(
            prerelease_tag
        )
        return self.cache_manager.atomic_write_json(
            self.get_prerelease_tracking_file(),
            {
                "latest_version": prerelease_tag,
                "file_type": FILE_TYPE_CLIENT_APP_PRERELEASE,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "base_version": metadata.get("base_version", ""),
                "prerelease_type": metadata.get("prerelease_type", ""),
                "prerelease_number": metadata.get("prerelease_number", ""),
                "commit_hash": metadata.get("commit_hash", ""),
            },
        )

    def should_download_prerelease(self, prerelease_tag: str) -> bool:
        if not self.config.get("CHECK_APP_PRERELEASES", False):
            return False
        tracking_file = self.get_prerelease_tracking_file()
        if os.path.exists(tracking_file):
            try:
                data = self.cache_manager.read_json(tracking_file) or {}
                current = data.get("latest_version")
                if current:
                    return (
                        self.version_manager.compare_versions(prerelease_tag, current)
                        > 0
                    )
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                return True
        return True

    def manage_prerelease_tracking_files(
        self, cached_releases: list[Release] | None = None
    ) -> None:
        if not self.config.get("CHECK_APP_PRERELEASES", False):
            return
        tracking_dir = os.path.dirname(self.get_prerelease_tracking_file())
        if not os.path.exists(tracking_dir):
            return
        current_releases = cached_releases or self.get_releases(limit=10)
        prerelease_manager = PrereleaseHistoryManager()
        current_tracking_data = [
            prerelease_manager.create_prerelease_tracking_data(
                prerelease_version=release.tag_name,
                base_version=self.version_manager.extract_clean_version(
                    release.tag_name
                )
                or "",
                expiry_hours=24,
                commit_hash=self.version_manager.get_prerelease_metadata_from_version(
                    release.tag_name
                ).get("commit_hash", ""),
            )
            for release in self.handle_prereleases(current_releases)
        ]
        prerelease_manager.manage_prerelease_tracking_files(
            tracking_dir, current_tracking_data, self.cache_manager
        )

    def validate_extraction_patterns(
        self, patterns: list[str], exclude_patterns: list[str]
    ) -> bool:
        """No-op: client app assets are downloaded as standalone files and do not support extraction."""
        del patterns, exclude_patterns
        return False

    def check_extraction_needed(
        self,
        file_path: str,
        extract_dir: str,
        patterns: list[str],
        exclude_patterns: list[str],
    ) -> bool:
        """No-op: client app assets are downloaded as standalone files and do not support extraction."""
        del file_path, extract_dir, patterns, exclude_patterns
        return False


def _is_supported_client_app_release(
    tag_name: str, version_manager: VersionManager | None = None
) -> bool:
    manager = version_manager or VersionManager()
    version_tuple = manager.get_release_tuple(tag_name)
    if not version_tuple:
        return True
    max_len = max(len(version_tuple), len(MIN_ANDROID_TRACKED_VERSION))
    padded_version = version_tuple + (0,) * (max_len - len(version_tuple))
    padded_minimum = MIN_ANDROID_TRACKED_VERSION + (0,) * (
        max_len - len(MIN_ANDROID_TRACKED_VERSION)
    )
    return padded_version >= padded_minimum


def _is_client_app_prerelease_payload(release: dict[str, Any]) -> bool:
    tag_name = (release or {}).get("tag_name", "")
    return is_release_prerelease(
        release,
        tag_prerelease_matcher=is_client_app_prerelease_tag,
    ) or _is_apk_prerelease_by_name(tag_name)
