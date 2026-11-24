# 변경 기록 — 단독 수집기 v1.1 (검수 항목별 대응)

대상: Claude 검수(`../standalone_collector_v1_REVIEW_claude.md`, 치명 0·높음 5·보통 11).
v1 폴더는 백업으로 그대로 두고, 이 폴더(`standalone_collector_v1_1/`)에 새로 만들었어요.
**높음 5개 전부 + 보통 11개 전부** 고쳤습니다. 입력은 피코2만, 관제기 코드 없음, 비밀값 규칙 유지. 시험 전부 통과(standalone 45 + legacy 39, ALL PASS). 노트북·법원·피코2 접속 없음.

## 높음 (5/5)

### H1 — 요청 간격 상·하한, 429 배수 누적 막기
- `config.py`: `clamp_pacing()` 추가. 설정을 읽을 때 고정 범위로 묶음 — `item_gap_sec` 30~600초, `max_items_per_hour` 1~20, `max_items_per_run` 1~40, `break_min` 5~30, `rate_rest_min` ≥120, `max_429_per_day` ≤1, `after_429_gap_mult`·`gap_mult_max` 1.0~4.0. `item_gap_sec:[0,0]`·`max_items_per_run:0` 같은 대기 없는 설정은 더 이상 안 통함(시험 C 방어).
- `merge_site_pacing()`: 사이트 JSON의 `pacing` 은 **더 느리게 하는 쪽**(max)으로만 반영. DB 서버가 준 사이트 파일로 간격을 0으로 못 만듦.
- `pacing.py`: `effective_mult(cap)` 로 `gap_mult` 를 `gap_mult_max`(≤4)로 묶고, **429 없는 날이 지나면 1로 되돌림**. 6일 뒤 64배(시험 B)가 안 생김.

### H2 — DB 받기 통로 보안(https만·리다이렉트 때 토큰 금지)
- `kb_sync.py`: `check_server_url()` — 기본 `https` 만. `http` 는 `allow_http:true` + `host:"..."` 를 **설정에서 직접 켜야만** 허용(안 켜면 시작 전 거부).
- `_NoRedirect` 핸들러 — 30x 넘기기를 따라가지 않고 실패. 토큰을 다른 호스트로 안 보냄(시험 A 방어). 요청마다 호스트 일치 확인.
- `build_safe_opener()` — `verify_tls` 를 실제로 적용(https 인증서 검사). 쓰지 않던 설정을 실제 동작에 연결.
- 본문 크기 상한 `r.read(MAX_FILE+1)`.

### H3 — 가상키패드가 필요한 사이트는 비번 키보드 입력 금지
- `vk_login.py`(신규) `type_password()`: '모두지움' → 별표 0개 확인 → 한 글자씩(대문자는 SHIFT) → 2자마다 별표 수 확인 → 마지막에 별표 수가 비번 길이와 같을 때만 제출. 안 맞으면 로그인 단계에서 멈추고 사람 호출. 비번 글자는 어디에도 기록 안 함.
- `runner.py`: 법원·iros 계열 기본 `password_mode=virtual_keypad`. 키보드 입력은 설정으로만.

### H4 — 법원 범위(1호 수도권+인천·부천, 2호 비수도권)
- `config.py`: `region`(metro|nonmetro) 추가. `runner._region_ok()` 로 사이트의 `region_required` 와 다르면 실행 거부.
- `collector_kb/sites/court_dongbu.json`: `region_required=metro`(1호).
- `collector_kb/sites/court_nonmetro.json`(신규): 제주부터 비수도권 법원 목록(`nonmetro_courts`), 매각예정 **신건**만(`items_loop.rows.require_words=["신건"]`), `region_required=nonmetro`, `verified=false`.

### H5 — 같은 법원 아이디 동시 사용 회피
- `account_lock.py`(신규) `AccountLock`: ① 서버 잠금(`/account/lock`, 서버 있을 때 우선) → ② 로컬 잠금 파일(하트비트, 끊기면 자동 해제) → ③ 서버 없으면 `court_account_window`(KST 시간대)만 허용. 계정은 아이디 SHA-256 앞 12자(`account_tag`)로만 식별(평문 아님).
- `runner.run()` 에서 시작 때 acquire(안 되면 시작 안 함)·주기 heartbeat·끝에 release.

## 보통 (11/11)
- **M1** 로그아웃 판정: `top_box_r` 상자만, 1초 간격 2번 연속 '로그인' 있음+'로그아웃' 없음일 때만 성공. '예' 못 찾으면 실패. 실패 시 `STOP_<작업> logout_unconfirmed`.
- **M2** 긴 잠자기 중 STOP: `isleep.InterruptibleSleep` 로 5초 단위로 쪼개 매번 STOP·전원 확인, 있으면 바로 종료.
- **M3** rate_state 깨짐: 읽기 실패하면 기본값 대신 `rest_until=now+rate_rest_min` 보수적 시작 + 기록.
- **M4** DB 교체 원자성: 전부 `.staging/` 에 받아 SHA 확인 후 **전부 맞을 때만** 한꺼번에 교체(하나 실패하면 아무것도 안 바꿈). 매니페스트엔 실제 교체한 것만. `with+flush+fsync` 뒤 `os.replace`.
- **M5** 로그인 중 사진·글자: `in_login` 이면 ClickGuard가 사진 저장 안 함(`shot_ok=False`), `near_text` 는 Redactor 거쳐 기록.
- **M6** AI에 비밀값 가리기: DeepSeek 단어 목록에 Redactor 적용, Gemini엔 찾는 상자(`box_r`)만 잘라 보냄. `top_login` 클릭 `ai:False`. 예전 ai_log 묶음에서 제외.
- **M7** 예상 밖 예외: `app.main` 에서 감싸 종류 이름만 기록(`crash`), 메시지·traceback은 Redactor 거친 뒤에만.
- **M8** 자격 키 이름: `COURT_LOGIN_ID/PW`, `login_id/password`, `id/pw` 세 쌍 모두 받음(`_cred`).
- **M9** 사람 알림: 캡차·STOP 종료는 전용 종료 코드 10 + 콘솔 경고 + 소리(winsound) + `reports/ALERT_<시각>.txt`.
- **M10** 오래된 STOP: `status` 가 STOP 내용 첫 줄과 경과 시간을 보여 줌. `stop_dir` 가 NAS 경로면 경고.
- **M11** collector_id: 설정 필수(비면 시작 거부), JsonLog 모든 기록·STOP 내용·kb_sync 로그인 본문에 포함.

## t761u 숫자 그림 캡차 되돌림(한영님 본인 계정·본인 지시)
- `captcha.py` `solve_number()`: ① 조각그림/기억 먼저 → ② 없으면 AI 가 **딱 한 번** 판단해 입력 → ③ 틀리면 다시 시도하지 않고 멈춰 그림 + "376689가 맞나요?" 질문 저장 → 사람이 답하면 그 숫자를 한 번 입력.
- `screen_guard.py`: 숫자 캡차(보안문자) → `captcha_number`(풀기). **체크박스 reCAPTCHA·Cloudflare·그림 고르기** → `captcha_human`(지금처럼 멈춤).

## 지시와 다르게 한 것 / 참고
- KB 동기화 허용 폴더(`ALLOWED_TOP`)에 `sites`·`rules` 를 유지함(검수 H2의 '최소: ALLOWED_TOP에서 뺌' 대신, 전송은 https+호스트 고정+리다이렉트 금지+SHA로 막고, 사이트 JSON은 `region_required`·`verified`·`allow_hint_xy=false`·pacing max-only 로 안전장치를 둠). 사이트 단계표는 수집기 동작에 꼭 필요한 DB라 받되, 받은 값으로 간격을 느슨하게 하거나 위치를 멋대로 클릭하지 못하게 함.
- M8: 세 이름 쌍을 모두 받도록 넓혔음(지시대로). 평문 파일 저장은 여전히 없음.
