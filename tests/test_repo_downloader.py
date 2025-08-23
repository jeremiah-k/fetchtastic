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


# Additional comprehensive tests for missing coverage areas


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_no_files_selected():
    """Test download_repo_files with no files selected."""
    # Test with None
    result = repo_downloader.download_repo_files(None, "/tmp/test")  # nosec B108
    assert result == []

    # Test with empty dict
    result = repo_downloader.download_repo_files({}, "/tmp/test")  # nosec B108
    assert result == []

    # Test with missing directory key
    result = repo_downloader.download_repo_files(
        {"files": []}, "/tmp/test"
    )  # nosec B108
    assert result == []

    # Test with missing files key
    result = repo_downloader.download_repo_files(
        {"directory": "test"}, "/tmp/test"
    )  # nosec B108
    assert result == []


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_directory_creation_error(mocker, mock_selected_files):
    """Test download_repo_files when directory creation fails."""
    # Mock exists to return False so makedirs is called
    mocker.patch("os.path.exists", return_value=False)
    # Mock makedirs to fail on the second call (dir_path creation)
    mock_makedirs = mocker.patch("os.makedirs")
    mock_makedirs.side_effect = [
        None,
        OSError("Permission denied"),
    ]  # First call succeeds, second fails

    result = repo_downloader.download_repo_files(
        mock_selected_files, "/tmp/test"
    )  # nosec B108
    assert result == []
    assert mock_makedirs.call_count == 2


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_missing_file_data(mocker, tmp_path):
    """Test download_repo_files with missing or invalid file data."""
    selected_files = {
        "directory": "test-dir",
        "files": [
            {"name": "valid.bin", "download_url": "http://example.com/valid.bin"},
            {
                "name": "",
                "download_url": "http://example.com/empty-name.bin",
            },  # Empty name
            {"name": "no-url.bin"},  # Missing download_url
            {"download_url": "http://example.com/no-name.bin"},  # Missing name
        ],
    }

    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry", return_value=True
    )

    result = repo_downloader.download_repo_files(selected_files, str(tmp_path))

    # Only the valid file should be processed
    assert mock_download.call_count == 1
    assert len(result) == 1


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_download_failure(mocker, mock_selected_files, tmp_path):
    """Test download_repo_files when download fails."""
    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry", return_value=False
    )

    result = repo_downloader.download_repo_files(mock_selected_files, str(tmp_path))

    # All downloads failed, so no files should be in result
    assert len(result) == 0
    assert mock_download.call_count == 4


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_chmod_error(mocker, tmp_path):
    """Test download_repo_files when chmod fails on shell scripts."""
    selected_files = {
        "directory": "test-dir",
        "files": [
            {"name": "script.sh", "download_url": "http://example.com/script.sh"}
        ],
    }

    mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry", return_value=True
    )
    mock_chmod = mocker.patch("os.chmod", side_effect=OSError("Permission denied"))

    result = repo_downloader.download_repo_files(selected_files, str(tmp_path))

    # File should still be added to result even if chmod fails
    assert len(result) == 1
    mock_chmod.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_malformed_data(mocker, tmp_path):
    """Test download_repo_files with malformed file data."""
    selected_files = {
        "directory": "test-dir",
        "files": [
            {"name": "valid.bin", "download_url": "http://example.com/valid.bin"},
            "not_a_dict",  # Invalid file data type
            None,  # None file data
        ],
    }

    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry", return_value=True
    )

    result = repo_downloader.download_repo_files(selected_files, str(tmp_path))

    # Only the valid file should be processed
    assert mock_download.call_count == 1
    assert len(result) == 1


@pytest.mark.core_downloads
@pytest.mark.unit
def test_download_repo_files_unexpected_error(mocker, tmp_path):
    """Test download_repo_files with unexpected error during processing."""
    selected_files = {
        "directory": "test-dir",
        "files": [
            {"name": "valid.bin", "download_url": "http://example.com/valid.bin"}
        ],
    }

    # Mock download to raise an unexpected exception
    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_file_with_retry",
        side_effect=RuntimeError("Unexpected error"),
    )

    result = repo_downloader.download_repo_files(selected_files, str(tmp_path))

    # Should handle the error gracefully and return empty list
    assert len(result) == 0
    mock_download.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
def test_clean_repo_directory_nonexistent():
    """Test clean_repo_directory when directory doesn't exist."""
    result = repo_downloader.clean_repo_directory("/nonexistent/path")  # nosec B108
    assert result is True  # Should return True when nothing to clean


@pytest.mark.core_downloads
@pytest.mark.unit
def test_clean_repo_directory_with_files_and_dirs(tmp_path):
    """Test clean_repo_directory with mixed files and directories."""
    repo_dls_dir = tmp_path / "firmware" / "repo-dls"
    repo_dls_dir.mkdir(parents=True)

    # Create test files and directories
    (repo_dls_dir / "test_file.txt").write_text("test content")
    (repo_dls_dir / "test_dir").mkdir()
    (repo_dls_dir / "test_dir" / "nested_file.txt").write_text("nested content")

    # Create a symlink
    (repo_dls_dir / "test_link").symlink_to(repo_dls_dir / "test_file.txt")

    result = repo_downloader.clean_repo_directory(str(tmp_path))

    assert result is True
    assert repo_dls_dir.exists()  # Directory itself should still exist
    assert len(list(repo_dls_dir.iterdir())) == 0  # But should be empty


@pytest.mark.core_downloads
@pytest.mark.unit
def test_clean_repo_directory_error(mocker, tmp_path):
    """Test clean_repo_directory when cleanup fails."""
    repo_dls_dir = tmp_path / "firmware" / "repo-dls"
    repo_dls_dir.mkdir(parents=True)
    (repo_dls_dir / "test_file.txt").write_text("test content")

    # Mock os.listdir to raise an error
    mock_listdir = mocker.patch("os.listdir", side_effect=OSError("Permission denied"))

    result = repo_downloader.clean_repo_directory(str(tmp_path))

    assert result is False
    mock_listdir.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.unit
def test_clean_repo_directory_remove_error(mocker, tmp_path):
    """Test clean_repo_directory when file removal fails."""
    repo_dls_dir = tmp_path / "firmware" / "repo-dls"
    repo_dls_dir.mkdir(parents=True)
    (repo_dls_dir / "test_file.txt").write_text("test content")

    # Mock os.remove to raise an error
    mock_remove = mocker.patch("os.remove", side_effect=OSError("Permission denied"))
    mocker.patch("os.path.isfile", return_value=True)
    mocker.patch("os.path.islink", return_value=False)

    result = repo_downloader.clean_repo_directory(str(tmp_path))

    assert result is False
    mock_remove.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_missing_download_dir(mocker):
    """Test main function when download directory is not configured."""
    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mock_download = mocker.patch("fetchtastic.repo_downloader.download_repo_files")

    # Test with missing DOWNLOAD_DIR key
    config = {}
    repo_downloader.main(config)
    mock_run_menu.assert_not_called()
    mock_download.assert_not_called()

    # Test with None DOWNLOAD_DIR
    config = {"DOWNLOAD_DIR": None}
    repo_downloader.main(config)
    mock_run_menu.assert_not_called()
    mock_download.assert_not_called()


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_no_files_downloaded(mocker, mock_selected_files):
    """Test main function when no files are downloaded."""
    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mock_download = mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files", return_value=[]
    )

    mock_run_menu.return_value = mock_selected_files
    config = {"DOWNLOAD_DIR": "/tmp/test"}  # nosec B108

    repo_downloader.main(config)

    mock_run_menu.assert_called_once()
    mock_download.assert_called_once_with(
        mock_selected_files, "/tmp/test"
    )  # nosec B108


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_windows_open_folder_success(mocker, mock_selected_files):
    """Test main function on Windows with successful folder opening."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("builtins.input", return_value="y")
    mock_startfile = mocker.patch("os.startfile", create=True)

    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files",
        return_value=["/tmp/test/firmware/repo-dls/test-dir/file.bin"],  # nosec B108
    )

    mock_run_menu.return_value = mock_selected_files
    config = {"DOWNLOAD_DIR": "/tmp/test"}  # nosec B108

    repo_downloader.main(config)

    mock_startfile.assert_called_once_with(
        "/tmp/test/firmware/repo-dls/test-dir"
    )  # nosec B108


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_windows_decline_open_folder(mocker, mock_selected_files):
    """Test main function on Windows when user declines to open folder."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("builtins.input", return_value="n")
    mock_startfile = mocker.patch("os.startfile", create=True)

    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files",
        return_value=["/tmp/test/firmware/repo-dls/test-dir/file.bin"],  # nosec B108
    )

    mock_run_menu.return_value = mock_selected_files
    config = {"DOWNLOAD_DIR": "/tmp/test"}  # nosec B108

    repo_downloader.main(config)

    mock_startfile.assert_not_called()


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_windows_open_folder_error(mocker, mock_selected_files):
    """Test main function on Windows when folder opening fails."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("builtins.input", return_value="y")
    mock_startfile = mocker.patch(
        "os.startfile", create=True, side_effect=OSError("Cannot open folder")
    )

    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files",
        return_value=["/tmp/test/firmware/repo-dls/test-dir/file.bin"],  # nosec B108
    )

    mock_run_menu.return_value = mock_selected_files
    config = {"DOWNLOAD_DIR": "/tmp/test"}  # nosec B108

    repo_downloader.main(config)

    mock_startfile.assert_called_once()


@pytest.mark.core_downloads
@pytest.mark.integration
def test_main_windows_open_folder_unexpected_error(mocker, mock_selected_files):
    """Test main function on Windows with unexpected error opening folder."""
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("builtins.input", return_value="y")
    mock_startfile = mocker.patch(
        "os.startfile", create=True, side_effect=RuntimeError("Unexpected error")
    )

    mock_run_menu = mocker.patch("fetchtastic.menu_repo.run_menu")
    mocker.patch(
        "fetchtastic.repo_downloader.download_repo_files",
        return_value=["/tmp/test/firmware/repo-dls/test-dir/file.bin"],  # nosec B108
    )

    mock_run_menu.return_value = mock_selected_files
    config = {"DOWNLOAD_DIR": "/tmp/test"}  # nosec B108

    repo_downloader.main(config)

    mock_startfile.assert_called_once()
