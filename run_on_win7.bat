@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: ─────────────────────────────────────────────
::  run_on_win7.bat — Auto-elevate and launch
::  NetworkMonitor_Win7.exe with admin rights
:: ─────────────────────────────────────────────

:: Check if already running as administrator
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Requesting administrator privileges...
    :: Re-launch this script with admin rights via PowerShell
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Now running as admin — launch the monitor
set "EXE_PATH=%~dp0NetworkMonitor_Win7.exe"

if not exist "!EXE_PATH!" (
    echo [ERROR] NetworkMonitor_Win7.exe not found.
    echo.
    echo   Expected at: !EXE_PATH!
    echo.
    echo   Run build_win7.bat first to compile the executable.
    pause
    exit /b 1
)

echo Starting Network Monitor...
start "NetworkMonitor" "!EXE_PATH!"
exit /b 0
