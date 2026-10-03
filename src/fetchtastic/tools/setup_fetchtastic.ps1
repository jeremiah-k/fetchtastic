param(
    [ValidateSet('auto', 'uv', 'pip', 'pipx')]
    [string]$Installer = 'auto'
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

function Test-UvOwnsFetchtastic {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { return $false }
    $listing = & $uv.Source tool list 2>$null
    return $LASTEXITCODE -eq 0 -and [bool]($listing -match '(?m)^fetchtastic(?:\s|$)')
}

function Get-PipxList {
    $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if (-not $pipx) { return $null }
    $listing = & $pipx.Source list --short 2>$null
    if ($LASTEXITCODE -ne 0) {
        $listing = & $pipx.Source list 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
    }
    return ($listing -join "`n")
}

function Test-PipxOwnsFetchtastic {
    $listing = Get-PipxList
    return $null -ne $listing -and [bool]($listing -match '(?m)(^|\s)fetchtastic(?:\s|$)')
}

function Select-DefaultInstaller {
    $existing = Get-Command fetchtastic -ErrorAction SilentlyContinue
    if (-not $existing) { return 'uv' }

    $uvOwns = $false
    if (Test-UvOwnsFetchtastic) {
        $uv = Get-Command uv -ErrorAction Stop
        $uvBin = (& $uv.Source tool dir --bin).Trim()
        if ($LASTEXITCODE -eq 0 -and $uvBin) {
            $uvOwns = $existing.Source -eq (Join-Path $uvBin 'fetchtastic.exe')
        }
    }
    $pipxOwns = $false
    if (Test-PipxOwnsFetchtastic) {
        $pipx = Get-Command pipx -ErrorAction Stop
        $pipxBin = (& $pipx.Source environment --value PIPX_BIN_DIR).Trim()
        if ($LASTEXITCODE -eq 0 -and $pipxBin) {
            $pipxOwns = $existing.Source -eq (Join-Path $pipxBin 'fetchtastic.exe')
        }
    }
    if ($uvOwns -and -not $pipxOwns) { return 'uv' }
    if ($pipxOwns -and -not $uvOwns) { return 'pipx' }
    if ($uvOwns -and $pipxOwns) {
        throw "Existing Fetchtastic is registered with both uv and pipx; refusing to guess which installation owns '$($existing.Source)'."
    }

    $managedPip = Join-Path $env:LOCALAPPDATA 'Fetchtastic\venv\Scripts\fetchtastic.exe'
    if ($existing.Source -eq $managedPip) { return 'pip' }

    $adjacentPython = Join-Path (Split-Path $existing.Source) 'python.exe'
    if (Test-Path $adjacentPython) {
        & $adjacentPython -m pip show fetchtastic *> $null
        if ($LASTEXITCODE -eq 0) { return 'legacy-pip' }
    }

    throw "Existing Fetchtastic found at '$($existing.Source)', but its installer could not be identified safely. Upgrade it with its current Python/package manager, or uninstall it before explicitly selecting uv, pip, or pipx."
}

$isUpgrade = [bool](Get-Command fetchtastic -ErrorAction SilentlyContinue)
$selectedInstaller = if ($Installer -eq 'auto') { Select-DefaultInstaller } else { $Installer }
$displayInstaller = if ($selectedInstaller -eq 'legacy-pip') { 'existing pip' } else { $selectedInstaller }
Write-Host "Installing Fetchtastic with $displayInstaller..." -ForegroundColor Cyan

switch ($selectedInstaller) {
    'uv' {
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uv) {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            $uvDirectory = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
            Add-UserPath $uvDirectory
            $uv = Get-Command uv -ErrorAction Stop
        }
        if (Test-UvOwnsFetchtastic) {
            Invoke-Checked $uv.Source @('tool', 'upgrade', 'fetchtastic')
        } else {
            Invoke-Checked $uv.Source @('tool', 'install', '--python', '>=3.10', 'fetchtastic[win]')
        }
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
    'legacy-pip' {
        $existingCommand = Get-Command fetchtastic -ErrorAction Stop
        $legacyPython = Join-Path (Split-Path $existingCommand.Source) 'python.exe'
        Invoke-Checked $legacyPython @('-m', 'pip', 'install', '--upgrade', 'fetchtastic[win]')
        $fetchtastic = $existingCommand.Source
    }
    'pipx' {
        $pipx = Get-Command pipx -ErrorAction Stop
        if (Test-PipxOwnsFetchtastic) {
            Invoke-Checked $pipx.Source @('upgrade', 'fetchtastic')
        } else {
            Invoke-Checked $pipx.Source @('install', 'fetchtastic[win]')
        }
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
    if (-not [string]::IsNullOrWhiteSpace($runSetup) -and $runSetup.Trim().ToLower() -eq 'y') {
        Invoke-Checked $fetchtastic @('setup')
    }
} else {
    Invoke-Checked $fetchtastic @('setup')
}
Write-Host 'Installation complete!' -ForegroundColor Green
