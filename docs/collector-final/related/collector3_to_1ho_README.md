# 수집기3호 → 1호 실행 파일 묶음 (2026-11-24 00:05 KST)

한영님 지시: 3호는 경기·인천, 1호(DESKTOP-C09LIQ2)는 서울 5곳(중앙·동부·서부·남부·북부) 감정평가서를 받습니다. 1호는 정한영 아이디로 로그인하되, 3호처럼 아주 천천히 받습니다. 이 묶음에는 아이디·비밀번호·키 값이 없습니다.

## 들어 있는 파일
- `run_today.ps1` — 1호 경로·COM4로 고친 반복 실행기. 클릭 사이 16~24초는 프로그램이, 사건 사이 6~9분(로봇 확인 뒤 8~10분)은 이 스크립트가 지킵니다. 종료 12분 전부터는 새 사건을 시작하지 않습니다.
- `stash.ps1`, `plans/stash.ps1` — PDF를 NAS(X:)에 먼저, 안 되면 `C:\BIDGUARD\court_fallback`에 저장하고 SHA를 확인합니다.
- `tools/c2_step_click_G_v1.2.4.py` — 3호가 오늘 쓴 실행 파일 그대로(키 값 없음).
- `plans/full_today_tpl.txt`, `plans/open_today_tpl.txt` — 사건 1건 계획 견본. `__COURT__` 등을 실행기가 채웁니다. 5건마다 `open_today_tpl`(새 창), 그 사이는 `full_today_tpl`.
- `plans/queue_today.sample.json`, `plans/queue_format.md` — 사건 목록 형식과 가짜 1건 견본.
- `plans/close_modal.json`, `plans/open_only.json` — 창 닫기·열기 재시도.
- `as_used_on_collector3/` — 3호 PC에서 실제로 쓰던 원본(참고용).

## 1호 PC에 둘 위치
```
C:\BIDGUARD\court\run_today.ps1
C:\BIDGUARD\court\plans\   (템플릿, stash.ps1, queue_today.json, close_modal.json, open_only.json)
C:\BIDGUARD\court\auto_flow\tools\c2_step_click_G_v1.2.4.py
C:\BIDGUARD\court\auto_flow\af\   ← 기존 1호 설치본의 af(actuator)를 여기로 연결하세요. 이 묶음에는 키·로그인 모듈을 넣지 않았습니다.
C:\BIDGUARD\court\out\
C:\BIDGUARD\court_fallback\
```
기존 v1.6·v1.7_4p·설치본은 직접 고치지 마세요. lg15 경로(`C:\BIDGUARD\collector2_out` 등)와 3호 경로(`collector3_out`, `Z:\HDD1`)는 쓰지 마세요. D:는 이 PC에 없습니다.

## 반드시 바꾸거나 확인할 것
1. `stash.ps1`과 `run_today.ps1`의 `$NasRoot` / `$NasCases` — 1호가 알려 준 대로 X:는 연결되어 있습니다. 쓰기 하위 폴더는 이 PC의 실제 경로로 바꾸세요. 지금은 자리만 `X:\VOL1\Home.bidguard\collector1_out`으로 적어 두었습니다.
2. `c2_step_click_G_v1.2.4.py` 안의 경로: `C3_CHROME_DIR`, `PAUSE_FLAG`, `CAP_LOG`가 아직 `C:\BIDGUARD\collector3\...`입니다. 1호 전용 크롬 프로필(예: `C:\BIDGUARD\court\chrome_c1`)과 `C:\BIDGUARD\court\out`으로 바꾸세요. 3호 프로필을 같이 쓰지 마세요. 3호는 `--force-device-scale-factor=1`로 1920×1080을 맞췄습니다. 1호도 1920×1080이면 이 설정을 쓰는 것이 좋습니다. 그래도 버튼 위치·로봇 체크칸은 1호 화면에서 다시 재세요. 고정 95px는 쓰지 마세요.
3. `af.actuator`(PicoActuator)는 1호 기존 auto_flow에서 가져오세요. 키 저장 파일은 복사하지 마세요.
4. 피코2는 COM4, `BIDGUARD_PICO_PORT`로 이미 적어 두었습니다.
5. 계획 템플릿의 좌표는 3호 화면(1920×1080, 배율 1) 기준입니다. 1호 화면에서 한 번 확인해 주세요.
6. 로그인 세션은 마지막 연장부터 30분입니다. 10분 아래로 내려가면 바로 연장하고, 연장 버튼을 정해진 간격으로만 누르지 마세요. 로그인할 때만 `1ho_start.flag`를 올리고, 그전에 `1ho_start.flag`·`jijuclub5_start.flag`·`3ho_start.flag`를 확인하세요. 로그인·인증서·열람·발급은 한영님이 직접 합니다.
7. 매각(낙찰)된 물건은 목록 맨 앞에 두세요. 경기·인천은 넣지 마세요.

## 속도·정지 규칙
- 클릭 사이 16~24초, 사건 사이 6~9분, 로봇 확인 뒤 8~10분 (11/27 06:12까지 이 느린 간격을 유지).
- 창은 하나, 5건마다 새 창.
- 그림 문제는 풀지 않고 정지, 차단·비정상 접속이면 3시간 이상 쉼, 같은 실패 3번 연속이면 정지.
- AI 개입은 최소. 재개 확인 전에는 법원 사이트를 열지 마세요.
