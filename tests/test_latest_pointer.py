import os
from unittest.mock import patch

import pytest

from fetchtastic.constants import LATEST_POINTER_NAME
from fetchtastic.download.latest_pointer import (
    remove_latest_pointer,
    update_latest_pointer,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


@pytest.fixture(scope="module")
def symlinks_supported(tmp_path_factory):
    """Check whether os.symlink works in the temp directory."""
    tmp = tmp_path_factory.mktemp("symlink_check")
    probe = tmp / ".probe"
    try:
        os.symlink(tmp.name, probe)
        probe.unlink()
        return True
    except (AttributeError, NotImplementedError, OSError):
        return False


def test_update_latest_pointer_creates_relative_same_dir_symlink(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    target = tmp_path / "v2.7.0"
    target.mkdir()

    result = update_latest_pointer(tmp_path, target.name)

    assert result is True
    latest = tmp_path / LATEST_POINTER_NAME
    assert latest.is_symlink()
    assert os.readlink(latest) == "v2.7.0"


def test_update_latest_pointer_rejects_traversal_target(tmp_path):
    (tmp_path / "v2.7.0").mkdir()

    result = update_latest_pointer(tmp_path, "../v2.7.0")

    assert result is False
    assert not (tmp_path / LATEST_POINTER_NAME).exists()


def test_update_latest_pointer_replaces_existing_symlink_without_following(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    old_target = tmp_path / "v2.6.0"
    new_target = tmp_path / "v2.7.0"
    outside = tmp_path.parent / "outside-latest-target"
    old_target.mkdir()
    new_target.mkdir()
    outside.mkdir(exist_ok=True)
    latest = tmp_path / LATEST_POINTER_NAME
    latest.symlink_to(outside)

    result = update_latest_pointer(tmp_path, new_target.name)

    assert result is True
    assert latest.is_symlink()
    assert os.readlink(latest) == "v2.7.0"
    assert outside.exists()


def test_update_latest_pointer_rejects_symlink_target(tmp_path, symlinks_supported):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    real_target = tmp_path / "v2.7.0"
    linked_target = tmp_path / "linked"
    real_target.mkdir()
    linked_target.symlink_to(real_target)

    result = update_latest_pointer(tmp_path, linked_target.name)

    assert result is False
    assert not (tmp_path / LATEST_POINTER_NAME).exists()


def test_update_latest_pointer_rejects_symlink_parent_before_mutation(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    real_parent = tmp_path / "real"
    symlink_parent = tmp_path / "linked"
    real_parent.mkdir()
    (real_parent / "v2.7.0").mkdir()
    symlink_parent.symlink_to(real_parent, target_is_directory=True)

    with (
        patch("os.symlink", wraps=os.symlink) as mock_symlink,
        patch(
            "fetchtastic.download.latest_pointer._is_valid_latest_target"
        ) as mock_target_check,
    ):
        result = update_latest_pointer(symlink_parent, "v2.7.0")

    assert result is False
    mock_symlink.assert_not_called()
    mock_target_check.assert_not_called()
    assert not (real_parent / LATEST_POINTER_NAME).exists()
    assert not (symlink_parent / LATEST_POINTER_NAME).exists()


def test_update_latest_pointer_rejects_symlink_ancestor_before_mutation(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    real_ancestor = tmp_path / "real"
    symlink_ancestor = tmp_path / "linked"
    real_ancestor.mkdir()
    symlink_ancestor.symlink_to(real_ancestor, target_is_directory=True)
    parent = symlink_ancestor / "downloads"

    with (
        patch("os.symlink", wraps=os.symlink) as mock_symlink,
        patch(
            "fetchtastic.download.latest_pointer._is_valid_latest_target"
        ) as mock_target_check,
    ):
        result = update_latest_pointer(parent, "v2.7.0")

    assert result is False
    mock_symlink.assert_not_called()
    mock_target_check.assert_not_called()
    assert not parent.exists()
    assert not (real_ancestor / "downloads").exists()


def test_update_latest_pointer_does_not_replace_regular_file(tmp_path):
    target = tmp_path / "v2.7.0"
    latest = tmp_path / LATEST_POINTER_NAME
    target.mkdir()
    latest.write_text("user content", encoding="utf-8")

    result = update_latest_pointer(tmp_path, target.name)

    assert result is False
    assert latest.read_text(encoding="utf-8") == "user content"


def test_remove_latest_pointer_removes_managed_symlink_and_leaves_target(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    target = tmp_path / "v2.7.0"
    target.mkdir()
    latest = tmp_path / LATEST_POINTER_NAME
    latest.symlink_to(target.name)

    assert remove_latest_pointer(tmp_path) is True
    assert not latest.exists()
    assert target.exists()


def test_remove_latest_pointer_refuses_to_remove_regular_file(tmp_path):
    target = tmp_path / "v2.7.0"
    target.mkdir()
    latest = tmp_path / LATEST_POINTER_NAME
    latest.write_text("not a symlink", encoding="utf-8")

    assert remove_latest_pointer(tmp_path) is False
    assert latest.exists()


def test_update_latest_pointer_calls_symlink_with_target_is_directory_for_dir_target(
    tmp_path, symlinks_supported
):
    if not symlinks_supported:
        pytest.skip("os.symlink not supported on this platform")
    target = tmp_path / "v2.7.0"
    target.mkdir()

    with patch("os.symlink", wraps=os.symlink) as mock_symlink:
        update_latest_pointer(tmp_path, target.name)

    mock_symlink.assert_called()
    _, kwargs = mock_symlink.call_args
    assert kwargs.get("target_is_directory") is True
