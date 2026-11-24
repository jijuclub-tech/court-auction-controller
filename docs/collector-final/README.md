# 수집기 최종 버전 묶음 (2026-11-24 KST)

한영님 지시 t361u (2026-11-24 23:28 KST): 수집기 최종 버전을 정리해 GitHub에 올리고, 수정 이유와 관련 자료를 버전업 참고용으로 남기며, ChatGPT에게 이 자료를 기준으로 반영하라고 요청한다.

작성: 지휘자 봇(실행기). S-1.0: 코드·문서만. 수집 데이터(감정평가서 PDF, merged.json.bgx, 사건 대기열 실데이터)와 키·비밀번호·토큰 값은 넣지 않았다. 새 GitHub Actions·배포는 만들지 않는다. 이 PR은 머지하지 않는다(ChatGPT 검수 후).

KEY-UPGRADE-AT-2026-12-01T00:00+09:00 — 시험 키 값은 여기 없음.

## 1. 최종(현재 운용) 버전

| 구분 | 이름 | 상태 (2026-11-24 23:28 KST) | 이 묶음에서의 위치 |
|---|---|---|---|
| 수집기 3호 실제 운용본 | `c2_step_click_G_v1.2.4.py` + `run_today.ps1` + `stash.ps1` | 가동 중(t358u: 3호만 활동). OCR plan runner, 저장은 ipDISK `Z:\HDD1\BIDGUARD\collector3_out` | `as_used_collector3_G_v1.2.4/` |
| 수집기 1호 설치본 | `collector1_c09liq2_G_v1.3.0` | 설치 준비만. t327u·t358u로 정지. 재개 승인 전에는 법원 사이트를 열지 않음 | `collector1_c09liq2_G_v1.3.0/` |
| 수집기 2호 | 같은 계열 `c2_step_click_G_v1.2.4.py`를 lg15에 보유 | 11/23 15:13 이후 정지, PC 오프라인. 감정평가서 확인된 성공 0건 | 코드 사본은 3호 운용본을 참고. 2호 전용 패치는 공용 폴더 `court/collector2_lg15/` |
| 법원봇 09 대전 | 11/23 시험은 `c2_step_click_G_v1.2.4.py` 비로그인 길. 설계안은 `standalone_collector_T_v1.8.0`(#50, PR #51) | PC 오프라인. t358u로 3호 외 중단(t36u 예외는 이후 지시로 대체) | 변경 기록만 `related/standalone_T_v1_8_0_docs/` |
| 관제기 | 이 저장소 `main`의 `controller.py`, `key_wallet.py`, `collector_dispatch.py`, `collector_key_wallet_T_v0_2.py`, `admin.html` 등 | 서버 주소 미확인(#55). 키는 관제기 금고(#47) 또는 1호 경로만 | 이미 `main`에 있으므로 여기 재복사하지 않음 |
| 피코2 펌웨어 | `1.0.8-v8 r2` (UF2 SHA256 `cc81a4b08ba4167dc38fa206351b6245cd4c14869d0aa4c591c163b2d9f393bb`) | 1호 DESKTOP-C09LIQ2 COM4에 굽힘. 3호는 COM9 | 안내·해시만 `related/firmware/` (UF2·zip은 바이너리라 생략. 원본은 공용 `firmware/pico2_1.0.8-v8-r2/`, 펌웨어 저장소 BIDGUARD-PICO2-FIRMWARE-PRIVATE) |

**버전업의 기준본은 3호가 실제로 쓰는 G v1.2.4이다.** G v1.3.0은 그 사본을 1호 경로(DESKTOP-C09LIQ2, COM4, 전용 크롬 프로필)로 맞춘 설치본이다. 실행 파일 본체의 클릭·정지 로직은 v1.2.4와 같다. 단독 패키지 T v1.8.0(/lock·/upload·/kiosk)은 #50 설계안이며, 3호 운용본이 아니다. PR #51은 미병합.

## 2. 3호 운용 방식 (버전업 때 깨지 말 것)

- PC: DESKTOP-5R67H71, 피코2 COM9, 1920×1080 배율 1, 전용 크롬 프로필. 비로그인으로 감정평가서 열람.
- 입력은 피코2(USB HID, VID 2E8A)만. 박스(Grok 클라우드)는 법원 사이트에 직접 접속하지 않음.
- 속도: 클릭 사이 16~24초, 사건 사이 6~9분, 로봇 확인 뒤 8~10분. 창 하나, 5건마다 새 창. 종료 12분 전 새 사건 금지.
- 저장 순서: X:(NAS2e, 포트 19641) → Z:(ipDISK FTP 21, `Z:\HDD1\BIDGUARD\collector3_out`) → 로컬 대기. 11/23 점검에서 19641·80·443이 닫혀 X:는 실패, 지금은 Z:만. 저장 후 SHA 비교.
- 원격 접속 감지 시 `pause.flag`로 입력 일시정지(G v1.2.3, 11/23 23:49~23:57 실제 작동).
- 그림 문제(이미지 선택 등)는 풀지 않고 정지. 차단·비정상 접속이면 즉시 종료 후 3시간 이상 쉼. 같은 실패 3회면 정지.
- AI 순서: 프로그램(OCR·템플릿) → Gemini(화면) → 사람. 일상은 봇 호출 없이, Gemini 같은 실패 3회면 관제기가 사람 호출(t342u~t344u).
- 당일 매각 물건의 감정평가서·현황조사서 버튼은 자정(KST) 이후 사라짐. 당일분은 23:50 전에 끝내야 함.

## 3. 폴더 안내

```
docs/collector-final/
  README.md                          이 파일
  CHANGELOG.md                       버전별 수정과 이유(문서·코드 주석에 근거가 있는 것만)
  as_used_collector3_G_v1.2.4/       3호 PC에서 쓰던 원본
  collector1_c09liq2_G_v1.3.0/       1호 설치본(키·로그인 모듈 없음)
  related/                           인수인계·주간 업그레이드 규칙·T v1.8.0 변경 기록·펌웨어 안내
```

1호 설치본에는 `af/keys.py`, `gemini_handoff.py`, `http_util.py`(키 읽기·그림 문제 AI 풀기)를 넣지 않았다(원 설치본 README_1HO.md). 키는 관제기(#47) 또는 1호 기존 경로에서만 받는다.

## 4. 관련 자료 목록 (이미 저장소·이슈에 있는 것 포함)

| 자료 | 위치 | 비고 |
|---|---|---|
| 봇·역할·저장 현황 G v1.0.0 | 이슈 #55 | 한영님 t354u·t355u. 수집 데이터 아님 |
| 수집기 실행 옵션 /lock·/upload·/kiosk | 이슈 #50, PR #51(Draft, 미병합) | T v1.8.0 설계. 3호 운용본 아님 |
| 키 금고 | 이슈 #47, `docs/KEY_WALLET_BETA.md`, `key_wallet.py` | 키 값 없음 |
| 파일 이름 규칙 | 이슈 #28 | `<기본이름>_<AI코드>_vX.Y.Z` |
| 저장 위치 | 이슈 #26 | NAS 원본 |
| 화면 증거 용량 | 이슈 #29 | 2호 PNG 용량 문제 |
| 단독 수집기 v1.6 (GitHub에 이미 있음) | `collectors/standalone_collector_v1_6/` | 이후 G v1.2.x 운용본과는 별 계열 |
| 로컬 관제 서버 v1.0 (이미 있음) | `collectors/local_control_server_v1_0/` | |
| 주간 업그레이드 규칙(t261u) | `related/collector_upgrade_README.md` | 월 04:00 적용, GPT 검수·시험·원복. 지휘자 전결은 조건 충족 시에만 |
| 3호→1호 인수인계 G v1.3.0 제안 | `related/2026-11-23_collector3_to_1ho_slow_appraisal_G_v1.3.0.md` | t91u |
| T v1.8.0·v1~v1.7 변경 기록 | `related/standalone_T_v1_8_0_docs/` | collector_kb(경험 DB)와 zip은 올리지 않음 |
| 피코2 1.0.8-v8 r2 안내 | `related/firmware/` | UF2는 공용 폴더·펌웨어 저장소 |

공용 폴더에만 있고 이 PR에 넣지 않은 것(용량·데이터·미사용 구버전):

- `court/standalone_collector_v1` ~ `v1_7_4p` 전체 트리(변경 기록은 위 문서로 대체. v1.6 코드는 이미 `collectors/`에 있음)
- `court/standalone_collector_court09_T_v1_8_0/collector_kb`(약 5MB, 경험·지도 자료)
- `court/court_bots/today_*`, `inbox/requests/`, `court/cases/`(수집 데이터)
- 감정평가서 PDF, `.json.bgx`, 실 사건 대기열
- 피코2 UF2·설치 zip(바이너리). SHA는 포함

## 5. ChatGPT에 대한 요청

이 묶음과 CHANGELOG를 기준으로 다음 버전업에 반영하라.

- 3호가 지금 쓰는 G v1.2.4 동작을 깨지 말 것. t358u로 3호만 가동 중이다.
- 사람 속도(저속 접근)를 유지하고, 차단·캡차 그림 문제 우회를 넣지 말 것.
- 저장은 NAS가 원본(t347u). X: 포트 19641이 닫힌 현실을 반영해 Z:·로컬 대기·(미완성) /upload를 설계할 것.
- 키·비밀번호는 코드·로그·이슈에 쓰지 말 것. 관제기 금고 또는 1호 경로만.
- S-1.0: 데이터 커밋 금지, 새 Actions·배포 금지. 이 PR은 검수 후 머지.

열린 문제 목록은 이슈 #55 7절을 따른다.
