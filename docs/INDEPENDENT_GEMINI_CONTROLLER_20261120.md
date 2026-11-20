# BIDGUARD 독립 설치형 수집기 · 관제탑 AI 설정 (2026-11-20, 검수용)

## 기본 구조
- 관제탑: 관리자 `/admin/independent-ai`에서 Gemini 모델과 키 출처를 선택한다.
- 중앙 키 1 / 중앙 키 2 / 수집기 개인 키 중 하나를 정책으로 승인한다.
- 모델 후보는 `gemini-3.1-pro-preview`, `gemini-3.8-flash`, `gemini-3.1-flash-lite`. 실제 계약과 지원 여부는 장치별 API를 검사하여 확인한다.
- 기본 설정 `enabled=false`; 명시적 관리자 사용 전환 없이 비용 발생 불가.
- 관제탑은 등록된 사이트와 `learning.for_visit`이 승인한 사이트 학습서/버전만 제공한다. 수집기는 학습서의 해시와 버전을 로컬 캐시에 보관한다.
- 사용자 자연어 계획 요청과 화면 분석은 기존 수집기 이름/토큰으로 인증하고, 관리자 선택 모델만 사용한다.
- 중앙 키는 서버에서만 Gemini 호출에 사용하고, API 응답/클라이언트 DB/공개 GitHub/로그에 보내지 않는다. 개인 키 선택 시 수집기 자체 키를 그대로 이용한다.
- 기존 Pico2, F8, 다운로드, 작업일지, 관제기 중앙 업무 등 기존 동작은 수정/삭제하지 않는다.

## 암호화 키 등록 및 배포
- 관리자 화면에서 키를 등록하려면 관제탑 서비스 환경변수 `BIDGUARD_AI_WRAP_KEY`가 32바이트 난수를 Base64로 인코딩한 **비밀값**이어야 한다.
- 이 암호화용 키는 서버 로컬 보호된 비밀 설정에 두고 공개 저장소나 응답 화면에 남기지 않는다. 저장된 Gemini 키는 AES-256-GCM으로 DB에 암호화한다. 키 회전·복구 시 기존 암호화 자료의 해독 여부를 검사한다.
- 기존 `key_wallet.py`의 마스터 키 백업 및 최고관리자 복구방침과 충돌하지 않도록 실제 운영 배포 전 키 계층을 일원화한다. 본 브랜치는 별도의 시험용 암호화 환경키를 사용하며 **기존 지갑과 자동 연결되었다고 주장하지 않는다**.
- 환경변수 `GEMINI_API_KEY`, `GEMINI_API_KEY_ALT`를 통해 사전 구성된 키도 선택할 수 있다. 서버는 키 상태(True/False)만 UI에 응답한다.
- 키 입력 요청은 TLS 연결 또는 본체의 로컬 리버스 프록시 경유로 제한한다. 외부로 관제탑 8770을 인증 없이 공개하지 않는다.
- 초기 암호화 키, API 키, 관리 토큰은 사용자가 통제하는 암호화 백업(관제기/NAS/Google Drive)으로만 보존한다.

## API·가드
- `GET /api/admin/independent-ai` / `POST /api/admin/independent-ai`: 관리자 키 종류, 모델, 일별 요청 한도, 사용 여부.
- `POST /api/admin/independent-ai/key`: 암호화된 키 등록(입력한 평문/키를 다시 반환하지 않음).
- `GET /api/collector/independent-ai/options?name=...&site_id=...`: 등록 수집기의 모델·키 출처와 사이트 학습서. 키 원문 없음.
- `POST /api/collector/independent-ai/plan`: 사용자 자연어 조건 + 승인 학습서 기반 Gemini 계획. 지시 원문 미저장.
- `POST /api/collector/independent-ai/observe`: 사용자가 별도 동의하고 승인한 계획의 화면 분석. 이미지 메모리 전달만 하고 이미지 저장하지 않음. 로그인/OTP/결제/차단 시 사용자 직접 처리.
- 중복 request_key 호출 거부 및 일별 장치 호출횟수 제한. AI 요청은 본인 실행 버튼/승인 시에만 수행된다.
- 기존 승인 상태이고 실제 토큰 해시가 있는 보급·독립형(supply) 장치만 신규 AI 경로 사용 가능.
- 모델 응답은 수집기에서 기존 `validate_plan`/`validate_action` 및 F8/세대 확인을 다시 검증한다.
- 실제 현장 자료는 관제탑의 기존 NAS/Drive ingest/검증 원장을 통해서만 중앙 보관·복구하도록 별도 현장 연동이 필요하다. GitHub에 실자료·키 업로드 금지.

## 검수/출시 제한
- 서버 `python3 -m unittest -v test_independent_ai_policy test_controller test_rental_control`.
- Windows `cd standalone_collector && python -m unittest -v test_controller_ai_client test_independent_collector test_local_manager test_control_collector`.
- 실제 Gemini API 연결, 모델 이용권 확인, TLS, 장치 인증, 개인정보 마스킹, 화면 전송, 장치 F8·중복 방지, 비용 기록, NAS/Google Drive 원본 확인을 시험한다.
- 코드 작성·정적 검수·단위시험·실기 연결·실제 자료 수집·정식 서비스는 각각 분리하여 보고한다.
- Claude: 읽기 전용 검수. Grok: 최종 개선·실기 시험. 기능 삭제 없이 누적 적용.
