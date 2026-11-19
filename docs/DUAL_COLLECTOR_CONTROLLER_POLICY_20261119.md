# BIDGUARD 관제기: 2대 수집기 중앙 작업 원장 및 배분 규칙 (P0)
기준: 2026-11-19 KST | 상태: **정책·수락기준 확정 / 실서버 적용 및 현장 검증 미완료**

## 0. 이번에 해결할 단 하나의 문제
**2대 수집기가 수집을 동시에 계속하더라도 하나의 사건·물건·문서 작업을 중복 배정하거나 잃지 않고, 양쪽 결과를 관제기 단일 원장에 검증된 상태로 누적한다.** 기존 UI/AI 손끝/Pico2/F8/성과·지도·학습서 기능은 건드리지 않는다.

장비 담당은 사용자의 기존 운용 지시 기준으로 `서울 1호기`와 `제주 시작 2호기`를 초기 배정한다. 단, **실제 장치 ID·작업 구역·작동 여부는 등록/heartbeat/수신 증거로 확인**해야 하며 GitHub PR 또는 사용자 보고만으로 운영 정상 상태를 단정하지 않는다. 작업 배분은 '수집기는 집행, 관제기는 단일 지휘·원장·검증·재배정'으로 정한다.

## 1. 현재 GitHub 소스에서 확인한 미완료 지점
- `controller.py`의 `instructions`는 수집기별 개별 큐이고 사건별 전역 잠금/작업 원장 없음.
- `GET /api/collector/instruction/{name}`에서 `pending -> sent`로 즉시 변경. **수신 ACK·lease 갱신·만료 뒤 회수·동일 사건 다른 수집기 할당 차단이 없음**. 응답 유실 시 지시가 사라진 것처럼 보일 수 있다.
- 기존 `POST /api/collector/report`는 개별 보고를 INSERT하며 독립 event ID/중복 전송 멱등키가 없다. 성공 보고만으로 다운로드 파일 무결성이 증명되지 않는다.
- `_collector_ok()`는 collector의 `token_hash`가 비어 있으면 토큰 없이도 승인 상태 통과 가능. 실운영 2대는 **등록 장치별 서로 다른 필수 인증 토큰**을 적용해야 한다.
- `standalone_collector/control/controller_sync.py`는 기본 120초마다 health/heartbeat/update를 동기화하며, 관제기 연결 실패 시 기존 수집을 계속한다. **새 중앙배정 작업 시작**과 **이미 승인된 작업 마무리·로컬 보관**은 다르게 취급해야 한다.
- 기존 `performance.py`, `site_map_ingest.py`, `learning.py`, `key_wallet.py`는 재사용. 중복 구현하거나 기존 DB를 갈아엎지 않는다.

## 2. 중앙 업무 원장 (관제기 SSD SQLite, NAS 공유 DB 금지)
핵심 테이블은 기존 `collectors`, `instructions`, `reports`를 파괴적으로 변경하지 않고 새 마이그레이션으로 확장한다.

- `collector_nodes`: 불변 `collector_id`, 인증 키 식별자, 호스트/버전/기능, 지역/법원 선호, 마지막 heartbeat, ONLINE/DEGRADED/OFFLINE/PAUSED, 마지막 성공시각.
- `work_items`: `job_id`, `work_key`, source_id, court_id, case_number, item_number, document_type, document_version, priority, region/court, discovered_at, next_check_at, status, assignee, lease_epoch, lease_until, created_at/updated_at.
- `work_events` append-only: `event_id` UNIQUE, `job_id`, `collector_id`, `lease_epoch`, at, kind, outcome, verification_status, manifest_sha256, local/NAS status, error_code. 원본/자격정보·개인정보·전체 PDF는 DB 로그에 넣지 않음.
- `file_manifest`: 수집기별 상대 경로·크기·SHA256·사건/물건 귀속·직접 다운로드 증거·관제 검증·NAS/Drive 검증 상태. 원본 파일은 GitHub/Actions/Vercel 금지.
- `dispatch_audit`: 예약/배정/재배정/보류/관리인 개입/충돌 거부에 대한 불변 감사 이력.

**work_key**는 적어도 `source_id+court_id+case_number+item_number+document_type+document_version`을 포함한다. 사건번호만으로 다른 물건이나 문서까지 합치지 않는다. 아직 정확한 문서 버전을 알 수 없는 탐색 단계는 `court+page/range+session` 범위를 작업 키로 예약하고 발견 시 문서 키로 분화한다. UNIQUE 및 트랜잭션으로 원자적 배정.

## 3. 분산 집행 계약
1. **지휘**: 관제기는 매 수집기의 현재 처리 범위·역량·최근 성공/장애를 보고 다음 할 일을 선택한다. 초기 서울/제주를 분리하되 실제 진행/잔여 법원 범위에 따라 변경 가능. 낙찰된 물건의 감정평가서 확보가 가능한 경우 우선 순위 지정. 1시간마다 작업 결과를 점검하고 다음 묶음을 조정한다. heartbeat는 기존 120초 로직과 호환하되 설정 가능. 1시간마다만 접속하는 설계가 아니다.
2. **배정**: `POST /api/collector/jobs/claim`(제안 경로)에서 서버가 승인된 장치 한 곳에만 `job_id+lease_epoch+lease_until` 부여. 한 DB 트랜잭션에서 중복 배정 금지. 서버의 현재시각 기준. 장치별 토큰 필수, TLS. HTTP GET 읽기만으로 작업 상태 바꾸지 않는다.
3. **수신 ACK**: 작업 수신 직후 명시적 `ACK`. 수신 ACK가 없으면 대기/재전송 가능. 진행 중 heartbeat로 lease 연장. 완료/실패 보고는 event_id에 대해 멱등 처리. 동일 event_id+다른 내용은 409 충돌. 승인되지 않은 장치/lease_epoch 불일치는 거부·격리한다.
4. **장애**: 통신 단절 시 이미 받은 작업은 안전한 단계에서 끝내고 로컬 manifest/outbox에 기록한다. **새 중앙 작업은 임대가 없으면 시작 금지.** lease 만료 즉시 타 기기에 재배정하지 않고 기존 장치의 중단·미확인 진행 상태와 증거를 확인해 회수한다(fencing/epoch). 장치가 늦게 돌아와 내는 결과는 이전 epoch로 별도 격리해 파일 보존하되 공식 완료 덮어쓰기 금지.
5. **완료와 저장**: 수집 성공은 클릭 성공이 아니라 문서/사건 귀속+파일 해시 확인. 이때 문서 종류별 적법한 획득 출처와 직접 다운로드 여부 구분. 최신 사용자 저장 지시(#21): 수집기는 **실제 마운트 확인된 NAS에 우선 저장**, 실패하면 자체 HDD/SSD에 원본+manifest+실패시각을 안전 저장하고 복구 뒤 누락분만 NAS에 복제·SHA 검증. 다만 **NAS 미마운트 일반 로컬 디렉터리를 NAS로 오판 금지**, NAS가 원장 SQLite 대체 아님. 기존 인증·저장 정책과 실제 권한 검증 전 경로 고정 금지.
6. **관제기 원장 동기화**: 양쪽 `manifest`와 검증된 사건 상태를 수신하고 사건/물건/문서 버전별 하나의 유효 결과로 통합; 두 노드가 같은 파일을 얻어도 별도 성과 중복계상 금지. 정상 중복 발견은 이벤트로 남김. NAS 파일 + 관제 메타 원장 불일치는 PENDING_VERIFY로 유지.
7. **학습/지도 배포**: 이미 검증된 사이트 화면 지도·경험·규칙은 관제기에서 버전 고정 배포. 어느 한 장치의 실패/성공이 다른 장치와 기존 데이터에 무검증 자동 덮어쓰기 되지 않도록 한다. 사이트 보안제한, 로그인, CAPTCHA는 우회하지 않고 관리자 확인.

## 4. 운영 관제 화면 (현재 화면에 증분 추가)
각 수집기마다 노드 ID, 배정 법원/범위, 버전, 마지막 심장박동, 현재 작업 ID·사건번호, 남은 대기 건, 오늘 검증 성공/중복/실패, 로컬 대기 업로드, NAS 검증, Drive 추가 백업, 오류 및 관리자 호출을 보여 준다. 합계와 각 장치 실적을 동시에 표시한다. 보고 없음은 '0건 완료'가 아니다.

## 5. 수락 기준 — **두 대 실측 전 완료 금지**
- 승인된 두 서로 다른 장치로 동일 work_key 동시 claim 100회: 정확히 한 곳만 lease 획득; 나머지 no-work/409. 신원 없는 요청 401/403.
- ACK 전 연결 끊김, HTTP 성공 응답 유실, heartbeat 중단/재개, 재기동 후 작업 유지, 동일 event_id 재전송, 이전 epoch 늦은 결과: 누락·중복·이중 완료 0.
- 1호기 서울, 2호기 제주 출발 정책으로 각 1건 이상 실제 작업 보고, 관리자가 다시 배분 가능. 수집기 수동 모드·기존 관제형 UI/F8/AI 단계·1호기 프로그램 회귀 없음.
- 동일 사건의 다른 물건번호, 감정평가서와 매각물건명세서, 발급시각이 다른 등기자료는 충돌 없이 구분.
- NAS 단절 중 로컬 저장→복구 뒤 NAS SHA-256 확인→관제 DB 반영→Drive 추가 백업 상태 갱신. 백업 미확인 때 성공 표시·로컬 삭제 금지.
- 실제 Windows/Pico2 두 대+Ubuntu 관제기에서 수신, DB 건수, 현장 파일 SHA, 장애 복구 증빙. 실측 전엔 `NOT_INSTALLED / FIELD_PENDING` 표시.

## 6. 이 한 건의 구현 순서
1. 관제기 기존 controller.py의 위험한 지시 `sent` 처리부터 수정: ACK·lease·원자 배분·event 중복 방지. 먼저 합성 2노드 오프라인 동시성 테스트.
2. 기존 `controller_sync.py` 및 1호기 송신부를 **기존 기능 유지** 방식으로 adapter 연결; 기기별 ID/인증, outbox/ACK, 1시간 재배분 체크.
3. 운영 SSD/NAS/Drive 경로와 인증을 현장 설정하고 2대 각 1건 실송신 검증. 사용자에게 명령어 복사 요청보다 실제 작업 수행 주체의 점검 보고 우선.

관계 이슈: controller #8, #9, #11 / collector-new #20, #21 / Jiju-01 #399. 본 문서는 코드/지시 계약이지 원격 실서버 설치 완료 증거가 아니다.
