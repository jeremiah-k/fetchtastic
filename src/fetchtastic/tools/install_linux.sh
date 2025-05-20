#!/bin/bash

echo "==================================="
echo "Fetchtastic Linux Installer"
echo "==================================="
echo

# Check if running as root
if [ "$EUID" -eq 0 ]; then
  echo "This script doesn't need to be run as root."
  echo "Continuing anyway..."
fi

echo
echo "This script will:"
echo " 1. Check if Python is installed"
echo " 2. Install Python if needed"
echo " 3. Install pipx"
echo " 4. Install Fetchtastic"
echo " 5. Run the Fetchtastic setup"
echo
echo "Press Ctrl+C to cancel or Enter to continue..."
read

# Check if Python is installed
if command -v python3 &>/dev/null; then
  echo "Python is already installed."
  python3 --version
else
  echo "Python is not installed. Installing Python..."
  
  # Detect package manager
  if command -v apt-get &>/dev/null; then
    # Debian/Ubuntu
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
  elif command -v dnf &>/dev/null; then
    # Fedora
    sudo dnf install -y python3 python3-pip
  elif command -v yum &>/dev/null; then
    # CentOS/RHEL
    sudo yum install -y python3 python3-pip
  elif command -v pacman &>/dev/null; then
    # Arch Linux
    sudo pacman -Sy python python-pip
  else
    echo "Could not detect package manager. Please install Python manually."
    echo "After installing Python, run this script again."
    exit 1
  fi
  
  # Verify Python installation
  if command -v python3 &>/dev/null; then
    echo "Python installed successfully."
    python3 --version
  else
    echo "Failed to install Python. Please install Python manually."
    echo "After installing Python, run this script again."
    exit 1
  fi
fi

# Install pipx
echo
echo "Installing pipx..."
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# Add pipx to PATH for this session
export PATH="$HOME/.local/bin:$PATH"

# Install Fetchtastic
echo
echo "Installing Fetchtastic..."
pipx install fetchtastic

# Run Fetchtastic setup
echo
echo "Running Fetchtastic setup..."
fetchtastic setup

echo
echo "==================================="
echo "Installation complete!"
echo "==================================="
echo
echo "You can now use Fetchtastic by running:"
echo "  fetchtastic download - to download firmware and APKs"
echo "  fetchtastic repo browse - to browse and download files from the repository"
echo
echo "Press Enter to exit..."
read
