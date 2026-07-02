@echo off
echo ==============================================
echo  Network Monitoring System - PyInstaller Build
echo ==============================================

:: Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

echo [INFO] Installing required dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo [INFO] Building standalone executable...
:: Compile into a single .exe
:: --name sets the output file name to NetworkMonitor.exe
:: --onefile packages everything into a single executable
:: --add-data includes the static folder inside the exe
:: --clean clears the pyinstaller cache
:: --hidden-import ensures dynamic imports are found
pyinstaller --name "NetworkMonitor" ^
            --onefile ^
            --noconsole ^
            --clean ^
            --uac-admin ^
            --add-data "static;static" ^
            --add-data "logo;logo" ^
            --hidden-import "aiosqlite" ^
            --hidden-import "apscheduler.triggers.interval" ^
            --hidden-import "uvicorn.logging" ^
            --hidden-import "uvicorn.loops" ^
            --hidden-import "uvicorn.loops.auto" ^
            --hidden-import "uvicorn.protocols" ^
            --hidden-import "uvicorn.protocols.http" ^
            --hidden-import "uvicorn.protocols.http.auto" ^
            --hidden-import "uvicorn.protocols.websockets" ^
            --hidden-import "uvicorn.protocols.websockets.auto" ^
            --hidden-import "uvicorn.lifespan" ^
            --hidden-import "uvicorn.lifespan.on" ^
            --hidden-import "pysnmp.smi.mibs" ^
            --hidden-import "pysnmp.smi.exval" ^
            --hidden-import "pysnmp.carrier.asyncio.dgram.udp" ^
            --hidden-import "anyio._backends._asyncio" ^
            --collect-data "pysnmp" ^
            --collect-data "pysmi" ^
            main.py

echo ==============================================
echo [SUCCESS] Build complete!
echo You can find your portable executable in the "dist" folder:
echo dist\NetworkMonitor.exe
echo ==============================================
pause
