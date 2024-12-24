# app/downloader.py

import json
import os
import re
import time
import zipfile
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import setup_config


def main():
    # Load configuration
    config = setup_config.load_config()
    if not config:
        print("Configuration not found. Please run 'fetchtastic setup' first.")
        return

    # Get configuration values
    save_apks = config.get("SAVE_APKS", False)
    save_firmware = config.get("SAVE_FIRMWARE", False)
    ntfy_server = config.get("NTFY_SERVER", "")
    ntfy_topic = config.get("NTFY_TOPIC", "")
    android_versions_to_keep = config.get("ANDROID_VERSIONS_TO_KEEP", 2)
    firmware_versions_to_keep = config.get("FIRMWARE_VERSIONS_TO_KEEP", 2)
    auto_extract = config.get("AUTO_EXTRACT", False)
    extract_patterns = config.get("EXTRACT_PATTERNS", [])
    exclude_patterns = config.get("EXCLUDE_PATTERNS", [])
    wifi_only = config.get("WIFI_ONLY", False) if setup_config.is_termux() else False
    notify_on_download_only = config.get("NOTIFY_ON_DOWNLOAD_ONLY", False)

    selected_apk_patterns = config.get("SELECTED_APK_ASSETS", [])
    selected_firmware_patterns = config.get("SELECTED_FIRMWARE_ASSETS", [])

    download_dir = config.get(
        "DOWNLOAD_DIR",
        os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"),
    )
    firmware_dir = os.path.join(download_dir, "firmware")
    apks_dir = os.path.join(download_dir, "apks")
    latest_android_release_file = os.path.join(apks_dir, "latest_android_release.txt")
    latest_firmware_release_file = os.path.join(
        firmware_dir, "latest_firmware_release.txt"
    )

    # Create necessary directories
    for dir_path in [download_dir, firmware_dir, apks_dir]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

    # Logging setup
    log_file = os.path.join(download_dir, "fetchtastic.log")

    def log_message(message):
        with open(log_file, "a") as log:
            log.write(f"{datetime.now()}: {message}\n")
        print(message)

    def send_ntfy_notification(message, title=None):
        if ntfy_server and ntfy_topic:
            try:
                ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
                headers = {
                    "Content-Type": "text/plain; charset=utf-8",
                }
                if title:
                    headers["Title"] = title
                response = requests.post(
                    ntfy_url, data=message.encode("utf-8"), headers=headers, timeout=10
                )
                response.raise_for_status()
                log_message(f"Notification sent to {ntfy_url}")
            except requests.exceptions.RequestException as e:
                log_message(f"Error sending notification to {ntfy_url}: {e}")
        else:
            # Don't log when notifications are not configured
            pass

    # Function to get the latest releases and sort by date
    def get_latest_releases(url, scan_count=10):
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        releases = response.json()
        # Sort releases by published date, descending order
        sorted_releases = sorted(
            releases, key=lambda r: r["published_at"], reverse=True
        )
        # Limit the number of releases to be scanned
        return sorted_releases[:scan_count]

    # Function to download a file with retry mechanism
    def download_file(url, download_path):
        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        try:
            if not os.path.exists(download_path):
                log_message(f"Downloading {url}")
                response = session.get(url, stream=True)
                response.raise_for_status()
                with open(download_path, "wb") as file:
                    for chunk in response.iter_content(1024):
                        file.write(chunk)
                log_message(f"Downloaded {download_path}")
            else:
                # Don't log when the file already exists
                pass
        except requests.exceptions.RequestException as e:
            log_message(f"Error downloading {url}: {e}")

    def is_connected_to_wifi():
        if setup_config.is_termux():
            # Termux-specific Wi-Fi check
            try:
                result = os.popen("termux-wifi-connectioninfo").read()
                if not result:
                    # If result is empty, assume not connected
                    return False
                data = json.loads(result)
                supplicant_state = data.get("supplicant_state", "")
                ip_address = data.get("ip", "")
                if supplicant_state == "COMPLETED" and ip_address != "":
                    return True
                else:
                    return False
            except Exception as e:
                log_message(f"Error checking Wi-Fi connection: {e}")
                return False
        else:
            # For non-Termux environments, assume connected
            return True

    # Function to extract files from zip archives
    def extract_files(zip_path, extract_dir, patterns, exclude_patterns):
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                matched_files = []
                for file_info in zip_ref.infolist():
                    file_name = file_info.filename
                    base_name = os.path.basename(file_name)
                    # Skip directories
                    if not base_name:
                        continue
                    # Check if file matches exclude patterns
                    if any(exclude in base_name for exclude in exclude_patterns):
                        continue
                    # Strip version numbers from the file name
                    stripped_base_name = strip_version_numbers(base_name)
                    for pattern in patterns:
                        if pattern in stripped_base_name:
                            # Check if the file already exists
                            target_path = os.path.join(extract_dir, base_name)
                            if not os.path.exists(target_path):
                                # Extract and flatten directory structure
                                source = zip_ref.open(file_info)
                                with open(target_path, "wb") as target_file:
                                    target_file.write(source.read())
                                log_message(f"Extracted {base_name} to {extract_dir}")
                            # If the file is a .sh script, check permissions
                            if base_name.endswith(".sh"):
                                # Check if the file has executable permissions
                                if not os.access(target_path, os.X_OK):
                                    os.chmod(target_path, 0o755)
                                    log_message(
                                        f"Set executable permissions for {base_name}"
                                    )
                            matched_files.append(base_name)
                            break  # Stop checking patterns for this file
        except zipfile.BadZipFile:
            log_message(f"Error: {zip_path} is a bad zip file and cannot be opened.")
        except Exception as e:
            log_message(
                f"Error: An unexpected error occurred while extracting files from {zip_path}: {e}"
            )

    # Function to strip version numbers from filenames
    def strip_version_numbers(filename):
        """
        Removes version numbers and commit hashes from the filename.
        Uses the same regex as in menu_firmware.py to ensure consistency.
        """
        # Regular expression matching version numbers and commit hashes
        base_name = re.sub(r"([_-])\d+\.\d+\.\d+(?:\.[\da-f]+)?", r"\1", filename)
        return base_name

    # Cleanup function to keep only specific versions based on release tags
    def cleanup_old_versions(directory, releases_to_keep):
        versions = [
            d
            for d in os.listdir(directory)
            if os.path.isdir(os.path.join(directory, d))
        ]
        for version in versions:
            if version not in releases_to_keep:
                version_path = os.path.join(directory, version)
                for root, dirs, files in os.walk(version_path, topdown=False):
                    for name in files:
                        os.remove(os.path.join(root, name))
                        log_message(f"Removed file: {os.path.join(root, name)}")
                    for name in dirs:
                        os.rmdir(os.path.join(root, name))
                os.rmdir(version_path)
                log_message(f"Removed directory: {version_path}")

    # Function to strip out non-printable characters and emojis
    def strip_unwanted_chars(text):
        """
        Strips out non-printable characters and emojis from a string.
        """
        # Regex for printable characters (including some extended ASCII)
        printable_regex = re.compile(r"[^\x00-\x7F]+")
        return printable_regex.sub("", text)

    # Function to check for missing releases and download them if necessary
    def check_and_download(
        releases,
        latest_release_file,
        release_type,
        download_dir,
        versions_to_keep,
        extract_patterns,
        selected_patterns=None,
    ):
        downloaded_versions = []
        new_versions_available = []
        actions_taken = False  # Track if any actions were taken

        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        # Load the latest release tag from file if available
        saved_release_tag = None
        if os.path.exists(latest_release_file):
            with open(latest_release_file, "r") as f:
                saved_release_tag = f.read().strip()

        # Determine which releases to download
        releases_to_download = releases[:versions_to_keep]

        if downloads_skipped:
            # Collect new versions available
            for release in releases_to_download:
                release_tag = release["tag_name"]
                if release_tag != saved_release_tag:
                    new_versions_available.append(release_tag)
            return downloaded_versions, new_versions_available

        for release in releases_to_download:
            release_tag = release["tag_name"]
            release_dir = os.path.join(download_dir, release_tag)
            release_notes_file = os.path.join(
                release_dir, f"release_notes-{release_tag}.md"
            )

            # Create release directory if it doesn't exist
            if not os.path.exists(release_dir):
                os.makedirs(release_dir, exist_ok=True)

            # Download release notes if missing
            if not os.path.exists(release_notes_file) and release.get("body"):
                log_message(f"Downloading release notes for version {release_tag}.")
                release_notes_content = strip_unwanted_chars(release["body"])
                with open(release_notes_file, "w", encoding="utf-8") as notes_file:
                    notes_file.write(release_notes_content)
                log_message(f"Saved release notes to {release_notes_file}")

            assets_to_download = []
            for asset in release["assets"]:
                file_name = asset["name"]
                # Strip version numbers from the file name
                stripped_file_name = strip_version_numbers(file_name)
                # Matching logic
                if selected_patterns:
                    if not any(
                        pattern in stripped_file_name for pattern in selected_patterns
                    ):
                        continue  # Skip this asset
                download_path = os.path.join(release_dir, file_name)
                if not os.path.exists(download_path):
                    assets_to_download.append(
                        (asset["browser_download_url"], download_path)
                    )

            if assets_to_download:
                actions_taken = True
                log_message(f"Downloading missing assets for version {release_tag}.")
                for url, path in assets_to_download:
                    download_file(url, path)
                downloaded_versions.append(release_tag)

                # Extraction logic
                if auto_extract and release_type == "Firmware":
                    for asset in release["assets"]:
                        file_name = asset["name"]
                        if file_name.endswith(".zip"):
                            zip_path = os.path.join(release_dir, file_name)
                            if os.path.exists(zip_path):
                                extraction_needed = check_extraction_needed(
                                    zip_path,
                                    release_dir,
                                    extract_patterns,
                                    exclude_patterns,
                                )
                                if extraction_needed:
                                    log_message(f"Extracting files from {zip_path}...")
                                    extract_files(
                                        zip_path,
                                        release_dir,
                                        extract_patterns,
                                        exclude_patterns,
                                    )
            else:
                # No action needed for this release
                pass

            # Set permissions on .sh files if needed
            set_permissions_on_sh_files(release_dir)

        # Only update latest_release_file if downloads occurred
        if downloaded_versions:
            with open(latest_release_file, "w") as f:
                f.write(downloaded_versions[0])

        # Create a list of all release tags to keep
        release_tags_to_keep = [release["tag_name"] for release in releases_to_download]

        # Clean up old versions
        cleanup_old_versions(download_dir, release_tags_to_keep)

        if not actions_taken:
            log_message(f"All {release_type} assets are up to date.")

        # Collect new versions available
        for release in releases_to_download:
            release_tag = release["tag_name"]
            if (
                release_tag != saved_release_tag
                and release_tag not in downloaded_versions
            ):
                new_versions_available.append(release_tag)

        return downloaded_versions, new_versions_available

    def set_permissions_on_sh_files(directory):
        """
        Sets executable permissions on .sh files if they do not already have them.
        """
        for root, _dirs, files in os.walk(directory):
            for file in files:
                if file.endswith(".sh"):
                    file_path = os.path.join(root, file)
                    if not os.access(file_path, os.X_OK):
                        os.chmod(file_path, 0o755)
                        log_message(f"Set executable permissions for {file}")

    def check_extraction_needed(zip_path, extract_dir, patterns, exclude_patterns):
        """
        Checks if extraction is needed based on the current extraction patterns.
        Returns True if any files matching the patterns are not already extracted.
        """
        files_to_extract = []
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                file_name = file_info.filename
                base_name = os.path.basename(file_name)
                # Skip directories
                if not base_name:
                    continue
                # Check if file matches exclude patterns
                if any(exclude in base_name for exclude in exclude_patterns):
                    continue
                # Strip version numbers from the file name
                stripped_base_name = strip_version_numbers(base_name)
                for pattern in patterns:
                    if pattern in stripped_base_name:
                        files_to_extract.append(base_name)
                        break  # Stop checking patterns for this file
        # Now check if any of the files to extract are missing
        for base_name in files_to_extract:
            extracted_file_path = os.path.join(extract_dir, base_name)
            if not os.path.exists(extracted_file_path):
                return True  # Extraction needed
        return False  # All files already extracted

    start_time = time.time()
    log_message("Starting Fetchtastic...")

    # Check Wi-Fi connection before starting downloads (Termux only)
    downloads_skipped = False
    if setup_config.is_termux():
        if wifi_only and not is_connected_to_wifi():
            downloads_skipped = True

    # Initialize variables
    downloaded_firmwares = []
    downloaded_apks = []
    new_firmware_versions = []
    new_apk_versions = []

    # URLs for releases
    android_releases_url = (
        "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    )
    firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"

    # Increase scan count to cover more releases for cleanup
    releases_to_scan = 10

    latest_firmware_releases = []
    latest_android_releases = []

    if save_firmware and selected_firmware_patterns:
        latest_firmware_releases = get_latest_releases(
            firmware_releases_url, releases_to_scan
        )
        fw_downloaded, fw_new_versions = check_and_download(
            latest_firmware_releases,
            latest_firmware_release_file,
            "Firmware",
            firmware_dir,
            firmware_versions_to_keep,
            extract_patterns,
            selected_patterns=selected_firmware_patterns,
        )
        downloaded_firmwares.extend(fw_downloaded)
        new_firmware_versions.extend(fw_new_versions)
        if fw_downloaded:
            log_message(f"Downloaded Firmware versions: {', '.join(fw_downloaded)}")
    elif not selected_firmware_patterns:
        log_message("No firmware assets selected. Skipping firmware download.")

    if save_apks and selected_apk_patterns:
        latest_android_releases = get_latest_releases(
            android_releases_url, releases_to_scan
        )
        apk_downloaded, apk_new_versions = check_and_download(
            latest_android_releases,
            latest_android_release_file,
            "Android APK",
            apks_dir,
            android_versions_to_keep,
            extract_patterns,  # Assuming extract_patterns is needed here
            selected_patterns=selected_apk_patterns,
        )
        downloaded_apks.extend(apk_downloaded)
        new_apk_versions.extend(apk_new_versions)
        if apk_downloaded:
            log_message(f"Downloaded Android APK versions: {', '.join(apk_downloaded)}")
    elif not selected_apk_patterns:
        log_message("No APK assets selected. Skipping APK download.")

    end_time = time.time()
    total_time = end_time - start_time
    log_message(
        f"Finished the Meshtastic downloader. Total time taken: {total_time:.2f} seconds"
    )

    if downloads_skipped:
        log_message("Not connected to Wi-Fi. Skipping all downloads.")
        # Prepare notification message
        message_lines = [
            "New releases are available but downloads were skipped because the device is not connected to Wi-Fi."
        ]
        if new_firmware_versions:
            message_lines.append(
                f"Firmware versions available: {', '.join(new_firmware_versions)}"
            )
        if new_apk_versions:
            message_lines.append(
                f"Android APK versions available: {', '.join(new_apk_versions)}"
            )
        notification_message = "\n".join(message_lines) + f"\n{datetime.now()}"
        log_message("\n".join(message_lines))
        send_ntfy_notification(
            notification_message, title="Fetchtastic Downloads Skipped"
        )
    elif downloaded_firmwares or downloaded_apks:
        # Prepare notification messages
        notification_messages = []
        if downloaded_firmwares:
            message = f"Downloaded Firmware versions: {', '.join(downloaded_firmwares)}"
            notification_messages.append(message)
        if downloaded_apks:
            message = f"Downloaded Android APK versions: {', '.join(downloaded_apks)}"
            notification_messages.append(message)
        notification_message = "\n".join(notification_messages) + f"\n{datetime.now()}"
        send_ntfy_notification(
            notification_message, title="Fetchtastic Download Completed"
        )
    else:
        # No new downloads; everything is up to date
        message = f"All assets are up to date.\n" f"{datetime.now()}"
        log_message(message)
        if not notify_on_download_only:
            send_ntfy_notification(message, title="Fetchtastic Up to Date")


if __name__ == "__main__":
    main()
