import pytest

from fetchtastic.download.orchestrator import DownloadOrchestrator


@pytest.mark.unit
@pytest.mark.core_downloads
def test_refresh_commit_history_cache_uses_config_github_token(mocker):
    orchestrator = DownloadOrchestrator({"GITHUB_TOKEN": "token-from-config"})
    mock_fetch = mocker.patch.object(
        orchestrator.prerelease_manager, "fetch_recent_repo_commits", return_value=[]
    )

    orchestrator._refresh_commit_history_cache()

    _args, kwargs = mock_fetch.call_args
    assert kwargs["github_token"] == "token-from-config"
    assert kwargs["allow_env_token"] is True


@pytest.mark.unit
@pytest.mark.core_downloads
def test_get_download_statistics_excludes_skipped_from_download_counts():
    orchestrator = DownloadOrchestrator({})

    class _Result:
        def __init__(self, *, success: bool, was_skipped: bool, file_type: str):
            """
            Initialize a Result object representing an individual download outcome.

            Parameters:
                success (bool): `True` if the download completed successfully, `False` otherwise.
                was_skipped (bool): `True` if the download was intentionally skipped, `False` otherwise.
                file_type (str): The category or type of the downloaded file (e.g., "firmware").

            Attributes:
                file_path (str | None): Path where the file was saved, set to `None` by default.
            """
            self.success = success
            self.was_skipped = was_skipped
            self.file_type = file_type
            self.file_path = None

    orchestrator.download_results = [
        _Result(success=True, was_skipped=True, file_type="firmware"),
        _Result(success=True, was_skipped=False, file_type="firmware"),
    ]
    orchestrator.failed_downloads = []

    stats = orchestrator.get_download_statistics()

    assert stats["total_downloads"] == 1
    assert stats["successful_downloads"] == 1
    assert stats["skipped_downloads"] == 1
    assert stats["failed_downloads"] == 0
