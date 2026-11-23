# CENTRAL WATCH · 독립 휴대폰 관제 모니터 T v0.1.0

**사용자 BIDGUARD PWA와 완전히 별개입니다.** `index.html`은 혼자 열어도 **샘플 화면**을 볼 수 있고, `monitor_server_T_v0.1.0.py`는 기존 관제 DB를 수정하지 않는 읽기 전용 로컬 서버입니다. 운영 서버에 아직 설치하지 않았고, 휴대폰 HTTPS 접속·알림·원격 지시 기능은 실증하지 않았습니다.

## 화면/기능

- 휴대폰, Galaxy S22 Ultra, 삼성 탭 대응. 큰 글씨, 5개 메뉴 **현황·장비·저장·장애·원격**.
- 등록 장비·서비스 실행/통신만·마지막 신호 시각·오프라인/미확인, 장애 이력과 NAS X:/Z:/Drive 분리.
- 서버 접속 전 **미확인**. 샘플 모드를 명시적으로 켠 경우 보라색 **샘플 데이터** 배지.
- 30초마다 자동 조회. 서버 응답 실패 → 연결 미확인. 완성 전 수집 실적과 백업 성공 **숫자 조작/성공 표시 금지**.
- Chrome 원격 데스크톱 열기 링크가 있으며, **문자 지시 서버 전송 버튼은 실제 API 검증 전 비활성**. 지시문 복사는 휴대폰 내부 동작.

## 로컬 실행 (관제기 서버 사용자 또는 개발용 PC에서)

```bash
cd monitor_mobile_T_v0.1.0
export MONITOR_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(40))')"
# 기본 관제 DB는 /opt/bidguard/control-center/data/control_center.db
# 다른 DB 경로가 확인된 경우에만 아래 환경변수를 지정
# export MONITOR_CONTROLLER_DB=/opt/bidguard/control-center/data/control_center.db
python3 monitor_server_T_v0.1.0.py
```

- PC 내부에서 `http://127.0.0.1:8765/` 열고 우측 상단 자물쇠에서 위 토큰으로 로그인. 토큰은 브라우저 메모리에만 유지되며 새로고침하면 다시 필요합니다.
- **휴대폰에서 볼 때**: Claude가 실제 관제기에서 승인된 Tailscale Serve HTTPS / 인증 역방향 프록시를 구성해 이 `127.0.0.1:8765` 서비스를 게시하고, 휴대폰의 Tailscale 인증 연결 후 **확인된 HTTPS 주소**로 접속하도록 구성해야 합니다. **포트 8765를 인터넷에 직접 공개하거나 `0.0.0.0`로 바꾸지 마세요.** 임의 접속 URL을 생성하지 않았습니다.
- 이 예시 토큰은 재시작 시 사라집니다. 운영할 때는 root만 읽을 수 있는 별도 보안 환경 파일·키 금고(#47)에 생성/등록해야 합니다. 키/토큰을 코드·GitHub·채팅·스크린샷에 넣지 마세요.
- Python 표준 라이브러리만 사용. 제어 작업·DB 테이블 생성·파일 업로드·실제 원격 조작은 하지 않습니다.

## 현재 실제 연동 범위

- `systemctl is-active bidguard-control-center`: **실제 프로세스 실행 상태** 확인. 이것만으로 API 정상은 증명되지 않으므로 **통신 확인** 상태로 표시.
- 선택 `MONITOR_LOCAL_HEALTH_URL=http://127.0.0.1:<실제 health 포트/경로>`: 200 + JSON `status=ok` 또는 `health=ok`이면 **정상** 판정. URL은 loopback만 허용.
- 읽기 전용 관제 DB의 `equipment_nodes_T`, `equipment_events_T` 테이블이 존재할 경우 개별 장비 heartbeat/기록 표시. 데이터가 오래됐으면 오프라인, 기록이 없는 장비는 미확인.
- NAS/USB Z/Drive는 실제 원격 저장/해시 검증 코드가 기존 모니터 API와 연결되기 전에는 모두 **미확인**으로 표시. 게임/AI 호출/백업 결과도 출처 없는 숫자를 만들지 않습니다.

## Claude와의 인수 계약

GitHub `jijuclub-tech/court-auction-controller` 이슈 **#52**, PR #34, 이슈 #33·#37 기반.
Claude는 기능을 마무리하되 **본 화면 HTML은 ChatGPT가 소유**합니다. Claude는 기존 관제기와 연결할 **읽기 전용 JSON API / 장비 상태 증거**를 제공하고, 위 UI의 `payload()` 계약을 보존해야 합니다. 추가 필드는 하위 호환 유지.

기본 `/api/status` 예:

```json
{
 "source":"live", "generated_at":"2026-11-23T07:00:00+00:00",
 "controller":{"state":"REACHABLE_ONLY","detail":"systemd 실행, HTTP 헬스 미검증"},
 "nodes":[{"id":"BG-PC-001","label":"삼성 관제기","kind":"controller","state":"UNKNOWN","detail":"미확인","last_seen":null}],
 "storage":[{"id":"nas","label":"NAS X:","state":"UNKNOWN","detail":"원격 파일 검증 미연결"}],
 "alerts":[],
 "jobs":{"running":null,"pending":null,"completed_today":null,"failed_today":null},
 "ai":{"gemini":null,"deepseek":null,"bot":null}
}
```

**대표 확정 장애 규칙은 변경 금지**: 캡차·로봇 등 AI로 정상 처리 가능한 화면 문제는 1회 입력 실패 시 사용자 호출, 기타 일반 오류는 최대 3회 실패 시 호출. 실제 모바일 푸시·문자 지시/ACK/원격 재개는 Claude가 검수된 방식으로 통합한 뒤 별도 활성화. 기존 Chrome 원격 데스크톱을 이용합니다.

## 검수/설치 주의

- 서비스 설치/재부팅/기존 DB 스키마 수정/사용자 앱 변경/키 신규 활성화는 이 패키지로 수행되지 않았습니다.
- GitHub PR 코드만으로 운영 준비 완료를 주장하지 않습니다. 서버 `systemctl`·로컬 health·PC heartbeat·NAS/Drive 해시·푸시 수신·휴대폰 HTTPS 접근을 실제 확인해야 합니다.
- 테스트: `python3 -m unittest discover -p 'test_*.py' -v`.