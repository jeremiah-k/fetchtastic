"""
Regression suite for the highest-active client-app prerelease policy.

These tests pin the intended selection behaviour for Android client-app
prereleases (the legacy ``-open.N`` / ``-closed.N`` tag tracks) and the
consistency invariant between ``MeshtasticClientAppDownloader.handle_prereleases``
and ``MeshtasticClientAppDownloader.get_latest_prerelease_tag``.

Intended policy:
- Consider classified non-snapshot prereleases only.
- Release tuples are compared by a shared width-6 normalized form so that
  semantically equal bases (v2.7 == v2.7.0 == v2.7.0.0) compare equal.
- When a parseable stable release exists, discard prerelease bases whose
  normalized base is <= the normalized stable base, then retain every tag
  on the single highest newer parseable base.
- When no parseable stable exists, retain every tag on the highest
  parseable prerelease base.
- When no candidate base parses, preserve every classified candidate.
- Output order is deterministic: published_at descending with a tag-name
  fallback so equal timestamps never depend on input order.
- Recent SHA filtering is a later step inside handle_prereleases and does
  not affect the base-selection invariants pinned here.

The semantic normalization cases below pin width-6 base comparison via the
shared helper in client_app.py; the production-shaped cases (Case 1, Case
11) guard the ordinary higher-base selection path.
"""

import pytest

from fetchtastic.download.cache import CacheManager
from fetchtastic.download.client_app import MeshtasticClientAppDownloader
from fetchtastic.download.interfaces import Asset, Release

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]

# Realistic Android release asset name used across the client-app suite.
_APK_NAME = "app-fdroid-universal-release.apk"


def _apk_release(tag_name: str, *, prerelease: bool, published_at: str) -> Release:
    """Build a minimal Release carrying one Android APK asset."""
    return Release(
        tag_name=tag_name,
        prerelease=prerelease,
        published_at=published_at,
        assets=[
            Asset(
                name=_APK_NAME, download_url="https://example.invalid/app.apk", size=1
            )
        ],
    )


def _stable(tag_name: str, published_at: str) -> Release:
    """Build a genuine stable client-app release (prerelease flag False)."""
    return _apk_release(tag_name, prerelease=False, published_at=published_at)


def _pre(tag_name: str, published_at: str) -> Release:
    """Build a classified client-app prerelease (prerelease flag True)."""
    return _apk_release(tag_name, prerelease=True, published_at=published_at)


def _snapshot(published_at: str) -> Release:
    """Build the rolling snapshot debug-build release."""
    return Release(
        tag_name="snapshot",
        prerelease=True,
        published_at=published_at,
        assets=[
            Asset(
                name="androidApp-fdroid-universal-debug-29321447.apk",
                download_url="https://example.invalid/snapshot.apk",
                size=1,
            )
        ],
    )


@pytest.fixture
def cache_manager(tmp_path):
    """Real CacheManager rooted at tmp_path (no network, file-safe)."""
    return CacheManager(cache_dir=str(tmp_path))


@pytest.fixture
def downloader(tmp_path, cache_manager):
    """Client-app downloader with prerelease checking enabled."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_CLIENT_APPS": True,
        "SELECTED_APP_ASSETS": [_APK_NAME],
        "APP_VERSIONS_TO_KEEP": 1,
        "CHECK_APP_PRERELEASES": True,
        "EXCLUDE_PATTERNS": [],
    }
    return MeshtasticClientAppDownloader(config, cache_manager)


# ---------------------------------------------------------------------------
# Case 1: stable v2.7.14 present, closed prerelease on newer base v2.8.0
# ---------------------------------------------------------------------------


def test_case1_newer_base_closed_prerelease_selected(downloader):
    """
    Given a parseable stable v2.7.14 and a v2.8.0-closed.8 prerelease,
    When handle_prereleases selects the active set,
    Then the v2.8.0-closed.8 prerelease is retained (its base 2.8.0 > 2.7.14).
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert "v2.8.0-closed.8" in selected


# ---------------------------------------------------------------------------
# Case 2: same-base superseded prereleases excluded when a newer base exists
# ---------------------------------------------------------------------------


def test_case2_superseded_base_prereleases_excluded(downloader):
    """
    Given stable v2.7.14 and prereleases on bases 2.7.15 and 2.8.0,
    When handle_prereleases selects the active set,
    Then only prereleases on the highest newer base (2.8.0) are retained and
    every tag on the superseded 2.7.15 base is excluded.
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.7.15-closed.3", "2024-02-01T00:00:00Z"),
        _pre("v2.7.15-open.5", "2024-02-10T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert "v2.8.0-closed.8" in selected
    assert "v2.7.15-closed.3" not in selected
    assert "v2.7.15-open.5" not in selected


# ---------------------------------------------------------------------------
# Case 3: next-patch prerelease selected over current stable
# ---------------------------------------------------------------------------


def test_case3_next_patch_prerelease_selected(downloader):
    """
    Given stable v2.7.14 and a v2.7.15-closed.5 prerelease (the immediate
    next patch base), When handle_prereleases selects the active set,
    Then v2.7.15-closed.5 is retained (base 2.7.15 > stable 2.7.14).
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.7.15-closed.5", "2024-02-01T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == ["v2.7.15-closed.5"]


# ---------------------------------------------------------------------------
# Case 4: only the highest of bases 2.7.15 / 2.8.0 is retained
# ---------------------------------------------------------------------------


def test_case4_only_highest_base_retained(downloader):
    """
    Given stable v2.7.14 and one prerelease on each of bases 2.7.15 and
    2.8.0, When handle_prereleases selects the active set,
    Then only the 2.8.0-base prerelease is retained.
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.7.15-open.3", "2024-02-01T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == ["v2.8.0-closed.8"]


# ---------------------------------------------------------------------------
# Case 5: both -open and -closed tags on the winning base are retained
# ---------------------------------------------------------------------------


def test_case5_open_and_closed_tags_on_winning_base_retained(downloader):
    """
    Given stable v2.7.14 and two v2.8.0 prereleases (one -open, one
    -closed), When handle_prereleases selects the active set,
    Then both tags on the winning v2.8.0 base are retained.
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.8.0-open.5", "2024-02-15T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = sorted(r.tag_name for r in downloader.handle_prereleases(releases))

    assert selected == ["v2.8.0-closed.8", "v2.8.0-open.5"]


# ---------------------------------------------------------------------------
# Case 6: deterministic published_at desc ordering with tag fallback
# ---------------------------------------------------------------------------


def test_case6_ordering_is_deterministic_across_input_permutations(downloader):
    """
    Given two prereleases sharing the same published_at timestamp, When
    handle_prereleases selects the active set from two different input
    orderings, Then the output tag sequence is identical for both inputs
    (ordering must not depend on input order).
    """
    # Use the next-patch base (2.7.15) so both prereleases survive selection
    # and their relative order is what the tiebreak must make deterministic.
    shared_published_at = "2024-02-20T00:00:00Z"
    first_input = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.7.15-closed.7", shared_published_at),
        _pre("v2.7.15-closed.8", shared_published_at),
    ]
    permuted_input = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.7.15-closed.8", shared_published_at),
        _pre("v2.7.15-closed.7", shared_published_at),
    ]

    selected_first = [r.tag_name for r in downloader.handle_prereleases(first_input)]
    selected_permuted = [
        r.tag_name for r in downloader.handle_prereleases(permuted_input)
    ]

    assert selected_first == selected_permuted


# ---------------------------------------------------------------------------
# Case 7: no stable release -> select the highest parseable prerelease base
# ---------------------------------------------------------------------------


def test_case7_no_stable_selects_highest_parseable_base(downloader):
    """
    Given no stable release and prereleases on parseable bases 2.7.15 and
    2.8.0, When handle_prereleases selects the active set,
    Then only the highest parseable base (2.8.0) prerelease is retained.
    """
    releases = [
        _pre("v2.7.15-closed.5", "2024-02-01T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == ["v2.8.0-closed.8"]


# ---------------------------------------------------------------------------
# Case 8: all candidate bases unparseable -> preserve classified candidates
# ---------------------------------------------------------------------------


def test_case8_all_unparseable_bases_preserves_classified_candidates(downloader):
    """
    Given a stable v2.7.14 and classified prereleases whose base versions
    cannot be parsed, When handle_prereleases selects the active set,
    Then every classified candidate is preserved (fallback so unparseable
    prereleases are never silently dropped).
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("vfoo-closed.1", "2024-02-01T00:00:00Z"),
        _pre("vbar-open.2", "2024-02-10T00:00:00Z"),
    ]

    selected = sorted(r.tag_name for r in downloader.handle_prereleases(releases))

    assert selected == ["vbar-open.2", "vfoo-closed.1"]


# ---------------------------------------------------------------------------
# Case 9: snapshot debug-build tag is never classified as an active prerelease
# ---------------------------------------------------------------------------


def test_case9_snapshot_excluded_from_selection(downloader):
    """
    Given the rolling snapshot release alongside a real prerelease and a
    stable, When handle_prereleases selects the active set,
    Then the snapshot tag is absent and the real prerelease is retained.
    """
    # The real prerelease uses the next-patch base so it survives selection
    # and the only behavior under test here is snapshot exclusion.
    releases = [
        _snapshot("2024-02-25T00:00:00Z"),
        _pre("v2.7.15-closed.5", "2024-02-20T00:00:00Z"),
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert "snapshot" not in selected
    assert "v2.7.15-closed.5" in selected


# ---------------------------------------------------------------------------
# Case 10: get_latest_prerelease_tag matches the first shared candidate
# ---------------------------------------------------------------------------


def test_case10_latest_prerelease_tag_matches_first_selected_candidate(downloader):
    """
    Given a release set, When handle_prereleases selects the active set and
    get_latest_prerelease_tag reports a single tag,
    Then the reported tag is either None or exactly the first tag of the
    selected set (the two methods must agree on the leading candidate).
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = downloader.handle_prereleases(releases)
    reported = downloader.get_latest_prerelease_tag(releases)

    if reported is not None:
        assert selected, "reported tag must not exist when selection is empty"
        assert reported == selected[0].tag_name


# ---------------------------------------------------------------------------
# Case 11: production scenario must never report a tag with an empty selection
# ---------------------------------------------------------------------------


def test_case11_production_scenario_no_empty_selection_with_reported_tag(downloader):
    """
    Given a production-shaped release set (realistic timestamps and asset
    layout for the v2.7.14 -> v2.8.0 transition), When handle_prereleases
    selects the active set and get_latest_prerelease_tag reports a tag,
    Then the selection is non-empty and contains the reported tag. The
    production system must never present an empty selection alongside a
    reported prerelease tag.
    """
    releases = [
        _stable("v2.7.14", "2024-12-10T18:32:11Z"),
        _pre("v2.8.0-open.4", "2025-01-08T09:14:00Z"),
        _pre("v2.8.0-closed.8", "2025-01-22T20:47:33Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]
    reported = downloader.get_latest_prerelease_tag(releases)

    assert selected, "selection must not be empty when a prerelease is available"
    assert reported in selected


# ---------------------------------------------------------------------------
# Semantic release-tuple normalization (width-6 base comparison)
#
# These cases pin width-6 normalization of release tuples so that
# semantically equal bases (v2.7 == v2.7.0 == v2.7.0.0) compare equal.
# They exercise the shared width-6 normalization helper in client_app.py;
# the production-shaped cases above guard the ordinary higher-base path.
# ---------------------------------------------------------------------------


def test_norm_stable_v2_7_excludes_prerelease_v2_7_0_open_1(downloader):
    """
    Given a parseable stable v2.7 and a classified prerelease v2.7.0-open.1
    whose base 2.7.0 is semantically equal to the stable base 2.7,
    When handle_prereleases selects the active set and
    get_latest_prerelease_tag reports the leading tag,
    Then the selection is empty and the reported tag is None (the
    prerelease base is <= the stable base once widths are normalized).
    """
    releases = [
        _stable("v2.7", "2024-01-15T00:00:00Z"),
        _pre("v2.7.0-open.1", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]
    reported = downloader.get_latest_prerelease_tag(releases)

    assert selected == []
    assert reported is None


def test_norm_stable_v2_7_0_excludes_prerelease_v2_7_open_1(downloader):
    """
    Given a parseable stable v2.7.0 and a classified prerelease v2.7-open.1
    whose base 2.7 is semantically equal to the stable base 2.7.0,
    When handle_prereleases selects the active set,
    Then the selection is empty (the prerelease base is <= the stable base
    once widths are normalized).
    """
    releases = [
        _stable("v2.7.0", "2024-01-15T00:00:00Z"),
        _pre("v2.7-open.1", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == []


def test_norm_open_and_closed_on_equal_semantic_base_retained(downloader):
    """
    Given an older stable v2.7.14 and two prereleases whose bases 2.8 and
    2.8.0 are semantically equal, When handle_prereleases selects the active
    set, Then both tags are retained on the single winning semantic base in
    deterministic published_at-descending order (tag-name tiebreak).
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.8-open.1", "2024-02-15T00:00:00Z"),
        _pre("v2.8.0-closed.1", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == ["v2.8.0-closed.1", "v2.8-open.1"]


def test_norm_bases_v2_8_v2_8_0_v2_8_0_0_grouped_as_one(downloader):
    """
    Given classified prereleases spelling the same semantic base as v2.8,
    v2.8.0, and v2.8.0.0, When handle_prereleases selects the active set,
    Then all three tags are retained on one semantic base (none is dropped
    to a narrower-width sibling group).
    """
    releases = [
        _pre("v2.8-open.1", "2024-02-01T00:00:00Z"),
        _pre("v2.8.0-closed.2", "2024-02-10T00:00:00Z"),
        _pre("v2.8.0.0-open.3", "2024-02-20T00:00:00Z"),
    ]

    selected = sorted(r.tag_name for r in downloader.handle_prereleases(releases))

    assert selected == ["v2.8-open.1", "v2.8.0-closed.2", "v2.8.0.0-open.3"]


def test_norm_private_stable_helper_returns_width_six_tuple(downloader):
    """
    Given stable candidates v2.7 and v2.7.0, When _latest_stable_release_tuple
    resolves the latest stable, Then the returned tuple is the width-6
    normalized form (2, 7, 0, 0, 0, 0) and the resolution is deterministic
    across input permutations (publication and tag-name tiebreaks).
    """
    later_v2_7 = _stable("v2.7", "2024-02-20T00:00:00Z")
    earlier_v2_7_0 = _stable("v2.7.0", "2024-01-15T00:00:00Z")
    first_input = [later_v2_7, earlier_v2_7_0]
    permuted_input = [earlier_v2_7_0, later_v2_7]

    resolved_first = downloader._latest_stable_release_tuple(first_input)
    resolved_permuted = downloader._latest_stable_release_tuple(permuted_input)

    assert resolved_first == (2, 7, 0, 0, 0, 0)
    assert resolved_permuted == (2, 7, 0, 0, 0, 0)


def test_norm_production_v2_7_14_and_v2_8_0_closed_8_remains_selected(downloader):
    """
    Given the production-shaped stable v2.7.14 and a v2.8.0-closed.8
    prerelease, When handle_prereleases selects the active set,
    Then the v2.8.0-closed.8 prerelease remains selected (the newer
    semantic base survives normalization). This is the green production
    regression guard for the normalization change.
    """
    releases = [
        _stable("v2.7.14", "2024-01-15T00:00:00Z"),
        _pre("v2.8.0-closed.8", "2024-02-20T00:00:00Z"),
    ]

    selected = [r.tag_name for r in downloader.handle_prereleases(releases)]

    assert selected == ["v2.8.0-closed.8"]
