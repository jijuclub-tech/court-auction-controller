# 단독 수집기 v1.6 바뀐 점 (v1.5 → v1.6, 최종판) — 수집기 1호, 2026-11-19 KST

v1.5 폴더는 그대로 두고 새 폴더에서 고쳤어요. 보급형 v1.1 은 한영님 t853u 에 따라 **뒤로 미뤘어요**(폴더만 복사, 아직 고치지 않음).

## 1. 참고자료 정책 (t844u) — `standalone/refpack.py`, `app.py`, `runner.py`, `snippets.py`
- **관제기 필수, 폴백 없음**: 주소 없음·접속 안 됨·로그인 실패·자료 이상이면 시작 안 하고 STOP `server_required <사유>`
  (`server_url_missing`·`server_unreachable`·`bad_manifest`·`sha_mismatch`·`refpack_over_grade`·`refpack_no_gallery`·`decrypt_failed`).
  '로컬 DB로 계속', '최소 지도', `allow_no_window`, `--no-sync` 를 없앴어요. 실기기(피코)에서는 Runner 도 참고자료 없이 시작 거부.
  작업 중 잠깐 끊김은 기존 멈춤 진단 복구 대기 → 같은 화면 이어가기 그대로.
- **화보(전체 화면)**: 단계마다 대표 화보 저장(ID/PW/캡차 칸 회색, 인증서 화면은 `_forbidden/gallery_*`, 지각 해시로 중복 안 저장, 단계당 최대 3장).
  로그인 칸 위치를 못 찾은 로그인 화면·보안문자 화면은 화보를 찍지 않아요.
- **등급** A 500MB · B 100MB · C 10MB · D 1MB, 관제기 관리 밖 최대 100MB·기본 C, 법원경매 기본 A(`site_grades`).
- **흐름**: 접속 → 매니페스트 → 받기(SHA) → '해당 사이트 학습 중입니다…'(`state/ui_status.json`·콘솔) → 메모리 적재 → 메모리 자료만 사용 → 끝에 정리·보고·올리기·메모리 해제.
  디스크 캐시는 `state/refcache/<사이트>/<버전>/` 임시(암호문), 같은 버전 재사용, 다른 버전 삭제.
- **정리**: 중복·유사(지각 해시 ≤6) → 오래된 것. 대표 화보·성공률 상위 20%·설명/표 보호. 보고 JSON(`/refpack/report`), 한도 넘으면 올리기 안 함.
- `SERVER_API_KO.md` 7장(매니페스트·파일·보고·올리기), 가짜 관제기 시험.

## 2. 적응 대기 (t845u-1) — `standalone/adaptive_wait.py`
- 복잡도 점수 = 글자 양(0.5)·표 줄 수(0.3)·그림 칸 수(0.2). 단순 0.7배 ~ 복잡 1.5배.
- 사이트·단계별 실제 반응시간(화면 '다 됨'까지 걸린 초)을 방문마다 누적: EWMA + 75% 분위수 중 큰 값. 보정값 ±(−0.5배~+2배).
  표본 5개 미만이면 보수적(늘리기만). 바닥: 법원 1초, 그 밖 0.3초, ×429 간격 배수. RateGate·429 휴식·시간당 휴식·계정 교대는 별도 경로라 손대지 않음.
- screen_state 상한은 사이트 JSON 으로 '늘리기만' 그대로. 반응시간 추정은 '클릭 뒤 첫 확인까지 쉼'만 바꿔 충돌 없음(멈춤 판정 바닥은 올리는 쪽만).
- 반응시간 표는 참고자료 `site_pack/<site>/timing.json` 으로 받고 회차 끝에 올려요.

## 3. 판단 담당표 (t845u-2) — `standalone/decision_routing.py`, `locator.py`, `ai_helper.py`
- 담당 PROGRAM / LOCAL_LLM(ollama, 이 PC 주소만, 설정 `ai.local_llm_url` 비면 건너뜀) / DEEPSEEK / GEMINI / (보급형) HUMAN.
- 처음 보는 이벤트만 아래부터, 아는 이벤트는 표의 담당으로 바로(GEMINI 담당이면 하위 모델 건너뜀). PROGRAM(조각·글자)은 무료라 늘 먼저 한 번.
- 담당 모델 N=2번 실패 → 승격, 프로그램 3연속 성공 → 강등. 푼 담당·걸린 초 기록. 딥시크는 한 판단에 1회. 단독판은 GEMINI 다음 기존대로(기록·알림·STOP).
- 표는 `site_pack/<site>/decision_routing.json` 으로 관제기와 공유(이벤트 이름·횟수·초만, 비밀 꼴 이벤트 이름은 버림).

## 4. 시간 예산 (t845u-3) — `standalone/time_budget.py`
- 판단 지연(PROGRAM 제외)을 합산, 20초 넘게 쌓이면 여유 대기만 0.6배(바닥 아래 금지). 안전 간격은 줄이지 않음.
- 회차 결과 `time_report`·기록 `run_report`: run_sec, decision_sec, saved_sec, deficit_sec, by_owner{n, sec}, routing_counts.

## 5. site_pack (t846u) — `standalone/site_pack.py`, `tools/build_site_pack.py`
- 형식: `site_pack/<site>/gallery/<step>/<state>_<full|2x|4x|8x>.png`(state normal/loading/error/popup), `steps.json`(expect_words·click_box_r·next·react_avg_sec·react_adj_sec·complexity),
  `errors.json`(오류 → 담당 owner 연결), `decision_routing.json`, `rank.json`(그림별 성공·실패·순위), `timing.json`, `knowhow/`, 조각 `snippets/<site>/`, 금지 `snippets/_forbidden/`.
- 메모리 색인 `SiteIndex`: 단계·글자(expect_words)·그림 해시(8비트 띠 8개 LSH) 조회.
- 도구: collector_kb(snippets·sites·errors·knowhow·rules)+shots → 암호화 묶음, 비밀값 파일 제외(이름·내용), manifest.json·SHA256SUMS.txt·build_report.json, 등급 넘으면 정리.
  상자 collector_kb + 사진 1장으로 만들면 108개·7.4MB(법원경매 A 500MB 안). 노트북 실제 자료(약 16MB)는 노트북에서 만들어야 해요.

## 6. 자료 암호화 (t850u·t851u) — `standalone/crypto.py`, `tools/key_upgrade_audit.py`
- scrypt(n=2^14) 키 유도(없으면 PBKDF2 20만 회) + 파일마다 nonce, SHAKE-256 키스트림 + HMAC-SHA256(암호화 후 MAC). 표준 라이브러리만.
- 암호화 대상: 관제기 묶음 파일·전송, 캐시, 올리는 자료, `snippet_rank.json`, `screen_timing_local.json`(옛 평문은 읽고 다음 저장 때 암호문). 메모리 안에서만 평문.
- 시험 키 `test`, 표시 **KEY-UPGRADE-AT-2026-12-01T00:00+09:00** (앞서 쓰던 BEFORE 꼴 옛 표기는 검사에서 위반 처리). 표시 11곳.
- **2026-12-01 00:00 KST 이후 시험 키면 경고 + STOP `key_upgrade_required`**(app·Runner 둘 다), 종료 코드 5.
- 갱신: `crypto.key_source:"dpapi"` + `keys` 로 DATA_KEY(DPAPI 저장). 설정 파일 `crypto.key` 는 'test' 외 값이면 거부(평문 비밀 금지 유지).
- `python tools/key_upgrade_audit.py` → 0(정상) / 1(표시 없는 자리·옛 표기) / 2(갱신 시각 뒤 시험 키 남음).

## 7. Claude 검수(v1.5 E1~E5) 반영
- E1(높음): 법원 고르기는 '한 줄 전체' 정확 일치만(같은 줄에 지원 이름 붙으면 거부), 칸 확인·아래키 ENTER 도 같음 비교. 2호용 C8b 패치도 따로 만듦.
- E2: 대상이 수도권·avoid 이름이면 입력 없이 거부, 아래키 경로도 검사.
- E3: site_alive HEAD 는 리다이렉트를 따라가지 않고, Location 호스트가 법원·iros 면 POLICY_REFUSED 기록(요청·DNS 조회 0).
- E4: 패키징 전 __pycache__ 삭제 후 SHA.
- E5: 입력칸 가림은 그 칸을 찾은 화면(지각 해시 거리 ≤12)에서만.
- (보급형 검수 P7 단독판에도) 딥시크 주소는 https://api.deepseek.com 만, 다르면 딥시크 끔. P11: 피코 연결 실패는 한 줄 안내·종료 코드 6.
- V4(이미지 위치 실측)·H5(서버 없을 때 동시 사용)는: H5 는 관제기 필수로 서버 잠금이 늘 쓰여 해소(로컬 관제기는 같은 PC 안만), V4 는 실측 필요로 남음.

## 8. 로컬 관제기 — `court/local_control_server_v1_0/`(수집기 폴더 밖)
- 사무실 관제기가 없을 때 같은 노트북에서 같은 규격으로 대신(127.0.0.1, 로그인 실행 때 입력). 설치는 `INSTALL_jijuclub5_KO.md`.

## 시험
- `tests/run_all.py`: **Ran 165 OK + legacy 39/39, ALL PASS**. v1.5 120개 − 지운 5개(서버 없을 때 허용·경고 시험) + 새 50개
  (T22 참고자료 13, T23 검수 수정 7, T24 적응 대기 7·담당표 6·시간 예산 3, T25 site_pack 6, T26 암호화 8).
- 적재 속도: 암호문 2000개(약 16MB) 풀기+색인 0.10초(상자). 해시 근접 조회 1회 10ms 미만 확인.
- 로컬 관제기 + v1.6: `tests/test_local_control.py` 3 run, 0 fail.
