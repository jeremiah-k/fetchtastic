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

_REVOKED_RX = re.compile(r"\brevoked\b", re.IGNORECASE)
_CHANNEL_RX = {
    "alpha": re.compile(r"\balpha\b", re.IGNORECASE),
    "beta": re.compile(r"\bbeta\b", re.IGNORECASE),
    "rc": re.compile(r"\b(?:rc|release candidate)\b", re.IGNORECASE),
}


def _join_text(parts: Iterable[Optional[str]]) -> str:
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
    Determine the release channel from name/tag/body and prerelease flag.

    Returns:
        str: "alpha", "beta", "rc", "prerelease", or "stable".
    """
    primary_text = _join_text([release.name, release.tag_name])
    for label, rx in _CHANNEL_RX.items():
        if rx.search(primary_text):
            return label

    body_text = _join_text([release.body])
    for label, rx in _CHANNEL_RX.items():
        if rx.search(body_text):
            return label

    if release.prerelease:
        return CHANNEL_PRERELEASE

    return CHANNEL_STABLE


def is_release_revoked(release: Release) -> bool:
    """
    Determine whether a release is revoked by scanning name/body text.
    """
    return bool(_REVOKED_RX.search(_join_text([release.name, release.body])))


class ReleaseHistoryManager:
    """
    Manage release history files for revoked/removed tracking and summary logging.
    """

    def __init__(self, cache_manager: CacheManager, history_path: str) -> None:
        self.cache_manager = cache_manager
        self.history_path = history_path
        self.version_manager = VersionManager()

    def get_release_channel(self, release: Release) -> str:
        return detect_release_channel(release)

    def is_release_revoked(self, release: Release) -> bool:
        return is_release_revoked(release)

    def format_release_label(
        self,
        release: Release,
        *,
        include_channel: bool = True,
        include_status: bool = True,
        include_stable: bool = False,
    ) -> str:
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
        suffix = self.format_release_label(
            release, include_channel=True, include_status=True
        )
        if suffix == release.tag_name:
            return ""
        return suffix[len(release.tag_name) :]

    def update_release_history(self, releases: List[Release]) -> Dict[str, Any]:
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
                    "name": release.name or "",
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
        for entry in self._sort_entries(revoked + removed):
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
            logger.info(
                "%s: multiple releases share base version %s: %s",
                label,
                base_version,
                items,
            )

    def _get_base_version(self, tag_name: str) -> str:
        clean = self.version_manager.extract_clean_version(tag_name) or tag_name
        return clean.lstrip("vV")

    def _get_oldest_published_at(self, releases: List[Release]) -> Optional[datetime]:
        timestamps = [
            parse_iso_datetime_utc(release.published_at) for release in releases
        ]
        timestamps = [ts for ts in timestamps if ts is not None]
        if not timestamps:
            return None
        return min(timestamps)

    def _should_mark_removed(
        self, entry: Dict[str, Any], oldest_published: Optional[datetime]
    ) -> bool:
        if oldest_published is None:
            return False
        published_at = parse_iso_datetime_utc(entry.get("published_at"))
        if published_at is None:
            return False
        return published_at >= oldest_published

    def _sort_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _sort_key(entry: Dict[str, Any]) -> tuple[datetime, str]:
            ts = parse_iso_datetime_utc(
                entry.get("published_at")
            ) or datetime.min.replace(tzinfo=timezone.utc)
            tag_name = entry.get("tag_name") or ""
            return (ts, tag_name)

        return sorted(entries, key=_sort_key, reverse=True)
