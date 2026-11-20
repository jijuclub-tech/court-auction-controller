# BIDGUARD 관제기 키 지갑 · 수집기별 일괄 발급 T-v0.2

기존 `key_wallet.py`의 관리자 마스터 암호화 지갑을 **그대로 확장**합니다. 마스터 키는 관리자 로컬에서만 해제합니다. **수집기에는 마스터 키를 배포하지 않습니다.** 각 수집기의 고유 이름과 독립적인 256비트 접속 키가 필요합니다.

## 시험 설치 순서 (Ubuntu 관제기)

1. 이전 `key-wallet.json`과 `controller.db`를 암호화 백업합니다. 현재 운영 서버 설치 여부는 별도 확인해야 합니다.
2. 신규 설치라면 기존 `python3 key_wallet.py --wallet /var/lib/court-auction-controller/key-wallet.json init` 명령으로 지갑을 먼저 만듭니다. **기존 지갑을 다시 초기화하지 마십시오.**
3. 보급형·관제형·원격 수집기의 **실제 config.json 이름**을 확인하여 관리자 컴퓨터의 비공개 `/var/lib/court-auction-controller/collectors.json`에 아래 형태로 기록합니다. 임시 가짜 이름으로 실장비를 등록하지 않습니다.

```json
[
  {"name": "COL-EXAMPLE-01", "type": "control"},
  {"name": "COL-EXAMPLE-02", "type": "supply"}
]
```

4. 관제기 서비스를 중지하거나 임시 시험 DB로 먼저 검증하고, 신규 소스 배포 후 아래 명령을 **관제기 로컬 터미널에서** 실행합니다. 관리자 암호는 프롬프트로 입력하며 기록·인자로 전달하지 않습니다.

```bash
python3 -m unittest -v test_key_wallet test_collector_key_wallet_T_v0_2
python3 collector_key_wallet_T_v0_2.py \
  --wallet /var/lib/court-auction-controller/key-wallet.json \
  --db /var/lib/court-auction-controller/controller.db \
  provision --manifest /var/lib/court-auction-controller/collectors.json \
  --out-dir /var/lib/court-auction-controller/pairing-out
```

5. 관리자는 `pairing-out/장비이름.pair.json` 파일을 **해당 수집기만** 접근할 수 있는 비공개 경로로 전달합니다. 파일은 1회 사용·기본 24시간 유효하며 고유 등록 코드가 들어 있습니다. GitHub, 공개 링크, 일반 메신저, 로그, 스크린샷 업로드 금지. 대량의 서로 다른 키를 한번에 **발급**하지만 실제 수집기 PC로의 파일 전달에는 접근 권한/전송 수단이 필요합니다.
6. 수집기 **관제형 탭 → 키 지갑 등록 파일 가져오기** → 장비 이름과 일치하는 파일을 선택합니다. 수집기가 HTTPS 관제기 `POST /api/collector/key/claim`에 1회 코드를 보내 자신의 키만 수령하여 로컬에 저장합니다. Windows는 사용자 DPAPI 암호화, Linux는 사용자 전용 0600 파일입니다. 등록 파일은 성공하면 삭제합니다.
7. 수집기는 `X-Collector-Token`과 고유 `name`을 함께 사용합니다. 연결/명령/보고/정책/성과/학습 요청 때마다 검증합니다. 이름 없음, 키 없음, 다른 수집기 키, 차단 장비는 거부합니다. `/api/health`는 서버 생존 확인용 공개 정보이며 수집자료에 접근하지 못합니다.

## 관리 및 장애

- 현황: `python3 collector_key_wallet_T_v0_2.py --db ... status` (키 자체 노출 없음)
- 분실·차단: `python3 collector_key_wallet_T_v0_2.py --wallet ... --db ... revoke --name COL-EXAMPLE-01`
- 키 교체: `provision ... --rotate`는 해당 수집기의 **이전 키를 즉시 무효화**합니다. 해당 장비에 새 등록 파일을 전달할 수 있을 때만 사용합니다. 기본 발급 명령은 기존 키를 보존하므로 가동 중인 다른 수집기 기능을 건드리지 않습니다.
- 관제기 자체 관리인 인증은 `X-Admin-Token`, 글록 봇은 `X-Bot-Token`으로 별도 유지합니다. 마스터 키/관리자 암호를 HTTP API로 넘기지 않습니다.
- 지도 수신·기타 비수집기 봇에 별도의 종전 토큰이 있는 경우, 키 지갑과 별도 인증 경로가 남을 수 있으므로 수집기화 이전에 관제기/클라이언트 버전을 함께 전환해야 합니다.
- 관제기 Ubuntu 서비스, Windows/Pico2 실제 장치, NAS/Drive 백업 및 HTTPS 역방향 프록시는 **GitHub 병합만으로 설치·검증된 것이 아닙니다**. 실제 실행 로그·HTTP 403/200 테스트가 필요합니다.
