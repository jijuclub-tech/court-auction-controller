# BIDGUARD 단독 수집기 v1.6 (최종판, 관제기 접속 필수) — 검수 전

만든 이: 수집기 1호 / 2026-11-19 (KST) / 한영님 지시 t844u·t845u·t846u·t850u·t851u·t853u
상태: **검수 전 시험판**. 상자에서 시험만 했어요(실기기·실제 법원 접속 시험 안 함). 노트북 설치는 `INSTALL_jijuclub5_KO.md`.

## 무엇인가요
- 입력은 피코2(펌웨어 1.0.7 이상)로만. 브라우저 자동화·JS·URL 직접 요청 없음. 법원·iros 로 직접 요청 없음.
- **관제기 접속 필수(v1.6)**: 시작 때 관제기에 로그인 → DB 목록 → 사이트 참고자료(조각·2/4/8배·전체 화면 화보·site_pack) 받기(SHA 확인)
  → 화면에 '해당 사이트 학습 중입니다…' → **전부 메모리에 적재** → 작업 중엔 메모리 자료만 → 끝나면 정리·보고·올리기·메모리 해제.
  관제기에 못 붙으면 **시작하지 않고** STOP 파일에 사유(`server_required ...`)를 남겨요(로컬 DB·최소 지도 예외 없음).
  작업 중 잠깐 끊기면 멈춤 진단이 복구를 기다렸다가 같은 화면에서 이어가요.
- 사이트 용량 등급 A 500MB · B 100MB · C 10MB · D 1MB(관제기 관리 밖은 최대 100MB, 기본 C, 법원경매 기본 A).
- **자료 암호화**: 참고자료·캐시·순위·반응시간·올리는 자료 모두 암호화(BGX1, scrypt 키 유도). 지금은 시험 키 `test`,
  **2026-12-01 00:00 KST** 에 새 키로 바꿔요. 그 뒤에도 시험 키면 경고하고 시작 안 해요. 갱신: `config.json` `crypto.key_source:"dpapi"` + `keys` 에서 DATA_KEY 입력.
  `python tools/key_upgrade_audit.py` 가 시험 키 자리(표시 KEY-UPGRADE-AT-2026-12-01T00:00+09:00)를 모두 찾아요.
- **적응 대기**: 화면 복잡도(글자 양·표 줄 수·그림 수)와 사이트·단계별 실제 반응시간(이동 평균·75% 분위수, 표본 5개 미만이면 늘리기만)으로
  클릭 뒤 쉼을 정해요. 법원 최소 1초·429 배수 아래로는 안 줄이고, RateGate(물건 사이 60~120초)·휴식·계정 교대는 손대지 않아요.
- **판단 담당표**: 이벤트마다 PROGRAM → LOCAL_LLM(ollama, 선택) → DEEPSEEK → GEMINI 담당을 기억해 다음엔 바로 그 담당으로(하위 모델 건너뜀).
  N번 실패면 승격, 프로그램이 연속으로 맞추면 강등. 딥시크는 한 판단에 1회. 그 위는 기존대로 기록·알림·STOP.
- **시간 예산**: 판단 지연을 합산해 회차 보고(`run_report`: 담당별 횟수·초, 절약 초)에 넣고, 지연이 쌓이면 여유 대기만 줄여 만회.

## 설치·실행 요약
자세한 것은 `INSTALL_jijuclub5_KO.md`. 시작 한 줄(노트북, 로컬 관제기 사용 시 먼저 관제기 창을 띄움):
`D:\BIDGUARD\standalone_collector_v1_6\.venv\Scripts\python.exe -m standalone run collector_kb\sites\court_dongbu.json --minutes 30`
- `config.example.json` → `config.json`. **설정 파일에는 키·암호를 넣을 수 없어요**(crypto.key 는 시험 키 `test` 만 허용).
- `collector_id`·`region` 은 꼭 적어요. `kb_server.url` 은 **필수**(관제기 주소, 지금은 로컬 관제기 `http://127.0.0.1:8790`).

## 키·아이디 입력 (사람이 직접)
`python -m standalone keys`
- 딥시크 키, 제미나이 키, 법원 아이디·비밀번호, DB 서버 아이디·비밀번호(대표가 정함)를 차례로 물어요. 쳐도 화면에 글자가 안 보여요.
- 끝에 "이 노트북에 Windows 암호화(DPAPI)로 저장할까요?" 를 물어요. **아니요**면 이번 실행 동안만 기억하고, 다음 실행 때 다시 물어요. **예**면 이 Windows 사용자만 풀 수 있게 암호화해서 `%LOCALAPPDATA%\BIDGUARD\standalone_secrets\` 에 둬요(공용 폴더·수집기 폴더 밖). 평문 파일은 만들지 않아요.
- 암호를 바꿨으면 `python -m standalone keys --forget` 로 저장본을 지우고 다시 입력해요.
- 키를 미리 넣지 않아도 `run` 하다가 필요할 때 물어봐요. 노트북에서 막히면 한영님 휴대폰 입력창으로 받아 사람이 노트북에 쳐요.

## 실행
- 법원(수도권, region=metro): `python -m standalone run collector_kb\sites\court_dongbu.json --minutes 30`
  - 첫 회차는 꼭 `--minutes 30` 으로 짧게, 사람이 화면을 보면서 해요(법원 단계 위치는 아직 실제 확인 전).
- 법원(비수도권 신건, region=nonmetro): `python -m standalone run collector_kb\sites\court_nonmetro.json --minutes 30`
  - 제주부터 비수도권 매각예정 **신건**만 받아요. 법원마다 `court_select` 글자를 바꿔 회차를 나눠 돌려요(`nonmetro_courts` 목록 참고, `verified=false`라 첫 회차는 사람 확인 필요).
- **비밀번호 입력**: 법원·iros 등 가상키패드가 뜨는 사이트는 비번을 키보드로 치지 않고 가상키패드를 눌러요. '모두지움'→별표 0개 확인→한 글자씩→별표 수가 비번 길이와 같을 때만 제출, 안 맞으면 로그인 단계에서 멈추고 사람을 불러요(검수 H3).
- 지도(야놀자): `python -m standalone run collector_kb\sites\map_yanolja.json`
- 최신 DB 받기만: `python -m standalone sync`
- 상태(쉬는 시간·429 횟수·멈춤 파일): `python -m standalone status`
- 하는 일 순서: 관제기 접속·참고자료 메모리 적재(필수) → 피코2 연결·ARM → 단계마다 [멈춤 파일·전원 확인 → 동작 → 화면 오류 확인] → 끝나면 로그아웃·NAS로 옮기기.

## 멈추기
- 그 작업만: `python -m standalone stop court_dongbu` (→ `STOP_court_dongbu` 파일)
- 전부: `python -m standalone stop all` (→ `STOP_ALL`)
- 예전 `STOP` 파일은 **법원 작업만** 막아요. 지도·예약 작업은 안 막아요.
- 다시 돌리려면 사람이 그 STOP 파일을 지워요(프로그램은 스스로 안 지워요).
- 피코2 F8·비상정지는 그대로 먹어요.

## 저절로 멈추는 경우
- 429(요청 과다): 즉시 손 멈춤 → 로그아웃 → 120분 쉼 → 다음엔 간격 2배. 같은 날 두 번째면 그날 끝.
- 차단 화면(403 등): 멈춤·180분 쉼·STOP 파일. 사람이 확인 뒤 재개.
- 인증서·결제·발급 화면: 아무것도 안 누르고 멈춤·STOP 파일.
- **숫자 그림 캡차(보안문자, t761u 규칙)**: 먼저 전에 맞춘 조각그림(기억)으로, 없으면 AI 가 **딱 한 번** 판단해 입력해요. 틀리면 다시 시도하지 않고 멈춰서 그림과 "376689가 맞나요?" 질문을 `state\captcha_ask\pending.json` 에 저장해요. 사람이 `answer.json {"digits":"..."}` 에 숫자를 적으면 그 숫자를 **한 번** 입력해요(한영님 본인 계정·본인 지시).
- **다른 봇 확인(체크박스 reCAPTCHA·Cloudflare·그림 고르기)**: 자동으로 풀지 않아요. 그림을 저장하고 멈춰요. 사람이 직접 풀고 STOP 파일을 지워요. 소리(비프)와 `reports\ALERT_*.txt`, 종료 코드 10 으로 사람을 불러요.
- 배터리 10% 미만이고 충전기 빠짐: 멈춤(20% 미만·충전기 빠짐은 경고만).

## 기록 위치
- `logs\<작업>_<날짜>.jsonl` (키·암호는 *** 로 바뀌어 기록돼요), 화면 사진 `logs\shots\<작업>\` (로그인 화면은 찍지 않아요)
- `state\rate_state_<사이트>.json` (쉬는 시간·429), `state\done_<작업>.json` (끝낸 사건), `state\pending_to_nas.jsonl` (NAS로 옮길 것)

## 시험 (사이트 접속 없음)
`python tests\run_all.py` → `ALL PASS` 가 나와야 해요.

## 화면 멈춤 판단·원인 진단 (v1.2)
- 매 단계 뒤 '로딩/완료/멈춤'을 스스로 보고, 멈추면 '왜'(내 PC 멈춤·자료 오는 중·사이트 무응답·인터넷 끊김)까지 프로그램으로 알아내요. 법원·iros 로는 직접 요청하지 않아요. 자세한 건 `SCREEN_STATE_KO.md`.
- 켜고 끄기: 전체 `config.json` 의 `ntp_check`(기본 켜짐), 사이트 JSON 의 `site_alive_check`(기본 꺼짐, 법원·iros 는 켜도 거부). 첫 실행 `keys` 끝에 물어봐요.

## v1.5 에서 바뀐 것 (짧게)
- **법원 고르기(C8 스크롤)**: 사이트 JSON `court_select` 가 `"type": "select_scroll"`. 드롭다운을 열고 휠 한 칸씩 내리며
  정확한 법원 이름만 클릭(부분일치 금지) → 안 되면 아래키 → 그래도 없으면 `COURT_NOT_FOUND` STOP. 비수도권은 `refuse_metro: true`.
- **사이트 거부 기록**: `logs/refusals.jsonl` (덧붙이기만). 한 줄 = 시각(KST)·호스트·종류(site_alive/ntp)·거부 주체
  (`POLICY`=우리 규칙이 막음, `REMOTE`=상대·망이 거절)·이유 코드·HTTP 상태·소켓 오류·시도 번호·다음 허용 시각.
  법원·iros 는 요청을 보내지 않고 `POLICY_REFUSED` 로만 적어요.
- **조각 그림 다중 크기**: 1배 조각 + 같은 중심의 2·4·8배 '맥락 그림'.
  저장 시도 10번째마다 2배, 100번째마다 4배, 300번째마다 8배. 실패가 2·5·10번이면 2·4·8배를 한 장씩 더(`snippet_scales` 로 바꿈).
  찾을 때는 큰 것→작은 것, 클릭 좌표는 항상 1배 조각으로만. 큰 그림에서도 찾은 ID/PW/캡차 입력칸은 회색으로 가림.
  금지 그림(인증서 등)은 `collector_kb/snippets/_forbidden/` 에 따로(찾기에 안 쓰고 서버와도 안 주고받음).
- v1.6: `allow_no_window` 는 없어졌어요(관제기 필수라 '서버 없이 시작'이 없음). 금지 그림은 관제기 묶음에서도 `snippets/_forbidden/` 로 따로.
- 참/거짓 설정(`ntp_check`, `captcha.number_auto`, `kb_server.allow_http`, `kb_server.verify_tls`,
  `power.warn_unplugged`, 사이트 JSON `site_alive_check`)은 모두 같은 규칙으로 읽어요.
