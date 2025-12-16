"""
Comprehensive tests for configuration utilities module.
"""

import pytest

from fetchtastic.download.config_utils import (
    _get_string_list_from_config,
    get_prerelease_patterns,
)


@pytest.mark.configuration
@pytest.mark.unit
class TestConfigUtils:
    """Test configuration utility functions."""

    def test_get_string_list_from_config_empty_value(self):
        """Test _get_string_list_from_config with empty/None value."""
        result = _get_string_list_from_config({}, "missing_key")
        assert result == []

        result = _get_string_list_from_config({"key": None}, "key")
        assert result == []

        result = _get_string_list_from_config({"key": ""}, "key")
        assert result == []

    def test_get_string_list_from_config_string_value(self):
        """Test _get_string_list_from_config with string value."""
        config = {"key": "single_value"}
        result = _get_string_list_from_config(config, "key")
        assert result == ["single_value"]

    def test_get_string_list_from_config_list_value(self):
        """Test _get_string_list_from_config with list value."""
        config = {"key": ["value1", "value2", "value3"]}
        result = _get_string_list_from_config(config, "key")
        assert result == ["value1", "value2", "value3"]

    def test_get_string_list_from_config_mixed_types(self):
        """
        Verify that when a config value is a list with mixed types, each element is converted to its string representation and returned in the same order.
        """
        config = {"key": ["string", 123, True, None, "another_string"]}
        result = _get_string_list_from_config(config, "key")
        assert result == ["string", "123", "True", "None", "another_string"]

    def test_get_prerelease_patterns_new_key(self):
        """Test get_prerelease_patterns with new SELECTED_PRERELEASE_ASSETS key."""
        config = {"SELECTED_PRERELEASE_ASSETS": ["pattern1", "pattern2"]}
        result = get_prerelease_patterns(config)
        assert result == ["pattern1", "pattern2"]

    def test_get_prerelease_patterns_new_key_empty(self):
        """Test get_prerelease_patterns with empty new key."""
        config = {"SELECTED_PRERELEASE_ASSETS": []}
        result = get_prerelease_patterns(config)
        assert result == []

    def test_get_prerelease_patterns_fallback_to_extract_patterns(self):
        """Test get_prerelease_patterns falling back to EXTRACT_PATTERNS."""
        config = {"EXTRACT_PATTERNS": ["extract1", "extract2"]}

        result = get_prerelease_patterns(config)

        assert result == ["extract1", "extract2"]

    def test_get_prerelease_patterns_fallback_string_extract_patterns(self):
        """Test get_prerelease_patterns falling back to string EXTRACT_PATTERNS."""
        config = {"EXTRACT_PATTERNS": "single_pattern"}

        result = get_prerelease_patterns(config)

        assert result == ["single_pattern"]

    def test_get_prerelease_patterns_no_keys(self):
        """Test get_prerelease_patterns with no relevant keys."""
        config = {"OTHER_KEY": "value"}
        result = get_prerelease_patterns(config)
        assert result == []

    def test_get_prerelease_patterns_both_keys_present(self):
        """Test get_prerelease_patterns prefers new key over old key."""
        config = {
            "SELECTED_PRERELEASE_ASSETS": ["new_pattern"],
            "EXTRACT_PATTERNS": ["old_pattern"],
        }

        result = get_prerelease_patterns(config)

        assert result == ["new_pattern"]

    def test_get_prerelease_patterns_complex_config(self):
        """Test get_prerelease_patterns with complex configuration."""
        config = {
            "SELECTED_PRERELEASE_ASSETS": ["rak4631-", "tbeam"],
            "OTHER_SETTING": True,
            "EXTRACT_PATTERNS": ["old_pattern"],  # Should be ignored
        }

        result = get_prerelease_patterns(config)
        assert result == ["rak4631-", "tbeam"]

    def test_get_string_list_from_config_integer_conversion(self):
        """Test _get_string_list_from_config with integer in list."""
        config = {"key": [123, 456]}
        result = _get_string_list_from_config(config, "key")
        assert result == ["123", "456"]
