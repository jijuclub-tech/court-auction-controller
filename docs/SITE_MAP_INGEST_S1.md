# S-1.0 사이트 화면 지도 → 관제기 수신/백업 (2026-11-18)

**상태: 개발 브랜치 / 실장비 설치 미검증 / 실제 배치 송신 0건 보고.** 관련 이슈: #11. 위치·경매 핀 지도와는 별도의 **사이트 화면 조작 지도**이다.

## 보존 및 보안
- 원본은 작성자 PC의 /workspace/map-data/ 에 그대로 보관. 데이터·키·스크린샷은 GitHub/Actions/Vercel에 저장하지 않는다.
- 서버의 검증된 로컬 SSD (설치 기준 /var/lib/court-auction-controller/site-map-batches/)에 먼저 저장. 기존 관제 DB는 새 독립 테이블만 추가.
- NAS는 실제 mountpoint 검증 뒤 SHA-256 대조하고, Drive는 별도의 미리 구성된 rclone 원격에 백업 후 rclone check --download 검증.
- ACK는 **SSD 영속 저장 성공**만 뜻함. NAS 및 Drive 백업이 끝났다는 뜻이 아니며 별도 backup_status로 관리한다.
- 작성자 로컬 원본 삭제 금지. 기존의 성과통계, 학습서, AI 3/4단계, Pico2 및 수집기 기존 API 변경 금지.

## API (임시 어댑터 계약 v1)
- POST /api/v1/site-maps/batches (HTTPS/TLS; X-Map-Token 전용 환경변수 인증).
- GET /api/v1/site-maps/latest?site_id=...&name=... (승인+토큰 설정된 수집기만; X-Collector-Token, HTTPS). with_evidence=1로 이미지 bytes 포함 가능.
- GET /api/admin/site-maps/state (기존 X-Admin-Token 인증; 최근 30건 및 백업 상태).
- POST 응답 201 새 수신, 200 기존 동일 재전송, 409 동일 batch_id 내용 충돌, 403 보안 거부, 413 용량 초과, 422 스키마/해시 불일치.
- TLS 종료 프록시는 loopback(127.0.0.1 또는 ::1)에서 컨트롤러로 프록시하며 X-Forwarded-Proto:https 설정. 직접 외부 HTTP는 수신 거부. CONTROLLER_MAP_ALLOW_HTTP_LOOPBACK=1은 로컬 테스트에만 쓸 수 있으며 운영에서는 금지.

서버 env: CONTROLLER_MAP_INGEST_TOKEN=충분히 긴 랜덤값(24자 이상; 비밀을 채팅/GitHub에 올리지 말 것). 선택적으로 CONTROLLER_SITE_MAP_DIR=로컬SSD위치. 관리자 비밀 env 파일은 기존 /etc/court-auction-controller/controller.env (root only).

수신 envelope 필드: protocol (bidguard.site-map-batch.v1), site_id, batch_id, map_version, captured_at (tz-aware ISO-8601), source_url (http/https), payload (JSON object), payload_sha256 (hex SHA-256), evidence (list).
payload_sha256 해시는 UTF-8 JSON serialization with sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False의 SHA256이다. 첨부 evidence는 path 상대경로, sha256, content_b64(JPEG/PNG/WebP bytes). 최대 payload 1 MiB, evidence 8개/각 512 KiB, JSON 전체 6 MiB. 경로 탈출/비밀키 필드/해시 불일치 거부. 두 번째 전송은 동일 manifest SHA 기준 중복 방지.

**작성자의 실제 batches 파일 형식과 해시 계산법은 확인되지 않았다.** 이 코드는 명시적 v1 envelope만 받으며 알 수 없는 schema/zip/jsonl 파일을 조용히 변환하거나 성공으로 표시하지 않는다. 형식 대조 뒤 별도 adapter가 필요할 수 있다. 원본 화면 지도에서 패턴/버튼/재방문 정책을 수집기 실제 HID 실행으로 옮기는 로직은 아직 별도 검수 대상이다.

## 작성자 PC 시험
로컬 원본은 손대지 않는 read-only 송신기(site_map_sender.py). 다음 명령은 해당 PC에 설치·권한 배포된 뒤에만 실행.

    python3 site_map_sender.py --root /workspace/map-data --dry-run

형식·사진 파일·SHA256 통과 후, 작성자 PC의 보호된 환경에서 MAP_INGEST_URL(HTTPS reverse proxy endpoint)과 MAP_INGEST_TOKEN을 설정한다.

    python3 site_map_sender.py --root /workspace/map-data --limit 1

성공한 배치만 /workspace/map-data/.send-state/*.ack.json 에 수신 영수증 기록. 수신 DB에는 site_id + batch_id 유일 제약; 내용 다른 중복은 409. 원본은 삭제하지 않는다. 실패하면 수정 전 원본 그대로 보존. 우선 1건만 현장 시험한다.

## NAS 및 Drive 현장 검증
NAS의 **실제 마운트 위치**를 운영자가 확인한 후에만 (예: /실제/NAS/마운트는 가상 예시이며 확정 위치가 아님):

    python3 site_map_backup.py --data /var/lib/court-auction-controller --nas-mount /실제/NAS/마운트

NAS mount가 아니라면 쓰기 전 실패한다. 로컬 그림자 디렉터리에 잘못 보관하지 않는다. Drive는 이미 구성된 rclone remote를 운영자가 확인한 경우:

    python3 site_map_backup.py --data /var/lib/court-auction-controller --drive-remote 승인된원격:site-maps

rclone copy 후 rclone check --download가 성공해야 Drive verified로 표시. 아직 주기 스케줄러는 설치되지 않았으며 실제 설정을 확인한 뒤 서비스 타이머에 등록해야 한다. 파일/DB/토큰 모두 24시간 내 복구 가능한지 별도 복원 시험 필수.

## 개발/시험
기반 브랜치는 chatgpt/performance-metrics-20261118. 신규 분기 chatgpt/site-map-ingest-s1-20261118. 기존 main에는 실제 컨트롤러 대신 placeholder가 있어 main에 직접 붙이거나 자동 병합하지 않는다. 개발 코드를 운영 설치와 혼동하지 않는다.

합성 테스트 (Actions 사용하지 않음):

    python3 -m unittest -v test_site_map_ingest test_performance test_controller

검증 게이트: 권한 없는 요청, 비TLS, 잘못된 상대경로, 사진 변조, payload 변조, 비밀키, 중복, 충돌, 서버 재시작, NAS 연결 끊김, Google Drive 오류, 수신/복원 해시 일치, 수집기 지도 조회와 사람이 승인한 시험 동작. 현장 인증/주소/스토리지 마운트가 확인되기 전엔 FIELD_PENDING 및 issue OPEN 유지.

### 지도 활용 계획 (미구현)
관제기 최신 지도 조회를 수집기 기존 visit/route/learning 흐름에 연동하고, 화면 상태 확인·사람 승인·버전 고정 후 AI 없이 지도 실행 → 실패하면 이전/다음 검증 버전 시도 → 기존 DeepSeek 글자/로그, Gemini 화면 순서 → 관리자 호출. 보안문자/캡차/403/429 우회 금지. GUI 동작이나 원격 서버에 관한 성공 주장은 반드시 실제 로그로 입증.
