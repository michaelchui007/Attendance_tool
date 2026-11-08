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

function Test-TransientBundleDownloadFailure {
    <#
    .SYNOPSIS
    仅识别安装包外部下载的瞬时网络故障，编译错误不进入重试。
    .PARAMETER BuildLog
    当前一次 Tauri 构建的完整输出。
    #>
    param([string]$BuildLog)

    if ($BuildLog -match '(?im)(error\[E\d+\]|error TS\d+|could not compile|failed to compile|compilation failed)') {
        return $false
    }
    $FailureLines = ($BuildLog -split "`r?`n" | Where-Object {
        $_ -match '(?i)(error|failed|timed? ?out|timeout)'
    }) -join "`n"
    # Tauri 将下载地址和 HTTP 错误拆成两行；必须保留同次构建的下载上下文。
    $ExternalDownload = ($FailureLines -match '(?i)(download|fetch|https?://)') -or
        ($BuildLog -match '(?im)(download(?:ing)?|fetch(?:ing)?)[^\r\n]*https?://')
    $TransientFailure = $FailureLines -match '(?i)(\b(?:429|502|503|504)\b|timed? ?out|timeout)'
    return $ExternalDownload -and $TransientFailure
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
    # 网络下载可短暂失败；最多三次构建，非网络编译错误立即终止。
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        $BuildMessages = @()
        $SavedErrorPreference = $ErrorActionPreference
        try {
            # Windows PowerShell 5 将原生 stderr 包装为错误记录，退出码统一在下方检查。
            $ErrorActionPreference = "Continue"
            npm run tauri build -- --bundles nsis 2>&1 | Tee-Object -Variable BuildMessages
            $BuildExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $SavedErrorPreference
        }
        if ($BuildExitCode -eq 0) { break }
        $BuildLog = ($BuildMessages | ForEach-Object { $_.ToString() }) -join "`n"
        if ($Attempt -eq 3 -or -not (Test-TransientBundleDownloadFailure -BuildLog $BuildLog)) {
            throw "Tauri Windows build failed (exit $BuildExitCode, attempt $Attempt/3)."
        }
        Write-Warning "Installer dependency download failed temporarily; retrying build ($Attempt/3)."
        Start-Sleep -Seconds (10 * $Attempt)
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
