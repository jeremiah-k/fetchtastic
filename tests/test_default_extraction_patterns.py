# Tests for the default/suggested firmware extraction patterns.
#
# Locks in that the default pattern list shipped to users covers the
# ESP32 OTA helper binaries (`mt-*-ota.bin`) and the nRF52 factory
# erase / recovery UF2 files (`Meshtastic_nRF52_factory_erase_*.uf2`).
#
# The tests exercise the PRODUCTION extraction path:
#   - matches_selected_patterns() is what FileOperations.extract_archive()
#     and FileOperations.check_extraction_needed() call on each archive
#     member (see src/fetchtastic/download/files.py).
#   - The ZIP-level tests drive those FileOperations methods directly with
#     DEFAULT_EXTRACTION_PATTERNS so the full include-filter + extract
#     pipeline is covered end-to-end.

import zipfile
from pathlib import Path

import pytest

from fetchtastic import constants
from fetchtastic.download.files import FileOperations
from fetchtastic.utils import matches_selected_patterns

# Files that upstream firmware releases publish and that users expect
# the default pattern list to catch.
DEFAULT_SHOULD_MATCH = [
    "mt-esp32-ota.bin",
    "mt-esp32s3-ota.bin",
    "mt-esp32c3-ota.bin",
    "mt-esp32c6-ota.bin",
    "Meshtastic_nRF52_factory_erase_v3_S140_6.1.0.uf2",
    "Meshtastic_nRF52_factory_erase_v3_S140_7.3.0.uf2",
]

# Negative controls: common release artifacts the defaults should NOT
# silently pull in. Chosen so that no existing default pattern
# (rak4631-, tbeam, t1000-e-, ...) hits them either.
DEFAULT_SHOULD_NOT_MATCH = [
    "readme.txt",
    # A device family not present in the default list.
    "firmware-heltec-v3-2.7.6.uf2",
    # An nRF52 *update* image (not a factory-erase recovery file).
    "Meshtastic_nRF52_tft_feather_sense_update_v3_S140_6.1.0.uf2",
]


@pytest.mark.unit
@pytest.mark.configuration
class TestDefaultExtractionPatterns:
    """The default/suggested extraction pattern list."""

    def test_includes_mt_prefix(self):
        # `mt-` covers ESP32, ESP32-S3, ESP32-C3, and ESP32-C6 OTA helpers
        # (`mt-esp32-ota.bin`, `mt-esp32s3-ota.bin`, ...). The narrower
        # `mt-esp32-ota` would miss S3/C3/C6.
        assert "mt-" in constants.DEFAULT_EXTRACTION_PATTERNS

    def test_includes_factory_erase(self):
        # nRF52 recovery / factory erase UF2 files.
        assert "factory_erase" in constants.DEFAULT_EXTRACTION_PATTERNS

    def test_existing_patterns_preserved(self):
        # Adding new patterns must not drop the existing suggestions.
        for expected in (
            "rak4631-",
            "tbeam",
            "t1000-e-",
            "tlora-v2-1-1_6-",
            "device-",
            "littlefs-",
            "bleota",
        ):
            assert (
                expected in constants.DEFAULT_EXTRACTION_PATTERNS
            ), f"existing pattern {expected!r} was removed"

    @pytest.mark.parametrize("filename", DEFAULT_SHOULD_MATCH)
    def test_default_patterns_match_expected_files(self, filename):
        # Production extraction filter: matches_selected_patterns is what
        # FileOperations.extract_archive() applies to each archive member.
        assert matches_selected_patterns(
            filename, constants.DEFAULT_EXTRACTION_PATTERNS
        ), f"default patterns did not match {filename!r}"

    @pytest.mark.parametrize("filename", DEFAULT_SHOULD_NOT_MATCH)
    def test_default_patterns_do_not_match_controls(self, filename):
        assert not matches_selected_patterns(
            filename, constants.DEFAULT_EXTRACTION_PATTERNS
        ), f"default patterns should not match {filename!r}"

    @pytest.mark.parametrize(
        "filename",
        [
            "mt-esp32-ota.bin",
            "mt-esp32s3-ota.bin",
            "mt-esp32c3-ota.bin",
            "mt-esp32c6-ota.bin",
        ],
    )
    def test_mt_prefix_alone_matches_all_esp32_ota_variants(self, filename):
        # Single `mt-` pattern is sufficient for every ESP32 variant.
        assert matches_selected_patterns(filename, ["mt-"])

    @pytest.mark.parametrize(
        ("filename", "should_match"),
        [
            ("Meshtastic_nRF52_factory_erase_v3_S140_6.1.0.uf2", True),
            ("Meshtastic_nRF52_factory_erase_v3_S140_7.3.0.uf2", True),
            # Substring match must not over-match: a file whose name merely
            # contains "erase" (but not "factory_erase") is not picked up.
            ("Meshtastic_nRF52_tft_feather_sense_erase_v3.uf2", False),
        ],
    )
    def test_factory_erase_targets_recovery_files_only(self, filename, should_match):
        result = matches_selected_patterns(filename, ["factory_erase"])
        assert result is should_match


# ---------------------------------------------------------------------------
# ZIP-level integration tests: drive FileOperations.extract_archive() and
# check_extraction_needed() with DEFAULT_EXTRACTION_PATTERNS so the full
# production extraction pipeline is covered, not just the helper function.
# ---------------------------------------------------------------------------


def _build_firmware_zip(zip_path: Path, members: dict[str, bytes]) -> None:
    """Write a zip archive whose member names map to the given contents."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)


@pytest.mark.integration
@pytest.mark.core_downloads
class TestFileOperationsExtractionWithDefaults:
    """Drive the real extraction pipeline with DEFAULT_EXTRACTION_PATTERNS."""

    # Archive contents: two files the defaults must extract, plus three
    # negative controls the defaults must leave behind.
    ARCHIVE_MEMBERS: dict[str, bytes] = {
        "mt-esp32s3-ota.bin": b"esp32s3 ota payload",
        "Meshtastic_nRF52_factory_erase_v3_S140_6.1.0.uf2": b"nrf52 factory erase",
        "readme.txt": b"should not be extracted",
        "firmware-heltec-v3-2.7.6.uf2": b"heltec image, not in defaults",
        "Meshtastic_nRF52_tft_feather_sense_update_v3_S140_6.1.0.uf2": b"update image",
    }

    EXPECTED_EXTRACTED = {
        "mt-esp32s3-ota.bin",
        "Meshtastic_nRF52_factory_erase_v3_S140_6.1.0.uf2",
    }

    def test_extract_archive_pulls_only_default_matched_files(self, tmp_path):
        zip_path = tmp_path / "firmware.zip"
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()
        _build_firmware_zip(zip_path, self.ARCHIVE_MEMBERS)

        ops = FileOperations()
        extracted = ops.extract_archive(
            str(zip_path),
            str(extract_dir),
            list(constants.DEFAULT_EXTRACTION_PATTERNS),
            [],
        )

        extracted_names = {p.name for p in extracted}
        assert extracted_names == self.EXPECTED_EXTRACTED
        # Files actually exist on disk.
        for name in self.EXPECTED_EXTRACTED:
            assert (extract_dir / name).is_file()
        # Negative controls were not extracted.
        for name in self.ARCHIVE_MEMBERS:
            if name not in self.EXPECTED_EXTRACTED:
                assert not (
                    extract_dir / name
                ).exists(), f"{name!r} should not have been extracted"

    def test_check_extraction_needed_true_when_matches_not_yet_extracted(
        self, tmp_path
    ):
        zip_path = tmp_path / "firmware.zip"
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()
        _build_firmware_zip(zip_path, self.ARCHIVE_MEMBERS)

        ops = FileOperations()
        # Nothing extracted yet → extraction is required.
        assert (
            ops.check_extraction_needed(
                str(zip_path),
                str(extract_dir),
                list(constants.DEFAULT_EXTRACTION_PATTERNS),
                [],
            )
            is True
        )

    def test_check_extraction_needed_false_after_extraction(self, tmp_path):
        zip_path = tmp_path / "firmware.zip"
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()
        _build_firmware_zip(zip_path, self.ARCHIVE_MEMBERS)

        ops = FileOperations()
        ops.extract_archive(
            str(zip_path),
            str(extract_dir),
            list(constants.DEFAULT_EXTRACTION_PATTERNS),
            [],
        )

        # All default-matched files now exist with matching sizes → no
        # further extraction needed.
        assert (
            ops.check_extraction_needed(
                str(zip_path),
                str(extract_dir),
                list(constants.DEFAULT_EXTRACTION_PATTERNS),
                [],
            )
            is False
        )
