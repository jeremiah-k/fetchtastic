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

function Get-PyPI-Version {
    param([string]$PackageName)

    try {
        $response = Invoke-RestMethod -Uri "https://pypi.org/pypi/$PackageName/json" -TimeoutSec 10
        return $response.info.version
    } catch {
        Write-Host "Could not check PyPI version: $($_.Exception.Message)" -ForegroundColor Yellow
        return $null
    }
}

function Install-Or-Upgrade-Fetchtastic {
    Write-Host "Checking for existing Fetchtastic installation..."

    # Check if fetchtastic is already installed
    $existing = pipx list | Select-String "fetchtastic"

    if ($existing) {
        Write-Host "Fetchtastic is already installed. Checking for updates..."

        # Get current version
        $currentVersion = ""
        try {
            $versionOutput = fetchtastic version 2>$null
            if ($versionOutput -match "Fetchtastic v(\d+\.\d+\.\d+)") {
                $currentVersion = $matches[1]
                Write-Host "Current version: $currentVersion"
            }
        } catch {
            Write-Host "Could not determine current version."
        }

        # Try to upgrade first
        Write-Host "Upgrading Fetchtastic..."
        $upgradeResult = pipx upgrade fetchtastic 2>&1

        # Check if upgrade says "already at latest version" but we might not be
        if ($upgradeResult -match "already at latest version") {
            Write-Host "pipx reports already at latest version. Checking PyPI for actual latest..." -ForegroundColor Yellow

            # Check actual PyPI version
            $pypiVersion = Get-PyPI-Version "fetchtastic"
            if ($pypiVersion) {
                Write-Host "Latest version on PyPI: $pypiVersion" -ForegroundColor Cyan
                if ($currentVersion -and $currentVersion -ne $pypiVersion) {
                    Write-Host "Version mismatch detected! Current: $currentVersion, PyPI: $pypiVersion" -ForegroundColor Yellow
                }
            }

            # Try force reinstall to ensure we get the actual latest from PyPI
            Write-Host "Force reinstalling to ensure latest version..."
            pipx install fetchtastic[win] --force

            if ($LASTEXITCODE -eq 0) {
                Write-Host "Fetchtastic force reinstalled successfully!" -ForegroundColor Green
            } else {
                Write-Host "Force install failed. Trying uninstall/reinstall..." -ForegroundColor Yellow
                pipx uninstall fetchtastic --force 2>$null
                pipx install fetchtastic[win]
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Fetchtastic reinstalled successfully!" -ForegroundColor Green
                } else {
                    Write-Error "Failed to install Fetchtastic. Please check the error messages above."
                    exit 1
                }
            }
        } elseif ($LASTEXITCODE -eq 0) {
            Write-Host "Fetchtastic upgraded successfully!" -ForegroundColor Green
        } else {
            Write-Host "Upgrade failed. Trying force reinstall..." -ForegroundColor Yellow
            pipx install fetchtastic[win] --force
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Fetchtastic force reinstalled successfully!" -ForegroundColor Green
            } else {
                pipx uninstall fetchtastic --force 2>$null
                pipx install fetchtastic[win]
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Fetchtastic reinstalled successfully!" -ForegroundColor Green
                } else {
                    Write-Error "Failed to install Fetchtastic. Please check the error messages above."
                    exit 1
                }
            }
        }
    } else {
        Write-Host "Installing Fetchtastic via pipx..."
        pipx install fetchtastic[win]
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Fetchtastic installed successfully!" -ForegroundColor Green
        } else {
            Write-Error "Failed to install Fetchtastic. Please check the error messages above."
            exit 1
        }
    }

    # Verify installation
    $fetchtastic = Get-Command fetchtastic -ErrorAction SilentlyContinue
    if (-not $fetchtastic) {
        Write-Error "Fetchtastic installation verification failed. Please restart your terminal and try again."
        exit 1
    }

    # Show final version and check against PyPI
    try {
        $finalVersion = fetchtastic version 2>$null
        if ($finalVersion) {
            Write-Host "Final version: $finalVersion" -ForegroundColor Cyan

            # Extract version number for comparison
            if ($finalVersion -match "Fetchtastic v(\d+\.\d+\.\d+)") {
                $installedVersion = $matches[1]
                $pypiVersion = Get-PyPI-Version "fetchtastic"

                if ($pypiVersion -and $installedVersion -ne $pypiVersion) {
                    Write-Host "Note: PyPI shows version $pypiVersion, but you have $installedVersion" -ForegroundColor Yellow
                    Write-Host "This may indicate a delay in PyPI publishing or local cache issues." -ForegroundColor Yellow
                    Write-Host "If you experience issues, try: pipx uninstall fetchtastic && pipx install fetchtastic[win]" -ForegroundColor Cyan
                }
            }
        }
    } catch {
        Write-Host "Installation complete, but could not verify version."
    }
}

function Run-Setup {
    param([bool]$IsUpgrade = $false)

    if ($IsUpgrade) {
        Write-Host "`nFetchtastic has been upgraded successfully!" -ForegroundColor Green

        # Check if Windows integrations need updating
        $updateIntegrations = (
            input "Would you like to update Windows integrations (Start Menu shortcuts, etc.)? [y/n] (default: yes): "
        ).strip().lower() -or "y"

        if ($updateIntegrations -eq "y") {
            Write-Host "Updating Windows integrations..."
            fetchtastic setup --update-integrations 2>$null
            if ($LASTEXITCODE -ne 0) {
                # Fallback: run full setup but skip most prompts
                Write-Host "Integration update failed. Running setup to refresh integrations..."
                fetchtastic setup
            }
        }

        $runSetup = (
            input "Would you like to run the full setup to review/update your configuration? [y/n] (default: no): "
        ).strip().lower() -or "n"

        if ($runSetup -eq "y") {
            Write-Host "Running fetchtastic setup..."
            fetchtastic setup
        } else {
            Write-Host "Setup skipped. You can run 'fetchtastic setup' later to modify your configuration."
        }
    } else {
        Write-Host "Running fetchtastic setup..."
        fetchtastic setup
    }
}

Prompt-Key
Ensure-Python
Ensure-Pipx

# Check if this is an upgrade (fetchtastic already exists)
$isUpgrade = $false
$existing = pipx list | Select-String "fetchtastic"
if ($existing) {
    $isUpgrade = $true
}

Install-Or-Upgrade-Fetchtastic
Run-Setup -IsUpgrade $isUpgrade

Write-Host "`nInstallation complete!" -ForegroundColor Green
