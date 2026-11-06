@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

echo ========================================
echo   Court Auction Collector Install
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed.
    echo Please install Python 3.10 or higher and run again.
    pause
    exit /b 1
)

echo [1/5] Creating virtual environment...
if not exist venv (
    python -m venv venv
)

echo [2/5] Installing dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
if exist requirements.txt (
    pip install -r requirements.txt
)

echo [3/5] Collector name
set /p COLLECTOR_NAME="Enter collector name: "
set "SAFE_NAME=!COLLECTOR_NAME:"=!"
set "SAFE_NAME=!SAFE_NAME:'=!"
set "SAFE_NAME=!SAFE_NAME:\=!"
set "SAFE_NAME=!SAFE_NAME: =!"
if "!SAFE_NAME!"=="" (
    echo [ERROR] Invalid name.
    pause
    exit /b 1
)

echo [4/5] Creating config.json...
python -c "import json,re,datetime; name='%SAFE_NAME%'; t='control' if re.fullmatch(r'jiju\d{4}', name, re.I) else 'supply'; open('config.json','w',encoding='utf-8').write(json.dumps({'name':name,'type':t,'created':str(datetime.datetime.now()),'ai_accounts':[],'pico2_enabled':True},ensure_ascii=False,indent=2)); print('Type:', t)"

echo [5/5] Creating desktop shortcut...
set "COLLECTOR_NAME=!SAFE_NAME!"
python create_shortcut.py
if errorlevel 1 (
    echo [WARN] Shortcut creation failed.
    echo You can manually: right-click run.bat -^> Send to -^> Desktop
) else (
    echo [OK] Check your desktop for Collector_!SAFE_NAME!.lnk
)

echo.
echo ========================================
echo   Installation complete
echo ========================================
echo Collector: !SAFE_NAME!
type config.json
echo.
echo To run: double-click the desktop shortcut or run.bat
echo.
pause
