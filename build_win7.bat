@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ==============================================
echo  Network Monitoring System - Windows 7 Builder
echo ==============================================
echo.

:: -------------------------------------------------
:: Step 0: Detect uv — try PATH first, then known locations
:: -------------------------------------------------
set "UV_EXE=uv.exe"
where uv.exe >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if exist "%LOCALAPPDATA%\bin\uv.exe" (
        set "UV_EXE=%LOCALAPPDATA%\bin\uv.exe"
    ) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
        echo [!] uv not found in PATH or common locations.
        echo.
        echo     Install it manually:
        echo       powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
        echo.
        echo     Or install Python 3.10+ from python.org, then re-run this script.
        echo.
        pause
        exit /b 1
    )
)
echo [OK] Using uv: !UV_EXE!

:: -------------------------------------------------
:: Step 1: Create / verify the virtual environment
:: -------------------------------------------------
echo.
echo Step 1: Creating Python 3.10+ virtual environment...

:: Check if venv already exists and has pyinstaller
if exist "venv_win7\Scripts\pyinstaller.exe" (
    echo [OK] venv_win7 already exists with PyInstaller. Skipping creation.
) else (
    if exist "venv_win7\pyvenv.cfg" (
        echo [INFO] venv_win7 exists but incomplete. Recreating...
        rmdir /s /q venv_win7
    )
    echo Creating venv with Python 3.10 or higher...
    "!UV_EXE!" venv --python 3.10 --seed --force venv_win7
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create venv. Make sure Python 3.10+ is available via uv.
        pause
        exit /b 1
    )
)

:: -------------------------------------------------
:: Step 2: Install dependencies
:: -------------------------------------------------
echo.
echo Step 2: Installing dependencies...

venv_win7\Scripts\python.exe -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed. Check requirements.txt.
    pause
    exit /b 1
)

:: -------------------------------------------------
:: Step 3: Compile using .spec file (single source of truth)
:: -------------------------------------------------
echo.
echo Step 3: Compiling the Windows 7 compatible executable...
venv_win7\Scripts\pyinstaller.exe --clean NetworkMonitor_Win7.spec
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo ==============================================
echo [SUCCESS] Build complete!
echo.
echo  Windows 7 executable:
echo    dist\NetworkMonitor_Win7.exe
echo ==============================================
pause
