param(
    [Parameter(Mandatory=$true)][string]$Dotnet,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$taskDotnet=(Resolve-Path -LiteralPath $Dotnet).Path
if ((& $taskDotnet --version) -ne '10.0.401') { throw 'This source snapshot was built with .NET SDK 10.0.401.' }
$taskOutput=[IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $taskOutput) { throw 'Choose a new output directory; existing files are preserved.' }
$taskProject=Join-Path $taskRoot 'dragonwilds-map-export/DragonwildsMapExport.csproj'
& $taskDotnet restore $taskProject -r win-x64 --locked-mode -p:CUE4PARSE_SKIP_NATIVE=true
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency restore failed.' }
# Copied source may preserve timestamps older than an existing intermediate DLL.
# Force compilation from this snapshot before publishing the portable runtime.
& $taskDotnet build $taskProject -c Release -r win-x64 --self-contained true --no-restore --no-incremental -p:CUE4PARSE_SKIP_NATIVE=true -p:DebugType=None -p:DebugSymbols=false -p:RuntimeFrameworkVersion=10.0.12
if ($LASTEXITCODE -ne 0) { throw 'Exporter compilation failed.' }
& $taskDotnet publish $taskProject -c Release -r win-x64 --self-contained true --no-restore -o $taskOutput -p:CUE4PARSE_SKIP_NATIVE=true -p:DebugType=None -p:DebugSymbols=false -p:RuntimeFrameworkVersion=10.0.12
if ($LASTEXITCODE -ne 0) { throw 'Exporter build failed.' }
Write-Output ('Built managed Dragonwilds exporter at ' + $taskOutput)
