import pytest
import os
import platform
from fetchtastic.downloader import safe_extract_path, compare_versions

# Tests for safe_extract_path

def test_safe_extract_path_valid(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = "some_file.txt"
    expected_path = os.path.join(str(extract_dir), file_path) # Use str(extract_dir) for os.path.join
    assert safe_extract_path(str(extract_dir), file_path) == expected_path

def test_safe_extract_path_traversal_parent(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = "../some_file.txt"
    with pytest.raises(ValueError, match="Unsafe path detected"):
        safe_extract_path(str(extract_dir), file_path)

def test_safe_extract_path_traversal_absolute(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    # Adjusted for platform neutrality in the test itself, though the function should handle it.
    file_path = os.path.abspath(os.path.join(str(tmp_path), "..", "an_absolute_file.txt"))
    # To make it a more realistic absolute path scenario that's outside extract_dir
    # but not relying on specific system files like /etc/passwd
    if platform.system() == "Windows":
        # A generic absolute path for Windows that would be outside a typical tmp_path structure
        # This is a bit contrived for testing; the key is it's absolute and outside.
        # Using an environment variable that usually points to a system directory.
        system_root = os.environ.get("SystemRoot", "C:\\Windows")
        file_path = os.path.join(system_root, "System32", "drivers", "etc", "hosts")
    else: # Linux/macOS like
        file_path = "/etc/passwd"

    with pytest.raises(ValueError, match="Unsafe path detected"):
        safe_extract_path(str(extract_dir), file_path)

def test_safe_extract_path_traversal_complex(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = "subdir/../../../../some_external_file.txt" # More robust traversal
    with pytest.raises(ValueError, match="Unsafe path detected"):
        safe_extract_path(str(extract_dir), file_path)

def test_safe_extract_path_valid_subdir(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = "subdir/some_file.txt"
    expected_path = os.path.join(str(extract_dir), file_path) # Use str(extract_dir)
    assert safe_extract_path(str(extract_dir), file_path) == expected_path

def test_safe_extract_path_empty_filepath(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = ""
    assert safe_extract_path(str(extract_dir), file_path) == str(extract_dir.resolve())

def test_safe_extract_path_dot_filepath(tmp_path):
    extract_dir = tmp_path / "extract_here"
    extract_dir.mkdir()
    file_path = "."
    assert safe_extract_path(str(extract_dir), file_path) == str(extract_dir.resolve())

# Tests for compare_versions

@pytest.mark.parametrize("version1, version2, expected", [
    ("1.0.0", "1.0.0", 0),
    ("1.0.1", "1.0.0", 1),
    ("1.0.0", "1.0.1", -1),
    ("2.0.0", "1.9.9", 1),
    ("1.10.0", "1.2.0", 1),
    ("1.2.3.abc", "1.2.3.def", 0),
    ("1.2.4.abc", "1.2.3.def", 1),
    ("1.2.0", "1.2.0.abc", 0),
    ("2.6.9.f93d031", "2.6.8.ef9d0d7", 1),
    ("2.6.8.ef9d0d7", "2.6.9.f93d031", -1),
    ("v1.0.0", "1.0.0", 0),
    ("1.0.0", "v1.0.0", 0),
    ("v1.0.1", "v1.0.0", 1),
    ("1.0", "1.0.0", -1),
    ("1.0.0", "1.0", 1),
    ("alpha", "beta", -1),
    ("beta", "alpha", 1),
    ("1.0.0-alpha", "1.0.0-beta", -1),
])
def test_compare_versions(version1, version2, expected):
    assert compare_versions(version1, version2) == expected

def test_compare_versions_invalid_parts(tmp_path): # tmp_path not used
    assert compare_versions("1.0", "1.0.0") == -1
    assert compare_versions("1.0.0", "1.0") == 1
    assert compare_versions("2.0", "1.0.0") == 1
    assert compare_versions("1.0.0", "2.0") == -1
    assert compare_versions("1", "1.0") == -1
    assert compare_versions("1.0", "1") == 1
    assert compare_versions("foo", "bar") == 1
