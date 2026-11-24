# 변경 기록 — 단독 수집기 v1.4 (재검수 반영)

대상 재검수: `../standalone_collector_v1_3_REVIEW_claude.md` (14건: 해결 12·부분 2(R6,H5)·미해결 0, 새 보통 4(V1~V4)).
v1.3 폴더는 백업으로 그대로 두되, 검수가 지적한 '시험용 로그인 값 노출'만 v1.3 파일에서도 제거했습니다(아래 V1).
v1.4 는 이 폴더에 새로 만들었습니다. 입력은 피코2만, 관제기 코드 없음, 비밀값 규칙 유지, 노트북·법원·iros 접속 없음.

## 시험 결과(보고 전 새로 돌린 실제 출력)
- `tests/run_all.py`: **standalone 93개 전부 통과, legacy page_guard 39/39 통과, ALL PASS (rc=0).**
- v1.3 검수 때 실제로는 81/82(1개 실패, rc=1)였던 점을 바로잡아, 이번엔 돌린 실제 숫자를 그대로 적습니다.

## 보통 4건

### V1 — 꾸러미 자체 시험 실패(시험용 로그인 값이 문서에 노출)
- 원인: `SECRET_SCAN_KO.txt` 에 금지값이 따옴표째 들어 있어, 꾸러미 시험 `test_no_test_login_values_in_package` 가 자기 문서에서 그 값을 찾아 실패(rc=1)했습니다.
- 고침
  - 시험을 **값 리터럴 없이** 고쳤습니다. 금지값을 조각으로 조립(`"".join([...])`)하고, 조립한 값이 맞는지 **SHA-256 해시로 한 번 더** 확인합니다. 그래서 코드·문서 어디에도 값이 드러나지 않습니다.
  - 이제 검사 대상에 **시험 파일 자신도 포함**합니다(조립했으니 리터럴이 없어 통과).
  - `SECRET_SCAN_KO.txt` 에서 노출 문구를 지우고 "조각 조립·해시 확인"으로 다시 썼습니다.
  - **v1.3 폴더의 `SECRET_SCAN_KO.txt`·`tests/test_standalone.py` 에서도 값을 제거**하고 v1.3 의 SHA256SUMS 를 다시 만들었습니다(그 폴더 시험도 rc=0 확인).
- 시험: `T01Secrets.test_no_test_login_values_in_package`(재작성).

### V2 — 로그인 회차에서 망 복구 대기가 작동하지 않던 문제
- 원인: 망이 끊긴 채로 먼저 `safe_logout()` 를 시도 → 로그아웃 확인 실패 → `logout_unconfirmed` STOP 기록 → 첫 잠자기에서 멈춤. 진단을 한 번도 못 하고 끝났습니다.
- 고침(`runner.py` `_wait_net_recover`): 망이 끊긴 동안에는 **로그아웃을 시도하지 않고 STOP 도 쓰지 않으며** 복구를 기다립니다. 대기 중 heartbeat 를 보냅니다. **복구되면 그때 로그인 상태를 확인(로그아웃)**하고 `net_recovered_relogin` 으로 끝냅니다(다음 회차 로그인부터). 끝내 복구 못 하면 그때 STOP.
- 시험: `T09Runner.test_login_net_recover_waits_no_stop_then_relogin`.

### V3 — 캡차 입력 중 세션 만료 시 그대로 진행하던 문제
- 원인: 캡차 입력 중 `relogin` 을 429 와 같은 분기로 처리해 재로그인 뒤 **남은 숫자를 새 화면에 입력**하고, `verify()` 가 "캡차 없음"으로 통과해 **틀릴 수 있는 답이 기억에 저장**될 수 있었습니다.
- 고침(`runner.py` `input_check`): 캡차 모드에서 `relogin` 을 만나면 입력을 중단하고 `RunEnd("session_lost_in_captcha")` 로 끝냅니다(다음 회차에서 새 캡차를 처음부터). verify 전에 끝나므로 **확인 안 된 답은 기억에 저장되지 않습니다.**
- 시험: `T09Runner.test_session_expired_in_captcha_aborts_and_no_memory_save`.

### V4 — 캡차 AI 안전 검사에 남은 작은 점
- ① **대소문자 무시**: `joined.lower()` 로 비교하고, 영어 `id/password/pw/login` 등 소문자 키워드로 검사 → 대문자 "ID"/"PASSWORD" 도 걸러 AI 금지.
- ② **자르기는 캡차 그림 자체 기준**: 라벨만 찾았을 때는 라벨을 가운데 두지 않고, 사이트 JSON `image_offset_r`(라벨 기준 그림 방향·크기)만큼 옮겨 그림 쪽을 자릅니다. 조각그림으로 그림 자체를 찾으면 그 중심을 씁니다.
- ③ **가리기는 부분일치·입력창 영역 통째**: Redactor 를 글자 전체 문자열에 적용해 비밀값이 **부분이라도** 보이면 금지. AI 로 보낼 그림에서 `input_box_r`/`redact_box_r` 와 겹치는 **입력창 영역은 통째로 하얗게 가립니다**(`_captcha_ai_crop`).
- 로그인 중(`in_login`) 숫자 캡차는 여전히 AI 금지(그대로 둠).
- 시험: `T19ReviewV14`(대문자 ID 차단, 숫자만 허용, Redactor 부분일치 차단, 상자 offset, 입력창 가림).

## R6·H5 보완

- **서버 없을 때 시간창 필수(H5)**: 설정에 `allow_no_window`(기본 **False**) 추가. KB 서버 주소도 없고 `court_account_window` 도 비어 있으면 법원 로그인 회차는 **시작을 거부**(`no_window_no_server`)하고 경고/알림을 냅니다. `allow_no_window:true` 로 둘 때만 '항상 허용'. 첫 실행(`keys`)에서 시간대를 묻습니다.
- **항목 루프 안 heartbeat(H5)**: 40건 루프에서 **건마다** heartbeat 를 보냅니다(`do_items_loop`). 긴 단계 대비로 `pre()` 에서도 60초마다 한 번 보냅니다. 잠금이 15분 기준을 넘어 '오래됨'으로 처리되지 않게 합니다.
- **서버 없이 도는 동안 경고(H5)**: 로그인 회차를 서버 없이 시작하면 **시작 때 + 30분마다** `no_server_warning` 로그를 남깁니다.
- 시험: `test_no_server_no_window_refuses_login_run`, `test_window_set_allows_without_server`, `test_no_server_warning_logged`, `test_items_loop_heartbeat_each_item`.

## 바뀌지 않은 규칙(그대로)
- 입력은 피코2(펌웨어 1.0.7, COM8)만. SendInput/keybd_event/pyautogui/pynput/playwright/selenium/Popen 등 입력 수단 0건.
- AI 키·법원 ID/PW·서버 로그인은 실행 때 입력(평문 저장 없음, DPAPI 선택). 공유 폴더·시험 파일에 비밀값/시험 로그인 값 0건.
- 법원·iros 주소로는 어떤 직접 요청도 하지 않음(site_alive_check HEAD·NTP 지역 서버 모두 거부).
- 숫자 그림 캡차 t761u 는 대표 직접 승인(1회 한정, 실패 시 사람). 기본 켜짐이되 설정으로 끌 수 있음.
- 관제기 코드·`.git`/`.github`/yml 없음.
