# 변경 기록 — 단독 수집기 v1.2 (재검수 반영 + 화면 멈춤 판단)

대상 재검수: `../standalone_collector_v1_1_REVIEW_claude.md` (해결 10·부분 5·미해결 1, 새 높음 2·보통 5).
v1.1 폴더는 그대로 두고 이 폴더(`standalone_collector_v1_2/`)에 새로 만들었습니다. 시험 전부 통과(standalone 68 + legacy 39, ALL PASS). 입력은 피코2만, 관제기 코드 없음, 비밀값 규칙 유지, 노트북·법원·iros 접속 없음.

## 새로 찾은 문제

### N1 (높음) — 서버 없으면 모든 회차가 account_busy 로 멈춤
- `account_lock.py`: `_server_lock` 이 `bool(None)`→False 로 처리하던 것을, **None(서버 못 씀)이면 로컬 잠금+시간대로 진행**하게 고침(`None if r is None else bool(r)`). → H5 완전 해결.
- 시험 추가(T11): 서버 없음→로컬 진행, 서버 거부→멈춤, 서버 허용→서버 잠금, 다른 수집기 로컬 점유→멈춤, 시간대 밖→멈춤, account_tag 해시.

### N2 (높음) — 숫자 캡차 자동풀기 정책
- 설정 `captcha.number_auto`(기본 **켜짐**, t761u 대표 직접 승인). 끄면 숫자 그림 캡차도 `captcha_human`(사람 멈춤)으로 보냄. 문서·규칙 문구를 '숫자 그림 캡차만 예외: 대표 직접 승인(t761u), 1회 한정, 실패 시 사람'으로 명시. 다른 봇 확인(체크박스·Cloudflare·그림 고르기)은 그대로 멈춤. 시험 T13.

### N3 (보통) — 캡차 콘솔 input 이 무한 대기
- 무인 실행에서 `console=None`(app.py). 캡차 답은 `answer.json` 으로만 받아 STOP·전원 확인이 계속 먹음.

### N4 (보통) — 캡차 입력 중 429·세션 무시
- `runner.input_check`: 숫자 캡차 입력 중에도 `rate_limit/rest/relogin` 이면 즉시 회차 종료 처리.

### N5 (보통) — 가상키패드 요소 이름에 비번 글자
- `runner._vk_password`: 요소 이름을 글자 무관 상수 `vk_key` 로(로그·조각그림·glob 로 비번 안 샘). note 도 'vk key'로 글자 없음.

### N6 (보통) — 꾸러미에 `__pycache__/*.pyc`
- 전부 지우고 `.gitignore` 추가.

### N7 (보통) — 429 배수·하한 느슨
- `pacing.effective_mult`: 날짜 바뀜이 아니라 **마지막 429 뒤 24시간**이 지나야 배수 1로 복귀(`last_429_ts`). `config` `after_429_gap_mult` 하한 2.0.

## 기존 부분 해결 마무리

### H2 — 서버 DB 가 동작을 바꾸던 구멍
- KB 동기화 허용 폴더에서 **`sites`·`rules` 제외**(코드 배포로만). 주석도 코드와 일치. `verify_tls:false` 는 https 에서 **거부**. 법원 family 는 좌표 힌트(`allow_hint_xy`) 강제 무시. → 서버 JSON 으로 429휴식·STOP·지역·가상키패드·좌표힌트를 끌 수 없음. 시험(safe_rel sites/rules 거부, update got 목록).

### H3 — 가상키패드
- N5(요소 이름) + **같은 글자 연속 규칙**(0.7~0.9초 쉼 + 4~7px 비켜 누르기)로 같은 글자 비밀번호도 눌림. 시험 T12.

### H4 — 법원 범위
- `court_nonmetro.json` `court_select` 에서 일반어 '지방법원' 제거(정확한 법원명만 + 수도권 법원 avoid). `region_required` 없으면 법원 회차 **거부**. `nonmetro_courts` 11곳 목록 유지.

### H5 — 같은 계정 동시 사용 → N1 로 완전 해결.

### M8 — 로그인 자격 별칭
- `_cred`: 별칭(`COURT_LOGIN_ID`/`login_id`/`id` 등)을 **먼저 묻지 않고** 모두 확인하고, 없을 때만 대표 이름으로 입력받음.

### M11 — collector_id
- STOP 파일 내용에 `[collector_id]` 포함, KB 로그인 본문에 `collector_id` 포함.

## 새 기능 (한영님 지시 14:45·14:50·14:52·14:55)

### 화면 멈춤 판단 `standalone/screen_state.py`
- 매 단계 뒤 3가지(로딩/완료/멈춤) 판단. 로딩 신호(글자·화면 변화·빈/반 그린 화면), 완료(연속 같음+기대 글자/조각그림, 목록은 줄 수 2연속 같음), 멈춤(로딩 없이 N초 그대로+기대 없음→page_guard 먼저→1회 재확인→멈춤). 기준값 사이트 JSON 에서 늘리기만. 대기시간 KB 누적(`screen_timing_local.json`)해 다음 대기에 반영. no_save_dialog 도 이 판단 사용. `do_goto`·`do_click(expect)`·목록·저장창에 연결. 시험 T14.

### 멈춤 원인 진단 `standalone/stall_diag.py`
- (A) PC 멈춤(마우스+시계/커서, NTP 시계 점프→PC_HICCUP) (B) 자료 흐름(psutil 받은 바이트 5초) (C) 연결(게이트웨이 ping·DNS·중립 사이트, 브라우저 안 염) → SITE_NO_RESPONSE(30분 쉬고 1회 재시도)·ISP_DOWN·LOCAL_NET_DOWN(5분 간격 복구 대기 후 그 화면부터). 법원·iros 직접 요청 금지. 통계 KB(`stall_stats_local.json`). 애매할 때만 딥시크 1회. 시험 T15.

### 지역별 NTP `standalone/sntp.py`
- 국내→일본→동남아→미국→유럽 묶음(설정으로 늘리기만). UDP 123 SNTP 48바이트 1회·서버당 2초·연속 요청 금지·10분 순환/의심 때 즉시 한 바퀴. 한 대 장애/지역 차단/전체 끊김 구분, 시계 점프(≥30초)→PC_HICCUP. 시험 T16.

### 켜고 끄기
- 전역 `ntp_check`(기본 켜짐), 사이트 JSON `site_alive_check`(기본 꺼짐, 법원·iros 는 켜도 거부). 첫 실행 `keys` 끝에 물어보고 넣을 값 안내. 시험 T17.

설명서: `SCREEN_STATE_KO.md`.
