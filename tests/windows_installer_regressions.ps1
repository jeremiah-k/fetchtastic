$ErrorActionPreference = 'Stop'
$installerPath = Join-Path $PSScriptRoot '../src/fetchtastic/tools/setup_fetchtastic.ps1'
$source = Get-Content -Raw $installerPath
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer contains syntax errors' }

# Keep reviewed installer contracts visible to the Windows CI job even though
# external manager commands are stubbed below.
if ($source -notmatch [regex]::Escape('$env:XDG_BIN_HOME')) {
    throw 'uv bootstrap no longer honors XDG_BIN_HOME'
}
if ($source -notmatch [regex]::Escape('$env:XDG_DATA_HOME')) {
    throw 'uv bootstrap no longer honors XDG_DATA_HOME'
}
if (([regex]::Matches($source, 'Select-Object -Last 1')).Count -lt 4) {
    throw 'Manager path output is not normalized to a single line'
}
if ($source -notmatch [regex]::Escape("@('tool', 'install', '--force', 'fetchtastic[win]')")) {
    throw 'uv upgrades no longer repair the Windows extra'
}
if ($source -notmatch [regex]::Escape("@('install', '--force', '--upgrade', 'fetchtastic[win]')")) {
    throw 'pipx upgrades no longer repair the Windows extra'
}
if ($source -notmatch [regex]::Escape("`$ErrorActionPreference = 'Continue'")) {
    throw 'Manager probes no longer tolerate native stderr under the Stop preference'
}
if (([regex]::Matches($source, [regex]::Escape('2>$null'))).Count -ne 1) {
    throw 'Redirected manager probes must go through the stderr-safe helper'
}
if ($source -notmatch 'Run full setup now to repair integrations') {
    throw 'Integration-update recovery prompt is missing'
}

# Stub discovery and external execution while running the real parameter
# validation, installer dispatch, and upgrade prompts.
$stubs = @{
    'Select-DefaultInstaller' = "function Select-DefaultInstaller { return 'legacy-pip' }"
    'Invoke-Checked' = @'
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    $script:installerCalls += @{ Executable = $Executable; Arguments = @($Arguments) }
}
'@
}
$functions = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $stubs.ContainsKey($node.Name)
}, $true) | Sort-Object { $_.Extent.StartOffset } -Descending
if (@($functions).Count -ne $stubs.Count) { throw 'Installer test stubs did not match' }
foreach ($function in $functions) {
    $extent = $function.Extent
    $source = $source.Remove($extent.StartOffset, $extent.EndOffset - $extent.StartOffset)
    $source = $source.Insert($extent.StartOffset, $stubs[$function.Name])
}

$script:legacyCommand = Join-Path $env:TEMP 'legacy pip/Scripts/fetchtastic.exe'
function Get-Command {
    param([string]$Name, [string]$ErrorAction)
    if ($Name -ne 'fetchtastic') { throw "Unexpected command lookup: $Name" }
    return [pscustomobject]@{ Source = $script:legacyCommand }
}
function Read-Host {
    param([string]$Prompt)
    return $null
}
$script:installerCalls = @()
& ([scriptblock]::Create($source)) -Installer auto

if ($installerCalls.Count -ne 3) { throw 'Unexpected upgrade or setup calls' }
$expectedPython = Join-Path (Split-Path $legacyCommand) 'python.exe'
if ($installerCalls[0].Executable -ne $expectedPython -or
    ($installerCalls[0].Arguments -join '|') -ne '-m|pip|install|--upgrade|fetchtastic[win]') {
    throw 'Legacy pip upgrade did not use the owning interpreter'
}
if ($installerCalls[1].Executable -ne $legacyCommand -or
    ($installerCalls[1].Arguments -join '|') -ne 'version') {
    throw 'Upgrade did not check the preserved command'
}
if ($installerCalls[2].Executable -ne $legacyCommand -or
    ($installerCalls[2].Arguments -join '|') -ne 'setup|--update-integrations') {
    throw 'Null prompt input did not preserve the default integration update'
}
Write-Host 'Legacy pip dispatch and null upgrade prompts passed.'
