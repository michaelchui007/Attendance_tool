$ErrorActionPreference = "Stop"

# WebView2 驱动必须与实际运行时版本相同，而不是采用 Edge 浏览器版本。
$RuntimeRoots = @(
    "C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
    "$env:LOCALAPPDATA\Microsoft\EdgeWebView\Application"
)
$Candidates = foreach ($Root in $RuntimeRoots) {
    if (Test-Path $Root) {
        Get-ChildItem $Root -Directory | Where-Object {
            $_.Name -match '^\d+\.\d+\.\d+\.\d+$' -and (Test-Path (Join-Path $_.FullName "msedgewebview2.exe"))
        }
    }
}
$Runtime = $Candidates | Sort-Object { [Version]$_.Name } -Descending | Select-Object -First 1
if (-not $Runtime) { throw "WebView2 runtime is not installed." }
$Version = $Runtime.Name
$Destination = Join-Path $env:RUNNER_TEMP "attendance-webdriver"
New-Item -ItemType Directory -Force $Destination | Out-Null
$Archive = Join-Path $Destination "driver.zip"
Invoke-WebRequest "https://msedgedriver.microsoft.com/$Version/edgedriver_win64.zip" -OutFile $Archive
Expand-Archive $Archive -DestinationPath $Destination -Force
$Executable = Join-Path $Destination "msedgedriver.exe"
if (-not (Test-Path $Executable)) { throw "EdgeDriver executable missing." }
Write-Host "WebView2 runtime: $Version"
& $Executable --version
"ATTENDANCE_EDGEDRIVER=$Executable" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
# 固定运行时目录，避免驱动与另一安装范围的不同版本配对。
"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER=$($Runtime.FullName)" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append

# 官方Tauri e2e同样降低测试进程完整性，不修改生产程序或HKLM策略。
# https://github.com/MicrosoftEdge/WebView2Feedback/issues/5645
$Gsudo = Get-Command gsudo -ErrorAction SilentlyContinue
if ($Gsudo) {
    $GsudoExecutable = $Gsudo.Source
} else {
    $GsudoDirectory = Join-Path $env:RUNNER_TEMP "attendance-gsudo"
    New-Item -ItemType Directory -Force $GsudoDirectory | Out-Null
    $GsudoArchive = Join-Path $GsudoDirectory "gsudo.portable.zip"
    $Headers = @{ Accept = "application/octet-stream"; "X-GitHub-Api-Version" = "2022-11-28" }
    if ($env:GH_TOKEN) { $Headers.Authorization = "Bearer $env:GH_TOKEN" }
    Invoke-WebRequest "https://api.github.com/repos/gerardog/gsudo/releases/assets/301079146" -Headers $Headers -OutFile $GsudoArchive -TimeoutSec 90
    if ((Get-FileHash $GsudoArchive -Algorithm SHA256).Hash -ne "21130BF178D7B9891207F00BFF56F05B6B363CE9CEC7B2D084E45FB12EE51F44") {
        throw "Official gsudo portable archive checksum mismatch."
    }
    Expand-Archive $GsudoArchive -DestinationPath $GsudoDirectory -Force
    $GsudoExecutable = Join-Path $GsudoDirectory "x64\gsudo.exe"
    if (-not (Test-Path $GsudoExecutable)) { throw "Official gsudo x64 executable missing." }
}
& $GsudoExecutable --version
if ($LASTEXITCODE -ne 0) { throw "gsudo validation failed." }
"ATTENDANCE_GSUDO=$GsudoExecutable" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
