$ErrorActionPreference = "Stop"

# 只载入判别函数的 AST，不执行真实依赖安装或构建。
$Tokens = $null
$ParseErrors = $null
$Source = Join-Path $PSScriptRoot "build_windows.ps1"
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$Tokens, [ref]$ParseErrors)
if ($ParseErrors.Count -ne 0) { throw ($ParseErrors | Out-String) }
$Function = $Ast.Find({
    param($Node)
    $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $Node.Name -eq "Test-TransientBundleDownloadFailure"
}, $true)
if (-not $Function) { throw "Retry classification function missing." }
. ([scriptblock]::Create($Function.Extent.Text))

$Cases = @(
    @{ Log = ('Downloading https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip' + "`n" + 'failed to bundle project: `http status: 504`'); Retry = $true },
    @{ Log = "failed to bundle project: https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip: http status: 504"; Retry = $true },
    @{ Log = "download failed: HTTP status 429"; Retry = $true },
    @{ Log = "fetch failed: HTTP status 502"; Retry = $true },
    @{ Log = "download error: HTTP 503"; Retry = $true },
    @{ Log = "download https://example.invalid/nsis.zip failed: operation timed out"; Retry = $true },
    @{ Log = "download failed: HTTP status 404"; Retry = $false },
    @{ Log = "error[E0503]: cannot use value; could not compile attendance-desktop"; Retry = $false },
    @{ Log = "download success`nerror TS2503: Cannot find namespace"; Retry = $false },
    @{ Log = "error: failed to compile due to timeout"; Retry = $false },
    @{ Log = "Downloading https://example.invalid/tool.zip`nerror: failed to compile due to timeout"; Retry = $false },
    @{ Log = "error: unable to find NSIS script"; Retry = $false }
)
foreach ($Case in $Cases) {
    $Actual = Test-TransientBundleDownloadFailure -BuildLog $Case.Log
    if ($Actual -ne $Case.Retry) { throw "Incorrect retry classification: $($Case.Log)" }
}
Write-Host "Windows build retry classification: $($Cases.Count) cases passed."
