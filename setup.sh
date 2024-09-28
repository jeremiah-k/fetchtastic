#!/data/data/com.termux/files/usr/bin/sh

# Ensure necessary packages are installed
pkg update -y
pkg install cronie python openssl termux-api -y

# Install Python requests and dotenv module
LDFLAGS=" -lm -lcompiler_rt" pip install requests python-dotenv

# Create the boot script directory if it doesn't exist
mkdir -p ~/.termux/boot

# Create the start-crond.sh script in the boot directory
echo '#!/data/data/com.termux/files/usr/bin/sh' > ~/.termux/boot/start-crond.sh
echo 'crond' >> ~/.termux/boot/start-crond.sh
echo 'python /data/data/com.termux/files/home/fetchtastic/fetchtastic.py' >> ~/.termux/boot/start-crond.sh
chmod +x ~/.termux/boot/start-crond.sh

# Add a separator and some spacing
echo "--------------------------------------------------------"
echo

# Get the directory of the setup.sh script
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

# Prompt to save APKs, firmware, or both
echo "Do you want to save APKs, firmware, or both? [a/f/b] (default: b): "
read save_choice
save_choice=${save_choice:-b}
case "$save_choice" in
    a) save_apks=true; save_firmware=false ;;
    f) save_apks=false; save_firmware=true ;;
    *) save_apks=true; save_firmware=true ;;
esac

# Save the configuration to .env
echo "SAVE_APKS=$save_apks" > "$SCRIPT_DIR/.env"
echo "SAVE_FIRMWARE=$save_firmware" >> "$SCRIPT_DIR/.env"

# Prompt for number of versions to keep for Android app if saving APKs
if [ "$save_apks" = true ]; then
    echo "Enter the number of different versions of the Android app to keep (default: 2): "
    read android_versions_to_keep
    android_versions_to_keep=${android_versions_to_keep:-2}
    echo "ANDROID_VERSIONS_TO_KEEP=$android_versions_to_keep" >> "$SCRIPT_DIR/.env"
fi

# Prompt for number of versions to keep for firmware if saving firmware
if [ "$save_firmware" = true ]; then
    echo "Enter the number of different versions of the firmware to keep (default: 2): "
    read firmware_versions_to_keep
    firmware_versions_to_keep=${firmware_versions_to_keep:-2}
    echo "FIRMWARE_VERSIONS_TO_KEEP=$firmware_versions_to_keep" >> "$SCRIPT_DIR/.env"

    # Prompt for automatic extraction of firmware files if saving firmware
    echo "Do you want to automatically extract specific files from firmware zips? [y/n] (default: n): "
    read auto_extract
    auto_extract=${auto_extract:-n}
    if [ "$auto_extract" = "y" ]; then
        echo "Enter the strings to match for extraction from the main firmware .zip file, separated by spaces (example: 'rak4631-'):"
        read extract_patterns
        if [ -z "$extract_patterns" ]; then
            echo "AUTO_EXTRACT=no" >> "$SCRIPT_DIR/.env"
        else
            echo "AUTO_EXTRACT=yes" >> "$SCRIPT_DIR/.env"
            echo "EXTRACT_PATTERNS=\"$extract_patterns\"" >> "$SCRIPT_DIR/.env"
        fi
    else
        echo "AUTO_EXTRACT=no" >> "$SCRIPT_DIR/.env"
    fi
fi

# Check for existing cron jobs related to fetchtastic.py
existing_cron=$(crontab -l 2>/dev/null | grep 'fetchtastic.py')

if [ -n "$existing_cron" ]; then
    echo "An existing cron job for fetchtastic.py was found:"
    echo "$existing_cron"
    read -p "Do you want to keep the existing crontab entry for running the script daily at 3 AM? [y/n] (default: y): " keep_cron
    keep_cron=${keep_cron:-y}

    if [ "$keep_cron" = "n" ]; then
        (crontab -l 2>/dev/null | grep -v 'fetchtastic.py') | crontab -
        echo "Crontab entry removed."
        read -p "Do you want to add a new crontab entry to run the script daily at 3 AM? [y/n] (default: y): " add_cron
        add_cron=${add_cron:-y}
        if [ "$add_cron" = "y" ]; then
            (crontab -l 2>/dev/null; echo "0 3 * * * python /data/data/com.termux/files/home/fetchtastic/fetchtastic.py") | crontab -
            echo "Crontab entry added."
        else
            echo "Skipping crontab installation."
        fi
    else
        echo "Keeping existing crontab entry."
    fi
else
    read -p "Do you want to add a crontab entry to run the script daily at 3 AM? [y/n] (default: y): " add_cron
    add_cron=${add_cron:-y}
    if [ "$add_cron" = "y" ]; then
        (crontab -l 2>/dev/null; echo "0 3 * * * python /data/data/com.termux/files/home/fetchtastic/fetchtastic.py") | crontab -
        echo "Crontab entry added."
    else
        echo "Skipping crontab installation."
    fi
fi

# Prompt for NTFY server configuration
echo "Do you want to set up notifications via NTFY? [y/n] (default: y): "
read notifications
notifications=${notifications:-y}

if [ "$notifications" = "y" ]; then
    # Prompt for the NTFY server
    echo "Enter the NTFY server (default: ntfy.sh): "
    read ntfy_server
    ntfy_server=${ntfy_server:-ntfy.sh}
    # Add https:// if not included
    case "$ntfy_server" in
        http://*|https://*) ;;  # Do nothing if it already starts with http:// or https://
        *) ntfy_server="https://$ntfy_server" ;;
    esac

    # Prompt for the topic name
    echo "Enter a unique topic name (default: fetchtastic-{random}): "
    read topic_name
    if [ -z "$topic_name" ]; then
        topic_name="fetchtastic-$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)"
    fi

    # Construct the full NTFY topic URL
    ntfy_topic="$ntfy_server/$topic_name"

    # Save the NTFY configuration to .env
    echo "NTFY_SERVER=$ntfy_topic" >> "$SCRIPT_DIR/.env"

    # Save the topic URL to topic.txt
    echo "$ntfy_topic" > "$SCRIPT_DIR/topic.txt"

    echo "Notification setup complete. Your NTFY topic URL is: $ntfy_topic"
else
    echo "Skipping notification setup."
    echo "NTFY_SERVER=" >> "$SCRIPT_DIR/.env"
    rm -f "$SCRIPT_DIR/topic.txt"  # Remove the topic.txt file if notifications are disabled
fi

# Run the script once after setup and show the latest version
echo
echo "Performing first run, this may take a few minutes..."
latest_output=$(python /data/data/com.termux/files/home/fetchtastic/fetchtastic.py)

echo
echo "Setup complete. The Meshtastic downloader script will run on boot and also daily at 3 AM (if crontab entry was added)."
echo "The downloaded files will be stored in '/storage/emulated/0/Download/Meshtastic' with subdirectories 'firmware' and 'apks'."
echo "$latest_output"

# New final message logic
if [ "$notifications" = "y" ]; then
    echo "Your NTFY topic URL is: $ntfy_topic"
else
    echo "Notifications are not set. To configure notifications, please rerun setup.sh."
fi
