#!/usr/bin/env bash
# Court auction controller (법원경매 관제기) installer for Ubuntu.
#   wget -qO- https://raw.githubusercontent.com/jijuclub-tech/court-auction-controller/<branch>/install/install_controller.sh | sudo bash
# or, from a downloaded copy of the repository:
#   sudo bash install/install_controller.sh
#
# What it does
#   - installs controller.py, admin.html, collector_defaults.json into /opt/bidguard/court-auction-controller
#     (earlier program files there are replaced; data is kept in /var/lib/court-auction-controller)
#   - asks the manager for the manager-page password (admin token) on first install; it is stored only in
#     /etc/court-auction-controller/controller.env (readable by root only) and never printed
#   - runs the controller as a system service (starts at boot, restarts after a crash), port 8770
#   - does not touch the old Node control center (/opt/bidguard/control-center, port 8787)
# Needs: Ubuntu with python3 (3.8 or later; Ubuntu 22.04/24.04 have it). No packages are installed.
set -euo pipefail

VERSION="S-0.1.2-CONTROL+T-v0.2-KEYS"
BRANCH="${BRANCH:-main}"
RAW="https://raw.githubusercontent.com/jijuclub-tech/court-auction-controller/${BRANCH}"
APP="/opt/bidguard/court-auction-controller"
DATA="/var/lib/court-auction-controller"
ETC="/etc/court-auction-controller"
ENVF="${ETC}/controller.env"
UNIT="/etc/systemd/system/court-auction-controller.service"
SVC_USER="bidguard-ctl"
PORT="${PORT:-8770}"
FILES="controller.py collector_dispatch.py learning.py performance.py site_map_ingest.py site_kb_T_v0_1.py site_map_sender.py site_map_backup.py key_wallet.py collector_key_wallet_T_v0_2.py admin.html collector_defaults.json install/uninstall_controller.sh"

say() { printf '\n== %s\n' "$*"; }
die() { printf '\n[중단] %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "관리자 권한이 필요합니다. 명령 앞에 sudo 를 붙여 다시 실행하십시오."
command -v python3 >/dev/null || die "python3 이 없습니다. (우분투에는 기본으로 있습니다)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || die "python3 3.8 이상이 필요합니다."

say "법원경매 관제기 ${VERSION} 설치를 시작합니다"

# 1. program files: from this folder if the script sits in the repository, otherwise from GitHub
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
if [ -f "${HERE}/../controller.py" ]; then
  say "내려받은 저장소 폴더의 파일을 씁니다: ${HERE%/install}"
  for f in $FILES; do cp "${HERE}/../${f}" "${STAGE}/$(basename "$f")"; done
else
  say "GitHub(${BRANCH})에서 파일을 받습니다"
  for f in $FILES; do
    if command -v wget >/dev/null; then wget -q -O "${STAGE}/$(basename "$f")" "${RAW}/${f}"
    elif command -v curl >/dev/null; then curl -fsSL -o "${STAGE}/$(basename "$f")" "${RAW}/${f}"
    else die "wget 이나 curl 이 필요합니다."; fi
  done
fi
python3 -m py_compile "${STAGE}/controller.py" "${STAGE}/collector_dispatch.py" "${STAGE}/learning.py" "${STAGE}/performance.py" "${STAGE}/site_map_ingest.py" "${STAGE}/site_kb_T_v0_1.py" "${STAGE}/site_map_sender.py" "${STAGE}/site_map_backup.py" "${STAGE}/key_wallet.py" "${STAGE}/collector_key_wallet_T_v0_2.py" || die "파이썬 프로그램 파일을 읽을 수 없습니다."
python3 -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" "${STAGE}/collector_defaults.json" \
  || die "collector_defaults.json 이 깨졌습니다."

# 1b. Free, audited library for local CLI wallet. Main controller works independently.
if ! python3 -c 'from cryptography.hazmat.primitives.ciphers.aead import AESGCM' >/dev/null 2>&1; then
  say "키 지갑 암호화 라이브러리 설치를 시도합니다 (무료 python3-cryptography)"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3-cryptography || say "설치 실패: 지갑만 비활성. python3-cryptography 설치 후 사용하십시오."
  else
    say "python3-cryptography 설치가 필요합니다. 기존 관제기는 계속 작동합니다."
  fi
fi

# 2. service account (no login, no home)
if ! id "$SVC_USER" >/dev/null 2>&1; then
  say "서비스 계정 ${SVC_USER} 을 만듭니다 (로그인 불가 계정)"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

# 3. stop an older copy, replace program files (data stays)
if systemctl list-unit-files 2>/dev/null | grep -q '^court-auction-controller.service'; then
  say "이전에 설치된 관제기를 멈춥니다 (자료는 그대로 둡니다)"
  systemctl stop court-auction-controller.service || true
fi
mkdir -p "$APP" "$DATA" "$ETC"
find "$APP" -mindepth 1 -maxdepth 1 -type f -delete
for f in $FILES; do install -m 0644 "${STAGE}/$(basename "$f")" "${APP}/"; done
chmod 0755 "${APP}/uninstall_controller.sh"
printf '%s\n' "$VERSION" > "${APP}/VERSION"
chown -R "$SVC_USER":"$SVC_USER" "$DATA"
chmod 0750 "$DATA"

# 4. settings file with the manager token (asked once, never printed)
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ ! -f "$ENVF" ]; then
  TOKEN="${CONTROLLER_ADMIN_TOKEN:-}"
  if [ -z "$TOKEN" ]; then
    [ -r /dev/tty ] || die "관리인 암호를 물어볼 화면이 없습니다. CONTROLLER_ADMIN_TOKEN 을 정해 다시 실행하십시오."
    say "관제기 관리인 화면에 들어갈 암호를 정합니다 (12자 이상, 화면에 보이지 않습니다)"
    while :; do
      read -r -s -p "관리인 암호: " T1 < /dev/tty; echo
      read -r -s -p "한 번 더: " T2 < /dev/tty; echo
      if [ "${#T1}" -lt 12 ]; then echo "12자 이상으로 정하십시오."; continue; fi
      if [ "$T1" != "$T2" ]; then echo "두 번 넣은 암호가 다릅니다."; continue; fi
      TOKEN="$T1"; break
    done
  fi
  case "$TOKEN" in *$'\n'*|*\"*|*\\*) die "암호에 따옴표·역슬래시·줄바꿈은 쓸 수 없습니다.";; esac
  BOT="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  umask 077
  cat > "$ENVF" <<EOF
# court-auction-controller settings (root only). Change, then: sudo systemctl restart court-auction-controller
CONTROLLER_ADMIN_TOKEN="${TOKEN}"
CONTROLLER_BOT_TOKEN="${BOT}"
CONTROLLER_DATA="${DATA}"
CONTROLLER_PUBLIC_URL="http://${IP:-127.0.0.1}:${PORT}"
# BIDGUARD call channel for phone calls (same channel as member verification). Empty = no phone notice.
NOTIFY_URL=""
# Optional own AI for rung 3, e.g. gemini:gemini-2.5-pro (then also set GEMINI_API_KEY)
CONTROLLER_AI=""
GEMINI_API_KEY=""
EOF
  umask 022
  unset TOKEN T1 T2
  say "설정 파일을 만들었습니다: ${ENVF} (root 만 읽을 수 있음)"
else
  say "기존 설정 파일을 그대로 씁니다: ${ENVF}"
fi
chmod 0600 "$ENVF"

# 5. service
cat > "$UNIT" <<EOF
[Unit]
Description=Court auction controller (BIDGUARD) ${VERSION}
After=network-online.target
Wants=network-online.target

[Service]
User=${SVC_USER}
Group=${SVC_USER}
EnvironmentFile=${ENVF}
WorkingDirectory=${DATA}
ExecStart=/usr/bin/env python3 ${APP}/controller.py --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${DATA}

[Install]
WantedBy=multi-user.target
EOF

if [ -d /run/systemd/system ]; then
  systemctl daemon-reload
  systemctl enable --now court-auction-controller.service >/dev/null
  systemctl restart court-auction-controller.service
else
  say "이 환경에는 systemd 가 없어 서비스로 등록만 하고 켜지 않았습니다"
fi

# 6. firewall: only if ufw is on, open the port to the home network only
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  say "방화벽(ufw)에서 집 안 네트워크(192.168.0.0/16)에만 ${PORT}번을 엽니다"
  ufw allow from 192.168.0.0/16 to any port "$PORT" proto tcp >/dev/null
fi

# 7. check
if [ -d /run/systemd/system ]; then
  ok=""
  for _ in $(seq 1 15); do
    if python3 - "$PORT" <<'PY' 2>/dev/null; then ok=1; break; fi
import json, sys, urllib.request
d = json.load(urllib.request.urlopen("http://127.0.0.1:%s/api/health" % sys.argv[1], timeout=2))
sys.exit(0 if d.get("ok") else 1)
PY
    sleep 1
  done
  [ -n "$ok" ] || die "관제기가 켜지지 않았습니다. 원인 보기: sudo journalctl -u court-auction-controller -n 50"
fi

say "설치 끝 — ${VERSION}"
echo "  관리인 화면 (같은 공유기의 PC·휴대폰): http://${IP:-이-컴퓨터-주소}:${PORT}/admin"
echo "  수집기 '관제형' 탭의 관제기 주소:      http://${IP:-이-컴퓨터-주소}:${PORT}"
echo "  상태 보기:   sudo systemctl status court-auction-controller"
echo "  지우기:      sudo ${APP}/uninstall_controller.sh   (자료까지 지우려면 --purge)"
