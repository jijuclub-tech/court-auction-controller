#!/bin/bash
# 법원경매 관제기 제거 스크립트
# 사용법: sudo bash uninstall.sh

set -e

APP_NAME="bidguard-controller"
INSTALL_DIR="/opt/bidguard/controller"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

echo "=========================================="
echo "  법원경매 관제기 제거"
echo "=========================================="

if [ "$EUID" -ne 0 ]; then
    echo "[오류] root 권한이 필요합니다."
    exit 1
fi

echo "[1/3] 서비스 중지 및 비활성화..."
systemctl stop "$APP_NAME" 2>/dev/null || true
systemctl disable "$APP_NAME" 2>/dev/null || true

echo "[2/3] systemd 유닛 제거..."
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "[3/3] 설치 디렉터리 제거..."
read -p "설치 디렉터리($INSTALL_DIR)를 삭제하시겠습니까? (y/N): " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo "  삭제 완료"
else
    echo "  디렉터리 유지: $INSTALL_DIR"
fi

echo ""
echo "제거 완료."
echo "데이터 디렉터리(/data/auction)는 수동으로 확인하세요."
