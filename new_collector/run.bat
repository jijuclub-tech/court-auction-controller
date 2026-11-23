@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1

echo ========================================
echo   Court Auction Collector
echo ========================================
echo.

if not exist venv\Scripts\activate.bat (
    echo [ERROR] venv not found.
    echo Please run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo [INFO] Checking Python and Tkinter...
python -c "import tkinter; print('Tkinter OK')" 2>nul
if errorlevel 1 (
    echo [ERROR] Tkinter is not available.
    echo Install Python with Tcl/Tk support, or reinstall Python.
    pause
    exit /b 1
)

echo [INFO] Starting UI...
python -m ui.app
if errorlevel 1 (
    echo.
    echo [ERROR] UI failed to start. See messages above.
    pause
    exit /b 1
)

echo.
echo [INFO] UI closed.
pause
