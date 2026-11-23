# BIDGUARD 수집자료 통합 원장 및 저장 경로 설계 — T v1.0.0
기준: 이슈 #55 (2026-11-24 01:10 KST), 관련 #26, #29, #47, #50, PR #51. **상태: 구현 지침 / 운영 이관 미실시**.

## 0. 정책 결정
1. **관제기는 유일한 논리적 접수·색인·작업관리 주체**, NAS2e는 검증 후 확정할 **원본 바이너리 저장소**, Google Drive는 암호화된 외부 복구 사본. GitHub/박스 공용 폴더는 코드·요약·사본용이며 원본 아님.
2. 현재 확인된 장치 불일치: 3호 X: = NAS2e `X:\VOL1\Home.bidguard\...`(포트 19641 불통), Z: = **다른 장치** `Z:\HDD1\BIDGUARD\...`(FTP 21), 1호 X: = `X:\HDD1\...`(오설정 위험). **Z:의 SHA 성공을 NAS2e 원본 확인으로 보고하지 않는다.**
3. 표준 경로를 Windows 드라이브 문자로 고정하지 않는다. 관제기에 `storage_target_id`, `volume_uuid` 또는 NAS 공유 폴더 식별자, `canonical_root`, `transport`, `endpoint`, `health`를 등록한다. 모든 수집기는 중앙에서 내려받은 **논리적 URI** `bg://court/cases/{court_code}/{year}/{case_no}/{item_no}/`로 업로드한다.
4. **NAS2e 실제 공유 루트는 확인 전 'UNVERIFIED'**. 검증 전 `/volume1/BIDGUARD` 등 임의의 물리 주소를 실사용 설정에 쓰지 않는다. NAS2e가 복구되면 전용 공유 디렉터리 `BIDGUARD/warehouse`를 관리자 확인 후 배정한다. Z:는 임시 staging으로만 취급하며 NAS의 대체 원본으로 자동 승격 금지.
5. 데이터 저장은 절대로 GitHub에 하지 않는다. 법원/유료사이트 데이터 수집 및 외부 제공은 출처의 이용 조건과 라이선스를 따른다. 공개 목록 메타데이터와 개인정보 포함 원본을 구분한다.

## 1. 수집·보관 경로
```
COLLECTOR (local durable outbox; no loss)
 -> CONTROLLER ingest HTTPS (device scoped token, idempotency key, stream, SHA256, receipt)
 -> controller SQLite intake ledger + temporary staging
 -> NAS2e verified canonical object store (immutable object)
 -> controller SQLite catalog commit (NAS verify ACK)
 -> encrypted Google Drive backup (verify remote hash/manifest)
 -> background indexing / OCR / AI-derived layers
```
- 기존 PR #51에서 요구한 NAS 직접 DDNS `/upload` 정책과 관제 경유 정책을 무조건 동일시하지 않는다. 수집기 업로드 목적지는 **관제 ingest 우선**; 관제 장애 시 NAS 직접 업로드는 별도 승인된 서비스 자격·단일 원장 재동기화 규격이 있을 때만 허용한다. 그 전에는 로컬 outbox에 대기.
- 수집기는 검증 완료까지 로컬 파일을 유지. 관제 서버의 **접수 ACK**와 NAS의 **영구보관 ACK**를 분리하고, 로컬 파일 자동정리는 NAS 확인+Drive 정책 충족+보존 유예기간 충족 후에만.
- 상태: `DISCOVERED -> LOCAL_DURABLE -> RECEIVED -> NAS_VERIFIED -> DRIVE_VERIFIED -> INDEXED`; 실패는 `RETRY_WAIT`, 손상은 `QUARANTINE`. 어느 단계도 전 단계의 검증 없이 성공 표시 금지.
- 로컬 장애 시: 디스크 사용률 80%에서 신규 수집 정지, 50% 아래로 돌아온 후 재개(기존 운영 기준), 데이터 삭제 금지. 관제 교신 단절 시 신규 수집 정지, 적체·마지막 성공·누락 건수를 알림. 이미 완료된 파일은 안전하게 큐에 보존.
- 수집기 #55 정지/재개 상태는 저장 개선만으로 변경하지 않는다. 3호 외 정지 지시 및 09 예외를 그대로 준수.

## 2. NAS 폴더 구조 (논리 규칙; 물리 최종 확정은 NAS 장치 확인 후)
```
BIDGUARD/warehouse/
  objects/sha256/ab/cd/<64hex>             # 파일 바이트 불변; 중복 저장 방지
  manifests/YYYY/MM/DD/<batch-id>.json    # 수집/검증/출처/버전
  exports/court/<court_code>/<year>/<case_no>/<item_no>/
      appraisal/<document_id>.pdf
      auction_description/<document_id>.pdf
      photos/<photo_id>.<ext>
  exports/market/<source>/<YYYY-MM-DD>/
  exports/map/<source>/<YYYY-MM-DD>/
  exports/media/<source>/<YYYY-MM-DD>/
  backups/controller-db/YYYY/MM/DD/
  quarantine/
```
- exports는 사람이 탐색하는 **링크 또는 인덱스 기반 뷰**; 객체 파일의 원본 바이트는 objects에 하나만. NAS의 파일시스템 하드링크 지원을 전제하지 말 것.
- 사건키는 최소 `court_code + year + case_no + item_no`로 구성. 사건번호 단독 키 금지. 동일 사건 재다운로드는 버전으로 연결. 원본 overwrite 금지.
- 사진은 법원 기본 미리보기의 출처·시각·해시·해상도, 감정평가서 사진은 문서와 페이지 및 추출 버전을 따로 연결. 감정가격과 낙찰가격은 다른 타입·기준일로 저장.
- AI OCR/해석 결과는 `derived` 계층 및 JSON 규격으로 저장하며 원본과 섞지 않는다. AI가 원본·검증 DB 원장을 직접 수정하지 않는다.

## 3. SQLite 관제 카탈로그 (기존 테이블 탐색 후 마이그레이션)
- `storage_targets(id, device_identity, canonical_root, transport, verified_at, health, read_only)`
- `collectors(id, machine_id, permissions, heartbeat_at, task_state)`
- `ingest_jobs(job_id, collector_id, logical_uri, idempotency_key UNIQUE, status, retries, received_at, nas_verified_at, drive_verified_at)`
- `objects(sha256 PRIMARY KEY, size, mime, storage_target_id, storage_key, verified_at, retention_class)`
- `source_documents(document_id, court_code, case_year, case_number, item_no, type, source_url, collected_at, source_published_at, source_ref, sha256, version, supersedes_id, visibility, license_class)`
- `auction_values(event_id, price_kind, amount_krw, value_date, source_document_id, verified_at)` (`appraisal`, `minimum_bid`, `winning_bid`, `transaction` 등)
- `backup_receipts(object_sha256, destination, remote_ref, verified_at, result)`
- `audit_log(actor, action, object_sha256, timestamp, evidence_ref)`
- `collector_fallbacks(collector_id, local_path_ref, first_seen_at, bytes_pending, last_error)`
- 운영 DB에 파일 자체(PDF/사진)를 BLOB으로 넣지 않는다. 암호/키는 #47 관제 금고로만 관리.
- SQLite 변경 전 백업, `PRAGMA integrity_check`, WAL/동시성/재시작 테스트 필수. 기존 `control_center.db` 경로와 스키마는 현장 확인 후 migration. 이 문서만으로 운영 DB가 변경된 것이 아니다.

## 4. 파일 접수 계약
요청 메타데이터 필수: `collector_id, job_id, idempotency_key, court_code, case_year, case_number, item_no, document_type, source_url, collected_at, file_size, sha256, content_type`. 업로드 과정과 완료 상태를 서버 로그에 보존하되 비밀값·개인정보 원문은 기록 금지.
1. `POST /api/ingest/sessions`: 작업 승인/업로드 토큰(짧은 수명) 및 blob 중복 여부 응답.
2. `PUT /api/ingest/sessions/{id}/content`: 길이 제한 스트리밍·임시파일 fsync·SHA 검증. 네트워크 단절은 재시도·resume 또는 전체 재시도.
3. `POST /api/ingest/sessions/{id}/commit`: 관제 RECEIVED ACK (NAS 확인 아님), NAS 적재 worker.
4. `GET /api/ingest/jobs/{job_id}`: 상태와 `nas_verified`, `drive_verified`, 오류 정보 반환.
5. 서버는 같은 idempotency_key + 같은 해시에 기존 영수증 반환; 키는 같은데 다른 해시면 409+QUARANTINE. 재전송 두 번으로 객체·장부 중복 생성 금지.
6. HTTPS, 인증/권한, 전송 크기/형식 제한, PDF 파일 서명 확인, 경로 순회 방지, 악성 파일 격리. 공개 포트 21의 평문 FTP에 민감자료·인증정보 전송 금지.

## 5. 백업·보존·복구
- NAS 원본 확인 후 Drive에 암호화·증분 백업. 일별 백업 매니페스트에 총 파일 수·바이트·해시·실패·지난 정상 시각 기재. **Drive API 성공 응답만으로 검증 완료 처리하지 않음**.
- 별도로 관제 SQLite 일관된 스냅샷 백업 및 복원 테스트; 운영 SQLite 파일을 실행 중 직접 복사하지 않는다. 복구용 매니페스트·키 복구 절차는 분리 보관.
- 감정평가서 PDF·사건 핵심 사진·실제 낙찰가/예측값·출처 및 버전 이력은 정책 승인 전 자동 삭제 금지. 화면증거는 일반 정상/오류/법적 증거 등 분류, #29 7일/30일/영구/1년은 **기존 제안**이며 승인된 데이터별 보존정책 및 법적 검토 후 시행.
- 기존 620MB 포함 어떤 과거 자료도 삭제·이동 전 수량, SHA 매니페스트, 백업 확인, 대표 승인 필요.
- 백업복구 검수: 임의 10건 + PDF 1건 + 사진 1건 + DB 스냅샷을 복원하고 원본 SHA·사건연결·낙찰가 필드를 비교한다.

## 6. 이행 순서 및 승인 게이트
P0 (즉시, 무중단): 모든 수집기·NAS·Z·Drive의 경로/장치 ID/용량/파일 건수/최종수정일/해시를 **읽기 전용**으로 수집, 조사 보고. 비밀번호 화면 노출 사고의 관련 기록 접근 제한 및 대표에게 교체 판단 보고.
P1 (우선): NAS2e X: 연결 진단, Z: 실제 장치 식별, 1호 X: 경로 오설정 제거. 관제 등록 `storage_targets` 정상화; 확정 전 복사/삭제 금지.
P2: 관제 ingest·SQLite 대기열·해시/영수증·중복방지 구현, #50 PR #51와 충돌 해소. 3호 테스트 파일 1건으로 관제→NAS 수신/원본 검증; 운영 수집 루틴 변경 없음.
P3: Google Drive 백업/검증/복원 실험; 출처별 retention, 사진/낙찰가 우선 색인 구현; 모바일 관제 대시보드의 파일 건수·누락·최근 NAS/Drive 성공표시.
P4: 1호/2호/09 이관은 별도의 재개 승인과 실기 검수 후 순차 확대; 60개 봇의 공통 프로그램 교체 여부는 별도 검토.
**완료 조건**: NAS 물리 루트 검증, 단일 사건 중복 업로드 0, 파일 SHA 일치, 관제 DB 검색, Drive 검증/복원, 장애 시 로컬 보존, 기존 기기 기능회귀, 보안 승인. 운영 자동 전환·기존 파일 삭제·키 교체·정지 수집기 재개를 이 문서로 승인한 것이 아님.
