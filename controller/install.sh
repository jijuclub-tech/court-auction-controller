#!/bin/bash
# 법원경매 관제기 - 우분투 서버 설치 스크립트
# 사용법: sudo bash install.sh
# 또는: bash install.sh  (일반 사용자, /opt 권한 필요 시 sudo)

set -e

APP_NAME="bidguard-controller"
INSTALL_DIR="/opt/bidguard/controller"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
DATA_DIR="/data/auction"
LOG_DIR="/var/log/bidguard"

echo "=========================================="
echo "  법원경매 관제기 설치 (Ubuntu)"
echo "=========================================="
echo ""

# root 권한 확인
if [ "$EUID" -ne 0 ]; then
    echo "[오류] root 권한이 필요합니다. sudo로 실행하세요."
    echo "  sudo bash install.sh"
    exit 1
fi

# OS 확인
if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
    echo "[경고] Ubuntu가 아닌 환경입니다. 계속 진행합니다."
fi

echo "[1/7] 시스템 패키지 설치..."
apt-get update -qq || echo "  [경고] apt update 일부 실패. 계속 진행합니다."
apt-get install -y -qq python3 python3-pip python3-venv python3-dev build-essential || {
    echo "  [경고] 패키지 설치 실패. Python이 이미 있으면 계속합니다."
}

echo "[2/7] 설치 디렉터리 생성..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$LOG_DIR"

# 현재 스크립트 위치 기준으로 파일 복사
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "  소스 경로: $SCRIPT_DIR"

cp -f "$SCRIPT_DIR/controller.py" "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/web_ui.py" "$INSTALL_DIR/"
cp -rf "$SCRIPT_DIR/templates" "$INSTALL_DIR/"
cp -rf "$SCRIPT_DIR/defaults" "$INSTALL_DIR/" 2>/dev/null || true
cp -rf "$SCRIPT_DIR/maps" "$INSTALL_DIR/" 2>/dev/null || true
cp -f "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true
cp -f "$SCRIPT_DIR/README.md" "$INSTALL_DIR/" 2>/dev/null || true

# 테스트 파일은 선택적
if [ -f "$SCRIPT_DIR/test_controller.py" ]; then
    cp -f "$SCRIPT_DIR/test_controller.py" "$INSTALL_DIR/"
fi

echo "[3/7] Python 가상환경 생성..."
cd "$INSTALL_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip -q
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q || echo "  (requirements.txt 설치 건너뜀 또는 비어 있음)"
fi

echo "[4/7] 권한 설정..."
# ubuntu 사용자 또는 현재 sudo 사용자
TARGET_USER="${SUDO_USER:-ubuntu}"
if id "$TARGET_USER" &>/dev/null; then
    chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR"
    chown -R "$TARGET_USER:$TARGET_USER" "$DATA_DIR"
    chown -R "$TARGET_USER:$TARGET_USER" "$LOG_DIR"
else
    echo "  [참고] 사용자 '$TARGET_USER' 없음. root 소유로 유지합니다."
fi

echo "[5/7] systemd 서비스 등록..."
cat > "$SERVICE_FILE" << SERVICEEOF
[Unit]
Description=Bidguard Court Auction Controller
After=network.target

[Service]
Type=simple
User=${TARGET_USER:-ubuntu}
Group=${TARGET_USER:-ubuntu}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/web_ui.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable "$APP_NAME"

echo "[6/7] 데이터 디렉터리 기본 설정..."
# 관제기 기본 저장 경로 힌트
echo "저장 경로 기본값: $DATA_DIR" > "$INSTALL_DIR/SAVE_PATH.txt"

echo "[7/7] 설치 완료"
echo ""
echo "=========================================="
echo "  설치 완료"
echo "=========================================="
echo ""
echo "설치 경로 : $INSTALL_DIR"
echo "데이터 경로: $DATA_DIR"
echo "서비스 이름: $APP_NAME"
echo ""
echo "서비스 제어:"
echo "  sudo systemctl start $APP_NAME"
echo "  sudo systemctl status $APP_NAME"
echo "  sudo journalctl -u $APP_NAME -f"
echo ""
echo "다음 단계:"
echo "  1. controller.py에서 set_save_path()로 저장 경로 확인"
echo "  2. bidguard 0단계 메뉴 연동 설정"
echo "  3. 수집기 등록 후 테스트"
echo ""
