import pytest
from fetchtastic.setup_config import validate_version_count

@pytest.mark.parametrize("value, current_str, min_val, max_val, expected", [
    ("5", "2", 1, 10, 5),      # Valid input
    ("", "3", 1, 10, 3),       # Empty input, use current
    ("1", "2", 1, 10, 1),      # Min boundary
    ("10", "2", 1, 10, 10),    # Max boundary
    ("7", "2", 5, 8, 7),       # Custom min/max
])
def test_validate_version_count_valid(value, current_str, min_val, max_val, expected):
    assert validate_version_count(value, current_str, min_val, max_val) == expected

@pytest.mark.parametrize("value, current_str, min_val, max_val, error_msg_part", [
    ("0", "2", 1, 10, "must be between 1 and 10"),     # Below min
    ("11", "2", 1, 10, "must be between 1 and 10"),    # Above max
    ("abc", "2", 1, 10, "Please enter a number"),      # Not a number
    ("", "abc", 1, 10, "Please enter a number"),       # Current is not a number
    ("5.5", "2", 1, 10, "Please enter a number"),      # Float
    (" ", "3", 1, 10, "Please enter a number"), # Whitespace only
])
def test_validate_version_count_invalid(value, current_str, min_val, max_val, error_msg_part):
    with pytest.raises(ValueError, match=error_msg_part):
        validate_version_count(value, current_str, min_val, max_val)

def test_validate_version_count_empty_uses_current_valid():
    assert validate_version_count("", "7", 1, 10) == 7

def test_validate_version_count_empty_uses_current_invalid_current():
    # This tests if current_versions_str itself is invalid and input is empty.
    with pytest.raises(ValueError, match="Please enter a number"):
        validate_version_count("", "xyz", 1, 10)
