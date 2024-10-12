#!/data/data/com.termux/files/usr/bin/sh

# Ensure necessary packages are installed
pkg update -y
pkg install -y cronie python openssl termux-api python-pip

# Install Python modules
LDFLAGS=" -lm -lcompiler_rt" pip install requests python-dotenv pick

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

# Load existing .env if it exists
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    # Load current settings
    . "$ENV_FILE"
    echo "Existing configuration found. Do you want to update it? [y/n] (default: n): "
    read update_config
    update_config=${update_config:-n}
else
    update_config="y"
fi

if [ "$update_config" = "y" ]; then
    # Prompt to save APKs, firmware, or both
    echo "Do you want to save APKs, firmware, or both? [a/f/b] (default: ${SAVE_CHOICE:-b}): "
    read save_choice
    save_choice=${save_choice:-${SAVE_CHOICE:-b}}
    case "$save_choice" in
        a) save_apks=true; save_firmware=false ;;
        f) save_apks=false; save_firmware=true ;;
        *) save_apks=true; save_firmware=true ;;
    esac
    SAVE_CHOICE="$save_choice"

    # Save the initial configuration to .env
    echo "SAVE_APKS=$save_apks" > "$ENV_FILE"
    echo "SAVE_FIRMWARE=$save_firmware" >> "$ENV_FILE"
    echo "SAVE_CHOICE=$SAVE_CHOICE" >> "$ENV_FILE"

    # Run the menu scripts based on user choices
    if [ "$save_apks" = true ]; then
        python menu_apk.py
    fi
    if [ "$save_firmware" = true ]; then
        python menu_firmware.py
    fi

    # Prompt for number of versions to keep for Android app if saving APKs
    if [ "$save_apks" = true ]; then
        echo "Enter the number of different versions of the Android app to keep (default: ${ANDROID_VERSIONS_TO_KEEP:-2}): "
        read android_versions_to_keep
        android_versions_to_keep=${android_versions_to_keep:-${ANDROID_VERSIONS_TO_KEEP:-2}}
        echo "ANDROID_VERSIONS_TO_KEEP=$android_versions_to_keep" >> "$ENV_FILE"
    fi

    # Prompt for number of versions to keep for firmware if saving firmware
    if [ "$save_firmware" = true ]; then
        echo "Enter the number of different versions of the firmware to keep (default: ${FIRMWARE_VERSIONS_TO_KEEP:-2}): "
        read firmware_versions_to_keep
        firmware_versions_to_keep=${firmware_versions_to_keep:-${FIRMWARE_VERSIONS_TO_KEEP:-2}}
        echo "FIRMWARE_VERSIONS_TO_KEEP=$firmware_versions_to_keep" >> "$ENV_FILE"
    fi

    # Prompt for automatic extraction of firmware files if saving firmware
    if [ "$save_firmware" = true ]; then
        echo "Do you want to automatically extract specific files from firmware zips? [y/n] (default: ${AUTO_EXTRACT_YN:-n}): "
        read auto_extract
        auto_extract=${auto_extract:-${AUTO_EXTRACT_YN:-n}}
        if [ "$auto_extract" = "y" ]; then
            echo "Enter the strings to match for extraction from the firmware .zip files, separated by spaces (current: '${EXTRACT_PATTERNS}'):"
            read extract_patterns
            extract_patterns=${extract_patterns:-${EXTRACT_PATTERNS}}
            if [ -z "$extract_patterns" ]; then
                echo "AUTO_EXTRACT=no" >> "$ENV_FILE"
            else
                echo "AUTO_EXTRACT=yes" >> "$ENV_FILE"
                echo "EXTRACT_PATTERNS=\"$extract_patterns\"" >> "$ENV_FILE"
            fi
        else
            echo "AUTO_EXTRACT=no" >> "$ENV_FILE"
        fi
        AUTO_EXTRACT_YN="$auto_extract"
    fi

    # Prompt for NTFY server configuration
    echo "Do you want to set up notifications via NTFY? [y/n] (default: ${NOTIFICATIONS:-y}): "
    read notifications
    notifications=${notifications:-${NOTIFICATIONS:-y}}
    NOTIFICATIONS="$notifications"

    if [ "$notifications" = "y" ]; then
        # Prompt for the NTFY server
        echo "Enter the NTFY server (default: ${NTFY_SERVER_URL:-ntfy.sh}): "
        read ntfy_server
        ntfy_server=${ntfy_server:-${NTFY_SERVER_URL:-ntfy.sh}}
        # Add https:// if not included
        case "$ntfy_server" in
            http://*|https://*) ;;  # Do nothing if it already starts with http:// or https://
            *) ntfy_server="https://$ntfy_server" ;;
        esac

        # Prompt for the topic name
        echo "Enter a unique topic name (default: ${NTFY_TOPIC_NAME:-fetchtastic-$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)}): "
        read topic_name
        topic_name=${topic_name:-${NTFY_TOPIC_NAME:-fetchtastic-$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)}}

        # Construct the full NTFY topic URL
        ntfy_topic="$ntfy_server/$topic_name"

        # Save the NTFY configuration to .env
        echo "NTFY_SERVER=\"$ntfy_topic\"" >> "$ENV_FILE"
        echo "NTFY_SERVER_URL=\"$ntfy_server\"" >> "$ENV_FILE"
        echo "NTFY_TOPIC_NAME=\"$topic_name\"" >> "$ENV_FILE"

        # Save the topic URL to topic.txt
        echo "$ntfy_topic" > "$SCRIPT_DIR/topic.txt"

        echo "Notification setup complete. Your NTFY topic URL is: $ntfy_topic"
    else
        echo "Skipping notification setup."
        echo "NTFY_SERVER=" >> "$ENV_FILE"
        rm -f "$SCRIPT_DIR/topic.txt"  # Remove the topic.txt file if notifications are disabled
    fi
else
    echo "Keeping existing configuration."
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

# Run the script once after setup and show the latest version
echo
echo "Performing first run, this may take a few minutes..."
latest_output=$(python /data/data/com.termux/files/home/fetchtastic/fetchtastic.py)

echo
echo "Setup complete. The Meshtastic downloader script will run on boot and also daily at 3 AM (if crontab entry was added)."
echo "The downloaded files will be stored in '/storage/emulated/0/Download/Meshtastic' with subdirectories 'firmware' and 'apks'."
echo "$latest_output"

# New final message logic
if [ "$NOTIFICATIONS" = "y" ]; then
    echo "Your NTFY topic URL is: $ntfy_topic"
else
    echo "Notifications are not set. To configure notifications, please rerun setup.sh."
fi
