[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$prefix = $sourceRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$manifest = Get-Content -LiteralPath (Join-Path $sourceRoot 'SOURCE-PROVENANCE.json') -Raw | ConvertFrom-Json
$seen = @{}
foreach ($entry in $manifest.files) {
    if ([IO.Path]::IsPathRooted($entry.path)) { throw "Absolute inventory path: $($entry.path)" }
    $path = [IO.Path]::GetFullPath((Join-Path $sourceRoot $entry.path))
    if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Inventory path escapes source folder: $($entry.path)" }
    if ($seen.ContainsKey($path)) { throw "Duplicate inventory path: $($entry.path)" }
    $seen[$path] = $true
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing source: $($entry.path)" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actual -ne $entry.sha256) { throw "Source hash mismatch: $($entry.path)" }
}
Write-Host "Verified $($seen.Count) inventoried source and notice files."
