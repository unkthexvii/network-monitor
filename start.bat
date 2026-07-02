@echo off
:: Auto-Elevate to Administrator for ICMP raw socket permissions
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~dpnx0' -Verb RunAs"
    exit /b
)

:: Ensure we are in the correct directory after elevation
cd /d "%~dp0"

echo ==============================================
echo  Network Monitoring System - Startup Script
echo ==============================================

:: Check if python is installed
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH!
    echo Please install Python 3.10 or higher from python.org
    pause
    exit /b
)

:: Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] First time setup: Creating virtual environment...
    python -m venv venv
    
    echo [INFO] Installing required dependencies...
    call venv\Scripts\activate.bat
    pip install --upgrade pip
    pip install -r requirements.txt
) else (
    echo [INFO] Virtual environment found. Activating...
    call venv\Scripts\activate.bat
)

echo [INFO] Starting the Network Monitoring System...
echo ==============================================
python main.py

pause
