[CmdletBinding()]
param(
    [ValidateSet('Build', 'Test', 'Fetch', 'Plan')][string]$Mode = 'Build',
    [ValidateSet('debug', 'release')][string]$Profile = 'debug',
    [string]$TargetDirectory = '',
    [string]$CargoExecutable = 'cargo',
    [string]$RustcExecutable = 'rustc',
    [string]$PythonExecutable = 'python',
    [string]$RegistryDirectory = '',
    [string]$RemapPrefix = '/source/dragonwilds-skate',
    [ValidateRange(1, 64)][int]$Jobs = 4,
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $TargetDirectory) { $TargetDirectory = Join-Path $sourceRoot 'target' }
$TargetDirectory = [IO.Path]::GetFullPath($TargetDirectory)
if (-not $RegistryDirectory) {
    $cargoDirectory = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cargo' }
    $RegistryDirectory = Join-Path $cargoDirectory 'registry'
}
$RegistryDirectory = [IO.Path]::GetFullPath($RegistryDirectory)
foreach ($value in @($sourceRoot, $RegistryDirectory, $RemapPrefix)) {
    if ($value.Contains('=') -or $value.Contains([char]31) -or $value.Contains("`n") -or $value.Contains("`r")) {
        throw 'Remap paths cannot contain equals signs, line breaks, or unit separators.'
    }
}
if (-not $RemapPrefix.StartsWith('/')) { throw 'RemapPrefix must be an absolute virtual path starting with /.' }
$manifest = Join-Path $sourceRoot 'dragonwilds-skate-worker/Cargo.toml'
$baseArguments = @('--locked', '--manifest-path', $manifest, '--target', 'x86_64-pc-windows-msvc')
if ($Offline) { $baseArguments += '--offline' }
$buildArguments = @('--target-dir', $TargetDirectory, '-j', [string]$Jobs)
if ($Profile -eq 'release') { $buildArguments += '--release' }
$remapFlags = @(
    "--remap-path-prefix=$sourceRoot=$RemapPrefix",
    "--remap-path-prefix=$($sourceRoot.Replace('\', '/'))=$RemapPrefix",
    "--remap-path-prefix=$RegistryDirectory=/cargo/registry",
    "--remap-path-prefix=$($RegistryDirectory.Replace('\', '/'))=/cargo/registry"
) | Select-Object -Unique
$steps = @(
    @{ program = $CargoExecutable; arguments = @('fetch') + $baseArguments },
    @{ program = $PythonExecutable; arguments = @('test_skate_world_export.py', '--fixture'); directory = $sourceRoot },
    @{ program = $CargoExecutable; arguments = @('test') + $baseArguments + $buildArguments + @('--tests') },
    @{ program = $CargoExecutable; arguments = @('build') + $baseArguments + $buildArguments + @('--bin', 'dragonwilds-skate-worker') }
)
if ($Mode -eq 'Plan') {
    @{ source = $sourceRoot; target = $TargetDirectory; profile = $Profile; source_verification = 'Verify-Source.ps1';
       rustc = '1.96.0'; rust_flags = $remapFlags; steps = $steps } | ConvertTo-Json -Depth 6
    return
}
& (Join-Path $sourceRoot 'Verify-Source.ps1')
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $global:LASTEXITCODE = 0
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with code $LASTEXITCODE" }
}
if ($Mode -eq 'Fetch') {
    Invoke-Checked $steps[0].program $steps[0].arguments
    return
}
$rustcCommand = Get-Command $RustcExecutable -ErrorAction Stop
$verifiedRustc = if ($rustcCommand.Source) { $rustcCommand.Source } else { $RustcExecutable }
$global:LASTEXITCODE = 0
$version = & $verifiedRustc --version
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^rustc 1\.96\.0(?:\s|$)') {
    throw "This source candidate was verified with Rust 1.96.0; found: $version"
}
$savedEnvironment = @{}
foreach ($name in @('CARGO_ENCODED_RUSTFLAGS', 'RUSTFLAGS', 'CARGO_INCREMENTAL', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    # Do not silently incorporate unaudited caller flags; restore them in finally.
    $env:CARGO_ENCODED_RUSTFLAGS = $remapFlags -join [char]31
    $env:RUSTFLAGS = $null
    $env:CARGO_INCREMENTAL = '0'
    $env:RUSTC = $verifiedRustc
    $env:RUSTC_WRAPPER = $null
    $env:RUSTC_WORKSPACE_WRAPPER = $null
    Push-Location -LiteralPath $sourceRoot
    try { Invoke-Checked $steps[1].program $steps[1].arguments } finally { Pop-Location }
    Invoke-Checked $steps[2].program $steps[2].arguments
    if ($Mode -eq 'Build') {
        Invoke-Checked $steps[3].program $steps[3].arguments
        $binary = Join-Path $TargetDirectory "x86_64-pc-windows-msvc/$Profile/dragonwilds-skate-worker.exe"
        Get-FileHash -LiteralPath $binary -Algorithm SHA256 | Format-List
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
