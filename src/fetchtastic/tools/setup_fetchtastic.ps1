param(
    [ValidateSet('uv', 'pip', 'pipx')]
    [string]$Installer = 'uv'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable exited with code $LASTEXITCODE"
    }
}

function Add-UserPath {
    param([string]$Directory)
    $userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($Directory -notin ($userPath -split ';')) {
        $updated = if ($userPath) { "$userPath;$Directory" } else { $Directory }
        [System.Environment]::SetEnvironmentVariable('PATH', $updated, 'User')
    }
    if ($Directory -notin ($env:PATH -split ';')) {
        $env:PATH = "$env:PATH;$Directory"
    }
}

Write-Host "Installing Fetchtastic with $Installer..." -ForegroundColor Cyan
$isUpgrade = [bool](Get-Command fetchtastic -ErrorAction SilentlyContinue)
switch ($Installer) {
    'uv' {
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uv) {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            $uvDirectory = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
            Add-UserPath $uvDirectory
            $uv = Get-Command uv -ErrorAction Stop
        }
        Invoke-Checked $uv.Source @('tool', 'install', '--upgrade', '--python', '>=3.10', 'fetchtastic[win]')
        Invoke-Checked $uv.Source @('tool', 'update-shell')
        $toolBin = & $uv.Source tool dir --bin
        if ($LASTEXITCODE -ne 0) { throw 'Unable to locate uv tool executables' }
        Add-UserPath $toolBin.Trim()
        $fetchtastic = Join-Path $toolBin.Trim() 'fetchtastic.exe'
    }
    'pip' {
        $python = Get-Command python -ErrorAction Stop
        Invoke-Checked $python.Source @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or later is required")')
        $venvDirectory = Join-Path $env:LOCALAPPDATA 'Fetchtastic\venv'
        $expectedCommand = Join-Path $venvDirectory 'Scripts\fetchtastic.exe'
        $existingCommand = Get-Command fetchtastic -ErrorAction SilentlyContinue
        if ($existingCommand -and $existingCommand.Source -ne $expectedCommand) {
            throw 'Fetchtastic belongs to another installation. Uninstall that package before switching to pip.'
        }
        Invoke-Checked $python.Source @('-m', 'venv', $venvDirectory)
        $venvPython = Join-Path $venvDirectory 'Scripts\python.exe'
        Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'fetchtastic[win]')
        $toolBin = Join-Path $venvDirectory 'Scripts'
        Add-UserPath $toolBin
        $fetchtastic = Join-Path $toolBin 'fetchtastic.exe'
    }
    'pipx' {
        $pipx = Get-Command pipx -ErrorAction Stop
        Invoke-Checked $pipx.Source @('install', 'fetchtastic[win]')
        Invoke-Checked $pipx.Source @('upgrade', 'fetchtastic')
        Invoke-Checked $pipx.Source @('ensurepath')
        $toolBin = & $pipx.Source environment --value PIPX_BIN_DIR
        if ($LASTEXITCODE -ne 0) { throw 'Unable to locate pipx tool executables' }
        Add-UserPath $toolBin.Trim()
        $fetchtastic = Join-Path $toolBin.Trim() 'fetchtastic.exe'
    }
}

Invoke-Checked $fetchtastic @('version')
if ($isUpgrade) {
    $updateIntegrations = Read-Host 'Update Windows integrations (Start Menu shortcuts, etc.)? [y/n] (default: yes)'
    if ([string]::IsNullOrWhiteSpace($updateIntegrations) -or $updateIntegrations.Trim().ToLower() -eq 'y') {
        Invoke-Checked $fetchtastic @('setup', '--update-integrations')
    }
    $runSetup = Read-Host 'Run setup to review your configuration? [y/n] (default: no)'
    if ($runSetup.Trim().ToLower() -eq 'y') {
        Invoke-Checked $fetchtastic @('setup')
    }
} else {
    Invoke-Checked $fetchtastic @('setup')
}
Write-Host 'Installation complete!' -ForegroundColor Green
