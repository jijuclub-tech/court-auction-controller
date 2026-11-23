# BIDGUARD #50 관제기 직접 수신 → NAS 저장 · 키 지갑 (T-v1.8.1 검수)

## 파일 흐름과 직접 연결 주소

수집기(X: 또는 기존 로컬 저장 원본 보존) → 검증 완료된 원본 업로드 대기열 → 내부망 HTTPS 또는 외부망 HTTPS → 관제기 POST /api/v1/collector/upload → 관제기 SSD SHA256 검증 및 수신 ACK → NAS 마운트 확인/복사/재읽기 SHA256 검증 → 원장 NAS_VERIFIED.

수집기는 NAS 계정/공유폴더/X 드라이브에 접근할 필요가 없다. 기존 v1.7_4p 실행본과 통합되지 않았으므로 운영 적용된 것으로 간주하지 않는다.

### 관제기 환경 변수(형식만 설명한 가상 예시; 실서버 주소 아님)

    BIDGUARD_COLLECTOR_UPLOAD_LAN_URL=https://collector-lan.example.test
    BIDGUARD_COLLECTOR_UPLOAD_WAN_URL=https://collector-wan.example.test
    BIDGUARD_COLLECTOR_UPLOAD_DIR=/var/lib/court-auction-controller/collector-upload-stage
    BIDGUARD_COLLECTOR_NAS_MOUNT=/mnt/actual-verified-nas-mount

LAN/WAN URL은 같은 관제기의 HTTPS 프록시로 접속한다. 실제 호스트명, TLS 인증서, DDNS 또는 사설 접속 경로는 관제 PC에서 설정해야 한다. 기존 Python 관제기 기본 포트 8770을 공인 인터넷에 직접 개방하지 말 것. SMB 445나 NAS 관리 화면도 외부 직접 개방 금지.

등록된 수집기는 X-Collector-Token으로 인증한 GET /api/policy?name=COLLECTOR_ID 에서 관리자가 실제 설정한 LAN/WAN 주소를 collector_upload.endpoints 로 받는다. 수집기 런타임은 LAN 실패 시 WAN으로 재시도하고, 둘 다 실패하면 로컬 대기열/원본을 지운 적 없이 다음 회차에 다시 시도한다.

### NAS 확인 및 무정지 처리

명시적으로 두 저장 경로가 설정된 경우에만 관제기 백그라운드 작업을 가동한다. 60초마다 미복사 파일을 확인한다. NAS는 os.path.ismount 검사를 통과하는 실제 마운트만 허용한다. 비어 있는 일반 디렉터리나 끊긴 NAS를 저장소라고 판단하지 않는다.

수신 ACK controller_stored는 SSD 저장 확인이며 NAS 확인은 아니다. NAS 파일을 다시 읽고 SHA256를 맞춘 뒤에만 NAS_VERIFIED로 업데이트한다. NAS 단절/해시 충돌/원본 변조/전원 장애에서는 NAS 성공 처리 금지. 원본 SSD 파일 삭제 없음. NAS와 Google Drive 백업은 별도 검증 대상이다.

### 키 지갑과 권한

관제기 GitHub에는 key_wallet.py(오프라인 암호화 관리자 지갑 CLI), collector_key_wallet_T_v0_2.py(장치별 토큰 생성/페어링) 구현이 이미 존재한다. 그러나 실제 운영 관제기에 key-wallet.json 파일이 생성되었는지, 마스터키가 승인되고 대여가 활성화되었는지는 서버 현장 확인 전 미확인이다.

새 관리자 전용 GET /api/admin/wallet/status 는 암호화 파일 유무, 사설 권한, 지갑 형식, 승인된 장치 토큰 수만 보인다. 비밀키 값·암호·마스터키를 절대 응답하지 않음. #47의 NAS 계정 키 실대여 API는 아직 연결되지 않았으며 사용자 대표의 키 발급/회수 직접 승인이 필수다.

### 추가 파일

- collector_upload_address_T_v1_8_1.py: 등록 단말에만 설정된 LAN/WAN HTTPS 주소 제공
- collector_nas_bridge_T_v1_8_1.py: 관제기 SSD→마운트 검증 NAS 미백업 파일 복사/검증
- collector_wallet_state_T_v1_8_1.py: 관리자 지갑 파일 메타 상태
- collector_feature_runtime_T_v1_8_0.py: 등록 관제기의 두 URL만 순서대로 시도
- controller.py: NAS 작업 스레드 및 인증된 정책/지갑 상태 경로
- test_collector_nas_bridge_T_v1_8_1.py / test_collector_wallet_state_T_v1_8_1.py: 합성 파일 기반 오프라인 테스트

### 이번 변경의 판정

개발 PR #51 검수 코드만 변경. 실 Windows 수집기 v1.7_4p 연동, 실제 관제기 배포, NAS 실마운트, 실제 파일 1건 전달, 지갑 파일 실존/대표 키 승인, NAS/Drive 원복 시험은 미실시. GitHub Actions 추가 없음. 자동 병합/운영 설치 금지.
