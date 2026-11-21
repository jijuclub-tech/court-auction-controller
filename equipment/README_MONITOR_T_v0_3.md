# BIDGUARD 삼성 태블릿 관제 — T v0.3 운영 지침

## 상태: 코드 구현 PR / 운영 설치 및 원격 실증은 미완료
기존 관제기 /admin 화면의 장비 관제 탭, 인증된 PC heartbeat, 30초 서버 감시 루프, 장애/복구 이벤트, 알림 재시도 및 선택적 외부 watchdog을 별도 코드로 추가했다. 기존 수집 지시·HID/F8·성과 DB·원본 자료는 삭제/변경하지 않았다. 설치 전 기존 controller.db/설정/설치본 백업 및 회귀 검증 필수.

## 운영 방식
- 삼성 Galaxy Tab은 관제 화면. Ubuntu systemd 서비스가 실제 관제를 수행하며 컴퓨터 화면을 따로 켤 필요는 없다. 기기 자체 전원이 끊기면 원격 관리도 불가능하며 별도 UPS/원격 전원 관리가 필요하다.
- 서버 루프 30초, NAS 연속 2회 실패 시 장애 등록, 복구 시 회복 이벤트. 장비별 인증 heartbeat 기본 60초, 180초 이상 미보고(이전 실제 신호가 있는 장비) OFFLINE. 최초 미등록/미수신은 UNKNOWN.
- PC 센서: RAM/디스크/배터리, 선택적 서비스 상태. 디스크 2GB 미만·배터리 15% 미만·메모리/CPU 95% 이상(값을 전송한 경우만) DEGRADED. CPU 값은 기본 에이전트에서 전송하지 않는 경우 미보고로 표시한다.
- NAS는 TCP 445 통과만으로 성공이 아니다. 관제 서버에 실제 원격 CIFS/NFS 마운트 후 파일 쓰기/읽기/삭제 실검사가 필요하다. 가짜 로컬 X: 드라이브를 성공으로 취급하지 않는다.
- NAS/Drive 원본 백업 완료 여부는 각각 실제 파일과 SHA-256 검증, 복원 시험 전까지 미검증.
- 사용자 직접 확인: 2026-11-21 NAS 현재 작동. 관제 서버의 확인은 설치 및 실연결 전 미확인.
- 서버 알림은 환경설정 NOTIFY_URL의 웹훅에 HTTP 전송하고 응답 결과를 DB에 기록한다. URL 미설정이면 탭 화면의 경고만 나타나므로 탭이 닫힌 상태에서 푸시 보장 불가. 실제 모바일 알림 수신 시험 필요.
- 장비 자체가 중단되는 경우는 같은 서버에서 탐지 불가하므로 외부 상시 PC에 equipment/external_watchdog_T_v0_3.py를 별도 기동한다.

## 태블릿 접속과 인증
1. Ubuntu 관제 서비스는 부팅 자동 실행, 중단 시 자동 재시작되는 기존 systemd 구성 이용. 코드 PR 검수 승인 후 설치.
2. 삼성 탭은 HTTPS 및 Tailscale/인증 프록시 등 안전한 경로로 관제기 /admin에 접속. 공용 인터넷에 무인증 관리 포트를 직접 공개하지 않는다.
3. 장비 관제 → 장비 연결 등록: 자산 ID와 이름 입력, 랜덤 토큰 1회 표시. 해당 PC에만 보안 설정으로 전달하고 토큰을 GitHub·채팅·일반 로그에 남기지 않는다. 최초 등록만으로 ONLINE이 되는 것은 아니다. 서비스 검증 없는 인증 heartbeat는 REACHABLE_ONLY(통신만 확인)이며, 승인된 로컬 서비스 헬스가 RUNNING이어야 PC를 ONLINE으로 표시한다.
4. 각 Windows/Ubuntu PC에 equipment/equipment_agent_T_v0_3.py를 설치하고 작업 스케줄러 또는 서비스로 자동 실행한다. 장치별 환경값 EQUIPMENT_NODE_ID, EQUIPMENT_TOKEN, EQUIPMENT_CONTROLLER_URL=https://관제서버를 설정한다. 선택적으로 EQUIPMENT_LOCAL_HEALTH_URL=http://127.0.0.1:포트/health 로 로컬 서비스 실제 응답도 확인한다.
5. 관제 서버 환경변수 CONTROLLER_MONITOR_TARGETS_FILE에 NAS 장비 프로브용 비공개 JSON 파일 경로 지정. 예시(아래 값은 반드시 실경로로 교체):

~~~json
[
  {
    "node_id": "BG-ST-001",
    "kind": "nas",
    "host": "NAS_PRIVATE_HOST",
    "port": 445,
    "mount_path": "/VERIFIED/REMOTE/NAS/MOUNT"
  }
]
~~~

6. 관리인이 사용하는 NOTIFY_URL에는 운영 PWA 모바일 알림 수신 웹훅을 연결하고, 테스트 장애 1건과 복구 1건을 발송·실수신 확인한다. HTTP 성공만으로 기기에서 푸시가 표시되었다고 단정하지 않는다.
7. 독립 PC에 CONTROLLER_HEALTH_URL(HTTPS), NOTIFY_URL(HTTPS)을 지정한 외부 watchdog 설치. 해당 PC가 꺼지면 관제기 중단 통지 기능도 동작하지 않을 수 있다.

## 필수 현장 시험
- 실 PC 두 대 이상 인증 heartbeat, 3분 이상 보고 끊김 -> OFFLINE -> 회복 시 ONLINE, 관리자 탭 시각 및 알림 이력 대조.
- NAS 실제 원격 마운트·쓰기/읽기 정상 -> ONLINE. 2회 연결 실패 -> 알림. 연결 회복 -> 복구 알림. 로컬 가짜 X: 또는 마운트 해제 상황에서 성공 오판 0.
- 로컬 원본 보존, NAS 장애 시 Drive 백업, NAS 복구 후 해시 검증 이관은 저장 우회 어댑터와 Drive 업로더 결합 후 파일 1건 실증해야 한다. 현재 별도 어댑터 PR 초안으로 인수 시험 전.
- 관제기 자체 종료는 외부 watchdog으로 검출. 삼성 탭 브라우저를 닫았을 때도 휴대폰 알림 실제 도착 검증.
- 보안: HTTPS 없이 토큰 교환 거부, 다른 PC 토큰 전송 거부, 운영 DB/수집기/기존 Node 서비스 유지, 문제 시 즉시 롤백.

## 비용
표준 라이브러리와 기존 SQLite 기반이므로 새 유료 AI API 호출 없음. 원격 푸시 제공자 비용은 별도 확인. GitHub Actions를 주기 감시로 사용하지 않는다.
