$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $Root "apps\desktop"
$BuildDir = Join-Path $Root "build"
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$NpmRegistry = if ($env:NPM_REGISTRY) {
    $env:NPM_REGISTRY
} else {
    "https://registry.npmjs.org"
}

Write-Host "[1/5] Preparing Python environment..."
if (-not (Test-Path $VenvPython)) {
    python -m venv $VenvDir
}

& $VenvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade Python packaging tools."
}

& $VenvPython -m pip install --no-build-isolation -e "${Root}[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python project dependencies."
}

Write-Host "[2/5] Building Windows x64 Python sidecar..."
$Dist = Join-Path $BuildDir "sidecar-windows"
$Work = Join-Path $BuildDir "pyinstaller-windows"
$SpecPath = $BuildDir
$EntryPoint = Join-Path $Root "scripts\sidecar_entry.py"

Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name attendance-engine `
    --distpath $Dist `
    --workpath $Work `
    --specpath $SpecPath `
    $EntryPoint
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller sidecar build failed."
}

Write-Host "[3/5] Installing the sidecar resource for Tauri..."
$SidecarSource = Join-Path $Dist "attendance-engine"
$SidecarExecutable = Join-Path $SidecarSource "attendance-engine.exe"
$ResourceDir = Join-Path $Desktop "src-tauri\resources\attendance-engine"
if (-not (Test-Path $SidecarExecutable)) {
    throw "Sidecar executable was not generated: $SidecarExecutable"
}
Remove-Item $ResourceDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Split-Path $ResourceDir -Parent) | Out-Null
Copy-Item $SidecarSource $ResourceDir -Recurse -Force

Write-Host "[4/5] Installing frontend dependencies..."
Push-Location $Desktop
try {
    if (Test-Path "package-lock.json") {
        npm ci --registry=$NpmRegistry --replace-registry-host=always
    } else {
        npm install --registry=$NpmRegistry --replace-registry-host=always
    }
    if ($LASTEXITCODE -ne 0) {
        throw "npm dependency installation failed."
    }

    Write-Host "[5/5] Building the Windows NSIS installer..."
    npm run tauri build -- --bundles nsis
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri Windows build failed."
    }
} finally {
    Pop-Location
}

$OutputDir = Join-Path $Desktop "src-tauri\target\release\bundle\nsis"
Write-Host "Windows x64 installer output directory:"
Write-Host $OutputDir
Get-ChildItem -Path $OutputDir -Filter "*.exe" -ErrorAction Stop | ForEach-Object {
    Write-Host $_.FullName
}
