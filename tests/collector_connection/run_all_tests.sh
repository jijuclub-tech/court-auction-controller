#!/bin/bash
# 수집기 ↔ 관제기 연결 시험 전체 실행 (우분투/리눅스, Python 3.10+, flask·cryptography·requests 필요)
# 저장소의 controller/ 와 new_collector/ 를 임시 폴더에 복사해서 시험하므로 원본 폴더에는 아무 파일도 생기지 않는다.
# 사용: bash tests/collector_connection/run_all_tests.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PORT="${PORT:-8099}"
WORK="$(mktemp -d)"
cp -r "$REPO/controller" "$WORK/controller"
cp -r "$REPO/new_collector" "$WORK/collector"
mkdir -p "$WORK/saved"
export TEST_CTRL_URL="http://127.0.0.1:$PORT"
export TEST_CTRL_DB="$WORK/controller/controller.db"
export TEST_SAVE_DIR="$WORK/saved"
export FAKE_TK_DIR="$HERE/fake_tk"

cd "$WORK/controller"
python3 "$HERE/run_controller.py" "$PORT" "$WORK/saved" > "$WORK/controller.log" 2>&1 &
CTRL_PID=$!
trap 'kill $CTRL_PID 2>/dev/null' EXIT
for i in $(seq 1 30); do
  curl -s -o /dev/null "$TEST_CTRL_URL/login" && break
  sleep 0.5
done

FAIL=0
cd "$WORK/collector"
for t in test_connection_flow.py test_connection_edge_cases.py test_grok_parts.py; do
  echo "=== $t"
  python3 "$HERE/$t" 2>&1 | grep -E "^PASS|^FAIL|^   |ALL PASS|SOME FAILED|Traceback|Error"
done | tee "$WORK/result.txt"
grep -q "SOME FAILED\|^FAIL\|Traceback" "$WORK/result.txt" && FAIL=1
echo
echo "시험 폴더: $WORK  (관제기 로그: $WORK/controller.log)"
[ $FAIL -eq 0 ] && echo "전체 결과: ALL PASS" || echo "전체 결과: 실패 있음"
exit $FAIL
