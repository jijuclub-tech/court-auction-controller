# 변경 기록 — 단독 수집기 v1.3 (재검수 반영)

대상 재검수: `../standalone_collector_v1_2_REVIEW_claude.md` (13건: 해결 9·부분 3·미해결 1, 새 높음 3·보통 7).
v1.2 폴더는 그대로 두고 이 폴더(`standalone_collector_v1_3/`)에 새로 만들었습니다.
시험 전부 통과(standalone **82** + legacy **39**, ALL PASS, rc=0). 입력은 피코2만, 관제기 코드 없음, 비밀값 규칙 유지, 노트북·법원·iros 접속 없음.

## 바로 막아야 했던 두 가지

### R2 (높음) — 숫자 캡차 입력 중 429 시 TypeError 로 죽던 문제
- `runner.py` input_check: `return self.handle(d, "captcha_input", im, 0)` 로 고침(예전엔 `im` 인자가 빠져 `handle()` 이 TypeError).
- 이제 숫자 캡차 입력 중 429·rest·relogin 화면이 보이면 **반드시 로그아웃 → 최소 120분 휴식(rest_until 기록) → 회차 `rate_limited` 종료**. crash 로 다음 회차가 곧바로 다시 들어가는 일 없음.
- 시험 T09 `test_429_during_number_captcha_logs_out_and_rests`: 끝 상태 rate_limited, 로그아웃 클릭, `rest_why=HTTP_429`, rest_until 기록 확인.

### R1 (높음) — 멈춤 진단이 거의 항상 PC_FROZEN 으로 끝나던 문제
- `stall_diag.py`: `_pc_frozen` 을 `_pc_state()`(`alive`/`frozen_suspect`/`defer`) 로 바꿈.
  - 커서 이동·작업표시줄 시계 변화·화면 그림 변화(썸네일 평균차 >6) 중 **하나라도 있으면 `alive`**.
  - **커서 위치를 못 읽으면 `defer`** → PC_FROZEN 으로 단정하지 않고 B(자료 흐름)·C(연결) 진단으로 넘어감.
  - `PC_FROZEN` 은 **화면이 마우스 이동 전후로 똑같고 + NTP 시계 점프(히컵)까지 있을 때만**.
- `runner.py`: StallDiag 를 만들 때 `cursor_pos`(윈도우 GetCursorPos, 못 읽으면 None)를 넘기고, 진단용 마우스 이동 좌표를 화면 크기에 맞춰 동적으로(가운데 아래 빈 곳) 계산(예전엔 (960,1010) 박힘).
- 시험 T15: `test_pc_frozen`(커서·시계 고정 + NTP 히컵 → PC_FROZEN), `test_pc_frozen_defer_when_no_cursor`(커서 None → PC_FROZEN 아님), **`test_normal_screen_not_frozen`(정상 화면은 PC_FROZEN 아님)**.

### R3 (높음) — AI 로 보내는 캡차 그림이 '캡차 영역만'이 아니던 문제
- `runner.py` `do_captcha_number`: 화면 가운데를 그대로 자르지 않고 `_captcha_box()` 로 **보안문자 그림을 조각그림/글자(앵커)로 찾아 그 상자만** 자름.
- `_captcha_crop_safe_for_ai()`: 보내기 전 그림을 글자 인식해 **아이디/비밀번호/주소 등 개인정보나 Redactor 비밀값이 보이면 AI 건너뜀**. **로그인 중(`in_login`)에는 AI 로 안 보냄.**
- 캡차 위치를 **못 찾으면 AI 로 안 보내고 사람에게**(`captcha_ai_skipped` 기록).
- 시험 T09 `test_captcha_not_found_goes_human_no_ai`: 앵커 못 찾으면 AI 호출 0번 + `captcha_ai_skipped` 기록.

## 보통 7건

- **R4** `required:false` 존중: `settle(..., required=False)` 가 실패해도 예외 대신 `trouble/expect_not_seen_optional` 만 기록하고 계속. goto 의 expect 는 기본 기록만. 시험 `test_required_false_expect_continues`.
- **R5** NTP·연결 진단 손질: ① `sntp.udp_sntp` 가 IP 를 제한시간 안에 미리 해석(`_resolve`, 실패 서버는 무응답). ② 설정 `ntp.timeout_sec` 를 0.5~3.0 으로 조임(`config.py`). ③④ NTP 가 ALL_DOWN/REGION_BLOCKED 이면 `neutral()` 로 한 번 더 확인한 뒤에만 ISP/LOCAL 판정(아니면 SITE_NO_RESPONSE). ⑤ `merge_regions` 가 법원·iros·`.go.kr` 호스트를 지역 목록에서 거부. ⑥ 10분 순환을 `pre()`→`_ntp_cycle()`(`next_region_probe`, `cycle_min`)로 실제 구동(실기기에서만). ⑦ `AIHelper.stall_hint`(애매할 때 1회) 추가해 문서와 동작 일치. 시험 `test_court_iros_host_rejected`, `test_ntp_timeout_clamped`, `test_ntp_all_down_but_internet_ok_is_site_no_response`.
- **R6** 망 복구 대기: 회차 전체에 **마감 시각 하나**, 설정값(`stall.net_down_wait_min`/`site_rest_min`) 사용, 5분 넘는 대기 전 **안전 로그아웃**, 대기 중 **heartbeat**, 복구되면 `net_recovered_relogin`(다음 회차 로그인부터). 재귀 호출 제거. 시험 `test_net_recover_relogin_uses_settings_and_heartbeat`.
- **R7** AI 키 getpass 무한대기 제거: `ai_helper.py` 의 키를 `required=False` 로 읽어 **없으면 AI 건너뜀**(사람에게). 시험 `test_ai_skipped_when_key_absent`.
- **R8** number_auto 값 형식: `config.load` 가 `captcha.number_auto`·`ntp_check` 를 `_truthy` 로 정규화 — `'false'/'0'/'no'/'off'/''/0` 은 **끔**. **미설정이면 기본 True 유지(t761u 대표 직접 승인).** 시험 `test_number_auto_string_off`, `test_ntp_check_string_off`.
- **R9** 옛 상태 파일 배수 복귀: `pacing.effective_mult` 가 `last_429_ts` 가 없으면 `last_429_day + " 23:59"` 를 기준 시각으로 삼아 24시간 뒤 배수 1 로 복귀. 시험 `test_old_state_file_mult_resets_after_24h`, `test_old_state_file_mult_kept_within_24h`.
- **R10** 로컬 전용 통계 보호: `kb_sync.LOCAL_ONLY` 에 `screen_timing_local.json`·`stall_stats_local.json`·`ntp_stats_local.json`·`snippet_rank.json`·`kb_manifest.json` 포함 → `safe_rel` 이 서버 제공분을 거부. 시험 `test_safe_rel`.

## 남아 있던 부분/미해결 반영

- **N4(미해결)** → **R2 로 해결.**
- **N2(부분, 숫자 캡차)** → `"false"` 문자열 끔(R8) + 캡차 영역만 자르기·개인정보 가림·못 찾으면 사람(R3). 기본값은 t761u 승인대로 **켜짐 유지**.
- **N3(부분, 콘솔 무한대기)** → AI 키 getpass 제거(R7)로 캡차/로그인 중 무한대기 없음.
- **H5(부분, 동시 사용)** → 긴 대기(망 복구) 동안 heartbeat 를 계속 보내 잠금이 오래된 것으로 처리되지 않게 함(R6). 서버 없는 환경의 다른-PC 동시 사용 한계는 KB 서버 주소가 있어야 완전 해결(설계상 서버 잠금 필요).
- **H3 작은 점** → 가상키패드 같은 글자 비켜누르기를 **최소 4px(기본 4~7px)** 로(0px 방지). `vk_login.py`. 시험 T12 유지.

## 바뀌지 않은 규칙(그대로)
- 입력은 피코2(펌웨어 1.0.7, COM8)만. SendInput/keybd_event/pyautogui/pynput/playwright/selenium/Popen 등 입력 수단 0건.
- AI 키·법원 ID/PW·서버 로그인은 실행 때 입력(평문 저장 없음, DPAPI 선택). 공유 폴더에 비밀값 없음.
- 법원·iros 주소로는 어떤 직접 요청도 하지 않음(site_alive_check HEAD·NTP 지역 서버 모두 거부).
- 관제기 코드·`.git`/`.github`/yml 없음.
