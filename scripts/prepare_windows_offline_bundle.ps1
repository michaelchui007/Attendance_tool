param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($env:OS -ne "Windows_NT") {
    throw "This script must run on Windows. Windows-specific Python, npm and Tauri dependencies cannot be prepared on macOS."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Windows x64 is required."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $Root "apps\desktop"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$ReleaseDir = Join-Path $Root "release"
$BundleName = "Attendance_tool_windows_x64_offline"
$BundleRoot = Join-Path $ReleaseDir $BundleName
$ArchivePath = "$BundleRoot.zip"
$ArchiveHashPath = "$ArchivePath.sha256"

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Invoke-Robocopy {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExtraArguments = @()
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP @ExtraArguments
    $RobocopyExitCode = $LASTEXITCODE
    if ($RobocopyExitCode -gt 7) {
        throw "robocopy failed with exit code $RobocopyExitCode while copying $Source"
    }
}

foreach ($Command in @("python", "node", "npm", "cargo", "rustc", "robocopy", "tar.exe")) {
    Assert-Command $Command
}

if (-not $SkipBuild) {
    Write-Host "[1/7] Running one online Windows build to populate platform-specific dependencies..."
    & (Join-Path $PSScriptRoot "build_windows.ps1")
} else {
    Write-Host "[1/7] Reusing the existing online build and dependency caches..."
}

$NodeModules = Join-Path $Desktop "node_modules"
$TauriTools = Join-Path $Desktop "src-tauri\target\.tauri"
foreach ($RequiredPath in @($VenvPython, $NodeModules, $TauriTools)) {
    if (-not (Test-Path $RequiredPath)) {
        throw "Required online build output is missing: $RequiredPath"
    }
}

Write-Host "[2/7] Staging the project source..."
Remove-Item $BundleRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ArchivePath -Force -ErrorAction SilentlyContinue
Remove-Item $ArchiveHashPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $BundleRoot | Out-Null
Invoke-Robocopy -Source $Root -Destination $BundleRoot -ExtraArguments @(
    "/XD",
    ".git",
    ".venv",
    ".codex",
    "node_modules",
    "target",
    "build",
    "release",
    "__pycache__",
    ".pytest_cache"
)

Write-Host "[3/7] Bundling Windows npm modules and the Tauri/NSIS tool cache..."
Invoke-Robocopy -Source $NodeModules -Destination (Join-Path $BundleRoot "apps\desktop\node_modules")
Invoke-Robocopy -Source $TauriTools -Destination (Join-Path $BundleRoot "apps\desktop\src-tauri\target\.tauri")

Write-Host "[4/7] Downloading Python wheels for the active Windows x64 Python version..."
$OfflineDir = Join-Path $BundleRoot "offline"
$Wheelhouse = Join-Path $OfflineDir "python-wheels-win-x64"
$RequirementsFile = Join-Path $OfflineDir "python-requirements-win-x64.txt"
New-Item -ItemType Directory -Force -Path $Wheelhouse | Out-Null
$FrozenRequirements = @(& $VenvPython -m pip freeze --exclude-editable)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to resolve Python dependencies from the project virtual environment."
}
$FrozenRequirements |
    Where-Object { $_ -and $_ -notmatch "^attendance-tool(?:==|\s*@)" } |
    Set-Content -Path $RequirementsFile -Encoding ascii
& $VenvPython -m pip download --only-binary=:all: --dest $Wheelhouse --requirement $RequirementsFile
if ($LASTEXITCODE -ne 0) {
    throw "Failed to download Windows Python wheels."
}
& $VenvPython -m pip download --only-binary=:all: --dest $Wheelhouse "setuptools>=75" wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to download Python build-system wheels."
}
$PythonMinor = ((& $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") -join "").Trim()
$PythonArchitecture = ((& $VenvPython -c "import struct; print(struct.calcsize('P') * 8)") -join "").Trim()
if ($PythonArchitecture -ne "64") {
    throw "The prepared Python runtime is not x64."
}
Set-Content -Path (Join-Path $OfflineDir "python-version.txt") -Value $PythonMinor -Encoding ascii

$NodeMajor = ((& node -p "process.versions.node.split('.')[0]") -join "").Trim()
Set-Content -Path (Join-Path $OfflineDir "node-major-version.txt") -Value $NodeMajor -Encoding ascii

Write-Host "[5/7] Vendoring Rust crates..."
$CargoVendor = Join-Path $OfflineDir "cargo-vendor"
& cargo vendor --locked --manifest-path (Join-Path $Desktop "src-tauri\Cargo.toml") $CargoVendor | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "cargo vendor failed."
}
$CargoConfigDir = Join-Path $BundleRoot ".cargo"
New-Item -ItemType Directory -Force -Path $CargoConfigDir | Out-Null
@'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "offline/cargo-vendor"

[net]
offline = true
'@ | Set-Content -Path (Join-Path $CargoConfigDir "config.toml") -Encoding ascii

$Readme = @"
考勤统计 Windows x64 离线构建包
=================================

此目录已经包含：
- Windows 版 node_modules
- Python $PythonMinor x64 依赖 wheelhouse
- Rust Cargo vendor 源码
- Tauri 使用的 NSIS/WebView2 构建工具缓存

离线电脑仍需提前安装系统级工具：
1. Python $PythonMinor x64
2. Node.js（主版本 $NodeMajor）
3. Rust stable，目标 x86_64-pc-windows-msvc
4. Visual Studio 2022 Build Tools，勾选“使用 C++ 的桌面开发”和 Windows SDK

建议解压到较短路径，例如 C:\Attendance_tool，避免 Windows 长路径问题。
在项目根目录打开 PowerShell 后执行：

  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_offline.ps1

也可以双击 scripts\build_windows_offline.bat。

生成的安装程序位于：
  apps\desktop\src-tauri\target\release\bundle\nsis\*.exe

离线脚本会设置 PIP_NO_INDEX 和 CARGO_NET_OFFLINE，并跳过 npm install；依赖缺失时会直接报错，不会尝试联网。
"@
Set-Content -Path (Join-Path $BundleRoot "WINDOWS_OFFLINE_BUILD.txt") -Value $Readme -Encoding utf8

Write-Host "[6/7] Creating the portable ZIP archive..."
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
& tar.exe -a -c -f $ArchivePath -C $ReleaseDir $BundleName
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the Windows offline build archive."
}
$Hash = (Get-FileHash -Algorithm SHA256 $ArchivePath).Hash.ToLowerInvariant()
Set-Content -Path $ArchiveHashPath -Value "$Hash  $([IO.Path]::GetFileName($ArchivePath))" -Encoding ascii

Write-Host "[7/7] Windows offline build kit is ready:"
Write-Host $ArchivePath
Write-Host "SHA-256: $Hash"
