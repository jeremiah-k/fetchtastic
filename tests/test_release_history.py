# Tests for release history tracking utilities.

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.interfaces import Release
from fetchtastic.download.release_history import (
    ReleaseHistoryManager,
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


def test_is_release_revoked_from_body():
    release = Release(
        tag_name="v2.0.1",
        prerelease=False,
        body="This release was revoked due to regressions.",
    )

    assert is_release_revoked(release) is True


def test_is_release_revoked_ignores_previous_revocation():
    release = Release(
        tag_name="v2.0.2",
        prerelease=False,
        body="If you installed the previously revoked 2.0.1 release, upgrade.",
    )

    assert is_release_revoked(release) is False


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
