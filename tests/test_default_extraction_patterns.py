# Tests for the default/suggested firmware extraction patterns.
#
# Locks in that the default pattern list shipped to users covers the
# ESP32 OTA helper binaries (`mt-*-ota.bin`) and the nRF52 factory
# erase / recovery UF2 files (`Meshtastic_nRF52_factory_erase_*.uf2`),
# and that those files are actually selected by matches_extract_patterns
# when the defaults are in use.

import pytest

from fetchtastic import constants
from fetchtastic.utils import matches_extract_patterns

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
# silently pull in just because of the new patterns. These are chosen so
# that no existing default pattern (rak4631-, tbeam, t1000-e-, ...) hits
# them either.
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
        assert matches_extract_patterns(
            filename, constants.DEFAULT_EXTRACTION_PATTERNS
        ), f"default patterns did not match {filename!r}"

    @pytest.mark.parametrize("filename", DEFAULT_SHOULD_NOT_MATCH)
    def test_default_patterns_do_not_match_controls(self, filename):
        assert not matches_extract_patterns(
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
        assert matches_extract_patterns(filename, ["mt-"])

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
        result = matches_extract_patterns(filename, ["factory_erase"])
        assert result is should_match
