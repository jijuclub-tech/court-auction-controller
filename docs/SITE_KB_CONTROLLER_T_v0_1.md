# 관제기 공통지식·사이트별 지식 통합 계약 T-v0.1

관련: controller 이슈 #40, #11, #23, #35, Jiju-01 #492.
상태: 코드·합성시험 파일 준비. 실제 Ubuntu 설치, 실수집기 수신, Galaxy Tab 검증, 기존 로컬 지식 이관은 미완료.

## 확정 아키텍처
- 기존 controller.db에 kb_reports, kb_docs, kb_acks 독립 테이블을 추가한다. 기존 수집기·Pico2·업무원장은 변경하지 않는다.
- 성공/실패 보고는 event_id 기반 추가 전용이다. 같은 내용 재전송은 200, 다른 내용으로 같은 ID를 재전송하면 409. 기존 원문을 수정/삭제하지 않는다.
- 승인 지식은 관리자가 새 revision으로 추가한다. 예전 판·해시를 보존한다.
- 지식 데이터와 증거 이미지, 계정, 키, 경매자료는 GitHub가 아닌 관제기 비공개 저장소에 둔다.
- 사이트 화면 조작 지식과 실제 주소/시설/경매 지도를 별도 원장으로 구분한다.
- 구형 Galaxy Tab 관제기 1번 화면은 사이트별 질문, 오류, 승인판, 반입, 배포 ACK를 표시한다. 화면 UI의 실제 API 연결은 후속 검증 대상이다.

## API
모든 비관리자 API는 HTTPS, 승인된 X-Collector-Name 및 X-Collector-Token을 요구한다. 봇도 개별 장비/서비스로 등록하고 키 지갑에서 개별 키를 발급한다. 관리자 엔드포인트는 X-Admin-Token을 요구한다.

- GET /kb/manifest?scope=common — 승인된 공통지식의 version 및 파일별 sha256.
- GET /kb/manifest?scope=site&site=www.courtauction.go.kr — 승인된 사이트별 지식 목록.
- GET /kb/file?scope=site&site=www.courtauction.go.kr&path=access.md&sha256=... — 지정 해시의 승인된 파일만 내려준다.
- POST /kb/report — 성공/실패 원문 및 원인·복구·시각·증거 해시 추가.
- POST /kb/ack — 개별 봇의 received/applied/failed 결과 추가.
- GET /kb/admin/status?site=... — 최신 실패·검수 대기·ACK 관리자 조회.
- POST /kb/admin/approve — 관리자 명시 승인 후 지식 새 버전 생성.

보고 필수 필드: event_id, scope(common|site), site(사이트인 경우), kind(success|failure), symptom, observed_at(타임존 포함). 권장 필드: cause, recovery, result, evidence_sha256, citation.
승인 필수 필드: scope, site, path, content, source_event. source_event가 report:이벤트ID일 때는 같은 사이트와 같은 scope의 원본 보고인지 확인한다.

지식 ID 예:
- 공통: kb:common/HTTP_403@sha256:승인문서해시
- 사이트 승인판: kb:site/www.courtauction.go.kr/access.md@sha256:승인문서해시
- 검수 전 실패 참조: kb:site/www.courtauction.go.kr/failure#row_gap_bad + 보고 event_id

승인 문서가 하나도 없으면 manifest는 ready=false, warning=NO_APPROVED_KNOWLEDGE로 응답한다. 이를 정상으로 위장하지 않는다.

## 봇 시작 규칙
1. 작업 시작 전 승인된 개별 키로 관제기 인증, 공통지식과 해당 사이트 지식 두 종류 모두 조회한다.
2. sha256로 승인 지식을 확인하고 버전을 작업 로그에 남긴다.
3. 관제 pull 실패, 구버전, 미검증, 해시 불일치 시 경고. 로그인·인증·위험 작업과 신규 수집을 정지하며 로컬 체크포인트 유지.
4. 성공·실패·오류는 원본을 덮어쓰지 않고 POST /kb/report. 인용한 knowledge_id, 노드, 시각, 화면 버전을 남긴다.
5. 수신과 실제 적용은 다르므로 POST /kb/ack에서 received와 applied를 따로 기록한다.
6. 400/403/429 등 실제 대응 지침은 기존 ops/collector-playbook/error_codes 원본과 1호 로컬 site_kb 원본을 검증하고 민감정보를 제거하여 운영자가 승인한 뒤 중앙에 이관한다. 차단 우회 로직을 새로 만들지 않는다.

## 검수 및 완료 기준
- [x] 독립 공통지식 API 모듈, 관제기 경로·DB 연결, 설치 목록 반영.
- [x] 합성 데이터 기반 테스트 파일 작성. 실제 실행은 아직 확인되지 않았다.
- [ ] python3 -m unittest -v test_site_kb_T_v0_1 실제 통과.
- [ ] 기존 test_controller, test_site_map_ingest, test_dispatch, test_collector_key_wallet_T_v0_2 전체 회귀.
- [ ] Ubuntu 운영 DB·설정·키 지갑 백업 및 복원 후 설치.
- [ ] 기존 로컬 site_kb와 common error_codes 안전한 이관 및 관리자 승인.
- [ ] 두 종류 수집기 이상이 KB pull, 해시 검증, 상태 ACK, 재사용 성공 실증.
- [ ] Galaxy Tab 가로·세로 화면과 관리자 인증/18px 이상 확인.
- [ ] NAS 및 Google Drive 실제 원격 저장/복구 SHA256 확인.

시험키 전환: KEY-UPGRADE-AT-2026-12-01T00:00+09:00. 공용 test 키를 운영 시스템에서 공개 허용하지 않는다. 등록된 장치별 키를 사용하고 구 시험키 차단을 별도 실기 검증한다.

역할: Grok은 1호 로컬 지식·공통 오류 원본, 봇의 pull/인용/보고/ACK 통합과 현장 증거 제출; Claude는 읽기 전용 회귀·보안 검수; ChatGPT는 관제기 통합·최종 검수. 이번 PR은 검수용이며 자동 병합·배포·Actions 신규 스케줄 금지.
