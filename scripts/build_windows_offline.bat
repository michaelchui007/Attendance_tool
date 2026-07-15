@echo off
setlocal
where pwsh >nul 2>nul
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_offline.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_offline.ps1"
)
if errorlevel 1 exit /b %errorlevel%
endlocal
