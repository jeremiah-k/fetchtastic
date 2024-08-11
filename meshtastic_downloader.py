import os
import requests
import zipfile
import time
from datetime import datetime
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Change to the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Load configuration
env_file = ".env"
load_dotenv(env_file)

save_apks = os.getenv("SAVE_APKS", "true") == "true"
save_firmware = os.getenv("SAVE_FIRMWARE", "true") == "true"
ntfy_server = os.getenv("NTFY_SERVER", "")
android_versions_to_keep = int(os.getenv("ANDROID_VERSIONS_TO_KEEP", 2))
firmware_versions_to_keep = int(os.getenv("FIRMWARE_VERSIONS_TO_KEEP", 2))
auto_extract = os.getenv("AUTO_EXTRACT", "no") == "yes"
extract_patterns = os.getenv("EXTRACT_PATTERNS", "").split()

# Fixed repository addresses
android_releases_url = "https://api.github.com/repos/meshtastic/Meshtastic-Android/releases"
firmware_releases_url = "https://api.github.com/repos/meshtastic/firmware/releases"
download_dir = "/storage/emulated/0/Download/Meshtastic"
firmware_dir = os.path.join(download_dir, "firmware")
apks_dir = os.path.join(download_dir, "apks")
latest_android_release_file = os.path.join(apks_dir, "latest_android_release.txt")
latest_firmware_release_file = os.path.join(firmware_dir, "latest_firmware_release.txt")

# Logging setup
log_file = "fetchtastic.log"

def log_message(message):
    with open(log_file, "a") as log:
        log.write(f"{datetime.now()}: {message}\n")
    print(message)

def send_ntfy_notification(message):
    if ntfy_server:
        requests.post(ntfy_server, data=message.encode('utf-8'))

def get_latest_releases(url, versions_to_keep):
    response = requests.get(url)
    response.raise_for_status()
    releases = response.json()
    return releases[:versions_to_keep]

def download_file(url, download_path):
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    
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

def extract_files(zip_path, extract_dir, patterns):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_name in zip_ref.namelist():
                log_message(f"Checking if {file_name} matches patterns {patterns}")
                if any(pattern in file_name for pattern in patterns):
                    zip_ref.extract(file_name, extract_dir)
                    log_message(f"Extracted {file_name} to {extract_dir}")
    except zipfile.BadZipFile:
        log_message(f"Error: {zip_path} is a bad zip file and cannot be opened.")
    except Exception as e:
        log_message(f"Error: An unexpected error occurred while extracting files from {zip_path}: {e}")

def cleanup_old_versions(directory, keep_count):
    versions = sorted(
        (os.path.join(directory, d) for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))),
        key=os.path.getmtime
    )
    old_versions = versions[:-keep_count]
    for version in old_versions:
        for root, dirs, files in os.walk(version, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
                log_message(f"Removed file: {os.path.join(root, name)}")
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(version)
        log_message(f"Removed directory: {version}")

def check_and_download(releases, latest_release_file, release_type, download_dir, versions_to_keep, extract_patterns):
    downloaded_versions = []
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    latest_release_tag = releases[0]['tag_name']
    if os.path.exists(latest_release_file):
        with open(latest_release_file, 'r') as f:
            saved_release_tag = f.read().strip()
    else:
        saved_release_tag = None

    if latest_release_tag != saved_release_tag:
        log_message(f"New {release_type} release found: {latest_release_tag}")
        with open(latest_release_file, 'w') as f:
            f.write(latest_release_tag)
        for release in releases:
            if release['tag_name'] != saved_release_tag:
                release_dir = os.path.join(download_dir, release['tag_name'])
                if not os.path.exists(release_dir):
                    os.makedirs(release_dir)
                for asset in release['assets']:
                    file_name = asset['name']
                    download_path = os.path.join(release_dir, file_name)
                    download_file(asset['browser_download_url'], download_path)
                    if auto_extract and file_name.endswith('.zip'):
                        log_message(f"Attempting to extract from {download_path} to {release_dir} with patterns {extract_patterns}")
                        extract_files(download_path, release_dir, extract_patterns)
                downloaded_versions.append(release['tag_name'])
    else:
        log_message(f"All {release_type} versions are up to date.")
    
    cleanup_old_versions(download_dir, versions_to_keep)
    return downloaded_versions

def main():
    start_time = time.time()
    log_message("Starting the Meshtastic downloader...")

    downloaded_firmwares = []
    downloaded_apks = []

    if save_firmware:
        latest_firmware_releases = get_latest_releases(firmware_releases_url, firmware_versions_to_keep)
        downloaded_firmwares = check_and_download(latest_firmware_releases, latest_firmware_release_file, "Firmware", firmware_dir, firmware_versions_to_keep, extract_patterns)
        log_message(f"Latest Firmware releases: {', '.join(release['tag_name'] for release in latest_firmware_releases)}")

    if save_apks:
        latest_android_releases = get_latest_releases(android_releases_url, android_versions_to_keep)
        downloaded_apks = check_and_download(latest_android_releases, latest_android_release_file, "Android APK", apks_dir, android_versions_to_keep, extract_patterns)
        log_message(f"Latest Android APK releases: {', '.join(release['tag_name'] for release in latest_android_releases)}")

    end_time = time.time()
    log_message(f"Finished the Meshtastic downloader. Total time taken: {end_time - start_time:.2f} seconds")

    if downloaded_firmwares or downloaded_apks:
        message = ""
        if downloaded_firmwares:
            message += f"New Firmware releases {', '.join(downloaded_firmwares)} downloaded.\n"
        if downloaded_apks:
            message += f"New Android APK releases {', '.join(downloaded_apks)} downloaded.\n"
        message += f"{datetime.now()}"
        send_ntfy_notification(message)
    else:
        message = f"All Firmware and Android APK versions are up to date.\nLatest Firmware releases: {', '.join(release['tag_name'] for release in latest_firmware_releases)}\nLatest Android APK releases: {', '.join(release['tag_name'] for release in latest_android_releases)}\n{datetime.now()}"
        send_ntfy_notification(message)

if __name__ == "__main__":
    main()
