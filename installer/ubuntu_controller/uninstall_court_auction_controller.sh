#!/usr/bin/env bash
# =====================================================================
#  BIDGUARD 법원경매 관제기 — 지우기 (우분투)
#  지우는 것: 자동 실행 서비스, 프로그램 폴더
#  자료(수집기 목록·작업·API 키·DB): 기본은 따로 옮겨 둠. 지우려면 질문에 '예'
#  시험용: BIDGUARD_NONINTERACTIVE=1, BIDGUARD_DELETE_DATA=1, BIDGUARD_INSTALL_DIR, BIDGUARD_ROOT_PREFIX
# =====================================================================
set -Euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(id -u)" -ne 0 ]; then
  echo "  관제기를 지우려면 관리자 비밀번호가 필요합니다."
  exec sudo -E bash "$HERE/$(basename "$0")" "$@"
fi
ROOT="${BIDGUARD_ROOT_PREFIX:-}"
SERVICE_NAME="bidguard-court-auction-controller"
UNIT_DIR="$ROOT/etc/systemd/system"
INSTALL_DIR="${BIDGUARD_INSTALL_DIR:-}"
if [ -z "$INSTALL_DIR" ]; then
  if [ -f "$HERE/install_record.json" ]; then INSTALL_DIR="$HERE"; else INSTALL_DIR="$ROOT/opt/bidguard/controller"; fi
fi
HAS_SYSTEMD=0; [ -d /run/systemd/system ] && [ -z "$ROOT" ] && HAS_SYSTEMD=1
PRESERVE=(controller.db controller_state.json credentials.json credentials.enc apikeys.enc .apikey_secret defaults/work_policy.json logs map_submissions reports)
KEEP_TO="$(dirname "$INSTALL_DIR")/controller_data_$(date +%Y%m%d_%H%M)"
UI="plain"; if [ -z "${BIDGUARD_NONINTERACTIVE:-}" ] && command -v whiptail >/dev/null 2>&1; then UI="whiptail"; fi
ask() { # 내용 기본
  if [ -n "${BIDGUARD_NONINTERACTIVE:-}" ]; then [ "$2" = yes ]; return; fi
  if [ "$UI" = whiptail ]; then if [ "$2" = no ]; then whiptail --title "관제기 지우기" --defaultno --yesno "$1" 18 76; else whiptail --title "관제기 지우기" --yesno "$1" 18 76; fi
  else printf '\n%s\n' "$1"; local a; read -r -p "  예/아니오 (y/n, 기본 $2): " a; a="${a:-$2}"; [[ "$a" =~ ^(y|Y|yes|예)$ ]]; fi
}
tell() { if [ "$UI" = whiptail ]; then whiptail --title "관제기 지우기" --msgbox "$1" 18 76; else printf '\n%s\n' "$1"; fi; }

if [ ! -f "$INSTALL_DIR/install_record.json" ] && [ ! -f "$INSTALL_DIR/web_ui.py" ]; then
  tell "$INSTALL_DIR 에 관제기가 설치되어 있지 않습니다."; exit 1
fi
ask "관제기를 이 컴퓨터에서 지웁니다.

  설치 자리 : $INSTALL_DIR
  • 자동 실행 서비스를 멈추고 지웁니다.
  • 프로그램 폴더를 지웁니다.

계속할까요?" yes || exit 0
DELETE_DATA=no
if [ -n "${BIDGUARD_NONINTERACTIVE:-}" ]; then [ "${BIDGUARD_DELETE_DATA:-}" = 1 ] && DELETE_DATA=yes
elif ask "자료(수집기 목록·작업·API 키·DB)도 모두 지울까요? 되살릴 수 없습니다.

'아니오'를 고르면 자료를 여기로 옮겨 둡니다:
$KEEP_TO" no; then DELETE_DATA=yes; fi

if [ "$HAS_SYSTEMD" = 1 ]; then
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  systemctl disable "$SERVICE_NAME" 2>/dev/null || true
fi
[ -f "$INSTALL_DIR/.bidguard_test_pid" ] && { kill "$(cat "$INSTALL_DIR/.bidguard_test_pid")" 2>/dev/null || true; sleep 1; }
rm -f "$UNIT_DIR/$SERVICE_NAME.service"
[ "$HAS_SYSTEMD" = 1 ] && systemctl daemon-reload
MOVED=""
if [ "$DELETE_DATA" = no ]; then
  for item in "${PRESERVE[@]}"; do
    if [ -e "$INSTALL_DIR/$item" ]; then mkdir -p "$KEEP_TO/$(dirname "$item")"; mv "$INSTALL_DIR/$item" "$KEEP_TO/$item"; MOVED="$MOVED $item"; fi
  done
  [ -d "$KEEP_TO" ] && chmod 700 "$KEEP_TO"
fi
rm -rf "$INSTALL_DIR"
PARENT="$(dirname "$INSTALL_DIR")"
[ -d "$PARENT" ] && [ -z "$(ls -A "$PARENT")" ] && rmdir "$PARENT"
MSG="관제기를 지웠습니다."
[ -n "$MOVED" ] && MSG="$MSG

자료는 여기로 옮겨 두었습니다:
$KEEP_TO
($MOVED )
다시 설치하면 설치 프로그램이 이 자료를 찾아 되살릴지 묻습니다."
tell "$MSG"
