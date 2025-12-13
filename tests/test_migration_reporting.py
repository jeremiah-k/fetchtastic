from pathlib import Path

from fetchtastic.download.interfaces import DownloadResult
from fetchtastic.download.migration import DownloadMigration


def test_migration_does_not_report_skipped_assets_as_downloads(tmp_path, monkeypatch):
    migration = DownloadMigration({"DOWNLOAD_DIR": str(tmp_path)})

    monkeypatch.setattr(
        migration.android_downloader, "get_latest_release_tag", lambda: "v1.0.0"
    )
    monkeypatch.setattr(
        migration.firmware_downloader, "get_latest_release_tag", lambda: "v1.0.0"
    )

    skipped_firmware = DownloadResult(
        success=True,
        release_tag="v2.0.0",
        file_path=Path(tmp_path / "firmware" / "v2.0.0" / "firmware.zip"),
        file_type="firmware",
        was_skipped=True,
    )
    skipped_android = DownloadResult(
        success=True,
        release_tag="v2.0.0",
        file_path=Path(tmp_path / "android" / "v2.0.0" / "app.apk"),
        file_type="android",
        was_skipped=True,
    )

    downloaded, new_fw, apks, new_apks = migration._convert_results_to_legacy_format(
        [skipped_firmware, skipped_android]
    )

    assert downloaded == []
    assert new_fw == []
    assert apks == []
    assert new_apks == []
