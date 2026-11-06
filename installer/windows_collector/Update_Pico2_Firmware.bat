@echo off
rem BIDGUARD Pico2 firmware update - uses the collector's private Python (install the collector first)
cd /d "%~dp0"
set "PY=C:\BIDGUARD\Court_Auction_Collector\runtime\venv\Scripts\pythonw.exe"
for /f "usebackq tokens=2*" %%A in (`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\BIDGUARD_Court_Auction_Collector" /v InstallLocation 2^>nul ^| find "InstallLocation"`) do set "PY=%%B\runtime\venv\Scripts\pythonw.exe"
if not exist "%PY%" goto nocollector
start "" "%PY%" "%~dp0payload\firmware_updater\pico2_firmware_updater.py" --firmware-dir "%~dp0payload\firmware"
exit /b 0
:nocollector
chcp 65001 >nul
echo.
echo  [!] Install the collector first (Install_Court_Auction_Collector.bat).
echo  [!] 수집기를 먼저 설치하십시오. 펌웨어 업데이트는 수집기 설치에 들어 있는 파이썬으로 돕니다.
echo      설치 뒤에는 바탕화면/시작 메뉴의 BIDGUARD Pico2 Firmware Update 로도 실행됩니다.
echo.
pause
