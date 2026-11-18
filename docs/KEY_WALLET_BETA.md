# BIDGUARD 관제기 키 지갑 — 베타 S-0.1 (2026-11-19)

본 모듈은 운영 중인 관제기 프로세스와 분리된 **로컬 관리자용 CLI**입니다. 서버 HTTP API에서 마스터 키를 반환하지 않습니다. GitHub에는 소스·합성 테스트만 두며 실제 지갑/비밀키/암호/로그 원문/인쇄 기록은 절대 올리지 않습니다.

## 구조

- 관리자 암호와 로컬 OS 계정 권한으로 접근합니다. 마스터 키는 `secrets.token_bytes(32)`의 **256비트 난수**입니다.
- 지갑(`key-wallet.json`)은 **Scrypt(N=32768,r=8,p=1, 16바이트 난수 salt)**로 관리자 암호에서 파생된 키로 **AES-256-GCM** 암호화하여 저장합니다. 비밀값은 평문 파일로 저장하지 않습니다.
- 로그마다 독립된 256비트 세션 키를 생성하고, 로그 데이터는 그 세션 키로 AES-256-GCM 암호화합니다. 같은 세션 키를 **마스터 키**와 **해당 항목의 직원 암호로 파생된 키**로 별도 감싸서 그 로그 항목에 저장합니다. 직원 암호/직원별 장기 키는 지갑에 보관하지 않습니다.
- 권한이 필요한 `encrypt-log`, `decrypt-log`, `print-master`, `recover` 호출 시에만 잠시 키를 사용합니다. `bytearray`는 사용 후 0으로 덮습니다. **파이썬/암호 라이브러리 내부 복사본과 터미널 버퍼까지 완전 삭제를 증명할 수는 없습니다.** 상용판에서는 별도 프로세스 격리/메모리 잠금/강제 종료를 검토합니다.
- `print-master`는 로컬 TTY, 관리자 암호, `PRINT` 재확인 후에만 16진수 키를 화면에 보여 줍니다. 관리자 Enter 후 ANSI 화면·스크롤버퍼 삭제 명령과 메모리 삭제를 시도합니다. 실제 프린터/터미널 특성상 출력 흔적이 남을 수 있으므로 **네트워크/화면공유/로그/클립보드/자동 인쇄 스풀** 사용을 피해야 합니다. 오프라인 종이 인쇄본은 봉인 보관합니다.
- `recover`는 오프라인 인쇄본으로 지갑의 관리자 암호를 재설정하는 복구 시험입니다.
- 백업은 로컬, NAS, Google Drive에 **암호화 지갑 파일만** 복사합니다. NAS는 실제 mountpoint를 확인합니다. Google Drive는 기존 rclone 연결이 있을 때만 `copyto`로 전송하며, 실제 복원·체크섬 검증은 별도 운영 시험이 필요합니다. 복사 성공만으로 전체 백업/복원 보증은 아닙니다.
- HSM/유료 서비스/API 호출 없음. `cryptography`(PyCA)만 필요합니다.

## 설치/시험

Ubuntu의 서비스 계정이 읽고 쓸 수 있는 비공개 데이터 폴더에 지갑을 둡니다. CLI 명령은 해당 계정 또는 권한 있는 관리자만 실행합니다. 다음 예에서 **실제 비밀번호를 명령행 인수·환경변수·GitHub Actions에 넣지 않습니다**.

```bash
# 무료 검증 라이브러리 (환경에 따라 python3-venv 후 가상환경 권장)
python3 -m pip install cryptography
python3 -m unittest -v test_key_wallet

# 암호화 지갑 최초 생성
python3 key_wallet.py --wallet /var/lib/court-auction-controller/key-wallet.json init

# 인쇄 요청 시에만 비밀키 화면 표시
python3 key_wallet.py --wallet /var/lib/court-auction-controller/key-wallet.json print-master

# 실제 로그 원문은 stdin에서만 받음 (일시 파일/쉘 기록 생성 금지)
# 로그 송신 프로세스가 stdin pipe로 넘기는 용도:
# producer | python3 key_wallet.py --wallet ... encrypt-log --staff-id worker01 --out /var/lib/.../entry.json

# 파일 원문 조회는 관리자 또는 해당 직원 암호로만 가능 (화면 표시 후 지움)
python3 key_wallet.py --wallet /var/lib/court-auction-controller/key-wallet.json decrypt-log --item /var/lib/court-auction-controller/logs/entry.json

# 3중 백업: 로컬 + NAS 마운트 + 이미 연동된 Google Drive rclone remote
python3 key_wallet.py --wallet /var/lib/court-auction-controller/key-wallet.json backup --local-dir /var/backups/bidguard-wallet --nas-dir /mnt/bidguard-nas --drive-remote gdrive:bidguard/wallet-encrypted
```

## 베타 검수 한계 및 후속 연동

1. 현 단계는 **CLI 시험판**입니다. 관제기 웹 관리인 메뉴의 `인쇄` 버튼, 중앙 관리자 세션과 CLI의 역할 인증 연동은 별도 검수 항목이며 구현된 것으로 표시하지 않습니다. 사용자는 관리자 로컬 터미널에서 `print-master`를 실행합니다.
2. 기존 관제기 웹 토큰 인증은 별도입니다. 지갑 해제에는 **독립 관리자 암호**가 필요하고 웹의 일반 토큰만으로는 복호화할 수 없습니다.
3. 로그 원장에 실제 연결, NAS/Drive 백업 스케줄 및 다운로드 복원시험은 현장 접근 후 확인합니다. 직원 암호 분실 때에는 관리인 복호화로 접근합니다.
4. 비밀키를 발급·교체하는 서버 API, 설치 자동 복호화 등 장기 실행 워크플로는 별도 권한 검증 없이 구현한 것으로 주장하지 않습니다.
5. **경고:** 키가 화면에 표시되는 이상 스크린샷·악성프로그램·프린터 스풀·운영체제 스왑까지 통제된다고 보장할 수 없습니다. 테스트 과정에서 관찰하고 상용 단계에서 강화합니다.

## 검증 명령

`python3 -m unittest -v test_key_wallet` — 가짜 비밀번호/가짜 로그 6개 테스트만 사용. 실제 자격정보는 시험 데이터로 사용하지 않습니다.