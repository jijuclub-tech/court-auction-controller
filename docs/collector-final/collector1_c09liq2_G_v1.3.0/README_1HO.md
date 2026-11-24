# 수집기1호 (DESKTOP-C09LIQ2) 설치본 G v1.3.0 — 2026-11-24 KST

근거: 한영님 t1151u(이 PC가 1호기), t1152u(X: 먼저, 안 되면 C:\BIDGUARD\court_fallback), t1153u(1호는 서울 5곳, 경기·인천은 3호).
원본: court/collector3_to_1ho/ (3호 v1.2.4 묶음), af·core는 court/daejeon_09/run_jeju_1_20261123/ 복사본. 원본은 고치지 않음.
비밀값 없음. af/keys.py·gemini_handoff.py·http_util.py(키 읽기·그림 문제 AI 풀기)는 넣지 않음.

## 1호 PC 위치
- C:\BIDGUARD\court\run_today.ps1, stash.ps1, plans\, auto_flow\tools\c2_step_click_G_v1.3.0.py, auto_flow\af\
- C:\BIDGUARD\court\core\, config\pico_s0324.json(COM4, 1.0.8-v8), local_data\ (PicoActuator가 ROOT=C:\BIDGUARD\court 에서 찾음)
- C:\BIDGUARD\court\out\, remote_guard\, chrome_c1\(빈 1호 전용 크롬 프로필)
- 저장: X:\HDD1\BIDGUARD\collector1_out\court\cases\ → 안 되면 C:\BIDGUARD\court_fallback\court\cases\

## 3호 판에서 바꾼 것
- run_today.ps1: NasCases=X:\HDD1\BIDGUARD\collector1_out\court\cases, python=C:\BIDGUARD\.venv_1ho\Scripts\python.exe, 실행 파일 c2_step_click_G_v1.3.0.py, 서울 5곳 코드만 통과 + p 오름차순 정렬(당일 매각 먼저).
- stash.ps1, plans\stash.ps1: NasRoot=X:\HDD1\BIDGUARD\collector1_out (UTF-8 BOM 추가).
- c2_step_click_G_v1.3.0.py: C3_CHROME_DIR=C:\BIDGUARD\court\chrome_c1, PAUSE_FLAG=C:\BIDGUARD\court\remote_guard\pause.flag, CAP_LOG=C:\BIDGUARD\court\out\recaptcha_log.jsonl, 기본 out_root=C:\BIDGUARD\court\out\steps, USERPROFILE 기본값 C:\Users\user. 간격 16~24초·그림 문제 정지·차단 정지는 그대로.
- config\pico_s0324.json: pico_port COM4.
- plans\queue_today.json: 빈 목록 [].

## 남은 일 (시작 전)
- 피코2 보정(local_data\pico_calibration.json 또는 pico_profiles\1920x1080_scale100_mon1.json)이 없음 → PicoActuator는 보정 없이는 열리지 않음. 보정은 마우스를 움직이므로 재개 승인 뒤에 한다.
- 서울 사건 목록(queue_today.json) 채우기, 한영님 직접 로그인, 로봇 체크칸·템플릿 좌표를 1호 화면에서 재기.
