@echo off
echo Stopping codebase-memory-mcp server...
taskkill /F /IM codebase-memory-mcp.exe 2>nul
timeout /t 1 >nul
tasklist /FI "IMAGENAME eq codebase-memory-mcp.exe" 2>nul | findstr /I "codebase-memory-mcp.exe" >nul
if errorlevel 1 (
    echo Server stopped successfully.
) else (
    echo Warning: Server may still be running.
)