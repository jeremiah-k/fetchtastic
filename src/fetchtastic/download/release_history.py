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
_STABLE_RX = re.compile(r"\bstable\b", re.IGNORECASE)
_CHANNEL_ORDER = ("alpha", "beta", "rc")
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


def get_release_sorting_key(release: Release) -> tuple[datetime, str]:
    """
    Compute sorting key for releases by date and tag name.

    Sorts releases by published_at (chronological order) with tag_name as a
    secondary key for consistent ordering when dates are equal.

    Parameters:
        release (Release): Release object to compute sorting key for.

    Returns:
        tuple[datetime, str]: Tuple of (published_at, tag_name) for sorting.
            Missing or invalid published_at is replaced with datetime.min.
    """
    return (
        parse_iso_datetime_utc(release.published_at)
        or datetime.min.replace(tzinfo=timezone.utc),
        release.tag_name or "",
    )


def detect_release_channel(release: Release) -> str:
    """
    Infer the release channel ('alpha', 'beta', or 'rc') from a release's name and tag.

    This function examines the release's name and tag text to decide the channel. Explicit channel keywords in the name or tag take precedence; the word "stable" is treated as "beta"; tags with hash-style suffixes are treated as "alpha"; the release's prerelease flag is ignored. If no condition matches, returns "alpha".

    Returns:
        'alpha', 'beta', or 'rc' indicating the inferred channel.
    """
    # Use only name + tag for channel detection; body text is ignored by design to
    # avoid accidental channel mismatches from release notes.
    primary_text = _join_text([release.name, release.tag_name])

    # Explicit channel keywords in the title/tag always win.
    for label, rx in _CHANNEL_RX.items():
        if rx.search(primary_text):
            return label

    # "Stable" is not a channel label we emit; treat it as "beta" when present
    # to stay aligned with the alpha/beta terminology used for full releases.
    if _STABLE_RX.search(primary_text):
        return "beta"

    # Firmware tags that include a hash suffix are still full releases; default to alpha.
    tag_name = release.tag_name if isinstance(release.tag_name, str) else ""
    if tag_name and _HASH_TAGGED_RELEASE_RX.match(tag_name):
        return "alpha"

    # NOTE: We intentionally ignore release.prerelease here. GitHub prerelease flags
    # are not used to label full releases in this project, and prereleases are
    # tracked in a separate workflow and directory tree.
    #
    # Default for full releases is alpha (the most common track).
    return "alpha"


def is_release_revoked(release: Release) -> bool:
    """
    Determine whether a release has been marked as revoked by inspecting its title and the first several non-empty lines of its body for revocation indicators.

    Returns:
        `true` if the release is revoked, `false` otherwise.
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
            str: The release channel, one of "alpha", "beta", or "rc".
        """
        return detect_release_channel(release)

    def is_release_revoked(self, release: Release) -> bool:
        """
        Determine whether a release has been marked as revoked.

        Returns:
            True if the release is revoked, False otherwise.
        """
        return is_release_revoked(release)

    def find_beta_releases(self, releases: List[Release]) -> List[Release]:
        """
        Find and return all beta releases from the given list.

        Parameters:
            releases: List of releases to filter.

        Returns:
            List of beta releases (empty list if none found).
        """
        return [r for r in releases if self.get_release_channel(r) == "beta"]

    def find_most_recent_beta(self, releases: List[Release]) -> Optional[Release]:
        """
        Find and return the most recent beta release from the given list.

        Parameters:
            releases: List of releases to filter.

        Returns:
            The most recent beta release, or None if no beta releases are found.
        """
        beta_releases = self.find_beta_releases(releases)
        if not beta_releases:
            return None
        return max(beta_releases, key=get_release_sorting_key)

    def expand_keep_limit_to_include_beta(
        self, releases: List[Release], keep_limit: int
    ) -> int:
        """
        Adjust keep_limit so that the most recent beta release remains in the retained window.

        Parameters:
            releases (List[Release]): Available releases to consider.
            keep_limit (int): Original keep limit.

        Returns:
            int: Updated keep limit that includes the most recent beta, bounded by the number of releases.
        """
        if keep_limit <= 0:
            return max(keep_limit, 0)

        most_recent_beta = self.find_most_recent_beta(releases)
        if not most_recent_beta:
            return keep_limit

        sorted_releases = self._get_sorted_releases_with_tags(releases)
        if most_recent_beta in sorted_releases[:keep_limit]:
            return keep_limit

        try:
            beta_index = sorted_releases.index(most_recent_beta)
        except ValueError:
            return keep_limit

        return min(beta_index + 1, len(sorted_releases))

    def _get_sorted_releases_with_tags(self, releases: List[Release]) -> List[Release]:
        """
        Filter the given releases to those with a `tag_name` and sort them newest-first.

        Parameters:
            releases (List[Release]): Iterable of release objects to filter and sort.

        Returns:
            List[Release]: Releases that include a `tag_name`, sorted by newest published first.
        """
        return sorted(
            (release for release in releases if release.tag_name),
            key=get_release_sorting_key,
            reverse=True,
        )

    def get_releases_for_summary(
        self, releases: List[Release], *, keep_limit: Optional[int] = None
    ) -> List[Release]:
        """
        Determine which releases should appear in summary reports based on the keep limit.

        Parameters:
            releases: Raw release list to consider.
            keep_limit: Maximum number of releases to include; when None, all releases are returned.

        Returns:
            List of releases sorted newest-first and limited to `keep_limit` when provided.
        """
        sorted_releases = self._get_sorted_releases_with_tags(releases)
        if keep_limit is None:
            return sorted_releases
        limit = max(0, keep_limit)
        return sorted_releases[:limit]

    def _format_release_label_with_keep(
        self,
        release: Release,
        *,
        include_channel: bool = True,
        include_status: bool = True,
        _include_stable: bool = False,
        is_kept: bool = False,
    ) -> str:
        """
        Create a human-readable label for a release, optionally marking it as kept and appending channel and revoked annotations.

        Parameters:
            release (Release): Release whose tag, channel, and status are used to construct the label.
            include_channel (bool): If true, append the detected release channel (e.g., "alpha", "beta") in parentheses.
            include_status (bool): If true, append "revoked" in parentheses when the release is detected as revoked.
            _include_stable (bool): Kept for backward compatibility; ignored by this formatter.
            is_kept (bool): If true, prefix the label with "[KEEP] " to indicate the release is being retained.

        Returns:
            str: The formatted label containing the release tag (or "<unknown>"), optionally prefixed with "[KEEP]" and followed by parenthesized annotations.
        """
        label = release.tag_name or "<unknown>"
        if is_kept:
            label = f"[KEEP] {label}"
        parts: List[str] = []
        if include_channel:
            channel = self.get_release_channel(release)
            if channel:
                parts.append(channel)
        if include_status and self.is_release_revoked(release):
            parts.append(STATUS_REVOKED)
        if parts:
            label = f"{label} ({', '.join(parts)})"
        return label

    def format_release_label(
        self,
        release: Release,
        *,
        include_channel: bool = True,
        include_status: bool = True,
        _include_stable: bool = False,
    ) -> str:
        """
        Build a human-readable label for a release combining its tag with optional channel and status annotations.

        Parameters:
            release: Release object used to obtain tag, channel, and revoked status.
            include_channel (bool): If true, append the detected release channel (for example, "alpha" or "beta").
            include_status (bool): If true, append a revoked status annotation when the release is detected as revoked.
            _include_stable (bool): Deprecated compatibility flag; "stable" annotations are not emitted regardless of this value.

        Returns:
            A string containing the release tag followed by optional parenthesized annotations (for example, "v1.2.3 (alpha, revoked)" or "v1.2.3").
        """
        return self._format_release_label_with_keep(
            release,
            include_channel=include_channel,
            include_status=include_status,
            _include_stable=_include_stable,
            is_kept=False,
        )

    def format_release_log_suffix(self, release: Release) -> str:
        """
        Get the formatted label suffix for a release when it differs from the release's tag name.

        Parameters:
            release (Release): Release whose formatted label is compared to its tag.

        Returns:
            str: Substring of the formatted label that follows `release.tag_name`, or an empty string if they are equal.
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
        Log a concise summary of releases marked revoked or removed from a history dictionary.

        Reports revoked entries always and reports removed entries. If `history["entries"]` is missing
        or not a dict, or if there are no revoked/removed entries to report,
        the function returns without logging.

        Parameters:
            history (Dict[str, Any]): History object containing an "entries" mapping of
                release entries keyed by tag. Each entry should include a "status" field.
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
        self, releases: List[Release], *, label: str, keep_limit: Optional[int] = None
    ) -> None:
        """
        Log a per-channel summary and list releases grouped by their inferred channel.

        Builds a compact channel count string using the preferred order (alpha, beta, rc, then other channels alphabetically) and logs a header plus a per-channel line listing releases for each non-empty channel. If keep_limit is provided it is applied as a cap to the releases considered for the header (the header shows how many of the total are being kept); when keep_limit is provided and results would be empty, a fallback to the full sorted set may be used to produce the summary.

        Parameters:
            releases (List[Release]): Releases to summarize; releases without a tag are ignored by the summary.
            label (str): Prefix label used in the logged summary message.
            keep_limit (Optional[int]): Optional cap on how many releases are considered "kept" for the summary header; if None no cap is applied.
        """
        if not releases:
            return

        sorted_releases = self._get_sorted_releases_with_tags(releases)
        filtered_releases = (
            sorted_releases
            if keep_limit is None
            else sorted_releases[: max(0, keep_limit)]
        )

        display_channel_map = self._build_channel_map(filtered_releases)
        summary_parts = self._build_summary_parts(display_channel_map)
        # Fallback to showing all available channels when keep_limit <= 0 to provide
        # useful information even though no releases will be retained
        if (
            not summary_parts
            and keep_limit is not None
            and keep_limit <= 0
            and sorted_releases
        ):
            display_channel_map = self._build_channel_map(sorted_releases)
            summary_parts = self._build_summary_parts(display_channel_map)

        if not summary_parts:
            return

        total_releases = len(sorted_releases)
        kept_count = len(filtered_releases)
        if keep_limit is not None:
            logger.info(
                "%s release channels (keeping %d of %d): %s",
                label,
                kept_count,
                total_releases,
                ", ".join(summary_parts),
            )
        else:
            logger.info("%s release channels: %s", label, ", ".join(summary_parts))

        for channel in _CHANNEL_ORDER:
            releases_for_channel = display_channel_map.get(channel)
            if not releases_for_channel:
                continue
            self._log_channel_releases(channel, releases_for_channel)

        for channel in sorted(set(display_channel_map) - set(_CHANNEL_ORDER)):
            releases_for_channel = display_channel_map.get(channel)
            if not releases_for_channel:
                continue
            self._log_channel_releases(channel, releases_for_channel)

    def _build_summary_parts(self, channel_map: Dict[str, List[Release]]) -> List[str]:
        """
        Build an ordered summary list of channel counts from a mapping of channel names to releases.

        Parameters:
            channel_map (Dict[str, List[Release]]): Mapping from channel name to list of releases for that channel.

        Returns:
            List[str]: Ordered list of strings of the form "channel=count". Channels in _CHANNEL_ORDER appear first (in that order), followed by any remaining channels sorted alphabetically; channels with a count of zero are omitted.
        """
        parts: List[str] = []
        for channel in _CHANNEL_ORDER:
            count = len(channel_map.get(channel, []))
            if count:
                parts.append(f"{channel}={count}")
        for channel in sorted(set(channel_map) - set(_CHANNEL_ORDER)):
            count = len(channel_map.get(channel, []))
            if count:
                parts.append(f"{channel}={count}")
        return parts

    def _build_channel_map(
        self, releases_to_map: List[Release]
    ) -> Dict[str, List[Release]]:
        """
        Group releases by their detected release channel.

        Parameters:
            releases_to_map (List[Release]): Releases to group by channel.

        Returns:
            Dict[str, List[Release]]: Mapping from channel label (e.g., "alpha", "beta", "rc") to list of releases belonging to that channel. The order of releases within each list follows their order in the input.
        """
        channel_map: Dict[str, List[Release]] = {}
        for release in releases_to_map:
            channel = self.get_release_channel(release)
            channel_map.setdefault(channel, []).append(release)
        return channel_map

    def _log_channel_releases(
        self, channel: str, releases_for_channel: List[Release]
    ) -> None:
        """
        Log a comma-separated list of releases for a specific channel, newest first.

        Formats each release using the manager's label formatter (omitting the channel and including status) and emits a single info-level line like "  - {channel}: {label1}, {label2}, …".

        Parameters:
            channel (str): Channel name to log (e.g., "alpha", "beta", "rc").
            releases_for_channel (List[Release]): Releases belonging to the channel; releases without tags are ignored.
        """
        items = ", ".join(
            self.format_release_label(
                release, include_channel=False, include_status=True
            )
            for release in releases_for_channel
            if release.tag_name
        )
        logger.info("  - %s: %s", channel, items)

    def _log_release_status_entry(self, entry: Dict[str, Any]) -> None:
        """
        Log a single release history entry as a formatted status line.

        Parameters:
            entry (dict): History entry with optional keys:
                - tag_name (str): Release tag to display; "<unknown>" is used if missing.
                - channel (str): Channel label to include in the output.
                - status (str): Status label; entries with the revoked status are rendered with revoked styling.
        """
        tag_name = entry.get("tag_name") or "<unknown>"
        channel = entry.get("channel")
        parts = []
        if channel:
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
            entries (List[Dict[str, Any]]): List of entry objects. Each entry may contain keys `"published_at"` (ISO-8601 string) and `"tag_name"` (string).

        Returns:
            List[Dict[str, Any]]: The input entries sorted by published date (newest first) and then by tag name.
        """

        def _sort_key(entry: Dict[str, Any]) -> tuple[datetime, str]:
            """
            Produce a sorting key for a history entry using its published timestamp and tag name.

            Parameters:
                entry (dict): A history entry dictionary; expected keys include "published_at" (ISO 8601 string) and "tag_name".

            Returns:
                tuple(datetime, str): A tuple where the first element is the parsed UTC datetime from "published_at" (or datetime.min with UTC if missing or invalid) and the second element is the "tag_name" (or an empty string).
            """
            ts = parse_iso_datetime_utc(
                entry.get("published_at")
            ) or datetime.min.replace(tzinfo=timezone.utc)
            tag_name = entry.get("tag_name") or ""
            return (ts, tag_name)

        return sorted(entries, key=_sort_key, reverse=True)
