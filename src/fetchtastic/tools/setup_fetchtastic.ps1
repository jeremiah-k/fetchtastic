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

function Get-NativeOutput {
    param([string]$Executable, [string[]]$Arguments)
    # Windows PowerShell 5.1 turns redirected native stderr into a terminating
    # NativeCommandError while $ErrorActionPreference is 'Stop'; probe commands
    # run with 'Continue' so manager warnings degrade to empty output instead.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Executable @Arguments 2>$null
    } finally {
        $ErrorActionPreference = $previousPreference
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
    $listing = Get-NativeOutput $uv.Source @('tool', 'list')
    return $LASTEXITCODE -eq 0 -and [bool]($listing -match '(?m)^fetchtastic(?:\s|$)')
}

function Get-PipxList {
    $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if (-not $pipx) { return $null }
    $listing = Get-NativeOutput $pipx.Source @('list', '--short')
    if ($LASTEXITCODE -ne 0) {
        $listing = Get-NativeOutput $pipx.Source @('list')
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
        $uvBinOutput = & $uv.Source tool dir --bin
        $uvBinExitCode = $LASTEXITCODE
        $uvBin = $uvBinOutput | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1
        if ($uvBinExitCode -eq 0 -and $uvBin) {
            $uvBin = $uvBin.Trim()
            $uvOwns = $existing.Source -eq (Join-Path $uvBin 'fetchtastic.exe')
        }
    }
    $pipxOwns = $false
    if (Test-PipxOwnsFetchtastic) {
        $pipx = Get-Command pipx -ErrorAction Stop
        $pipxBinOutput = & $pipx.Source environment --value PIPX_BIN_DIR
        $pipxBinExitCode = $LASTEXITCODE
        $pipxBin = $pipxBinOutput | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1
        if ($pipxBinExitCode -eq 0 -and $pipxBin) {
            $pipxBin = $pipxBin.Trim()
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
        Get-NativeOutput $adjacentPython @('-m', 'pip', 'show', 'fetchtastic') | Out-Null
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
            $uvDirectory = if ($env:UV_INSTALL_DIR) {
                $env:UV_INSTALL_DIR
            } elseif ($env:XDG_BIN_HOME) {
                $env:XDG_BIN_HOME
            } elseif ($env:XDG_DATA_HOME) {
                [System.IO.Path]::GetFullPath((Join-Path $env:XDG_DATA_HOME '..\bin'))
            } else {
                Join-Path $HOME '.local\bin'
            }
            Add-UserPath $uvDirectory
            $uv = Get-Command uv -ErrorAction Stop
        }
        if (Test-UvOwnsFetchtastic) {
            Invoke-Checked $uv.Source @('tool', 'install', '--force', 'fetchtastic[win]')
        } else {
            Invoke-Checked $uv.Source @('tool', 'install', '--python', '>=3.10', 'fetchtastic[win]')
        }
        Invoke-Checked $uv.Source @('tool', 'update-shell')
        $toolBinOutput = & $uv.Source tool dir --bin
        $toolBinExitCode = $LASTEXITCODE
        $toolBin = $toolBinOutput | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1
        if ($toolBinExitCode -ne 0 -or -not $toolBin) { throw 'Unable to locate uv tool executables' }
        $toolBin = $toolBin.Trim()
        Add-UserPath $toolBin
        $fetchtastic = Join-Path $toolBin 'fetchtastic.exe'
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
            Invoke-Checked $pipx.Source @('install', '--force', '--upgrade', 'fetchtastic[win]')
        } else {
            Invoke-Checked $pipx.Source @('install', 'fetchtastic[win]')
        }
        Invoke-Checked $pipx.Source @('ensurepath')
        $toolBinOutput = & $pipx.Source environment --value PIPX_BIN_DIR
        $toolBinExitCode = $LASTEXITCODE
        $toolBin = $toolBinOutput | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1
        if ($toolBinExitCode -ne 0 -or -not $toolBin) { throw 'Unable to locate pipx tool executables' }
        $toolBin = $toolBin.Trim()
        Add-UserPath $toolBin
        $fetchtastic = Join-Path $toolBin 'fetchtastic.exe'
    }
}

Invoke-Checked $fetchtastic @('version')
if ($isUpgrade) {
    $updateIntegrations = Read-Host 'Update Windows integrations (Start Menu shortcuts, etc.)? [y/n] (default: yes)'
    if ([string]::IsNullOrWhiteSpace($updateIntegrations) -or $updateIntegrations.Trim().ToLower() -eq 'y') {
        try {
            Invoke-Checked $fetchtastic @('setup', '--update-integrations')
        } catch {
            Write-Warning "Windows integration update failed after the package upgrade: $($_.Exception.Message)"
            $runRecoverySetup = Read-Host 'Run full setup now to repair integrations? [y/n] (default: yes)'
            if ([string]::IsNullOrWhiteSpace($runRecoverySetup) -or $runRecoverySetup.Trim().ToLower() -eq 'y') {
                Invoke-Checked $fetchtastic @('setup')
            }
        }
    }
    $runSetup = Read-Host 'Run setup to review your configuration? [y/n] (default: no)'
    if (-not [string]::IsNullOrWhiteSpace($runSetup) -and $runSetup.Trim().ToLower() -eq 'y') {
        Invoke-Checked $fetchtastic @('setup')
    }
} else {
    Invoke-Checked $fetchtastic @('setup')
}
Write-Host 'Installation complete!' -ForegroundColor Green
