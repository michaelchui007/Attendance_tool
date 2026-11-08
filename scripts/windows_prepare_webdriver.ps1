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
