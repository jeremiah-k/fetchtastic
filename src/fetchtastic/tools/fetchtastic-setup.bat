@echo off
title Fetchtastic Installer
echo ===================================
echo Fetchtastic Windows Installer
echo ===================================
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrator privileges.
) else (
    echo This script doesn't require administrator privileges.
)

echo.
echo This script will:
echo  1. Check if Python is installed
echo  2. Install Python if needed
echo  3. Install pipx
echo  4. Install Fetchtastic with Windows integration
echo  5. Run the Fetchtastic setup
echo.
echo Press Ctrl+C to cancel or any key to continue...
pause >nul

:: Check if Python is installed
python --version >nul 2>&1
if %errorLevel% == 0 (
    echo Python is already installed.
    python --version
) else (
    echo Python is not installed. Installing Python...
    
    :: Create a temporary directory
    mkdir %TEMP%\fetchtastic_install >nul 2>&1
    cd %TEMP%\fetchtastic_install
    
    :: Download Python installer
    echo Downloading Python installer...
    curl -L -o python_installer.exe https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe
    
    :: Install Python
    echo Installing Python...
    start /wait python_installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1
    
    :: Clean up
    cd %~dp0
    rmdir /s /q %TEMP%\fetchtastic_install >nul 2>&1
    
    :: Verify Python installation
    echo Verifying Python installation...
    python --version >nul 2>&1
    if %errorLevel% == 0 (
        echo Python installed successfully.
        python --version
    ) else (
        echo Failed to install Python. Please install Python manually from https://www.python.org/downloads/
        echo After installing Python, run this script again.
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
)

:: Install pipx
echo.
echo Installing pipx...
python -m pip install --user pipx
python -m pipx ensurepath

:: Refresh environment variables
echo Refreshing environment variables...
call refreshenv.cmd >nul 2>&1
if %errorLevel% neq 0 (
    :: If refreshenv.cmd is not available, set PATH manually
    set PATH=%USERPROFILE%\AppData\Roaming\Python\Python311\Scripts;%PATH%
    set PATH=%USERPROFILE%\AppData\Local\Programs\Python\Python311\Scripts;%PATH%
    set PATH=%USERPROFILE%\AppData\Local\Programs\Python\Python311;%PATH%
)

:: Install Fetchtastic
echo.
echo Installing Fetchtastic with Windows integration...
pipx install fetchtastic[win]

:: Run Fetchtastic setup
echo.
echo Running Fetchtastic setup...
fetchtastic setup

echo.
echo ===================================
echo Installation complete!
echo ===================================
echo.
echo You can now use Fetchtastic from the Start Menu or by running:
echo   fetchtastic download - to download firmware and APKs
echo   fetchtastic repo browse - to browse and download files from the repository
echo.
echo Press any key to exit...
pause >nul
