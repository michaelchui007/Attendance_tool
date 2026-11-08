$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# 缓存布局和 SHA-1 来自锁定 CLI 的官方 bundler 源码：
# https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle/windows/nsis/mod.rs
# 仅改用官方 GitHub REST asset 下载路由，保留原始包和 Tauri 的校验。
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Lock = Get-Content (Join-Path $ProjectRoot "apps\desktop\package-lock.json") -Raw | ConvertFrom-Json
$CliVersion = $Lock.packages.'node_modules/@tauri-apps/cli'.version
if ($CliVersion -ne "2.11.4") { throw "Update verified NSIS tool hashes for Tauri CLI $CliVersion before building." }
$ArchiveCache = Join-Path $env:LOCALAPPDATA "attendance-build-cache"
$TauriCache = Join-Path $env:LOCALAPPDATA "tauri"
$NsisTarget = Join-Path $TauriCache "NSIS"
New-Item -ItemType Directory -Force $ArchiveCache, $TauriCache | Out-Null

function Get-VerifiedOfficialAsset {
    <#
    .SYNOPSIS
    从官方 GitHub asset API 下载并验证文件，缓存命中仍检查两种摘要。
    .PARAMETER AssetUrl
    固定版本的官方发布附件API地址。
    #>
    param([string]$AssetUrl, [string]$Destination, [string]$Sha1, [string]$Sha256)
    if (Test-Path $Destination) {
        if ((Get-FileHash $Destination -Algorithm SHA1).Hash -eq $Sha1 -and
            (Get-FileHash $Destination -Algorithm SHA256).Hash -eq $Sha256) {
            Write-Host "Verified cached tool: $(Split-Path $Destination -Leaf)"
            return
        }
        Remove-Item $Destination -Force
    }
    $Headers = @{ Accept = "application/octet-stream"; "X-GitHub-Api-Version" = "2022-11-28" }
    if ($env:GH_TOKEN) { $Headers.Authorization = "Bearer $env:GH_TOKEN" }
    $Partial = "$Destination.partial"
    for ($Attempt = 1; $Attempt -le 2; $Attempt++) {
        try {
            Invoke-WebRequest $AssetUrl -Headers $Headers -OutFile $Partial -TimeoutSec 90 -MaximumRedirection 5
            if ((Get-FileHash $Partial -Algorithm SHA1).Hash -ne $Sha1 -or
                (Get-FileHash $Partial -Algorithm SHA256).Hash -ne $Sha256) {
                throw "Official asset hash mismatch: $(Split-Path $Destination -Leaf)"
            }
            Move-Item $Partial $Destination -Force
            Write-Host "Verified official tool: $(Split-Path $Destination -Leaf)"
            return
        } catch {
            Remove-Item $Partial -Force -ErrorAction SilentlyContinue
            if ($Attempt -eq 2 -or $_.Exception.Message -match 'hash mismatch') { throw }
            Write-Warning "Official asset API download failed; retrying once before compilation."
            Start-Sleep -Seconds 5
        }
    }
}

$Archive = Join-Path $ArchiveCache "nsis-3.11.zip"
$Plugin = Join-Path $ArchiveCache "nsis_tauri_utils-0.5.3.dll"
Get-VerifiedOfficialAsset -AssetUrl "https://api.github.com/repos/tauri-apps/binary-releases/releases/assets/317051073" -Destination $Archive -Sha1 "EF7FF767E5CBD9EDD22ADD3A32C9B8F4500BB10D" -Sha256 "C7D27F780DDB6CFFB4730138CD1591E841F4B7EDB155856901CDF5F214394FA1"
Get-VerifiedOfficialAsset -AssetUrl "https://api.github.com/repos/tauri-apps/nsis-tauri-utils/releases/assets/345882097" -Destination $Plugin -Sha1 "75197FEE3C6A814FE035788D1C34EAD39349B860" -Sha256 "5BA143B5DB4A87D32D6E7802E033330AAE56CBCEABE0D1E3BA41948385AD4709"

$Stage = Join-Path $ArchiveCache "nsis-extract-stage"
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $Archive -DestinationPath $Stage -Force
$Extracted = Join-Path $Stage "nsis-3.11"
if (-not (Test-Path (Join-Path $Extracted "Bin\makensis.exe"))) { throw "Official NSIS archive has unexpected structure." }
$PluginDir = Join-Path $Extracted "Plugins\x86-unicode\additional"
New-Item -ItemType Directory -Force $PluginDir | Out-Null
Copy-Item $Plugin (Join-Path $PluginDir "nsis_tauri_utils.dll") -Force
Remove-Item $NsisTarget -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $Extracted $NsisTarget
Remove-Item $Stage -Recurse -Force
Write-Host "NSIS tools prepared with upstream hash verification: $NsisTarget"
