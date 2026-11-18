#!/usr/bin/env bash
# Remove the court auction controller service and program files.
#   sudo /opt/bidguard/court-auction-controller/uninstall_controller.sh          keeps data and settings
#   sudo /opt/bidguard/court-auction-controller/uninstall_controller.sh --purge  also removes data, settings, service account
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "sudo 를 붙여 실행하십시오." >&2; exit 1; }
APP="/opt/bidguard/court-auction-controller"
DATA="/var/lib/court-auction-controller"
ETC="/etc/court-auction-controller"
UNIT="/etc/systemd/system/court-auction-controller.service"

if [ -d /run/systemd/system ]; then
  systemctl disable --now court-auction-controller.service 2>/dev/null || true
fi
rm -f "$UNIT"
[ -d /run/systemd/system ] && systemctl daemon-reload || true
rm -rf "$APP"
echo "관제기 프로그램과 서비스를 지웠습니다."
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$DATA" "$ETC"
  id bidguard-ctl >/dev/null 2>&1 && userdel bidguard-ctl || true
  echo "자료(${DATA})와 설정(${ETC}), 서비스 계정도 지웠습니다."
else
  echo "자료(${DATA})와 설정(${ETC})은 남겨 두었습니다. 다시 설치하면 그대로 이어서 씁니다."
fi
