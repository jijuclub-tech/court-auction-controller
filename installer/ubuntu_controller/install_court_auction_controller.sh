#!/usr/bin/env bash
# =====================================================================
#  BIDGUARD 법원경매 관제기 — 우분투 설치 프로그램
#
#  사람이 하는 일: 이 파일을 '프로그램으로 실행'(또는 터미널에서 bash 로 실행)
#                 → 비밀번호(sudo) 한 번 → 화면 질문에 [예]/[확인]
#  인터넷이 없어도 설치된다 (파이썬 부품이 묶음 안에 들어 있음).
#
#  하는 일
#    1 점검   우분투·파이썬·디스크·포트·묶음 지문·이전 설치(글록 판 서비스 포함)
#    2 설정   설치 자리, 포트
#    3 설치   자료 보관 → 새 판을 옆에 다 만든 뒤 한 번에 바꿔 끼움 → 서비스 등록
#             → 실제로 켜서 화면이 열리는지 확인 → 실패하면 설치 전 상태로 되돌림
#    4 완료   수집기에 넣을 관제기 주소, 브라우저 주소, 방화벽 안내
#
#  지키는 자료: controller.db, controller_state.json, credentials.json, credentials.enc,
#              apikeys.enc, .apikey_secret, defaults/work_policy.json, logs, map_submissions, reports
#
#  시험·자동화용 환경변수 (평소에는 쓰지 않음)
#    BIDGUARD_NONINTERACTIVE=1   질문 없이 기본값으로 진행
#    BIDGUARD_INSTALL_DIR, BIDGUARD_PORT, BIDGUARD_RUN_USER
#    BIDGUARD_ROOT_PREFIX        /etc/systemd 등을 이 아래로 (시험 상자용)
# =====================================================================
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 파일 관리자에서 두 번 눌러 실행했으면 터미널을 열어 그 안에서 다시 실행
if [ ! -t 0 ] && [ -z "${BIDGUARD_NONINTERACTIVE:-}" ]; then
  for term in gnome-terminal x-terminal-emulator konsole xfce4-terminal xterm; do
    if command -v "$term" >/dev/null 2>&1; then
      case "$term" in
        gnome-terminal) exec gnome-terminal -- bash "$HERE/$(basename "$0")" "$@" ;;
        *) exec "$term" -e bash "$HERE/$(basename "$0")" "$@" ;;
      esac
    fi
  done
fi

# 관리자 권한(sudo)으로 다시 실행 — /opt 와 서비스 등록에 필요
if [ "$(id -u)" -ne 0 ]; then
  echo
  echo "  관제기를 설치하려면 이 컴퓨터의 관리자 비밀번호가 필요합니다."
  echo "  (로그인할 때 쓰는 비밀번호입니다. 입력해도 화면에 글자가 보이지 않는 것이 정상입니다)"
  echo
  exec sudo -E bash "$HERE/$(basename "$0")" "$@"
fi

ROOT="${BIDGUARD_ROOT_PREFIX:-}"
VERSION_LABEL="$(python3 -c "import json;print(json.load(open('$HERE/version.json'))['label'])" 2>/dev/null || echo '?')"
PRODUCT="BIDGUARD 법원경매 관제기"
SERVICE_NAME="bidguard-court-auction-controller"
LEGACY_SERVICE="bidguard-controller"           # 글록 판 install.sh 가 만들던 서비스
UNIT_DIR="$ROOT/etc/systemd/system"
INSTALL_DIR="${BIDGUARD_INSTALL_DIR:-$ROOT/opt/bidguard/controller}"
PORT="${BIDGUARD_PORT:-8080}"
RUN_USER="${BIDGUARD_RUN_USER:-${SUDO_USER:-$(id -un)}}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="/tmp/bidguard_controller_install_$STAMP.log"
PRESERVE=(controller.db controller_state.json credentials.json credentials.enc apikeys.enc .apikey_secret defaults/work_policy.json logs map_submissions reports)
HAS_SYSTEMD=0; [ -d /run/systemd/system ] && [ -z "$ROOT" ] && HAS_SYSTEMD=1
UI="plain"; if [ -z "${BIDGUARD_NONINTERACTIVE:-}" ] && command -v whiptail >/dev/null 2>&1; then UI="whiptail"; fi

touch "$LOG"; chmod 600 "$LOG"
log()  { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >> "$LOG"; }
say()  { printf '%s\n' "$*"; log "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; log "OK $*"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*"; log "WARN $*"; }
fail() { printf '  \033[31m✖\033[0m %s\n' "$*"; log "FAIL $*"; }

# ── 화면 (whiptail 이 있으면 창, 없으면 글자) ─────────────────────
ui_msg() {   # 제목 내용
  if [ "$UI" = whiptail ]; then whiptail --title "$1" --msgbox "$2" 22 78
  else printf '\n=== %s ===\n%s\n' "$1" "$2"; [ -n "${BIDGUARD_NONINTERACTIVE:-}" ] || read -r -p "  [Enter] 를 누르십시오 " _; fi
}
ui_yesno() { # 제목 내용 기본(yes/no) → 0=예
  local def="${3:-yes}"
  if [ -n "${BIDGUARD_NONINTERACTIVE:-}" ]; then [ "$def" = yes ]; return; fi
  if [ "$UI" = whiptail ]; then
    if [ "$def" = no ]; then whiptail --title "$1" --defaultno --yesno "$2" 22 78; else whiptail --title "$1" --yesno "$2" 22 78; fi
  else
    printf '\n=== %s ===\n%s\n' "$1" "$2"; local a; read -r -p "  예/아니오 (y/n, 기본 $def): " a
    a="${a:-$def}"; [[ "$a" =~ ^(y|Y|yes|예|ㅇ)$ ]]
  fi
}
ui_input() { # 제목 내용 기본값 → 값
  if [ -n "${BIDGUARD_NONINTERACTIVE:-}" ]; then echo "$3"; return; fi
  if [ "$UI" = whiptail ]; then whiptail --title "$1" --inputbox "$2" 14 78 "$3" 3>&1 1>&2 2>&3
  else local a; printf '\n=== %s ===\n%s\n' "$1" "$2" >&2; read -r -p "  (기본 $3): " a; echo "${a:-$3}"; fi
}

# ── 도우미 ───────────────────────────────────────────────────────
free_mb() { python3 - "$1" <<'EOF'
import shutil, sys
from pathlib import Path
p = Path(sys.argv[1])
while not p.exists():
    p = p.parent          # 아직 없는 폴더면 있는 윗폴더 기준으로 잼
print(shutil.disk_usage(p).free // (1024 * 1024))
EOF
}
py_ver_ok() { python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; }
port_free() { python3 - "$1" <<'EOF'
import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError: sys.exit(1)
EOF
}
lan_ip() { python3 - <<'EOF'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.168.0.1", 9)); print(s.getsockname()[0])   # 실제로 보내지 않음 (주소 고르기만)
except OSError:
    print("127.0.0.1")
EOF
}
check_manifest() { python3 - "$HERE" <<'EOF'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1]); m = json.loads((root / "BUILD-MANIFEST.json").read_text(encoding="utf-8"))
for rel, want in m["files"].items():
    p = root / rel
    if not p.is_file():
        print(f"빠진 파일: {rel}"); sys.exit(1)
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != want:
        print(f"바뀌었거나 망가진 파일: {rel}"); sys.exit(1)
print(len(m["files"]))
EOF
}
wheels_for_this_python() {   # 이 파이썬에 맞는 부품이 묶음에 있는지
  local tag; tag="cp$(python3 -c 'import sys;print(f"{sys.version_info[0]}{sys.version_info[1]}")')"
  ls "$HERE/prerequisites/wheels/" | grep -qi "markupsafe.*${tag}" && ls "$HERE/prerequisites/wheels/" | grep -qi '^pip-'
}
service_active()  { [ "$HAS_SYSTEMD" = 1 ] && systemctl is-active --quiet "$1" 2>/dev/null; }
service_enabled() { [ "$HAS_SYSTEMD" = 1 ] && systemctl is-enabled --quiet "$1" 2>/dev/null; }
http_ok() { python3 - "$1" <<'EOF'
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/login", timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
EOF
}

# =====================================================================
ui_msg "$PRODUCT 설치 ($VERSION_LABEL)" "이 프로그램은 법원경매 관제기를 이 컴퓨터에 설치합니다.

 • 인터넷이 없어도 설치됩니다. 필요한 부품이 묶음 안에 들어 있습니다.
 • 컴퓨터를 켜면 관제기가 자동으로 실행되도록 등록합니다.
 • 이미 설치된 관제기가 있으면 수집기 목록·작업·API 키 등 자료를
   그대로 지키고 프로그램만 새 판으로 바꿉니다.
 • 설치 중 문제가 생기면 설치 전 상태로 자동으로 되돌립니다.

걸리는 시간: 1~2분.  다음 화면에서 먼저 이 컴퓨터를 점검합니다."

say ""; say "■ 1. 점검"
BLOCK=0
. /etc/os-release 2>/dev/null || true
if [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID%%.*}" -ge 22 ] 2>/dev/null; then ok "우분투 ${VERSION_ID}"; else warn "우분투 22.04 이상에서 시험했습니다 (지금: ${PRETTY_NAME:-알 수 없음}). 계속은 할 수 있습니다."; fi
if command -v python3 >/dev/null && py_ver_ok; then ok "파이썬 $(python3 -c 'import platform;print(platform.python_version())')"; else fail "파이썬 3.10 이상이 필요합니다 (우분투 22.04 이상에는 기본으로 있습니다)."; BLOCK=1; fi
if python3 -c 'import venv' 2>/dev/null; then ok "파이썬 실행 공간(venv) 기능"; else fail "파이썬 venv 기능이 없습니다."; BLOCK=1; fi
FREE_MB=$(free_mb "$INSTALL_DIR")
if [ "${FREE_MB:-0}" -ge 500 ]; then ok "디스크 여유 ${FREE_MB}MB"; else fail "디스크 여유가 500MB 보다 적습니다 (${FREE_MB}MB)."; BLOCK=1; fi
if N=$(check_manifest 2>&1); then ok "설치 묶음 파일 ${N}개 지문 확인"; else fail "설치 묶음 문제: $N — 묶음을 다시 받으십시오."; BLOCK=1; fi
if wheels_for_this_python; then ok "이 파이썬에 맞는 부품이 묶음에 있음"; else fail "이 파이썬($(python3 -V 2>&1))에 맞는 부품이 묶음에 없습니다. 관리자에게 알리십시오."; BLOCK=1; fi
OLD_VERSION=""; OLD_PORT=""
if [ -f "$INSTALL_DIR/install_record.json" ]; then
  OLD_VERSION=$(python3 -c "import json;print(json.load(open('$INSTALL_DIR/install_record.json')).get('version','?'))")
  ok "이전 설치: $OLD_VERSION ($INSTALL_DIR) → 자료를 지키고 새 판으로 바꿉니다"
  OLD_PORT=$(python3 -c "import json;print(json.load(open('$INSTALL_DIR/install_record.json')).get('port',''))")
  [ -n "$OLD_PORT" ] && [ -z "${BIDGUARD_PORT:-}" ] && PORT="$OLD_PORT"
elif [ -f "$INSTALL_DIR/web_ui.py" ]; then
  ok "예전 방식으로 설치된 관제기 발견 ($INSTALL_DIR) → 자료를 지키고 새 판으로 바꿉니다"
else ok "이전 설치 없음 (새로 설치)"; fi
KEPT_DATA=""
if [ ! -d "$INSTALL_DIR" ]; then
  KEPT_DATA=$(ls -d "$(dirname "$INSTALL_DIR")"/controller_data_* 2>/dev/null | sort | tail -1 || true)
  [ -n "$KEPT_DATA" ] && ok "예전에 지울 때 옮겨 둔 자료 발견: $KEPT_DATA"
fi
LEGACY_UNIT="$UNIT_DIR/$LEGACY_SERVICE.service"
if [ -f "$LEGACY_UNIT" ]; then warn "글록 판 서비스($LEGACY_SERVICE)가 있습니다 → 새 서비스로 바꾸고 정리합니다"; fi
if [ "$HAS_SYSTEMD" = 1 ]; then ok "자동 실행(systemd) 사용 가능"; else warn "systemd 가 없습니다 → 자동 실행 대신 실행 파일만 만듭니다."; fi
if id "$RUN_USER" >/dev/null 2>&1; then ok "관제기를 실행할 계정: $RUN_USER"; else fail "계정 $RUN_USER 이(가) 없습니다."; BLOCK=1; fi

if [ "$BLOCK" = 1 ]; then
  ui_msg "점검 결과" "✖ 표시된 문제를 해결해야 설치할 수 있습니다.
아무것도 바뀌지 않았습니다.

기록 파일: $LOG"
  exit 1
fi

say ""; say "■ 2. 설정"
while :; do
  PORT=$(ui_input "포트" "수집기와 브라우저가 관제기에 들어오는 번호(포트)입니다.
보통 그대로 두면 됩니다." "$PORT")
  if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then ui_msg "포트" "1024~65535 사이 숫자를 넣으십시오."; [ -n "${BIDGUARD_NONINTERACTIVE:-}" ] && exit 1; continue; fi
  # 지금 그 포트를 쓰는 것이 이 관제기 자신(이전 설치)이면 괜찮음 — 설치 때 멈추고 새 판으로 다시 켬
  if service_active "$SERVICE_NAME" || service_active "$LEGACY_SERVICE" || port_free "$PORT"; then break; fi
  if [ "${OLD_PORT:-}" = "$PORT" ] && http_ok "$PORT"; then break; fi
  ui_msg "포트" "$PORT 번은 다른 프로그램이 쓰고 있습니다. 다른 번호를 넣으십시오 (예: 8090)."
  [ -n "${BIDGUARD_NONINTERACTIVE:-}" ] && exit 1
done
ok "포트 $PORT"
if [ -n "$KEPT_DATA" ]; then
  if ui_yesno "옮겨 둔 자료" "예전에 관제기를 지울 때 옮겨 둔 자료가 있습니다.
$KEPT_DATA

이 자료(수집기 목록·작업·API 키·DB)를 새 설치에 되살릴까요?" yes; then ok "옮겨 둔 자료를 되살립니다"; else KEPT_DATA=""; fi
fi
ui_yesno "설치 확인" "다음 내용으로 설치합니다.

  설치 자리 : $INSTALL_DIR
  포트      : $PORT
  실행 계정 : $RUN_USER
  자동 실행 : $( [ "$HAS_SYSTEMD" = 1 ] && echo "컴퓨터를 켜면 자동 실행" || echo "없음 (직접 실행)")
  이전 자료 : $( { [ -n "$OLD_VERSION" ] || [ -f "$INSTALL_DIR/web_ui.py" ]; } && echo "그대로 지킴" || { [ -n "$KEPT_DATA" ] && echo "옮겨 둔 자료 되살림" || echo "없음"; })

설치할까요?" yes || { say "취소했습니다. 아무것도 바뀌지 않았습니다."; exit 0; }

# =====================================================================
say ""; say "■ 3. 설치"
NEW="$INSTALL_DIR.new_$STAMP"; OLD="$INSTALL_DIR.old_$STAMP"; SAVE="/tmp/bidguard_controller_preserve_$STAMP"
UNIT_BACKUP="/tmp/bidguard_controller_units_$STAMP"
SWAPPED=0; STOPPED_SERVICES=()
rollback() {
  trap - ERR; set +e
  local reason="$1"
  fail "멈춤: $reason"
  say "  설치 전 상태로 되돌리는 중..."
  if [ "$HAS_SYSTEMD" = 1 ]; then systemctl stop "$SERVICE_NAME" 2>/dev/null || true; systemctl disable "$SERVICE_NAME" 2>/dev/null || true; fi
  [ -f "$UNIT_DIR/$SERVICE_NAME.service" ] && rm -f "$UNIT_DIR/$SERVICE_NAME.service"
  if [ -d "$UNIT_BACKUP" ]; then cp -a "$UNIT_BACKUP/." "$UNIT_DIR/" 2>/dev/null || true; fi
  [ -f "$INSTALL_DIR/.bidguard_test_pid" ] && kill "$(cat "$INSTALL_DIR/.bidguard_test_pid")" 2>/dev/null || true
  if [ "$SWAPPED" = 1 ]; then rm -rf "$INSTALL_DIR"; [ -d "$OLD" ] && mv "$OLD" "$INSTALL_DIR"; fi
  rm -rf "$NEW"
  if [ "$HAS_SYSTEMD" = 1 ]; then
    systemctl daemon-reload || true
    for s in "${STOPPED_SERVICES[@]}"; do systemctl start "$s" 2>/dev/null || true; done
  fi
  say "  되돌리기 끝. 이전 상태 그대로입니다. 기록: $LOG"
  ui_msg "설치를 마치지 못했습니다" "이유: $reason

설치 전 상태로 되돌렸습니다. 컴퓨터에는 바뀐 것이 없습니다.
아래 기록 파일을 관리자에게 보내 주십시오.
$LOG"
  exit 1
}
trap 'rollback "예상하지 못한 오류 (줄 $LINENO)"' ERR

# 3-1 새 판을 옆자리에 다 만들기 (지금 돌고 있는 관제기는 아직 그대로)
mkdir -p "$NEW"
cp -a "$HERE/payload/controller/." "$NEW/"
ok "프로그램 파일 준비"
python3 -m venv --without-pip "$NEW/venv" >>"$LOG" 2>&1 || rollback "파이썬 실행 공간(venv)을 만들지 못했습니다."
PIP_WHEEL=$(ls "$HERE"/prerequisites/wheels/pip-*.whl | head -1)
"$NEW/venv/bin/python" "$PIP_WHEEL/pip" install --no-index --find-links "$HERE/prerequisites/wheels" \
   -r "$HERE/prerequisites/controller_requirements.txt" --disable-pip-version-check --no-warn-script-location >>"$LOG" 2>&1 \
   || rollback "부품 설치에 실패했습니다 (인터넷 없이 묶음 부품으로 설치)."
ok "부품 설치 (인터넷 없이)"
( cd "$NEW" && "$NEW/venv/bin/python" -c "import flask, cryptography, controller; print('SELFCHECK OK')" ) >>"$LOG" 2>&1 \
   || rollback "새 판 자가점검 실패 (부품을 불러오지 못함)."
ok "새 판 자가점검"

# 3-2 자료 보관 → 멈추기 → 바꿔 끼우기 → 자료 되돌려 놓기
mkdir -p "$SAVE"; chmod 700 "$SAVE"
SAVED=()
DATA_SRC=""; [ -d "$INSTALL_DIR" ] && DATA_SRC="$INSTALL_DIR"; [ -z "$DATA_SRC" ] && [ -n "$KEPT_DATA" ] && DATA_SRC="$KEPT_DATA"
if [ -n "$DATA_SRC" ]; then
  for item in "${PRESERVE[@]}"; do
    if [ -e "$DATA_SRC/$item" ]; then mkdir -p "$SAVE/$(dirname "$item")"; cp -a "$DATA_SRC/$item" "$SAVE/$item"; SAVED+=("$item"); fi
  done
fi
ok "자료 보관: ${SAVED[*]:-(없음)}"
mkdir -p "$UNIT_BACKUP"
for s in "$SERVICE_NAME" "$LEGACY_SERVICE"; do
  if [ -f "$UNIT_DIR/$s.service" ]; then cp -a "$UNIT_DIR/$s.service" "$UNIT_BACKUP/"; fi
  if service_active "$s"; then systemctl stop "$s"; STOPPED_SERVICES+=("$s"); ok "실행 중이던 $s 멈춤"; fi
done
[ -f "$INSTALL_DIR/.bidguard_test_pid" ] && kill "$(cat "$INSTALL_DIR/.bidguard_test_pid")" 2>/dev/null || true
if [ -d "$INSTALL_DIR" ]; then mv "$INSTALL_DIR" "$OLD"; fi
mv "$NEW" "$INSTALL_DIR"; SWAPPED=1
for item in "${SAVED[@]}"; do mkdir -p "$INSTALL_DIR/$(dirname "$item")"; rm -rf "${INSTALL_DIR:?}/$item"; cp -a "$SAVE/$item" "$INSTALL_DIR/$item"; done
cat > "$INSTALL_DIR/Run_Court_Auction_Controller.sh" <<EOF
#!/usr/bin/env bash
# 관제기를 직접 켜는 파일 (보통은 자동 실행 서비스가 켬)
cd "\$(dirname "\$0")"
umask 077   # 관제기가 만드는 DB·API 키 파일을 이 계정만 읽게 함
exec ./venv/bin/python -c "import web_ui; web_ui.app.run(host='0.0.0.0', port=$PORT)"
EOF
chmod 755 "$INSTALL_DIR/Run_Court_Auction_Controller.sh"
cp "$HERE/installer/uninstall_court_auction_controller.sh" "$INSTALL_DIR/"; chmod 755 "$INSTALL_DIR/uninstall_court_auction_controller.sh"
chown -R "$RUN_USER":"$(id -gn "$RUN_USER")" "$INSTALL_DIR"
for secret in credentials.json credentials.enc apikeys.enc .apikey_secret; do if [ -f "$INSTALL_DIR/$secret" ]; then chmod 600 "$INSTALL_DIR/$secret"; fi; done
ok "새 판으로 바꿔 끼우고 자료 되돌려 놓음"

# 3-3 자동 실행 등록
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$SERVICE_NAME.service" <<EOF
[Unit]
Description=BIDGUARD Court Auction Controller ($VERSION_LABEL)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
# 관제기가 만드는 DB·API 키 파일을 이 계정만 읽게 함
UMask=0077
ExecStart=$INSTALL_DIR/venv/bin/python -c "import web_ui; web_ui.app.run(host='0.0.0.0', port=$PORT)"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
if [ "$HAS_SYSTEMD" = 1 ]; then
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME" >>"$LOG" 2>&1
  ok "자동 실행 등록 ($SERVICE_NAME)"
else
  # 설치 창과 완전히 떼어 놓고 켬 (입출력을 모두 기록 파일로 돌려 설치 창이 닫혀도 계속 돎)
  if [ "$RUN_USER" = "$(id -un)" ]; then
    ( cd "$INSTALL_DIR" && exec setsid nohup ./Run_Court_Auction_Controller.sh </dev/null >>"$LOG" 2>&1 ) &
  else
    ( cd "$INSTALL_DIR" && exec setsid sudo -u "$RUN_USER" nohup ./Run_Court_Auction_Controller.sh </dev/null >>"$LOG" 2>&1 ) &
  fi
  echo $! > "$INSTALL_DIR/.bidguard_test_pid"
  warn "systemd 없음 → 지금만 직접 켰습니다. 다음부터는 $INSTALL_DIR/Run_Court_Auction_Controller.sh 로 켜십시오."
fi

# 3-4 실제로 켜졌는지 확인 (화면이 열리는지)
say "  관제기가 켜지기를 기다리는 중..."
UP=0; for _ in $(seq 1 30); do if http_ok "$PORT"; then UP=1; break; fi; sleep 1; done
[ "$UP" = 1 ] || rollback "관제기가 켜지지 않았습니다 (포트 $PORT 에서 로그인 화면이 열리지 않음)."
ok "관제기 화면 열림 확인 (http://127.0.0.1:$PORT/login)"
trap - ERR

# 3-5 마무리: 옛 판·글록 판 서비스 정리, 기록
if [ -f "$UNIT_DIR/$LEGACY_SERVICE.service" ]; then
  [ "$HAS_SYSTEMD" = 1 ] && systemctl disable "$LEGACY_SERVICE" >>"$LOG" 2>&1 || true
  rm -f "$UNIT_DIR/$LEGACY_SERVICE.service"; [ "$HAS_SYSTEMD" = 1 ] && systemctl daemon-reload
  ok "글록 판 서비스($LEGACY_SERVICE) 정리"
fi
rm -rf "$OLD" "$SAVE" "$UNIT_BACKUP"
[ -n "$KEPT_DATA" ] && rm -rf "$KEPT_DATA" && ok "되살린 옮겨 둔 자료 폴더 정리" || true
IP="$(lan_ip)"
python3 - "$INSTALL_DIR/install_record.json" <<EOF
import json, sys
json.dump({"product": "BIDGUARD_Court_Auction_Controller", "version": "$VERSION_LABEL", "installed_at": "$(date -Iseconds)",
           "install_dir": "$INSTALL_DIR", "port": "$PORT", "run_user": "$RUN_USER", "service": "$SERVICE_NAME",
           "systemd": $HAS_SYSTEMD == 1, "preserved": "${SAVED[*]:-}".split()}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
EOF
chown "$RUN_USER" "$INSTALL_DIR/install_record.json"
cp "$LOG" "$INSTALL_DIR/logs/" 2>/dev/null || { mkdir -p "$INSTALL_DIR/logs"; cp "$LOG" "$INSTALL_DIR/logs/"; chown -R "$RUN_USER" "$INSTALL_DIR/logs"; }

FIREWALL_NOTE=""
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  if ! ufw status | grep -q "^$PORT"; then
    if ui_yesno "방화벽" "이 컴퓨터의 방화벽(ufw)이 켜져 있습니다.
같은 공유기에 있는 수집기가 들어올 수 있게 $PORT 번을 집 안 네트워크에만 열까요?" yes; then
      NET="$(echo "$IP" | awk -F. '{print $1"."$2"."$3".0/24"}')"
      ufw allow from "$NET" to any port "$PORT" proto tcp >>"$LOG" 2>&1 && ok "방화벽: $NET 에서 $PORT 번 허용"
    else FIREWALL_NOTE="방화벽이 $PORT 번을 막고 있어 다른 컴퓨터의 수집기는 연결되지 않습니다."; fi
  fi
fi

say ""; say "■ 4. 완료"
ui_msg "설치를 마쳤습니다" "✔ $PRODUCT $VERSION_LABEL 설치 완료

브라우저에서 관제기 열기 :  http://$IP:$PORT
수집기에 넣을 관제기 주소 :  $IP:$PORT
  (수집기 설치 화면의 '관제기 주소' 또는 수집기 Settings 탭에 넣습니다)

자동 실행 : $( [ "$HAS_SYSTEMD" = 1 ] && echo "컴퓨터를 켜면 자동으로 켜집니다" || echo "없음 — Run_Court_Auction_Controller.sh 로 켜십시오")
지키는 자료 : ${SAVED[*]:-새 설치}
${FIREWALL_NOTE}

⚠ 주의: 관리자 로그인은 지금 admin / admin 입니다.
   관제기 프로그램이 켜질 때마다 admin/admin 으로 되돌리게 되어 있어
   설치 프로그램에서 바꿀 수 없습니다 (프로그램 수정 검수 항목으로 올림).
   집 안 네트워크 밖으로 이 주소를 열지 마십시오.

지우기 : sudo bash $INSTALL_DIR/uninstall_court_auction_controller.sh"
say "기록: $INSTALL_DIR/logs/$(basename "$LOG")"
