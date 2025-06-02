#!/bin/bash

echo "==================================="
echo "Fetchtastic Installer"
echo "==================================="
echo

# Detect OS
if [[ ${OSTYPE} == "darwin"* ]]; then
	OS_TYPE="macOS"
elif [[ ${OSTYPE} == "linux-android"* ]]; then
	OS_TYPE="Termux"
else
	OS_TYPE="Linux"
fi

echo "Detected platform: ${OS_TYPE}"
echo

# Check if running as root
if [[ ${EUID} -eq 0 ]]; then
	echo "This script doesn't need to be run as root."
	echo "Continuing anyway..."
fi

echo
echo "This script will:"
echo " 1. Check if Python is installed"
echo " 2. Install Python if needed"
if [[ ${OS_TYPE} == "macOS" ]]; then
	echo " 3. Check if Homebrew is installed"
	echo " 4. Install Homebrew if needed"
fi
echo " 3. Install pipx"
echo " 4. Install Fetchtastic"
echo " 5. Run the Fetchtastic setup"
echo
echo "Press Ctrl+C to cancel or Enter to continue..."
read -r

# macOS-specific setup
if [[ ${OS_TYPE} == "macOS" ]]; then
	# Check if Homebrew is installed
	if command -v brew &>/dev/null; then
		echo "Homebrew is already installed."
	else
		echo "Homebrew is not installed. Installing Homebrew..."
		/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true

		# Add Homebrew to PATH for this session
		ARCH=$(uname -m)
		if [[ ${ARCH} == "arm64" ]]; then
			# M1/M2 Mac
			eval "$(/opt/homebrew/bin/brew shellenv)" || true
		else
			# Intel Mac
			eval "$(/usr/local/bin/brew shellenv)" || true
		fi

		# Verify Homebrew installation
		if command -v brew &>/dev/null; then
			echo "Homebrew installed successfully."
		else
			echo "Failed to install Homebrew. Please install Homebrew manually."
			echo "After installing Homebrew, run this script again."
			exit 1
		fi
	fi
fi

# Check if Python is installed
if command -v python3 &>/dev/null; then
	echo "Python is already installed."
	python3 --version
else
	echo "Python is not installed. Installing Python..."

	if [[ ${OS_TYPE} == "macOS" ]]; then
		# macOS - use Homebrew
		brew install python
	elif [[ ${OS_TYPE} == "Termux" ]]; then
		# Termux
		pkg install python python-pip -y
	else
		# Linux - detect package manager
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
export PATH="${HOME}/.local/bin:${PATH}"

# Install Fetchtastic
echo
pipx install fetchtastic

# Run Fetchtastic setup
echo
echo "You can now run Fetchtastic setup:"
echo "  fetchtastic setup"
