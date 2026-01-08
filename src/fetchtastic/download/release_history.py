"""
Release History Tracking

Tracks GitHub release status changes (revoked/removed) and provides helpers for
release channel labeling (alpha/beta/rc).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fetchtastic.log_utils import logger

from .cache import CacheManager, parse_iso_datetime_utc
from .interfaces import Release
from .version import VersionManager

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_REMOVED = "removed"

CHANNEL_STABLE = "stable"
CHANNEL_PRERELEASE = "prerelease"

_REVOKED_TITLE_RX = re.compile(r"\brevoked\b", re.IGNORECASE)
_REVOKED_BODY_LINE_RX = re.compile(
    r"^(this release (has been|was|is) revoked|release (has been|was) revoked|revoked\b)",
    re.IGNORECASE,
)
_CHANNEL_RX = {
    "alpha": re.compile(r"\balpha\b", re.IGNORECASE),
    "beta": re.compile(r"\bbeta\b", re.IGNORECASE),
    "rc": re.compile(r"\b(?:rc|release candidate)\b", re.IGNORECASE),
}
_CHANNEL_ORDER = ("alpha", "beta", "rc", CHANNEL_PRERELEASE, CHANNEL_STABLE)
_HASH_TAGGED_RELEASE_RX = re.compile(r"^v?\d+\.\d+\.\d+\.[a-f0-9]{6,}$", re.IGNORECASE)


def _join_text(parts: Iterable[Optional[str]]) -> str:
    """
    Concatenate non-empty string parts with single spaces and return the result in lowercase.

    Parameters:
        parts (Iterable[Optional[str]]): An iterable of values; non-string items and strings that are empty or only whitespace are ignored.

    Returns:
        joined (str): The cleaned parts joined by single spaces, converted to lowercase.
    """
    cleaned: List[str] = []
    for part in parts:
        if not isinstance(part, str):
            continue
        stripped = part.strip()
        if stripped:
            cleaned.append(stripped)
    return " ".join(cleaned).lower()


def detect_release_channel(release: Release) -> str:
    """
    Determine the release channel from the release's name, tag, body, and prerelease flag.

    The function checks, in order: combined name and tag, the release body, and finally the release.prerelease boolean to classify the release.

    Returns:
        One of "alpha", "beta", "rc", "prerelease", or "stable" indicating the inferred channel.
    """
    primary_text = _join_text([release.name, release.tag_name])
    for label, rx in _CHANNEL_RX.items():
        if rx.search(primary_text):
            return label

    body_text = _join_text([release.body])
    for label, rx in _CHANNEL_RX.items():
        if rx.search(body_text):
            return label

    tag_name = release.tag_name if isinstance(release.tag_name, str) else ""
    if tag_name and _HASH_TAGGED_RELEASE_RX.match(tag_name):
        return "alpha"

    if release.prerelease:
        return CHANNEL_PRERELEASE

    return CHANNEL_STABLE


def is_release_revoked(release: Release) -> bool:
    """
    Determine whether a release is revoked by scanning name and explicit body markers.
    """
    name_text = release.name if isinstance(release.name, str) else ""
    if _REVOKED_TITLE_RX.search(name_text):
        return True

    body = release.body if isinstance(release.body, str) else ""
    if not body:
        return False

    for line in body.splitlines()[:14]:
        cleaned = line.strip()
        if not cleaned:
            continue
        while cleaned.startswith(">"):
            cleaned = cleaned[1:].lstrip()
        cleaned = re.sub(r"^[^a-zA-Z0-9]+", "", cleaned)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower.startswith("previously revoked"):
            continue
        if _REVOKED_BODY_LINE_RX.match(cleaned):
            return True

    return False


class ReleaseHistoryManager:
    """
    Manage release history files for revoked/removed tracking and summary logging.
    """

    def __init__(self, cache_manager: CacheManager, history_path: str) -> None:
        """
        Initialize the ReleaseHistoryManager.

        Stores the provided cache manager and history file path, and creates a VersionManager used for parsing and deriving base versions.
        Parameters:
                history_path (str): Filesystem path to the JSON file used to persist release history.
        """
        self.cache_manager = cache_manager
        self.history_path = history_path
        self.version_manager = VersionManager()

    def get_release_channel(self, release: Release) -> str:
        """
        Determine the release channel for a given release.

        Parameters:
            release (Release): The release object (e.g., GitHub release) to inspect.

        Returns:
            str: The release channel, one of "alpha", "beta", "rc", "prerelease", or "stable".
        """
        return detect_release_channel(release)

    def is_release_revoked(self, release: Release) -> bool:
        """
        Determine whether a release has been marked as revoked.

        Parameters:
            release (Release): The release object to inspect for revoked indicators.

        Returns:
            `true` if the release is revoked, `false` otherwise.
        """
        return is_release_revoked(release)

    def format_release_label(
        self,
        release: Release,
        *,
        include_channel: bool = True,
        include_status: bool = True,
        include_stable: bool = False,
    ) -> str:
        """
        Create a display label for a release by combining its tag name with optional channel and status annotations.

        Parameters:
            release (Release): The release object whose tag, channel, and status will be used to build the label.
            include_channel (bool): If true, append the release channel (e.g., "alpha", "beta") unless it is "stable" and `include_stable` is False.
            include_status (bool): If true, append the revoked status label when the release is detected as revoked.
            include_stable (bool): If true, include the "stable" channel explicitly when present; otherwise omit stable channel.

        Returns:
            label (str): A string containing the release tag name, optionally followed by parenthesized annotations (e.g., "v1.2.3 (alpha, revoked)" or "v1.2.3").
        """
        label = release.tag_name
        parts: List[str] = []
        if include_channel:
            channel = self.get_release_channel(release)
            if include_stable or channel not in (CHANNEL_STABLE, ""):
                parts.append(channel)
        if include_status and self.is_release_revoked(release):
            parts.append(STATUS_REVOKED)
        if parts:
            label = f"{label} ({', '.join(parts)})"
        return label

    def format_release_log_suffix(self, release: Release) -> str:
        """
        Return the label suffix for a release when the formatted label differs from its tag name.

        Parameters:
            release (Release): Release object whose label will be formatted.

        Returns:
            str: The substring of the formatted label that follows `release.tag_name`, or an empty string if the formatted label equals the tag name.
        """
        suffix = self.format_release_label(
            release, include_channel=True, include_status=True
        )
        if suffix == release.tag_name:
            return ""
        return suffix[len(release.tag_name) :]

    def update_release_history(self, releases: List[Release]) -> Dict[str, Any]:
        """
        Merge the given releases into the persisted release history, updating per-release metadata, status transitions, and removal markers.

        Parameters:
            releases (List[Release]): Iterable of release objects to incorporate; releases without a `tag_name` are ignored.

        Returns:
            dict: The updated history object containing at least the "entries" mapping (tag_name -> entry) and "last_updated" timestamp. The method persists the updated history to self.history_path (via the cache manager) and will set or update fields such as `first_seen`, `last_seen`, `status`, `status_updated_at`, and `removed_at` as appropriate.
        """
        history = self.cache_manager.read_json(self.history_path) or {}
        entries = history.get("entries")
        if not isinstance(entries, dict):
            entries = {}

        now = datetime.now(timezone.utc).isoformat()
        current_tags = {r.tag_name for r in releases if r.tag_name}
        oldest_published = self._get_oldest_published_at(releases)

        for release in releases:
            if not release.tag_name:
                continue
            status = (
                STATUS_REVOKED if self.is_release_revoked(release) else STATUS_ACTIVE
            )
            channel = self.get_release_channel(release)
            entry = entries.get(release.tag_name, {})
            first_seen = entry.get("first_seen") or now
            base_version = self._get_base_version(release.tag_name)
            previous_status = entry.get("status")

            entry.update(
                {
                    "tag_name": release.tag_name,
                    "name": release.name if isinstance(release.name, str) else "",
                    "published_at": release.published_at,
                    "channel": channel,
                    "base_version": base_version,
                    "status": status,
                    "first_seen": first_seen,
                    "last_seen": now,
                }
            )
            if previous_status != status:
                entry["status_updated_at"] = now
            if status != STATUS_REMOVED:
                entry.pop("removed_at", None)

            entries[release.tag_name] = entry

        for tag_name, entry in entries.items():
            if tag_name in current_tags:
                continue
            if entry.get("status") == STATUS_REMOVED:
                continue
            if not self._should_mark_removed(entry, oldest_published):
                continue
            entry["status"] = STATUS_REMOVED
            entry["removed_at"] = now
            entry["status_updated_at"] = now

        history["entries"] = entries
        history["last_updated"] = now
        if not self.cache_manager.atomic_write_json(self.history_path, history):
            logger.debug("Release history cache write failed for %s", self.history_path)
        return history

    def log_release_status_summary(
        self, history: Dict[str, Any], *, label: str
    ) -> None:
        """
        Log a concise summary of releases marked revoked or removed in the provided history.

        Examines history["entries"] (expected to be a dict of release entries keyed by tag) and, if any entries have status "revoked" or "removed", logs a header with counts and then logs each matching entry in sorted order. If "entries" is missing or not a dict, or no revoked/removed entries exist, the function does nothing.

        Parameters:
                history (Dict[str, Any]): History object containing an "entries" mapping where each entry is a dict with at least a "status" field.
                label (str): Prefix label used in log messages (e.g., source or context name).
        """
        entries = history.get("entries") or {}
        if not isinstance(entries, dict):
            return

        revoked = [e for e in entries.values() if e.get("status") == STATUS_REVOKED]
        removed = [e for e in entries.values() if e.get("status") == STATUS_REMOVED]
        if not revoked and not removed:
            return

        logger.info(
            "%s release status: %d revoked, %d removed",
            label,
            len(revoked),
            len(removed),
        )
        if revoked:
            logger.info("%s revoked releases:", label)
            for entry in self._sort_entries(revoked):
                self._log_release_status_entry(entry)
        if removed:
            logger.info("%s removed releases:", label)
            for entry in self._sort_entries(removed):
                self._log_release_status_entry(entry)

    def log_release_channel_summary(
        self, releases: List[Release], *, label: str
    ) -> None:
        """
        Log a summary of releases grouped by inferred channel and list releases per channel.

        Groups the provided releases by channel (using get_release_channel), logs a compact count for each channel in a preferred order (alpha, beta, rc, prerelease, stable, then other channels alphabetically), and logs a per-channel list of release labels for non-empty channels.

        Parameters:
            releases (List[Release]): Releases to summarize; releases without entries are ignored.
            label (str): Prefix label used in the logged summary message.
        """
        if not releases:
            return

        channel_map: Dict[str, List[Release]] = {}
        for release in releases:
            channel = self.get_release_channel(release)
            channel_map.setdefault(channel, [])
            if not release.tag_name:
                continue
            channel_map[channel].append(release)

        summary_parts = []
        for channel in _CHANNEL_ORDER:
            count = len(channel_map.get(channel, []))
            if count:
                summary_parts.append(f"{channel}={count}")
        for channel in sorted(set(channel_map) - set(_CHANNEL_ORDER)):
            count = len(channel_map.get(channel, []))
            if count:
                summary_parts.append(f"{channel}={count}")

        if not summary_parts:
            return

        logger.info("%s release channels: %s", label, ", ".join(summary_parts))

        for channel in _CHANNEL_ORDER:
            releases_for_channel = channel_map.get(channel)
            if not releases_for_channel:
                continue
            items = ", ".join(
                self.format_release_label(
                    release, include_channel=False, include_status=True
                )
                for release in releases_for_channel
            )
            logger.info("  - %s: %s", channel, items)

        for channel in sorted(set(channel_map) - set(_CHANNEL_ORDER)):
            releases_for_channel = channel_map.get(channel, [])
            if not releases_for_channel:
                continue
            items = ", ".join(
                self.format_release_label(
                    release, include_channel=False, include_status=True
                )
                for release in releases_for_channel
            )
            logger.info("  - %s: %s", channel, items)

    def _log_release_status_entry(self, entry: Dict[str, Any]) -> None:
        """
        Log a single release history entry as a colored, struck-through tag with optional channel and status.

        Parameters:
            entry (Dict[str, Any]): Release history entry containing `tag_name`, optional `channel`, and optional `status`. The `channel` is omitted from the output when equal to the stable channel.
        """
        tag_name = entry.get("tag_name") or "<unknown>"
        channel = entry.get("channel")
        parts = []
        if channel and channel != CHANNEL_STABLE:
            parts.append(channel)
        status = entry.get("status") or ""
        if status:
            parts.append(status)
        detail = f" ({', '.join(parts)})" if parts else ""
        color = "yellow" if status == STATUS_REVOKED else "red"
        logger.info(
            "  - [%s][strike]%s[/strike][/%s]%s",
            color,
            tag_name,
            color,
            detail,
        )

    def log_duplicate_base_versions(
        self, releases: List[Release], *, label: str
    ) -> None:
        """
        Logs base-version collisions where multiple releases share the same base version.

        Groups the provided releases by the base version derived from each release's tag_name and emits an info-level log entry for each base version that appears in two or more releases. Each log message is prefixed with the given label.

        Parameters:
            releases (List[Release]): Releases to inspect for duplicate base versions.
            label (str): Prefix to include in each log message.
        """
        base_map: Dict[str, List[Release]] = {}
        for release in releases:
            if not release.tag_name:
                continue
            base_version = self._get_base_version(release.tag_name)
            if not base_version:
                continue
            base_map.setdefault(base_version, []).append(release)

        for base_version, grouped in base_map.items():
            if len(grouped) < 2:
                continue
            items = ", ".join(self.format_release_label(r) for r in grouped)
            logger.warning(
                "%s: multiple releases share base version %s: %s",
                label,
                base_version,
                items,
            )

    def _get_base_version(self, tag_name: str) -> str:
        """
        Derives the canonical base version string from a release tag.

        Parameters:
            tag_name (str): The release tag or version string to normalize.

        Returns:
            The base version with any leading 'v' or 'V' removed.
        """
        clean = self.version_manager.extract_clean_version(tag_name) or tag_name
        return clean.lstrip("vV")

    def _get_oldest_published_at(self, releases: List[Release]) -> Optional[datetime]:
        """
        Get the earliest UTC `published_at` timestamp among the provided releases.

        Parses each release's `published_at` value and ignores missing or unparsable timestamps.

        Parameters:
            releases (List[Release]): Releases whose `published_at` values will be parsed and considered.

        Returns:
            Optional[datetime]: The earliest parsed UTC `published_at` datetime, or `None` if no valid timestamps are present.
        """
        timestamps = [
            parse_iso_datetime_utc(release.published_at) for release in releases
        ]
        filtered_timestamps: list[datetime] = [
            ts for ts in timestamps if ts is not None
        ]
        if not filtered_timestamps:
            return None
        return min(filtered_timestamps)

    def _should_mark_removed(
        self, entry: Dict[str, Any], oldest_published: Optional[datetime]
    ) -> bool:
        """
        Determine whether a history entry should be marked as removed based on its published timestamp.

        Parameters:
            entry (dict): A history entry object expected to contain a `published_at` ISO-8601 UTC timestamp string.
            oldest_published (datetime | None): The cutoff UTC datetime; entries published at or after this time are eligible.

        Returns:
            bool: `True` if `entry.published_at` is present, parses to a UTC datetime, and is greater than or equal to `oldest_published`; `False` otherwise.
        """
        if oldest_published is None:
            return False
        published_at = parse_iso_datetime_utc(entry.get("published_at"))
        if published_at is None:
            return False
        return published_at >= oldest_published

    def _sort_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort a list of release history entries so the most recently published appear first.

        Sort order: primary key is `published_at` (ISO-8601 datetime); entries without `published_at` are treated as oldest. Ties are broken by `tag_name` in descending lexicographic order.

        Parameters:
            entries (List[Dict[str, Any]]): List of entry objects. Each entry may contain the keys `"published_at"` (ISO-8601 string) and `"tag_name"` (string).

        Returns:
            List[Dict[str, Any]]: The input entries sorted by published date (newest first) and then by tag name.
        """

        def _sort_key(entry: Dict[str, Any]) -> tuple[datetime, str]:
            ts = parse_iso_datetime_utc(
                entry.get("published_at")
            ) or datetime.min.replace(tzinfo=timezone.utc)
            tag_name = entry.get("tag_name") or ""
            return (ts, tag_name)

        return sorted(entries, key=_sort_key, reverse=True)
