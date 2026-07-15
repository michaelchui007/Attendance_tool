$ErrorActionPreference = "Stop"
$env:CARGO_NET_OFFLINE = "true"
$env:PIP_NO_INDEX = "1"

$BuildScript = Join-Path $PSScriptRoot "build_windows.ps1"
& $BuildScript -Offline
