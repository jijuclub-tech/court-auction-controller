# 단독 수집기 v1.5 바뀐 점 (2026-11-19 KST, 대표 직접 지시 t840u)

바탕: v1.4 복사(v1.4 는 백업 유지 — 시험용 로그인 값 제거만 예외로 고치고 SHA 다시 만듦. v1.3 도 같음).

## 검수(v1.4 REVIEW) 남은 것
- **W1 (높음) 캡차 그림 하얗게 지움**: 넓은 `input_box_r` 로 가리던 것을 없앰. 찾은 입력칸 하나 크기(`field_half_r`)만 '가릴 칸'으로 기록.
  그 칸이 캡차 상자와 겹치면 AI 로 안 보내고 사람에게. 자른 그림이 거의 한 색이면 한 번 다시 자르고, 그래도면 AI·기억 안 씀(사람).
  기억 열쇠는 그림 해시(48×16), 빈 그림은 열쇠 없음. 실제 법원 JSON 값으로 시험(AI 그림 흰색 비율 < 0.5, 이전 1.000).
- **W2 / V2 c**: `_safe_logout` 맨 앞에서 이미 로그아웃(상단 '로그인'만 보임)이면 STOP 없이 끝. 망 복구 뒤 이미 로그아웃된 화면도 STOP 없음.
- **W3**: 참/거짓 설정 전부 `_truthy` 로(`BOOL_KEYS`). 실행 중 읽을 때도 `_truthy`. `"false"` 문자열이면 서버·시간대 없는 법원 회차 거부.
- **W4 / V1**: 시험에서 특정 값(조각 조립·짧은 해시)을 없앰. 일반 꼴(값/값, 아이디와 비번에 같은 값) 검사 + 환경변수
  `BG_FORBIDDEN_LOGIN_SHA256` 가 있을 때만 해시 비교. v1.3 `SECRET_SCAN_KO.txt` 2·10줄의 단어 제거, v1.3·v1.4 시험도 같은 방식으로.
- **V4 ②**: 법원 JSON 두 개에 `image_offset_r` 를 명시하고 `image_offset_verified: false`(첫 실기 확인 전 추정값임을 표시).
- **H5 작은 점**: 서버 없이 시작하면 화면 알림(1번), 긴 휴식(`break_min`) 중에도 60초마다 잠금 하트비트, README 에 `allow_no_window` 설명.

## C8 스크롤 court_select
- `standalone/court_select.py` (2호 patch_C8 과 같은 규칙) + `runner.do_select_scroll` + `hw.wheel`(피코2 WHEEL).
- 법원 JSON 두 개의 `court_select` 를 `select_scroll` 로. 비수도권은 `refuse_metro: true` + avoid 목록.

## 사이트 거부 기록 (`standalone/refusal_log.py`, `logs/refusals.jsonl`)
- site_alive HEAD: 법원·iros 는 요청 없이 POLICY_REFUSED. 다른 사이트는 HTTP 5xx/4xx·소켓 오류(TIMEOUT/CONN_REFUSED/DNS_FAIL…)를 REMOTE 로,
  다음 허용 시각(`site_alive_retry_sec`, 기본 300초) 전에는 다시 안 보냄.
- NTP: 설정에 금지 호스트가 있으면 POLICY_REFUSED, 무응답 서버는 REMOTE `NTP_NO_RESPONSE`(다음 허용 = 순환 주기).
- KB 서버 주소가 법원·iros 면 거부(`banned_host`).

## 조각 그림 다중 크기 (`standalone/snippets.py`)
- 1배 + 같은 중심 2·4·8배 맥락 그림. 10/100/300번째 캡처마다, 실패 2/5/10번마다(설정 `snippet_scales`). 크기마다 최대 3장.
- 찾기: 크기별 성공률 순(같으면 큰 것 먼저)으로 큰 그림을 성기게 맞춰 범위만 좁히고, 그 안에서 1배로 좌표를 정함. 큰 그림만으로는 좌표를 안 냄.
- 가리기: 로그인·캡차에서 찾은 ID/PW/캡차 입력칸(정확한 칸)을 회색으로. 금지 그림은 `_forbidden/`(찾기·서버 동기화 제외, 근처면 거부).
- 성공·실패는 파일별 + 크기별(`__meta__.scale_stats`)로 쌓고 순서가 자동으로 바뀜. Locator 가 못 찾으면(learn=True) 실패 수를 셈.
- 참고: `bank.find(im, 이름, None)`(화면 전체)가 이제 실제로 찾음(전에는 예외로 늘 '없음'). 화면 판단·캡차 상자 찾기에 쓰임.

## 시험
- `python tests/run_all.py` 실제 출력: **Ran 120 tests … OK, legacy page_guard 39/39 pass, ALL PASS** (v1.4 는 93개).
- 새 시험 28개(T20ReviewV15: W1 4, W2/V2 2, W3 2, H5 2, C8 3, 거부 기록 5, 다중 크기 10), 지운 시험 1개(V4 가리기 → W1 로 대체) → 93+28-1=120. 바뀐 시험: 비밀 검사(W4), 기억 시험(빈 그림 → 무늬 그림).

## 남은 것
- `image_offset_r`·`field_half_r`·select 상자 비율은 화면 확인 전 추정값(첫 실기 때 확인 필요).
- 아래키 단계에서 못 찾고 끝나면 드롭다운 값이 다른 법원으로 바뀌어 있을 수 있음(그래도 STOP 이라 검색은 안 함).
