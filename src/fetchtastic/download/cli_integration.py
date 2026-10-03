"""
CLI reporting for the download pipeline

Run the artifact pipeline and report firmware and shared client app results.
"""

import os
import re
import time
import urllib.parse
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Tuple,
    cast,
)

if TYPE_CHECKING:
    from .version import VersionManager

import requests  # type: ignore[import-untyped]

from fetchtastic.client_app_config import client_app_downloads_enabled
from fetchtastic.constants import (
    ANDROID_FILE_TYPES,
    APP_DIR_NAME,
    APP_PRERELEASES_DIR_NAME,
    APP_SNAPSHOTS_DIR_NAME,
    CLIENT_APP_FILE_TYPES,
    DEFAULT_CHECK_APP_PRERELEASES,
    DEFAULT_CHECK_APP_SNAPSHOTS,
    DEFAULT_CHECK_FIRMWARE_NIGHTLIES,
    DEFAULT_NOTIFY_ON_FIRMWARE_NIGHTLIES,
    DEFAULT_NOTIFY_ON_SNAPSHOTS,
    DESKTOP_FILE_TYPES,
    FILE_TYPE_ANDROID,
    FILE_TYPE_ANDROID_PRERELEASE,
    FILE_TYPE_APP_SNAPSHOT,
    FILE_TYPE_CLIENT_APP,
    FILE_TYPE_CLIENT_APP_PRERELEASE,
    FILE_TYPE_DESKTOP,
    FILE_TYPE_DESKTOP_PRERELEASE,
    FILE_TYPE_FIRMWARE,
    FILE_TYPE_FIRMWARE_MANIFEST,
    FILE_TYPE_FIRMWARE_NIGHTLY,
    FILE_TYPE_FIRMWARE_PRERELEASE,
    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
    FILE_TYPE_REPOSITORY,
    FIRMWARE_DIR_NAME,
    FIRMWARE_DIR_PREFIX,
    FIRMWARE_FILE_TYPES,
    FIRMWARE_NIGHTLIES_DIR_NAME,
    FIRMWARE_PRERELEASES_DIR_NAME,
    REPO_DOWNLOADS_DIR,
    SNAPSHOT_VERSION_CODE_PATTERN,
    STORAGE_CHANNEL_SUFFIXES,
    NightlyRunState,
)
from fetchtastic.log_utils import logger
from fetchtastic.notifications import (
    send_download_completion_notification,
    send_new_releases_available_notification,
    send_up_to_date_notification,
)
from fetchtastic.utils import (
    coerce_bool,
    format_api_summary,
    get_api_request_summary,
    get_effective_github_token,
)

from .client_app import MeshtasticClientAppDownloader
from .firmware import FirmwareReleaseDownloader
from .orchestrator import DownloadOrchestrator

_SNAPSHOT_VC_RE = re.compile(SNAPSHOT_VERSION_CODE_PATTERN)


class DownloadReport(NamedTuple):
    """Release-level results shared by every client app artifact type."""

    downloaded_firmwares: List[str]
    new_firmware_versions: List[str]
    downloaded_client_apps: List[str]
    new_client_app_versions: List[str]
    downloaded_firmware_prereleases: List[str]
    downloaded_client_app_prereleases: List[str]
    failed_downloads: List[Dict[str, Any]]
    latest_firmware_version: str
    latest_client_app_version: str

    @classmethod
    def empty(cls) -> "DownloadReport":
        return cls([], [], [], [], [], [], [], "", "")


class DownloadCLIIntegration:
    """
    Connects the CLI to the artifact download pipeline.

    This class provides:
    - Shared release reports for every installer format
    - Download pipeline coordination
    - Error handling and reporting
    - Progress reporting
    """

    def __init__(self) -> None:
        """
        Initialize a DownloadCLIIntegration instance and set initial internal state.

        Sets attributes used to connect CLI to download subsystem:
        - orchestrator: Orchestrator instance or None until initialized.
        - client_app_downloader: Client app downloader instance or None until initialized.
        - firmware_downloader: Firmware downloader instance or None until initialized.
        - config: Configuration mapping or None until provided.
        """
        self.orchestrator: Optional[DownloadOrchestrator] = None
        self.client_app_downloader: Optional[MeshtasticClientAppDownloader] = None
        self.firmware_downloader: Optional[FirmwareReleaseDownloader] = None
        self.config: Optional[Dict[str, Any]] = None
        self.download_run_failed = False

    def _initialize_components(self, config: Dict[str, Any]) -> None:
        """
        Set up the DownloadOrchestrator and expose its downloaders on the integration instance.

        Stores the provided configuration on self, constructs a DownloadOrchestrator using that configuration, and assigns the orchestrator's client_app_downloader and firmware_downloader to instance attributes for shared state and caches.

        Parameters:
            config (Dict[str, Any]): Configuration used to initialize the orchestrator and downloaders.
        """
        self.config = config
        self.orchestrator = DownloadOrchestrator(config)
        # Reuse the orchestrator's downloaders so state and caches stay unified
        self.client_app_downloader = self.orchestrator.client_app_downloader
        self.firmware_downloader = self.orchestrator.firmware_downloader

    def run_download(
        self, config: Dict[str, Any], force_refresh: bool = False
    ) -> DownloadReport:
        """Run downloads and return one report for firmware and client apps."""
        self.download_run_failed = False
        try:
            self._initialize_components(config)
            if self.orchestrator is None:
                raise RuntimeError("Failed to initialize download orchestrator")
            orchestrator = self.orchestrator
            tracked_versions = self._get_tracked_client_app_versions()
            if force_refresh and not self._clear_caches():
                raise OSError("Failed to clear downloader caches for force refresh")
            success_results, _failed_results = orchestrator.run_download_pipeline()
            report = self._collect_download_report(success_results, tracked_versions)
            orchestrator.cleanup_old_versions()
            orchestrator.update_version_tracking()
            latest = orchestrator.get_latest_versions()
            return report._replace(
                failed_downloads=self.get_failed_downloads(),
                latest_firmware_version=latest.get("firmware") or "",
                latest_client_app_version=latest.get("client_app") or "",
            )
        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            self.download_run_failed = True
            logger.exception("Error in CLI integration: %s", exc)
            return DownloadReport.empty()

    def _get_tracked_client_app_versions(self) -> Dict[str, Optional[str]]:
        """
        Return client app tracking versions from local tracking files before a pipeline run.

        Returns:
            Dict[str, Optional[str]]: Mapping with keys `current` and
                `prerelease` representing locally tracked versions.
        """
        tracked_client_app: Optional[str] = None
        tracked_client_app_prerelease: Optional[str] = None

        if self.client_app_downloader:
            try:
                tracked_client_app = self.client_app_downloader.get_latest_release_tag()
            except (OSError, ValueError, TypeError):
                tracked_client_app = None

            try:
                tracking_file = (
                    self.client_app_downloader.get_prerelease_tracking_file()
                )
                if (
                    isinstance(tracking_file, str)
                    and tracking_file
                    and os.path.exists(tracking_file)
                ):
                    tracking_data = (
                        self.client_app_downloader.cache_manager.read_json(
                            tracking_file
                        )
                        or {}
                    )
                    if isinstance(tracking_data, dict):
                        tracked_value = tracking_data.get("latest_version")
                        if isinstance(tracked_value, str):
                            tracked_client_app_prerelease = tracked_value
            except (OSError, ValueError, TypeError, KeyError):
                tracked_client_app_prerelease = None

        if not isinstance(tracked_client_app, str):
            tracked_client_app = None
        if not isinstance(tracked_client_app_prerelease, str):
            tracked_client_app_prerelease = None

        return {
            "current": tracked_client_app,
            "prerelease": tracked_client_app_prerelease,
        }

    def _clear_caches(self) -> bool:
        """
        Clear downloader caches managed by this integration.

        This calls the Client app downloader's cache manager to remove all cached data; exceptions raised during the clear operation (e.g., OSError, ValueError) are caught and logged and are not propagated.

        Returns:
            bool: True if cache was cleared successfully, False otherwise.
        """
        try:
            # Clear shared cache manager (same instance used by all downloaders)
            success = True
            if self.client_app_downloader:
                if not self.client_app_downloader.clear_cache():
                    logger.warning("Failed to clear cache for Client app downloader")
                    success = False

            if success:
                logger.info("All caches cleared")
            return success

        except (OSError, ValueError) as e:
            logger.warning(f"Error clearing caches: {e}")
            return False

    # ------------------------------------------------------------------
    # Download-summary helpers: resolve the newest local copy of each asset
    # type so the latest-version summary can show how fresh each one is.
    # ------------------------------------------------------------------

    def _download_base_dir(self) -> Optional[str]:
        """Return the configured DOWNLOAD_DIR, or None when unavailable."""
        cfg = self.config if isinstance(self.config, dict) else None
        base = cfg.get("DOWNLOAD_DIR") if cfg else None
        return base if isinstance(base, str) and base.strip() else None

    @staticmethod
    def _dir_has_download_payload(path: str) -> bool:
        """Return whether a release directory contains downloaded payload data.

        Release-note metadata is written before client-app downloads begin, so
        directory existence alone does not prove that any asset was downloaded.
        Legacy adjacent hash sidecars likewise do not count as payloads, and
        neither does an empty extracted subdirectory: only an actual payload
        file does. Payload files nested inside extracted subdirectories count
        (archives are extracted with their member paths preserved); symlinked
        subdirectories are not followed to avoid cycles.
        """

        def has_payload_file(dir_path: str, depth: int) -> bool:
            try:
                with os.scandir(dir_path) as entries:
                    for entry in entries:
                        name = entry.name
                        if name.startswith("release_notes-") and name.endswith(".md"):
                            continue
                        if name.endswith(".sha256"):
                            continue
                        if entry.is_file(follow_symlinks=True):
                            return True
                        if depth > 0 and entry.is_dir(follow_symlinks=False):
                            if has_payload_file(entry.path, depth - 1):
                                return True
            except FileNotFoundError:
                return False
            except OSError as exc:
                logger.warning("Cannot inspect %s: %s", dir_path, exc)
                return False
            return False

        # Downloaded payloads sit at the top level; extracted archives keep a
        # couple of directory levels from their member paths.
        return has_payload_file(path, 3)

    @classmethod
    def _newest_existing_dir(cls, candidates: Iterable[str]) -> Optional[str]:
        """
        Return the payload-bearing candidate directory with the newest mtime.

        Multiple storage variants of one version can coexist (e.g. a release
        tag next to its channel-suffixed copy), so the most recently written
        payload-bearing directory is the honest answer for "when was this
        downloaded". Metadata-only directories are ignored because release
        notes can be created before an asset download succeeds. Missing
        candidates are skipped silently; other OSError problems are logged and
        the candidate is treated as absent so the summary never crashes.
        """
        newest: Optional[Tuple[float, str]] = None
        for candidate in candidates:
            try:
                mtime = os.path.getmtime(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning("Cannot inspect %s: %s", candidate, exc)
                continue
            if not os.path.isdir(candidate) or not cls._dir_has_download_payload(
                candidate
            ):
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, candidate)
        return newest[1] if newest else None

    @classmethod
    def _first_existing_dir(cls, base: Optional[str], *names: str) -> Optional[str]:
        """Return the newest-by-mtime existing directory among ``names`` under ``base``."""
        if not base:
            return None
        return cls._newest_existing_dir([os.path.join(base, name) for name in names])

    def _downloaded_age_suffix(self, local_dir: Optional[str]) -> str:
        """
        Describe how long ago ``local_dir`` was last written, for summary lines.

        Returns ``" (downloaded <age> ago)"`` derived from the directory mtime
        (set when its files were downloaded), ``" (not downloaded yet)"`` when a
        download directory is configured but the version is absent locally, and
        ``""`` when no download directory is known (library use) so that no
        claim about local state is made.
        """
        if not self._download_base_dir():
            return ""
        if local_dir is None:
            return " (not downloaded yet)"
        try:
            age = time.time() - os.path.getmtime(local_dir)
        except FileNotFoundError:
            return " (not downloaded yet)"
        except OSError as exc:
            logger.warning("Cannot inspect %s: %s", local_dir, exc)
            return " (not downloaded yet)"
        if age < 120:
            return " (downloaded just now)"
        minutes = int(age // 60)
        if minutes < 60:
            return f" (downloaded {minutes}m ago)"
        hours = int(age // 3600)
        if hours < 48:
            return f" (downloaded {hours}h ago)"
        return f" (downloaded {int(age // 86400)}d ago)"

    def _local_firmware_release_dir(self, tag: Optional[str]) -> Optional[str]:
        """
        Locate the stored directory for a firmware release tag.

        Covers the channel-suffixed storage layouts (``<tag>-alpha`` and
        friends) and the ``-revoked`` suffix used for filtered releases.
        """
        base = self._download_base_dir()
        if not base or not tag:
            return None
        firmware_base = os.path.join(base, FIRMWARE_DIR_NAME)
        candidates = [
            tag,
            *(f"{tag}-{channel}" for channel in sorted(STORAGE_CHANNEL_SUFFIXES)),
            f"{tag}-revoked",
        ]
        return self._first_existing_dir(firmware_base, *candidates)

    def _local_firmware_prerelease_dir(
        self, identifier: Optional[str]
    ) -> Optional[str]:
        """Locate the stored directory for a repo-based firmware prerelease identifier."""
        base = self._download_base_dir()
        if not base or not identifier:
            return None
        names = (identifier, f"{FIRMWARE_DIR_PREFIX}{identifier}")
        # Both prerelease storage layouts can hold copies of the same
        # identifier; compare every existing candidate by mtime.
        return self._newest_existing_dir(
            os.path.join(base, FIRMWARE_DIR_NAME, parent, name)
            for parent in (REPO_DOWNLOADS_DIR, FIRMWARE_PRERELEASES_DIR_NAME)
            for name in names
        )

    def _local_firmware_nightly_dir(self, build_id: Optional[str]) -> Optional[str]:
        """Locate the stored directory for a firmware-nightly build id."""
        base = self._download_base_dir()
        if not base or not build_id:
            return None
        return self._first_existing_dir(
            os.path.join(base, FIRMWARE_DIR_NAME, FIRMWARE_NIGHTLIES_DIR_NAME),
            build_id,
        )

    def _local_app_dir(
        self, tag: Optional[str], *, prerelease: bool = False
    ) -> Optional[str]:
        """Locate the stored directory for a client app release or prerelease tag."""
        base = self._download_base_dir()
        if not base or not tag:
            return None
        if prerelease:
            return self._first_existing_dir(
                os.path.join(base, APP_DIR_NAME, APP_PRERELEASES_DIR_NAME), tag
            )
        return self._first_existing_dir(os.path.join(base, APP_DIR_NAME), tag)

    def _local_snapshot_entry(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Return ``(versionCode, directory)`` for the newest stored app snapshot.

        Snapshot directories are named ``<YYYYMMDD>-<HHMMSS>-<versionCode>``;
        the highest versionCode wins (mtime only breaks ties), matching
        snapshot retention semantics, and its trailing segment is the
        versionCode.
        """
        base = self._download_base_dir()
        if not base:
            return None, None
        snapshots_root = os.path.join(base, APP_DIR_NAME, APP_SNAPSHOTS_DIR_NAME)
        try:
            entries: list[tuple[int, float, str]] = []
            with os.scandir(snapshots_root) as snapshot_entries:
                for entry in snapshot_entries:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    version_text = entry.name.rsplit("-", 1)[-1]
                    if not version_text.isdigit():
                        continue
                    # One unreadable candidate must not discard snapshots that
                    # were listed fine; only failures on the root itself fall
                    # through to the handlers below.
                    try:
                        mtime = os.path.getmtime(entry.path)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        logger.warning("Cannot inspect %s: %s", entry.path, exc)
                        continue
                    if not self._dir_has_download_payload(entry.path):
                        continue
                    entries.append((int(version_text), mtime, entry.path))
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            logger.warning("Cannot list app snapshots in %s: %s", snapshots_root, exc)
            return None, None
        if not entries:
            return None, None
        version_code, _mtime, newest_path = max(
            entries, key=lambda item: (item[0], item[1])
        )
        return str(version_code), newest_path

    def log_download_results_summary(
        self,
        *,
        logger_override: Any = None,
        elapsed_seconds: float,
        downloaded_firmwares: List[str],
        downloaded_client_apps: List[str],
        downloaded_firmware_prereleases: Optional[List[str]] = None,
        downloaded_client_app_prereleases: Optional[List[str]] = None,
        failed_downloads: List[Dict[str, Any]],
        latest_firmware_version: str,
        latest_client_app_version: str,
        new_firmware_versions: Optional[List[str]] = None,
        new_client_app_versions: Optional[List[str]] = None,
    ) -> None:
        """
        Emit a release summary of download results to the provided logger.

        Logs elapsed time, counts of downloaded assets, a latest-version line for every asset type configured for download (firmware release/prerelease/nightly, client app release/prerelease, app snapshots) annotated with how long ago the newest local copy was downloaded, detailed information about any failed downloads, and a GitHub API usage summary. If no downloads or failures occurred, logs an up-to-date timestamp. If this instance has a configured `config`, sends notifications for completion or up-to-date state.

        Parameters:
            logger_override (logging-like, optional): Logger to use instead of the module logger.
            elapsed_seconds (float): Total time elapsed for the download run.
            downloaded_firmwares (List[str]): Downloaded firmware filenames or tags.
            downloaded_client_apps (List[str]): Downloaded client app release tags.
            downloaded_firmware_prereleases (Optional[List[str]]): Downloaded firmware prerelease tags, if any.
            downloaded_client_app_prereleases (Optional[List[str]]): Downloaded client app prerelease tags, if any.
            new_firmware_versions (List[str]): Retained for backward compatibility; not used by this method.
            new_client_app_versions (List[str]): Retained for backward compatibility; not used by this method.
        """
        log = logger_override or logger

        if self.orchestrator:
            self.orchestrator.log_firmware_release_history_summary()

        log.info(f"\nCompleted in {elapsed_seconds:.1f}s")

        downloaded_firmware_prereleases = downloaded_firmware_prereleases or []
        downloaded_client_app_prereleases = downloaded_client_app_prereleases or []

        # Snapshot debug builds — always collect successful versionCodes for
        # local logging and download counts.  NTFY inclusion is gated by
        # NOTIFY_ON_SNAPSHOTS (default False).
        downloaded_app_snapshots: list[str] = []
        if self.orchestrator:
            for result in self.orchestrator.download_results:
                if (
                    getattr(result, "file_type", "") == FILE_TYPE_APP_SNAPSHOT
                    and getattr(result, "success", False)
                    and not getattr(result, "was_skipped", False)
                ):
                    vc = self._extract_snapshot_version_code(result)
                    if vc and vc not in downloaded_app_snapshots:
                        downloaded_app_snapshots.append(vc)

        # Snapshots included in NTFY notifications only when explicitly enabled.
        notified_app_snapshots = (
            downloaded_app_snapshots
            if (
                self.config
                and coerce_bool(
                    self.config.get("NOTIFY_ON_SNAPSHOTS", DEFAULT_NOTIFY_ON_SNAPSHOTS)
                )
            )
            else []
        )

        # Firmware nightly builds — reported only when the transaction
        # finalized WITH an actual download (at least one asset had
        # ``was_skipped=False``). ``MAINTENANCE_ONLY`` (tracking-only
        # reconciliation / already-complete maintenance) and ``CHECK_FAILED``
        # never surface as a download. NTFY inclusion is gated by
        # NOTIFY_ON_FIRMWARE_NIGHTLIES (default False).
        downloaded_firmware_nightlies: list[str] = []
        if (
            self.orchestrator
            and self.orchestrator.nightly_run_state
            == NightlyRunState.FINALIZED_WITH_DOWNLOAD
            and self.orchestrator.latest_firmware_nightly_build_id
        ):
            downloaded_firmware_nightlies.append(
                self.orchestrator.latest_firmware_nightly_build_id
            )

        notified_firmware_nightlies = (
            downloaded_firmware_nightlies
            if (
                self.config
                and coerce_bool(
                    self.config.get(
                        "NOTIFY_ON_FIRMWARE_NIGHTLIES",
                        DEFAULT_NOTIFY_ON_FIRMWARE_NIGHTLIES,
                    )
                )
            )
            else []
        )

        # Actual download count includes snapshots and firmware nightlies for
        # local reporting.
        downloaded_count = (
            len(downloaded_firmwares)
            + len(downloaded_client_apps)
            + len(downloaded_firmware_prereleases)
            + len(downloaded_client_app_prereleases)
            + len(downloaded_app_snapshots)
            + len(downloaded_firmware_nightlies)
        )
        # Notifiable count excludes snapshots/nightlies when their gates are False.
        notifiable_count = (
            downloaded_count
            - len(downloaded_app_snapshots)
            + len(notified_app_snapshots)
            - len(downloaded_firmware_nightlies)
            + len(notified_firmware_nightlies)
        )
        if downloaded_count > 0:
            log.info(f"Downloaded {downloaded_count} new versions")

        latest_versions = self.get_latest_versions()
        latest_firmware_prerelease = latest_versions.get("firmware_prerelease")
        latest_client_app = (
            latest_versions.get("client_app") or latest_client_app_version
        )
        latest_client_app_prerelease = latest_versions.get("client_app_prerelease")

        # Latest-version lines: exactly one line per asset type configured for
        # download (firmware releases, repo-based firmware prereleases, firmware
        # nightlies, client app releases/prereleases, app snapshots), each
        # annotated with how long ago the newest local copy of that version was
        # downloaded. With no attached config (library use) the legacy ungated
        # firmware/client-app lines are kept and opt-in types stay hidden.
        cfg = self.config if isinstance(self.config, dict) else {}
        if isinstance(self.config, dict):
            # An attached config — even an empty one — decides gating; only
            # library use without any config keeps the legacy ungated lines.
            save_firmware = coerce_bool(cfg.get("SAVE_FIRMWARE", False))
            save_apps = client_app_downloads_enabled(cfg)
        else:
            save_firmware = True
            save_apps = True
        firmware_prereleases_enabled = save_firmware and (
            not isinstance(self.config, dict)
            or coerce_bool(
                cfg.get(
                    "CHECK_FIRMWARE_PRERELEASES",
                    cfg.get("CHECK_PRERELEASES", False),
                )
            )
        )
        nightlies_enabled = save_firmware and coerce_bool(
            cfg.get("CHECK_FIRMWARE_NIGHTLIES", DEFAULT_CHECK_FIRMWARE_NIGHTLIES)
        )
        app_prereleases_enabled = save_apps and coerce_bool(
            cfg.get("CHECK_APP_PRERELEASES", DEFAULT_CHECK_APP_PRERELEASES)
        )
        snapshots_enabled = save_apps and coerce_bool(
            cfg.get("CHECK_APP_SNAPSHOTS", DEFAULT_CHECK_APP_SNAPSHOTS)
        )
        latest_firmware_nightly = latest_versions.get("firmware_nightly")

        if save_firmware:
            if latest_firmware_version:
                log.info(
                    "Latest firmware release: %s%s",
                    latest_firmware_version,
                    self._downloaded_age_suffix(
                        self._local_firmware_release_dir(latest_firmware_version)
                    ),
                )
            else:
                log.info("Latest firmware release: none")
            if firmware_prereleases_enabled:
                if latest_firmware_prerelease:
                    log.info(
                        "Latest firmware prerelease: %s%s",
                        latest_firmware_prerelease,
                        self._downloaded_age_suffix(
                            self._local_firmware_prerelease_dir(
                                latest_firmware_prerelease
                            )
                        ),
                    )
                else:
                    log.info("Latest firmware prerelease: none")
            if nightlies_enabled:
                if latest_firmware_nightly:
                    log.info(
                        "Latest firmware nightly: %s%s",
                        latest_firmware_nightly,
                        self._downloaded_age_suffix(
                            self._local_firmware_nightly_dir(latest_firmware_nightly)
                        ),
                    )
                else:
                    log.info("Latest firmware nightly: none")
        if save_apps:
            if latest_client_app:
                log.info(
                    "Latest client app release: %s%s",
                    latest_client_app,
                    self._downloaded_age_suffix(self._local_app_dir(latest_client_app)),
                )
            else:
                log.info("Latest client app release: none")
            if app_prereleases_enabled:
                if latest_client_app_prerelease:
                    log.info(
                        "Latest client app prerelease: %s%s",
                        latest_client_app_prerelease,
                        self._downloaded_age_suffix(
                            self._local_app_dir(
                                latest_client_app_prerelease, prerelease=True
                            )
                        ),
                    )
                else:
                    log.info("Latest client app prerelease: none")
            if snapshots_enabled:
                snapshot_version_code, snapshot_dir = self._local_snapshot_entry()
                if snapshot_version_code:
                    log.info(
                        "Latest app snapshot: %s%s",
                        snapshot_version_code,
                        self._downloaded_age_suffix(snapshot_dir),
                    )
                else:
                    log.info("Latest app snapshot: none")

        if downloaded_app_snapshots:
            log.info(
                "Downloaded client app snapshot builds: %s",
                ", ".join(downloaded_app_snapshots),
            )

        if downloaded_firmware_nightlies:
            log.info(
                "Downloaded firmware nightly builds: %s",
                ", ".join(downloaded_firmware_nightlies),
            )

        if failed_downloads:
            log.info(f"{len(failed_downloads)} downloads failed:")
            for failure in failed_downloads:
                url = failure.get("url", "unknown")
                retryable = failure.get("retryable")
                http_status = failure.get("http_status")
                error = failure.get("error", "")
                log.info(
                    f"- {failure.get('type', 'Unknown')} {failure.get('release_tag', '')}: "
                    f"{failure.get('file_name', 'unknown')} "
                    f"URL={url} retryable={retryable} http_status={http_status} error={error}"
                )

        # A nightly transaction that was attempted but not finalized, or whose
        # source check failed, must suppress the generic up-to-date log/NTFY —
        # these are distinct failure states, not "up to date". MAINTENANCE_ONLY
        # (already-complete reconciliation) is NOT incomplete and allows the
        # up-to-date message when nothing else was downloaded.
        nightly_incomplete = (
            self.orchestrator is not None
            and self.orchestrator.nightly_run_state
            in (
                NightlyRunState.ATTEMPTED_INCOMPLETE,
                NightlyRunState.CHECK_FAILED,
            )
        )
        # A stable release fetch (firmware or client app) that failed during
        # the run is likewise a distinct state: "could not check", not
        # "nothing new".
        release_check_failed = self.orchestrator is not None and getattr(
            self.orchestrator, "release_check_failed", False
        )
        # A run skipped because another fetchtastic process holds the
        # cross-process run lock checked nothing at all.
        pipeline_lock_skipped = self.orchestrator is not None and getattr(
            self.orchestrator, "pipeline_lock_skipped", False
        )

        if (
            downloaded_count == 0
            and not failed_downloads
            and not self.download_run_failed
            and not nightly_incomplete
            and not release_check_failed
            and not pipeline_lock_skipped
        ):
            log.info(
                "All assets are up to date.\n%s",
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        elif downloaded_count == 0 and self.download_run_failed:
            log.info("Download run failed; check logs for details.")
        elif downloaded_count == 0 and failed_downloads:
            log.info("All attempted downloads failed; check logs for details.")
        elif downloaded_count == 0 and not failed_downloads and pipeline_lock_skipped:
            log.info(
                "Skipped this run: another fetchtastic download run is active. "
                "No new versions downloaded."
            )
        elif downloaded_count == 0 and not failed_downloads and release_check_failed:
            # Zero downloads, no asset-level failures, but a stable release
            # check failed (e.g. GitHub outage or rate limit). Emit a distinct
            # summary instead of claiming up to date.
            log.info(
                "Release check failed; could not verify all sources. "
                "No new versions downloaded."
            )
        elif (
            downloaded_count == 0
            and not failed_downloads
            and nightly_incomplete
            and self.orchestrator is not None
        ):
            # Zero downloads, no asset-level failures, but the nightly
            # transaction itself is incomplete or the source check failed.
            # Emit a local summary so the user sees the distinct state
            # instead of silence. Up-to-date is excluded; the existing
            # all-attempted-failed branch above handles asset failures.
            nightly_state = self.orchestrator.nightly_run_state
            if nightly_state == NightlyRunState.CHECK_FAILED:
                log.info(
                    "Firmware nightly check failed; see logs for details. "
                    "No new versions downloaded."
                )
            else:
                log.info(
                    "Firmware nightly build incomplete; tracking, latest "
                    "pointer, and cleanup deferred. No new versions downloaded."
                )

        new_versions_available = bool(
            (new_firmware_versions or []) or (new_client_app_versions or [])
        )

        # Send notifications based on download results
        if self.config:
            if notifiable_count > 0:
                send_download_completion_notification(
                    self.config,
                    downloaded_firmwares,
                    downloaded_client_apps,
                    downloaded_firmware_prereleases,
                    downloaded_client_app_prereleases,
                    downloaded_app_snapshots=notified_app_snapshots,
                    downloaded_firmware_nightlies=notified_firmware_nightlies,
                )
            elif downloaded_count > 0:
                # Downloads occurred (e.g. snapshot-only with notifications
                # disabled) but none were notifiable — send no NTFY at all.
                pass
            elif self.orchestrator and self.orchestrator.wifi_skipped:
                send_new_releases_available_notification(
                    self.config,
                    self.orchestrator.available_new_firmware_versions,
                    self.orchestrator.available_new_client_app_versions,
                    downloads_skipped_reason="Downloads skipped: not connected to Wi-Fi.",
                )
            elif (
                not failed_downloads
                and not self.download_run_failed
                and not new_versions_available
                and not nightly_incomplete
                and not release_check_failed
                and not pipeline_lock_skipped
            ):
                send_up_to_date_notification(self.config)

        summary = get_api_request_summary()
        if summary.get("total_requests", 0) > 0:
            log.debug(format_api_summary(summary))
        else:
            log.debug(
                "📊 GitHub API Summary: No API requests made (all data served from cache)"
            )

    def _collect_download_report(
        self,
        success_results: List[Any],
        tracked_versions: Optional[Dict[str, Optional[str]]] = None,
    ) -> DownloadReport:
        """Deduplicate release versions across all selected installer formats."""
        report = DownloadReport.empty()
        latest = self.get_latest_versions()
        current_app = (
            tracked_versions.get("current")
            if tracked_versions is not None
            else latest.get("client_app")
        )
        current_app_prerelease = (
            tracked_versions.get("prerelease")
            if tracked_versions is not None
            else latest.get("client_app_prerelease")
        )
        new_firmware_set: set[str] = set()
        new_app_set: set[str] = set()
        for result in success_results:
            tag = result.release_tag
            if not tag or getattr(result, "was_skipped", False):
                continue
            kind = result.file_type
            if kind in CLIENT_APP_FILE_TYPES | ANDROID_FILE_TYPES | DESKTOP_FILE_TYPES:
                prerelease = kind in {
                    FILE_TYPE_CLIENT_APP_PRERELEASE,
                    FILE_TYPE_ANDROID_PRERELEASE,
                    FILE_TYPE_DESKTOP_PRERELEASE,
                }
                versions = (
                    report.downloaded_client_app_prereleases
                    if prerelease
                    else report.downloaded_client_apps
                )
                current = (
                    (current_app_prerelease or current_app)
                    if prerelease
                    else current_app
                )
                self._update_new_versions(
                    tag, current, report.new_client_app_versions, new_app_set
                )
            elif kind in FIRMWARE_FILE_TYPES and kind != FILE_TYPE_FIRMWARE_MANIFEST:
                prerelease = kind in {
                    FILE_TYPE_FIRMWARE_PRERELEASE,
                    FILE_TYPE_FIRMWARE_PRERELEASE_REPO,
                }
                versions = (
                    report.downloaded_firmware_prereleases
                    if prerelease
                    else report.downloaded_firmwares
                )
                current = latest.get("firmware")
                comparison_tag = None
                if prerelease:
                    comparison_tag = self._normalize_firmware_prerelease_tag(tag)
                    current = self._normalize_firmware_prerelease_tag(
                        latest.get("firmware_prerelease") or current
                    )
                self._update_new_versions(
                    tag,
                    current,
                    report.new_firmware_versions,
                    new_firmware_set,
                    comparison_release_tag=comparison_tag,
                )
            else:
                continue
            if tag not in versions:
                versions.append(tag)
        return report

    def _update_new_versions(
        self,
        release_tag: str,
        current_version: Optional[str],
        new_versions_list: List[str],
        new_versions_set: set[str],
        *,
        comparison_release_tag: Optional[str] = None,
    ) -> None:
        """
        Add release_tag to new_versions_list and new_versions_set if it is not already present and is newer than current_version.

        Parameters:
            release_tag (str): The release tag to consider for recording.
            current_version (Optional[str]): The existing version to compare against; if None, release_tag is treated as newer.
            new_versions_list (List[str]): Mutable list to append the release_tag to when it is new.
            new_versions_set (set[str]): Mutable set used to ensure uniqueness of recorded versions.
            comparison_release_tag (Optional[str]): If provided, this tag is used for version comparison instead of release_tag.
        """
        compare_tag = comparison_release_tag or release_tag
        if release_tag not in new_versions_set and (
            not current_version or self._is_newer_version(compare_tag, current_version)
        ):
            new_versions_list.append(release_tag)
            new_versions_set.add(release_tag)

    @staticmethod
    def _extract_snapshot_version_code(result: Any) -> Optional[str]:
        """Derive the numeric versionCode from a snapshot result's canonical path.

        Handles both plain ``<versionCode>`` and timestamp-prefixed
        ``<YYYYMMDD-HHMMSS>-<versionCode>`` directory names.
        """
        file_path = getattr(result, "file_path", None)
        if file_path:
            parent = os.path.basename(os.path.dirname(str(file_path)))
            if parent.isdigit():
                return parent
            # Handle timestamp-prefixed dirs: <YYYYMMDD-HHMMSS>-<versionCode>
            parts = parent.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return parts[1]
        # Fallback: parse from asset name in download_url
        url = getattr(result, "download_url", None)
        if url:
            # Use only the URL path so query parameters/fragments don't interfere
            parsed = urllib.parse.urlparse(str(url))
            name = os.path.basename(parsed.path)
            match = _SNAPSHOT_VC_RE.search(name)
            if match:
                return match.group(1)
        return None

    def _normalize_firmware_prerelease_tag(self, tag: Optional[str]) -> Optional[str]:
        """
        Normalize a firmware prerelease tag by removing the configured firmware directory prefix if present.

        Parameters:
            tag (Optional[str]): A prerelease tag that may be prefixed with FIRMWARE_DIR_PREFIX.

        Returns:
            normalized_tag (Optional[str]): The tag with FIRMWARE_DIR_PREFIX stripped if it was present;
                otherwise the original tag (or None/empty unchanged).
        """
        if not tag:
            return tag
        return tag.removeprefix(FIRMWARE_DIR_PREFIX)

    def _add_downloaded_asset(
        self,
        release_tag: str,
        downloaded_list: List[str],
        downloaded_set: set[str],
    ) -> None:
        """
        Add a release tag to the downloaded list while ensuring uniqueness via the downloaded set.

        Parameters:
            release_tag: The release tag to record.
            downloaded_list: Ordered list of recorded release tags; the tag is appended if not already recorded.
            downloaded_set: Set used to track recorded tags for fast membership checks and to prevent duplicates.
        """
        if release_tag not in downloaded_set:
            downloaded_list.append(release_tag)
            downloaded_set.add(release_tag)

    def _is_newer_version(self, version1: str, version2: str) -> bool:
        """
        Determine whether `version1` represents a newer version than `version2`.

        Parameters:
            version1 (str): Version string to compare.
            version2 (str): Version string to compare against.

        Returns:
            bool: `True` if `version1` represents a newer version than `version2`, `False` otherwise.
        """
        version_manager = self._get_version_manager()
        comparison = (
            version_manager.compare_versions(version1, version2)
            if version_manager
            else 0
        )
        return comparison > 0

    def _get_version_manager(self) -> Optional["VersionManager"]:
        """
        Acquire version manager exposed by Client app downloader.
        """
        if not self.client_app_downloader:
            return None
        # Check for method first (for backward compatibility and mocks),
        # then fall back to direct attribute access
        getter = getattr(self.client_app_downloader, "get_version_manager", None)
        if callable(getter):
            result = getter()
            return cast(Optional["VersionManager"], result)
        result = getattr(self.client_app_downloader, "version_manager", None)
        return cast(Optional["VersionManager"], result)

    def get_failed_downloads(self) -> List[Dict[str, Any]]:
        """
        Builds a legacy-formatted list describing failed downloads.

        Each item is a dict with the following keys:
            file_name: Base filename of the intended download or "unknown".
            release_tag: Associated release tag or "unknown".
            url: Download URL or "unknown".
            type: Human-readable file type (e.g., "Firmware", "Client App", "Repository", "Firmware Prerelease", "Client App Prerelease", or "Unknown").
            path_to_download: Full path where the file was to be saved, or "unknown".
            error: Error message for the failure, or empty string if none.
            retryable: Whether the failure is considered retryable.
            http_status: HTTP status code associated with the failure, or None if not applicable.

        Returns:
            List[Dict[str, Any]]: The list of failed download records. Returns an empty list if the integration is not initialized or there are no failures.
        """
        if not self.orchestrator:
            return []

        failed_downloads = []

        file_type_map = {
            FILE_TYPE_FIRMWARE: "Firmware",
            FILE_TYPE_FIRMWARE_MANIFEST: "Firmware Manifest",
            FILE_TYPE_FIRMWARE_NIGHTLY: "Firmware Nightly",
            FILE_TYPE_APP_SNAPSHOT: "Client App Snapshot",
            FILE_TYPE_ANDROID: "Client App",
            FILE_TYPE_FIRMWARE_PRERELEASE: "Firmware Prerelease",
            FILE_TYPE_FIRMWARE_PRERELEASE_REPO: "Firmware Prerelease",
            FILE_TYPE_REPOSITORY: "Repository",
            FILE_TYPE_ANDROID_PRERELEASE: "Client App Prerelease",
            FILE_TYPE_DESKTOP: "Client App",
            FILE_TYPE_DESKTOP_PRERELEASE: "Client App Prerelease",
            FILE_TYPE_CLIENT_APP: "Client App",
            FILE_TYPE_CLIENT_APP_PRERELEASE: "Client App Prerelease",
        }

        for result in self.orchestrator.failed_downloads:
            failure_type = (
                file_type_map.get(result.file_type, "Unknown")
                if result.file_type
                else "Unknown"
            )
            failed_downloads.append(
                {
                    "file_name": (
                        os.path.basename(str(result.file_path))
                        if result.file_path
                        else "unknown"
                    ),
                    "release_tag": result.release_tag or "unknown",
                    "url": result.download_url or "unknown",
                    "type": failure_type,
                    "path_to_download": (
                        str(result.file_path) if result.file_path else "unknown"
                    ),
                    "error": result.error_message or "",
                    "retryable": result.is_retryable,
                    "http_status": result.http_status_code,
                }
            )

        return failed_downloads

    def main(
        self, config: Dict[str, Any], force_refresh: bool = False
    ) -> DownloadReport:
        """Normalize authentication and run the shared artifact pipeline."""
        if config is None:
            raise TypeError("config must be provided to the download integration.")

        try:
            # Normalize token once for the run so all downstream call sites see the
            # same effective value (config token preferred, env token fallback).
            config_token = get_effective_github_token(
                config.get("GITHUB_TOKEN"),
                allow_env_token=config.get("ALLOW_ENV_TOKEN", True),
            )
            if config_token:
                config["GITHUB_TOKEN"] = config_token
            else:
                config.pop("GITHUB_TOKEN", None)

            results = self.run_download(config, force_refresh)
            return results

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            self.handle_cli_error(error)
            return DownloadReport.empty()

    def clear_cache(self, config: Dict[str, Any]) -> bool:
        """
        Clear all download caches without running the download pipeline.

        Parameters:
            config (Dict[str, Any]): Configuration mapping for cache clear.

        Returns:
            bool: True if caches were cleared successfully, False otherwise.
        """
        try:
            self._initialize_components(config)
            return self._clear_caches()

        except (
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as error:
            self.handle_cli_error(error)
            return False

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
                - "client_app_downloads": count of successful client app artifact downloads.
                - "firmware_downloads": count of successful firmware artifact downloads.
                - "repository_downloads": count of repository downloads (always 0 for automatic pipeline).
        """
        if self.orchestrator:
            return self.orchestrator.get_download_statistics()
        return {
            "total_downloads": 0,
            "successful_downloads": 0,
            "skipped_downloads": 0,
            "failed_downloads": 0,
            "success_rate": 0.0,
            "client_app_downloads": 0,
            "firmware_downloads": 0,
            "repository_downloads": 0,
        }

    def get_latest_versions(self) -> Dict[str, str]:
        """
        Get the latest known version strings for each artifact type.

        Returns:
            dict: Mapping with keys 'client_app', 'client_app_prerelease', 'firmware', and 'firmware_prerelease'; an empty string indicates that a version is unavailable.
        """
        versions: Dict[str, Any] = {
            "firmware": "",
            "firmware_prerelease": "",
            "client_app": "",
            "client_app_prerelease": "",
        }
        if self.orchestrator:
            versions.update(self.orchestrator.get_latest_versions())
        # Convert Optional[str] to str for compatibility
        return {k: v or "" for k, v in versions.items()}

    def handle_cli_error(self, error: Exception) -> None:
        logger.error(f"CLI Error: {error!s}")

        if isinstance(error, ImportError):
            logger.error(
                "Import error - please check your Python environment and dependencies"
            )
        elif isinstance(error, FileNotFoundError):
            logger.error("File not found - please check your configuration and paths")
        elif isinstance(error, PermissionError):
            logger.error("Permission error - please check file system permissions")
        elif isinstance(
            error, (requests.ConnectionError, requests.Timeout, ConnectionError)
        ):
            logger.error(
                "Network connection error - please check your internet connection"
            )
        else:
            logger.error("An unexpected error occurred - please check logs for details")
