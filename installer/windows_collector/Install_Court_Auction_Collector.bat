@echo off
rem BIDGUARD Court Auction Collector - double-click to install
cd /d "%~dp0"
if not exist "%~dp0installer\Court_Auction_Collector_Installer.ps1" goto notextracted
if not exist "%~dp0prerequisites" goto notextracted
start "" powershell -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0installer\Court_Auction_Collector_Installer.ps1"
exit /b 0
:notextracted
chcp 65001 >nul
echo.
echo  [!] Please extract the whole ZIP file first, then run this file again.
echo  [!] 압축 파일을 먼저 끝까지 푼 뒤, 풀린 폴더 안의 이 파일을 다시 실행하십시오.
echo      (압축 파일 안에서 바로 실행하면 설치 파일이 보이지 않습니다)
echo.
pause
