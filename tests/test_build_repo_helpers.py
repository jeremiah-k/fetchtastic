import pytest

from fetchtastic.build.base import (
    parse_github_repo_url,
    repo_dirname_from_url,
    resolve_repo_url,
)


@pytest.mark.unit
def test_parse_github_repo_url_handles_https_and_ssh():
    assert parse_github_repo_url("https://github.com/meshtastic/firmware.git") == (
        "meshtastic",
        "firmware",
    )
    assert parse_github_repo_url("git@github.com:meshtastic/firmware.git") == (
        "meshtastic",
        "firmware",
    )


@pytest.mark.unit
def test_repo_dirname_from_url_uses_owner_repo():
    repo_dirname = repo_dirname_from_url(
        "https://github.com/meshtastic/Meshtastic-Android.git",
        "fallback",
    )
    assert repo_dirname == "meshtastic-Meshtastic-Android"


@pytest.mark.unit
def test_resolve_repo_url_prefers_override_or_fork():
    default_repo = "https://github.com/meshtastic/Meshtastic-Android.git"
    resolved = resolve_repo_url(
        default_repo,
        "Meshtastic-Android",
        repo_url="https://github.com/example/forked.git",
    )
    assert resolved == "https://github.com/example/forked.git"

    resolved = resolve_repo_url(
        default_repo,
        "Meshtastic-Android",
        fork="someone",
    )
    assert resolved == "https://github.com/someone/Meshtastic-Android.git"

    resolved = resolve_repo_url(
        default_repo,
        "Meshtastic-Android",
        fork="someone/custom-app",
    )
    assert resolved == "https://github.com/someone/custom-app.git"
