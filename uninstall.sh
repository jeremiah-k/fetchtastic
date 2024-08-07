#!/data/data/com.termux/files/usr/bin/sh

# Remove the crontab entry for meshtastic_downloader.py
existing_cron=$(crontab -l 2>/dev/null | grep 'meshtastic_downloader.py')

if [ -n "$existing_cron" ]; then
    (crontab -l 2>/dev/null | grep -v 'meshtastic_downloader.py') | crontab -
    echo "Crontab entry for meshtastic_downloader.py removed."
else
    echo "No crontab entry for meshtastic_downloader.py found."
fi

# Remove the start-crond.sh script from the boot directory
if [ -f ~/.termux/boot/start-crond.sh ]; then
    rm ~/.termux/boot/start-crond.sh
    echo "start-crond.sh removed from the boot directory."
else
    echo "start-crond.sh not found in the boot directory."
fi

# Remove the .env file and topic.txt if they exist
if [ -f ~/fetchtastic/.env ]; then
    rm ~/fetchtastic/.env
    echo ".env file removed."
else
    echo ".env file not found."
fi

if [ -f ~/fetchtastic/topic.txt ]; then
    rm ~/fetchtastic/topic.txt
    echo "topic.txt removed."
else
    echo "topic.txt not found."
fi

# Remove version tracking files if they exist
if [ -f /storage/emulated/0/Download/Meshtastic/apks/latest_android_release.txt ]; then
    rm /storage/emulated/0/Download/Meshtastic/apks/latest_android_release.txt
    echo "latest_android_release.txt removed."
else
    echo "latest_android_release.txt not found."
fi

if [ -f /storage/emulated/0/Download/Meshtastic/firmware/latest_firmware_release.txt ]; then
    rm /storage/emulated/0/Download/Meshtastic/firmware/latest_firmware_release.txt
    echo "latest_firmware_release.txt removed."
else
    echo "latest_firmware_release.txt not found."
fi

# Ask to remove downloaded firmware directory
read -p "Do you want to remove the downloaded firmware directory? [y/n] (default: n): " remove_firmware_dir
remove_firmware_dir=${remove_firmware_dir:-n}
if [ "$remove_firmware_dir" = "y" ]; then
    if [ -d /storage/emulated/0/Download/Meshtastic/firmware ]; then
        rm -r /storage/emulated/0/Download/Meshtastic/firmware
        echo "Firmware directory removed."
    else
        echo "Firmware directory not found."
    fi
else
    echo "Skipping removal of firmware directory."
fi

# Ask to remove downloaded APKs directory
read -p "Do you want to remove the downloaded APKs directory? [y/n] (default: n): " remove_apks_dir
remove_apks_dir=${remove_apks_dir:-n}
if [ "$remove_apks_dir" = "y" ]; then
    if [ -d /storage/emulated/0/Download/Meshtastic/apks ]; then
        rm -r /storage/emulated/0/Download/Meshtastic/apks
        echo "APKs directory removed."
    else
        echo "APKs directory not found."
    fi
else
    echo "Skipping removal of APKs directory."
fi

# Inform user about remaining files
echo "Uninstall complete. If you want to remove the remaining downloaded files, delete the '/storage/emulated/0/Download/Meshtastic' directory."
echo "You may also want to remove the fetchtastic repository by deleting the '~/fetchtastic' directory."
