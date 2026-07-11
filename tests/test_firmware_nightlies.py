"""
Regression-contract suite for opt-in rolling firmware-nightly downloads.

Firmware nightlies are a rolling flat directory published at
``meshtastic.github.io/firmware-nightly`` containing ~487 files and one
release-level manifest (``firmware-<version>.<hash>.json``) that identifies
an immutable build (e.g. ``2.8.0.f52e2ea``).

This feature is **separate** from:
  - the workflow named "nightly" (a CI schedule, not a firmware source);
  - stable firmware releases (GitHub Releases API);
  - prerelease firmware directories (``firmware-<version>.<hash>`` dirs).

It is opt-in, snapshot-like in lifecycle, and reuses the firmware
GitHub-contents transport and asset-selection patterns.

These tests lock the implemented nightly contract: any failure indicates a
regression (a required constant or method went missing, or documented
behavior changed).  Each symbol is accessed via ``getattr``/``hasattr`` so
the module collects cleanly even when an API is partially absent.
"""

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from fetchtastic import constants
from fetchtastic.constants import (
    FIRMWARE_DIR_NAME,
    FIRMWARE_PRERELEASES_DIR_NAME,
    LATEST_POINTER_NAME,
)
from fetchtastic.download.cache import CacheManager
from fetchtastic.download.firmware import FirmwareReleaseDownloader
from fetchtastic.download.interfaces import DownloadResult
from fetchtastic.download.prerelease_history import PrereleaseHistoryManager

pytestmark = [pytest.mark.core_downloads]

# ------------------------------------------------------------------
# Nightly API surface — constants (defined in fetchtastic.constants)
# ------------------------------------------------------------------
# DEFAULT_CHECK_FIRMWARE_NIGHTLIES = False
# FIRMWARE_NIGHTLIES_DIR_NAME = "nightlies"
# LATEST_FIRMWARE_NIGHTLY_JSON_FILE = "latest_firmware_nightly.json"
# FILE_TYPE_FIRMWARE_NIGHTLY = "firmware_nightly"
# FIRMWARE_NIGHTLY_SOURCE_DIR = "firmware-nightly"
# DEFAULT_FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP = 1

# Nightly API surface — methods on FirmwareReleaseDownloader:
#   fetch_firmware_nightlies() -> list[dict]
#   parse_nightly_build_id(name) -> str | None      (static)
#   get_nightly_build_id(entries) -> str | None
#   should_download_nightly(build_id) -> bool
#   should_process_nightly(entries, build_id) -> bool
#   update_nightly_tracking(build_id) -> bool
#   get_selected_nightly_assets(entries) -> list[dict]
#   is_nightly_complete(build_id) -> bool
#   get_nightly_target_path(build_id, name, *, create=False) -> str
#   download_nightly_asset(entry, build_id) -> DownloadResult
#   cleanup_superseded_nightlies(current_build_id=None) -> int
#   _ensure_nightly_base_dir() -> str
#   _resolve_nightly_dir(build_id) -> str


_NOT_IMPL = "firmware-nightly regression: required API missing"


def _require_method(obj: object, name: str) -> Any:
    """Return ``obj.name`` or fail with a clear missing-API message."""
    method = getattr(obj, name, None)
    assert callable(method), f"{type(obj).__name__}.{name} missing — {_NOT_IMPL}"
    return method


def _require_constant(name: str, expected: Any) -> Any:
    """Return ``constants.name`` or fail if absent / wrong value."""
    value = getattr(constants, name, None)
    assert value is not None, f"constants.{name} missing — {_NOT_IMPL}"
    assert value == expected, f"constants.{name} expected {expected!r}, got {value!r}"
    return value


# ==================================================================
# GitHub Contents entry helpers (model the live API shape accurately)
# ==================================================================

_RAW_BASE = (
    "https://raw.githubusercontent.com/meshtastic/meshtastic.github.io"
    "/main/firmware-nightly"
)


def _contents_entry(name: str, size: int = 1000, entry_type: str = "file") -> dict:
    """Build a GitHub Contents API entry for a file in firmware-nightly/."""
    return {
        "name": name,
        "path": f"firmware-nightly/{name}",
        "download_url": f"{_RAW_BASE}/{name}",
        "size": size,
        "type": entry_type,
    }


# The immutable build identity for the production 2.8 nightly fixture.
BUILD_2_8_0 = "2.8.0.f52e2ea"
MANIFEST_2_8_0 = f"firmware-{BUILD_2_8_0}.json"


def _make_nightly_listing(build: str = BUILD_2_8_0) -> list[dict]:
    """A realistic flat listing of the firmware-nightly directory.

    Models the live shape: one release-level manifest, several per-device
    manifests (``.mt.json``), firmware zips, helper scripts, BLE OTA blobs,
    LittleFS images, and ESP32 OTA helpers.  The real directory has ~487
    files; this subset covers every category the asset-selection logic
    must distinguish.
    """
    return [
        # Release-level manifest — the ONLY file that identifies the build.
        _contents_entry(f"firmware-{build}.json", size=45_000),
        # Per-device manifests (must be rejected by build-id parsing).
        _contents_entry(f"firmware-rak4631-{build}.mt.json", size=3_200),
        _contents_entry(f"firmware-tbeam-{build}.mt.json", size=3_100),
        _contents_entry(f"firmware-heltec-v3-{build}.mt.json", size=3_150),
        # Firmware zip archives.
        _contents_entry(f"firmware-rak4631-{build}.zip", size=1_200_000),
        _contents_entry(f"firmware-tbeam-{build}.zip", size=1_100_000),
        _contents_entry(f"firmware-heltec-v3-{build}.zip", size=1_150_000),
        _contents_entry(f"firmware-tlora-v2-1-1_6-{build}.zip", size=1_180_000),
        _contents_entry(f"firmware-t1000-e-{build}.zip", size=1_220_000),
        # Helper scripts.
        _contents_entry("device-install.sh", size=12_000),
        _contents_entry("device-update.sh", size=10_000),
        # BLE OTA blobs.
        _contents_entry("bleota.bin", size=500_000),
        _contents_entry("bleota-c3.bin", size=480_000),
        _contents_entry("bleota-s3.bin", size=490_000),
        # ESP32 OTA helpers.
        _contents_entry("mt-esp32.bin", size=400_000),
        _contents_entry("mt-esp32s3.bin", size=410_000),
        _contents_entry("mt-esp32c3.bin", size=395_000),
        # LittleFS images.
        _contents_entry("littlefs-esp32s3.bin", size=300_000),
    ]


# ==================================================================
# Fixtures
# ==================================================================


@pytest.fixture
def cache_manager(tmp_path):
    """Real CacheManager backed by tmp_path for file I/O."""
    return CacheManager(cache_dir=str(tmp_path / "cache"))


@pytest.fixture
def mock_cache_manager(tmp_path):
    """Mock CacheManager for API-call verification (no real I/O)."""
    mock = Mock(spec=CacheManager)
    mock.cache_dir = str(tmp_path / "cache")
    mock.get_cache_file_path.side_effect = lambda file_name: os.path.join(
        mock.cache_dir, file_name
    )
    return mock


def _make_config(tmp_path, **overrides) -> dict:
    """Build a firmware config with nightlies enabled by default."""
    config = {
        "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SAVE_FIRMWARE": True,
        "CHECK_FIRMWARE_NIGHTLIES": True,
        "SELECTED_FIRMWARE_ASSETS": ["rak4631"],
        "EXCLUDE_PATTERNS": [],
        "FIRMWARE_VERSIONS_TO_KEEP": 2,
        "ADD_CHANNEL_SUFFIXES_TO_DIRECTORIES": False,
        "FILTER_REVOKED_RELEASES": False,
    }
    config.update(overrides)
    return config


@pytest.fixture
def downloader(tmp_path, cache_manager):
    """FirmwareReleaseDownloader with nightlies enabled and real cache."""
    config = _make_config(tmp_path)
    return FirmwareReleaseDownloader(config, cache_manager)


@pytest.fixture
def downloader_disabled(tmp_path, cache_manager):
    """FirmwareReleaseDownloader with nightlies explicitly disabled."""
    config = _make_config(tmp_path, CHECK_FIRMWARE_NIGHTLIES=False)
    return FirmwareReleaseDownloader(config, cache_manager)


@pytest.fixture
def downloader_absent(tmp_path, cache_manager):
    """FirmwareReleaseDownloader with CHECK_FIRMWARE_NIGHTLIES absent from config."""
    config = _make_config(tmp_path)
    del config["CHECK_FIRMWARE_NIGHTLIES"]
    return FirmwareReleaseDownloader(config, cache_manager)


# ==================================================================
# 1. Default-Disabled: no API calls, no files, no tracking
# ==================================================================


def test_default_check_firmware_nightlies_is_false():
    """The default for CHECK_FIRMWARE_NIGHTLIES must be False (opt-in)."""
    _require_constant("DEFAULT_CHECK_FIRMWARE_NIGHTLIES", False)


def test_disabled_no_api_calls(downloader_disabled, mock_cache_manager):
    """When disabled, fetch_firmware_nightlies must not hit the GitHub API."""
    downloader_disabled.cache_manager = mock_cache_manager
    fetch = _require_method(downloader_disabled, "fetch_firmware_nightlies")
    result = fetch()
    assert result == []
    mock_cache_manager.get_repo_contents.assert_not_called()


def test_absent_config_defaults_false_no_side_effects(downloader_absent, tmp_path):
    """Absent CHECK_FIRMWARE_NIGHTLIES must default to False with no side effects."""
    fetch = _require_method(downloader_absent, "fetch_firmware_nightlies")
    result = fetch()
    assert result == []
    # No nightlies directory created.
    nightly_dir = tmp_path / "downloads" / FIRMWARE_DIR_NAME / "nightlies"
    assert not nightly_dir.exists()
    # No tracking file written.
    tracking = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    if tracking is not None:
        assert not (tmp_path / "cache" / tracking).exists()


def test_disabled_does_not_download(downloader_disabled):
    """should_download_nightly must return False when disabled."""
    should = _require_method(downloader_disabled, "should_download_nightly")
    assert should(BUILD_2_8_0) is False


def test_disabled_does_not_process(downloader_disabled):
    """should_process_nightly must return False when disabled."""
    should = _require_method(downloader_disabled, "should_process_nightly")
    entries = _make_nightly_listing()
    assert should(entries, BUILD_2_8_0) is False


# ==================================================================
# 2. Live-Shaped Flat Listing with Release Manifest
# ==================================================================


def test_fetch_firmware_nightlies_returns_flat_listing(downloader, mock_cache_manager):
    """fetch_firmware_nightlies returns the flat GitHub Contents listing."""
    listing = _make_nightly_listing()
    mock_cache_manager.get_repo_contents = Mock(return_value=listing)
    downloader.cache_manager = mock_cache_manager

    fetch = _require_method(downloader, "fetch_firmware_nightlies")
    entries = fetch()
    assert isinstance(entries, list)
    assert len(entries) == len(listing)
    # Every entry must be a dict with the expected GitHub Contents keys.
    for entry in entries:
        assert "name" in entry
        assert "download_url" in entry
        assert entry.get("type") == "file"


def test_fetch_firmware_nightlies_queries_firmware_nightly_dir(
    downloader, mock_cache_manager
):
    """fetch_firmware_nightlies must query the firmware-nightly repo path."""
    source_dir = _require_constant("FIRMWARE_NIGHTLY_SOURCE_DIR", "firmware-nightly")
    mock_cache_manager.get_repo_contents = Mock(return_value=[])
    downloader.cache_manager = mock_cache_manager

    fetch = _require_method(downloader, "fetch_firmware_nightlies")
    fetch()
    mock_cache_manager.get_repo_contents.assert_called_once()
    call_args = mock_cache_manager.get_repo_contents.call_args
    # The first positional arg must be the source directory name.
    assert call_args.args[0] == source_dir


def test_listing_contains_release_manifest(downloader, mock_cache_manager):
    """The flat listing must include the release manifest firmware-2.8.0.f52e2ea.json."""
    listing = _make_nightly_listing()
    mock_cache_manager.get_repo_contents = Mock(return_value=listing)
    downloader.cache_manager = mock_cache_manager

    fetch = _require_method(downloader, "fetch_firmware_nightlies")
    entries = fetch()
    names = [e["name"] for e in entries]
    assert MANIFEST_2_8_0 in names


def test_get_nightly_build_id_extracts_from_manifest(downloader, mock_cache_manager):
    """get_nightly_build_id finds the release manifest and returns its build-id."""
    listing = _make_nightly_listing()
    mock_cache_manager.get_repo_contents = Mock(return_value=listing)
    downloader.cache_manager = mock_cache_manager

    get_build = _require_method(downloader, "get_nightly_build_id")
    build_id = get_build(listing)
    assert build_id == BUILD_2_8_0


def test_get_nightly_build_id_none_when_no_manifest(downloader):
    """get_nightly_build_id returns None when no release manifest is present."""
    listing = [
        _contents_entry("firmware-rak4631-2.8.0.f52e2ea.zip"),
        _contents_entry("device-install.sh"),
    ]
    get_build = _require_method(downloader, "get_nightly_build_id")
    assert get_build(listing) is None


# ==================================================================
# 3. Build-ID Parsing and Rejection of Device Manifests
# ==================================================================


def test_parse_nightly_build_id_from_release_manifest(downloader):
    """parse_nightly_build_id extracts 2.8.0.f52e2ea from firmware-2.8.0.f52e2ea.json."""
    parse = _require_method(downloader, "parse_nightly_build_id")
    assert parse(MANIFEST_2_8_0) == BUILD_2_8_0


def test_parse_nightly_build_id_rejects_device_manifest(downloader):
    """Per-device manifests (.mt.json) must be rejected by build-id parsing."""
    parse = _require_method(downloader, "parse_nightly_build_id")
    device_manifest = f"firmware-rak4631-{BUILD_2_8_0}.mt.json"
    assert parse(device_manifest) is None


def test_parse_nightly_build_id_rejects_zip(downloader):
    """Firmware zip archives must not yield a build-id."""
    parse = _require_method(downloader, "parse_nightly_build_id")
    assert parse(f"firmware-rak4631-{BUILD_2_8_0}.zip") is None


def test_parse_nightly_build_id_rejects_non_firmware(downloader):
    """Non-firmware-prefixed files must not yield a build-id."""
    parse = _require_method(downloader, "parse_nightly_build_id")
    assert parse("device-install.sh") is None
    assert parse("bleota.bin") is None
    assert parse("config.json") is None


def test_parse_nightly_build_id_case_insensitive(downloader):
    """Build-id parsing must be case-insensitive on the filename."""
    parse = _require_method(downloader, "parse_nightly_build_id")
    assert parse("FIRMWARE-2.8.0.f52e2ea.json") == BUILD_2_8_0


def test_parse_nightly_build_id_different_build():
    """parse_nightly_build_id works for a different build identity."""
    # Use the class directly — implemented as a static method.
    parse = getattr(FirmwareReleaseDownloader, "parse_nightly_build_id", None)
    assert callable(parse), f"parse_nightly_build_id missing — {_NOT_IMPL}"
    assert parse("firmware-2.7.20.abcdef0.json") == "2.7.20.abcdef0"


# ==================================================================
# 4. Selected Assets Use Production SELECTED_FIRMWARE_ASSETS Key
# ==================================================================


def test_get_selected_nightly_assets_uses_selected_firmware_assets(
    downloader, mock_cache_manager
):
    """Nightly selection reads SELECTED_FIRMWARE_ASSETS (the production key)."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    # Must include the rak4631 zip but not other devices.
    names = [e["name"] for e in selected]
    assert any("rak4631" in n and n.endswith(".zip") for n in names)
    assert not any("tbeam" in n for n in names)


def test_get_selected_nightly_assets_ignores_dead_keys(downloader):
    """SELECTED_NIGHTLY_ASSETS / SELECTED_ASSETS are not consulted; only SELECTED_FIRMWARE_ASSETS."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]
    # Dead keys set to a different device — must be ignored.
    downloader.config["SELECTED_NIGHTLY_ASSETS"] = ["tbeam"]
    downloader.config["SELECTED_ASSETS"] = ["heltec"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    names = [e["name"] for e in selected]
    assert any("rak4631" in n and n.endswith(".zip") for n in names)
    assert not any("tbeam" in n for n in names)
    assert not any("heltec" in n for n in names)


def test_get_selected_nightly_assets_includes_release_manifest(downloader):
    """The release manifest must always be selected regardless of patterns."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    names = [e["name"] for e in selected]
    assert MANIFEST_2_8_0 in names


def test_get_selected_nightly_assets_respects_exclude_patterns(downloader):
    """EXCLUDE_PATTERNS must remove matching entries from the selection."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]
    downloader.config["EXCLUDE_PATTERNS"] = ["*.zip"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    names = [e["name"] for e in selected]
    # The manifest survives; the zip is excluded.
    assert MANIFEST_2_8_0 in names
    assert not any(n.endswith(".zip") for n in names)


def test_get_selected_nightly_assets_empty_when_no_match(downloader):
    """When no assets match the selection, return an empty list."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["nonexistent-device"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    # The release manifest is always included, so this is just the manifest.
    names = [e["name"] for e in selected]
    assert all(n == MANIFEST_2_8_0 for n in names)


def test_get_selected_nightly_assets_canonical_key_excludes_unrelated_devices(
    downloader,
):
    """Realistic fixture: canonical key selects only the matched device from many.

    The listing includes firmware zips for rak4631, tbeam, heltec-v3,
    tlora-v2-1-1_6, and t1000-e (all under the same build).  With
    SELECTED_FIRMWARE_ASSETS=['rak4631'] only the rak4631 zip and the
    release manifest must be selected — no unrelated device zips, no
    helper scripts, no BLE OTA blobs, no LittleFS images.
    """
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    names = [e["name"] for e in selected]

    # The rak4631 zip and the release manifest are selected.
    assert any("rak4631" in n and n.endswith(".zip") for n in names)
    assert MANIFEST_2_8_0 in names

    # No unrelated device zips, scripts, blobs, or LittleFS images.
    assert not any("tbeam" in n for n in names)
    assert not any("heltec" in n for n in names)
    assert not any("tlora" in n for n in names)
    assert not any("t1000" in n for n in names)
    assert not any(n.endswith(".sh") for n in names)
    assert not any(n.startswith("bleota") for n in names)
    assert not any(n.startswith("mt-esp32") for n in names)
    assert not any(n.startswith("littlefs") for n in names)


# ==================================================================
# 5. Dedicated firmware/nightlies/<build>/ Storage
# ==================================================================


def test_nightly_base_dir_under_firmware(downloader, tmp_path):
    """_ensure_nightly_base_dir creates firmware/nightlies/ and returns its path."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    ensure = _require_method(downloader, "_ensure_nightly_base_dir")
    result = ensure()
    expected = str(tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name)
    assert result == expected
    assert os.path.isdir(result)


def test_resolve_nightly_dir_includes_build_id(downloader, tmp_path):
    """_resolve_nightly_dir returns firmware/nightlies/<build>/."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    resolve = _require_method(downloader, "_resolve_nightly_dir")
    result = resolve(BUILD_2_8_0)
    expected = str(
        tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name / BUILD_2_8_0
    )
    assert result == expected
    assert os.path.isdir(result)


def test_get_nightly_target_path(downloader, tmp_path):
    """get_nightly_target_path places files under firmware/nightlies/<build>/."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    get_path = _require_method(downloader, "get_nightly_target_path")
    result = get_path(BUILD_2_8_0, "firmware-rak4631-2.8.0.f52e2ea.zip")
    expected = str(
        tmp_path
        / "downloads"
        / FIRMWARE_DIR_NAME
        / nightly_dir_name
        / BUILD_2_8_0
        / "firmware-rak4631-2.8.0.f52e2ea.zip"
    )
    assert result == expected


def test_get_nightly_target_path_create_flag(downloader, tmp_path):
    """get_nightly_target_path with create=True makes the build directory."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    get_path = _require_method(downloader, "get_nightly_target_path")
    get_path(BUILD_2_8_0, "test.bin", create=True)
    build_dir = (
        tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name / BUILD_2_8_0
    )
    assert build_dir.is_dir()


def test_nightly_storage_separate_from_prerelease(downloader, tmp_path):
    """Nightly storage must be under firmware/nightlies/, not firmware/prerelease/."""
    resolve = _require_method(downloader, "_resolve_nightly_dir")
    nightly_path = resolve(BUILD_2_8_0)
    assert FIRMWARE_PRERELEASES_DIR_NAME not in nightly_path
    assert "nightlies" in nightly_path


# ==================================================================
# 6. Identity-Changed Downloads
# ==================================================================


def test_should_download_nightly_new_build(downloader):
    """A new build-id (not tracked) must trigger a download."""
    should = _require_method(downloader, "should_download_nightly")
    assert should(BUILD_2_8_0) is True


def test_should_download_nightly_changed_build(downloader, cache_manager):
    """When the tracked build-id differs, must download the new one."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)
    Path(tracking_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_path).write_text(json.dumps({"build_id": "2.7.20.abcdef0"}))

    should = _require_method(downloader, "should_download_nightly")
    # Use build-id inequality, not hash ordering.
    assert should(BUILD_2_8_0) is True
    assert should("2.7.20.abcdef0") is False


def test_should_process_nightly_identity_changed(downloader, cache_manager):
    """should_process_nightly returns True when build-id differs from tracked."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)
    Path(tracking_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_path).write_text(json.dumps({"build_id": "2.7.20.abcdef0"}))

    entries = _make_nightly_listing()
    should = _require_method(downloader, "should_process_nightly")
    assert should(entries, BUILD_2_8_0) is True


# ==================================================================
# 7. Same Identity Complete — Skip
# ==================================================================


def test_should_process_nightly_same_identity_complete_skips(downloader, cache_manager):
    """Same build-id tracked and all selected files present → must NOT process."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)
    Path(tracking_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_path).write_text(json.dumps({"build_id": BUILD_2_8_0}))

    entries = _make_nightly_listing()
    # Write all selected assets to disk so the build is "complete".
    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(entries)
    get_path = _require_method(downloader, "get_nightly_target_path")
    for entry in selected:
        target = get_path(BUILD_2_8_0, entry["name"], create=True)
        Path(target).write_bytes(b"x" * entry.get("size", 1))

    should = _require_method(downloader, "should_process_nightly")
    assert should(entries, BUILD_2_8_0) is False


def test_is_nightly_complete_when_all_present(downloader):
    """is_nightly_complete returns True when all selected assets are present."""
    entries = _make_nightly_listing()
    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(entries)
    get_path = _require_method(downloader, "get_nightly_target_path")
    for entry in selected:
        target = get_path(BUILD_2_8_0, entry["name"], create=True)
        Path(target).write_bytes(b"x" * entry.get("size", 1))

    is_complete = _require_method(downloader, "is_nightly_complete")
    assert is_complete(BUILD_2_8_0) is True


def test_is_nightly_complete_false_when_missing(downloader):
    """is_nightly_complete returns False when selected assets are missing."""
    is_complete = _require_method(downloader, "is_nightly_complete")
    assert is_complete(BUILD_2_8_0) is False


# ==================================================================
# 8. Same Identity Incomplete — Backfill
# ==================================================================


def test_should_process_nightly_same_identity_incomplete_backfills(
    downloader, cache_manager
):
    """Same build-id tracked but files missing → must process for backfill."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)
    Path(tracking_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_path).write_text(json.dumps({"build_id": BUILD_2_8_0}))

    entries = _make_nightly_listing()
    # Do NOT write any files — the build is incomplete.
    should = _require_method(downloader, "should_process_nightly")
    assert should(entries, BUILD_2_8_0) is True


def test_should_process_nightly_partial_files_backfills(downloader, cache_manager):
    """Same build-id, some files present but not all → must process for backfill."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)
    Path(tracking_path).parent.mkdir(parents=True, exist_ok=True)
    Path(tracking_path).write_text(json.dumps({"build_id": BUILD_2_8_0}))

    entries = _make_nightly_listing()
    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(entries)
    get_path = _require_method(downloader, "get_nightly_target_path")
    # Write only the first selected asset — leave the rest missing.
    if selected:
        target = get_path(BUILD_2_8_0, selected[0]["name"], create=True)
        Path(target).write_bytes(b"x" * selected[0].get("size", 1))

    should = _require_method(downloader, "should_process_nightly")
    assert should(entries, BUILD_2_8_0) is True


# ==================================================================
# 9. Transactional Tracking / Latest Pointer (all assets must succeed)
# ==================================================================


def test_update_nightly_tracking_writes_json(downloader, cache_manager):
    """update_nightly_tracking writes build_id to the tracking JSON file."""
    tracking_file = getattr(constants, "LATEST_FIRMWARE_NIGHTLY_JSON_FILE", None)
    assert (
        tracking_file is not None
    ), f"LATEST_FIRMWARE_NIGHTLY_JSON_FILE missing — {_NOT_IMPL}"
    tracking_path = cache_manager.get_cache_file_path(tracking_file)

    update = _require_method(downloader, "update_nightly_tracking")
    assert update(BUILD_2_8_0) is True

    data = json.loads(Path(tracking_path).read_text())
    assert data["build_id"] == BUILD_2_8_0
    assert "last_updated" in data


def test_orch_nightly_all_success_tracks_and_updates_latest(tmp_path, cache_manager):
    """All selected assets succeed → tracking written once, latest pointer updated."""
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = _make_config(tmp_path, SAVE_FIRMWARE=True)
    orch = DownloadOrchestrator(config)

    listing = _make_nightly_listing()
    # Mock the nightly methods on the firmware downloader.
    fd = orch.firmware_downloader
    fd.fetch_firmware_nightlies = Mock(return_value=listing)
    fd.get_nightly_build_id = Mock(return_value=BUILD_2_8_0)
    fd.should_process_nightly = Mock(return_value=True)
    selected = [listing[0], listing[4]]  # manifest + rak4631 zip
    fd.get_selected_nightly_assets = Mock(return_value=selected)
    file_type = getattr(constants, "FILE_TYPE_FIRMWARE_NIGHTLY", "firmware_nightly")
    fd.download_nightly_asset = Mock(
        return_value=DownloadResult(
            success=True,
            release_tag=BUILD_2_8_0,
            file_path=str(tmp_path / "x.bin"),
            download_url="http://x",
            file_size=1,
            file_type=file_type,
        )
    )
    fd.update_nightly_tracking = Mock(return_value=True)
    fd.cleanup_superseded_nightlies = Mock(return_value=0)
    fd.update_latest_pointer_for_nightly = Mock(return_value=True)
    orch._handle_download_result = Mock()

    # The orchestrator must have a nightly seam.
    assert hasattr(
        orch, "_process_firmware_nightlies"
    ), f"DownloadOrchestrator._process_firmware_nightlies missing — {_NOT_IMPL}"
    orch._process_firmware_nightlies()

    fd.update_nightly_tracking.assert_called_once_with(BUILD_2_8_0)
    fd.cleanup_superseded_nightlies.assert_called_once_with(BUILD_2_8_0)
    fd.update_latest_pointer_for_nightly.assert_called_once_with(BUILD_2_8_0)


def test_orch_nightly_partial_failure_no_track_no_cleanup(tmp_path, cache_manager):
    """Mixed success/failure → no tracking, no cleanup, no latest pointer."""
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = _make_config(tmp_path, SAVE_FIRMWARE=True)
    orch = DownloadOrchestrator(config)

    listing = _make_nightly_listing()
    fd = orch.firmware_downloader
    fd.fetch_firmware_nightlies = Mock(return_value=listing)
    fd.get_nightly_build_id = Mock(return_value=BUILD_2_8_0)
    fd.should_process_nightly = Mock(return_value=True)
    asset1, asset2 = listing[0], listing[4]
    fd.get_selected_nightly_assets = Mock(return_value=[asset1, asset2])
    file_type = getattr(constants, "FILE_TYPE_FIRMWARE_NIGHTLY", "firmware_nightly")
    fd.download_nightly_asset = Mock(
        side_effect=[
            DownloadResult(
                success=True,
                release_tag=BUILD_2_8_0,
                file_path=str(tmp_path / "a.bin"),
                download_url="http://x",
                file_size=1,
                file_type=file_type,
            ),
            DownloadResult(
                success=False,
                release_tag=BUILD_2_8_0,
                file_path=str(tmp_path / "b.bin"),
                download_url="http://y",
                file_size=1,
                file_type=file_type,
            ),
        ]
    )
    fd.update_nightly_tracking = Mock(return_value=True)
    fd.cleanup_superseded_nightlies = Mock(return_value=0)
    fd.update_latest_pointer_for_nightly = Mock(return_value=True)
    orch._handle_download_result = Mock()

    assert hasattr(
        orch, "_process_firmware_nightlies"
    ), f"DownloadOrchestrator._process_firmware_nightlies missing — {_NOT_IMPL}"
    orch._process_firmware_nightlies()

    fd.update_nightly_tracking.assert_not_called()
    fd.cleanup_superseded_nightlies.assert_not_called()
    fd.update_latest_pointer_for_nightly.assert_not_called()


def test_orch_nightly_all_failure_no_track(tmp_path, cache_manager):
    """All assets fail → no tracking, no cleanup."""
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = _make_config(tmp_path, SAVE_FIRMWARE=True)
    orch = DownloadOrchestrator(config)

    listing = _make_nightly_listing()
    fd = orch.firmware_downloader
    fd.fetch_firmware_nightlies = Mock(return_value=listing)
    fd.get_nightly_build_id = Mock(return_value=BUILD_2_8_0)
    fd.should_process_nightly = Mock(return_value=True)
    fd.get_selected_nightly_assets = Mock(return_value=[listing[0]])
    file_type = getattr(constants, "FILE_TYPE_FIRMWARE_NIGHTLY", "firmware_nightly")
    fd.download_nightly_asset = Mock(
        return_value=DownloadResult(
            success=False,
            release_tag=BUILD_2_8_0,
            file_path=str(tmp_path / "x.bin"),
            download_url="http://x",
            file_size=1,
            file_type=file_type,
        )
    )
    fd.update_nightly_tracking = Mock(return_value=True)
    fd.cleanup_superseded_nightlies = Mock(return_value=0)
    fd.update_latest_pointer_for_nightly = Mock(return_value=True)
    orch._handle_download_result = Mock()

    assert hasattr(
        orch, "_process_firmware_nightlies"
    ), f"DownloadOrchestrator._process_firmware_nightlies missing — {_NOT_IMPL}"
    orch._process_firmware_nightlies()

    fd.update_nightly_tracking.assert_not_called()
    fd.cleanup_superseded_nightlies.assert_not_called()


def test_orch_nightly_empty_selected_no_track(tmp_path, cache_manager):
    """Empty selected set → no downloads, no tracking, no cleanup."""
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = _make_config(tmp_path, SAVE_FIRMWARE=True)
    orch = DownloadOrchestrator(config)

    listing = _make_nightly_listing()
    fd = orch.firmware_downloader
    fd.fetch_firmware_nightlies = Mock(return_value=listing)
    fd.get_nightly_build_id = Mock(return_value=BUILD_2_8_0)
    fd.should_process_nightly = Mock(return_value=True)
    fd.get_selected_nightly_assets = Mock(return_value=[])
    fd.update_nightly_tracking = Mock(return_value=True)
    fd.cleanup_superseded_nightlies = Mock(return_value=0)
    fd.update_latest_pointer_for_nightly = Mock(return_value=True)
    orch._handle_download_result = Mock()

    assert hasattr(
        orch, "_process_firmware_nightlies"
    ), f"DownloadOrchestrator._process_firmware_nightlies missing — {_NOT_IMPL}"
    orch._process_firmware_nightlies()

    fd.update_nightly_tracking.assert_not_called()
    fd.cleanup_superseded_nightlies.assert_not_called()


def test_orch_nightly_disabled_skips_entirely(tmp_path, cache_manager):
    """CHECK_FIRMWARE_NIGHTLIES=False → fetch never called."""
    from fetchtastic.download.orchestrator import DownloadOrchestrator

    config = _make_config(tmp_path, CHECK_FIRMWARE_NIGHTLIES=False)
    orch = DownloadOrchestrator(config)

    fd = orch.firmware_downloader
    fd.fetch_firmware_nightlies = Mock(return_value=[])

    assert hasattr(
        orch, "_process_firmware_nightlies"
    ), f"DownloadOrchestrator._process_firmware_nightlies missing — {_NOT_IMPL}"
    orch._process_firmware_nightlies()
    fd.fetch_firmware_nightlies.assert_not_called()


# ==================================================================
# 10. Cleanup: Keep Count and Latest Preservation
# ==================================================================


def test_cleanup_superseded_nightlies_keep_count(downloader, tmp_path):
    """cleanup_superseded_nightlies removes old builds, keeping the latest N."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    # Create three build directories with files.
    for build in ("2.7.18.aaaaaaa", "2.7.19.bbbbbbb", "2.8.0.f52e2ea"):
        d = base / build
        d.mkdir(parents=True)
        (d / "firmware-rak4631.zip").write_bytes(b"x")

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 1
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    removed = cleanup()
    assert removed == 2
    # Latest build preserved.
    assert (base / "2.8.0.f52e2ea").is_dir()
    assert not (base / "2.7.19.bbbbbbb").is_dir()
    assert not (base / "2.7.18.aaaaaaa").is_dir()


def test_cleanup_superseded_nightlies_keep_two(downloader, tmp_path):
    """Keep count of 2 preserves the two newest builds."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    for build in ("2.7.18.aaaaaaa", "2.7.19.bbbbbbb", "2.8.0.f52e2ea"):
        (base / build).mkdir(parents=True)

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 2
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    removed = cleanup()
    assert removed == 1
    assert (base / "2.8.0.f52e2ea").is_dir()
    assert (base / "2.7.19.bbbbbbb").is_dir()
    assert not (base / "2.7.18.aaaaaaa").is_dir()


def test_cleanup_nightly_retention_floor_is_one(downloader, tmp_path):
    """A keep count of zero must be clamped to 1 — never delete the current build."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    (base / "2.8.0.f52e2ea").mkdir(parents=True)

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 0
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    removed = cleanup()
    assert removed == 0
    assert (base / "2.8.0.f52e2ea").is_dir()


def test_cleanup_nightly_no_dir(downloader):
    """cleanup_superseded_nightlies returns 0 when the nightlies dir does not exist."""
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    assert cleanup() == 0


def test_cleanup_nightly_preserves_latest_pointer(downloader, tmp_path):
    """cleanup must not remove the 'latest' pointer inside nightlies/."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    build_dir = base / "2.8.0.f52e2ea"
    build_dir.mkdir(parents=True)
    (build_dir / "firmware.zip").write_bytes(b"x")
    # Create a latest symlink pointing to the build.
    latest = base / LATEST_POINTER_NAME
    try:
        os.symlink(str(build_dir), str(latest))
    except OSError:
        pytest.skip("Symlinks not supported on this platform")

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 1
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    cleanup()
    # The latest pointer must survive cleanup.
    assert latest.is_symlink() or latest.exists()


def test_cleanup_nightly_rejects_symlinked_root(downloader, tmp_path):
    """A symlinked nightlies root must be rejected (do not follow symlinks)."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    firmware_base = tmp_path / "downloads" / FIRMWARE_DIR_NAME
    firmware_base.mkdir(parents=True)
    real_target = tmp_path / "real_nightlies"
    real_target.mkdir()
    try:
        os.symlink(str(real_target), str(firmware_base / nightly_dir_name))
    except OSError:
        pytest.skip("Symlinks not supported on this platform")

    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    # Must not follow the symlink or delete anything inside real_target.
    assert cleanup() == 0
    assert real_target.is_dir()
    # Nothing inside real_target should have been deleted.
    assert list(real_target.iterdir()) == []


def test_cleanup_nightly_preserves_non_build_dirs(downloader, tmp_path):
    """Non-build-id directories inside nightlies/ must not be deleted."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    (base / "2.8.0.f52e2ea").mkdir(parents=True)
    (base / "not_a_build").mkdir(parents=True)
    (base / "2.8.0.f52e2ea" / "firmware.zip").write_bytes(b"x")

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 1
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    cleanup()
    assert (base / "not_a_build").is_dir()


def test_cleanup_nightly_same_base_current_survives(downloader, tmp_path):
    """current_build_id must survive even when same-base hashes sort it first.

    Three builds share base 2.8.0 but differ in hash suffix:
      - 2.8.0.aaaaaaa  (the just-downloaded current build)
      - 2.8.0.bbbbbbb  (an older build)
      - 2.8.0.f52e2ea  (the oldest build)

    All have release_tuple (2, 8, 0), so sort falls through to the name
    string.  'aaaaaaa' sorts first.  With keep_limit=1, the sort-only
    keep window would be {f52e2ea} — deleting aaaaaaa (the current build)
    and bbbbbbb.  The current_build_id pin must rescue aaaaaaa while
    still allowing bbbbbbb to be removed.
    """
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir_name
    current = "2.8.0.aaaaaaa"
    middle = "2.8.0.bbbbbbb"
    oldest = "2.8.0.f52e2ea"
    for build in (current, middle, oldest):
        d = base / build
        d.mkdir(parents=True)
        (d / "firmware.zip").write_bytes(b"x")

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 1
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    removed = cleanup(current_build_id=current)

    # Current build must survive even though it sorts first.
    assert (base / current).is_dir()
    # Oldest (in the keep window) also survives.
    assert (base / oldest).is_dir()
    # Middle build is removed.
    assert not (base / middle).is_dir()
    assert removed == 1


def test_cleanup_nightly_same_base_current_survives_without_arg(downloader, tmp_path):
    """Without current_build_id, the same-base sort order decides what lives.

    This documents the pre-fix danger: with no current_build_id pin and
    keep_limit=1, the alphabetically-first hash ('aaaaaaa') is deleted
    even if it was the just-downloaded build.  The fix is for callers
    to pass current_build_id; the primitive itself still respects sort
    order when the pin is absent.
    """
    nightly_dir = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    base = tmp_path / "downloads" / FIRMWARE_DIR_NAME / nightly_dir
    first = "2.8.0.aaaaaaa"
    middle = "2.8.0.bbbbbbb"
    last = "2.8.0.f52e2ea"
    for build in (first, middle, last):
        (base / build).mkdir(parents=True)
        (base / build / "firmware.zip").write_bytes(b"x")

    downloader.config["FIRMWARE_NIGHTLY_VERSIONS_TO_KEEP"] = 1
    cleanup = _require_method(downloader, "cleanup_superseded_nightlies")
    removed = cleanup()

    # Without the pin, sort order alone decides: only f52e2ea survives.
    assert removed == 2
    assert not (base / first).is_dir()
    assert not (base / middle).is_dir()
    assert (base / last).is_dir()


# ==================================================================
# 11. Stable Cleanup Preserves Nightlies
# ==================================================================


def test_stable_cleanup_preserves_nightlies_dir(downloader, tmp_path):
    """cleanup_old_versions (stable) must not remove firmware/nightlies/."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    firmware_dir = tmp_path / "downloads" / FIRMWARE_DIR_NAME
    nightly_dir = firmware_dir / nightly_dir_name
    nightly_build = nightly_dir / BUILD_2_8_0
    nightly_build.mkdir(parents=True)
    (nightly_build / "firmware.zip").write_bytes(b"x")

    # Also create a stable release dir so cleanup has something to scan.
    stable_dir = firmware_dir / "v2.7.20"
    stable_dir.mkdir(parents=True)
    (stable_dir / "firmware.zip").write_bytes(b"x")

    # Run stable cleanup with keep_limit=0 and no cached releases.
    downloader.config["FILTER_REVOKED_RELEASES"] = False
    downloader.cleanup_old_versions(keep_limit=0, cached_releases=[])

    # The nightlies directory and its contents must survive.
    assert nightly_dir.is_dir()
    assert nightly_build.is_dir()
    assert (nightly_build / "firmware.zip").exists()


def test_stable_cleanup_preserves_nightlies_with_symlink(downloader, tmp_path):
    """Stable cleanup must not follow or remove a nightlies symlink."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    firmware_dir = tmp_path / "downloads" / FIRMWARE_DIR_NAME
    firmware_dir.mkdir(parents=True)
    real_nightlies = tmp_path / "real_nightlies"
    real_nightlies.mkdir()
    (real_nightlies / BUILD_2_8_0).mkdir()
    try:
        os.symlink(str(real_nightlies), str(firmware_dir / nightly_dir_name))
    except OSError:
        pytest.skip("Symlinks not supported on this platform")

    downloader.config["FILTER_REVOKED_RELEASES"] = False
    downloader.cleanup_old_versions(keep_limit=0, cached_releases=[])

    # Symlink must survive.
    assert (firmware_dir / nightly_dir_name).is_symlink()
    # Real target contents must survive.
    assert (real_nightlies / BUILD_2_8_0).is_dir()


# ==================================================================
# 12. Generic Prerelease Scan Excludes firmware-nightly
# ==================================================================


def test_scan_prerelease_directories_excludes_firmware_nightly():
    """scan_prerelease_directories must not return 'nightly' for any stable floor.

    The history API admits prerelease bases strictly newer than the supplied
    stable release, so callers pass the *preceding* stable (e.g. ``2.7.19`` when
    a ``2.7.20.<sha>`` prerelease should be admitted).  ``firmware-nightly`` must
    never appear in the result regardless.
    """
    manager = PrereleaseHistoryManager()
    dirs = [
        "firmware-nightly",
        "firmware-2.7.20.abcdef0",
        "firmware-2.8.0.f52e2ea",
        "firmware-2.7.19.1234567",
    ]
    result = manager.scan_prerelease_directories(dirs, "2.7.19")
    # "nightly" must never appear in the results.
    assert "nightly" not in result
    # 2.7.20 and 2.8.0 prerelease identifiers are admitted (> 2.7.19);
    # 2.7.19.* is rejected (not strictly newer than the stable floor).
    assert "2.7.20.abcdef0" in result
    assert "2.8.0.f52e2ea" in result
    assert "2.7.19.1234567" not in result


def test_scan_prerelease_directories_excludes_firmware_nightly_for_2_8():
    """The exclusion holds for any stable floor; 2.8.0 admits against 2.7.26."""
    manager = PrereleaseHistoryManager()
    dirs = [
        "firmware-nightly",
        f"firmware-{BUILD_2_8_0}",
    ]
    result = manager.scan_prerelease_directories(dirs, "2.7.26")
    assert "nightly" not in result
    assert BUILD_2_8_0 in result


def test_firmware_nightly_not_treated_as_prerelease_dir(downloader, mock_cache_manager):
    """download_repo_prerelease_firmware must not treat firmware-nightly as a prerelease."""
    # When scanning repo dirs, firmware-nightly must not be selected as an active dir.
    mock_cache_manager.get_repo_directories = Mock(
        return_value=["firmware-nightly", "firmware-2.7.20.abcdef0"]
    )
    mock_cache_manager.get_repo_contents = Mock(return_value=[])
    downloader.cache_manager = mock_cache_manager

    with (
        patch(
            "fetchtastic.download.firmware.PrereleaseHistoryManager"
            ".get_latest_active_prerelease_from_history",
            return_value=(None, []),
        ),
    ):
        successes, failures, active_dir, _ = (
            downloader.download_repo_prerelease_firmware("v2.7.20")
        )
    # active_dir must not be "firmware-nightly".
    assert active_dir != "firmware-nightly"


# ==================================================================
# 13. Production 2.8 Nightly Fixture
# ==================================================================


def test_production_2_8_nightly_fixture_listing_shape():
    """The production 2.8 nightly fixture models the live flat directory shape."""
    listing = _make_nightly_listing()
    assert len(listing) >= 15  # representative subset of ~487 live files

    # Every entry has the GitHub Contents API fields.
    for entry in listing:
        assert "name" in entry
        assert "path" in entry
        assert "download_url" in entry
        assert "size" in entry
        assert entry["type"] == "file"

    # The release manifest is present and correctly named.
    names = [e["name"] for e in listing]
    assert MANIFEST_2_8_0 in names

    # Download URLs point to the firmware-nightly raw path.
    manifest_entry = next(e for e in listing if e["name"] == MANIFEST_2_8_0)
    assert "firmware-nightly" in manifest_entry["download_url"]
    assert manifest_entry["path"] == f"firmware-nightly/{MANIFEST_2_8_0}"


def test_production_2_8_nightly_build_id_round_trip(downloader):
    """The production 2.8 fixture build-id parses correctly end-to-end."""
    listing = _make_nightly_listing()
    get_build = _require_method(downloader, "get_nightly_build_id")
    build_id = get_build(listing)
    assert build_id == BUILD_2_8_0

    parse = _require_method(downloader, "parse_nightly_build_id")
    assert parse(MANIFEST_2_8_0) == BUILD_2_8_0


def test_production_2_8_nightly_device_manifests_rejected(downloader):
    """All per-device manifests in the 2.8 fixture must be rejected by build-id parsing."""
    listing = _make_nightly_listing()
    parse = _require_method(downloader, "parse_nightly_build_id")
    device_manifests = [e["name"] for e in listing if e["name"].endswith(".mt.json")]
    assert len(device_manifests) >= 3
    for name in device_manifests:
        assert parse(name) is None, f"Device manifest {name} must not yield a build-id"


def test_production_2_8_nightly_selected_assets(downloader):
    """The 2.8 fixture with SELECTED_FIRMWARE_ASSETS=['rak4631'] selects the right files."""
    listing = _make_nightly_listing()
    downloader.config["SELECTED_FIRMWARE_ASSETS"] = ["rak4631"]

    select = _require_method(downloader, "get_selected_nightly_assets")
    selected = select(listing)
    names = [e["name"] for e in selected]

    # Must include the rak4631 zip and the release manifest.
    assert any("rak4631" in n and n.endswith(".zip") for n in names)
    assert MANIFEST_2_8_0 in names
    # Must NOT include other devices.
    assert not any("tbeam" in n for n in names)
    assert not any("heltec" in n for n in names)


def test_production_2_8_nightly_storage_path(downloader, tmp_path):
    """The 2.8 fixture stores files under firmware/nightlies/2.8.0.f52e2ea/."""
    nightly_dir_name = _require_constant("FIRMWARE_NIGHTLIES_DIR_NAME", "nightlies")
    get_path = _require_method(downloader, "get_nightly_target_path")
    target = get_path(BUILD_2_8_0, "firmware-rak4631-2.8.0.f52e2ea.zip", create=True)
    expected = str(
        tmp_path
        / "downloads"
        / FIRMWARE_DIR_NAME
        / nightly_dir_name
        / BUILD_2_8_0
        / "firmware-rak4631-2.8.0.f52e2ea.zip"
    )
    assert target == expected
    assert os.path.isdir(os.path.dirname(target))
