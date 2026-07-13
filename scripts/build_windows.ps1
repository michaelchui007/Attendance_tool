$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $Root "apps\desktop"
$NpmRegistry = if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "https://registry.npmjs.org" }
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    py -3 -m venv (Join-Path $Root ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --no-build-isolation -e "$Root[dev]"

$Dist = Join-Path $Root "build\sidecar-windows"
$Work = Join-Path $Root "build\pyinstaller-windows"
Remove-Item $Dist, $Work -Recurse -Force -ErrorAction SilentlyContinue

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name attendance-engine `
    --distpath $Dist `
    --workpath $Work `
    --specpath (Join-Path $Root "build") `
    (Join-Path $Root "scripts\sidecar_entry.py")

$BinaryDir = Join-Path $Desktop "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinaryDir | Out-Null
Copy-Item (Join-Path $Dist "attendance-engine.exe") (Join-Path $BinaryDir "attendance-engine-x86_64-pc-windows-msvc.exe") -Force

Push-Location $Desktop
try {
    if (Test-Path "package-lock.json") { npm ci --registry=$NpmRegistry --replace-registry-host=always } else { npm install --registry=$NpmRegistry --replace-registry-host=always }
    npm run tauri build -- --bundles nsis
} finally {
    Pop-Location
}

Write-Host "Windows x64 安装程序输出目录："
Write-Host (Join-Path $Desktop "src-tauri\target\release\bundle\nsis")
