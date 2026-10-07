param([Parameter(Mandatory=$true)][string]$OutputDirectory,
 [string]$CargoExecutable="cargo",[string]$RustcExecutable="rustc",[switch]$Offline)
$ErrorActionPreference="Stop"
$root=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$output=[IO.Path]::GetFullPath($OutputDirectory)
$rustc=(Get-Command -Name $RustcExecutable -ErrorAction Stop).Source
$version=& $rustc --version
if($LASTEXITCODE -ne 0 -or $version -notmatch '^rustc 1\.96\.0 '){throw "Rust 1.96.0 is required"}
$cargoHome=if($env:CARGO_HOME){[IO.Path]::GetFullPath($env:CARGO_HOME)}else{Join-Path $env:USERPROFILE '.cargo'}
$remaps=@($root,$cargoHome,$env:USERPROFILE)
foreach($value in $remaps){if(-not $value -or $value.Contains("=") -or $value.Contains([char]31)){throw "Unsupported remap path"}}
$names=@('CARGO_ENCODED_RUSTFLAGS','RUSTFLAGS','RUSTC','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER','CARGO_INCREMENTAL')
$previous=@{}
foreach($name in $names){$previous[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
try {
 $flags=@("--remap-path-prefix=$root=/source","--remap-path-prefix=$cargoHome=/build/cargo","--remap-path-prefix=$env:USERPROFILE=/build/user")
 $env:CARGO_ENCODED_RUSTFLAGS=$flags -join [char]31
 $env:RUSTFLAGS=$null;$env:RUSTC=$rustc;$env:RUSTC_WRAPPER=$null;$env:RUSTC_WORKSPACE_WRAPPER=$null;$env:CARGO_INCREMENTAL='0'
 $closure=Get-Content -LiteralPath (Join-Path $root 'SOURCE-CLOSURE.json') -Raw | ConvertFrom-Json
 $components=@($closure.helpers | ForEach-Object { $_.component })
 if($components.Count -lt 2 -or $components -notcontains 'ipc' -or $components -notcontains 'render' -or
    @($components | Select-Object -Unique).Count -ne $components.Count -or
    @($components | Where-Object { $_ -notin @('ipc','render','buildings') }).Count -ne 0){throw 'Invalid source helper closure'}
 foreach($component in $components) {
  $arguments=@("build","--release","--locked","--manifest-path",(Join-Path $root "helpers/$component/Cargo.toml"),"--target-dir",(Join-Path $output $component))
  if($Offline){$arguments+="--offline"}
  & $CargoExecutable @arguments
  if($LASTEXITCODE -ne 0){throw "Helper build failed: $component"}
 }
} finally {foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$previous[$name],'Process')}}
