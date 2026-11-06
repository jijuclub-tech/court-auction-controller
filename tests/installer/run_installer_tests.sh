#!/bin/bash
# 설치 프로그램 모듈 전체 시험 (리눅스, root 권한, Python 3.10+ 와 flask·cryptography 가 설치된 환경)
#   1 피코2 펌웨어 업데이트기   2 묶음 제작기   3 우분투 관제기 설치기(격리 상자에서 실제 설치·실행)
# 인터넷 없이 돈다: 이 컴퓨터에 설치된 부품을 시험용 wheel 로 다시 묶어 씀.
# 윈도우 설치 마법사(PowerShell 화면)는 윈도우에서만 돌 수 있어 여기서는 괄호·따옴표 짝만 검사.
# 사용: sudo bash tests/installer/run_installer_tests.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"; FAIL=0
run() { echo "=== $1"; shift; "$@" 2>&1 | grep -E "^PASS|^FAIL|ALL PASS|SOME FAILED|Traceback|Error|짝" | tee -a "$WORK/result.txt"; }
python3 "$HERE/make_test_wheels.py" "$WORK/linux_wheels" "$WORK/windows_wheels" >/dev/null
python3 -c "import sys; sys.path.insert(0,'$HERE'); from uf2_test_files import make_uf2; open('$WORK/fw.uf2','wb').write(make_uf2([0xE48BFF57,0xE48BFF59],40))"
printf 'MZ test placeholder' > "$WORK/python-3.12.10-amd64.exe"
run "피코2 펌웨어 업데이트기" python3 "$HERE/test_pico2_firmware_updater.py"
run "묶음 제작기" python3 "$HERE/test_build_release_bundles.py" "$WORK/python-3.12.10-amd64.exe" "$WORK/windows_wheels" "$WORK/linux_wheels"
if [ -f "$REPO/controller/web_ui.py" ]; then   # 관제기가 있는 저장소에서만
  python3 "$REPO/installer/build/build_release_bundles.py" --out "$WORK/out" --python-installer "$WORK/python-3.12.10-amd64.exe" \
    --windows-wheels "$WORK/windows_wheels" --linux-wheels "$WORK/linux_wheels" --firmware-uf2 "$WORK/fw.uf2" >/dev/null
  run "우분투 관제기 설치기" python3 "$HERE/test_ubuntu_controller_installer.py" "$WORK"/out/Court-Auction-Controller-Ubuntu-Setup-*.zip
fi
run "윈도우 설치 마법사 (짝 검사만)" python3 "$HERE/check_powershell_balance.py" "$REPO"/installer/windows_collector/*.ps1
grep -q "SOME FAILED\|^FAIL\|Traceback" "$WORK/result.txt" && FAIL=1
echo; echo "통과 $(grep -c '^PASS' "$WORK/result.txt")개 / 실패 $(grep -c '^FAIL' "$WORK/result.txt")개   (시험 폴더 $WORK)"
exit $FAIL
