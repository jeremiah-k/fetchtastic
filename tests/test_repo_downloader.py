import pytest

from fetchtastic import repo_downloader


@pytest.fixture
def mock_selected_files():
    """Provides a mock dictionary of selected files for download."""
    return {
        "directory": "firmware-2.7.4.c1f4f79",
        "files": [
            {
                "name": "firmware-rak4631-2.7.4.c1f4f79.bin",
                "download_url": "http://fake.url/firmware-rak4631.bin",
            },
            {
                "name": "littlefs-rak4631-2.7.4.c1f4f79.bin",
                "download_url": "http://fake.url/littlefs-rak4631.bin",
            },
            {
                "name": "device-update.sh",
                "download_url": "http://fake.url/device-update.sh",
            },
            {"name": "bleota.bin", "download_url": "http://fake.url/bleota.bin"},
        ],
    }


def test_download_repo_files(mocker, tmp_path, mock_selected_files):
    """Test the file download logic for the repo browser."""
    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry", return_value=True
    )
    mock_chmod = mocker.patch("os.chmod")

    download_dir = tmp_path

    downloaded = repo_downloader.download_repo_files(
        mock_selected_files, str(download_dir)
    )

    # Check that download_file_with_retry was called for each file
    assert mock_download.call_count == 4

    # Check that directories were created
    expected_dir = tmp_path / "firmware" / "repo-dls" / "firmware-2.7.4.c1f4f79"
    assert expected_dir.exists()

    # Check that chmod was called for the .sh file
    mock_chmod.assert_called_once_with(str(expected_dir / "device-update.sh"), 0o755)

    assert len(downloaded) == 4


def test_clean_repo_directory(tmp_path):
    """Test the logic for cleaning the repo-dls directory."""
    repo_dls_dir = tmp_path / "firmware" / "repo-dls"
    repo_dls_dir.mkdir(parents=True)
    (repo_dls_dir / "some_file.txt").write_text("data")
    (repo_dls_dir / "subdir").mkdir()

    repo_downloader.clean_repo_directory(str(tmp_path))

    # The repo-dls dir itself should still exist, but be empty
    assert repo_dls_dir.exists()
    assert len(list(repo_dls_dir.iterdir())) == 0


def test_main_orchestration(mocker, mock_selected_files):
    """Test the main orchestration logic of the repo downloader."""
    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files", return_value=["path/to/file"]
    )
    mock_config = {"DOWNLOAD_DIR": "/fake/dir"}

    # 1. Test successful flow
    mock_run_menu.return_value = mock_selected_files
    repo_downloader.main(mock_config)
    mock_download.assert_called_once_with(mock_selected_files, "/fake/dir")

    # 2. Test user quitting the menu
    mock_run_menu.return_value = None
    mock_download.reset_mock()
    repo_downloader.main(mock_config)
    mock_download.assert_not_called()
