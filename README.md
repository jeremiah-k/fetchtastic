# Fetchtastic Termux Setup

This repository contains a set of scripts to download the latest Meshtastic Android app and Firmware releases to your phone via Termux. It also provides optional notifications via a NTFY server. Follow the steps below to set up and run the script.

## Setup Steps

### Step 1: Install **Termux** and addons.

1. Install Termux: Download and install [Termux](https://f-droid.org/en/packages/com.termux/) from F-Droid.
2. Install Termux Boot: Download and install [Termux Boot](https://f-droid.org/en/packages/com.termux.boot/) from F-Droid.
3. Install Termux API: Download and install [Termux API](https://f-droid.org/en/packages/com.termux.api/) from F-Droid.
4. (Optional) Install ntfy: Download and install [ntfy](https://f-droid.org/en/packages/io.heckel.ntfy/) from F-Droid.

### Step 2: Request storage access for Termux API

Open Termux and run this command, allowing Termux API storage access:
```
termux-setup-storage
```

### Step 3: Install Git and Clone the Repository

Next run these commands to install git and clone the repository:
```
pkg install git -y
git clone https://github.com/jeremiah-k/fetchtastic.git
cd fetchtastic
```

### Step 3: Run the Setup Script

Run setup.sh and follow the prompts to complete the setup.
```
sh setup.sh
```
