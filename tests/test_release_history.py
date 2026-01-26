# Tests for release history tracking utilities.
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fetchtastic import log_utils
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Release
from fetchtastic.download.release_history import (
    ReleaseHistoryManager,
    _join_text,
    detect_release_channel,
    is_release_revoked,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def test_detect_release_channel_from_name():
    release = Release(
        tag_name="v2.0.0",
        prerelease=False,
        name="Meshtastic Firmware 2.0.0 Beta",
    )

    assert detect_release_channel(release) == "beta"


def test_detect_release_channel_stable_maps_to_beta():
    # "Stable" is treated as beta to avoid emitting a stable channel label.
    release = Release(
        tag_name="v2.0.1",
        prerelease=False,
        name="Meshtastic Firmware 2.0.1 Stable",
    )

    assert detect_release_channel(release) == "beta"


def test_join_text_filters_non_strings():
    parts = [None, "  Alpha  ", "", " ", 123, "Beta"]

    assert _join_text(parts) == "alpha beta"


def test_detect_release_channel_ignores_body():
    release = Release(
        tag_name="v2.1.0",
        prerelease=False,
        body="This is a Beta preview release.",
    )

    assert detect_release_channel(release) == "alpha"


def test_is_release_revoked_from_body():
    release = Release(
        tag_name="v2.0.1",
        prerelease=False,
        body="This release was revoked due to regressions.",
    )

    assert is_release_revoked(release) is True


def test_is_release_revoked_parses_body_lines():
    release = Release(
        tag_name="v2.0.3",
        prerelease=False,
        body="\n> \n> !!! Revoked due to regressions.\n",
    )

    assert is_release_revoked(release) is True


def test_is_release_revoked_ignores_previous_revocation():
    release = Release(
        tag_name="v2.0.2",
        prerelease=False,
        body="If you installed the previously revoked 2.0.1 release, upgrade.",
    )

    assert is_release_revoked(release) is False


def test_is_release_revoked_skips_previously_revoked_prefix():
    release = Release(
        tag_name="v2.0.4",
        prerelease=False,
        body="Previously revoked release mentioned here.",
    )

    assert is_release_revoked(release) is False


def test_format_release_label_and_suffix(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_labels")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    release = Release(
        tag_name="v3.0.0",
        prerelease=False,
        name="Meshtastic Firmware 3.0.0 Alpha (Revoked)",
    )

    label = manager.format_release_label(
        release, include_channel=True, include_status=True
    )
    assert label == "v3.0.0 (alpha, revoked)"

    stable_release = Release(tag_name="v3.1.0", prerelease=False)
    assert manager.format_release_log_suffix(stable_release) == " (alpha)"


def test_format_release_label_includes_channel_when_present(tmp_path):
    # Exercise the channel-append path without revoked status.
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_channel")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    release = Release(tag_name="v4.0.0", prerelease=False)
    manager.get_release_channel = lambda _release: "beta"

    assert (
        manager.format_release_label(release, include_status=False) == "v4.0.0 (beta)"
    )


def test_format_release_label_skips_empty_channel(tmp_path):
    # Ensure empty channel values do not add an annotation.
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_no_channel")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    release = Release(tag_name="v4.1.0", prerelease=False)
    manager.get_release_channel = lambda _release: ""

    assert manager.format_release_label(release, include_status=False) == "v4.1.0"


def test_log_release_status_entry_includes_channel_and_status(tmp_path, mocker):
    # Directly cover the channel/status assembly in the log helper.
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_log_entry")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    mock_logger = mocker.patch("fetchtastic.download.release_history.logger")

    manager._log_release_status_entry(
        {
            "tag_name": "v5.0.0",
            "channel": "alpha",
            "status": "revoked",
        }
    )

    mock_logger.info.assert_any_call(
        "  - [%s][strike]%s[/strike][/%s]%s",
        "yellow",
        "v5.0.0",
        "yellow",
        " (alpha, revoked)",
    )


def test_log_release_status_entry_skips_empty_parts(tmp_path, mocker):
    # Cover the empty channel/status branches in the logger helper.
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_log_empty")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    mock_logger = mocker.patch("fetchtastic.download.release_history.logger")

    manager._log_release_status_entry(
        {
            "tag_name": "v6.0.0",
            "channel": "",
            "status": "",
        }
    )

    mock_logger.info.assert_any_call(
        "  - [%s][strike]%s[/strike][/%s]%s",
        "red",
        "v6.0.0",
        "red",
        "",
    )


def test_format_release_log_suffix_includes_annotations(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_suffix")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    release = Release(
        tag_name="v3.2.0",
        prerelease=False,
        name="Meshtastic Firmware 3.2.0 Beta (Revoked)",
    )

    assert manager.format_release_log_suffix(release) == " (beta, revoked)"


def test_update_release_history_marks_revoked_and_removed(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_test")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    cache_manager.atomic_write_json(
        history_path,
        {
            "entries": {
                "v1.0.0": {
                    "tag_name": "v1.0.0",
                    "published_at": "2024-02-01T00:00:00Z",
                    "status": "active",
                }
            }
        },
    )

    releases = [
        Release(
            tag_name="v2.0.0",
            prerelease=False,
            published_at="2024-01-01T00:00:00Z",
            name="Meshtastic Firmware 2.0.0",
        ),
        Release(
            tag_name="v2.0.1",
            prerelease=False,
            published_at="2024-01-02T00:00:00Z",
            name="(Revoked)",
            body="Revoked due to regressions.",
        ),
    ]

    history = manager.update_release_history(releases)

    assert history["entries"]["v2.0.0"]["status"] == "active"
    assert history["entries"]["v2.0.1"]["status"] == "revoked"
    assert history["entries"]["v1.0.0"]["status"] == "removed"
    assert history["entries"]["v1.0.0"]["removed_at"] is not None


def test_update_release_history_skips_empty_tag_and_updates_status(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_update")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    cache_manager.atomic_write_json(
        history_path,
        {
            "entries": {
                "v1.0.0": {
                    "tag_name": "v1.0.0",
                    "published_at": "2024-01-01T00:00:00Z",
                    "status": "active",
                    "removed_at": "2024-01-05T00:00:00Z",
                }
            }
        },
    )

    releases = [
        Release(tag_name="", prerelease=False),
        Release(
            tag_name="v1.0.0",
            prerelease=False,
            name="Alpha (Revoked)",
        ),
    ]

    history = manager.update_release_history(releases)

    assert "" not in history["entries"]
    entry = history["entries"]["v1.0.0"]
    assert entry["status"] == "revoked"
    assert "status_updated_at" in entry
    assert "removed_at" not in entry


def test_update_release_history_removal_checks_and_write_failure(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_removal")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    cache_manager.atomic_write_json(
        history_path,
        {
            "entries": {
                "v0.9.0": {
                    "tag_name": "v0.9.0",
                    "published_at": "2020-01-01T00:00:00Z",
                    "status": "active",
                },
                "v0.8.0": {
                    "tag_name": "v0.8.0",
                    "published_at": "2020-01-01T00:00:00Z",
                    "status": "removed",
                },
            }
        },
    )

    release = Release(
        tag_name="v1.0.0",
        prerelease=False,
        published_at="2024-01-01T00:00:00Z",
    )

    with patch.object(log_utils.logger, "debug") as mock_debug:
        manager.cache_manager.atomic_write_json = lambda *_args, **_kwargs: False
        history = manager.update_release_history([release])

    assert history["entries"]["v0.9.0"]["status"] == "active"
    assert history["entries"]["v0.8.0"]["status"] == "removed"
    assert mock_debug.called


def test_log_release_status_summary_and_entry(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_logs")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    history = {
        "entries": {
            "v1.0.0": {
                "tag_name": "v1.0.0",
                "channel": "alpha",
                "status": "revoked",
                "published_at": "2024-01-02T00:00:00Z",
            },
            "v0.9.0": {
                "tag_name": "v0.9.0",
                "channel": "beta",
                "status": "removed",
                "published_at": "2024-01-01T00:00:00Z",
            },
            "v0.8.0": {
                "tag_name": "v0.8.0",
                "channel": "beta",
                "status": "removed",
                "published_at": "2023-01-01T00:00:00Z",
            },
        }
    }

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_status_summary(history, label="Firmware")

    assert mock_info.called

    logged_calls = [str(call) for call in mock_info.call_args_list]
    logged_text = " ".join(logged_calls)
    assert "v1.0.0" in logged_text
    assert "v0.9.0" in logged_text
    assert "v0.8.0" in logged_text


def test_log_release_status_summary_invalid_entries(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_invalid")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    history = {"entries": ["v1.0.0"]}

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_status_summary(history, label="Firmware")

    assert not mock_info.called


def test_log_release_status_summary_without_revoked_or_removed(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_clean")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    history = {
        "entries": {
            "v1.0.0": {"tag_name": "v1.0.0", "status": "active"},
        }
    }

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_status_summary(history, label="Firmware")

    assert not mock_info.called


def test_log_release_channel_summary_with_custom_channel(tmp_path, monkeypatch):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_channels")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(tag_name="v1.0.0", prerelease=False, name="Alpha"),
        Release(tag_name="v1.1.0", prerelease=False),
    ]

    def _fake_channel(release):
        return "alpha" if release.tag_name == "v1.0.0" else "custom"

    monkeypatch.setattr(manager, "get_release_channel", _fake_channel)

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_channel_summary(releases, label="Firmware")

    assert mock_info.called


def test_log_release_channel_summary_empty(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_empty")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_channel_summary([], label="Firmware")

    assert not mock_info.called


def test_log_release_channel_summary_all_missing_tags(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_missing_tags")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [Release(tag_name="", prerelease=False)]

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_channel_summary(releases, label="Firmware")

    assert not mock_info.called


def test_format_release_label_with_keep_unknown_tag(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_label")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    release = Release(tag_name="", prerelease=False)

    label = manager._format_release_label_with_keep(
        release,
        include_channel=False,
        include_status=False,
        is_kept=True,
    )

    assert label == "[KEEP] <unknown>"


def test_log_release_channel_summary_negative_keep_limit(tmp_path, monkeypatch):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_negative_keep")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(tag_name="v1.0.0", prerelease=False),
        Release(tag_name="v1.1.0", prerelease=False),
    ]

    monkeypatch.setattr(manager, "get_release_channel", lambda _release: "alpha")

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_channel_summary(releases, label="Firmware", keep_limit=-1)

    mock_info.assert_any_call(
        "%s release channels (keeping %d of %d): %s",
        "Firmware",
        0,
        2,
        "alpha=2",
    )


def test_log_release_channel_summary_custom_empty_group(tmp_path, monkeypatch):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_custom_empty")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(tag_name="v1.0.0", prerelease=False),
        Release(tag_name="", prerelease=False),
    ]

    def _fake_channel(release):
        return "custom" if not release.tag_name else "alpha"

    monkeypatch.setattr(manager, "get_release_channel", _fake_channel)

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_release_channel_summary(releases, label="Firmware")

    assert mock_info.called


def test_get_releases_for_summary_limits_and_sorts(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_summary_limit")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(
            tag_name="v1.0.0", prerelease=False, published_at="2024-01-01T00:00:00Z"
        ),
        Release(
            tag_name="v2.0.0", prerelease=False, published_at="2024-03-01T00:00:00Z"
        ),
        Release(
            tag_name="v1.1.0", prerelease=False, published_at="2024-02-01T00:00:00Z"
        ),
    ]

    limited = manager.get_releases_for_summary(releases, keep_limit=2)
    assert [release.tag_name for release in limited] == ["v2.0.0", "v1.1.0"]

    assert manager.get_releases_for_summary(releases, keep_limit=-1) == []
    assert [
        release.tag_name for release in manager.get_releases_for_summary(releases)
    ] == [
        "v2.0.0",
        "v1.1.0",
        "v1.0.0",
    ]


def test_log_duplicate_base_versions(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_dupes")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(tag_name="v1.2.3.aaaaaaa", prerelease=False),
        Release(tag_name="v1.2.3.bbbbbbb", prerelease=False),
    ]

    with patch.object(log_utils.logger, "warning") as mock_warning:
        manager.log_duplicate_base_versions(releases, label="Firmware")

    assert mock_warning.called


def test_log_duplicate_base_versions_skips_invalid_entries(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_dupes_skip")
    manager = ReleaseHistoryManager(cache_manager, history_path)
    releases = [
        Release(tag_name="", prerelease=False),
        Release(tag_name="v", prerelease=False),
        Release(tag_name="v1.0.0", prerelease=False),
    ]

    with patch.object(log_utils.logger, "info") as mock_info:
        manager.log_duplicate_base_versions(releases, label="Firmware")

    assert not mock_info.called


def test_should_mark_removed_and_sort_entries(tmp_path):
    cache_manager = CacheManager(cache_dir=str(tmp_path))
    history_path = cache_manager.get_cache_file_path("release_history_helpers")
    manager = ReleaseHistoryManager(cache_manager, history_path)

    assert (
        manager._should_mark_removed({"published_at": "2024-01-01T00:00:00Z"}, None)
        is False
    )
    assert (
        manager._should_mark_removed({"published_at": None}, datetime.now(timezone.utc))
        is False
    )

    entries = [
        {"tag_name": "v1.0.0", "published_at": None},
        {"tag_name": "v2.0.0", "published_at": "2024-01-02T00:00:00Z"},
    ]
    sorted_entries = manager._sort_entries(entries)

    assert sorted_entries[0]["tag_name"] == "v2.0.0"
