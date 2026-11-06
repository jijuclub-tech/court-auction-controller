# 법원경매 관제기

대전·충남 지역 법원경매 자료를 중앙에서 관리하는 관제기입니다.

**설치 위치**: 우분투 서버  
**연동 위치**: 기존 서비스 **bidguard의 0단계 메뉴**

## 역할

- 사건 마스터 강제 생성 (유찰/낙찰 무관)
- 감정평가서 우선 배정 + 장시간 대기 강제 (20~45초)
- **저장 경로 중앙 관리** (ChatGPT 지정 경로 지원)
- 수집기(보급형/관제형) 등록 및 지시서 발행
- 단일 세션 제어 (GG옥션 ID 제약)
- 1시간 단위 계획 재수립
- SQLite로 상태 영속화
- bidguard 0단계 메뉴에 지시·현황·수집자료·알림 전달

## bidguard 0단계 메뉴 연동

관제기는 bidguard 0단계의 한 항목으로 동작합니다.

| 항목 유형 | 내용 |
|-----------|------|
| `ai_instruction` | 1시간 계획 수립 결과 |
| `collected_data` | 수집된 경매 자료 |
| `status` | 현황 요약 |
| `user_interaction` | 사용자 대화 기록 |
| `alert` | 오류·장애 알림 |

사용자는 관제기를 통해서만 대화하며, 모든 AI 지시와 수집 결과는 bidguard 0단계로 전달됩니다.

## 우분투 서버 설치

```bash
# 의존성
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

# 디렉터리
sudo mkdir -p /opt/bidguard/controller
sudo chown $USER:$USER /opt/bidguard/controller

# 파일 배치 후
cd /opt/bidguard/controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # 필요 시 생성

# 실행
python controller.py
```

### systemd 서비스 예시 (`/etc/systemd/system/bidguard-controller.service`)

```ini
[Unit]
Description=Bidguard Court Auction Controller
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/bidguard/controller
ExecStart=/opt/bidguard/controller/venv/bin/python controller.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bidguard-controller
```

## 수집기 연동

1. 수집기가 `register_collector(name, type)` 로 등록
2. 관제기가 `generate_instruction()` 으로 지시서 생성 (`save_path` 포함)
3. 수집기가 `get_next_instruction(name)` 으로 지시서 수령
4. 수집 후 `report_result(task_id, success, error)` 로 보고

## 저장 경로

```python
ctrl = AuctionController()
ctrl.set_save_path("/data/auction/collected")  # 서버 내 경로 또는 마운트 경로
```

지시서에 `save_path`가 포함되어 수집기로 전달됩니다.

## 다음 단계

- bidguard 0단계 실제 API/큐 연동
- 수집기와의 HTTP 또는 메시지 큐 통신
- 로그·모니터링 (journalctl)
- 백업 및 재시작 정책 강화

## 우분투 서버 자동 설치

```bash
# 설치
sudo bash install.sh

# 서비스 시작
sudo systemctl start bidguard-controller
sudo systemctl status bidguard-controller

# 로그 확인
sudo journalctl -u bidguard-controller -f

# 제거
sudo bash uninstall.sh
```

### 설치 후 경로

| 항목 | 경로 |
|------|------|
| 프로그램 | `/opt/bidguard/controller` |
| 데이터 | `/data/auction` |
| 서비스 | `bidguard-controller` |
| 로그 | `journalctl -u bidguard-controller` |

## 복수 수집기 배정 (1시간 간격)

수집기가 여러 대 등록되면 관제기는 1시간마다 다음 기준으로 업무를 분배합니다.

- **속도 점수** (`speed_score`): 성공률 × (기준시간 / 평균소요시간)
- **성공률** (`success_rate`): 완료 건수 / 전체 시도
- **부하 분산**: 감정평가서 우선, 수집기별로 라운드로빈 배정
- 결과 보고 시마다 성능 지표가 자동 갱신되어 다음 계획에 반영

```python
ctrl.register_collector("수집기-A", "control", speed_score=1.2)
ctrl.register_collector("수집기-B", "supply", speed_score=0.9)
ctrl.plan_next_hour()  # 1시간마다 호출
```

## 사이트 맵 배포 및 수집

보급형 수집기가 접속하면 수집 대상 사이트를 확인하고 관련 맵을 전송합니다.  
반대로 수집기가 새로운 맵 형성 자료를 보내면 저장·분석 후 추후 배포에 반영합니다.

### 맵 전송 (수집기 접속 시)

```python
result = ctrl.on_collector_connect("보급형-user01", "courtauction.go.kr")
# result["map"] 에 맵 내용 포함
```

### 신규 맵 자료 수신

```python
materials = {
    "scene_name": "열람하기 다이얼로그",
    "description": "reCAPTCHA 모달",
    "solution": "열람하기 클릭",
    "success": True,
}
result = ctrl.receive_map_submission("보급형-user01", "courtauction.go.kr", materials)
```

### 저장 위치

| 항목 | 경로 |
|------|------|
| 배포용 맵 | `maps/` |
| 수집기 제출 자료 | `map_submissions/` |

## 수집기 로그 정책

| 유형 | 기록 범위 | 용도 |
|------|-----------|------|
| **보급형** | 접속 로그 + 기본 결과만 | 최소 추적 |
| **관제형** | 모든 행동 기록 | 분석 자료 |

로그 위치: `logs/YYYYMMDD_{수집기이름}.jsonl`

```python
# 접속 시 자동 기록
ctrl.on_collector_connect("보급형-user01", "courtauction.go.kr")

# 결과 보고 시 유형에 따라 기록
ctrl.report_result(task_id, success=True)

# 조회
logs = ctrl.get_collector_logs("수집기-A")  # 관제형은 상세
```

## 관리자 인증 및 API 키

- 관리자 ID/비밀번호: 해시 저장 (`credentials.json`)
- API 키: 암호화 저장 (`apikeys.enc`)
- **관제형 수집기에만** 암호화하여 전달
- 보급형은 API 키 수신 불가

```python
ctrl.set_admin("admin", "secure-password")
ctrl.verify_admin("admin", "secure-password")

ctrl.store_api_key("gg_auction", "sk-xxxx")

# 관제형만 성공
payload = ctrl.get_encrypted_api_key_for_collector("수집기-A", "gg_auction")
# payload["encrypted_key"], payload["session_key"]
```

> 실제 운영 시 session_key는 별도 보안 채널로 전달하는 것을 권장합니다.
