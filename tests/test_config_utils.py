from fetchtastic.download.config_utils import (
    _get_string_list_from_config,
    get_prerelease_patterns,
)


def test_get_string_list_from_config_with_string():
    """Test _get_string_list_from_config with a string value."""
    config = {"test_key": "single_value"}
    result = _get_string_list_from_config(config, "test_key")
    assert result == ["single_value"]


def test_get_string_list_from_config_with_list():
    """Test _get_string_list_from_config with a list value."""
    config = {"test_key": ["value1", "value2", "value3"]}
    result = _get_string_list_from_config(config, "test_key")
    assert result == ["value1", "value2", "value3"]


def test_get_string_list_from_config_missing_key():
    """Test _get_string_list_from_config with missing key."""
    config = {}
    result = _get_string_list_from_config(config, "missing_key")
    assert result == []


def test_get_string_list_from_config_none_value():
    """
    Verifies that when a config key exists with value None, _get_string_list_from_config returns an empty list.
    """
    config = {"test_key": None}
    result = _get_string_list_from_config(config, "test_key")
    assert result == []


def test_get_string_list_from_config_empty_list():
    """Test _get_string_list_from_config with empty list."""
    config = {"test_key": []}
    result = _get_string_list_from_config(config, "test_key")
    assert result == []


def test_get_string_list_from_config_mixed_types():
    """Test _get_string_list_from_config with mixed types in list."""
    config = {"test_key": ["string", 123, True]}
    result = _get_string_list_from_config(config, "test_key")
    assert result == ["string", "123", "True"]


def test_get_prerelease_patterns_with_new_key():
    """Test get_prerelease_patterns with SELECTED_PRERELEASE_ASSETS key."""
    config = {"SELECTED_PRERELEASE_ASSETS": ["pattern1", "pattern2"]}
    result = get_prerelease_patterns(config)
    assert result == ["pattern1", "pattern2"]


def test_get_prerelease_patterns_fallback_to_extract_patterns():
    """Test get_prerelease_patterns fallback to EXTRACT_PATTERNS with warning."""
    config = {"EXTRACT_PATTERNS": ["legacy_pattern1", "legacy_pattern2"]}
    result = get_prerelease_patterns(config)
    assert result == ["legacy_pattern1", "legacy_pattern2"]


def test_get_prerelease_patterns_both_keys_prefers_new():
    """Test get_prerelease_patterns prefers new key when both are present."""
    config = {
        "SELECTED_PRERELEASE_ASSETS": ["new_pattern"],
        "EXTRACT_PATTERNS": ["legacy_pattern"],
    }
    result = get_prerelease_patterns(config)
    assert result == ["new_pattern"]


def test_get_prerelease_patterns_no_keys():
    """Test get_prerelease_patterns with no relevant keys."""
    config = {"other_key": "value"}
    result = get_prerelease_patterns(config)
    assert result == []


def test_get_prerelease_patterns_new_key_empty_list():
    """Test get_prerelease_patterns with empty list for new key."""
    config = {"SELECTED_PRERELEASE_ASSETS": []}
    result = get_prerelease_patterns(config)
    assert result == []


def test_get_prerelease_patterns_legacy_key_empty_list():
    """Test get_prerelease_patterns with empty list for legacy key."""
    config = {"EXTRACT_PATTERNS": []}
    result = get_prerelease_patterns(config)
    assert result == []


def test_get_prerelease_patterns_new_key_string():
    """Test get_prerelease_patterns with string value for new key."""
    config = {"SELECTED_PRERELEASE_ASSETS": "single_pattern"}
    result = get_prerelease_patterns(config)
    assert result == ["single_pattern"]


def test_get_prerelease_patterns_legacy_key_string():
    """Test get_prerelease_patterns with string value for legacy key."""
    config = {"EXTRACT_PATTERNS": "legacy_pattern"}
    result = get_prerelease_patterns(config)
    assert result == ["legacy_pattern"]
