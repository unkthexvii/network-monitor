@echo off
echo ==============================================
echo  Network Monitoring System - Debug Launcher
echo ==============================================
echo.
echo Launching the compiled executable...
echo.

if not exist "dist\NetworkMonitor.exe" (
    echo [ERROR] NetworkMonitor.exe not found in dist/ folder.
    echo Please run build.bat first.
    pause
    exit /b 1
)

.\dist\NetworkMonitor.exe

echo.
echo ==============================================
echo Process exited with code %ERRORLEVEL%
echo ==============================================
pause
