# 법원경매 관제기 (Controller)

대전·충남 지역 법원경매 자료를 천천히·꾸준히 수집하기 위한 중앙 관제기입니다.

## 핵심 특징

- **감정평가서 우선 관리**: 건당 15~45초 이상 강제 대기, 실패 시 30~90초로 연장
- **단일 세션 제어**: GG옥션 ID 1개 제약에 맞춰 동시에 1개 수집기만 메인 작업
- **1시간 단위 계획 수립**: 수집기 상태·성공률·오류를 반영해 자동 재배정
- **사건번호 마스터 강제 생성**: 유찰·낙찰·취소·연기 여부와 무관하게 즉시 생성
- **오류·장애 자동 대응**: 연속 실패 시 휴식, 다른 수집기로 재배정

## 사용자 대화 및 bidguard 연동

- **사용자는 관제기를 통해서만 대화** (`user_query()`)
- AI가 만든 지시·계획·수집 자료는 모두 **bidguard 0단계 메뉴**로 전송
- 관제기가 중앙 허브 역할

### bidguard 0단계 항목 유형
- `ai_instruction`: AI 계획 수립 결과
- `collected_data`: 수집된 경매 자료
- `status`: 현황 요약
- `user_interaction`: 사용자 대화 기록
- `alert`: 오류·장애 알림

## 실행

```bash
python controller.py
```

SQLite DB(`controller.db`)에 상태가 자동 저장됩니다.

## 다음 단계

1. 실제 수집기(Playwright/Selenium 등)와 연동
2. 사이트 맵 상세화
3. 웹 대시보드 (FastAPI)
4. 일일/월간 목표 자동 산출 로직 강화

---

## 우분투에 설치 (S-0.1.1-CONTROL)

우분투 서버 터미널에서 한 줄:

```bash
wget -qO- https://raw.githubusercontent.com/jijuclub-tech/court-auction-controller/claude/controller-S010-control-20261117/install/install_controller.sh | sudo bash
```

- 처음 설치 때 관리인 화면 암호(12자 이상)를 두 번 묻습니다. 화면에 보이지 않고 `/etc/court-auction-controller/controller.env`(root만 읽기)에만 저장됩니다.
- 프로그램: `/opt/bidguard/court-auction-controller`, 자료: `/var/lib/court-auction-controller`, 서비스: `court-auction-controller` (부팅 때 자동 시작, 죽으면 다시 켬), 포트 8770.
- 다시 실행하면 프로그램만 바꾸고 자료·설정은 그대로 씁니다. 예전 Node 관제센터(`/opt/bidguard/control-center`, 8787)는 건드리지 않습니다.
- 휴대폰 호출 주소(`NOTIFY_URL`), 관제기 자체 AI(`CONTROLLER_AI`, `GEMINI_API_KEY`)는 설정 파일에서 넣고 `sudo systemctl restart court-auction-controller`.
- 지우기: `sudo /opt/bidguard/court-auction-controller/uninstall_controller.sh` (자료까지 `--purge`).

관리인 화면 탭: 호출(화면 사진에서 클릭 지정·아이디/암호/보안문자 입력·건너뛰기·정지, 소리 알림) · 수집기(승인·차단·토큰·실시간 상태) · 메뉴(자주가는/기타 사이트) · 정해진 경로(단계·시작 AI 단계·실행기) · AI 단계(1~4단계 업체·모델) · 지시서 · 프로그램 갱신(묶음 올리기·승인) · 기록.

## S-0.1.0-CONTROL (2026-11-17) — 관제형 수집기와 짝을 이루는 관제기

`controller.py` 하나로 돕니다. 파이썬 3 기본 기능만 쓰므로 따로 설치할 꾸러미가 없습니다.

```bash
export CONTROLLER_ADMIN_TOKEN='관리인만 아는 긴 글자'
export CONTROLLER_PUBLIC_URL='http://관제기주소:8770'      # 휴대폰 알림에 넣을 링크 (선택)
export NOTIFY_URL='BIDGUARD 호출 통로 주소'                # 휴대폰 호출 (선택)
export CONTROLLER_BOT_TOKEN='글록 봇용 글자'               # 3단계 질문에 글록 봇이 답할 때 (선택)
export CONTROLLER_AI='gemini:gemini-2.5-pro'               # 3단계에서 관제기 자체 AI (선택, GEMINI_API_KEY 필요)
python3 controller.py --host 0.0.0.0 --port 8770
```

관리인 화면: `http://관제기주소:8770/admin` (휴대폰에서도 열림, BIDGUARD 관리자 메뉴에 이 주소를 넣어 볼 수 있음)

### AI 단계 (관리인 지시 2026-11-17)
| 단계 | 누가 | 비고 |
|---|---|---|
| 1 | 로컬 Ollama, 딥시크 | 수집기 PC에서 |
| 2 | 하위 AI (업체·모델 선택) | 수집기 PC에서 |
| 3 | **관제기 (기본)**, 글록 봇, 사용자, 쳇GPT, 클로드, 상위 모델 | 보급형은 관제기 단계가 빠짐. **관제기와 연결이 안 되면 바로 4단계** |
| 4 | **관리인 (최종)** | 현장에 없으면 원격 호출(휴대폰 옵션): 화면 이미지, 클릭 지정, 아이디·암호·보안문자 입력, 건너뛰기, 정지 |

### 주소
기존 수집기가 이미 부르는 주소 (그대로):
- `POST /api/collector/connect` — 처음 보는 수집기는 "등록 대기", 관리인이 /admin에서 승인
- `GET /api/policy`, `GET /api/collector/instruction/<이름>`, `POST /api/collector/report`

새로 더한 주소:
- `GET /api/health` — 수집기가 수집 사이사이 점검
- `POST /api/collector/heartbeat` — 수집기 현재 상태(경로·단계)
- `GET /api/collector/updates?name=&rev=` — 메뉴(자주가는 사이트/기타 사이트)·정해진 경로·AI 단계 설정·승인된 새 프로그램
- `GET /api/collector/update_file/<파일>` — 승인된 프로그램 꾸러미 (수집기는 SHA-256 확인 후 받아 두기만, 설치는 관리인)
- `POST /api/ladder/ask`, `GET /api/ladder/ask/<id>` — 3단계 관제기 질문
- `POST /api/escalations`, `GET /api/escalations/<id>` — 4단계 관리인 호출
- 관리인(`X-Admin-Token`): `/api/admin/state`, `/api/admin/collector`, `/api/admin/answer/<id>`, `/api/admin/bundle`, `/api/admin/instruction`, `/api/admin/program`, `/api/admin/policy`
- 글록 봇(`X-Bot-Token`): `/api/bot/asks`, `/api/bot/answer/<id>` — 3단계 질문만 답할 수 있음 (4단계는 관리인 전용)

### 비밀
- 관리인이 원격으로 넣은 아이디·암호·보안문자 답은 **메모리에만** 두었다가 수집기가 한 번 가져가면 지웁니다. DB·기록에 남지 않습니다 (시험으로 확인).
- 화면 사진은 `controller_data/shots`에 두고 수집기가 답을 가져가면 지웁니다. 호출 기록은 100건까지만 둡니다.

### 시험
```bash
python3 -m pytest -q test_controller.py
```
GitHub Actions는 쓰지 않습니다.
