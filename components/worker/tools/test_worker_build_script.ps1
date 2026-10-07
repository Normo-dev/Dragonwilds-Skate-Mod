$ErrorActionPreference = 'Stop'
$testRoot = Join-Path $PSScriptRoot ('build-script-tests-' + [Guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $testRoot 'source with spaces'
New-Item -ItemType Directory -Path $sourceRoot | Out-Null
foreach ($name in @('Build-Worker.ps1', 'Verify-Source.ps1')) {
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) $name) -Destination (Join-Path $sourceRoot $name)
}
$probe = Join-Path $sourceRoot 'probe.txt'
[IO.File]::WriteAllText($probe, 'numeric source fixture')
$inventory = @{ files = @(@{ path = 'probe.txt'; sha256 = (Get-FileHash -LiteralPath $probe -Algorithm SHA256).Hash }) }
$inventory | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $sourceRoot 'SOURCE-PROVENANCE.json') -Encoding UTF8
$global:workerBuildCalls = @()
$global:workerBuildFail = $false
function global:Test-WorkerCargo {
    $global:workerBuildCalls += ,@($args)
    if ($args[0] -eq 'test') {
        if (-not $env:CARGO_ENCODED_RUSTFLAGS.Contains("$sourceRoot=/source/test")) { throw 'Source path remap was not applied as one argument.' }
        if ($env:RUSTFLAGS) { throw 'Caller RUSTFLAGS leaked into the build.' }
        if ($env:CARGO_INCREMENTAL -ne '0') { throw 'Incremental build was not disabled.' }
        if ($env:RUSTC -ne 'Test-WorkerRustc' -or $env:RUSTC_WRAPPER -or $env:RUSTC_WORKSPACE_WRAPPER) { throw 'Cargo was not bound to the verified compiler.' }
    }
    $global:LASTEXITCODE = if ($global:workerBuildFail) { 17 } else { 0 }
}
function global:Test-WorkerRustc { $global:LASTEXITCODE = 0; 'rustc 1.96.0 (fixture)' }
function global:Test-WorkerPython { $global:workerBuildCalls += ,@($args); $global:LASTEXITCODE = 0 }
$old = @{}
foreach ($name in @('CARGO_ENCODED_RUSTFLAGS', 'RUSTFLAGS', 'CARGO_INCREMENTAL', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER')) { $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$passed = @()
try {
    $env:RUSTFLAGS = 'caller-value'; $env:CARGO_ENCODED_RUSTFLAGS = 'caller-encoded'; $env:CARGO_INCREMENTAL = '1'
    $env:RUSTC = 'caller-rustc'; $env:RUSTC_WRAPPER = 'caller-wrapper'; $env:RUSTC_WORKSPACE_WRAPPER = 'caller-workspace-wrapper'
    $common = @{ TargetDirectory = (Join-Path $testRoot 'target with spaces'); RegistryDirectory = (Join-Path $testRoot 'registry with spaces');
        RemapPrefix = '/source/test'; CargoExecutable = 'Test-WorkerCargo'; RustcExecutable = 'Test-WorkerRustc'; PythonExecutable = 'Test-WorkerPython'; Offline = $true }
    $plan = (& (Join-Path $sourceRoot 'Build-Worker.ps1') -Mode Plan @common) | ConvertFrom-Json
    if ($plan.steps[2].arguments -notcontains '--offline' -or $plan.steps[2].arguments -notcontains '--locked') { throw 'Locked/offline flags absent.' }
    if ($plan.steps[2].arguments -notcontains (Join-Path $sourceRoot 'dragonwilds-skate-worker/Cargo.toml')) { throw 'Manifest with spaces was split.' }
    if ($global:workerBuildCalls.Count) { throw 'Plan executed a tool.' }
    $passed += 'Plan is nonexecuting and keeps paths with spaces as individual arguments'
    & (Join-Path $sourceRoot 'Build-Worker.ps1') -Mode Test @common
    if ($global:workerBuildCalls.Count -ne 2 -or $global:workerBuildCalls[0][1] -ne '--fixture' -or $global:workerBuildCalls[1][0] -ne 'test') { throw 'Wrong test/fixture sequence.' }
    if ($env:RUSTFLAGS -ne 'caller-value' -or $env:CARGO_ENCODED_RUSTFLAGS -ne 'caller-encoded' -or $env:CARGO_INCREMENTAL -ne '1' -or $env:RUSTC -ne 'caller-rustc' -or $env:RUSTC_WRAPPER -ne 'caller-wrapper' -or $env:RUSTC_WORKSPACE_WRAPPER -ne 'caller-workspace-wrapper') { throw 'Environment was not restored.' }
    $passed += 'Test generates numeric fixtures, verifies source, applies remaps, and restores the caller environment'
    $global:workerBuildFail = $true; $caught = $false
    try { & (Join-Path $sourceRoot 'Build-Worker.ps1') -Mode Test @common } catch { $caught = $_.Exception.Message.Contains('17') }
    if (-not $caught -or $env:RUSTFLAGS -ne 'caller-value' -or $env:CARGO_INCREMENTAL -ne '1') { throw 'Failure propagation/environment restoration failed.' }
    $passed += 'Nonzero Cargo status fails the build and still restores environment'
    $global:workerBuildCalls = @(); $global:workerBuildFail = $false
    [IO.File]::WriteAllText($probe, 'changed')
    $caught = $false
    try { & (Join-Path $sourceRoot 'Build-Worker.ps1') -Mode Test @common } catch { $caught = $_.Exception.Message.Contains('hash mismatch') }
    if (-not $caught -or $global:workerBuildCalls.Count) { throw 'Changed source was executed.' }
    $passed += 'Source hash mismatch stops before invoking a compiler or fixture generator'
    $inventory.files[0].path = '../outside.txt'
    $inventory | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $sourceRoot 'SOURCE-PROVENANCE.json') -Encoding UTF8
    $caught = $false
    try { & (Join-Path $sourceRoot 'Verify-Source.ps1') } catch { $caught = $_.Exception.Message.Contains('escapes') }
    if (-not $caught) { throw 'Escaped source inventory was accepted.' }
    $passed += 'Source inventory cannot escape its folder'
    @{ passed = $passed; count = $passed.Count; compiled = $false } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'worker-build-script-tests.json') -Encoding UTF8
    Write-Host "$($passed.Count) build-script checks passed."
} finally {
    foreach ($name in $old.Keys) { [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process') }
    foreach ($name in @('Test-WorkerCargo', 'Test-WorkerRustc', 'Test-WorkerPython')) { Remove-Item -LiteralPath "Function:\$name" -ErrorAction SilentlyContinue }
}
