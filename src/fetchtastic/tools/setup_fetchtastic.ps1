[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Fetchtastic Installer ===`n" -ForegroundColor Cyan

function Prompt-Key {
    Write-Host "Press any key to continue or Ctrl+C to cancel..."
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
}

function Install-Python {
    $pyVersion = "3.11.8"
    $installer = "python-$pyVersion-amd64.exe"
    $url = "https://www.python.org/ftp/python/$pyVersion/$installer"
    $dest = "$env:TEMP\$installer"

    Write-Host "Downloading Python $pyVersion..."
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

    Write-Host "Installing Python..."
    & $dest /quiet PrependPath=1 Include_launcher=1 | Out-Null
    Remove-Item $dest -Force

    Start-Sleep -Seconds 3
    $exists = Get-Command python -ErrorAction SilentlyContinue
    if (-not $exists) {
        Write-Error "Python installation failed. Install manually: https://www.python.org/downloads/"
        exit 1
    }
    Write-Host "Python installed successfully."
}

function Ensure-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host "Python not found. Installing..."
        Install-Python
    } else {
        Write-Host "Python is already installed."
    }
}

function Ensure-Pipx {
    Write-Host "Ensuring pipx is available..."
    python -m pip install --upgrade pip > $null 2>&1
    python -m pip install --user pipx > $null 2>&1
    python -m pipx ensurepath > $null 2>&1

    $envPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if (-not $pipx -and $envPath) {
        $env:PATH = $envPath
        $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    }
    if (-not $pipx) {
        Write-Error "pipx installation failed. Please restart your terminal and run this script again."
        exit 1
    }
    Write-Host "pipx installed and available."
}

function Install-Fetchtastic {
    Write-Host "Installing Fetchtastic via pipx..."
    pipx install fetchtastic[win]
}

function Run-Setup {
    Write-Host "Running fetchtastic setup..."
    fetchtastic setup
}

Prompt-Key
Ensure-Python
Ensure-Pipx
Install-Fetchtastic
Run-Setup

Write-Host "`nInstallation complete!" -ForegroundColor Green
