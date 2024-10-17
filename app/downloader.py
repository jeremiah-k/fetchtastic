# app/downloader.py

import os
import requests
import zipfile
import time
from datetime import datetime
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

    selected_apk_patterns = config.get('SELECTED_APK_ASSETS', [])
    selected_firmware_patterns = config.get('SELECTED_FIRMWARE_ASSETS', [])

    download_dir = config.get('DOWNLOAD_DIR', os.path.join(os.path.expanduser("~"), "storage", "downloads", "Meshtastic"))
    firmware_dir = os.path.join(download_dir, "firmware")
    apks_dir = os.path.join(download_dir, "apks")
    latest_android_release_file = os.path.join(apks_dir, "latest_android_release.txt")
    latest_firmware_release_file = os.path.join(firmware_dir, "latest_firmware_release.txt")

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

    def send_ntfy_notification(message):
        if ntfy_server and ntfy_topic:
            try:
                ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}"
                response = requests.post(ntfy_url, data=message.encode('utf-8'))
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                log_message(f"Error sending notification: {e}")
        else:
            log_message("Notifications are not configured.")

    # Function to get the latest releases and sort by date
    def get_latest_releases(url, scan_count=10):
        response = requests.get(url)
        response.raise_for_status()
        releases = response.json()
        # Sort releases by published date, descending order
        sorted_releases = sorted(releases, key=lambda r: r['published_at'], reverse=True)
        # Limit the number of releases to be scanned
        return sorted_releases[:scan_count]

    # Function to download a file with retry mechanism
    def download_file(url, download_path):
        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        
        try:
            if not os.path.exists(download_path):
                log_message(f"Downloading {url}")
                response = session.get(url, stream=True)
                response.raise_for_status()
                with open(download_path, 'wb') as file:
                    for chunk in response.iter_content(1024):
                        file.write(chunk)
                log_message(f"Downloaded {download_path}")
            else:
                log_message(f"{download_path} already exists, skipping download.")
        except requests.exceptions.RequestException as e:
            log_message(f"Error downloading {url}: {e}")

    # Updated extract_files function
    def extract_files(zip_path, extract_dir, patterns):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                matched_files = []
                for file_info in zip_ref.infolist():
                    file_name = file_info.filename
                    base_name = os.path.basename(file_name)
                    log_message(f"Checking file: {base_name}")
                    for pattern in patterns:
                        if pattern in base_name:
                            # Extract and flatten directory structure
                            source = zip_ref.open(file_info)
                            target_path = os.path.join(extract_dir, base_name)
                            with open(target_path, 'wb') as target_file:
                                target_file.write(source.read())
                            log_message(f"Extracted {base_name} to {extract_dir}")
                            matched_files.append(base_name)
                            break  # Stop checking patterns for this file
                if not matched_files:
                    log_message(f"No files matched the extraction patterns in {zip_path}.")
        except zipfile.BadZipFile:
            log_message(f"Error: {zip_path} is a bad zip file and cannot be opened.")
        except Exception as e:
            log_message(f"Error: An unexpected error occurred while extracting files from {zip_path}: {e}")

    # Cleanup function to keep only specific versions based on release tags
    def cleanup_old_versions(directory, releases_to_keep):
        versions = [d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]
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

    # Function to check for missing releases and download them if necessary
    def check_and_download(releases, latest_release_file, release_type, download_dir, versions_to_keep, extract_patterns, selected_patterns=None):
        downloaded_versions = []

        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        # Load the latest release tag from file if available
        saved_release_tag = None
        if os.path.exists(latest_release_file):
            with open(latest_release_file, 'r') as f:
                saved_release_tag = f.read().strip()

        # Determine which releases to download
        releases_to_download = releases[:versions_to_keep]

        for release in releases_to_download:
            release_tag = release['tag_name']
            release_dir = os.path.join(download_dir, release_tag)

            if os.path.exists(release_dir) or release_tag == saved_release_tag:
                log_message(f"Skipping version {release_tag}, already exists.")
            else:
                # Proceed to download this version
                os.makedirs(release_dir, exist_ok=True)
                log_message(f"Downloading new version: {release_tag}")
                for asset in release['assets']:
                    file_name = asset['name']
                    # Modify the matching logic here
                    if selected_patterns:
                        matched = False
                        for pattern in selected_patterns:
                            if pattern in file_name:
                                matched = True
                                break
                        if not matched:
                            continue  # Skip this asset
                    download_path = os.path.join(release_dir, file_name)
                    download_file(asset['browser_download_url'], download_path)
                    if auto_extract and file_name.endswith('.zip') and release_type == "Firmware":
                        extract_files(download_path, release_dir, extract_patterns)
                downloaded_versions.append(release_tag)

        # Update latest_release_file with the most recent tag
        if releases_to_download:
            with open(latest_release_file, 'w') as f:
                f.write(releases_to_download[0]['tag_name'])

        # Create a list of all release tags to keep
        release_tags_to_keep = [release['tag_name'] for release in releases_to_download]

        # Clean up old versions
        cleanup_old_versions(download_dir, release_tags_to_keep)
        return downloaded_versions

    start_time = time.time()
    log_message("Starting Fetchtastic...")

    downloaded_firmwares = []
    downloaded_apks = []

    # URLs for releases
    android_releases_url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
    firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"

    # Increase scan count to cover more releases for cleanup
    releases_to_scan = 10

    latest_firmware_releases = []
    latest_android_releases = []

    if save_firmware and selected_firmware_patterns:
        versions_to_download = firmware_versions_to_keep
        latest_firmware_releases = get_latest_releases(firmware_releases_url, releases_to_scan)
        downloaded_firmwares = check_and_download(
            latest_firmware_releases,
            latest_firmware_release_file,
            "Firmware",
            firmware_dir,
            firmware_versions_to_keep,
            extract_patterns,
            selected_patterns=selected_firmware_patterns
        )
        log_message(f"Latest Firmware releases: {', '.join(release['tag_name'] for release in latest_firmware_releases[:versions_to_download])}")
    elif not selected_firmware_patterns:
        log_message("No firmware assets selected. Skipping firmware download.")

    if save_apks and selected_apk_patterns:
        versions_to_download = android_versions_to_keep
        latest_android_releases = get_latest_releases(android_releases_url, releases_to_scan)
        downloaded_apks = check_and_download(
            latest_android_releases,
            latest_android_release_file,
            "Android APK",
            apks_dir,
            android_versions_to_keep,
            extract_patterns,
            selected_patterns=selected_apk_patterns
        )
        log_message(f"Latest Android APK releases: {', '.join(release['tag_name'] for release in latest_android_releases[:versions_to_download])}")
    elif not selected_apk_patterns:
        log_message("No APK assets selected. Skipping APK download.")

    end_time = time.time()
    total_time = end_time - start_time
    log_message(f"Finished the Meshtastic downloader. Total time taken: {total_time:.2f} seconds")

    # Send notification if there are new downloads
    if downloaded_firmwares or downloaded_apks:
        message = ""
        if downloaded_firmwares:
            message += f"New Firmware releases {', '.join(downloaded_firmwares)} downloaded.\n"
        if downloaded_apks:
            message += f"New Android APK releases {', '.join(downloaded_apks)} downloaded.\n"
        message += f"{datetime.now()}"
        send_ntfy_notification(message)
    else:
        if latest_firmware_releases or latest_android_releases:
            message = (
                f"All Firmware and Android APK versions are up to date.\n"
                f"Latest Firmware releases: {', '.join(release['tag_name'] for release in latest_firmware_releases[:versions_to_download])}\n"
                f"Latest Android APK releases: {', '.join(release['tag_name'] for release in latest_android_releases[:versions_to_download])}\n"
                f"{datetime.now()}"
            )
            send_ntfy_notification(message)
        else:
            log_message("No releases found to check for updates.")

if __name__ == "__main__":
    main()
