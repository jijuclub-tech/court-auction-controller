# 관제기 성과통계 연동·운영 준비서 — 2026-11-18

**상태: 검수용 브랜치. 운영 설치 및 현장 수집기 연동 미확인.**

## 1. 기존 기능 보존

원본은 S-0.1.2-CONTROL 개발 브랜치입니다. 기존 수집기 승인, 지시서, heartbeat, /api/collector/report, 3/4단계 호출, 학습서, 업데이트 기능을 제거하지 않습니다. 새 performance.py는 기존 SQLite에 테이블만 추가합니다. 배포 서비스는 Ubuntu의 /opt/bidguard/court-auction-controller, 데이터는 /var/lib/court-auction-controller에 둡니다. 기존 Node 관제기(port 8787)를 건드리지 않습니다.

수집기 로컬 원본은 D:\BIDGUARD\local_data에 보관합니다. 민감 데이터, PDF, 계정 키, 성과 원장 자체를 GitHub, Actions, Vercel에 올리지 않습니다. 비용이 드는 API를 사용하지 않습니다.

## 2. 1호기/보급형이 보내야 하는 성과 이벤트

POST /api/collector/performance (헤더 X-Collector-Token 필수)

**검수용 가짜 예시:**
    {
      "name": "서울1호기",
      "event_id": "fake-work-001-doc-1",
      "work_date": "2026-11-18",
      "court": "서울중앙",
      "document_type": "감정평가서",
      "computer": "jijuclub5",
      "outcome": "success",
      "verification_status": "VERIFIED",
      "case_no": "2025타경12345",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "duration_seconds": 21
    }

- outcome은 success / failure / duplicate 중 하나. 성공은 실제 문서 귀속 검증 후 VERIFIED와 사건번호·원본 SHA가 있어야만 승인. 피코 마우스 클릭 ACK 또는 다운로드 버튼 클릭을 문서 성공으로 세지 않습니다.
- event_id는 수집기별 자료 시도 고유 ID. 같은 ID·같은 내용 재전송은 ACK하되 통계는 재집계하지 않음. 동일 ID에 다른 내용은 오류.
- duplicate(원본 자료 중복 건너뜀)와 duplicate_delivery(네트워크 재전송)는 서로 다른 개념.
- 서버는 작업 결과가 도착한 시점에 원장과 일일 집계표를 같은 트랜잭션으로 갱신. 조회할 때 폴더 파일 재집계 안 함.
- 1호기 기존 heartbeat의 power_event 등은 그대로 보존. 본 API를 쓰도록 1호기만 추가 연동하는 작업은 아직 남아 있음.
- 기존 수집기 승인 외에 신규 통계 API용 수집기 토큰 설정 필수. 서버에는 비밀번호/키를 기록하지 않음.

## 3. 전원·장애 이벤트

POST /api/collector/incident, 동일한 승인 토큰 사용. 예시:
    {"name":"서울1호기","event_id":"fake-power-001","code":"POWER_DISCONNECTED","computer":"jijuclub5","battery_percent":87}

허용 코드:
- POWER_DISCONNECTED, BATTERY_BELOW_20, BATTERY_CRITICAL, POWER_RESTORED
- LOGIN_LOST, CAPTCHA, ROBOT_CHECK, UNEXPECTED_VIEWER
- NETWORK_SLOW, NETWORK_LOST, BREAK_STARTED, BREAK_ENDED

전원 해제 즉시 감지, 20% 미만 경고, 5% 이하 작업 중단, 충전 복귀 후 재시작 판단은 수집기 담당입니다. 관제기는 이벤트 저장·표시와 NOTIFY_URL이 유효할 때만 알림 시도. NOTIFY_URL 미설정이면 푸시 알림이 없으며, 신규 API 연동 이전에는 이 사건들이 자동 수집되지 않습니다. 사이트 보안 확인은 우회하지 않고 기록·관리인 인계합니다.

UNEXPECTED_VIEWER는 수집기가 감지했을 때만 기록합니다. 관제기 자체가 원격 PC 화면을 감지하는 기능은 아닙니다.

## 4. 조회·사용자 질문

GET /api/admin/performance?period=day|week|month|year&date=YYYY-MM-DD

day=선택일, week=끝 날짜 포함 7일, month=해당 월 1일부터 기준일까지, year=해당 해 1월 1일부터 기준일까지. court, document_type, collector, computer로 선택 필터를 추가할 수 있습니다. 관리자 토큰이 필요합니다.

관제기 관리자 화면의 [성과 통계] 탭은 기준 날짜, 기간, 법원·자료종류 필터, 일별 막대/선 그래프, 종류별/법원별 합계를 보여줍니다. 수집 보고가 한 건도 없으면 “보고 없음”으로 표시하여 0건과 구분합니다. “오늘 감정평가서 몇 개 받았지?” 질문은 로컬 입력에서 오늘/어제·종류를 인식하고 저장된 집계표만 조회합니다. 원문 질문은 서버로 보내지 않습니다. 질문 주제 코드(daily_count, document_count 등)의 횟수만 누적합니다.

GET /api/admin/performance/incidents 는 최근 사건 50건 조회.
GET /api/admin/performance/questions 는 질문 코드별 빈도 조회.

## 5. 일일 마감/별도 검산

관제기 기동 시와 10분마다, 완료된 KST 날짜 중 실적이 수신된 날을 자동 마감합니다. POST /api/admin/performance/close 로 이전 날짜 수동 마감도 됩니다. 마감 당시의 스냅샷은 보존하고, 늦게 온 수집건은 지연 입력으로 별도 표시하면서 누적 집계에 포함합니다.

독립 파일 검산 자체는 **미구현**입니다. 현재는 별도 검사에서 생성한 건수를 받아 저장된 성공건수와 비교하는 API만 제공:
POST /api/admin/performance/reconcile
    {"work_date":"2026-11-18","court":"서울중앙","document_type":"감정평가서","collector":"서울1호기","computer":"jijuclub5","external_count":122}

차이 발생 시 difference와 matched=false 반환·검산 이력 저장. 실제 파일의 숫자를 검증했다고 오인하지 않도록 별도 대조기가 필요합니다.

## 6. 다음 연동 작업 (미구현이므로 완료 처리 금지)

1. 글록 1호기에서 검증 완료/실패/중복 결과 전송, 로컬 outbox 재전송, 전원 및 로그인/캡차/비정상 뷰어 이벤트 전송. **1호기만 수정하고 보급형 버전·API·DB 경로 호환을 유지**할 것.
2. 낙찰(매각) 물건의 감정평가서 선순위 수집 정책은 실제 법원 열람기간과 문서 귀속을 확인한 후 관제 지시서에 반영.
3. 1시간 작업 후 5~10분 휴식 정책 및 BREAK_STARTED/ENDED 실이력은 수집기 실행부 연동 필요.
4. 외부 통신 속도와 연결 이상은 실제 수집기/관제기의 측정 결과를 이벤트화해야 함.
5. 원본 파일/매니페스트와 독립 대조하는 검산기 구현 및 불일치 푸시 경고.
6. Windows+Pico2+법원 실제 작업과 Ubuntu 관제기 설치 및 모바일 화면 시험.

## 7. 오프라인 시험 및 설치 체크

개발 검수: python3 -m unittest test_performance -v
기존 회귀: python3 -m unittest test_controller -v

수집기 토큰 인증/미인증, VERIFIED 증거 필수, 이벤트 중복 재전송, 다른 내용의 재전송 거부, 실패·자료 중복, KST 날짜 마감, 지연 도착, 외부 검산 차이, 기존 보고 API 호환을 확인합니다. **현재 테스트 파일만 작성했고 실제 PASS 결과를 확인하지 못했습니다.** GitHub Actions 실행하지 않습니다.

설치기 install/install_controller.sh에 performance.py 복사를 포함시켰습니다. 실제 설치 전에 운영 SQLite 데이터, 데이터 폴더, 환경파일을 분리 보관하고 체크섬/복구 절차를 확인합니다. systemd 서비스는 court-auction-controller, 기존 Node 관제기는 별개. 환경파일에는 실제 비밀정보가 있으므로 공개 저장소 업로드 금지. 운영 브랜치 병합 또는 재배포는 검수 증거와 관리자 승인 후 진행합니다.
