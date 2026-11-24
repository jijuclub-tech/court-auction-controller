#!/usr/bin/env bash
# BIDGUARD controller: install only the reviewed AI secretary UI. No DB or service code changes.
set -Eeuo pipefail
ROOT="${BIDGUARD_CONTROLLER_ROOT:-/opt/bidguard/court-auction-controller}"
SERVICE="${BIDGUARD_CONTROLLER_SERVICE:-court-auction-controller}"
SOURCE="https://raw.githubusercontent.com/jijuclub-tech/court-auction-controller/f550e537b11d5d55f738bedb82de21765c7e4cf8/admin.html"
EXPECTED_BLOB="c55b63d08468f742610f4cceb83bf1db581d0bd1"
if [[ "${EUID}" -ne 0 ]]; then echo 'sudo로 실행해야 합니다.' >&2; exit 2; fi
for command in curl git mktemp install cmp; do command -v "$command" >/dev/null || { echo "필수 도구 없음: $command" >&2; exit 2; }; done
[[ -f "$ROOT/controller.py" && -f "$ROOT/admin.html" ]] || { echo "Python 관제기 설치 폴더를 찾지 못함: $ROOT (기존 Node 관제기는 변경하지 않음)" >&2; exit 3; }
TEMP="$(mktemp)"
cleanup(){ rm -f "$TEMP"; }; trap cleanup EXIT
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --max-time 45 "$SOURCE" -o "$TEMP"
ACTUAL="$(git hash-object "$TEMP")"
[[ "$ACTUAL" == "$EXPECTED_BLOB" ]] || { echo "원본 무결성 실패" >&2; exit 4; }
if cmp -s "$TEMP" "$ROOT/admin.html"; then echo "이미 동일한 AI 비서 화면 설치됨"; exit 0; fi
OLD_UID="$(stat -c %u "$ROOT/admin.html")"; OLD_GID="$(stat -c %g "$ROOT/admin.html")"
BACKUP="$ROOT/admin.html.bak.T.$(date -u +%Y%m%dT%H%M%SZ)"
cp -p -- "$ROOT/admin.html" "$BACKUP"
install -m 0644 -o "$OLD_UID" -g "$OLD_GID" "$TEMP" "$ROOT/admin.html"
if command -v systemctl >/dev/null && systemctl cat "$SERVICE" >/dev/null 2>&1; then
  if ! systemctl restart "$SERVICE"; then
    echo "재시작 실패, 이전 화면 복원 시도" >&2
    cp -p -- "$BACKUP" "$ROOT/admin.html"
    systemctl restart "$SERVICE" || true
    exit 5
  fi
  if ! systemctl is-active --quiet "$SERVICE"; then
    echo "서비스 비활성, 이전 화면 복원 시도" >&2
    cp -p -- "$BACKUP" "$ROOT/admin.html"
    systemctl restart "$SERVICE" || true
    exit 6
  fi
else
  echo "해당 systemd 서비스가 없음: 파일만 교체함. 실제 프로세스 재시작이 필요합니다."
fi
echo "관제기 UI 설치 결과: 파일 교체 완료, 백업: $BACKUP"
echo "상태 검증: 서비스 상태/로그인·기능·태블릿 화면은 별도 현장 확인 필요"
