import os

import pytest

from fetchtastic.download.latest_pointer import (
    remove_latest_pointer,
    update_latest_pointer,
)

pytestmark = [pytest.mark.unit, pytest.mark.core_downloads]


def test_update_latest_pointer_creates_relative_same_dir_symlink(tmp_path):
    target = tmp_path / "v2.7.0"
    target.mkdir()

    result = update_latest_pointer(tmp_path, target.name)

    assert result is True
    latest = tmp_path / "latest"
    assert latest.is_symlink()
    assert os.readlink(latest) == "v2.7.0"


def test_update_latest_pointer_rejects_traversal_target(tmp_path):
    (tmp_path / "v2.7.0").mkdir()

    result = update_latest_pointer(tmp_path, "../v2.7.0")

    assert result is False
    assert not (tmp_path / "latest").exists()


def test_update_latest_pointer_replaces_existing_symlink_without_following(tmp_path):
    old_target = tmp_path / "v2.6.0"
    new_target = tmp_path / "v2.7.0"
    outside = tmp_path.parent / "outside-latest-target"
    old_target.mkdir()
    new_target.mkdir()
    outside.mkdir(exist_ok=True)
    latest = tmp_path / "latest"
    latest.symlink_to(outside)

    result = update_latest_pointer(tmp_path, new_target.name)

    assert result is True
    assert latest.is_symlink()
    assert os.readlink(latest) == "v2.7.0"
    assert outside.exists()


def test_update_latest_pointer_rejects_symlink_target(tmp_path):
    real_target = tmp_path / "v2.7.0"
    linked_target = tmp_path / "linked"
    real_target.mkdir()
    linked_target.symlink_to(real_target)

    result = update_latest_pointer(tmp_path, linked_target.name)

    assert result is False
    assert not (tmp_path / "latest").exists()


def test_update_latest_pointer_does_not_replace_regular_file(tmp_path):
    target = tmp_path / "v2.7.0"
    latest = tmp_path / "latest"
    target.mkdir()
    latest.write_text("user content", encoding="utf-8")

    result = update_latest_pointer(tmp_path, target.name)

    assert result is False
    assert latest.read_text(encoding="utf-8") == "user content"


def test_remove_latest_pointer_only_removes_symlink(tmp_path):
    target = tmp_path / "v2.7.0"
    target.mkdir()
    latest = tmp_path / "latest"
    latest.symlink_to(target.name)

    assert remove_latest_pointer(tmp_path) is True
    assert not latest.exists()
    assert target.exists()

    latest.write_text("not a symlink", encoding="utf-8")
    assert remove_latest_pointer(tmp_path) is False
    assert latest.exists()
