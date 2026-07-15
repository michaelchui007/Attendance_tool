param(
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $Root "apps\desktop"
$BuildDir = Join-Path $Root "build"
$VenvDir = Join-Path $Root ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$OfflineDir = Join-Path $Root "offline"
$Wheelhouse = Join-Path $OfflineDir "python-wheels-win-x64"
$NpmRegistry = if ($env:NPM_REGISTRY) {
    $env:NPM_REGISTRY
} else {
    "https://registry.npmjs.org"
}

if ($Offline) {
    Write-Host "Offline mode enabled: network dependency resolution is disabled."
    if (-not (Test-Path $Wheelhouse)) {
        throw "Offline Python wheelhouse is missing: $Wheelhouse"
    }
    if (-not (Test-Path (Join-Path $Desktop "node_modules\.bin\tauri.cmd"))) {
        throw "Offline node_modules is missing or incomplete. Please use the generated Windows offline build kit."
    }
    if (-not (Test-Path (Join-Path $Root ".cargo\config.toml"))) {
        throw "Offline Cargo source configuration is missing: $Root\.cargo\config.toml"
    }
    if (-not (Test-Path (Join-Path $Desktop "src-tauri\target\.tauri"))) {
        throw "Offline Tauri/NSIS tool cache is missing. Please use the generated Windows offline build kit."
    }

    $env:PIP_NO_INDEX = "1"
    $env:PIP_FIND_LINKS = $Wheelhouse
    $env:CARGO_NET_OFFLINE = "true"
}

Write-Host "[1/5] Preparing Python environment..."
if (-not (Test-Path $VenvPython)) {
    python -m venv $VenvDir
}

if ($Offline) {
    $ExpectedPythonVersionFile = Join-Path $OfflineDir "python-version.txt"
    if (Test-Path $ExpectedPythonVersionFile) {
        $ExpectedPythonVersion = (Get-Content $ExpectedPythonVersionFile -Raw).Trim()
        $ActualPythonVersion = (& $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
        $ActualPythonArchitecture = (& $VenvPython -c "import struct; print(struct.calcsize('P') * 8)").Trim()
        if ($ActualPythonVersion -ne $ExpectedPythonVersion -or $ActualPythonArchitecture -ne "64") {
            throw "Python runtime mismatch. Offline kit requires Python $ExpectedPythonVersion x64; current runtime is Python $ActualPythonVersion, $ActualPythonArchitecture-bit."
        }
    }

    & $VenvPython -m pip install --no-index --find-links $Wheelhouse setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python packaging tools from the offline wheelhouse."
    }
    & $VenvPython -m pip install --no-index --find-links $Wheelhouse --no-build-isolation -e "${Root}[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python project dependencies from the offline wheelhouse."
    }
} else {
    & $VenvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade Python packaging tools."
    }

    & $VenvPython -m pip install --no-build-isolation -e "${Root}[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python project dependencies."
    }
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
    --paths $Root `
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
$SmokeOutput = (& $SidecarExecutable --help 2>&1) -join [Environment]::NewLine
if ($LASTEXITCODE -ne 0 -or $SmokeOutput -notmatch "(?m)serve") {
    throw "Sidecar smoke test failed: serve command is unavailable.`n$SmokeOutput"
}
Remove-Item $ResourceDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Split-Path $ResourceDir -Parent) | Out-Null
Copy-Item $SidecarSource $ResourceDir -Recurse -Force

Write-Host "[4/5] Preparing frontend dependencies..."
Push-Location $Desktop
try {
    if ($Offline) {
        $ExpectedNodeMajorFile = Join-Path $OfflineDir "node-major-version.txt"
        if (Test-Path $ExpectedNodeMajorFile) {
            $ExpectedNodeMajor = (Get-Content $ExpectedNodeMajorFile -Raw).Trim()
            $ActualNodeMajor = (& node -p "process.versions.node.split('.')[0]").Trim()
            $ActualNodeArchitecture = (& node -p "process.arch").Trim()
            if ($ActualNodeMajor -ne $ExpectedNodeMajor -or $ActualNodeArchitecture -ne "x64") {
                throw "Node.js runtime mismatch. Offline kit requires Node.js $ExpectedNodeMajor x64; current runtime is major version $ActualNodeMajor, architecture $ActualNodeArchitecture."
            }
        }
        Write-Host "Using bundled node_modules; npm install is intentionally skipped."
    } elseif (Test-Path "package-lock.json") {
        npm ci --registry=$NpmRegistry --replace-registry-host=always
        if ($LASTEXITCODE -ne 0) {
            throw "npm dependency installation failed."
        }
    } else {
        npm install --registry=$NpmRegistry --replace-registry-host=always
        if ($LASTEXITCODE -ne 0) {
            throw "npm dependency installation failed."
        }
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
